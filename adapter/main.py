"""Адаптер кабинета: наш контракт `/api/v1/public/*` поверх бота «Бедолага».

Зачем. Кабинет всегда ходит в `/api/*`, nginx переписывает это в путь бэкенда
(`API_PATH_PREFIX`, см. cabinet/nginx.conf). Для нашего бота путь совпадает, для
«Бедолаги» — нет: у неё префикс `/cabinet`, часть ручек названа иначе, а часть
того, что нужно кабинету, не существует вовсе. Этот сервис встаёт между ними.

Что он делает сейчас:
  • транслирует пути по карте `route_map.json` (сгенерирована из их `/openapi.json`
    и нашего замороженного контракта `docs/CABINET-API-CONTRACT.md`), включая пути
    с параметрами: `/api/subscription/devices/{hwid}` подставляется в их шаблон;
  • пробрасывает метод, тело, query и заголовки;
  • собирает сам то, чего у них нет одним запросом (оформление, права, форма
    `/auth/me`) — см. `compose.py`;
  • на пути, которых у «Бедолаги» нет, честно отвечает 501 со списком причины —
    вместо тихой пустоты, по которой потом не найдёшь концов.

  • МОСТ АВТОРИЗАЦИИ: кабинет живёт на HttpOnly-куках и токенов не видит вовсе,
    «Бедолага» — на Bearer в заголовке. Адаптер логинится у них, а их access/refresh
    кладёт в свои HttpOnly-куки (JS до них не достаёт — защита от XSS сохраняется)
    и подставляет `Authorization` в каждый проксируемый запрос.

Чего он ещё НЕ делает (следующие этапы, см. роадмап):
  • денежный контур: у них нет прямой покупки подписки картой, только списание с
    баланса, поэтому `payment_url` придётся собирать связкой «пополнение +
    автопокупка отложенной корзины».

Запуск: `uvicorn adapter.main:app --port 8090`; куда ходить — `BEDOLAGA_UPSTREAM`.

Окружение, помимо `BEDOLAGA_UPSTREAM`/`BEDOLAGA_ADMIN_TOKEN`/`REMNAWAVE_*`:
  • `ADAPTER_ALLOWED_ORIGINS` — с каких адресов кабинету разрешено слать
    изменяющие запросы (защита от CSRF, см. `_csrf_blocked`). Несколько — через
    запятую, годится и полный URL, и голый хост. По умолчанию пусто: разрешён
    «тот же хост», с которого пришёл запрос (кабинет и адаптер стоят за одним
    nginx на одном домене), а если nginx подменил `Host` на имя контейнера —
    метка браузера `Sec-Fetch-Site`. Указать публичный адрес кабинета всё же
    стоит: только тогда проверка строгая, как в нашем бэкенде.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from fastapi import FastAPI, Request, Response

import compose
from compose import HANDLERS, Ctx, NotAvailable, UpstreamError
from state import State

UPSTREAM = os.environ.get("BEDOLAGA_UPSTREAM", "http://127.0.0.1:8080").rstrip("/")
# Панель Remnawave — источник того, что боту не принадлежит (серверы, трафик,
# устройства). Не задана — соответствующие разделы отдают, что смогли получить от
# бота, но не выключаются: см. правило в compose.Ctx.
def _panel_url(raw: str) -> str:
    """Адрес панели у разных ботов записан по-разному: полный URL, `host:port`
    или просто имя контейнера. Приводим к рабочему виду, а не требуем от
    оператора угадывать формат."""
    value = raw.strip().rstrip("/")
    if not value:
        return ""
    if "://" not in value:
        value = "http://" + value
    tail = value.split("://", 1)[1]
    host = tail.split("/", 1)[0]
    # Имя контейнера без порта — у Remnawave в docker это 3000.
    if ":" not in host and "." not in host:
        value = value.replace(host, host + ":3000", 1)
    return value


PANEL_URL = _panel_url(os.environ.get("REMNAWAVE_URL", ""))
PANEL_TOKEN = os.environ.get("REMNAWAVE_TOKEN", "")
# Админский токен «Бедолаги» (их таблица web_api_tokens). Нужен там, где кабинет
# умеет больше их бота — например заморозка подписки. Не задан — такие разделы
# честно выключены, но всё остальное работает.
ADMIN_TOKEN = os.environ.get("BEDOLAGA_ADMIN_TOKEN", "")
# Куда адаптеру писать своё состояние: папка с кодом монтируется только на чтение.
STATE_PATH = os.environ.get("ADAPTER_STATE", "/data/state.db")
ROUTE_MAP_PATH = Path(__file__).with_name("route_map.json")
TIMEOUT = httpx.Timeout(connect=5.0, read=25.0, write=10.0, pool=5.0)
# Покупка и продление у них синхронно ходят в панель Remnawave: 25 секунд им не
# хватает, запрос обрывается, а деньги списываются. Двойное списание мы теперь
# ловим сверкой, но лучше не доводить — денежным вызовам даём отдельный срок.
MONEY_TIMEOUT = httpx.Timeout(connect=5.0, read=90.0, write=10.0, pool=5.0)
# Общий дедлайн на СОБСТВЕННЫЙ обработчик. TIMEOUT выше — это лимит одного вызова
# бота, а наши ручки собирают экран из нескольких подряд (`/api/info` — до десятка),
# и на медленном боте вкладка кабинета висела бы минутами вместо честного отказа.
OWN_DEADLINE = 30.0
# Откуда кабинету позволено слать изменяющие запросы; пусто — только «тот же хост».
ALLOWED_ORIGINS = os.environ.get("ADAPTER_ALLOWED_ORIGINS", "")

# Заголовки, которые нельзя тащить между хостами: их выставляет транспорт.
_HOP_BY_HOP = {
    "host", "connection", "keep-alive", "transfer-encoding", "upgrade",
    "content-length", "proxy-authorization", "te", "trailer",
    # Адрес клиента подставляем сами (см. _client_ip): пришедший от браузера
    # заголовок доверять нельзя — он подделывается одной строкой.
    "x-forwarded-for", "x-real-ip", "x-forwarded-proto",
}


# Заголовки ответа бота, которые наружу отдавать нельзя:
#   • content-encoding/content-length — httpx тело уже распаковал, а размер
#     посчитает наш сервер; чужие значения = битый ответ в браузере;
#   • access-control-* — кабинет ходит с того же origin, CORS ему не нужен, а
#     политика бота (его CABINET_ALLOWED_ORIGINS) к нашей установке отношения
#     не имеет и молча разрешала бы чужие сайты;
#   • set-cookie — сессию держим мы, свои куки бот ставить не должен.
_DROP_FROM_UPSTREAM = {"content-encoding", "content-length", "set-cookie"}


def _drop_from_upstream(name: str) -> bool:
    low = name.lower()
    return low in _DROP_FROM_UPSTREAM or low.startswith("access-control-")


def _client_ip(request: Request) -> str:
    """Настоящий адрес посетителя: первый в цепочке от нашего же nginx."""
    chain = request.headers.get("x-forwarded-for", "")
    first = chain.split(",")[0].strip() if chain else ""
    return first or (request.client.host if request.client else "")


def _forwarded(request: Request) -> dict[str, str]:
    """Заголовки адреса для бота: без них его лимиты на вход и регистрацию
    считаются на один адрес адаптера — то есть на всех пользователей сразу,
    и один человек может заблокировать вход всему кабинету."""
    ip = _client_ip(request)
    if not ip:
        return {}
    return {
        "x-forwarded-for": ip,
        "x-real-ip": ip,
        "x-forwarded-proto": request.headers.get("x-forwarded-proto", "https"),
    }

# Имена кук — те же, что ставит наш бэкенд: кабинет их не читает (HttpOnly), но
# совпадение имён избавляет от расхождений в logout и в отладке.
ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
# Куда логинимся у «Бедолаги»: наш кабинет знает один /auth/login, у них вход
# разнесён по способам.
LOGIN_ROUTES = {
    "/api/auth/login": "/cabinet/auth/email/login",
    # register — только standalone: обычная /email/register у них требует уже
    # авторизованного пользователя (проверено на стенде: 401, security HTTPBearer).
    "/api/auth/register": "/cabinet/auth/email/register/standalone",
    "/api/auth/telegram": "/cabinet/auth/telegram/widget",
    "/api/auth/telegram/webapp": "/cabinet/auth/telegram",
}
COOKIE_KW = dict(httponly=True, secure=True, samesite="lax", path="/")

# Методы, меняющие данные: только их и защищаем от CSRF — чтение чужой страницей
# всё равно недоступно (браузер не даст прочитать ответ с другого origin).
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _origin_host(value: str) -> str:
    """Хост источника: в Origin приходит `схема://host:port`, в Referer — целый
    URL, а в настройке оператор может написать просто `cabinet.example.com`."""
    value = value.strip()
    if not value:
        return ""
    return urlsplit(value if "://" in value else "//" + value).netloc.lower()


# WEB_CABINET_URL читаем тем же именем, что и наш бэкенд: если оператор прокинул
# его в адаптер, отдельной настройки не требуется.
_ALLOWED_HOSTS = {
    h
    for h in (
        _origin_host(x)
        for x in (ALLOWED_ORIGINS + "," + os.environ.get("WEB_CABINET_URL", "")).split(",")
    )
    if h
}


def _allowed_hosts(request: Request) -> set[str]:
    """С каких хостов принимаем изменяющие запросы.

    Кроме заданных в окружении сюда идёт собственный хост запроса — кабинет и
    адаптер стоят на одном домене, и очевидное настраивать незачем. Но только
    если этим хостом мог пользоваться браузер: nginx кабинета подменяет `Host` на
    имя сервиса в docker (`API_HOST_HEADER=cabinet-adapter`, install.sh), а с
    таким именем ни один Origin не совпадёт — сравнивать с ним значит отбить
    кабинету все кнопки разом.
    """
    hosts = set(_ALLOWED_HOSTS)
    for header in ("x-forwarded-host", "host"):
        value = request.headers.get(header, "").split(",")[0].strip().lower()
        # Точка = домен или IP, то есть адрес, который вообще может стоять в
        # адресной строке; голое имя контейнера отбрасываем.
        if value and "." in value.split(":")[0]:
            hosts.add(value)
    return hosts


def _csrf_blocked(request: Request) -> bool:
    """True — запрос отклонить как CSRF.

    Кабинет держит сессию в куках, а кука уходит и при запросе, начатом чужим
    сайтом: `SameSite=lax` спасает от простой формы, но не от всех случаев
    (поддомены, дырявые редиректы), и держаться на нём одном нельзя. Правило —
    как в нашем бэкенде (`admin_src/src/web/csrf.py`), чтобы кабинет вёл себя
    одинаково на обоих: защищаем только изменяющие `/api/`-запросы, которые несут
    НАШУ куку. Запрос без куки не аутентифицирован, красть у него нечего —
    нативные клиенты и curl без сессии проходят как раньше. У запроса с кукой
    источник обязан быть: браузер шлёт Origin на любой POST, и его отсутствие —
    это и есть попытка обойти проверку.

    Отличие от бэкенда одно и вынужденное: тому публичный адрес кабинета известен
    из настроек, а адаптеру — нет (nginx подменяет `Host` на имя контейнера, а
    `WEB_CABINET_URL` в него не прокидывается). Пока адрес не задан
    `ADAPTER_ALLOWED_ORIGINS`, сравнивать Origin не с чем, и вместо него работает
    `Sec-Fetch-Site`: его проставляет сам браузер и со страницы не подделать, так
    что чужой сайт мы всё равно узнаём. С заданной переменной проверка становится
    строгой — как у нас.
    """
    if request.method.upper() not in _UNSAFE_METHODS:
        return False
    if "/api/" not in request.url.path:
        return False
    if not (request.cookies.get(ACCESS_COOKIE) or request.cookies.get(REFRESH_COOKIE)):
        return False

    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    # `Origin: null` шлёт песочница iframe и документ с data:-схемы — доверять нечему.
    if origin and origin.lower() != "null":
        source = _origin_host(origin)
    else:
        source = _origin_host(referer or "")

    allowed = _allowed_hosts(request)
    if allowed:
        return not source or source not in allowed
    # Свой адрес неизвестен — верим метке браузера. `cross-site` значит, что
    # запрос начала чужая страница; `same-origin`/`same-site`/`none` (адресная
    # строка, закладка) — свои. Старые браузеры заголовка не шлют: тогда остаётся
    # прежнее поведение, но запрос без источника вовсе всё равно отклоняем.
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        return True
    return not source


def _jwt_exp(token: str | None) -> float | None:
    """Момент истечения, записанный в самом токене (claim `exp`), либо None.

    Подпись не проверяем — она не наша, и проверять нам её нечем; нужен только
    срок. Токен пришёл от бота по внутренней сети, а не от браузера, поэтому
    доверять его содержимому здесь можно."""
    if not token:
        return None
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
        if isinstance(exp, (int, float)):
            return float(exp)
    except Exception:  # noqa: BLE001 — чужой формат токена не должен ронять вход
        pass
    return None


def _ttl(data: dict[str, Any], key: str, token: str | None, default: int) -> int:
    """Сколько секунд браузеру хранить куку.

    Без срока кука сессионная: закрыл браузер — разлогинен, хотя в ответе тому
    же кабинету обещан месяц. Источники по убыванию достоверности: что бот
    сказал в ответе (`expires_in`), затем `exp` самого токена, и лишь потом
    умолчание. Второй шаг не formality: про refresh «Бедолага» не сообщает
    ничего, а живёт он у неё 7 дней — с голым умолчанием в 30 дней кука
    пережила бы токен на три недели, и всё это время человек получал бы
    непонятные отказы вместо честного «войдите заново»."""
    raw = data.get(key)
    if raw is not None:
        try:
            seconds = int(float(raw))
        except (TypeError, ValueError):
            seconds = 0
        if seconds > 0:
            return seconds
    exp = _jwt_exp(token)
    if exp is not None:
        left = int(exp - time.time())
        if left > 0:
            return left
    return default


def _tokens(data: dict[str, Any]) -> tuple[str | None, str | None]:
    """Пара токенов из их ответа: ручки бота пишут их то в snake_case, то в camelCase."""
    return (
        data.get("access_token") or data.get("accessToken"),
        data.get("refresh_token") or data.get("refreshToken"),
    )


def _session_deadlines(data: dict[str, Any]) -> dict[str, str | None]:
    """Сроки жизни сессии в нашем виде: у них `expires_in` в секундах, у нас — метка времени.

    Считаем из тех же источников, что и `max_age` кук: обещание в теле ответа не
    должно расходиться с тем, сколько браузер на самом деле держит сессию."""
    access, refresh = _tokens(data)
    now = datetime.now(timezone.utc)

    def stamp(key: str, seconds_key: str, token: str | None, default: int) -> str | None:
        if data.get(key):
            return str(data[key])
        return (now + timedelta(seconds=_ttl(data, seconds_key, token, default))).isoformat()

    return {
        "expires_at": stamp("expires_at", "expires_in", access, 900),
        # Срок refresh они не сообщают вовсе; 30 дней — их же дефолт настройки,
        # но сперва спрашиваем сам токен, см. `_ttl`.
        "refresh_expires_at": stamp(
            "refresh_expires_at", "refresh_expires_in", refresh, 30 * 24 * 3600
        ),
    }


# Коды рекламных кампаний бота. Наш кабинет знает один параметр `?ref=`, а у
# «Бедолаги» это ДВА разных мира: код пригласившего (referral_code) и метка
# рекламной кампании (campaign_slug). Отправишь не туда — переход не засчитается
# ни там, ни там. Поэтому спрашиваем у бота список кампаний и решаем по факту.
_CAMPAIGNS: dict[str, Any] = {"codes": set(), "until": 0.0}


async def _campaign_codes() -> set[str]:
    now = time.time()
    if now < _CAMPAIGNS["until"]:
        return _CAMPAIGNS["codes"]
    codes: set[str] = set()
    if admin is not None:
        try:
            resp = await admin.request("GET", "/campaigns", params={"limit": 100})
            if resp.status_code < 400:
                data = resp.json() or {}
                rows = data.get("campaigns") or data.get("items") or []
                codes = {
                    str(c.get("start_parameter") or "") for c in rows if isinstance(c, dict)
                } - {""}
        except (httpx.HTTPError, ValueError):
            codes = _CAMPAIGNS["codes"]  # не смогли спросить — держим прежнее
    _CAMPAIGNS.update({"codes": codes, "until": now + 300})
    return codes


async def _register_body(body: bytes) -> bytes:
    """Приводит тело регистрации к тому, что понимает «Бедолага».

    Два расхождения, каждое молча теряло данные: имя человека они ждут в
    `first_name` (наше `name` просто отбрасывалось), а рекламный код — в
    `campaign_slug`, если это метка кампании, а не приглашение от друга.
    """
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return body
    if not isinstance(data, dict):
        return body
    name = data.pop("name", None)
    if name and not data.get("first_name"):
        data["first_name"] = str(name)[:64]
    code = str(data.get("referral_code") or "").strip()
    if code and code in await _campaign_codes():
        data.pop("referral_code", None)
        data["campaign_slug"] = code
    return json.dumps(data, ensure_ascii=False).encode()


async def _login_after_register(body: bytes, data: dict[str, Any]) -> dict[str, Any]:
    """Пробует войти сразу после регистрации теми же email и паролем."""
    try:
        creds = json.loads(body or b"{}")
    except ValueError:
        return data
    email, password = creds.get("email"), creds.get("password")
    if not email or not password:
        return data
    try:
        resp = await _call(
            "POST", LOGIN_ROUTES["/api/auth/login"],
            json={"email": email, "password": password},
        )
    except Unreachable:
        return data
    if resp.status_code >= 400 or not resp.content:
        return data
    try:
        return resp.json() or data
    except ValueError:
        return data


def _set_session(resp: Response, data: dict[str, Any]) -> None:
    """Кладёт их токены в наши HttpOnly-куки. Кабинету токены не показываем."""
    access, refresh = _tokens(data)
    if access:
        resp.set_cookie(
            ACCESS_COOKIE, access, max_age=_ttl(data, "expires_in", access, 900), **COOKIE_KW
        )
    if refresh:
        resp.set_cookie(
            REFRESH_COOKIE, refresh,
            max_age=_ttl(data, "refresh_expires_in", refresh, 30 * 24 * 3600), **COOKIE_KW
        )


# Отозванные access-токены. У «Бедолаги» access отзыву не подлежит: их logout гасит
# только refresh, а выданный JWT остаётся годным до конца срока (~15 минут). На чужом
# компьютере это значит, что «Выйти» не закрывает доступ немедленно, поэтому список
# держим у себя: хранится отпечаток токена и его срок, дольше срока запись не живёт.
# Память процесса: перезапуск адаптера список теряет — за это время токены протухают сами.
_REVOKED: dict[str, float] = {}


def _token_expiry(token: str) -> float:
    """До какого момента держать токен в списке отозванных: дольше его же срока
    запись бессмысленна — токен и так перестанет приниматься."""
    exp = _jwt_exp(token)
    # Срока не видно — держим час, дольше их access всё равно не живёт.
    return exp if exp is not None else time.time() + 3600


def _revoke(token: str) -> None:
    now = time.time()
    for key, until in list(_REVOKED.items()):
        if until <= now:
            del _REVOKED[key]
    _REVOKED[hashlib.sha256(token.encode()).hexdigest()] = _token_expiry(token)


def _revoked(token: str | None) -> bool:
    if not token:
        return False
    until = _REVOKED.get(hashlib.sha256(token.encode()).hexdigest())
    return until is not None and until > time.time()


def _load_routes() -> dict[str, Any]:
    try:
        return json.loads(ROUTE_MAP_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — без карты адаптер бесполезен, но падать нельзя
        return {"routes": {}, "todo": []}


ROUTES: dict[str, Any] = _load_routes()

# Пути с параметрами (`/api/subscription/devices/{hwid}`) нельзя искать по точному
# совпадению — сравниваем по шаблону и переносим значения в их шаблон цели.
_PARAM = re.compile(r"\{([^}]+)\}")


def _compile(pattern: str) -> re.Pattern[str]:
    parts = []
    last = 0
    for m in _PARAM.finditer(pattern):
        parts.append(re.escape(pattern[last:m.start()]))
        parts.append(f"(?P<{m.group(1)}>[^/]+)")
        last = m.end()
    parts.append(re.escape(pattern[last:]))
    return re.compile("^" + "".join(parts) + "$")


# Сначала пути без параметров: точное совпадение всегда важнее шаблона.
_MATCHERS: list[tuple[re.Pattern[str], str, dict[str, Any]]] = sorted(
    ((_compile(p), p, r) for p, r in ROUTES.get("routes", {}).items()),
    key=lambda item: ("{" in item[1], -len(item[1])),
)

client: httpx.AsyncClient | None = None
panel: httpx.AsyncClient | None = None
admin: httpx.AsyncClient | None = None
state = State(STATE_PATH)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Один клиент на весь процесс: соединения к боту переиспользуются."""
    global client, panel, admin
    async with httpx.AsyncClient(base_url=UPSTREAM, timeout=TIMEOUT) as c:
        client = c
        if ADMIN_TOKEN:
            admin = httpx.AsyncClient(
                base_url=UPSTREAM, timeout=TIMEOUT,
                headers={"x-api-key": ADMIN_TOKEN},
            )
        if PANEL_URL and PANEL_TOKEN:
            async with httpx.AsyncClient(
                base_url=PANEL_URL,
                timeout=TIMEOUT,
                headers={"authorization": f"Bearer {PANEL_TOKEN}"},
            ) as p:
                panel = p
                yield
            panel = None
        else:
            yield
    if admin is not None:
        await admin.aclose()
    client = None


