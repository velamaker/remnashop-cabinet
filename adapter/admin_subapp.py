"""Админка: экран «Подписка в прилож.» — настройки страницы подписки Remnawave.

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ — то же правило, что у `admin_panel.py` и `admin_cabinet.py`:
админских ручек в контракте под сорок, переводят их несколькими руками сразу, и
один файл на всех — гарантированный конфликт. Регистрация общая, тем же
декоратором `handler`; сам себя модуль не импортирует — его подхватывает
`compose.py` по маске `admin_*.py`, в образ он попадает строкой `COPY admin*.py`
в `adapter/Dockerfile`.

ОТКУДА ДАННЫЕ. Только панель Remnawave (`/api/subscription-settings`), и это не
выбор из двух источников, а единственный существующий: у «Бедолаги» этих
настроек нет ни в кабинетном API (в `app/cabinet/routes/` есть admin_remnawave с
нодами, сквадами, инбаундами и системой — страницы подписки нет), ни в машинном.
Её `subscriptionSettings` в `app/external/remnawave_api.py` — это поле СКВАДА,
совсем другая сущность. Тот же случай, что вкладка «Хосты» в `admin_panel.py`:
данные боту не принадлежат, поэтому раздел не гасим, а берём из панели.

Что именно правится: заголовок профиля, ссылка поддержки, интервал обновления,
кнопка сайта, объявление Happ, конфиг маршрутизации и произвольные заголовки
ответа подписки. Приложение (Happ и совместимые) читает их при импорте ссылки —
отсюда «брендинг, плашка, тема и роутинг подтягиваются в приложение». Остальное
из того же документа панели (шаблоны `customRemarks`, `responseRules`,
`hwidSettings`) мы не трогаем вовсе: PATCH уходит частичный, только с теми
полями, что пришли с экрана.

КТО РЕШАЕТ, ПУСКАТЬ ЛИ. Панель адаптер дёргает СВОИМ ключом, а он с полными
правами: без проверки настройки страницы подписки менял бы любой, кто вошёл в
кабинет, — и одним сохранением ломал бы ссылки всем пользователям сразу.
Спрашивать за нас некого (их ручки, которая бы это проверила, не существует),
поэтому право подтверждает их же RBAC по списку `/cabinet/auth/me/permissions`:
чтение — `remnawave:read`, запись — `remnawave:manage`. Те же права, которыми у
них закрыты остальные экраны панели (`app/cabinet/routes/admin_remnawave.py`).
Не смогли спросить — считаем, что права нет. Оговорка, о которой честнее сказать
сразу: кроме ролей у них есть точечные ABAC-запреты, а список прав их не
показывает — персональный deny именно на `remnawave:manage` мы не увидим.

ПАНЕЛЬ НЕ НАСТРОЕНА — честный 501 с причиной, а не пустая форма: сохранять
настройки страницы подписки некуда, и форма, которая молча ничего не меняет,
хуже отказа.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import re
import socket
from fnmatch import fnmatchcase
from typing import Any
from urllib.parse import urlsplit

import httpx

from compose import Ctx, NotAvailable, UpstreamError, handler

# Их права на раздел панели (`app/cabinet/routes/admin_remnawave.py`).
_READ = "remnawave:read"
_MANAGE = "remnawave:manage"

# Happ обрезает объявление до 200 символов, заголовок профиля — до 25. Лимиты те
# же, что у нашего бэкенда (admin_src/.../subscription_app.py): экран один и тот
# же, и расхождение означало бы, что в одной установке текст влезает, а в другой
# молча режется приложением.
HAPP_ANNOUNCE_MAX = 200
PROFILE_TITLE_MAX = 25

# Интервал обновления профиля, часы — границы формы кабинета.
_INTERVAL_MIN = 1
_INTERVAL_MAX = 168

_SETTINGS_PATH = "/api/subscription-settings"

# Наше имя поля ↔ имя в панели. Панель говорит camelCase, кабинет — snake_case.
_FIELDS = {
    "profile_title": "profileTitle",
    "support_link": "supportLink",
    "profile_update_interval": "profileUpdateInterval",
    "is_profile_webpage_url_enabled": "isProfileWebpageUrlEnabled",
    "happ_announce": "happAnnounce",
    "happ_routing": "happRouting",
    "custom_response_headers": "customResponseHeaders",
}

# Happ ждёт в заголовке routing САМ deep-link (happ://routing/onadd/<base64>), а
# не ссылку на файл с ним. Ссылку (github raw и т.п.) разворачиваем сами.
_ROUTING_DEEPLINK_RE = re.compile(r"happ://routing/\S+")

# Имя HTTP-заголовка — token из RFC 7230: буквы, цифры и вот эти знаки.
_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")

# Сколько тянем из файла с маршрутизацией. Deep-link лежит в первых килобайтах
# любого нормального конфига, а без предела админ ссылкой на гигабайтный файл
# кладёт адаптер целиком (он однопроцессный).
_ROUTING_MAX_BYTES = 1 << 20
_ROUTING_TIMEOUT = 10.0


def _fail(status: int, detail: str) -> UpstreamError:
    """Отказ самого адаптера в той же форме, что и ошибка бота."""
    return UpstreamError(httpx.Response(status, json={"detail": detail}))


# --- права -------------------------------------------------------------------


async def _require(ctx: Ctx, permission: str) -> None:
    """Право в ИХ RBAC. Сравниваем по их правилу: право пользователя — ШАБЛОН
    (`*:*`, `remnawave:*`), требуемое — строка (`permission_service`). Нет ответа
    — нет права (fail-closed): молчание их API не повод пускать к панели."""
    data = await ctx.json("/cabinet/auth/me/permissions", {}, soft=True) or {}
    granted = data.get("permissions")
    ok = isinstance(granted, list) and any(
        isinstance(p, str) and fnmatchcase(permission, p) for p in granted
    )
    if not ok:
        raise _fail(403, f"Недостаточно прав в боте: нужно право «{permission}»")


# --- разговор с панелью ------------------------------------------------------


def _panel_client(ctx: Ctx) -> httpx.AsyncClient:
    """Клиент панели, которым можно и ЧИТАТЬ, и ПИСАТЬ.

    Наружу `Ctx` отдаёт только чтение (`panel_json`), а этот экран настройки
    ещё и меняет. Свой httpx-клиент здесь заводить нельзя: пришлось бы второй
    раз разбирать REMNAWAVE_URL (у разных ботов он записан по-разному, см.
    `main._panel_url`), держать вторую пару соединений и рисковать разъехаться с
    основным. Добавлять метод в `compose.Ctx` — трогать общий файл, который в
    это же время правят соседние разделы. Поэтому берём тот самый клиент, что
    собран в `main.lifespan`.
    """
    client: httpx.AsyncClient | None = getattr(ctx, "_panel", None)
    if client is None:
        raise NotAvailable(
            "Настройки страницы подписки живут в панели Remnawave, а её адрес и "
            "токен адаптеру не заданы (REMNAWAVE_URL / REMNAWAVE_TOKEN)"
        )
    return client


def _panel_detail(resp: httpx.Response) -> str:
    """Причина отказа панели человеческими словами. У Remnawave это
    `{"message": ...}` или `{"errors": [...]}`, но бывает и голый текст."""
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        for key in ("message", "detail", "error"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    text = (resp.text or "").strip()
    return text[:200] if text else f"код {resp.status_code}"


async def _panel(ctx: Ctx, method: str, **kw: Any) -> dict[str, Any]:
    """Запрос к настройкам подписки в панели — с честной ошибкой.

    `ctx.panel_json` для этого экрана не годится намеренно: он глотает любую
    беду и отдаёт default. Там это правильно (кусочек чужого экрана деградирует
    мягко), а здесь панель — ЕДИНСТВЕННЫЙ источник, и «форма не загрузилась, а
    почему — неизвестно» вместо «панель ответила 401, протух токен» скрывает
    ровно ту причину, которую человек и ищет.
    """
    client = _panel_client(ctx)
    try:
        resp = await client.request(method, _SETTINGS_PATH, **kw)
    except httpx.HTTPError as exc:
        raise _fail(502, f"Панель Remnawave недоступна: {exc}") from exc
    if resp.status_code >= 400:
        raise _fail(
            502,
            f"Панель Remnawave отклонила запрос ({resp.status_code}): {_panel_detail(resp)}",
        )
    try:
        body = resp.json()
    except ValueError as exc:
        raise _fail(502, "Панель Remnawave ответила не-JSON") from exc
    # У Remnawave полезная часть всегда под "response".
    data = body.get("response", body) if isinstance(body, dict) else body
    if not isinstance(data, dict):
        raise _fail(502, "Панель Remnawave не отдала настройки страницы подписки")
    return data


# --- форма ответа ------------------------------------------------------------


def _header_display(value: str) -> str:
    """Обратно к человекочитаемому: base64:… → текст (для формы в админке)."""
    if not value.startswith("base64:"):
        return value
    try:
        return base64.b64decode(value[len("base64:"):]).decode("utf-8")
    except Exception:
        return value


def _shape(raw: dict[str, Any]) -> dict[str, Any]:
    """Ответ панели → форма, которую ждёт кабинет (`SubscriptionAppSettings`)."""
    data: dict[str, Any] = {name: raw.get(panel) for name, panel in _FIELDS.items()}
    headers = raw.get("customResponseHeaders")
    data["custom_response_headers"] = (
        {str(k): _header_display(str(v)) for k, v in headers.items()}
        if isinstance(headers, dict)
        else None
    )
    data["limits"] = {"announce": HAPP_ANNOUNCE_MAX, "title": PROFILE_TITLE_MAX}
    return data


# --- проверка того, что пришло с экрана --------------------------------------


def _as_text(name: str, value: Any) -> str | None:
    """Строковое поле. Пусто = «очистить»: панель принимает null.

    Одни пробелы — это тоже «очистить», а не пустая строка: сохранив " ",
    получили бы заголовок профиля из пробела, который в приложении выглядит как
    пропавшее название сервиса.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise _fail(400, f"Поле «{name}» должно быть строкой")
    return value.strip() or None


