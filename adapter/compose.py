"""Ручки, которые адаптер собирает сам.

Не всё, что нужно кабинету, есть у «Бедолаги» одним запросом. Оформление там
разложено по шести публичным ручкам, права — по трём, а формы ответов другие:
у нас `name`, у них `first_name` + `last_name`; у нас байты трафика, у них
гигабайты; у нас рубли строкой, у них копейки числом.

Здесь живут переводчики: каждый берёт одно или несколько их полей и отдаёт ровно
ту форму, которую кабинет ждёт по `docs/CABINET-API-CONTRACT.md`. Всё, чего у них
нет вовсе (и что не подделать), сюда не попадает — такие пути честно отвечают 501
в `main.py`, чтобы дырка была видна, а не притворялась пустым списком.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Awaitable, Callable

import httpx

# Реестр: (метод, наш путь) → обработчик. Заполняется декоратором ниже, main.py
# смотрит сюда раньше, чем в карту маршрутов.
HANDLERS: dict[tuple[str, str], Callable[["Ctx"], Awaitable[Any]]] = {}


def handler(method: str, path: str) -> Callable[..., Any]:
    def deco(fn: Callable[["Ctx"], Awaitable[Any]]) -> Callable[["Ctx"], Awaitable[Any]]:
        HANDLERS[(method.upper(), path)] = fn
        return fn

    return deco


class Ctx:
    """Контекст запроса: клиент к «Бедолаге» + токен текущей сессии."""

    def __init__(self, client: httpx.AsyncClient, token: str | None, query: dict[str, str]):
        self._client = client
        self.token = token
        self.query = query

    async def call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        headers = dict(kw.pop("headers", {}))
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        return await self._client.request(method, path, headers=headers, **kw)

    async def json(self, path: str, default: Any = None, method: str = "GET", **kw: Any) -> Any:
        """GET с мягким провалом: одна отвалившаяся ручка не должна валить сборку."""
        try:
            resp = await self.call(method, path, **kw)
        except httpx.HTTPError:
            return default
        if resp.status_code >= 400:
            return default
        try:
            return resp.json()
        except ValueError:
            return default


# --- какие возможности бэкенда доступны кабинету ----------------------------

# Кабинет один, а боты под ним разные. Вместо того чтобы показывать кнопки,
# которые упрутся в 501, кабинет спрашивает у бэкенда список возможностей.
#
# ГЛАВНОЕ ПРАВИЛО СОВМЕСТИМОСТИ: наш собственный бэкенд этого поля не отдаёт
# вовсе, и кабинет обязан считать «поля нет = умеет всё». Поэтому список ниже
# существует только в адаптере и ничего не меняет в текущей установке.
#
# Список не пишется руками «на глаз»: каждая возможность объявляет, без каких
# путей она бессмысленна, а доступность считается по тому, что адаптер реально
# отдаёт (свои обработчики + карта маршрутов). Реализовали ручку — возможность
# включилась сама.
FEATURE_REQUIREMENTS: dict[str, list[tuple[str, str]]] = {
    # --- принадлежит боту: без его ручки раздела действительно нет -----------
    # Подписки, деньги, тарифы, промо, рефералка, тикеты и учётные записи живут
    # в базе бота. Пока перевод не написан — раздел честно спрятан.
    "subscription": [("GET", "/api/subscription/current")],
    "trial": [("POST", "/api/subscription/trial")],
    "plans_public": [("GET", "/api/plans/public")],
    "purchase": [("GET", "/api/subscription/offers"), ("POST", "/api/subscription/purchase")],
    "pay_with_balance": [("POST", "/api/subscription/pay-with-balance")],
    "freeze": [("GET", "/api/subscription/freeze-status")],
    "topup": [("POST", "/api/balance/topup")],
    "autopay": [("POST", "/api/balance/autopay")],
    "points": [("POST", "/api/balance/convert-points")],
    "transactions": [("GET", "/api/balance/transactions")],
    "renew_from_balance": [("POST", "/api/balance/spend-on-renewal")],
    "promocode": [("POST", "/api/promocode/activate")],
    "referral": [("GET", "/api/referral/program")],
    "gift": [("GET", "/api/gift/my"), ("POST", "/api/gift/create")],
    "tickets": [("GET", "/api/support/tickets")],
    "notifications": [("GET", "/api/notifications")],
    "info_pages": [("GET", "/api/info")],
    "email_verify": [("POST", "/api/auth/email/request-verification")],
    "password_change": [("POST", "/api/auth/change-password")],
    "account_delete": [("DELETE", "/api/account/delete")],
    "data_export": [("GET", "/api/account/export")],
    # Админка — десятки ручек; включаем, только когда появится список
    # пользователей, иначе админ проваливается в экраны с ошибками.
    "admin": [("GET", "/api/admin/users")],
}

# --- НЕ принадлежит боту: гасить нельзя ---------------------------------------
#
# Правило владельца (29 июля): часть кабинета к боту отношения не имеет.
#   • данные панели Remnawave — статус и список серверов, устройства (HWID),
#     трафик по нодам, перевыпуск ссылки подписки. Любой бэкенд получает их из
#     панели напрямую, поэтому при чужом боте это не отключается, а берётся из
#     API панели (adapter → Remnawave), см. docs/BEDOLAGA-MAPPING.md;
#   • собственные фишки кабинета — каталог приложений, диагностика, замер
#     скорости, онбординг, темы и языки: им бэкенд не нужен вовсе;
#   • push и список сессий — механика самого кабинета, хранилище может держать
#     адаптер.
# Эти ключи в список возможностей НЕ попадают: отсутствие ключа = «доступно».
NOT_BOT_OWNED = (
    "status_page", "servers", "service_status", "server_stats", "traffic_history",
    "devices", "reissue", "apps", "push", "sessions", "speedtest", "diagnostics",
)

# Возможности, про которые одного наличия пути мало: тут адаптер знает больше.
#
# ВАЖНОЕ ПРАВИЛО (владелец, 29 июля). Гасить можно ТОЛЬКО то, что действительно
# принадлежит боту. Часть кабинета живёт не на боте вовсе: статус и список
# серверов, трафик по нодам — это данные панели Remnawave, и они доступны любому
# бэкенду напрямую через её API. Такие разделы отключать нельзя — их надо брать
# из панели. Плюс собственные фишки кабинета (каталог приложений, диагностика,
# замер скорости, онбординг) вообще ни от кого не зависят.
FEATURE_OVERRIDES: dict[str, bool] = {}

# Проверку «отдаём ли мы такой путь» ставит main.py: только он знает карту.
_serves: Callable[[str, str], bool] | None = None


def set_route_probe(probe: Callable[[str, str], bool]) -> None:
    global _serves
    _serves = probe


def features() -> dict[str, bool] | None:
    """Что умеет этот бэкенд. None = «не знаем» — кабинет тогда показывает всё.

    Осторожность здесь важнее полноты: если карта маршрутов не прочиталась,
    все возможности посчитались бы выключенными и кабинет схлопнулся бы в пустой
    экран. Лучше показать лишнюю кнопку, чем спрятать рабочий кабинет.
    """
    if _serves is None:
        return None

    def ok(method: str, path: str) -> bool:
        if (method.upper(), path) in HANDLERS:
            return True
        return bool(_serves(method, path))

    result = {
        name: all(ok(m, p) for m, p in needs)
        for name, needs in FEATURE_REQUIREMENTS.items()
    }
    result.update(FEATURE_OVERRIDES)
    return result


# --- оформление -------------------------------------------------------------

# Оформление грузится ДО первой отрисовки кабинета и не меняется поминутно, а
# собирается из шести запросов. Держим короткий кэш, иначе каждый заход в кабинет
# бьёт по боту шесть раз.
_APPEARANCE_TTL = 60.0
_appearance_cache: tuple[float, dict[str, Any]] | None = None
# Во время тех-работ кэш держим коротким: включаются и выключаются они быстро,
# и минута «всё лежит» после починки выглядит как поломка кабинета.
_MAINTENANCE_TTL = 15.0


def _maintenance_from(resp: httpx.Response) -> tuple[bool, str]:
    """Их 503 с `code: maintenance` → наш флаг тех-работ и текст для заглушки."""
    if resp.status_code != 503:
        return False, ""
    try:
        body = resp.json()
    except ValueError:
        return True, ""
    if not isinstance(body, dict):
        return True, ""
    detail = body.get("detail")
    if isinstance(detail, dict):
        body = detail
    if body.get("code") and body.get("code") != "maintenance":
        return False, ""
    message = body.get("message") or body.get("detail") or ""
    return True, message if isinstance(message, str) else ""


@handler("GET", "/api/appearance")
async def appearance(ctx: Ctx) -> dict[str, Any]:
    global _appearance_cache
    now = time.monotonic()
    if _appearance_cache and now - _appearance_cache[0] < _APPEARANCE_TTL:
        return _appearance_cache[1]

    # Тех-работы у них — это 503 с `code: maintenance` на любом кабинетном запросе,
    # и включаются они не только руками, но и сами при недоступности панели. Ловим
    # их на первом же вызове: иначе кабинет во время их тех-работ продолжает
    # показывать витрину и принимать оплату, а пользователь видит голые 503.
    brand_resp = await ctx.call("GET", "/cabinet/branding")
    maintenance, maintenance_message = _maintenance_from(brand_resp)
    brand = brand_resp.json() if brand_resp.status_code < 400 else {}
    colors = await ctx.json("/cabinet/branding/colors", {}) or {}
    widget = await ctx.json("/cabinet/branding/telegram-widget", {}) or {}
    support = await ctx.json("/cabinet/info/support-config", {}) or {}

    username = support.get("support_username") or None
    if isinstance(username, str):
        username = username.lstrip("@") or None

    data = {
        "brand_name": brand.get("name") or "VPN",
        "accent": colors.get("accent"),
        # legacy-поле: кабинет использует его как фолбэк, если нет пары тем.
        "background": colors.get("darkBackground"),
        "background_dark": colors.get("darkBackground"),
        "background_light": colors.get("lightBackground"),
        "support_username": username,
        # Их логотип отдаётся байтами по своему пути — заворачиваем в наш,
        # чтобы кабинету не пришлось знать про префикс «Бедолаги».
        "logo_url": "/api/appearance/logo" if brand.get("has_custom_logo") else None,
        "telegram_oidc_enabled": bool(widget.get("oidc_enabled")),
        # Прямая ссылка подписки: у них тумблер живёт в самой подписке
        # (hide_subscription_link), общего запрета нет — значит, показываем.
        "sub_link_enabled": True,
        "maintenance": maintenance,
        "maintenance_message": maintenance_message,
        # В тех-работы их API не пускает никого, кроме админов, — значит закрыто
        # всё, включая оплату: иначе кабинет продаст то, что не сможет выдать.
        "maintenance_block_payments": True,
        # Языки кабинета — наши собственные переводы интерфейса, их список
        # (`/cabinet/info/languages`) про язык контента бота, поэтому не сужаем.
        "enabled_languages": None,
    }
    # Что этот бэкенд умеет. Ключа нет вовсе = «умеет всё» (так ведёт себя наш
    # собственный бэкенд), поэтому при неизвестном ответе поле не добавляем.
    known = features()
    if known is not None:
        data["features"] = known
    _appearance_cache = (now - _APPEARANCE_TTL + _MAINTENANCE_TTL if maintenance else now, data)
    return data


@handler("GET", "/api/appearance/logo")
async def appearance_logo(ctx: Ctx) -> httpx.Response:
    """Логотип отдаём байтами как есть — main.py умеет вернуть сырой ответ."""
    return await ctx.call("GET", "/cabinet/branding/logo")


# --- кто я и что мне можно --------------------------------------------------


def _display_name(user: dict[str, Any]) -> str:
    parts = [user.get("first_name") or "", user.get("last_name") or ""]
    name = " ".join(p for p in parts if p).strip()
    return name or user.get("username") or user.get("email") or "Пользователь"


@handler("GET", "/api/auth/me")
async def me(ctx: Ctx) -> dict[str, Any]:
    resp = await ctx.call("GET", "/cabinet/auth/me")
    if resp.status_code >= 400:
        raise UpstreamError(resp)
    u = resp.json()
    return {
        "telegram_id": u.get("telegram_id"),
        "auth_type": (u.get("auth_type") or "email").lower(),
        "email": u.get("email"),
        "is_email_verified": bool(u.get("email_verified")),
        # Смена почты у них подтверждается отдельной ручкой; текущий адрес в
        # ожидании она не возвращает — оставляем пусто, пока не смапим статус.
        "pending_email": None,
        "name": _display_name(u),
        "username": u.get("username"),
        "language": u.get("language") or "ru",
    }


@handler("GET", "/api/auth/whoami")
async def whoami(ctx: Ctx) -> dict[str, Any]:
    """Права. Всё, чего не знаем наверняка, — закрыто (fail-closed)."""
    is_admin = bool((await ctx.json("/cabinet/auth/me/is-admin", {}) or {}).get("is_admin"))
    perms = await ctx.json("/cabinet/auth/me/permissions", {}) or {}
    user = await ctx.json("/cabinet/auth/me", {}) or {}
    role_level = perms.get("role_level") or 0
    return {
        "role": role_level or None,
        "is_admin": is_admin,
        # Разделения «только просмотр» у них в кабинетном API нет: либо админ,
        # либо нет. Не выдаём права, которых не можем подтвердить.
        "is_readonly_admin": False,
        "can_access_admin": is_admin,
        "is_owner": is_admin and role_level >= 90,
        "full_access": is_admin,
        "can_write": is_admin,
        # Пусто при full_access = «все разделы» (так это читает кабинет).
        "sections": [],
        "grant_expires_at": None,
        # Пароль есть у всех, кто вошёл по почте; у телеграм-входа — нет.
        "has_password": (user.get("auth_type") or "").lower() == "email",
    }


# --- подписка ---------------------------------------------------------------

_GB = 1024 ** 3


def _bytes(gb: Any) -> int | None:
    try:
        return int(float(gb) * _GB)
    except (TypeError, ValueError):
        return None


@handler("GET", "/api/subscription/current")
async def subscription_current(ctx: Ctx) -> Any:
    """Подписка. У них гигабайты и «дней осталось», у нас — байты и срок."""
    data = await ctx.json("/cabinet/subscription", {}) or {}
    sub = data.get("subscription")
    if not data.get("has_subscription") or not sub:
        return None

    # Длительность тарифа они не отдают — считаем по границам самой подписки.
    duration = 0
    start, end = sub.get("start_date"), sub.get("end_date")
    if start and end:
        try:
            fmt = lambda s: datetime.fromisoformat(str(s).replace("Z", "+00:00"))  # noqa: E731
            duration = max(0, round((fmt(end) - fmt(start)).total_seconds() / 86400))
        except ValueError:
            duration = 0

    limit_gb = sub.get("traffic_limit_gb") or 0
    return {
        # Свой идентификатор в панели они в кабинет не отдают; кабинет использует
        # его только как ключ отображения — подставляем id подписки.
        "user_remna_id": str(sub.get("id") or ""),
        "status": str(sub.get("status") or "").upper(),
        "is_trial": bool(sub.get("is_trial")),
        # 0 у них = «безлимит», и у нас тоже 0.
        "traffic_limit": _bytes(limit_gb) or 0,
        "device_limit": sub.get("device_limit") or 0,
        "traffic_limit_strategy": sub.get("traffic_reset_mode") or "NO_RESET",
        "expire_at": sub.get("end_date"),
        # Тумблер «прятать ссылку» у них живёт в самой подписке.
        "url": None if sub.get("hide_subscription_link") else sub.get("subscription_url"),
        "plan_name": sub.get("tariff_name") or "",
        "plan_duration_days": duration,
        "used_traffic_bytes": _bytes(sub.get("traffic_used_gb")),
        # Расход за всё время они не считают — не выдумываем.
        "lifetime_used_traffic_bytes": None,
        "online_at": None,
    }


@handler("GET", "/api/subscription/trial-info")
async def trial_info(ctx: Ctx) -> dict[str, Any]:
    t = await ctx.json("/cabinet/subscription/trial", {}) or {}
    # У «Бедолаги» пробный период бывает ПЛАТНЫМ (тумблер TRIAL_PAYMENT_ENABLED):
    # активация молча списывает цену с баланса. Наш экран показывает пробный как
    # бесплатный и цену показать не умеет, поэтому платный триал не предлагаем
    # вовсе — тихое списание денег хуже отсутствующей кнопки.
    price_kopeks = t.get("price_kopeks") or 0
    paid = bool(t.get("requires_payment")) or price_kopeks > 0
    return {
        "available": bool(t.get("is_available")) and not paid,
        "days": t.get("duration_days") or 0,
        "traffic_gb": t.get("traffic_limit_gb") or 0,
        "devices": t.get("device_limit") or 0,
        # Сверх контракта — чтобы платный триал был виден в отладке, а не молчал.
        "requires_payment": paid,
        "price": round(price_kopeks / 100, 2),
    }


@handler("GET", "/api/subscription/devices")
async def devices(ctx: Ctx) -> dict[str, Any]:
    resp = await ctx.call("GET", "/cabinet/subscription/devices")
    if resp.status_code == 404:
        # «Нет подписки» — для кабинета это просто пустой список устройств.
        return {"devices": [], "current_count": 0, "max_count": 0}
    if resp.status_code >= 400:
        raise UpstreamError(resp)
    d = resp.json()
    items = []
    for dev in d.get("devices") or []:
        items.append({
            "hwid": dev.get("hwid"),
            # Своё имя устройства у них есть, у нас его показывает device_model.
            "platform": dev.get("platform"),
            "device_model": dev.get("local_name") or dev.get("device_model"),
            # Версии ОС и user-agent панель им не отдаёт.
            "os_version": None,
            "user_agent": None,
            "created_at": dev.get("created_at"),
            "updated_at": None,
        })
    return {
        "devices": items,
        "current_count": d.get("total") if d.get("total") is not None else len(items),
        "max_count": d.get("device_limit") or 0,
    }


def _nodes_from_countries(data: dict[str, Any]) -> dict[str, Any]:
    nodes = [
        {
            "name": c.get("name") or "",
            "country_code": (c.get("country_code") or "").lower(),
            "online": bool(c.get("is_available")),
            # Адрес ноды они не публикуют — пинг из браузера отключён, аптайма нет.
            "uptime_30d": None,
            "history": [],
        }
        for c in (data.get("countries") or [])
    ]
    online = sum(1 for n in nodes if n["online"])
    return {
        "nodes": nodes,
        "all_operational": bool(nodes) and online == len(nodes),
        "total": len(nodes),
        "online": online,
        "enabled": True,
    }


@handler("GET", "/api/subscription/servers")
async def servers(ctx: Ctx) -> dict[str, Any]:
    return _nodes_from_countries(await ctx.json("/cabinet/subscription/countries", {}) or {})


@handler("GET", "/api/status")
async def status(ctx: Ctx) -> dict[str, Any]:
    """Публичный статус. У «Бедолаги» источника без авторизации нет: список
    серверов отдаётся только вошедшему, поэтому гостю честно говорим 501."""
    if not ctx.token:
        raise NotAvailable("Публичного статуса сервиса у «Бедолаги» нет — список серверов доступен только после входа")
    return _nodes_from_countries(await ctx.json("/cabinet/subscription/countries", {}) or {})


# --- деньги (пока только чтение) --------------------------------------------


@handler("GET", "/api/balance")
async def balance(ctx: Ctx) -> dict[str, Any]:
    b = await ctx.json("/cabinet/balance", {}) or {}
    sub = (await ctx.json("/cabinet/subscription", {}) or {}).get("subscription") or {}
    # Сумма трат есть готовая — её считает их же программа лояльности; перебирать
    # ленту транзакций ради неё не нужно. Число покупок берём счётчиком `total`
    # с фильтром по типу, поэтому запрашиваем одну запись, а не всю страницу.
    loyalty = await ctx.json("/cabinet/promo/loyalty-tiers", {}) or {}
    purchases = await ctx.json(
        "/cabinet/balance/transactions", {},
        params={"type": "subscription_payment", "per_page": 1},
    ) or {}
    return {
        "balance": b.get("balance_rubles") or 0,
        # Баллов и кэшбэка баллами у них нет — рефералка начисляет сразу рублями.
        "points": 0,
        "point_value_rub": 0,
        "total_spent": loyalty.get("current_spent_rubles") or 0,
        "total_purchases": purchases.get("total") or 0,
        "autopay_enabled": bool(sub.get("autopay_enabled")),
    }


@handler("GET", "/api/balance/transactions")
async def transactions(ctx: Ctx) -> dict[str, Any]:
    """У нас limit/offset, у них страницы — пересчитываем в обе стороны."""
    try:
        limit = max(1, min(100, int(ctx.query.get("limit") or 20)))
        offset = max(0, int(ctx.query.get("offset") or 0))
    except ValueError:
        limit, offset = 20, 0
    page = offset // limit + 1
    data = await ctx.json(
        "/cabinet/balance/transactions", {}, params={"page": page, "per_page": limit}
    ) or {}

    items = []
    for t in data.get("items") or []:
        amount = abs(t.get("amount_rubles") or 0)
        items.append({
            "payment_id": str(t.get("id") or ""),
            "status": "COMPLETED" if t.get("is_completed") else "PENDING",
            "gateway_type": (t.get("payment_method") or "BALANCE").upper(),
            "gateway_display_name": t.get("payment_method"),
            "purchase_type": t.get("type") or "",
            "plan_name": t.get("description"),
            "original_amount": f"{amount:.2f}",
            "discount_percent": 0,
            "final_amount": f"{amount:.2f}",
            "currency": "RUB",
            "is_free": amount == 0,
            "is_test": False,
            "created_at": t.get("created_at"),
        })
    return {"total": data.get("total") or 0, "limit": limit, "offset": offset, "items": items}


class NotAvailable(Exception):
    """Того, что просит кабинет, у «Бедолаги» нет — отвечаем 501 с причиной."""


class UpstreamError(Exception):
    """Ошибку «Бедолаги» отдаём кабинету как есть, не превращая в 500."""

    def __init__(self, resp: httpx.Response):
        self.resp = resp
        super().__init__(f"upstream {resp.status_code}")
