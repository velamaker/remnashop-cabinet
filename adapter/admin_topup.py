"""Пополнение баланса в админке поверх «Бедолаги»: пульт к ЕЁ настройкам сумм.

ЗАЧЕМ ОТДЕЛЬНЫЙ МОДУЛЬ. Раздел «Шлюзы» (`admin_money.py`) — про ПОДКЛЮЧЕНИЕ
платёжек: список провайдеров, их включение и ключи. Здесь — про ДЕНЬГИ В ФОРМЕ
пополнения: сколько человеку можно внести и какими кнопками. Это разные ручки
бота и разные права, и мешать их в одном файле незачем. Ни одного пути
`admin_money.py` этот модуль не объявляет — перед объявлением каждый проверен по
`HANDLERS` (наши модули грузятся последними и молча перехватили бы чужой слот).

ГЛАВНОЕ РЕШЕНИЕ: НАШ ЭКРАН — ПУЛЬТ, А НЕ ВТОРАЯ НАСТРОЙКА. У «Бедолаги»
условия пополнения ЕСТЬ, просто лежат они не там, где у нас: не одним блоком на
весь сервис, а СВОИ У КАЖДОГО способа оплаты (`payment_method_configs`:
`min_amount_kopeks`, `max_amount_kopeks`, `quick_amounts`). Своей копии этих
условий адаптер не заводит — он показывает и правит их же значения через
`PUT /cabinet/admin/payment-methods/{method_id}`. Иначе получилось бы два места
с разными числами, и покупатель платил бы по тем, что в боте.

ЧТО У НИХ РЕАЛЬНО НАСТРАИВАЕТСЯ (сверено по их openapi и коду):
  • быстрые суммы — `quick_amounts` у каждого способа. Три состояния, и путать
    их нельзя: `null` — «кнопки по умолчанию бота» (100/300/500/1000 ₽),
    список — свои кнопки, ПУСТОЙ список — «кнопок нет вовсе» (так и задумано,
    см. их `normalize_quick_amounts`). Сброс к умолчанию у них делается не
    пустым списком, а отдельным флагом `reset_quick_amounts`;
  • минимум и максимум — `min_amount_kopeks`/`max_amount_kopeks` у каждого
    способа, `null` = умолчание этого способа (`default_min/max_amount_kopeks`,
    у них считается из переменных окружения бота). Сброс — `reset_min_amount`/
    `reset_max_amount`;
  • включение отдельного способа — `is_enabled` есть, но правит его наш раздел
    «Шлюзы» (`admin_money.gateway_toggle`), и второй такой же выключатель тут
    был бы ровно тем дублированием, которого делать нельзя. Поэтому состояние
    показываем, а переключатель отдаём кабинету в `locked` с причиной.

ЧЕГО У НИХ НЕТ — И ПОЧЕМУ ЭТО ЧЕСТНЫЙ `supported: false`, А НЕ ЗАГЛУШКА:
  • БОНУС ЗА ПОПОЛНЕНИЕ. На баланс зачисляется ровно уплаченное. Единственный
    «бонус» в их настройках — `REFERRAL_FIRST_TOPUP_BONUS_KOPEKS`, награда
    ПРИГЛАСИВШЕМУ за первое пополнение приглашённого; к проценту на зачисление
    он отношения не имеет и живёт на экране рефералки.
  • КОМИССИЯ ЗА ПОПОЛНЕНИЕ. Ни общей, ни пошлюзовой у них нет: сколько человек
    заплатил, столько и пришло. Похожая на комиссию настройка ровно одна —
    `HELEKET_MARKUP_PERCENT`, курсовая надбавка КРИПТО-шлюза: она меняет сумму
    крипто-счёта, а не рублёвое зачисление, и лежит в настройках самого шлюза
    (раздел «Шлюзы»). Рисовать из неё «комиссию пополнения» значило бы выдумать
    настройку, которой нет.
  • ОБЩИЙ ВЫКЛЮЧАТЕЛЬ ПОПОЛНЕНИЯ и ОБЩИЕ минимум/максимум/быстрые суммы. Их
    сводка (`admin_money.topup_settings`) считает эти числа по способам, и они
    настоящие — но записать в них одно значение некуда: запись стёрла бы
    настройку каждого способа. Поэтому сводная карточка кабинета приезжает с
    картой `summary.locked`: значения видно, кнопки «Сохранить» нет.

ЧЕГО ЗДЕСЬ НЕТ НАМЕРЕННО. У их способа оплаты есть ещё подметоды (Карта/СБП
внутри ЮKassa), фильтры аудитории (`user_type_filter`, `first_topup_filter`,
промо-группы), имя и описание кнопки, порядок. Это не «суммы и правила
пополнения», а витрина и адресность способа — у нашего экрана таких полей нет,
и придумывать их за владельца в этой волне не нужно. Ручка бота одна и та же,
так что добавить их потом можно, ничего не ломая.

ДОГОВОР С КАБИНЕТОМ. Всё новое — НЕОБЯЗАТЕЛЬНОЕ. Наш собственный бэкенд
(`admin_src/src/web/endpoints/admin/topup.py`) этих путей не знает и ответит на
них 404; кабинет в этом случае показывает раздел ровно как раньше. Карты
`supported`/`locked` — тот же договор, что в `admin_system.py`: `supported:
false` кабинет не рисует вовсе, `locked` рисует выключенным с причиной.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

import httpx

from compose import HANDLERS, Ctx, UpstreamError, handler

# --- мелочи -----------------------------------------------------------------


def _bad_request(message: str) -> UpstreamError:
    """Ошибка В ЗАПРОСЕ, а не «не умеем». 501 админ читает как «не переведено»
    и перестаёт пытаться там, где всё работает."""
    return UpstreamError(httpx.Response(400, json={"detail": message}))


def _not_found(message: str) -> UpstreamError:
    return UpstreamError(httpx.Response(404, json={"detail": message}))


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# Их суммы — всегда копейки рубля (`amount_kopeks` во всех схемах, выбора валюты
# в API нет). Кабинет показывает рубли, поэтому граница «копейки ↔ рубли»
# проходит ровно здесь, в двух функциях ниже, и больше нигде.
def _rub(kopeks: int) -> float | int:
    """Копейки → рубли. Целое остаётся целым: «100», а не «100.0».

    Дробные суммы у них законны — их собственная админка бота прямым текстом
    предлагает «Дробные суммы — через точку» (handlers/admin/quick_amounts.py),
    и округлять чужие 123.45 ₽ до 123 значило бы молча испортить настройку.
    """
    return kopeks // 100 if kopeks % 100 == 0 else round(kopeks / 100, 2)


def _kopeks(value: Any, field: str) -> int:
    """Рубли из кабинета → копейки. Через Decimal, а не float: 123.45 * 100 в
    двоичной дробной арифметике даёт 12344.999…, и «сохранил 123.45» превратилось
    бы в 123.44 ₽."""
    try:
        amount = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, AttributeError, TypeError):
        raise _bad_request(f"{field}: нужна сумма в рублях") from None
    if not amount.is_finite():
        raise _bad_request(f"{field}: нужна сумма в рублях")
    kopeks = int((amount * 100).to_integral_value())
    if kopeks != amount * 100:
        raise _bad_request(f"{field}: точнее копейки сумма не бывает")
    return kopeks


# Их собственные пределы — не наши выдумки, а то, что откажет на их стороне:
#   • быстрых сумм не больше 10 и каждая не дороже 1 000 000 ₽
#     (`MAX_QUICK_AMOUNTS`, `MAX_QUICK_AMOUNT_KOPEKS` в их
#     payment_method_config_service);
#   • сумма пополнения из кабинета — от 10 ₽ до 20 000 000 ₽ (`TopUpRequest`:
#     ge=1000, le=2 000 000 000). Это предел ЗАПРОСА, а не настройки способа:
#     минимум ниже 10 ₽ настроить можно, но в кабинете он всё равно не сработает
#     — про такое мы предупреждаем подсказкой, а не запретом (в боте свои формы).
_MAX_PRESETS = 10
_MAX_PRESET_KOPEKS = 100_000_000
_CABINET_FLOOR_KOPEKS = 1000
_CABINET_CEIL_KOPEKS = 2_000_000_000

# Звёзды. Их же `POST /cabinet/balance/topup` отвечает на этот метод 400
# «Telegram Stars payments are only available through the bot» — счёт на звёзды
# выставляет только бот. Способ при этом настоящий и настраивается, поэтому из
# списка его НЕ убираем (это его настройки, и владелец правит их для бота), а
# подписываем, где он работает.
_STARS_METHOD = "telegram_stars"


# --- чтение их настроек ------------------------------------------------------


async def _methods(ctx: Ctx) -> list[dict[str, Any]]:
    """Способы оплаты «Бедолаги» — их АДМИНСКИМ списком.

    Строго: их ошибка (403 без права `payment_methods:read`, 502) доезжает до
    кабинета как ошибка. Пустой список читался бы как «способов нет», и владелец
    пошёл бы искать пропавшие платежи вместо того, чтобы увидеть отказ прав.

    Пользовательский список (`/cabinet/balance/methods`) для этого экрана не
    годится в принципе: он отфильтрован под конкретного человека (тип учётки,
    первое пополнение, промо-группа) и описывал бы личную выдачу админа, а не
    установку.
    """
    rows = await ctx.json("/cabinet/admin/payment-methods", [])
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


async def _method(ctx: Ctx, method_id: str) -> dict[str, Any]:
    """Их запись способа по имени из пути. Заодно это и проверка прав на чтение.

    Имя из пути НИКОГДА не уходит к ним «как есть»: сперва ищем его в их же
    списке. Иначе кабинет мог бы адресовать запись куда угодно внутри их API.
    """
    for row in await _methods(ctx):
        if str(row.get("method_id") or "") == method_id:
            return row
    raise _not_found("Способ оплаты не найден")


def _limits(method: dict[str, Any]) -> tuple[int, int, bool, bool]:
    """Действующие границы способа и признак «задано вручную».

    `null` у них означает не «ноль», а «умолчание этого способа», и умолчание
    приезжает соседним полем. Отличать это важно для экрана: пустое поле с
    подсказкой-умолчанием честнее, чем цифра, которую владелец примет за свою.
    """
    low, high = method.get("min_amount_kopeks"), method.get("max_amount_kopeks")
    return (
        _int(low) if low is not None else _int(method.get("default_min_amount_kopeks")),
        _int(high) if high is not None else _int(method.get("default_max_amount_kopeks")),
        low is not None,
        high is not None,
    )


def _presets(method: dict[str, Any]) -> tuple[list[int], list[int], bool]:
    """Быстрые суммы способа: действующие, умолчание бота и «задано вручную».

    Три состояния их `quick_amounts` (см. шапку) сводим к паре «список +
    признак»: пустой список с признаком True — это «кнопок нет», и подменять его
    умолчанием нельзя, иначе выключенные кнопки сами бы включились.
    """
    default = [_int(v) for v in (method.get("default_quick_amounts") or [])]
    own = method.get("quick_amounts")
    if own is None:
        return default, default, False
    return [_int(v) for v in own if v is not None], default, True


def _hints(method: dict[str, Any]) -> list[str]:
    """Предупреждения по способу: то, что бот сделает молча, а владелец не ждёт.

    Не запреты: всё перечисленное — законные состояния их настройки. Но каждое
    из них означает «настроил одно, покупатель увидит другое», и промолчать про
    это хуже, чем сказать.
    """
    low, high, _, _ = _limits(method)
    presets, _default, _custom = _presets(method)
    in_cabinet = str(method.get("method_id") or "") != _STARS_METHOD
    hints: list[str] = []

    if not in_cabinet:
        hints.append(
            "Счёт на звёзды выставляет только сам бот — в форме пополнения кабинета "
            "этот способ не показывается. Настройки ниже действуют в боте."
        )
    if not method.get("is_provider_configured"):
        hints.append(
            "Провайдер не настроен: пока не заполнены его ключи, бот этот способ "
            "покупателю не покажет. Ключи — в разделе «Шлюзы»."
        )
    # Про предел САМОГО КАБИНЕТА говорим только тем способам, которые в кабинете
    # вообще бывают: у звёзд минимум по умолчанию 1 ₽, и подпись «в кабинете
    # всё равно 10 ₽» рядом с «этого способа в кабинете нет» — противоречие,
    # после которого владелец перестаёт верить обеим.
    if in_cabinet and low < _CABINET_FLOOR_KOPEKS:
        hints.append(
            f"В кабинете минимум пополнения всё равно {_rub(_CABINET_FLOOR_KOPEKS)} ₽ — "
            "так устроен сам запрос пополнения «Бедолаги». В боте минимум ниже работает."
        )
    if in_cabinet and high > _CABINET_CEIL_KOPEKS:
        hints.append(
            f"В кабинете максимум пополнения всё равно {_rub(_CABINET_CEIL_KOPEKS)} ₽ — "
            "так устроен сам запрос пополнения «Бедолаги»."
        )
    # Кнопка вне диапазона — это кнопка, которой не будет: их
    # `get_effective_quick_amounts` отсекает такие молча.
    outside = [p for p in presets if p < low or p > high]
    if outside:
        shown = ", ".join(f"{_rub(p)} ₽" for p in outside)
        hints.append(
            f"Быстрые суммы вне диапазона бот не показывает: {shown}. "
            "Либо уберите их, либо расширьте минимум/максимум."
        )
    return hints


def _item(method: dict[str, Any]) -> dict[str, Any]:
    """Их способ оплаты → строка нашего экрана «Пополнение»."""
    low, high, low_custom, high_custom = _limits(method)
    presets, presets_default, presets_custom = _presets(method)
    return {
        "method_id": str(method.get("method_id") or ""),
        # Имя показываем то же, что видит покупатель: переопределение из их
        # админки, иначе их же имя по умолчанию.
        "name": (
            method.get("display_name")
            or method.get("default_display_name")
            or str(method.get("method_id") or "")
        ),
        "is_active": bool(method.get("is_enabled")),
        "is_configured": bool(method.get("is_provider_configured")),
        "order_index": _int(method.get("sort_order")),
        "min_amount": _rub(low),
        "max_amount": _rub(high),
        "min_default": _rub(_int(method.get("default_min_amount_kopeks"))),
        "max_default": _rub(_int(method.get("default_max_amount_kopeks"))),
        "min_custom": low_custom,
        "max_custom": high_custom,
        "presets": [_rub(p) for p in presets],
        "presets_default": [_rub(p) for p in presets_default],
        "presets_custom": presets_custom,
        "hints": _hints(method),
    }


# --- что на этом бэкенде применимо (карты `supported` и `locked`) ------------
#
# Договор ровно как в admin_system.py: `supported: false` кабинет не рисует
# вовсе (поле, которое на сохранении отвечает отказом, — ловушка), `locked`
# рисует выключенным и подписывает причиной (значение настоящее и на работу
# сервиса влияет, спрятать его было бы враньём).

_SUMMARY_SUPPORTED = {
    "enabled": True,
    "min_amount": True,
    "max_amount": True,
    # Бонуса за пополнение и общего списка быстрых сумм у «Бедолаги» нет вовсе —
    # см. шапку модуля. Ноль и пустой список в сводке — не значения, а отсутствие
    # настройки, и показывать их полем ввода нельзя.
    "bonus_percent": False,
    "presets": False,
}

_SUMMARY_LOCKED = {
    "enabled": (
        "Отдельного выключателя пополнения у бота нет: оно работает, пока включён "
        "хотя бы один готовый способ оплаты"
    ),
    "min_amount": "Считается по способам оплаты ниже — общего минимума у бота нет",
    "max_amount": "Считается по способам оплаты ниже — общего максимума у бота нет",
}

_METHODS_SUPPORTED = {
    "limits": True,
    "presets": True,
    "method_enabled": True,
    # Комиссии за пополнение у бота нет, бонуса за пополнение тоже (шапка модуля).
    "commission": False,
    "bonus": False,
}

_METHODS_LOCKED = {
    "method_enabled": "Включается в разделе «Шлюзы» — там же ключи провайдера",
}

_NOTE = (
    "Условия пополнения у этого бота свои у КАЖДОГО способа оплаты: общего минимума, "
    "общих быстрых сумм, бонуса и комиссии за пополнение у него нет — на баланс "
    "зачисляется ровно уплаченное. Включение способов и ключи провайдеров — в разделе «Шлюзы»."
)


# --- ручки ------------------------------------------------------------------
#
# ⚠️ Слоты проверены перед объявлением: у admin_money.py заняты
# ("GET", "/api/admin/topup") и ветка "/api/admin/gateways/*", здесь объявляются
# только пути ниже — пересечений нет. Проверка оставлена в коде, а не в памяти:
# модули грузятся последними и перехват чужого пути проходит молча.
# Отказ — ImportError, а не что попало: compose ловит именно его и продолжает
# без модуля (раздел просто не появится, в лог уйдёт причина). Любое другое
# исключение здесь уронило бы адаптер целиком — из-за раздела «Пополнение».
for _slot in (("GET", "/api/admin/topup/methods"), ("PUT", "/api/admin/topup/methods/{id}")):
    if _slot in HANDLERS:  # pragma: no cover — страховка от чужого слота
        raise ImportError(f"путь {_slot[0]} {_slot[1]} уже занят другим модулем")


@handler("GET", "/api/admin/topup/methods")
async def topup_methods(ctx: Ctx) -> dict[str, Any]:
    """Правила пополнения по способам оплаты + что на этом боте применимо.

    Порядок — их: список приходит отсортированным их же `sort_order`, и в этом
    порядке кнопки видит покупатель. Перетасовывать его нельзя, иначе владелец
    правит не тот способ, который ищет глазами.

    Отдаём ВСЕ способы, включая выключенные и ненастроенные: суммы и кнопки
    задают заранее, до включения шлюза, а список «только включённых» прятал бы
    настройку ровно тогда, когда она нужна. Состояние каждого при этом видно
    (`is_active`, `is_configured`), и кабинет показывает выключенные отдельно.
    """
    return {
        "items": [_item(m) for m in await _methods(ctx)],
        # Пределы самой «Бедолаги» — чтобы кабинет отказывал ДО запроса, её же
        # словами, а не показывал потом их pydantic-простыню на английском.
        "limits": {
            "presets_max_count": _MAX_PRESETS,
            "preset_max_amount": _rub(_MAX_PRESET_KOPEKS),
            "cabinet_min_amount": _rub(_CABINET_FLOOR_KOPEKS),
            "cabinet_max_amount": _rub(_CABINET_CEIL_KOPEKS),
        },
        "note": _NOTE,
        "summary": {"supported": _SUMMARY_SUPPORTED, "locked": _SUMMARY_LOCKED},
        "methods": {"supported": _METHODS_SUPPORTED, "locked": _METHODS_LOCKED},
    }


def _wanted_limit(payload: dict[str, Any], key: str, field: str) -> tuple[bool, int | None]:
    """Поле суммы из тела: (прислали ли, копейки или None-«вернуть умолчание»).

    Отсутствие ключа и `null` — РАЗНЫЕ вещи, и в этом вся тонкость. Их запрос
    трактует `null` как «не менять», а «вернуть умолчание» у них делается
    отдельным флагом `reset_*`. Кабинету такая пара неудобна, поэтому договор
    у нас человеческий: ключа нет — не трогаем, `null` — сбрасываем к умолчанию
    бота, число — ставим своё.
    """
    if key not in payload:
        return False, None
    value = payload[key]
    if value is None or (isinstance(value, str) and not value.strip()):
        return True, None
    kopeks = _kopeks(value, field)
    if kopeks < 0:
        raise _bad_request(f"{field}: сумма не может быть отрицательной")
    return True, kopeks


def _wanted_presets(payload: dict[str, Any]) -> tuple[bool, list[int] | None]:
    """Быстрые суммы из тела: (прислали ли, список копеек или None-«умолчание»).

    Пустой список — законное «кнопок нет», а не «сбросить»: сброс приезжает
    отдельно, значением `null`. Спутать их значит выключить кнопки там, где
    просили вернуть умолчание (или наоборот).
    """
    if "presets" not in payload:
        return False, None
    value = payload["presets"]
    if value is None:
        return True, None
    if not isinstance(value, list):
        raise _bad_request("Быстрые суммы: нужен список сумм в рублях")
    amounts: list[int] = []
    for raw in value:
        kopeks = _kopeks(raw, "Быстрые суммы")
        if kopeks <= 0:
            raise _bad_request("Быстрые суммы: сумма должна быть больше нуля")
        if kopeks > _MAX_PRESET_KOPEKS:
            raise _bad_request(
                f"Быстрые суммы: бот не принимает кнопку дороже {_rub(_MAX_PRESET_KOPEKS)} ₽"
            )
        if kopeks not in amounts:
            amounts.append(kopeks)
    if len(amounts) > _MAX_PRESETS:
        raise _bad_request(f"Быстрые суммы: бот принимает не больше {_MAX_PRESETS} кнопок")
    # Их `normalize_quick_amounts` всё равно вернёт отсортированный набор —
    # сортируем сами, чтобы сверка «сохранилось ли» ниже сравнивала одинаковое.
    return True, sorted(amounts)


@handler("PUT", "/api/admin/topup/methods/{id}")
async def topup_method_update(ctx: Ctx) -> dict[str, Any]:
    """Сохранить правила пополнения одного способа: минимум, максимум, кнопки.

    Пишем В ИХ настройку, своей копии не заводим — иначе экран показывал бы одно,
    а покупатель платил по другому.

    Чего здесь намеренно НЕТ: включения способа (`is_enabled`). Им заведует
    раздел «Шлюзы», и вторая кнопка того же действия — это дублирование чужой
    функции. Если тело всё-таки принесёт такое поле, честно отказываем, а не
    делаем вид, что сохранили.
    """
    method_id = str(ctx.params.get("id") or "")
    method = await _method(ctx, method_id)
    payload = ctx.payload()

    for forbidden, where in (
        ("is_active", "«Шлюзы»"),
        ("is_enabled", "«Шлюзы»"),
        ("bonus_percent", "нигде: бонуса за пополнение у этого бота нет"),
        ("commission", "нигде: комиссии за пополнение у этого бота нет"),
    ):
        if forbidden in payload:
            raise _bad_request(f"Это поле отсюда не меняется — {where}")

    has_min, min_kopeks = _wanted_limit(payload, "min_amount", "Минимум")
    has_max, max_kopeks = _wanted_limit(payload, "max_amount", "Максимум")
    has_presets, presets = _wanted_presets(payload)
    if not (has_min or has_max or has_presets):
        raise _bad_request("Менять нечего: в запросе нет ни лимитов, ни быстрых сумм")

    # Итоговый диапазон считаем ПОСЛЕ подстановки умолчаний: половина запроса
    # может оставить старое значение или вернуть умолчание бота, и проверять
    # надо то, что получится, а не то, что прислали.
    low_now, high_now, _, _ = _limits(method)
    default_min = _int(method.get("default_min_amount_kopeks"))
    default_max = _int(method.get("default_max_amount_kopeks"))
    low = (min_kopeks if min_kopeks is not None else default_min) if has_min else low_now
    high = (max_kopeks if max_kopeks is not None else default_max) if has_max else high_now
    if low > high:
        raise _bad_request(
            f"Минимум {_rub(low)} ₽ больше максимума {_rub(high)} ₽ — так способ не заработает"
        )

    # Кнопка вне диапазона — кнопка, которой покупатель не увидит: их
    # `get_effective_quick_amounts` отсекает такие МОЛЧА. Пропустить это значит
    # сказать «сохранено» про настройку, которой не будет.
    if has_presets and presets:
        outside = [p for p in presets if p < low or p > high]
        if outside:
            shown = ", ".join(f"{_rub(p)} ₽" for p in outside)
            raise _bad_request(
                f"Бот не покажет быстрые суммы вне диапазона {_rub(low)}–{_rub(high)} ₽: {shown}"
            )

    # Их тело: «вернуть умолчание» — это отдельный флаг, а не null (null у них
    # значит «не менять»). Ровно поэтому договор наверху и переводится здесь.
    body: dict[str, Any] = {}
    if has_min:
        if min_kopeks is None:
            body["reset_min_amount"] = True
        else:
            body["min_amount_kopeks"] = min_kopeks
    if has_max:
        if max_kopeks is None:
            body["reset_max_amount"] = True
        else:
            body["max_amount_kopeks"] = max_kopeks
    if has_presets:
        if presets is None:
            body["reset_quick_amounts"] = True
        else:
            body["quick_amounts"] = presets

    updated = await ctx.json(
        # Экранируем: имя способа приехало из их же ответа, но «/» внутри увёл бы
        # запрос по чужому адресу их API.
        f"/cabinet/admin/payment-methods/{quote(method_id, safe='')}", {},
        method="PUT", json=body,
    ) or {}

    # Сверяем ПО ИХ ОТВЕТУ, а не по своему оптимизму: отвечать «сохранено», не
    # убедившись, что бот принял значение, — худший вид молчаливого отказа
    # (владелец уверен, что лимит поднят, а покупателю отказывают на старом).
    fresh_low, fresh_high, _, _ = _limits(updated)
    fresh_presets, _default, _custom = _presets(updated)
    mismatch = (
        (has_min and fresh_low != low)
        or (has_max and fresh_high != high)
        or (has_presets and presets is not None and fresh_presets != presets)
        or (has_presets and presets is None and updated.get("quick_amounts") is not None)
    )
    if mismatch:
        raise UpstreamError(
            httpx.Response(502, json={"detail": "Бот не применил настройку — попробуйте ещё раз"})
        )
    return _item(updated)