def _as_interval(value: Any) -> int | None:
    if value is None or value == "":
        return None
    # bool — подкласс int, и `True` иначе доехал бы до панели как 1 час.
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise _fail(400, "Интервал обновления должен быть числом часов")
    try:
        hours = int(str(value).strip())
    except ValueError as exc:
        raise _fail(400, "Интервал обновления должен быть числом часов") from exc
    if not _INTERVAL_MIN <= hours <= _INTERVAL_MAX:
        raise _fail(
            400,
            f"Интервал обновления — от {_INTERVAL_MIN} до {_INTERVAL_MAX} часов",
        )
    return hours


def _minify_json(value: str) -> str:
    """JSON-значение (тема оформления) — одной строкой без переносов: заголовок
    их не переживёт."""
    stripped = value.strip()
    if not stripped.startswith("{"):
        return value
    try:
        return json.dumps(json.loads(stripped), separators=(",", ":"), ensure_ascii=False)
    except ValueError:
        return value


def _header_safe(value: str) -> str:
    """Значение, которое можно поставить в HTTP-заголовок.

    Панель отдаёт custom-заголовки как есть, а Node роняет ответ на любом
    символе вне latin-1 (`ERR_INVALID_CHAR`) — то есть кириллица в плашке это
    502 на ВСЕХ ссылках подписки разом. Поэтому не-latin1 кодируем в `base64:` —
    формат, который Happ понимает. (Свои поля, например announce, панель кодирует
    сама.)
    """
    try:
        value.encode("latin-1")
        return value
    except UnicodeEncodeError:
        return "base64:" + base64.b64encode(value.encode("utf-8")).decode("ascii")


