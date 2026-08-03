"""Каталог админки поверх «Бедолаги»: тарифы с ценами и промокоды.

Зачем отдельный модуль. Админских ручек в нашем контракте под сорок штук, и
держать их в одном файле нельзя: адаптер пишется несколькими руками сразу, а
один файл на всех — это гарантированные конфликты. `admin.py` отвечает за людей
и деньги (пользователи, платежи, сводка), здесь — витрина: тарифы, цены по
периодам и промокоды.

ОТКУДА БЕРЁМ ДАННЫЕ. Только их кабинетное API (`/cabinet/admin/tariffs*`,
`/cabinet/admin/promocodes*`) и только по сессии САМОГО админа — `ctx.call` /
`ctx.json` подставляют его Bearer, а решение «пускать или нет» принимает их RBAC
(`tariffs:read/edit/create/delete`, `promocodes:*`). Служебный токен адаптера
(`ctx.admin`, X-API-Key) здесь не используется ни разу и не должен: он
принадлежит адаптеру, а не человеку, и сходи мы за тарифами с ним — цены смог бы
править ЛЮБОЙ вошедший в кабинет. Цена — это деньги владельца, тут цена ошибки
выше, чем у пустой колонки.

ЕДИНИЦЫ (главная засада перевода).
  • Цена. У них `price_kopeks` — целые КОПЕЙКИ. У нас `price` — РУБЛИ СТРОКОЙ
    («349.00»), кабинет прогоняет её через Number(). Туда и обратно считаем
    Decimal'ом, а не float: `0.1 + 0.2` в float — это 30 копеек мимо кассы.
  • Трафик. У них `traffic_limit_gb`, у нас в тарифах `traffic_limit` — тоже
    ГИГАБАЙТЫ (cabinet/src/pages/admin/AdminPlansPage.tsx:27), а байты только в
    подписке. Конвертировать нечего, но перепутать легко: 1000 «байт» вместо
    1000 ГБ — это тариф, который кончится на первой картинке.
  • 0 по трафику = «безлимит» в обеих системах. А вот 0 устройств у них
    невозможен вовсе (минимум одно), и наш «безлимит по устройствам» тут честно
    отбивается 501, а не превращается тихо в единицу.

ЧЕГО ЗДЕСЬ НЕТ И ПОЧЕМУ. У их тарифа нет ни тега, ни публичного кода, ни списков
«кому можно» — доступ они режут промо-группами. Такие поля отдаются пустыми, а
если админ ПОПЫТАЛСЯ их заполнить, сохранение честно отвечает 501 с причиной:
молча проглотить правку хуже, чем отказать — админ уверен, что сохранил.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

import httpx

from compose import Ctx, NotAvailable, UpstreamError, handler

# --- мелочи -----------------------------------------------------------------


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bad_request(message: str) -> UpstreamError:
    """Ошибка В ЗАПРОСЕ КАБИНЕТА (а не «не умеем») — 400 с русским текстом.

    501 (`NotAvailable`) значит «у бота такого нет», и вешать его на кривую цену
    неправильно: админ решит, что функция не переведена, и перестанет пытаться.
    """
    return UpstreamError(httpx.Response(400, json={"detail": message}))


def _not_found(message: str) -> UpstreamError:
    return UpstreamError(httpx.Response(404, json={"detail": message}))


def _rub(kopeks: Any) -> str:
    """Копейки → рубли строкой, как ждёт контракт (`price: string`)."""
    return f"{_int(kopeks) / 100:.2f}"


def _kopeks(price: Any, where: str) -> int:
    """Рубли из формы → копейки. Отрицательных и мусора быть не должно.

    Decimal, а не float: `int(float('349.99') * 100)` даёт 34998 — рубль в
    минус на каждой сотне продаж, и найти такое потом нечем.
    """
    text = str(price).strip().replace(",", ".").replace(" ", "")
    if not text:
        raise _bad_request(f"{where}: цена не заполнена")
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise _bad_request(f"{where}: цена «{price}» — не число") from None
    if value < 0:
        raise _bad_request(f"{where}: цена не может быть отрицательной")
    return int((value * 100).to_integral_value(rounding=ROUND_HALF_UP))


# --- тарифы: перевод их формы в нашу ----------------------------------------

# Наш «тип тарифа» (PlanType) их тариф не хранит вовсе — у него просто пара
# лимитов. Правило вывода то же, что у витрины покупки (compose._plan_type);
# повторено здесь намеренно, чтобы модуль не зависел от приватного имени
# соседнего файла, который правят параллельно.
def _plan_type(traffic_gb: Any, device_limit: Any) -> str:
    traffic, devices = bool(_int(traffic_gb)), bool(_int(device_limit))
    if traffic and devices:
        return "BOTH"
    if traffic:
        return "TRAFFIC"
    if devices:
        return "DEVICES"
    return "UNLIMITED"


# Режим сброса трафика: у них DAY/WEEK/MONTH/MONTH_ROLLING/NO_RESET, у нас
# DAILY_RESET/WEEKLY_RESET/MONTHLY_RESET/NO_RESET. `None` у них — «как в
# глобальной настройке бота», отдельного значения под это у нас нет.
_RESET_TO_OURS = {
    "DAY": "DAILY_RESET",
    "WEEK": "WEEKLY_RESET",
    "MONTH": "MONTHLY_RESET",
    "NO_RESET": "NO_RESET",
}
_RESET_TO_THEIRS = {v: k for k, v in _RESET_TO_OURS.items()}
# «Скользящий месяц» пары у нас не имеет. Значение проходит насквозь: селект в
# кабинете его не покажет, зато сохранение НЕ подменит режим на другой.
_THEIR_RESET_MODES = set(_RESET_TO_OURS) | {"MONTH_ROLLING"}


def _reset_ours(mode: Any) -> str:
    """Их режим → наш. Пусто = «глобальная настройка бота», её мы не выдумываем."""
    if not mode:
        return ""
    text = str(mode).upper()
    return _RESET_TO_OURS.get(text, text)


def _durations(period_prices: Any) -> list[dict[str, Any]]:
    """Их `period_prices` → наши `durations` с ценой строкой.

    Валюта одна — рубли: цена периода у них лежит одним числом в копейках, а
    звёзды/доллары в тарифе не хранятся вовсе (шлюз влияет только на пополнение
    баланса). Поэтому один ценник на период, а не пустой список валют.
    """
    rows: list[tuple[int, int]] = []
    for item in period_prices or []:
        if not isinstance(item, dict):
            continue
        days = _int(item.get("days"))
        if days <= 0:
            continue
        rows.append((days, _int(item.get("price_kopeks"))))
    rows.sort()
    return [
        {
            "days": days,
            # У них периоды хранятся словарём «дни → цена», своего порядка нет:
            # нумеруем по возрастанию срока, как их же список.
            "order_index": index,
            "prices": [{"currency": "RUB", "price": _rub(kopeks)}],
        }
        for index, (days, kopeks) in enumerate(rows)
    ]


def _availability(tariff: dict[str, Any]) -> str:
    """Кому доступен тариф.

    Аудиториями (новые/старые/приглашённые) их бот тарифы не режет вовсе —
    ограничение у него одно: промо-группы. Поэтому «ALL» отдаём только когда
    тариф действительно открыт всем; как только он привязан к промо-группе,
    поле остаётся ПУСТЫМ — это «мы такого не выражаем», а не «для всех».
    """
    groups = tariff.get("promo_groups")
    if isinstance(groups, list) and any(
        isinstance(g, dict) and g.get("is_selected") for g in groups
    ):
        return ""
    return "ALL"


def _plan(tariff: dict[str, Any]) -> dict[str, Any]:
    """Их карточка тарифа → наш `AdminPlan` (cabinet/src/api/admin.ts:569)."""
    traffic = _int(tariff.get("traffic_limit_gb"))
    devices = _int(tariff.get("device_limit"))
    return {
        "id": _int(tariff.get("id")),
        # Своего кода у их тарифа нет — только числовой id (витрина отдаёт его
        # строкой как public_code, но в админке подменять поле нечем: админ
        # решит, что код можно поменять).
        "public_code": None,
        "name": tariff.get("name") or "",
        "description": tariff.get("description"),
        # Тега (значка «Хит»/«Выгодно») у их тарифа нет.
        "tag": None,
        "type": _plan_type(traffic, devices),
        "availability": _availability(tariff),
        "traffic_limit_strategy": _reset_ours(tariff.get("traffic_reset_mode")),
        # ГИГАБАЙТЫ и там, и тут (см. шапку модуля).
        "traffic_limit": traffic,
        "device_limit": devices,
        # Персональных списков доступа у их тарифа нет — пустые, а не выдуманные.
        "allowed_telegram_ids": [],
        "allowed_emails": [],
        "internal_squads": [str(s) for s in (tariff.get("allowed_squads") or [])],
        "external_squad": tariff.get("external_squad_uuid"),
        "order_index": _int(tariff.get("display_order")),
        "is_active": bool(tariff.get("is_active")),
        # У них «пробным» может быть только один тариф разом — это их правило,
        # и включение через нашу форму его соблюдает (см. `_set_trial`).
        "is_trial": bool(tariff.get("is_trial_available")),
        "durations": _durations(tariff.get("period_prices")),
        "created_at": tariff.get("created_at"),
    }


# Их список тарифов цен НЕ отдаёт — они есть только в карточке, поэтому за
# ценами приходится сходить в карточку каждого тарифа. Идём пачками: разом
# открывать сотню соединений к чужому боту невежливо, а по одному — долго.
_DETAIL_BATCH = 6


async def _detail(ctx: Ctx, plan_id: int) -> dict[str, Any]:
    data = await ctx.json(f"/cabinet/admin/tariffs/{plan_id}", {})
    if not isinstance(data, dict):
        raise _not_found("Тариф не найден")
    return data


async def _details(ctx: Ctx, ids: list[int]) -> list[dict[str, Any]]:
    """Карточки всех тарифов. Любая ошибка — ошибка всего списка, СПЕЦИАЛЬНО.

    Мягкая деградация здесь опаснее пустого экрана: тариф без загруженных цен
    приехал бы в форму с пустым списком периодов, и первое же «Сохранить»
    стёрло бы у него ВСЕ цены. Лучше честная ошибка загрузки.
    """
    out: list[dict[str, Any]] = []
    for start in range(0, len(ids), _DETAIL_BATCH):
        batch = ids[start : start + _DETAIL_BATCH]
        out.extend(await asyncio.gather(*(_detail(ctx, i) for i in batch)))
    return out


def _path_id(ctx: Ctx, what: str) -> int:
    """Номер из пути. Не число — значит такого нет: 404, а не пятисотка изнутри
    их роутера (их FastAPI ответил бы на строку простынёй pydantic-ошибки)."""
    raw = str(ctx.params.get("id") or "")
    try:
        return int(raw)
    except ValueError:
        raise _not_found(f"{what} не найден") from None


# --- тарифы: чтение ---------------------------------------------------------


@handler("GET", "/api/admin/plans")
async def plans(ctx: Ctx) -> dict[str, Any]:
    """Список тарифов с ценами по периодам."""
    data = await ctx.json(
        "/cabinet/admin/tariffs", {},
        # Выключенные тарифы админке нужны — их как раз и включают обратно.
        params={"include_inactive": True},
    ) or {}
    ids = [
        _int(t.get("id"))
        for t in (data.get("tariffs") or [])
        if isinstance(t, dict) and t.get("id") is not None
    ]
    items = [_plan(d) for d in await _details(ctx, ids)]
    # Постраничности у их списка тарифов нет вовсе (он отдаёт всё разом), и наш
    # экран её не просит — `total` считаем по факту.
    return {"items": items, "total": len(items)}


@handler("GET", "/api/admin/plans/{id}")
async def plan_get(ctx: Ctx) -> dict[str, Any]:
    return _plan(await _detail(ctx, _path_id(ctx, "Тариф")))


@handler("GET", "/api/admin/plans/meta/squads")
async def plan_squads(ctx: Ctx) -> dict[str, Any]:
    """Сквады для формы тарифа: внутренние (наши серверы) и внешние.

    Внутренние — строгий запрос: без них форма не даст выбрать серверы, и об
    этом лучше сказать. Внешние — мягкий: их отдаёт панель Remnawave, она может
    быть недоступна, и это не повод ронять форму (их же ручка в таком случае
    молча возвращает пустой список).
    """
    internal = await ctx.json("/cabinet/admin/tariffs/available-servers", []) or []
    external = await ctx.json(
        "/cabinet/admin/tariffs/available-external-squads", [], soft=True
    ) or []
    return {
        "internal": [
            {"uuid": str(s.get("squad_uuid")), "name": s.get("display_name") or ""}
            for s in internal
            if isinstance(s, dict) and s.get("squad_uuid")
        ],
        "external": [
            {"uuid": str(s.get("uuid")), "name": s.get("name") or ""}
            for s in external
            if isinstance(s, dict) and s.get("uuid")
        ],
        # Досюда доходим, только если их список серверов ответил — выбор сквадов
        # в форме работает. Пустой `external` при этом нормален: внешних сквадов
        # может не быть вовсе.
        "available": True,
    }


# --- тарифы: перевод нашей формы в их запрос --------------------------------


def _periods(durations: Any) -> list[dict[str, Any]]:
    """Наши `durations` → их `period_prices` (дни + копейки).

    Проверок больше, чем кажется нужным, и все — про деньги: их хранилище это
    словарь «дни → цена», и два периода с одним сроком там молча схлопнутся в
    один, а лишняя валюта пропадёт без следа. Такое лучше отбить на входе.
    """
    if not isinstance(durations, list):
        raise _bad_request("Периоды тарифа переданы не списком")
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in durations:
        if not isinstance(row, dict):
            raise _bad_request("Период тарифа передан не объектом")
        days = _int(row.get("days"))
        if days < 1:
            raise _bad_request("Срок периода должен быть не меньше одного дня")
        if days in seen:
            raise _bad_request(
                f"Период «{days} дней» указан дважды: у этого бота на срок приходится одна цена"
            )
        seen.add(days)

        prices = [p for p in (row.get("prices") or []) if isinstance(p, dict)]
        other = [p for p in prices if str(p.get("currency") or "RUB").upper() != "RUB"]
        if other:
            names = ", ".join(sorted({str(p.get("currency")).upper() for p in other}))
            raise NotAvailable(
                f"Цена в {names} недоступна: этот бот хранит стоимость тарифа только в рублях"
            )
        if len(prices) != 1:
            raise _bad_request(
                f"Период «{days} дней»: нужна ровно одна цена в рублях"
            )
        result.append(
            {"days": days, "price_kopeks": _kopeks(prices[0].get("price"), f"{days} дней")}
        )
    return result


def _reset_theirs(value: Any) -> str | None:
    """Наш режим сброса трафика → их. Пусто — значит «не трогаем»."""
    text = str(value or "").strip().upper()
    if not text:
        return None
    if text in _RESET_TO_THEIRS:
        return _RESET_TO_THEIRS[text]
    if text in _THEIR_RESET_MODES:
        # Их собственное значение, которое мы отдали насквозь (MONTH_ROLLING).
        return text
    raise NotAvailable(f"Режим сброса трафика «{text}» этот бот не знает")


def _plan_payload(
    body: dict[str, Any], current: dict[str, Any] | None
) -> tuple[dict[str, Any], bool | None]:
    """Форма тарифа из кабинета → тело их запроса + отдельно флаг «пробный».

    Возвращаем только те поля, которые кабинет реально прислал: тогда одна и та
    же функция обслуживает и PUT (форма целиком), и PATCH (частичная правка), и
    ничего лишнего у тарифа не перетирается.

    «Пробный» едет отдельно: их update такого поля не принимает вовсе — у них
    это переключатель `/{id}/trial`, который заодно снимает пробность со всех
    остальных тарифов.
    """
    payload: dict[str, Any] = {}
    current = current or {}

    # --- то, чего у их тарифа нет: молча игнорировать нельзя ----------------
    if str(body.get("public_code") or "").strip():
        raise NotAvailable(
            "Публичный код тарифа недоступен: у тарифов этого бота его нет, "
            "тариф адресуется номером"
        )
    if str(body.get("tag") or "").strip():
        raise NotAvailable("Тег тарифа недоступен: у тарифов этого бота такого поля нет")
    if body.get("allowed_telegram_ids") or body.get("allowed_emails"):
        raise NotAvailable(
            "Списки «кому доступен тариф» недоступны: этот бот ограничивает доступ "
            "промо-группами, а не перечнем пользователей"
        )
    availability = str(body.get("availability") or "").strip().upper()
    if availability and availability != "ALL":
        raise NotAvailable(
            "Ограничение тарифа по аудитории недоступно: этот бот делит доступ "
            "промо-группами, а не на новых/существующих/приглашённых"
        )

    # --- собственно поля ----------------------------------------------------
    if "name" in body:
        name = str(body.get("name") or "").strip()
        if not name:
            raise _bad_request("Название тарифа не заполнено")
        payload["name"] = name
    if "description" in body:
        # Пустое описание шлём пустой строкой, а не null: их update поле со
        # значением None просто пропускает, и «стёр описание» не сработало бы.
        payload["description"] = str(body.get("description") or "")
    if "traffic_limit" in body:
        traffic = _int(body.get("traffic_limit"), -1)
        if traffic < 0:
            raise _bad_request("Лимит трафика не может быть отрицательным")
        payload["traffic_limit_gb"] = traffic
    if "device_limit" in body:
        devices = _int(body.get("device_limit"), -1)
        if devices < 1:
            raise NotAvailable(
                "Безлимит по устройствам недоступен: тариф этого бота требует "
                "хотя бы одно устройство"
            )
        payload["device_limit"] = devices
    if "is_active" in body:
        payload["is_active"] = bool(body.get("is_active"))
    if "order_index" in body:
        payload["display_order"] = max(0, _int(body.get("order_index")))
    if "durations" in body:
        payload["period_prices"] = _periods(body.get("durations"))
    if "internal_squads" in body:
        squads = body.get("internal_squads") or []
        if not isinstance(squads, list):
            raise _bad_request("Серверы тарифа переданы не списком")
        payload["allowed_squads"] = [str(s) for s in squads if s]
    if "external_squad" in body or body.get("clear_external_squad"):
        external = str(body.get("external_squad") or "").strip()
        # Именно явный null: у их update внешний сквад — одно из двух полей,
        # которые читаются через model_fields_set, поэтому null тут «сбросить»,
        # а не «не менять». Пустую строку они бы отбили по маске UUID.
        cleared = bool(body.get("clear_external_squad")) or not external
        payload["external_squad_uuid"] = None if cleared else external
    if "traffic_limit_strategy" in body:
        mode = _reset_theirs(body.get("traffic_limit_strategy"))
        if mode is not None:
            payload["traffic_reset_mode"] = mode

    # `type` кабинет присылает всегда, но у их тарифа такого поля нет: тип —
    # производная от лимитов (см. `_plan_type`), и после сохранения он
    # пересчитается сам. Поэтому селект «Тип» здесь ни на что не влияет и
    # отказом не отбивается: иначе обычная правка «трафик → безлимит»
    # упиралась бы в 501 из-за не переключённого вручную селекта.

    trial = bool(body["is_trial"]) if "is_trial" in body else None
    if trial is not None and trial == bool(current.get("is_trial_available")):
        trial = None  # ничего не меняется — их переключатель не дёргаем
    return payload, trial


async def _set_trial(ctx: Ctx, plan_id: int) -> bool:
    """Переключить «пробный». У них это toggle без аргумента, поэтому дёргаем
    его только когда состояние действительно должно измениться."""
    data = await ctx.json(f"/cabinet/admin/tariffs/{plan_id}/trial", {}, method="POST") or {}
    return bool(data.get("is_trial_available"))


# --- тарифы: изменение ------------------------------------------------------


@handler("PUT", "/api/admin/plans/{id}")
@handler("PATCH", "/api/admin/plans/{id}")
async def plan_update(ctx: Ctx) -> dict[str, Any]:
    """Правка тарифа и цен.

    Сначала читаем карточку: она нужна и для сравнения «пробного» (их ручка —
    переключатель, а не установка значения), и как проверка прав на чтение до
    любых изменений.
    """
    plan_id = _path_id(ctx, "Тариф")
    current = await _detail(ctx, plan_id)
    payload, trial = _plan_payload(ctx.payload(), current)

    result = current
    if payload:
        result = await ctx.json(
            f"/cabinet/admin/tariffs/{plan_id}", {}, method="PUT", json=payload
        ) or current
    if trial is not None:
        # После основной правки: если она не прошла, пробность останется прежней.
        result = dict(result)
        result["is_trial_available"] = await _set_trial(ctx, plan_id)
    return _plan(result)


@handler("POST", "/api/admin/plans")
async def plan_create(ctx: Ctx) -> dict[str, Any]:
    """Создание тарифа."""
    body = ctx.payload()
    if not str(body.get("name") or "").strip():
        raise _bad_request("Название тарифа не заполнено")
    payload, trial = _plan_payload(body, None)
    created = await ctx.json("/cabinet/admin/tariffs", {}, method="POST", json=payload) or {}
    plan_id = _int(created.get("id"))
    if trial and plan_id:
        created = dict(created)
        created["is_trial_available"] = await _set_trial(ctx, plan_id)
    return _plan(created)


@handler("PUT", "/api/admin/plans/{id}/toggle")
@handler("POST", "/api/admin/plans/{id}/toggle")
async def plan_toggle(ctx: Ctx) -> dict[str, Any]:
    """Включить/выключить тариф. И у нас, и у них это переключатель без тела."""
    plan_id = _path_id(ctx, "Тариф")
    data = await ctx.json(
        f"/cabinet/admin/tariffs/{plan_id}/toggle", {}, method="POST"
    ) or {}
    return {"id": _int(data.get("id"), plan_id), "is_active": bool(data.get("is_active"))}


@handler("DELETE", "/api/admin/plans/{id}")
async def plan_delete(ctx: Ctx) -> dict[str, Any]:
    """Удаление тарифа.

    Их бот удаляет тариф даже с живыми подписками и сообщает, скольких это
    задело. Своей проверки не добавляем: запрещать то, что их админка
    разрешает, — это придуманное нами правило, а не перевод. Ответ кабинет не
    читает (кнопка просто перезагружает список), но число отдаём — оно попадёт
    в лог адаптера при разборе.
    """
    plan_id = _path_id(ctx, "Тариф")
    data = await ctx.json(f"/cabinet/admin/tariffs/{plan_id}", {}, method="DELETE") or {}
    return {"affected_subscriptions": _int(data.get("affected_subscriptions"))}


# --- промокоды --------------------------------------------------------------
#
# Наборы наград не совпадают, и это не мелочь: у нас шесть типов (дни, трафик,
# устройства, тариф, личная скидка, скидка на покупку), у них пять и других
# (баланс, дни подписки, пробная подписка, промо-группа, процентная скидка).
# Совпадает ровно один — «дни». Поэтому читаем всё (чужие типы едут своим
# именем, кабинет незнакомое печатает как есть), а СОЗДАЁМ только то, что
# переводится один в один: промокод — это подарок за счёт владельца, и
# «примерно похожий» тип тут недопустим.

# Их 0 «без ограничений» превращается при создании в 999999 (их же костыль для
# проверки `current_uses < max_uses`), поэтому «безлимит» узнаём по порогу.
_UNLIMITED_USES = 999999


def _promocode(row: dict[str, Any]) -> dict[str, Any]:
    """Их промокод → наш `AdminPromocode` (cabinet/src/api/admin.ts:193)."""
    kind = str(row.get("type") or "").lower()
    days = _int(row.get("subscription_days"))
    bonus = _int(row.get("balance_bonus_kopeks"))

    snapshot: dict[str, Any] | None = None
    if kind == "subscription_days":
        reward_type, reward = "DURATION", days
    elif kind == "trial_subscription":
        # Их «пробная подписка по тарифу» — ближайшее к нашему «тариф»: даёт
        # подписку выбранного тарифа на N дней. Название тарифа и срок кладём в
        # снимок, кабинет печатает его вместо числа.
        reward_type, reward = "SUBSCRIPTION", None
        snapshot = {"name": row.get("tariff_name"), "duration": days}
    elif kind == "discount":
        # У них процент лежит в поле бонуса, а срок жизни скидки — в днях (на
        # самом деле часах). Процент — это ровно наша «скидка на покупку»;
        # часы в нашей форме выразить нечем, поэтому они видны только в боте.
        reward_type, reward = "PURCHASE_DISCOUNT", bonus
    elif kind == "balance":
        # Пополнения баланса в наших типах наград нет вовсе — отдаём их имя, и
        # кабинет печатает его как есть (REWARD_LABEL[...] ?? reward_type).
        # Значение — в рублях: копейки в этой колонке читались бы как рубли.
        reward_type, reward = "BALANCE", round(bonus / 100, 2)
    else:
        # promo_group и всё, что они добавят потом: показываем имя типа, а
        # значение оставляем пустым — id промо-группы в колонке «награда»
        # выглядел бы как количество.
        reward_type, reward = kind.upper(), None

    max_uses = _int(row.get("max_uses"))
    unlimited = max_uses <= 0 or max_uses >= _UNLIMITED_USES
    return {
        "id": _int(row.get("id")),
        "code": row.get("code") or "",
        "is_active": bool(row.get("is_active")),
        "reward_type": reward_type,
        "reward": reward,
        "plan_snapshot": snapshot,
        # Аудиторию их промокод не различает вовсе (есть только «первая покупка»
        # и промо-группа — это про покупку, а не про то, кому код показывать).
        "availability": "ALL",
        "is_reusable": unlimited or max_uses > 1,
        "max_activations": None if unlimited else max_uses,
        "expires_at": row.get("valid_until"),
        "created_at": row.get("created_at"),
        "total_activations": _int(row.get("current_uses")),
    }


@handler("GET", "/api/admin/promocodes")
async def promocodes(ctx: Ctx) -> dict[str, Any]:
    """Список промокодов."""
    # Верхняя граница — их: limit>200 они отбивают 422, и лучше отдать сколько
    # можно, чем уронить экран из-за размера страницы.
    limit = max(1, min(200, _int(ctx.query.get("limit"), 25)))
    offset = max(0, _int(ctx.query.get("offset"), 0))
    data = await ctx.json(
        "/cabinet/admin/promocodes", {}, params={"limit": limit, "offset": offset}
    ) or {}
    items = [_promocode(p) for p in (data.get("items") or []) if isinstance(p, dict)]
    return {
        "total": _int(data.get("total")),
        "limit": limit,
        "offset": offset,
        "items": items,
    }


@handler("POST", "/api/admin/promocodes")
async def promocode_create(ctx: Ctx) -> dict[str, Any]:
    """Создание промокода — только для награды, которая переводится точно."""
    body = ctx.payload()
    code = str(body.get("code") or "").strip().upper()
    if not code:
        raise _bad_request("Код промокода не заполнен")

    reward_type = str(body.get("reward_type") or "").strip().upper()
    if reward_type in ("TRAFFIC", "DEVICES"):
        raise NotAvailable(
            "Промокод на трафик и устройства недоступен: этот бот такими наградами не умеет "
            "(у него баланс, дни подписки, пробная подписка и процентная скидка)"
        )
    if reward_type in ("PERSONAL_DISCOUNT", "PURCHASE_DISCOUNT"):
        raise NotAvailable(
            "Скидочный промокод недоступен: у этого бота у скидки обязателен срок жизни "
            "в часах, а форма кабинета его не передаёт"
        )
    if reward_type == "SUBSCRIPTION":
        raise NotAvailable(
            "Промокод на тариф недоступен: этот бот выдаёт по тарифу ПРОБНУЮ подписку "
            "(со всеми её ограничениями), а не оплаченную — это другая награда"
        )
    if reward_type != "DURATION":
        raise NotAvailable(f"Награда «{reward_type}» этому боту неизвестна")

    days = _int(body.get("reward"), -1)
    if days < 1:
        # Их валидация всё равно отобьёт 0, но своим текстом на английском.
        raise _bad_request("Промокод на дни: срок должен быть больше нуля")

    availability = str(body.get("availability") or "ALL").strip().upper()
    if availability != "ALL":
        raise NotAvailable(
            "Промокод для отдельной аудитории недоступен: этот бот различает только "
            "«первая покупка» и промо-группу, а не новых/существующих/приглашённых"
        )

    payload: dict[str, Any] = {
        "code": code,
        "type": "subscription_days",
        "subscription_days": days,
        "is_active": True,
    }
    # У них одно поле вместо нашей пары: 1 — одноразовый, 0 — без предела.
    if body.get("max_activations") is not None:
        payload["max_uses"] = max(0, _int(body.get("max_activations")))
    else:
        payload["max_uses"] = 0 if body.get("is_reusable") else 1
    expires = str(body.get("expires_at") or "").strip()
    if expires:
        payload["valid_until"] = expires

    return _promocode(
        await ctx.json("/cabinet/admin/promocodes", {}, method="POST", json=payload) or {}
    )


@handler("PUT", "/api/admin/promocodes/{id}/toggle")
@handler("POST", "/api/admin/promocodes/{id}/toggle")
async def promocode_toggle(ctx: Ctx) -> dict[str, Any]:
    """Включить/выключить промокод.

    У нас это отдельный путь с нужным состоянием в теле, у них — частичная
    правка. Состояние берём из тела и не вычисляем сами: кабинет присылает то,
    что видел админ, и «переключить наоборот» на устаревшем списке включило бы
    код, который кто-то только что погасил.
    """
    promo_id = _path_id(ctx, "Промокод")
    body = ctx.payload()
    if "is_active" not in body:
        raise _bad_request("Не передано состояние промокода")
    data = await ctx.json(
        f"/cabinet/admin/promocodes/{promo_id}", {},
        method="PATCH", json={"is_active": bool(body.get("is_active"))},
    ) or {}
    return _promocode(data)


@handler("DELETE", "/api/admin/promocodes/{id}")
async def promocode_delete(ctx: Ctx) -> dict[str, Any]:
    """Удаление промокода. Их ответ — 204 без тела, нам отдавать нечего."""
    promo_id = _path_id(ctx, "Промокод")
    await ctx.json(f"/cabinet/admin/promocodes/{promo_id}", {}, method="DELETE")
    return {}
