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

import html
import json
import re
import time
from datetime import datetime, timedelta, timezone
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
    """Контекст запроса: клиент к «Бедолаге», токен сессии и доступ к панели.

    Про панель. Часть кабинета к боту отношения не имеет — статус и список
    серверов, трафик по нодам, устройства. Эти данные живут в Remnawave, и брать
    их надо оттуда, а не отключать раздел из-за того, что чужой бот такой ручки
    не отдаёт (правило владельца). Панель необязательна: не настроена — методы
    ниже возвращают None, и раздел деградирует мягко, а не падает.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        token: str | None,
        query: dict[str, str],
        panel: httpx.AsyncClient | None = None,
        admin: httpx.AsyncClient | None = None,
        state: Any = None,
        params: dict[str, str] | None = None,
        body: bytes = b"",
    ):
        self._client = client
        self._panel = panel
        self._admin = admin
        self.token = token
        self.query = query
        # Куски пути: /api/support/tickets/{id} → {"id": "12"}.
        self.params = params or {}
        self._body = body
        # Своя память адаптера (state.State) — нужна там, где фича наша, а не бота.
        self.state = state

    def payload(self) -> dict[str, Any]:
        """Тело запроса от кабинета. Пустое или битое — пустой словарь: падать
        на кривом JSON незачем, обработчик сам решит, чего ему не хватает."""
        if not self._body:
            return {}
        try:
            data = json.loads(self._body)
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    @property
    def can_admin(self) -> bool:
        """Выдан ли адаптеру админский токен «Бедолаги»."""
        return self._admin is not None

    async def admin(self, method: str, path: str, **kw: Any) -> httpx.Response | None:
        """Их штатное админское API (X-API-Key). Правок в их коде не требует —
        токен выпускается их же ручкой /tokens или переменной окружения."""
        if self._admin is None:
            return None
        try:
            return await self._admin.request(method, path, **kw)
        except httpx.HTTPError:
            return None

    @property
    def has_panel(self) -> bool:
        return self._panel is not None

    async def panel_json(self, path: str, default: Any = None, **kw: Any) -> Any:
        """Чтение из Remnawave. Панель может быть медленной или недоступной —
        для кабинета это «нет данных», а не ошибка всего экрана."""
        if self._panel is None:
            return default
        try:
            resp = await self._panel.get(path, **kw)
        except httpx.HTTPError:
            return default
        if resp.status_code >= 400:
            return default
        try:
            body = resp.json()
        except ValueError:
            return default
        # У Remnawave полезная часть всегда под "response".
        return body.get("response", body) if isinstance(body, dict) else body

    async def call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        headers = dict(kw.pop("headers", {}))
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        return await self._client.request(method, path, headers=headers, **kw)

    async def json(
        self, path: str, default: Any = None, method: str = "GET",
        soft: bool = False, **kw: Any
    ) -> Any:
        """Чтение с мягким провалом там, где данных может не быть.

        Две вещи мягко глотать НЕЛЬЗЯ, иначе кабинет покажет аварию как факт:
          • «нет сессии» (401/403) — иначе вместо возврата на вход человек
            увидит «у вас нет подписки»;
          • «бот не ответил» — иначе при перезапуске бота в кабинете нарисуются
            баланс 0 ₽, пустые «Правила» и «обращений нет».
        `soft=True` — только для необязательных кусочков (например, оформление
        собирается из шести ручек, и одна отвалившаяся не должна ронять экран).
        """
        try:
            resp = await self.call(method, path, **kw)
        except httpx.HTTPError as exc:
            if soft:
                return default
            raise UpstreamError(
                httpx.Response(502, json={"detail": f"Бэкенд недоступен: {exc}"})
            ) from exc
        if resp.status_code in (401, 403):
            raise UpstreamError(resp)
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
    "account_delete": [("POST", "/api/account/delete")],
    "data_export": [("GET", "/api/account/export")],
    # Список сессий — это их refresh-токены, а push-подписки хранит бэкенд:
    # обе вещи принадлежат бэкенду, поэтому гасятся честно, а не показываются
    # пустыми карточками.
    "sessions": [("GET", "/api/sessions")],
    "push": [("GET", "/api/push/status")],
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
    "devices", "reissue", "apps", "speedtest", "diagnostics",
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
    colors = await ctx.json("/cabinet/branding/colors", {}, soft=True) or {}
    widget = await ctx.json("/cabinet/branding/telegram-widget", {}, soft=True) or {}
    support = await ctx.json("/cabinet/info/support-config", {}, soft=True) or {}

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
        # ГИГАБАЙТЫ, как в нашем контракте: `subscriptions.traffic_limit` у нас
        # хранится и отдаётся в ГБ (в байты переводится только на выходе в
        # Remnawave — gb_to_bytes в infrastructure/services/remnawave.py), поэтому
        # адаптер обязан отдавать ту же единицу, иначе поле значит разное на разных
        # бэкендах. 0 = «безлимит» и у них, и у нас.
        "traffic_limit": limit_gb or 0,
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


# --- витрина: тарифы, цены, пополнение ---------------------------------------

# Валюта у «Бедолаги» одна: всё считается в копейках рубля (`*_kopeks`), выбора
# валюты в API нет. Кабинет же ждёт код и символ в каждой цене, а по `currency ==
# "RUB"` решает, показывать ли кнопку «оплатить с баланса» (BillingPage.tsx:70).
_RUB, _RUB_SYMBOL = "RUB", "₽"

# Шлюз у них выбирается только для ПОПОЛНЕНИЯ баланса: подписка всегда оплачивается
# списанием с баланса (docs/BEDOLAGA-MONEY.md §2), поэтому один и тот же список
# методов кормит и витрину, и форму пополнения.
#
# Telegram Stars из списка убираем: их же POST /cabinet/balance/topup на этот метод
# отвечает 400 «Telegram Stars payments are only available through the bot»
# (app/cabinet/routes/balance.py:441-447) — счёт на звёзды выдаёт только бот, в
# браузере кнопка вела бы в тупик. Наш бэкенд поступает так же: TELEGRAM_STARS не
# попадает в web_gateways (src/web/endpoints/public/subscription.py:661-668).
_NOT_PAYABLE_IN_BROWSER = {"telegram_stars"}

# Их собственный минимум пополнения, зашитый в схему запроса: TopUpRequest
# .amount_kopeks ge=1000 (10 ₽). У метода минимум может быть ниже (Platega — 1 ₽),
# но валидация тела всё равно отобьёт сумму меньше десяти рублей.
_TOPUP_FLOOR_KOPEKS = 1000


def _rub(kopeks: Any, short: bool = False) -> str:
    """Копейки → рубли строкой: кабинет печатает сумму как есть, рядом с символом.

    `short` — для лендинга: там сумма стоит внутри фразы («1249 ₽ за 360 дней»), и
    хвост «.00» читается как сбой; наш бэкенд в тех же полях нули срезает
    (`_normalize_decimal_str`, src/web/endpoints/public/_common.py:19).
    """
    try:
        value = int(kopeks or 0)
    except (TypeError, ValueError):
        value = 0
    if short and value % 100 == 0:
        return str(value // 100)
    return f"{value / 100:.2f}"


def _plan_type(traffic_gb: Any, device_limit: Any) -> str:
    """Тип тарифа в наших терминах (PlanType) — по тому, что реально ограничено.

    Своего «типа» у их тарифа нет, есть пара лимитов, где 0 = безлимит. Кабинет по
    этому полю ничего не рисует, но контракт его требует — выводим из лимитов,
    а не подставляем константу.
    """
    traffic, devices = bool(traffic_gb), bool(device_limit)
    if traffic and devices:
        return "BOTH"
    if traffic:
        return "TRAFFIC"
    if devices:
        return "DEVICES"
    return "UNLIMITED"


def _gateway_type(method: dict[str, Any]) -> str:
    """Их `id` метода → наш `gateway_type` (кабинет подписывает шлюзы по верхнему
    регистру, BillingPage.tsx:20-26). Обратный разбор в оплате — `.lower()`."""
    return str(method.get("id") or "").upper()


async def _payment_methods(ctx: Ctx) -> list[dict[str, Any]]:
    """Способы оплаты, которыми реально можно заплатить из браузера."""
    methods = await ctx.json("/cabinet/balance/payment-methods", []) or []
    return [
        m
        for m in methods
        if isinstance(m, dict)
        and m.get("is_available", True)
        and m.get("id") not in _NOT_PAYABLE_IN_BROWSER
    ]


async def _tariff_catalog(ctx: Ctx) -> dict[str, Any]:
    """Каталог тарифов «Бедолаги». Ошибку апстрима поднимаем наверх: пустая
    витрина вместо «сессия истекла» — худший из возможных ответов."""
    resp = await ctx.call("GET", "/cabinet/subscription/purchase-options")
    if resp.status_code >= 400:
        raise UpstreamError(resp)
    return resp.json()


@handler("GET", "/api/subscription/offers")
async def subscription_offers(ctx: Ctx) -> dict[str, Any]:
    """Витрина покупки и продления.

    Три их ручки вместо одной, потому что цена покупки и цена продления считаются
    у них в разных местах: `purchase-options` даёт каталог со скидкой промогруппы,
    а точную цену продления (уже с персональным промо-предложением) знает только
    `renewal-options` — docs/BEDOLAGA-MONEY.md §3. Цена на кнопке обязана совпасть
    со списанной, иначе пользователь платит не то, что видел.
    """
    catalog = await _tariff_catalog(ctx)
    gateways = [
        {"gateway_type": _gateway_type(m), "currency": _RUB, "currency_symbol": _RUB_SYMBOL}
        for m in await _payment_methods(ctx)
    ]

    # Режим продаж «classic»: готовых тарифов у них нет вовсе — подписка собирается
    # конструктором (период + трафик + серверы + устройства), и в ответе вместо
    # `tariffs` лежит другой payload. Наша витрина такое не выражает, поэтому
    # отдаём пустой список: экран покажет «нет доступных тарифов»
    # (BillingPage.tsx:314), а не ошибку, за которой не видно причины.
    tariffs = catalog.get("tariffs") or [] if catalog.get("sales_mode") == "tariffs" else []
    has_subscription = bool(catalog.get("has_subscription"))

    renewal: dict[int, dict[str, Any]] = {}
    if has_subscription:
        # Без подписки ручка возвращает [] — лишний запрос не делаем.
        for row in await ctx.json("/cabinet/subscription/renewal-options", []) or []:
            if isinstance(row, dict) and row.get("period_days"):
                renewal[int(row["period_days"])] = row

    plans: list[dict[str, Any]] = []
    for tariff in tariffs:
        if not isinstance(tariff, dict) or not tariff.get("is_available"):
            continue
        is_current = bool(tariff.get("is_current"))
        # Мульти-тариф: уже оплаченный, но не текущий тариф второй раз не продать —
        # их собственный фронт такие прячет. Текущий оставляем: это кнопка продления.
        if tariff.get("is_purchased") and not is_current:
            continue

        durations: list[dict[str, Any]] = []
        for period in tariff.get("periods") or []:
            days = period.get("days")
            if not days:
                continue
            # Для текущего тарифа цена — из продления; если этого периода там нет
            # (скрытый тариф — у них тогда подставляются стандартные периоды),
            # остаёмся на каталожной.
            source = (renewal.get(int(days)) if is_current else None) or period
            final = int(source.get("price_kopeks") or 0)
            # Поля скидки они добавляют только когда скидка есть.
            original = int(source.get("original_price_kopeks") or final)
            durations.append({
                "days": int(days),
                # Цена одна на период: шлюз влияет только на пополнение баланса, на
                # стоимость подписки — нет. Поэтому один ценник размножается по всем
                # способам оплаты, как того ждёт кабинет.
                "prices": [
                    {
                        "gateway_type": g["gateway_type"],
                        "currency": _RUB,
                        "currency_symbol": _RUB_SYMBOL,
                        "original_amount": _rub(original),
                        "discount_percent": int(source.get("discount_percent") or 0),
                        "final_amount": _rub(final),
                        "is_free": final == 0,
                    }
                    for g in gateways
                ],
            })

        # Тариф без периодов купить нечем — так выглядят суточные тарифы (цена за
        # день, `is_daily`), которых наша витрина не умеет.
        if not durations:
            continue

        plans.append({
            "id": int(tariff.get("id") or 0),
            # Своего «кода» у тарифа нет, есть числовой id — его и отдаём строкой:
            # оплата разберёт обратно (`int(plan_code)` → tariff_id), справочник не нужен.
            "public_code": str(tariff.get("id") or ""),
            "name": tariff.get("name") or "",
            "description": tariff.get("description"),
            # ГИГАБАЙТЫ, не байты: в нашем контракте это поле — ГБ (plans.traffic_limit
            # в базе = 150/300/500, cabinet/src/pages/admin/AdminPlansPage.tsx:27
            # «в тарифах traffic_limit хранится в ГБ»). 0 = безлимит и у них, и у нас.
            "traffic_limit": tariff.get("traffic_limit_gb") or 0,
            # Именно `device_limit`, а не `base_device_limit`: для текущего тарифа он
            # уже учитывает докупленные устройства, и цена периода посчитана с ними же.
            "device_limit": tariff.get("device_limit") or 0,
            "type": _plan_type(tariff.get("traffic_limit_gb"), tariff.get("device_limit")),
            "recommended_purchase_type": (
                "RENEW" if is_current else ("CHANGE" if has_subscription else "NEW")
            ),
            "durations": durations,
        })

    status = catalog.get("subscription_status")
    return {
        "gateways": gateways,
        "plans": plans,
        "has_current_subscription": has_subscription,
        # У них статус строчный («active»), у нас — верхний регистр.
        "current_subscription_status": str(status).upper() if status else None,
    }


@handler("GET", "/api/plans/public")
async def plans_public(ctx: Ctx) -> dict[str, Any]:
    """Прайс для лендинга и страницы «Тарифы» — единственная витрина, которую
    кабинет показывает ДО входа.

    Публичного прайса у «Бедолаги» нет: `purchase-options` требует токен (гостю
    401), их собственная гостевая витрина — лендинги `/cabinet/landing/{slug}`,
    которых в установке может не быть вовсе (на стенде 404).

    Гостю отдаём пустой список, а не 501: на ошибке лендинг остаётся с вечным
    скелетоном (LandingPage.tsx:136 гасит исключение и не выходит из состояния
    «грузится»), а на пустом списке честно печатает «тарифы скоро».
    """
    if not ctx.token:
        return {"plans": []}

    catalog = await _tariff_catalog(ctx)
    if catalog.get("sales_mode") != "tariffs":
        return {"plans": []}

    plans: list[dict[str, Any]] = []
    for tariff in catalog.get("tariffs") or []:
        if not isinstance(tariff, dict) or not tariff.get("is_available"):
            continue
        # Купленные тарифы здесь не прячем: это прайс, а не корзина.
        periods = [p for p in (tariff.get("periods") or []) if p.get("days")]
        if not periods:
            continue
        longest = max(periods, key=lambda p: int(p["days"]))
        days, price = int(longest["days"]), int(longest.get("price_kopeks") or 0)
        plans.append({
            "public_code": str(tariff.get("id") or ""),
            "name": tariff.get("name") or "",
            "description": tariff.get("description"),
            # ГБ, 0 = безлимит: страница цен печатает число и подпись «ГБ» сама
            # (PricingPage.tsx:40, LandingPage.tsx:536).
            "traffic_limit": tariff.get("traffic_limit_gb") or 0,
            "device_limit": tariff.get("device_limit") or 0,
            # «от N ₽ в месяц» считаем по самому длинному сроку — он же самый
            # выгодный, ровно как наш бэкенд (src/web/endpoints/public/plans.py:56).
            "monthly_from_rub": _rub(price * 30 // days, short=True),
            "max_duration_days": days,
            "max_duration_price_rub": _rub(price, short=True),
        })
    return {"plans": plans}


@handler("GET", "/api/balance/topup/config")
async def topup_config(ctx: Ctx) -> dict[str, Any]:
    """Условия пополнения баланса.

    Мягко: не отдались способы оплаты — форма просто не показывается
    (`enabled: false`, BalancePage.tsx:249), а не падает ошибкой поверх баланса.
    """
    methods = await _payment_methods(ctx)

    # Лимиты у каждого метода свои (Tribute от 100 ₽, Stars до 10 000 ₽, Platega от
    # 1 ₽), а кабинет знает ОДИН диапазон на всю форму и не перепроверяет сумму при
    # смене шлюза (BalancePage.tsx:265). Объединение диапазонов пропустило бы сумму,
    # которую их `topup` отбивает своим 400 (app/cabinet/routes/balance.py:330-341),
    # поэтому собираем пересечение: диапазон, годный для КАЖДОГО оставленного
    # метода. Метод, который в него не влезает, из списка убираем — лучше меньше
    # кнопок, чем оплата, падающая после нажатия.
    kept: list[dict[str, Any]] = []
    low = high = 0
    # От самого широкого диапазона: он задаёт основу, к которой прирастают
    # совместимые методы (иначе один узкий метод отрезал бы все остальные).
    for m in sorted(
        methods,
        key=lambda m: int(m.get("max_amount_kopeks") or 0) - int(m.get("min_amount_kopeks") or 0),
        reverse=True,
    ):
        lo = max(int(m.get("min_amount_kopeks") or 0), _TOPUP_FLOOR_KOPEKS)
        hi = int(m.get("max_amount_kopeks") or 0)
        if hi < lo:
            continue
        if not kept:
            kept, low, high = [m], lo, hi
            continue
        if max(low, lo) > min(high, hi):
            continue
        kept.append(m)
        low, high = max(low, lo), min(high, hi)

    # Порядок кнопок — их: список приходит уже отсортированным настройкой
    # `sort_order` из админки бота, и перетасовывать его нашим отбором нельзя.
    kept_ids = {m.get("id") for m in kept}
    ordered = [m for m in methods if m.get("id") in kept_ids]

    return {
        "enabled": bool(ordered),
        # Бонуса за пополнение у них нет: единственный «бонус» в настройках —
        # реферальная награда за первое пополнение приглашённого
        # (REFERRAL_FIRST_TOPUP_BONUS_KOPEKS), а не процент к зачислению.
        "bonus_percent": 0,
        # Округляем ВНУТРЬ диапазона: вверх для минимума, вниз для максимума —
        # иначе граничная сумма из кабинета не пройдёт их проверку в копейках.
        "min_amount": -(-low // 100),
        "max_amount": high // 100,
        # Быстрых сумм у них нет ни в API, ни в настройках. Поле обязательное —
        # отдаём пустым: кнопки-пресеты не рисуются, сумма вводится руками.
        "presets": [],
        "gateways": [
            {
                "gateway_type": _gateway_type(m),
                # Человеческое имя даёт их админка — берём как есть.
                "name": m.get("name") or _gateway_type(m),
                "currency_symbol": _RUB_SYMBOL,
                # Сверх контракта: точные границы метода. Кабинет их пока не читает,
                # но когда научится проверять сумму по выбранному шлюзу — они здесь.
                "min_amount": -(-max(int(m.get("min_amount_kopeks") or 0), _TOPUP_FLOOR_KOPEKS) // 100),
                "max_amount": int(m.get("max_amount_kopeks") or 0) // 100,
            }
            for m in ordered
        ],
    }


# --- поддержка: тикеты ---------------------------------------------------------

# Их статусов четыре и они в нижнем регистре (app/database/models.py:3261,
# admin_tickets.py:539), наш кабинет знает три (cabinet/src/api/support.ts:4).
# `pending` — «пользователь ответил, ждём поддержку» (routes/tickets.py:316), для
# экрана это тот же «открыт».
# Отдать наружу незнакомое значение нельзя: StatusPill ищет его в словаре и падает
# на undefined (cabinet/src/pages/SupportPage.tsx:22-25) — экран поддержки белеет.
# Поэтому карта закрытая, всё неизвестное схлопывается в "open".
_TICKET_STATUS = {"open": "open", "pending": "open", "answered": "answered", "closed": "closed"}

# Их страницы против нашего «отдай всё»: берём максимум, который они разрешают
# (per_page ≤ 100, проверено 422 на 200), — иначе список молча обрежется на 20.
_TICKETS_PER_PAGE = 100


def _ticket_message(m: dict[str, Any]) -> dict[str, Any]:
    """Сообщение тикета в нашу форму (cabinet/src/components/TicketThread.tsx:55-67)."""
    text = (m.get("message_text") or "").strip()
    if not text:
        # Вложение у них — telegram file_id: отдать его браузеру нечем (ручка
        # медиа есть только в админском webapi по API-ключу). Пустой «пузырь» в
        # переписке выглядит как потерянное сообщение, поэтому подписываем, где
        # его смотреть. Подпись автора, если она есть, честнее нашей.
        text = (m.get("media_caption") or "").strip()
        if not text and m.get("has_media"):
            text = "📎 Вложение — откройте в Telegram-боте"
    return {
        "id": m.get("id"),
        "sender": "admin" if m.get("is_from_admin") else "user",
        "body": text,
        "created_at": m.get("created_at"),
    }


# --- поддержка: уведомления ----------------------------------------------------

# У них уведомления двух видов, и «общие» — не лента:
#   /cabinet/tickets/notifications — настоящая лента (is_read, created_at), но
#     только про тикеты; пользователю доезжает один тип, admin_reply
#     (app/database/crud/ticket_notification.py:216-235);
#   /cabinet/notifications — НАСТРОЙКИ рассылок (routes/notifications.py:84-90),
#   /cabinet/notifications/history — заглушка, всегда пустой список
#     (routes/notifications.py:136-151).
# Поэтому лента собирается из тикетных, а не из «общих»: подставить настройки в
# колокольчик — уронить его (NotificationBell.tsx:57-59 получит items=undefined).
_NOTIF_TITLE = {"admin_reply": "Ответ поддержки"}


# Собирается из пяти-шести их ручек, а страница открывается заново при каждом
# заходе — держим короткий кэш по языку, как для оформления.
_INFO_TTL = 60.0
_info_cache: dict[str, tuple[float, dict[str, Any]]] = {}

_HTML_TAG = re.compile(r"<[^>]+>")

# Расшифровка статусов — текст КАБИНЕТА, а не данные бота: у «Бедолаги» такой
# ручки нет и быть не может. Отдаём ту же константу, что наш собственный бэкенд
# (src/web/endpoints/public/info_content.py:138-149), иначе вкладка «Статусы»
# просто пустая. Плейсхолдера {brand} в этом тексте нет — подстановка не нужна.
_STATUSES_MD = """## Статусы подписки
- **Активна** — подписка активна и работает. Подключение доступно на всех ваших устройствах.
- **Истекает скоро** — до окончания подписки осталось менее 3 дней. Рекомендуем продлить заранее.
- **Истекла** — срок действия подписки завершился. Подключение приостановлено. Продлите подписку для восстановления доступа.
- **Пробная** — активен бесплатный пробный период. После его окончания необходимо оформить подписку.
- **Отключена** — подписка отключена администратором. Обратитесь в поддержку для уточнения причины.
- **Нет подписки** — подписка ещё не оформлена. Перейдите в раздел «Тарифы», чтобы выбрать план.

