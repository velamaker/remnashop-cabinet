"""Админка кабинета поверх «Бедолаги»: разделы, которые реально переводятся.

Зачем отдельный модуль. Админских ручек в нашем контракте под сорок штук
(`docs/CABINET-API-CONTRACT.md`), у «Бедолаги» — свой набор и свои формы. Пока
переведено не всё, показывать раздел целиком нельзя: админ провалится в экраны с
ошибками. Поэтому здесь живут только те ручки, под которые у бота есть данные, а
`whoami` отдаёт `full_access=false` и ровно те разделы, что здесь реализованы.

ОТКУДА БЕРЁМ ДАННЫЕ И ПОЧЕМУ ИМЕННО ОТТУДА.

У «Бедолаги» два admin-API:
  • кабинетное `/cabinet/admin/*` — по сессии самого пользователя (Bearer), права
    считает их RBAC (`require_permission`), владелец из ADMIN_IDS проходит как
    legacy-админ;
  • «машинное» с `X-API-Key` — по токену, выданному адаптеру.

Основной источник — ПЕРВЫЙ, и это вопрос безопасности, а не удобства. Токен
`BEDOLAGA_ADMIN_TOKEN` принадлежит адаптеру, а не человеку: сходи мы за списком
пользователей с ним, админкой смог бы пользоваться ЛЮБОЙ вошедший в кабинет —
адаптер сам себе выдал бы права. С сессией пользователя решение принимает их
бот: не админ — 403, и наружу уходит их же отказ.

Токен адаптера используется здесь ровно один раз и только как дополнение: их
список пользователей не отдаёт email (в карточке он есть, в списке — нет), а
колонка «Email» в кабинете важна — почти все местные учётки как раз почтовые.
Обогащение включается ТОЛЬКО после того, как их же кабинетная ручка ответила 200
(то есть их RBAC уже подтвердил админа), и только если страницы совпали по id —
иначе поле остаётся пустым. Подробности — в `_enrich_by_id`.

ЧЕГО ЗДЕСЬ НЕТ И ПОЧЕМУ. Всё, чему у бота нет источника, не выдумывается:
поля, которых нет, не кладутся вовсе (пустая карточка честнее нуля, который
читается как настоящий), а фильтры и сортировки, которых их API не умеет,
отвечают 501 с внятным текстом — админ видит причину, а не молча другой ответ.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from compose import Ctx, NotAvailable, UpstreamError, handler

# --- мелкие переводчики -----------------------------------------------------


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _amount(kopeks: Any) -> str:
    """Копейки → строка суммы, как в нашем контракте (`amount: string`).

    Кабинет прогоняет её через Number() и сам дорисовывает символ валюты, поэтому
    здесь именно число строкой, без «₽» и пробелов. Знак убираем: у них списание
    с баланса записано отрицательным, а в реестре платежей сумма — модуль.
    """
    return f"{abs(_int(kopeks)) / 100:.2f}"


def _name(u: dict[str, Any]) -> str:
    """Имя для показа. У них уже есть `full_name` (и он умеет падать в локальную
    часть почты), но на всякий случай собираем из частей — иначе в списке
    появляются безымянные строки."""
    full = (u.get("full_name") or "").strip()
    if full:
        return full
    parts = [u.get("first_name") or "", u.get("last_name") or ""]
    name = " ".join(p for p in parts if p).strip()
    return name or (u.get("username") or "") or (u.get("email") or "") or "—"


def _auth_type(u: dict[str, Any]) -> str:
    """Как человек входит. Отдельного поля у них нет, но вход определяется тем,
    что к учётке привязано: есть telegram_id — вход через Telegram, иначе почта.
    Это не догадка, а то же правило, по которому их бот пускает в кабинет."""
    return "telegram" if u.get("telegram_id") else "email"


async def _extra(ctx: Ctx, path: str, **kw: Any) -> Any:
    """Необязательный кусочек экрана: любая беда — просто «данных нет».

    Почему не `ctx.json(..., soft=True)`. Мягкий режим compose.py осознанно НЕ
    глотает 401/403 — для пользовательских ручек это правильно (чужая сессия не
    должна выглядеть как пустой ответ). Здесь же ровно наоборот: права в их RBAC
    нарезаны по разделам, и админ с `users:read` без `stats:read` получал бы 403
    на второстепенном запросе — и вместе с ним пустой экран вместо списка,
    который ему как раз доступен. Главный запрос каждой ручки остаётся строгим,
    он же и решает, пускать ли человека.
    """
    try:
        resp = await ctx.call("GET", path, **kw)
    except httpx.HTTPError:
        return None
    if resp.status_code >= 400:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


# --- роли ------------------------------------------------------------------
#
# В их списке пользователей роли нет вовсе, а колонка «Роль» в кабинете есть.
# Кто админ — знает их RBAC: `/cabinet/admin/rbac/users` отдаёт ТОЛЬКО тех, у
# кого есть роль (обычно единицы), поэтому запрос дешёвый. Роль владельца бот
# выдаёт сам при первом входе в кабинет (ensure_superadmin_role_on_login), так
# что владелец в этом списке появляется.
#
# Ответ общий для всех админов и не меняется поминутно — держим короткий кэш,
# чтобы не бить по боту на каждой странице списка.
_ROLES_TTL = 60.0
_roles_cache: tuple[float, dict[int, int]] | None = None

# Наши роли (src/core/enums.Role): 1 — пользователь, 3 — админ, 5 — владелец.
_ROLE_USER, _ROLE_ADMIN, _ROLE_OWNER = 1, 3, 5


async def _roles(ctx: Ctx) -> dict[int, int]:
    """id пользователя → наша роль. Не смогли узнать — пусто (все «пользователи»).

    Список ролей — второстепенная колонка: у админа с урезанными правами эта
    ручка ответит 403, и это не повод ронять весь список (см. `_extra`).
    """
    global _roles_cache
    now = time.monotonic()
    if _roles_cache and now - _roles_cache[0] < _ROLES_TTL:
        return _roles_cache[1]

    rows = await _extra(ctx, "/cabinet/admin/rbac/users")
    if not isinstance(rows, list):
        return {}
    result: dict[int, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        uid = row.get("user_id")
        if uid is None:
            continue
        names = [str(n).lower() for n in (row.get("role_names") or [])]
        if not names:
            continue
        # Superadmin у них — единственная роль с полным доступом («*:*»), это и
        # есть наш «владелец». Остальные роли (Admin, Moderator, Support…) —
        # админы: точнее их уровни в наши три ступени не раскладываются.
        result[uid] = _ROLE_OWNER if "superadmin" in names else _ROLE_ADMIN
    _roles_cache = (now, result)
    return result


# --- обогащение списка почтой ----------------------------------------------


async def _enrich_by_id(
    ctx: Ctx, users: list[dict[str, Any]], params: dict[str, Any]
) -> dict[int, dict[str, Any]]:
    """id → запись того же пользователя из их «машинного» `/users` (X-API-Key).

    Зачем. Их кабинетный список не отдаёт ни email, ни язык, ни реф-код, а
    машинный `/users` отдаёт всё это — но у него сломан `total` (их
    `with_only_columns(func.count())` теряет FROM и всегда возвращает 1), так что
    строить на нём пагинацию нельзя. Поэтому страницы берём из кабинетного
    списка, а недостающие поля — отсюда.

    Почему это безопасно: сюда попадаем только после успешного ответа их
    кабинетной ручки, то есть их RBAC уже признал этого человека админом.

    Почему это не «пришивание чужих данных»: обе выборки идут одним и тем же
    порядком (`created_at DESC`) с тем же limit/offset, и мы сверяем список id
    один-в-один. Разошлись (поиск, гонка, другая версия бота) — возвращаем
    пусто, и поля останутся незаполненными. Лучше прочерк, чем чужая почта в
    чужой строке.
    """
    if not ctx.can_admin or not users:
        return {}
    resp = await ctx.admin("GET", "/users", params=params)
    if resp is None or resp.status_code >= 400:
        return {}
    try:
        body = resp.json()
    except ValueError:
        return {}
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list) or len(items) != len(users):
        return {}
    if [u.get("id") for u in users] != [i.get("id") for i in items]:
        return {}
    return {i["id"]: i for i in items if isinstance(i, dict) and i.get("id") is not None}


# --- список пользователей ---------------------------------------------------

# Наша сортировка → их `sort_by`. Всё, чего в их списке нет, честно отказывает:
# молча отдать другой порядок хуже, чем сказать «так не умеем» — админ хотя бы
# знает, что смотрит.
_SORT = {"created_at": "created_at"}


@handler("GET", "/api/admin/users")
async def users(ctx: Ctx) -> dict[str, Any]:
    """Список пользователей — та самая ручка, по которой кабинет включает админку."""
    limit = max(1, min(200, _int(ctx.query.get("limit"), 25)))
    offset = max(0, _int(ctx.query.get("offset"), 0))
    search = (ctx.query.get("search") or "").strip()
    blocked = (ctx.query.get("blocked") or "").lower()
    sort = (ctx.query.get("sort") or "created_at").lower()
    order = (ctx.query.get("order") or "desc").lower()

    # Фильтры, которых у их списка нет вовсе. Тихо игнорировать нельзя: кабинет
    # показал бы полный список под видом отфильтрованного.
    if ctx.query.get("role"):
        raise NotAvailable(
            "Фильтр по ролям недоступен: список пользователей этого бота роль не отдаёт"
        )
    if ctx.query.get("expiring_days"):
        raise NotAvailable(
            "Фильтр «истекают ≤ N дней» недоступен: у этого бота нет такой выборки"
        )
    if sort not in _SORT:
        raise NotAvailable(
            "Такая сортировка недоступна: этот бот сортирует список только по дате регистрации"
        )
    if order != "desc":
        raise NotAvailable("Обратный порядок сортировки недоступен: бот отдаёт только «сначала новые»")

    params: dict[str, Any] = {"limit": limit, "offset": offset, "sort_by": _SORT[sort]}
    if blocked == "true":
        params["status"] = "blocked"
    elif blocked == "false":
        params["status"] = "active"

    # Поиск у них разрезан надвое: `search` смотрит имя/username/telegram_id, а
    # почта живёт в отдельном параметре `email`, и оба условия складываются через
    # AND. Одного запроса на «имя ИЛИ почта» не существует, поэтому: адрес со
    # «собакой» сразу ищем как почту, всё остальное — сначала по имени, и только
    # если совпадений нет, повторяем как поиск по почте (админ часто вбивает
    # локальную часть адреса без «@»).
    if search:
        params["email" if "@" in search else "search"] = search
    data = await ctx.json("/cabinet/admin/users", {}, params=params) or {}
    if search and "@" not in search and not (data.get("users") or []):
        params.pop("search", None)
        params["email"] = search
        data = await ctx.json("/cabinet/admin/users", {}, params=params) or {}

    raw = [u for u in (data.get("users") or []) if isinstance(u, dict)]
    # Их «машинный» список тех же фильтров не знает: `search` там ищет иначе, а
    # `email` он не умеет вовсе. Поэтому обогащаем только чистую страницу — при
    # поиске сверка id всё равно не сойдётся, и запрос был бы холостым.
    extra: dict[int, dict[str, Any]] = {}
    if not search:
        mirror = {"limit": limit, "offset": offset}
        if params.get("status"):
            mirror["status"] = params["status"]
        extra = await _enrich_by_id(ctx, raw, mirror)
    roles = await _roles(ctx)

    items = []
    for u in raw:
        uid = u.get("id")
        more = extra.get(uid, {})
        items.append(
            {
                "id": uid,
                "telegram_id": u.get("telegram_id"),
                "auth_type": _auth_type(u),
                # Почта есть только если обогащение сошлось; иначе честный прочерк.
                "email": more.get("email"),
                # Подтверждена ли почта, их список не говорит (в карточке —
                # говорит). Не знаем — значит «нет»: fail-closed.
                "is_email_verified": bool(more.get("email_verified")),
                "name": _name(u),
                "username": u.get("username"),
                "role": roles.get(uid, _ROLE_USER),
                "language": more.get("language") or "",
                "is_blocked": str(u.get("status") or "").lower() == "blocked",
                # Отдельного «заблокировал бота» у них нет — только статус учётки.
                "is_bot_blocked": False,
                # Доступность пробного периода у них решается по тарифу и истории
                # активаций, готового флага на пользователе нет — не выдумываем.
                "is_trial_available": False,
                # Персональных скидок на пользователе у них нет: скидки живут в
                # промо-группах. Ноль здесь — это «механики нет», а не «нулевая».
                "personal_discount": 0,
                "purchase_discount": 0,
                # Баллов лояльности в их боте нет вовсе.
                "points": 0,
                # Рублёвый кошелёк у них и есть основной баланс.
                "cabinet_balance": round(_int(u.get("balance_kopeks")) / 100, 2),
                "referral_code": more.get("referral_code"),
                "created_at": u.get("created_at"),
                # Время входа в кабинет в списке они не отдают (в карточке есть).
                "last_login_at": None,
            }
        )
    return {
        "total": _int(data.get("total")),
        "limit": limit,
        "offset": offset,
        "items": items,
    }


# --- карточка пользователя --------------------------------------------------


def _subscription(sub: Any) -> dict[str, Any] | None:
    """Их подписка → форма `current_subscription` нашего контракта."""
    if not isinstance(sub, dict):
        return None
    return {
        "status": str(sub.get("status") or "").upper(),
        "is_trial": bool(sub.get("is_trial")),
        "plan_name": sub.get("tariff_name"),
        "expire_at": sub.get("end_date"),
        # ГИГАБАЙТЫ — как в нашем контракте (см. subscription_current в compose.py).
        "traffic_limit": _int(sub.get("traffic_limit_gb")),
        "device_limit": _int(sub.get("device_limit")),
    }


@handler("GET", "/api/admin/users/{id}")
async def user_detail(ctx: Ctx) -> dict[str, Any]:
    """Карточка. Здесь их данные полнее списка: есть почта, язык, реф-код и
    последний вход в кабинет."""
    data = await ctx.json(f"/cabinet/admin/users/{ctx.param('id')}", {}) or {}
    uid = data.get("id")
    roles = await _roles(ctx)
    referral = data.get("referral") if isinstance(data.get("referral"), dict) else {}
    subs = [s for s in (data.get("subscriptions") or []) if isinstance(s, dict)]

    user = {
        "id": uid,
        "telegram_id": data.get("telegram_id"),
        "auth_type": _auth_type(data),
        "email": data.get("email"),
        "is_email_verified": bool(data.get("email_verified")),
        "name": _name(data),
        "username": data.get("username"),
        "role": roles.get(uid, _ROLE_USER),
        "language": data.get("language") or "",
        "is_blocked": str(data.get("status") or "").lower() == "blocked",
        "is_bot_blocked": False,
        "is_trial_available": False,
        "personal_discount": 0,
        "purchase_discount": 0,
        "points": 0,
        "cabinet_balance": round(_int(data.get("balance_kopeks")) / 100, 2),
        "referral_code": referral.get("referral_code"),
        "created_at": data.get("created_at"),
        "last_login_at": data.get("cabinet_last_login"),
    }

    # Лента платежей человека. У их транзакции нет внешнего id платежа и нет
    # тарифа — это движение по балансу, а не запись шлюза, поэтому лишние поля
    # остаются пустыми, а не заполняются похожими.
    transactions = []
    for t in data.get("recent_transactions") or []:
        if not isinstance(t, dict):
            continue
        transactions.append(
            {
                "payment_id": None,
                "user_id": uid,
                "user_name": user["name"],
                "user_email": user["email"],
                "status": "COMPLETED" if t.get("is_completed") else "PENDING",
                "gateway_type": t.get("payment_method") or "",
                "purchase_type": t.get("type") or "",
                "is_test": False,
                "amount": _amount(t.get("amount_kopeks")),
                "currency": "RUB",
                "plan_name": None,
                "plan_duration": None,
                "created_at": t.get("created_at"),
                "updated_at": None,
            }
        )

    return {
        "user": user,
        "current_subscription": _subscription(data.get("subscription")),
        "subscriptions_count": len(subs),
        # `logins` не кладём вовсе: журнала входов у их бота нет, а блок в
        # карточке печатает «всего входов / уникальных IP» — нули там читались бы
        # как «человек ни разу не заходил». Последний вход виден в поле выше.
        "transactions": transactions,
    }


# --- сводка для главной админки ---------------------------------------------


@handler("GET", "/api/admin/statistics/overview")
async def statistics_overview(ctx: Ctx) -> dict[str, Any]:
    """Три блока дашборда из трёх их ручек.

    Первая — строгая: она же и проверка прав (не админ → их 403 уедет в кабинет).
    Остальные мягкие: у админа с урезанными правами (в их RBAC статистика продаж
    — отдельное право) блок просто останется пустым, а не уронит весь экран.
    """
    stats, dash, health = await asyncio.gather(
        ctx.json("/cabinet/admin/users/stats", {}),
        _extra(ctx, "/cabinet/admin/stats/dashboard"),
        # days=0 — «за всё время»: наш дашборд показывает итоги, а не месяц.
        _extra(ctx, "/cabinet/admin/stats/sales/payment-health", params={"days": 0}),
    )
    stats = stats or {}
    subs = (dash or {}).get("subscriptions") or {}
    health = health or {}

    total_users = _int(stats.get("total_users"))
    with_sub = _int(stats.get("users_with_subscription"))
    users_block = {
        "total": total_users,
        "active": _int(stats.get("active_users")),
        "blocked": _int(stats.get("blocked_users")),
        "new_today": _int(stats.get("new_today")),
        "new_week": _int(stats.get("new_week")),
        "new_month": _int(stats.get("new_month")),
        "with_active_subscription": _int(stats.get("users_with_active_subscription")),
        "with_expired_subscription": _int(stats.get("users_with_expired_subscription")),
        "without_subscription": max(0, total_users - with_sub),
        "with_trial": _int(stats.get("users_with_trial")),
        # «Платящих» (уникальных плательщиков) они не считают нигде — ключ не
        # кладём: пустая плитка честнее нуля, который выглядит как «никто не
        # платит».
    }

    # Подписки: их сводка знает только всего/активные/пробные. «Истекшие» у них
    # считаются как «всего минус активные», то есть туда попадают и отключённые,
    # и лимитированные — под нашу плитку «Истекших» это не годится. Остальных
    # разрезов (отключённые, безлимит, ограниченные, истекают скоро) у них нет,
    # поэтому ключей нет вовсе.
    subs_block = {
        "total": _int(subs.get("total")),
        "active": _int(subs.get("active")),
        "trial": _int(subs.get("trial")),
    }

    # Платежи: их «здоровье оплат» считает попытки и успешные по всем шлюзам.
    # Разбивку по шлюзам не отдаём: доход по каждому (всего/месяц/неделя/день)
    # там не считается, а карточка шлюза без сумм показывала бы четыре нуля —
    # пустой список кабинет просто не рисует.
    tx_block: dict[str, Any] = {"gateways": []}
    if health:
        tx_block["total"] = _int(health.get("total_attempts"))
        tx_block["completed"] = _int(health.get("total_paid"))

    # Ключи блоков присутствуют всегда, даже пустые: кабинет разбирает ответ
    # деструктуризацией и на отсутствующем объекте падает белым экраном.
    return {"users": users_block, "transactions": tx_block, "subscriptions": subs_block}


# --- реестр платежей --------------------------------------------------------

# Наш фильтр статуса → их. «FAILED» у них отдельным состоянием не хранится.
_PAYMENT_STATUS = {"PENDING": "pending", "COMPLETED": "paid", "CANCELLED": "cancelled"}

_METHODS_TTL = 60.0
_methods_cache: tuple[float, set[str]] | None = None


async def _payment_methods(ctx: Ctx) -> set[str]:
    """Какие платёжные методы у этого бота вообще бывают.

    Нужно ровно для одного: их поиск по платежам МОЛЧА игнорирует незнакомое имя
    метода, и фильтр «шлюз» выглядел бы применённым, а список приходил бы полный.
    """
    global _methods_cache
    now = time.monotonic()
    if _methods_cache and now - _methods_cache[0] < _METHODS_TTL:
        return _methods_cache[1]
    rows = await _extra(ctx, "/cabinet/admin/payment-methods")
    if not isinstance(rows, list):
        return set()
    known = {
        str(r.get("method_id")).lower()
        for r in rows or []
        if isinstance(r, dict) and r.get("method_id")
    }
    if known:
        _methods_cache = (now, known)
    return known


@handler("GET", "/api/admin/transactions")
async def transactions(ctx: Ctx) -> dict[str, Any]:
    """Лента платежей.

    Источник — их поиск по платежам (`/cabinet/admin/payments/search`), а не
    таблица транзакций: только у него есть постраничность с честным `total`,
    фильтры по статусу, методу и датам — то есть всё, что просит наш экран. У
    «машинного» `/transactions` (X-API-Key) `total` сломан (всегда 1), а имени и
    почты плательщика он не отдаёт вовсе.

    Что при этом видно: платежи через шлюзы. Оплата подписки СПИСАНИЕМ С БАЛАНСА
    в этот реестр не попадает — у них это движение по кошельку, а не платёж;
    такие строки видны в карточке пользователя.
    """
    limit = max(1, min(100, _int(ctx.query.get("limit"), 25)))
    offset = max(0, _int(ctx.query.get("offset"), 0))
    # У них страницы, у нас смещение. Страница получается только из смещения,
    # кратного размеру, — на произвольном offset честнее отказать, чем показать
    # соседнюю страницу под видом запрошенной.
    if offset % limit:
        raise NotAvailable(
            "Произвольное смещение недоступно: бот отдаёт платежи страницами по "
            f"{limit} записей"
        )

    params: dict[str, Any] = {
        "page": offset // limit + 1,
        "per_page": limit,
        # Их пресет по умолчанию — сутки; наш экран показывает всё, что есть.
        "period": "all",
        "status_filter": "all",
    }

    status = (ctx.query.get("status") or "").upper()
    if status:
        if status not in _PAYMENT_STATUS:
            raise NotAvailable(f"Статус «{status}» этот бот не различает")
        params["status_filter"] = _PAYMENT_STATUS[status]

    gateway = (ctx.query.get("gateway") or "").strip().lower()
    if gateway:
        known = await _payment_methods(ctx)
        # Список методов не получили — не притворяемся, что отфильтровали.
        if known and gateway not in known:
            raise NotAvailable(f"Шлюз «{gateway}» у этого бота не подключён")
        params["method_filter"] = gateway

    # Даты приходят днями (YYYY-MM-DD), у них — момент времени. Верхнюю границу
    # растягиваем до конца суток: иначе «по 5 июля» отрезает весь пятый день.
    date_from = (ctx.query.get("date_from") or "").strip()
    date_to = (ctx.query.get("date_to") or "").strip()
    if date_from:
        params["date_from"] = f"{date_from}T00:00:00" if len(date_from) == 10 else date_from
    if date_to:
        params["date_to"] = f"{date_to}T23:59:59" if len(date_to) == 10 else date_to

    data = await ctx.json("/cabinet/admin/payments/search", {}, params=params) or {}
    items = []
    for p in data.get("items") or []:
        if not isinstance(p, dict):
            continue
        # Статус отдаём их же словами (кабинет незнакомые красит нейтрально) и
        # только оплаченный приводим к нашему COMPLETED — по нему в интерфейсе
        # считается «успешные».
        raw_status = str(p.get("status") or "").upper()
        items.append(
            {
                # Идентификатор счёта у шлюза — он же ключ строки и путь в карточку.
                "payment_id": p.get("identifier"),
                "user_id": p.get("user_id"),
                # Имени плательщика их поиск не отдаёт — только почту и username.
                "user_name": p.get("user_username"),
                "user_email": p.get("user_email"),
                "status": "COMPLETED" if p.get("is_paid") else raw_status,
                "gateway_type": p.get("method_display") or p.get("method") or "",
                # Что именно куплено, счёт не знает: у них это пополнение
                # кошелька, а покупка происходит уже с баланса.
                "purchase_type": "",
                "is_test": False,
                "amount": _amount(p.get("amount_kopeks")),
                "currency": "RUB",
                "plan_name": None,
                "plan_duration": None,
                "created_at": p.get("created_at"),
                "updated_at": None,
            }
        )
    return {
        "total": _int(data.get("total")),
        "limit": limit,
        "offset": offset,
        "items": items,
    }


@handler("GET", "/api/admin/transactions/{payment_id}")
async def transaction_detail(ctx: Ctx) -> dict[str, Any]:
    """Карточка платежа.

    Их карточка (`/cabinet/admin/payments/{method}/{id}`) адресуется парой
    «метод + внутренний номер», а у нас в ссылке только идентификатор счёта.
    Поэтому находим платёж их же поиском (он умеет искать по номеру счёта) —
    заодно это ровно те данные, что показывает карточка: их detail-ручка
    возвращает то же самое, что строка списка.
    """
    # Значение из пути берём сырым: оно едет их поиску параметром запроса, а
    # экранирование сделает http-клиент (ctx.param() готовит строку для URL пути).
    payment_id = str(ctx.params.get("payment_id") or "")
    data = await ctx.json(
        "/cabinet/admin/payments/search",
        {},
        params={"search": payment_id, "period": "all", "status_filter": "all", "per_page": 20},
    ) or {}
    found = None
    for p in data.get("items") or []:
        if isinstance(p, dict) and str(p.get("identifier")) == payment_id:
            found = p
            break
    if found is None:
        # Именно 404, а не «не умеем»: ручка рабочая, платежа с таким номером нет
        # (их поиск к тому же не заглядывает дальше своего предельного периода).
        raise UpstreamError(
            httpx.Response(404, json={"detail": "Платёж не найден"})
        )

    raw_status = str(found.get("status") or "").upper()
    return {
        "payment_id": found.get("identifier"),
        "status": "COMPLETED" if found.get("is_paid") else raw_status,
        "is_test": False,
        "purchase_type": "",
        "gateway_type": found.get("method") or "",
        "gateway_display_name": found.get("method_display"),
        # Способ оплаты внутри шлюза (карта/СБП) они не сохраняют.
        "payment_method": None,
        "currency": "RUB",
        # Скидок и «цены до скидки» у счёта нет — только итоговая сумма.
        "pricing": {"final_amount": _amount(found.get("amount_kopeks"))},
        "plan_snapshot": None,
        "user": {
            "id": found.get("user_id"),
            "name": found.get("user_username"),
            "email": found.get("user_email"),
            "username": found.get("user_username"),
        },
    }