def _as_headers(value: Any) -> dict[str, str]:
    """Произвольные заголовки ответа подписки.

    `null` от кабинета означает «заголовков нет», но панель null не принимает —
    это пустой объект. Пустые имена выкидываем: панель их сохранит, а ответ с
    безымянным заголовком не соберётся.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _fail(400, "Дополнительные заголовки должны быть парами имя/значение")

    out: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip()
        if not key:
            continue
        # Имя и перевод строки в значении проверяем СВОИ, хотя наш бэкенд этого
        # не делает: панель кладёт эти пары прямо в HTTP-ответ подписки, поэтому
        # кривое имя ломает ответ целиком, а «\r\n» в значении — это подстановка
        # чужих заголовков в ответ (инъекция). Отказ с причиной здесь честнее
        # молчаливого сохранения того, что потом положит все ссылки.
        if not _HEADER_NAME_RE.match(key):
            raise _fail(
                400,
                f"Имя заголовка «{key}» недопустимо: только латинские буквы, "
                "цифры и знаки -_.",
            )
        # Схлопываем ПЕРЕД проверкой на переносы: тему оформления из приложения
        # вставляют красиво отформатированным JSON, и она законно многострочная —
        # после минификации от переносов не остаётся ничего. А вот перенос в
        # обычном значении так и останется, и его отвергаем (см. выше).
        text = _minify_json("" if raw_value is None else str(raw_value))
        if any(ch in text for ch in ("\r", "\n", "\0")):
            raise _fail(400, f"Значение заголовка «{key}» не должно содержать переносов строк")
        out[key] = _header_safe(text)
    return out


# --- маршрутизация -----------------------------------------------------------


async def _is_public_host(url: str) -> bool:
    """Ведёт ли URL на публичный хост.

    Ссылку на конфиг задаёт админ, а тянет её адаптер — изнутри docker-сети, где
    рядом стоят бот, панель и обе базы. Без проверки это готовый SSRF: `http://
    remnawave:3000/...` или метаданные облака `169.254.169.254`. Пропускаем
    только те URL, у которых ВСЕ адреса публичные (у хоста бывает несколько
    A/AAAA-записей). Резолвим через loop.getaddrinfo, а не socket напрямую:
    адаптер однопроцессный, и синхронный DNS на пару секунд подвесил бы всех.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    host = parts.hostname
    if not host:
        return False
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, port, proto=socket.IPPROTO_TCP
        )
    except (socket.gaierror, OSError, ValueError):
        return False
    if not infos:
        return False
    for info in infos:
        try:
            addr = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        except ValueError:
            return False
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        ):
            return False
    return True