## Статусы трафика
- **В норме** — использовано менее 80% лимита трафика.
- **Заканчивается** — использовано более 80% лимита. Скорость будет снижена при достижении 100%.
- **Исчерпан** — лимит трафика исчерпан. Подключение ограничено до следующего сброса или продления."""


def _plain(text: Any) -> str:
    """Текст без разметки — для ответов FAQ: кабинет рисует их как есть,
    без markdown (cabinet/src/pages/InfoPage.tsx:210)."""
    if not isinstance(text, str):
        return ""
    return html.unescape(_HTML_TAG.sub("", text)).strip()


def _md(text: Any) -> str:
    """Их тексты писались для Telegram (HTML bot API — на стенде правила приходят
    с <b>…</b>), а вкладки «Информации» рендерит мини-markdown кабинета: он знает
    только `## `, `### `, `- ` и `**жирный**` (InfoPage.tsx:41-108). Без перевода
    пользователь увидит буквальные теги — React экранирует html, а не исполняет."""
    if not isinstance(text, str) or not text:
        return ""
    out = re.sub(r"</?(b|strong)>", "**", text)
    out = re.sub(r"</?(i|em)>", "*", out)
    out = re.sub(r"</?(code|pre)>", "`", out)
    out = re.sub(r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>', r"\2 (\1)", out, flags=re.S)
    out = re.sub(r"<br\s*/?>", "\n", out)
    out = _HTML_TAG.sub("", out)
    # Их markdown-дефолты начинаются с «# Заголовок», а мини-рендер такой уровень
    # не разбирает — решётка осталась бы в тексте абзацем.
    out = re.sub(r"^# (?!#)", "## ", out, flags=re.M)
    return html.unescape(out).strip()


def _pick_lang(value: Any, lang: str) -> str:
    """Мультиязычное поле инфо-страницы ({'ru': '…', 'en': '…'},
    app/cabinet/schemas/info_pages.py:12-13) в одну строку."""
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""
    for key in (lang, "ru", "en"):
        text = value.get(key)
        if isinstance(text, str) and text.strip():
            return text
    return next((v for v in value.values() if isinstance(v, str) and v.strip()), "")


@handler("GET", "/api/support/tickets")
async def tickets(ctx: Ctx) -> dict[str, Any]:
    """Список тикетов. У них title и страницы, у нас subject и плоский items."""
    data = await ctx.json(
        "/cabinet/tickets", {}, params={"page": 1, "per_page": _TICKETS_PER_PAGE}
    ) or {}
    items = [
        {
            "id": t.get("id"),
            "subject": t.get("title") or "",
            "status": _TICKET_STATUS.get(str(t.get("status") or "").lower(), "open"),
            "created_at": t.get("created_at"),
            "updated_at": t.get("updated_at") or t.get("created_at"),
            "messages_count": t.get("messages_count") or 0,
        }
        for t in (data.get("items") or [])
    ]
    return {"items": items}


@handler("GET", "/api/notifications")
async def notifications(ctx: Ctx) -> dict[str, Any]:
    try:
        limit = max(1, min(100, int(ctx.query.get("limit") or 50)))
    except ValueError:
        limit = 50
    data = await ctx.json(
        "/cabinet/tickets/notifications", {}, params={"limit": limit}
    ) or {}
    items = [
        {
            "id": n.get("id"),
            "title": _NOTIF_TITLE.get(n.get("notification_type") or "", "Поддержка"),
            # Готовый текст вида «Ответ на тикет #12: …» они собирают сами.
            "body": n.get("message") or "",
            # Маршрута на конкретный тикет в кабинете нет (App.tsx:153) — ведём в
            # раздел; чужие/внешние адреса тут появиться не должны.
            "url": "/support",
            "is_read": bool(n.get("is_read")),
            "created_at": n.get("created_at"),
        }
        for n in (data.get("items") or [])
    ]
    return {"unread": data.get("unread_count") or 0, "items": items}


@handler("GET", "/api/notifications/unread-count")
async def notifications_unread(ctx: Ctx) -> dict[str, int]:
    data = await ctx.json("/cabinet/tickets/notifications/unread-count", {}) or {}
    return {"unread": data.get("unread_count") or 0}


@handler("GET", "/api/info")
async def info(ctx: Ctx) -> dict[str, Any]:
    """FAQ/правила/политика/оферта. Ручка публичная у обеих сторон — гостю тоже
    отвечаем, авторизация не нужна."""
    lang = (ctx.query.get("lang") or "ru").split("-", 1)[0].lower() or "ru"
    cached = _info_cache.get(lang)
    now = time.monotonic()
    if cached and now - cached[0] < _INFO_TTL:
        return cached[1]

    params = {"language": lang}
    # Админ может подменить любую вкладку своей инфо-страницей: карта «вкладка →
    # slug» (app/database/crud/info_pages.py:157-178). Подмена важнее дефолта —
    # иначе кабинет покажет текст, который в боте уже выключен.
    swap = await ctx.json("/cabinet/info-pages/tab-replacements", {}) or {}

    async def replaced(tab: str) -> str:
        slug = swap.get(tab)
        if not slug:
            return ""
        page = await ctx.json(f"/cabinet/info-pages/{slug}", {}) or {}
        return _md(_pick_lang(page.get("content"), lang))

    faq_md = await replaced("faq")
    if faq_md:
        # Подменённая вкладка — один документ, а у нас список вопрос/ответ:
        # заголовком берём название страницы, чтобы аккордеон не был безымянным.
        page = await ctx.json(f"/cabinet/info-pages/{swap['faq']}", {}) or {}
        faq = [{"q": _plain(_pick_lang(page.get("title"), lang)) or "FAQ", "a": faq_md}]
    else:
        faq = [
            {"q": _plain(p.get("title")), "a": _plain(p.get("content"))}
            for p in (await ctx.json("/cabinet/info/faq", [], params=params) or [])
        ]

    rules = await replaced("rules") or _md(
        (await ctx.json("/cabinet/info/rules", {}, params=params) or {}).get("content")
    )
    privacy = await replaced("privacy") or _md(
        (await ctx.json("/cabinet/info/privacy-policy", {}, params=params) or {}).get("content")
    )
    offer = await replaced("offer") or _md(
        (await ctx.json("/cabinet/info/public-offer", {}, params=params) or {}).get("content")
    )

    # Все пять ключей обязаны быть на месте и быть строками: кабинет отдаёт
    # content[active] прямо в рендер, и на undefined падает (InfoPage.tsx:241,42).
    data = {
        "faq": faq,
        "rules": rules,
        "privacy": privacy,
        "offer": offer,
        "statuses": _STATUSES_MD,
    }
    _info_cache[lang] = (now, data)
    return data


    def __init__(
        self,
        client: httpx.AsyncClient,
        token: str | None,
        query: dict[str, str],
        panel: httpx.AsyncClient | None = None,
        params: dict[str, str] | None = None,
        body: bytes = b"",
    ):
        self._client = client
        self._panel = panel
        self.token = token
        self.query = query
        # Путь с параметром («…/tickets/{id}/messages») и тело запроса: без них
        # свои обработчики умеют только GET без аргументов.
        self.params = params or {}
        self.body = body

    def payload(self) -> dict[str, Any]:
        """Тело кабинета словарём. Пустое тело — это {} (так кабинет просит
        «отметить все прочитанными»), а не ошибка."""
        if not self.body:
            return {}
        try:
            data = json.loads(self.body)
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}


# --- промо и рефералка ------------------------------------------------------


@handler("GET", "/api/referral/program")
async def referral_program(ctx: Ctx) -> dict[str, Any]:
    """Реферальная программа. У «Бедолаги» она разложена на две ручки: персональные
    счётчики (`/cabinet/referral`) и условия (`/cabinet/referral/terms`)."""
    terms = await ctx.json("/cabinet/referral/terms", {}) or {}
    info = await ctx.json("/cabinet/referral", {}) or {}
    code = str(info.get("referral_code") or "")
    # Выключенную программу кабинет показывает отдельной карточкой, но ТОЛЬКО по 403:
    # поле enabled он не смотрит вовсе (pages/ReferralPage.tsx:268). Пустой код —
    # то же самое: ссылка «/register?ref=» никого не приведёт. В detail не должно быть
    # слова «subscription» — по нему кабинет выбирает другую причину (ReferralPage.tsx:232).
    if not terms.get("is_enabled", True) or not code:
        raise UpstreamError(
            httpx.Response(403, json={"detail": "Реферальная программа отключена"})
        )

    total = int(info.get("total_referrals") or 0)
    # Счётчика «оплативших» у них нет. В списке приглашённых есть флаг has_paid —
    # считаем по нему, пока список влезает в одну страницу (100). У крупных рефереров
    # перебирать все страницы ради цифры на дашборде не станем: там остаётся их
    # `active_referrals` — это «с действующей подпиской», метрика другая, но ближайшая.
    paid = int(info.get("active_referrals") or 0)
    if 0 < total <= 100:
        lst = await ctx.json(
            "/cabinet/referral/list", {}, params={"page": 1, "per_page": 100}
        ) or {}
        items = lst.get("items")
        if isinstance(items, list) and len(items) == int(lst.get("total") or -1):
            paid = sum(1 for r in items if r.get("has_paid"))

    # Персональный процент реферера приоритетнее общего (routes/referral.py:70-72).
    percent = int(info.get("commission_percent") or terms.get("commission_percent") or 0)
    return {
        "enabled": True,
        "referral_code": code,
        "invited_count": total,
        "invited_with_payment_count": paid,
        # Награда у них — РУБЛИ на баланс за процент от платежа друга. При стратегии
        # PERCENT кабинет печатает уровни как «N % от платежей», а заработок — с «₽»
        # для любого типа, кроме EXTRA_DAYS (ReferralPage.tsx:20-25, 155-158). Поэтому
        # POINTS здесь не «баллы», а способ получить рублёвое отображение.
        "reward_type": "POINTS",
        "reward_strategy": "PERCENT",
        # 1 платёж на реферала = наша «только первая оплата»; 0 у них = без лимита.
        "accrual_strategy": (
            "ON_FIRST_PAYMENT"
            if int(terms.get("max_commission_payments") or 0) == 1
            else "ON_EACH_PAYMENT"
        ),
        # Колено у них одно: комиссия платится только прямому пригласившему
        # (users.referred_by_id, app/services/referral_service.py:645-652).
        "max_level": 1,
        "reward_levels": [{"level": 1, "value": percent}] if percent else [],
    }


@handler("GET", "/api/referral/earnings")
async def referral_earnings(ctx: Ctx) -> dict[str, Any]:
    """Итоги заработка. Просим одну запись: сумму и количество их ручка считает по всей
    истории, а не по странице (routes/referral.py:174-183) — страницу тащить незачем."""
    data = await ctx.json(
        "/cabinet/referral/earnings", {}, params={"page": 1, "per_page": 1}
    ) or {}
    return {
        # Кабинет печатает earned с «₽» без деления, поэтому рубли, не копейки.
        "earned": round(float(data.get("total_amount_rubles") or 0), 2),
        "rewards_count": int(data.get("total") or 0),
    }


# Их ошибки промокода — английские строки (routes/promocode.py:103-117), а кабинет
# показывает detail пользователю как есть (api/client.ts:44). Переводим известное,
# незнакомое пропускаем без изменений — лучше англ. текст, чем «что-то пошло не так».
_PROMO_ERRORS = {
    "Promo code not found": "Промокод не найден или неактивен",
    "Promo code has expired": "Срок действия промокода истёк",
    "Promo code is deactivated": "Промокод отключён",
    "Promo code is not yet active": "Промокод ещё не начал действовать",
    "Promo code has been fully used": "Лимит активаций промокода исчерпан",
    "You have already used this promo code": "Вы уже активировали этот промокод",
    "You already have an active discount. Deactivate it first via /deactivate-discount":
        "У вас уже есть активная скидка — сначала отмените её",
    "This promo code requires an active or expired subscription":
        "Для этого промокода нужна подписка",
    "Subscription not found": "Подписка не найдена",
    "This promo code is only available for first purchase":
        "Промокод действует только на первую покупку",
    "Too many promo code activations today":
        "Слишком много активаций промокодов за сутки — попробуйте позже",
    "User not found": "Пользователь не найден",
    "Server error occurred": "Не удалось активировать промокод",
}


@handler("GET", "/api/trial-discount")
async def trial_discount(ctx: Ctx) -> dict[str, Any]:
    """Персональная скидка на покупку — баннер с таймером. У них это одно поле на
    пользователе, куда пишут и промо-оффер, и промокод типа discount
    (routes/promo.py:145-162, promocode_service.py:261-282) — источник в `source`."""
    d = await ctx.json("/cabinet/promo/active-discount", {}) or {}
    percent = int(d.get("discount_percent") or 0)
    # Срок они уже проверили сами; при отсутствии скидки не выдумываем нули.
    if not d.get("is_active") or percent <= 0:
        return {"active": False}
    return {
        "active": True,
        "percent": percent,
        # null = скидка бессрочна до первой покупки: баннер тогда без таймера
        # (components/TrialDiscountBanner.tsx:51-53).
        "expires_at": d.get("expires_at"),
    }


@handler("GET", "/api/promo-banner")
async def promo_banner(ctx: Ctx) -> dict[str, Any]:
    """Промо-баннер. Админ-конструктора баннера у «Бедолаги» нет, зато есть
    персональные промо-офферы с собственным сроком — их и показываем в этом слоте.

    Про кнопку. Оффер надо КЛЕЙМИТЬ (`POST /cabinet/promo/claim`): сам собой он в
    скидку не превращается. Кликабельной ссылки, которая клеймит, у них не существует,
    а баннер умеет только переход по ссылке — поэтому CTA не выдаём и не клеймим
    молча на GET: клейм запускает таймер самой скидки (routes/promo.py:377-382), и
    открытый «на посмотреть» кабинет сжёг бы окно скидки.
    """
    offers = await ctx.json("/cabinet/promo/offers", []) or []
    if not isinstance(offers, list):
        return {"active": False}
    # Их список уже отфильтрован по «не истёк»; забранные оставлены — они не новость.
    offer = next(
        (o for o in offers if o.get("is_active") and not o.get("is_claimed")), None
    )
    if not offer:
        return {"active": False}

    percent = int(offer.get("discount_percent") or 0)
    if str(offer.get("effect_type") or "percent_discount").lower() == "test_access":
        title = "Для вас открыт пробный доступ к серверам"
    elif percent > 0:
        title = f"Персональная скидка {percent}%"
    else:
        # Бонус баллами/деньгами их баннер не описывает — пустой оффер не показываем.
        return {"active": False}

    return {
        "active": True,
        "title": title,
        "text": "Предложение ждёт активации: заберите его в Telegram-боте — "
                "в кабинете кнопки активации пока нет.",
        "cta_text": None,
        "cta_url": None,
        # Не реклама, а «требует действия» — отсюда янтарный, а не акцентный.
        "color": "amber",
        "dismissible": True,
        # Версия по конкретному офферу: «скрыть» гасит его, а не баннеры вообще.
        "version": f"{offer.get('id')}:{offer.get('expires_at')}",
    }


class NotAvailable(Exception):
    """Того, что просит кабинет, у «Бедолаги» нет — отвечаем 501 с причиной."""


class UpstreamError(Exception):
    """Ошибку «Бедолаги» отдаём кабинету как есть, не превращая в 500."""

    def __init__(self, resp: httpx.Response):
        self.resp = resp
        super().__init__(f"upstream {resp.status_code}")


@handler("POST", "/api/support/tickets")
async def ticket_create(ctx: Ctx) -> dict[str, Any]:
    """Создание тикета: у нас subject, у них title (и минимум 3 символа против
    наших 2 — их 422 отдаём как есть, придумывать своё правило нельзя)."""
    body = ctx.payload()
    resp = await ctx.call(
        "POST", "/cabinet/tickets",
        json={
            "title": str(body.get("subject") or "").strip(),
            "message": str(body.get("message") or "").strip(),
        },
    )
    if resp.status_code >= 400:
        raise UpstreamError(resp)
    # Они возвращают тикет целиком, кабинету нужен только id для перехода в диалог.
    return {"id": (resp.json() or {}).get("id")}


@handler("GET", "/api/support/tickets/{id}")
async def ticket_detail(ctx: Ctx) -> dict[str, Any]:
    """Переписка. 404 «чужой/несуществующий тикет» — их ответ, не наша выдумка,
    поэтому уходит наверх как есть, а не превращается в пустой диалог."""
    resp = await ctx.call("GET", f"/cabinet/tickets/{ctx.params['id']}")
    if resp.status_code >= 400:
        raise UpstreamError(resp)
    t = resp.json() or {}
    return {
        "id": t.get("id"),
        "subject": t.get("title") or "",
        "status": _TICKET_STATUS.get(str(t.get("status") or "").lower(), "open"),
        "created_at": t.get("created_at"),
        "updated_at": t.get("updated_at") or t.get("created_at"),
        "messages": [_ticket_message(m) for m in (t.get("messages") or [])],
    }


@handler("POST", "/api/support/tickets/{id}/messages")
async def ticket_reply(ctx: Ctx) -> dict[str, Any]:
    """Ответ пользователя. ВАЖНО: у нас поле body, у них message — прямой проброс
    (как в route_map до этого) даёт 422 «message or media is required», то есть
    ответить в тикет было нельзя вообще. Запись из карты надо снять."""
    resp = await ctx.call(
        "POST", f"/cabinet/tickets/{ctx.params['id']}/messages",
        json={"message": str(ctx.payload().get("body") or "").strip()},
    )
    if resp.status_code >= 400:
        # 400 «тикет закрыт» и 403 «ответы заблокированы» — осмысленные для
        # пользователя причины, глотать их нельзя.
        raise UpstreamError(resp)
    return {"success": True}


# POST /api/support/tickets/{id}/close обработчика НЕ имеет намеренно: закрытия
# тикета пользователем в их кабинетном API нет (только админское
# /cabinet/admin/tickets/{id}/status), а main.py уже отвечает на незакрытый путь
# 501 с причиной. Кабинет от этого не страдает: supportApi.close объявлен
# (cabinet/src/api/support.ts:54), но не вызывается ни на одном экране.


# --- поддержка: уведомления ----------------------------------------------------

# У них уведомления двух видов, и «общие» — не лента:
#   /cabinet/tickets/notifications — настоящая лента (is_read, created_at), но
#     только про тикеты; пользователю доезжает один тип, admin_reply
#     (app/database/crud/ticket_notification.py:216-235);
#   /cabinet/notifications — НАСТРОЙКИ рассылок (routes/notifications.py:84-90),
#   /cabinet/notifications/history — заглушка, всегда пустой список
#     (routes/notifications.py:136-151).
# Поэтому лента собирается из тикетных, а не из «общих»: подставить настройки в
# колокольчик — уронить его (NotificationBell.tsx:57-59 получит items=undefined).
_NOTIF_TITLE = {"admin_reply": "Ответ поддержки"}


@handler("POST", "/api/notifications/read")
async def notifications_read(ctx: Ctx) -> dict[str, bool]:
    """Одно или все: у нас это одна ручка с телом, у них две разных."""
    notif_id = ctx.payload().get("id")
    if notif_id is None:
        path = "/cabinet/tickets/notifications/read-all"
    else:
        path = f"/cabinet/tickets/notifications/{int(notif_id)}/read"
    resp = await ctx.call("POST", path)
    # 404 — уведомления уже нет; для колокольчика это ровно то же «прочитано».
    # 403 (чужое или админское) прячем не будем: это признак ошибки, не нормы.
    if resp.status_code >= 400 and resp.status_code != 404:
        raise UpstreamError(resp)
    return {"ok": True}


# --- поддержка: «Информация» ---------------------------------------------------

# Собирается из пяти-шести их ручек, а страница открывается заново при каждом
# заходе — держим короткий кэш по языку, как для оформления.
_INFO_TTL = 60.0
_info_cache: dict[str, tuple[float, dict[str, Any]]] = {}

_HTML_TAG = re.compile(r"<[^>]+>")

# Расшифровка статусов — текст КАБИНЕТА, а не данные бота: у «Бедолаги» такой
# ручки нет и быть не может. Отдаём ту же константу, что наш собственный бэкенд
# (src/web/endpoints/public/info_content.py:138-149), иначе вкладка «Статусы»
# просто пустая. Плейсхолдера {brand} в этом тексте нет — подстановка не нужна.
_STATUSES_MD = """## Статусы подписки
- **Активна** — подписка активна и работает. Подключение доступно на всех ваших устройствах.
- **Истекает скоро** — до окончания подписки осталось менее 3 дней. Рекомендуем продлить заранее.
- **Истекла** — срок действия подписки завершился. Подключение приостановлено. Продлите подписку для восстановления доступа.
- **Пробная** — активен бесплатный пробный период. После его окончания необходимо оформить подписку.
- **Отключена** — подписка отключена администратором. Обратитесь в поддержку для уточнения причины.
- **Нет подписки** — подписка ещё не оформлена. Перейдите в раздел «Тарифы», чтобы выбрать план.

