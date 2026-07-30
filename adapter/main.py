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
"""

from __future__ import annotations

import json
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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

# Заголовки, которые нельзя тащить между хостами: их выставляет транспорт.
_HOP_BY_HOP = {
    "host", "connection", "keep-alive", "transfer-encoding", "upgrade",
    "content-length", "proxy-authorization", "te", "trailer",
    # Адрес клиента подставляем сами (см. _client_ip): пришедший от браузера
    # заголовок доверять нельзя — он подделывается одной строкой.
    "x-forwarded-for", "x-real-ip", "x-forwarded-proto",
}


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


def _session_deadlines(data: dict[str, Any]) -> dict[str, str | None]:
    """Сроки жизни сессии в нашем виде: у них `expires_in` в секундах, у нас — метка времени."""
    def stamp(key: str, seconds_key: str, default: int) -> str | None:
        if data.get(key):
            return str(data[key])
        seconds = data.get(seconds_key)
        seconds = int(seconds) if isinstance(seconds, (int, float, str)) and str(seconds).isdigit() else default
        return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()

    return {
        "expires_at": stamp("expires_at", "expires_in", 900),
        # Срок refresh они не сообщают вовсе; 30 дней — их же дефолт настройки.
        "refresh_expires_at": stamp("refresh_expires_at", "refresh_expires_in", 30 * 24 * 3600),
    }


def _set_session(resp: Response, data: dict[str, Any]) -> None:
    """Кладёт их токены в наши HttpOnly-куки. Кабинету токены не показываем."""
    access = data.get("access_token") or data.get("accessToken")
    refresh = data.get("refresh_token") or data.get("refreshToken")
    if access:
        resp.set_cookie(ACCESS_COOKIE, access, **COOKIE_KW)
    if refresh:
        resp.set_cookie(REFRESH_COOKIE, refresh, **COOKIE_KW)


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
        for name, value in m.groupdict().items():
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


@app.get("/adapter/health")
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
        "todo": len(ROUTES.get("todo", [])),
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
        if up.status_code >= 400:
            # Протухший refresh — гасим куки, кабинет отправит пользователя на вход.
            out = Response("{}", 401, media_type="application/json")
            out.delete_cookie(ACCESS_COOKIE, path="/")
            out.delete_cookie(REFRESH_COOKIE, path="/")
            return out
        out = Response("{}", 200, media_type="application/json")
        _set_session(out, up.json())
        return out

    if ours == "/api/auth/logout":
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
    up = await _call("POST", target, content=body or None,
                     headers={"content-type": request.headers.get("content-type", "application/json"),
                              **_forwarded(request)})
    if up.status_code >= 400:
        return Response(up.content, up.status_code, media_type="application/json")
    data = up.json() if up.content else {}
    # Кабинет ждёт только сроки жизни сессии — сами токены он не хранит.
    out = Response(json.dumps(_session_deadlines(data), ensure_ascii=False), 200,
                   media_type="application/json")
    _set_session(out, data)
    return out


@app.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def proxy(full_path: str, request: Request) -> Response:
    ours = _our_path("/" + full_path)
    access = request.cookies.get(ACCESS_COOKIE)

    # 1. Ручки, которые адаптер собирает сам (оформление, права, форма /auth/me).
    own, path_params = _own_handler(request.method, ours)
    if own is not None:
        assert client is not None
        ctx = Ctx(
            client, access, dict(request.query_params), panel, admin, state,
            params=path_params, body=await request.body(),
        )
        try:
            result = await own(ctx)
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
        if isinstance(result, httpx.Response):
            # Сырой ответ (например, байты логотипа) — отдаём как есть.
            return Response(result.content, result.status_code,
                            media_type=result.headers.get("content-type"))
        return Response(json.dumps(result, ensure_ascii=False), 200, media_type="application/json")

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
            request.method,
            target,
            params=dict(request.query_params),
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
        k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=passthrough,
        media_type=upstream.headers.get("content-type"),
    )