async def _download(url: str) -> str:
    """Начало файла по ссылке. `follow_redirects=False` — иначе публичный 30x
    увёл бы нас внутрь сети мимо проверки выше."""
    chunks: list[bytes] = []
    size = 0
    try:
        async with httpx.AsyncClient(
            timeout=_ROUTING_TIMEOUT, follow_redirects=False
        ) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= _ROUTING_MAX_BYTES:
                        break
    except httpx.HTTPError as exc:
        raise _fail(502, f"Не удалось скачать конфиг маршрутизации: {exc}") from exc
    return b"".join(chunks).decode("utf-8", "replace")


async def _resolve_routing(value: str) -> str:
    """Ссылка на файл с deep-link → сам deep-link. Готовый deep-link — как есть.

    GitHub-страницу (/blob/) тихо переводим в raw: по обычной ссылке отдаётся
    HTML, в котором deep-link не найти.
    """
    value = value.strip()
    if value.startswith("happ://"):
        return value
    if not value.startswith(("http://", "https://")):
        raise _fail(
            400,
            "Нужна ссылка на конфиг (http/https) или готовый deep-link happ://routing/…",
        )

    url = value
    if "github.com" in url and "/blob/" in url:
        url = url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/", 1)

    if not await _is_public_host(url):
        raise _fail(
            400,
            "Ссылка должна вести на публичный хост (внутренние адреса запрещены).",
        )

    match = _ROUTING_DEEPLINK_RE.search(await _download(url))
    if not match:
        raise _fail(
            400,
            "По ссылке нет deep-link вида happ://routing/… — приложение такой конфиг "
            "не поймёт. Соберите правила в конструкторе Happ Routing Builder и вставьте "
            "полученный happ://routing/onadd/… сюда.",
        )
    return match.group(0)