app = FastAPI(
    title="Cabinet ↔ Bedolaga adapter", docs_url=None, redoc_url=None, lifespan=lifespan
)


@app.middleware("http")
async def csrf_guard(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Проверка источника до всякой работы: и для моста авторизации, и для прокси."""
    if _csrf_blocked(request):
        return Response(
            json.dumps({"detail": "Недопустимый источник запроса"}, ensure_ascii=False),
            403, media_type="application/json",
        )
    return await call_next(request)


# Ставится последним, поэтому оборачивает всё остальное, включая проверку выше.
@app.middleware("http")
async def json_errors(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Последний рубеж: наружу всегда JSON, даже когда упало что-то незапланированное.

    Своё падение uvicorn отдаёт как `text/plain` «Internal Server Error», а
    кабинет разбирает ответ как JSON и падает уже сам — человек видит пустой
    экран, и настоящей причины не видно ни в браузере, ни в интерфейсе. Точечные
    `except` в обработчиках остаются на месте: они говорят пользователю, ЧТО
    именно не вышло. Сюда доходит только то, что никто не предусмотрел, — такому
    честное имя одно: внутренняя ошибка.

    Traceback пишем в лог адаптера и никогда наружу: в нём пути, версии и куски
    чужих ответов, посетителю их знать незачем.
    """
    try:
        return await call_next(request)
    except Exception:  # noqa: BLE001 — на то и последний рубеж
        traceback.print_exc()
        return Response(
            json.dumps({"detail": "Внутренняя ошибка адаптера"}, ensure_ascii=False),
            500, media_type="application/json",
        )


def _our_path(request_path: str) -> str:
    """Путь, каким его знает кабинет: nginx уже переписал `/api/*` в `/api/v1/public/*`
    (а `/api/admin/*` — в `/api/v1/admin/*`)."""
    if request_path.startswith("/api/v1/public/"):
        return "/api/" + request_path[len("/api/v1/public/"):]
    if request_path.startswith("/api/v1/admin/"):
        return "/api/admin/" + request_path[len("/api/v1/admin/"):]
    return request_path


def _resolve(ours: str) -> str | None:
    """Наш путь → путь «Бедолаги» с подставленными параметрами, либо None."""
    for rx, _pattern, route in _MATCHERS:
        m = rx.match(ours)
        if not m:
            continue
        target = route["target"]
        for name, raw in m.groupdict().items():
            # Значение из пути идёт в URL апстрима, поэтому экранируем: иначе
            # «?» или «#» внутри параметра уезжают к нему как query/фрагмент.
            value = quote(raw, safe="")
            # Их шаблон может звать параметр иначе (`{id}` ↔ `{ticket_id}`),
            # поэтому подставляем по позиции, если имя не совпало.
            if "{" + name + "}" in target:
                target = target.replace("{" + name + "}", value)
            else:
                # lambda, а не строка: в значении может быть «\», и re.sub принял
                # бы его за экранирование группы.
                target = _PARAM.sub(lambda _m, v=value: v, target, count=1)
        return target
    return None


# Свои обработчики тоже бывают с параметром в пути (`/api/support/tickets/{id}`),
# и точным сравнением строки их не найти. Шаблоны компилируем один раз при старте.
_OWN_MATCHERS: list[tuple[str, re.Pattern[str], Any]] = [
    (method, _compile(path), fn)
    for (method, path), fn in HANDLERS.items()
    if "{" in path
]


def _own_handler(method: str, ours: str):
    """Наш обработчик для пути и значения его параметров."""
    exact = HANDLERS.get((method.upper(), ours))
    if exact is not None:
        return exact, {}
    for m, rx, fn in _OWN_MATCHERS:
        if m != method.upper():
            continue
        hit = rx.match(ours)
        if hit:
            return fn, hit.groupdict()
    return None, {}


def _serves(method: str, path: str) -> bool:
    """Отдаём ли мы такой путь этим методом — по карте маршрутов (свои
    обработчики compose.py проверяет сам)."""
    for rx, _pattern, route in _MATCHERS:
        if rx.match(path):
            return method.upper() in {m.upper() for m in route.get("methods", [])}
    return False


# Карту читаем один раз при старте; если её не оказалось, зонд не ставим — тогда
# адаптер вообще не заявляет список возможностей и кабинет показывает всё.
if ROUTES.get("routes"):
    compose.set_route_probe(_serves)


def _not_implemented(ours: str) -> Response:
    """Явный отказ вместо молчания: видно, какой ручки не хватает."""
    return Response(
        content=json.dumps(
            {"detail": f"Адаптер пока не умеет {ours} — нет соответствия в «Бедолаге»"},
            ensure_ascii=False,
        ),
        status_code=501,
        media_type="application/json",
    )


@app.api_route("/adapter/health", methods=["GET", "HEAD"])
async def health() -> dict[str, Any]:
    """Самопроверка: сколько путей закрыто картой, сколько собираем сами и сколько ещё нет."""
    return {
        "upstream": UPSTREAM,
        # Панель Remnawave: источник данных, которые боту не принадлежат.
        "panel": bool(panel),
        # Админский токен бота и своя память — от них зависит заморозка.
        "admin_token": bool(admin),
        "state": state.available,
        "mapped": len(ROUTES.get("routes", {})),
        "composed": len(HANDLERS),
        # Сколько путей кабинета мы НЕ переводим (у бота их нет). Отдельно
        # пользовательские и админские: первые видит человек, вторые — только владелец.
        "todo": len(ROUTES.get("todo", [])),
        "todo_admin": len(ROUTES.get("todo_admin", [])),
    }


class Unreachable(Exception):
    """Бот не ответил вовсе. Пользователю — понятная строка, а не «500»."""


async def _call(method: str, path: str, **kw: Any) -> httpx.Response:
    assert client is not None  # клиент живёт весь процесс, см. lifespan
    try:
        return await client.request(method, path, **kw)
    except httpx.HTTPError as exc:
        raise Unreachable(str(exc)) from exc


@app.post("/api/v1/public/auth/{kind:path}")
async def auth_bridge(kind: str, request: Request) -> Response:
    """Вход, регистрация, обновление и выход: единственное место, где живут токены."""
    try:
        return await _auth_bridge(kind, request)
    except Unreachable as exc:
        # Форма входа — единственная страница, куда доходит новый человек:
        # техническая английская строка тут недопустима.
        return Response(
            json.dumps({"detail": f"Сервис временно недоступен: {exc}"}, ensure_ascii=False),
            502, media_type="application/json",
        )


async def _auth_bridge(kind: str, request: Request) -> Response:
    ours = "/api/auth/" + kind
    body = await request.body()

    if ours == "/api/auth/refresh":
        token = request.cookies.get(REFRESH_COOKIE)
        if not token:
            return Response('{"detail":"Нет сессии"}', 401, media_type="application/json")
        up = await _call("POST", "/cabinet/auth/refresh", json={"refresh_token": token})
        if up.status_code in (401, 403):
            # Протухший refresh — гасим куки, кабинет отправит пользователя на вход.
            out = Response("{}", 401, media_type="application/json")
            out.delete_cookie(ACCESS_COOKIE, path="/")
            out.delete_cookie(REFRESH_COOKIE, path="/")
            return out
        if up.status_code >= 400:
            # Сбой на их стороне (500 моргнула база, 502/504 от прокси, 503 «тех. работы»)
            # — это НЕ отзыв сессии. Куки не трогаем: иначе короткий сбой у бота
            # разлогинивает всех разом, и все они разом идут во вход под рейт-лимит.
            return Response(
                json.dumps({"detail": "Сервис временно недоступен"}, ensure_ascii=False),
                503 if up.status_code == 503 else 502,
                media_type="application/json",
            )
        out = Response("{}", 200, media_type="application/json")
        _set_session(out, up.json())
        return out

    if ours == "/api/auth/logout":
        access = request.cookies.get(ACCESS_COOKIE)
        if access:
            _revoke(access)
        token = request.cookies.get(REFRESH_COOKIE)
        if token:
            try:
                await _call("POST", "/cabinet/auth/logout", json={"refresh_token": token})
            except httpx.HTTPError:
                pass  #их сторона молчит — куки всё равно гасим
        out = Response('{"success":true}', 200, media_type="application/json")
        out.delete_cookie(ACCESS_COOKIE, path="/")
        out.delete_cookie(REFRESH_COOKIE, path="/")
        return out

    target = LOGIN_ROUTES.get(ours)
    if not target:
        # Не вход и не выход (смена почты, сброс пароля) — обычный путь через карту.
        return await proxy("api/v1/public/auth/" + kind, request)
    # Регистрация у них отправляет письмо с подтверждением прямо в этом запросе.
    # На медленной почте обычного срока не хватает: аккаунт создан, а человек
    # видит «сервис недоступен» и регистрируется снова — в упор в «email уже
    # занят». Ждём дольше именно здесь. (timeout=None у httpx означает «ждать
    # вечно», поэтому ключ добавляем, а не подставляем пустое значение.)
    extra: dict[str, Any] = {}
    if "register" in target:
        extra["timeout"] = MONEY_TIMEOUT
        body = await _register_body(body)
    up = await _call("POST", target, content=body or None,
                     headers={"content-type": request.headers.get("content-type", "application/json"),
                              **_forwarded(request)},
                     **extra)
    if up.status_code >= 400:
        return Response(up.content, up.status_code, media_type="application/json")
    data = up.json() if up.content else {}
    if "register" in target and not any(_tokens(data)):
        # Их регистрация сессии не открывает: сперва подтверждение почты. Наш
        # кабинет после регистрации ждёт готовую сессию, и раньше получал пустые
        # «сроки жизни» — то есть считал человека вошедшим, а он молча вылетал на
        # витрину. Сначала честно пробуем войти теми же данными (в сборках без
        # обязательного подтверждения это срабатывает), и только если вход
        # закрыт — говорим прямо, что делать дальше.
        data = await _login_after_register(body, data)
        if not any(_tokens(data)):
            return Response(
                json.dumps(
                    {"detail": "Аккаунт создан. Подтвердите почту по ссылке из письма "
                               "и войдите."},
                    ensure_ascii=False,
                ),
                403, media_type="application/json",
            )
    # Кабинет ждёт только сроки жизни сессии — сами токены он не хранит.
    out = Response(json.dumps(_session_deadlines(data), ensure_ascii=False), 200,
                   media_type="application/json")
    _set_session(out, data)
    return out


class Query(dict):
    """Query-параметры запроса для наших обработчиков.

    Обычный `dict(request.query_params)` молча оставляет от `?id=1&id=2` только
    последнее значение. Словарём это остаётся ради `ctx.query.get(...)` в
    compose.py, но полный список пар лежит рядом — чтобы повторяющийся параметр
    можно было и прочитать, и передать боту без потерь.
    """

    def __init__(self, items: list[tuple[str, str]]):
        super().__init__(items)
        self._items = items

    def multi_items(self) -> list[tuple[str, str]]:
        return list(self._items)

    def getlist(self, key: str) -> list[str]:
        return [v for k, v in self._items if k == key]


@app.api_route(
    # HEAD обязателен: FastAPI, в отличие от голого Starlette, сам его к GET не
    # добавляет, и без него любой путь отвечал 405 — на HEAD ходят проверки
    # доступности (мониторинг, healthcheck прокси).
    "/{full_path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def proxy(full_path: str, request: Request) -> Response:
    ours = _our_path("/" + full_path)
    # HEAD обязан отвечать ровно тем же, чем GET, только без тела (его срежет сам
    # uvicorn, заголовки при этом остаются от полного ответа). Ни своих
    # обработчиков, ни целей в карте на HEAD нет, поэтому ищем и зовём по GET.
    method = "GET" if request.method.upper() == "HEAD" else request.method
    access = request.cookies.get(ACCESS_COOKIE)
    if _revoked(access):
        # Токен уже погашен выходом — их сторона об этом не знает, отвечаем сами.
        out = Response('{"detail":"Нет сессии"}', 401, media_type="application/json")
        out.delete_cookie(ACCESS_COOKIE, path="/")
        out.delete_cookie(REFRESH_COOKIE, path="/")
        return out

    # 1. Ручки, которые адаптер собирает сам (оформление, права, форма /auth/me).
    own, path_params = _own_handler(method, ours)
    if own is not None:
        assert client is not None
        ctx = Ctx(
            client, access, Query(request.query_params.multi_items()), panel, admin, state,
            params=path_params, body=await request.body(),
        )
        try:
            # У денежных ручек предел свой: см. compose.handler(deadline=...).
            result = await asyncio.wait_for(
                own(ctx), getattr(own, "deadline", OWN_DEADLINE)
            )
        except TimeoutError:
            # Обработчик ходит к боту несколько раз подряд, и на каждый вызов свой
            # 25-секундный таймаут: без общего дедлайна запрос кабинета висит минутами,
            # а человек всё это время смотрит на крутилку и жмёт кнопку ещё раз.
            return Response(
                json.dumps(
                    {"detail": "Бэкенд отвечает слишком долго — попробуйте позже"},
                    ensure_ascii=False,
                ),
                504, media_type="application/json",
            )
        except UpstreamError as exc:
            return Response(exc.resp.content, exc.resp.status_code, media_type="application/json")
        except NotAvailable as exc:
            return Response(
                json.dumps({"detail": str(exc)}, ensure_ascii=False), 501,
                media_type="application/json",
            )
        except httpx.HTTPError as exc:
            return Response(
                json.dumps({"detail": f"Бэкенд недоступен: {exc}"}, ensure_ascii=False),
                502, media_type="application/json",
            )
        except Exception:  # noqa: BLE001 — иначе кривое тело запроса даёт голый
            # text/plain «Internal Server Error», на разборе которого падает уже
            # сам кабинет и причина не видна ни там, ни тут.
            traceback.print_exc()
            return Response(
                json.dumps({"detail": "Внутренняя ошибка адаптера"}, ensure_ascii=False),
                500, media_type="application/json",
            )
        if isinstance(result, httpx.Response):
            # Сырой ответ (например, байты логотипа) — отдаём как есть.
            return Response(result.content, result.status_code,
                            media_type=result.headers.get("content-type"))
        out = Response(json.dumps(result, ensure_ascii=False), 200, media_type="application/json")
        if ours == "/api/account/delete":
            # Аккаунта больше нет — гасим сессию здесь же, как делает наш бэкенд.
            if access:
                _revoke(access)
            out.delete_cookie(ACCESS_COOKIE, path="/")
            out.delete_cookie(REFRESH_COOKIE, path="/")
        return out

    # 2. Простая трансляция пути по карте.
    target = _resolve(ours)
    if target is None:
        return _not_implemented(ours)

    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
    headers.update(_forwarded(request))
    # Мост: их API ждёт Bearer, а у нас сессия живёт в HttpOnly-куке.
    if access:
        headers["authorization"] = f"Bearer {access}"
    body = await request.body()
    try:
        upstream = await _call(
            method,
            target,
            # Список пар, а не словарь: `?tag=a&tag=b` в словаре схлопывается в
            # последнее значение, и бот получает половину фильтра.
            params=request.query_params.multi_items(),
            content=body or None,
            headers=headers,
        )
    except (httpx.HTTPError, Unreachable) as exc:
        return Response(
            content=json.dumps({"detail": f"Бэкенд недоступен: {exc}"}, ensure_ascii=False),
            status_code=502,
            media_type="application/json",
        )

    passthrough = {
        k: v for k, v in upstream.headers.items()
        if k.lower() not in _HOP_BY_HOP and not _drop_from_upstream(k)
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=passthrough,
        media_type=upstream.headers.get("content-type"),
    )