## Статусы трафика
- **В норме** — использовано менее 80% лимита трафика.
- **Заканчивается** — использовано более 80% лимита. Скорость будет снижена при достижении 100%.
- **Исчерпан** — лимит трафика исчерпан. Подключение ограничено до следующего сброса или продления."""


def _plain(text: Any) -> str:
    """Текст без разметки — для ответов FAQ: кабинет рисует их как есть,
    без markdown (cabinet/src/pages/InfoPage.tsx:210)."""
    if not isinstance(text, str):
        return ""
    return html.unescape(_HTML_TAG.sub("", text)).strip()


def _md(text: Any) -> str:
    """Их тексты писались для Telegram (HTML bot API — на стенде правила приходят
    с <b>…</b>), а вкладки «Информации» рендерит мини-markdown кабинета: он знает
    только `## `, `### `, `- ` и `**жирный**` (InfoPage.tsx:41-108). Без перевода
    пользователь увидит буквальные теги — React экранирует html, а не исполняет."""
    if not isinstance(text, str) or not text:
        return ""
    out = re.sub(r"</?(b|strong)>", "**", text)
    out = re.sub(r"</?(i|em)>", "*", out)
    out = re.sub(r"</?(code|pre)>", "`", out)
    out = re.sub(r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>', r"\2 (\1)", out, flags=re.S)
    out = re.sub(r"<br\s*/?>", "\n", out)
    out = _HTML_TAG.sub("", out)
    # Их markdown-дефолты начинаются с «# Заголовок», а мини-рендер такой уровень
    # не разбирает — решётка осталась бы в тексте абзацем.
    out = re.sub(r"^# (?!#)", "## ", out, flags=re.M)
    return html.unescape(out).strip()


def _pick_lang(value: Any, lang: str) -> str:
    """Мультиязычное поле инфо-страницы ({'ru': '…', 'en': '…'},
    app/cabinet/schemas/info_pages.py:12-13) в одну строку."""
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""
    for key in (lang, "ru", "en"):
        text = value.get(key)
        if isinstance(text, str) and text.strip():
            return text
    return next((v for v in value.values() if isinstance(v, str) and v.strip()), "")


@handler("POST", "/api/promocode/activate")
async def promocode_activate(ctx: Ctx) -> dict[str, Any]:
    """Активация промокода. Награду они применяют сами, но в ответе отдают только текст
    и баланс до/после — машинного типа награды у них нет вовсе."""
    code = str(ctx.payload().get("code") or "").strip()
    if not code:
        raise UpstreamError(httpx.Response(400, json={"detail": "Введите промокод"}))

    resp = await ctx.call("POST", "/cabinet/promocode/activate", json={"code": code})
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail")
        except ValueError:
            detail = None
        if isinstance(detail, str):
            raise UpstreamError(
                httpx.Response(
                    resp.status_code, json={"detail": _PROMO_ERRORS.get(detail, detail)}
                )
            )
        raise UpstreamError(resp)  # 422-валидация: detail списком, кабинет её разберёт

    data = resp.json() if resp.content else {}

    # Мульти-тариф: промокод бьёт в конкретную подписку, и они отвечают 200 с
    # success=false и списком на выбор (routes/promocode.py:59-65). Кабинет успех в
    # теле не проверяет (PromocodeCard.tsx:44-45) — молча показал бы «применён»,
    # поэтому переводим в явную ошибку. Сами подписку не выбираем: промокод сгорит
    # на той, которую пользователь не имел в виду.
    if data.get("error") == "select_subscription":
        names = ", ".join(
            str(s.get("tariff_name") or s.get("id"))
            for s in data.get("eligible_subscriptions") or []
        )
        raise UpstreamError(
            httpx.Response(
                409,
                json={
                    "detail": "Промокод применяется к конкретной подписке"
                    + (f" ({names})" if names else "")
                    + " — выбрать её можно только в боте"
                },
            )
        )
    if not data.get("success"):
        raise UpstreamError(
            httpx.Response(
                400,
                json={"detail": data.get("message") or "Не удалось активировать промокод"},
            )
        )

    # Единственный машинный признак награды — рост баланса (у них уже рубли). Дни,
    # трафик и скидку отличить нечем, поэтому тип оставляем пустым: кабинет тогда
    # покажет нейтральное «Промокод применён», а не соврёт про «+N дней».
    before = float(data.get("balance_before") or 0)
    after = float(data.get("balance_after") or 0)
    gained = round(after - before, 2)
    return {
        "success": True,
        # Свой же код в ответ: их успешный ответ кода не содержит.
        "code": code,
        "reward_type": "BALANCE" if gained > 0 else "",
        "reward": gained if gained > 0 else None,
        # Сверх контракта: их описание награды. Кабинет его пока не показывает, но без
        # него по ответу вообще не понять, что выдали.
        "message": data.get("bonus_description") or data.get("message"),
    }


# --- данные панели: живые ноды и трафик по нодам ------------------------------
#
# Правило владельца: это НЕ данные бота. Список нод, их состояние и расход по
# нодам живут в Remnawave; у «Бедолаги» кабинетных ручек для этого нет вовсе (в её
# API такое есть только под админом: /cabinet/admin/remnawave/nodes,
# /cabinet/admin/users/{id}/node-usage). Поэтому берём из панели напрямую и раздел
# не выключаем: панель не настроена — отдаём пустые формы, кабинет прячет блоки сам.

_PANEL_DAYS = 30  # окно графика и «любимого сервера» — как в нашем бэкенде
_PANEL_TOP_NODES = 5  # столько нод показывает главная
# Статистику панель считает не бесплатно, а главная дёргает сразу две наши ручки
# (график + любимый сервер) из одного и того же ответа — держим короткий кэш.
_USAGE_TTL = 60.0
# uuid юзера в панели не меняется даже при перевыпуске ссылки (меняется shortUuid),
# поэтому связку shortUuid → uuid можно держать долго.
_UUID_TTL = 600.0
_NODES_TTL = 30.0

_usage_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_uuid_cache: dict[str, tuple[float, str]] = {}
_nodes_cache: tuple[float, list[dict[str, Any]]] | None = None


def _short_uuid(url: str) -> str:
    """shortUuid из ссылки подписки: `https://sub.example.com/BMqCVzejpeRtH3fp` → хвост.

    Это единственный мост между ботом и панелью: свой `remnawave_uuid` «Бедолага» в
    кабинетное API не отдаёт ни одной ручкой, а ссылку подписки отдаёт всегда —
    даже когда включено «скрывать ссылку» (это отдельный флаг ответа).
    """
    tail = str(url or "").split("?")[0].split("#")[0].rstrip("/")
    return tail.rsplit("/", 1)[-1] if "/" in tail else ""


async def _panel_uuid(ctx: Ctx) -> str | None:
    """UUID пользователя в панели. None = панели нет, подписки нет или ссылка пустая."""
    if not ctx.has_panel:
        return None
    sub = (await ctx.json("/cabinet/subscription", {}) or {}).get("subscription") or {}
    short = _short_uuid(sub.get("subscription_url") or "")
    if not short:
        return None
    now = time.monotonic()
    hit = _uuid_cache.get(short)
    if hit and now - hit[0] < _UUID_TTL:
        return hit[1]
    user = await ctx.panel_json(f"/api/users/by-short-uuid/{short}", {}) or {}
    uuid = user.get("uuid") if isinstance(user, dict) else None
    if not uuid:
        return None
    _uuid_cache[short] = (now, str(uuid))
    return str(uuid)


async def _panel_usage(ctx: Ctx) -> dict[str, Any]:
    """Расход пользователя по нодам за 30 дней одним запросом к панели.

    Ответ панели: {categories: [даты], sparklineData: [итог за день],
    topNodes: [{name, countryCode, total}], series: [{name, countryCode, total,
    data: [по дням]}]}. Всё в БАЙТАХ — как и наш контракт, пересчёт не нужен.
    """
    uuid = await _panel_uuid(ctx)
    if not uuid:
        return {}
    now = time.monotonic()
    hit = _usage_cache.get(uuid)
    if hit and now - hit[0] < _USAGE_TTL:
        return hit[1]
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=_PANEL_DAYS)
    data = await ctx.panel_json(
        f"/api/bandwidth-stats/users/{uuid}",
        {},
        # Панель принимает только дату, без времени.
        params={
            "topNodesLimit": _PANEL_TOP_NODES,
            "start": start.strftime("%Y-%m-%d"),
            "end": end.strftime("%Y-%m-%d"),
        },
    )
    if not isinstance(data, dict):
        return {}
    _usage_cache[uuid] = (now, data)
    return data


async def _panel_nodes(ctx: Ctx) -> list[dict[str, Any]] | None:
    """Живое состояние нод из панели. None = панель не настроена или не ответила.

    Онлайн считаем по `isConnected`: в Remnawave 2.8 ответ `/api/nodes` полей
    `isNodeOnline` и `isXrayRunning` больше не содержит вовсе (проверено на живой
    панели), и опора на них показала бы аварию на здоровых нодах. Ноды, выключенные
    админом (`isDisabled`), в статус не попадают — их отсутствие не поломка.
    """
    global _nodes_cache
    if not ctx.has_panel:
        return None
    now = time.monotonic()
    if _nodes_cache and now - _nodes_cache[0] < _NODES_TTL:
        return _nodes_cache[1]
    raw = await ctx.panel_json("/api/nodes")
    if not isinstance(raw, list):
        return None
    nodes = [
        {
            "name": n.get("name") or "",
            "country_code": (n.get("countryCode") or "").lower(),
            "online": bool(n.get("isConnected")),
        }
        for n in raw
        if isinstance(n, dict) and not n.get("isDisabled")
    ]
    _nodes_cache = (now, nodes)
    return nodes


@handler("GET", "/api/subscription/server-stats")
async def server_stats(ctx: Ctx) -> dict[str, Any]:
    """«Любимый сервер» и расход по нодам за 30 дней."""
    usage = await _panel_usage(ctx)
    # topNodes уже урезан лимитом; series — все ноды. Берём первое непустое, чтобы
    # не потерять данные, если панель отдала только один из двух срезов.
    raw = usage.get("topNodes") or usage.get("series") or []
    nodes = [
        {
            "name": n.get("name") or "",
            "country_code": (n.get("countryCode") or "").lower(),
            "total": int(n.get("total") or 0),
        }
        for n in raw
        if isinstance(n, dict)
    ]
    nodes.sort(key=lambda x: x["total"], reverse=True)
    # Пусто = «нет данных»: кабинет покажет прочерк, а не ошибку.
    return {"favorite": nodes[0] if nodes else None, "nodes": nodes}


@handler("GET", "/api/subscription/traffic-history")
async def traffic_history(ctx: Ctx) -> dict[str, Any]:
    """График расхода по дням за 30 дней."""
    usage = await _panel_usage(ctx)
    dates = usage.get("categories") or []
    # sparklineData — готовая сумма по всем нодам за день (сверено на живой панели:
    # поэлементно совпадает с суммой series[].data). Если длина не бьётся с датами,
    # не доверяем ей и складываем сами.
    totals = usage.get("sparklineData") or []
    if len(totals) != len(dates):
        series = [s.get("data") or [] for s in usage.get("series") or [] if isinstance(s, dict)]
        totals = [sum(int(d[i] or 0) for d in series if i < len(d)) for i in range(len(dates))]
    days = [
        # Панель отдаёт дату строкой YYYY-MM-DD; срез — страховка от ISO-datetime.
        {"date": str(dates[i])[:10], "total": int(totals[i] or 0)}
        for i in range(min(len(dates), len(totals)))
    ]
    return {"days": days}


@handler("GET", "/api/subscription/service-status")
async def service_status(ctx: Ctx) -> dict[str, Any]:
    """Быстрая проверка «всё ли работает» для мастера диагностики."""
    nodes = await _panel_nodes(ctx)
    if nodes is None:
        # Панели нет. Живого здоровья нод у «Бедолаги» в кабинетном API не существует;
        # ближайшее, что есть, — её собственный флаг «сервер доступен для подключения»
        # (`is_available` = включён админом и не переполнен). Это не «нода онлайн», но
        # для мастера диагностики честнее, чем молчание: он ищет явную аварию.
        data = await ctx.json("/cabinet/subscription/countries", {}) or {}
        nodes = [
            {
                "name": c.get("name") or "",
                "country_code": (c.get("country_code") or "").lower(),
                "online": bool(c.get("is_available")),
            }
            for c in data.get("countries") or []
        ]
    online = sum(1 for n in nodes if n["online"])
    # Пустой список = «проверять нечего»: мастер не должен объявлять аварию там,
    # где просто нет данных (у него ветка «ни одна нода не онлайн» = fail).
    return {"nodes": nodes, "all_operational": online == len(nodes)}


@handler("POST", "/api/subscription/reissue")
async def reissue(ctx: Ctx) -> dict[str, Any]:
    """Перевыпуск ссылки подписки. У них это `revoke`.

    Панели тут не нужно: их ручка сама зовёт панель и сразу пишет новую ссылку себе
    в БД (SubscriptionService.revoke_subscription), поэтому доучивать url из панели,
    как это делает наш бэкенд, не требуется — кабинету достаточно перезапросить
    /api/subscription/current.
    """
    resp = await ctx.call("POST", "/cabinet/subscription/revoke")
    if resp.status_code >= 400:
        # 403 «перевыпуск отключён настройкой», 429 «кулдаун» (по умолчанию 15 мин),
        # 400 «подписка не активна» — текст ошибки кабинет показывает пользователю,
        # поэтому отдаём ответ как есть, а не превращаем в 500.
        raise UpstreamError(resp)
    body = resp.json() if resp.content else {}
    if not isinstance(body, dict):
        body = {}
    return {
        "success": bool(body.get("success", True)),
        # Сверх контракта: кулдаун настраиваемый, полезно видеть в отладке.
        "cooldown_seconds": body.get("cooldown_seconds"),
    }


# --- заморозка: у них это пауза ТОЛЬКО суточного тарифа -----------------------
#
# Общей заморозки подписки (наши «дни не сгорают, доступ выключен, лимит max_days»)
# у «Бедолаги» нет. Есть `POST /cabinet/subscription/pause` — переключатель паузы
# суточного тарифа: пауза останавливает суточные списания, а возобновление СПИСЫВАЕТ
# день и ставит end_date = сейчас + 1 день. Для суточного тарифа смысл совпадает
# («деньги не тратятся, пока не пользуюсь»), для остальных их ручка отвечает 400.
# Поэтому `enabled` включаем только на суточных тарифах — на прочих кабинет прячет
# блок сам, и мы не обещаем того, чего бэкенд не сделает.
#
# Подделать нашу заморозку через панель нельзя: выключить юзера в панели адаптер
# сможет, а вот продлить срок в БД бота при возобновлении — нет, и «дни не сгорают»
# окажется ложью (их монитор считает срок по своему end_date).


# --- заморозка подписки -------------------------------------------------------
#
# Заморозка — фича НАШЕГО кабинета: у «Бедолаги» её нет, а их «пауза» совсем про
# другое (только суточные тарифы, при возобновлении списывает деньги). Код их бота
# мы не трогаем, поэтому делаем всё их же штатным админским HTTP-API:
#
#   пауза       DELETE /subscriptions/{id}      → статус DISABLED + выключение в
#                                                 панели, срок НЕ трогается;
#   возобновить POST   /subscriptions/{id}/extend {days} → срок сдвигается вперёд,
#                                                 статус возвращается в ACTIVE,
#                                                 панель синхронизируется.
#
# Почему именно так, а не «поправить дату в панели»: любое действие пользователя
# (продление, покупка, промокод, смена серверов) заставляет бота переписать панель
# из своей базы — панельная правка живёт до первого клика. Через их API запись
# делает сам бот, поэтому его же синхронизации нашу паузу подтверждают.
#
# Плата за их API: продлевать можно только ЦЕЛЫМИ сутками. Остаток копим в своей
# памяти (state.credits) и учитываем при следующем возобновлении.

FREEZE_MAX_DAYS = 30
_DAY = 86400


def _iso(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


async def _freeze_context(ctx: Ctx) -> tuple[str, dict[str, Any]]:
    """Кто спрашивает и что у него с подпиской."""
    me = await ctx.json("/cabinet/auth/me", {}) or {}
    sub = (await ctx.json("/cabinet/subscription", {}) or {}).get("subscription") or {}
    return str(me.get("id") or ""), sub


@handler("GET", "/api/subscription/freeze-status")
async def freeze_status(ctx: Ctx) -> dict[str, Any]:
    enabled = ctx.can_admin and ctx.state is not None and ctx.state.available
    user_key, sub = await _freeze_context(ctx)
    record = ctx.state.get_freeze(user_key) if (enabled and user_key) else None

    if record:
        return {
            "enabled": True,
            "frozen": True,
            # Показываем остаток срока, сохранённый на момент паузы, — он и вернётся.
            "remaining_days": max(0, int(record["remaining_seconds"]) // _DAY),
            "max_days": FREEZE_MAX_DAYS,
            "can_freeze": False,
        }

    end = _iso(sub.get("end_date"))
    left = (end - datetime.now(timezone.utc)).total_seconds() if end else 0
    active = str(sub.get("status") or "").lower() == "active" and left > 0
    return {
        "enabled": bool(enabled),
        "frozen": False,
        "can_freeze": bool(enabled and active),
        "max_days": FREEZE_MAX_DAYS,
        "days_left": max(0, int(left // _DAY)),
    }


@handler("POST", "/api/subscription/freeze")
async def freeze(ctx: Ctx) -> dict[str, Any]:
    if not (ctx.can_admin and ctx.state is not None and ctx.state.available):
        raise NotAvailable(
            "Заморозка недоступна: адаптеру не выдан админский токен «Бедолаги» "
            "или ему некуда сохранить состояние"
        )
    user_key, sub = await _freeze_context(ctx)
    if not user_key or not sub:
        raise UpstreamError(httpx.Response(404, json={"detail": "Нет активной подписки"}))
    if ctx.state.get_freeze(user_key):
        raise UpstreamError(httpx.Response(409, json={"detail": "Подписка уже на паузе"}))

    end = _iso(sub.get("end_date"))
    left = (end - datetime.now(timezone.utc)).total_seconds() if end else 0
    if str(sub.get("status") or "").lower() != "active" or left <= 0:
        raise UpstreamError(
            httpx.Response(409, json={"detail": "Подписка неактивна — заморозить нельзя"})
        )

    resp = await ctx.admin("DELETE", f"/subscriptions/{sub.get('id')}")
    if resp is None or resp.status_code >= 400:
        raise UpstreamError(
            httpx.Response(502, json={"detail": "Не удалось заморозить подписку"})
        )
    # Пишем в свою память ПОСЛЕ успеха у них: иначе покажем паузу, которой нет.
    ctx.state.add_freeze(user_key, int(sub.get("id") or 0), int(left), sub.get("end_date"))
    return {"frozen": True, "remaining_days": max(0, int(left // _DAY))}


@handler("POST", "/api/subscription/unfreeze")
async def unfreeze(ctx: Ctx) -> dict[str, Any]:
    if not (ctx.can_admin and ctx.state is not None and ctx.state.available):
        raise NotAvailable("Заморозка недоступна на этом бэкенде")
    user_key, _sub = await _freeze_context(ctx)
    record = ctx.state.get_freeze(user_key) if user_key else None
    if not record:
        raise UpstreamError(httpx.Response(409, json={"detail": "Подписка не на паузе"}))

    frozen_at = _iso(record["frozen_at"]) or datetime.now(timezone.utc)
    paused = max(0, int((datetime.now(timezone.utc) - frozen_at).total_seconds()))

    # Продление у них работает ЦЕЛЫМИ сутками, и другого способа вернуть доступ
    # нет — значит пауза короче суток технически невозможна: вернуть меньше дня
    # нечем, а округление вверх раньше дарило календарный день за паузу в секунду
    # (и повторами — сколько угодно дней).
    if paused < _DAY:
        wait_h = max(1, (_DAY - paused + 3599) // 3600)
        raise UpstreamError(httpx.Response(409, json={
            "detail": f"Снять с паузы можно через {wait_h} ч: продление у этого "
                      f"бэкенда работает целыми сутками",
        }))

    # Вернуть можно не больше, чем оставалось на момент паузы, и не больше предела:
    # их продление истёкшей подписке ставит «сейчас + дни», поэтому долгая пауза
    # без этого предела превращалась бы в подарок.
    usable = min(paused, int(record["remaining_seconds"]), FREEZE_MAX_DAYS * _DAY)
    total = usable + ctx.state.credit(user_key)
    days = max(1, total // _DAY)

    resp = await ctx.admin(
        "POST", f"/subscriptions/{record['subscription_id']}/extend", json={"days": int(days)}
    )
    if resp is None or resp.status_code >= 400:
        raise UpstreamError(
            httpx.Response(502, json={"detail": "Не удалось снять подписку с паузы"})
        )
    # Остаток — ТОЛЬКО после успеха: иначе упавшее продление списало бы накопленное,
    # а повторная попытка выдала лишний день.
    ctx.state.set_credit(user_key, total - days * _DAY)
    ctx.state.drop_freeze(user_key)
    sub = (await ctx.json("/cabinet/subscription", {}) or {}).get("subscription") or {}
    return {"frozen": False, "expire_at": sub.get("end_date")}