def _default_routing_profile(brand: str) -> str:
    """Базовый профиль маршрутизации: всё в туннель, РФ-ресурсы — напрямую.

    Ровно тот же профиль, что собирает наш бэкенд, — кнопка на экране одна и та
    же, и «базовый профиль» обязан означать одно и то же в любой установке.
    Российские сайты, банки и госуслуги из-за рубежа часто не открываются,
    поэтому ходят мимо VPN. Категории берутся из geo-файлов, которые приложение
    качает само (свои Geoipurl/Geositeurl НЕ задаём: крупные сборки CDN не
    отдаёт, а телефону незачем тянуть десятки мегабайт).
    """
    profile = {
        "Name": brand[:PROFILE_TITLE_MAX],
        "GlobalProxy": "true",
        "UseChunkFiles": "false",
        "RemoteDns": "8.8.8.8",
        "RemoteDNSType": "DoH",
        "RemoteDNSDomain": "https://8.8.8.8/dns-query",
        "RemoteDNSIP": "8.8.8.8",
        "DomesticDns": "77.88.8.8",
        "DomesticDNSType": "DoH",
        "DomesticDNSDomain": "https://77.88.8.8/dns-query",
        "DomesticDNSIP": "77.88.8.8",
        "DirectSites": [
            "geosite:category-ru",
            "geosite:category-gov-ru",
            "geosite:category-bank-ru",
            "geosite:private",
        ],
        "DirectIp": ["geoip:ru", "geoip:private"],
        "ProxySites": [],
        "ProxyIp": [],
        "BlockSites": [],
        "BlockIp": [],
        "DnsHosts": {"lkfl2.nalog.ru": "213.24.64.175", "lknpd.nalog.ru": "213.24.64.181"},
        "RouteOrder": "block-proxy-direct",
        "DomainStrategy": "IPIfNonMatch",
        "FakeDNS": "false",
    }
    payload = base64.b64encode(
        json.dumps(profile, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    return "happ://routing/onadd/" + payload


# --- ручки -------------------------------------------------------------------


@handler("GET", "/api/admin/subscription-app")
async def subscription_app(ctx: Ctx) -> dict[str, Any]:
    """Экран целиком: что сейчас стоит в панели плюс лимиты для полей формы."""
    await _require(ctx, _READ)
    return _shape(await _panel(ctx, "GET"))


@handler("PUT", "/api/admin/subscription-app")
async def subscription_app_save(ctx: Ctx) -> dict[str, Any]:
    """Сохранение. Меняем ТОЛЬКО пришедшие поля.

    Панель хранит настройки страницы подписки одним документом, где рядом лежат
    шаблоны надписей, правила ответов и лимит устройств. PATCH у неё частичный,
    и это важно: собери мы тело «из всех полей», экран кабинета (он знает про
    семь) затирал бы соседние настройки при каждом сохранении.
    """
    await _require(ctx, _MANAGE)
    body = ctx.payload()

    changes: dict[str, Any] = {}
    for name, panel_name in _FIELDS.items():
        if name not in body:
            continue
        value = body[name]
        if name == "profile_update_interval":
            changes[panel_name] = _as_interval(value)
        elif name == "is_profile_webpage_url_enabled":
            if not isinstance(value, bool):
                raise _fail(400, "Кнопка сайта в приложении — это «да» или «нет»")
            changes[panel_name] = value
        elif name == "custom_response_headers":
            changes[panel_name] = _as_headers(value)
        else:
            text = _as_text(name, value)
            if name == "profile_title" and text and len(text) > PROFILE_TITLE_MAX:
                raise _fail(400, f"Название сервиса — до {PROFILE_TITLE_MAX} символов")
            if name == "happ_announce" and text and len(text) > HAPP_ANNOUNCE_MAX:
                raise _fail(400, f"Объявление — до {HAPP_ANNOUNCE_MAX} символов")
            if name == "happ_routing" and text:
                text = await _resolve_routing(text)
            changes[panel_name] = text

    # uuid панель требует в теле PATCH, а знает его только она сама.
    current = await _panel(ctx, "GET")
    uuid = current.get("uuid")
    if not uuid:
        raise _fail(502, "Панель Remnawave не сказала uuid настроек подписки")
    if not changes:
        # Менять нечего — не дёргаем панель зря, но и не врём «сохранено»
        # молча: отдаём то же, что отдал бы GET.
        return _shape(current)

    return _shape(await _panel(ctx, "PATCH", json={"uuid": uuid, **changes}))


@handler("POST", "/api/admin/subscription-app/routing/default")
async def subscription_app_default_routing(ctx: Ctx) -> dict[str, str]:
    """Готовый deep-link базового профиля — фронт подставляет его в поле routing.

    Панель здесь не нужна вовсе (строка собирается на месте), но право спросить
    обязаны: кнопка живёт на этом же экране, и открывать её тому, кому раздел
    закрыт, незачем. Имя профиля — бренд из настроек бота: у чужого бота своё
    название, и подставлять наше было бы выдумкой.
    """
    await _require(ctx, _READ)
    brand = await ctx.json("/cabinet/branding", {}, soft=True) or {}
    name = brand.get("name") if isinstance(brand, dict) else None
    return {"routing": _default_routing_profile(str(name or "VPN"))}
