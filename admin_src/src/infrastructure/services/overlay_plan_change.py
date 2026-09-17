"""Смена тарифа без сгорания остатка: перенос по цене дня (overlay RемнаShop).

ЧТО ДЕЛАЕТ. У базы смена тарифа (CHANGE) начинает срок с нуля: остаток текущей
подписки сгорает, у бессрочной — сгорает «навсегда». Здесь остаток пересчитывается
в дни нового тарифа по цене дня:

    новый срок = момент зачисления + оплаченная длительность + бонус,
    бонус = floor(стоимость остатка ÷ цена дня нового тарифа).

КАК СЧИТАЕТСЯ СТОИМОСТЬ ОСТАТКА. Остаток — это ПОЗДНИЕ дни строки подписки, поэтому
оплаченные «слои» берутся с конца (LIFO): свежие продления, затем счёт, которым строка
создана, затем перенос, которым она была создана. Каждый слой — по реально уплаченной
цене дня (скидка при оплате не превращается в лишние дни). Дни, не покрытые слоями
(подарки, компенсации, выдачи админа), — по самой низкой витринной цене дня текущего
тарифа; тарифа нет в витрине (импорт, удалён, выключен) — такие дни честно «перенести
нельзя». Цена дня НОВОГО тарифа — витринная цена срока без личной скидки, та, что
зафиксирована в счёте.

ЧЕГО НЕ ДЕЛАЕТ. Триал, резерв, бессрочная, удалённая админом подписка, возврат денег
за последний год — без переноса (режимы ниже). Тот же тариф через CHANGE — 1:1, как
продление. Технический предел — MAX_BONUS_DAYS.

ПОЧЕМУ ТАК УСТРОЕНО. Расчёт — одна ЧИСТАЯ функция `compute_carryover` над снимком
состояния `CarryState`: её одинаково зовут зачисление (правка покупки), витрина
кабинета и окно подтверждения в боте, и её смысл заперт тестами на синтетических
ценах. Загрузка состояния и журнал — отдельные функции с SQL, стражи по тексту
запросов и тест на настоящем Postgres.

Импорты — только stdlib, sqlalchemy и loguru: модуль зовут из хука импорта модуля
покупки, и прикосновение к application-слою здесь дало бы циклический импорт.
"""

from __future__ import annotations

import json
import math
import os
import socket
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence
from uuid import UUID

from loguru import logger
from sqlalchemy import text

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
CONFIG_PATH = ASSETS_DIR / "plan_change.json"

# Установки из git получают перенос включённым: сгорание оплаченных дней для
# покупателя выглядит как ошибка магазина. Выключить — `{"enabled": false}` в файле,
# без пересборки (файл читается на каждый вызов).
DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "notify_admins": True,
}

DAY = 86400
# Технический предел бонуса — как у админского «Продлить» и оплаты с баланса.
MAX_BONUS_DAYS = 3650
# Год бессрочной подписки у базы (src.core.constants.UNLIMITED_EXPIRE_YEAR).
# Дублируем числом: импорт core отсюда тянул бы конфигурацию приложения; равенство
# запирает тест.
UNLIMITED_YEAR = 2099
# Резерв сдвигает срок строки на своё окно; «срок не дальше резерва» — с этим запасом.
RESERVE_SLACK = timedelta(hours=1)
# Окно, в котором счёт NEW/CHANGE считается создающим строку подписки.
CREATE_WINDOW_BEFORE = 120
CREATE_WINDOW_AFTER = 5

PURCHASE_MODULE = "src.application.use_cases.subscription.commands.purchase"
PROMOCODE_MODULE = "src.application.use_cases.promocode.commands.activate"

# Режимы состояния (одинаковы для любой цели) и режимы конкретной цели.
STATE_MODES = ("carry", "lifetime", "reserve", "refund", "none")
TARGET_MODES = ("carry", "same_plan", "unpriced", "none")

_ZERO_UUID = "00000000-0000-0000-0000-000000000000"


# ── настройки ───────────────────────────────────────────────────────────────


def load_config() -> dict[str, Any]:
    """Выключатель и уведомления. Нет файла — дефолт; битый — дефолт + warning."""
    try:
        data = json.loads(CONFIG_PATH.read_text("utf-8"))
    except FileNotFoundError:
        return dict(DEFAULT_CONFIG)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"plan_change: не удалось прочитать {CONFIG_PATH} ({exc}) — беру дефолт")
        return dict(DEFAULT_CONFIG)
    if not isinstance(data, dict):
        logger.warning(f"plan_change: {CONFIG_PATH} не объект — беру дефолт")
        return dict(DEFAULT_CONFIG)
    return {
        "enabled": bool(data.get("enabled", DEFAULT_CONFIG["enabled"])),
        "notify_admins": bool(data.get("notify_admins", DEFAULT_CONFIG["notify_admins"])),
    }


def _wrapped(module_name: str, class_name: str, method: str) -> bool:
    module = sys.modules.get(module_name)
    if module is None:
        try:
            import importlib

            module = importlib.import_module(module_name)
        except Exception:  # noqa: BLE001
            return False
    cls = getattr(module, class_name, None)
    return bool(getattr(getattr(cls, method, None), "_overlay_wrapped", False))


def patch_applied() -> bool:
    """Наша ли `PurchaseSubscription._execute` в ЭТОМ процессе."""
    return _wrapped(PURCHASE_MODULE, "PurchaseSubscription", "_execute")


def promo_patch_applied() -> bool:
    """Умеет ли активация промокода в ЭТОМ процессе переносить остаток (сессия в __init__)."""
    return _wrapped(PROMOCODE_MODULE, "ActivatePromocode", "__init__")


def overlay_active() -> bool:
    """Перенос реально случится при оплате: правка встала И выключатель включён.

    Это читают витрина кабинета и окно подтверждения в боте: обещать перенос можно,
    только если он произойдёт. Правка не встала — честное «без пересчёта».
    """
    return patch_applied() and bool(load_config().get("enabled"))


def process_name() -> str:
    """Где мы: имя хоста (контейнер) и точка входа — для алерта «правка не встала»."""
    try:
        host = socket.gethostname()
    except Exception:  # noqa: BLE001
        host = "?"
    entry = os.path.basename(sys.argv[0]) if sys.argv and sys.argv[0] else "?"
    return f"{host} ({entry}, pid {os.getpid()})"


# ── снимок состояния ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SubRow:
    """Строка текущей подписки — свежее чтение сырым SQL, не ORM-объект."""

    id: int
    expire_at: datetime
    status: str
    is_trial: bool
    plan_id: Optional[int]
    created_at: Optional[datetime]

    @property
    def is_unlimited(self) -> bool:
        return self.expire_at is not None and self.expire_at.year == UNLIMITED_YEAR


def sub_row_from_dto(dto: Any) -> SubRow:
    """Снимок из SubscriptionDto (витрина, бот): поля читаются мягко."""
    status = getattr(dto, "status", None)
    plan = getattr(dto, "plan_snapshot", None)
    plan_id = getattr(plan, "id", None) if plan is not None else None
    return SubRow(
        id=getattr(dto, "id", None),  # type: ignore[arg-type]
        expire_at=getattr(dto, "expire_at", None),  # type: ignore[arg-type]
        status=str(getattr(status, "value", status) or ""),
        is_trial=bool(getattr(dto, "is_trial", False)),
        plan_id=int(plan_id) if plan_id is not None else None,
        created_at=getattr(dto, "created_at", None),
    )


@dataclass(frozen=True)
class Layer:
    """Слой оплаченных дней строки подписки.

    `seconds` — сколько остатка слой покрывает (у счёта — его длительность).
    `paid`/`listed` — уплачено и витрина в `currency` (у переноса listed = paid).
    `plan_id`/`duration` — по ним ищется цена в другой валюте.
    """

    kind: str  # "renew" | "create" | "carry"
    payment_id: Optional[str]
    seconds: int
    currency: str
    paid: Fraction
    listed: Fraction
    plan_id: Optional[int]
    duration: int

    @property
    def days(self) -> int:
        return self.seconds // DAY


@dataclass(frozen=True)
class ParallelLayer:
    """Докупка (следующая фича): сумма за период, идущий параллельно основному сроку.

    Неиспользованная стоимость = amount × remaining_seconds / total_seconds.
    """

    amount: Fraction
    currency: str
    total_seconds: int
    remaining_seconds: int
    ref: Optional[str] = None


@dataclass(frozen=True)
class CarryState:
    status: str
    is_unlimited: bool
    expire_at: datetime
    frozen_seconds: Optional[int]
    reserve_expire_at: Optional[datetime]
    refund_recent: bool
    old_plan_id: Optional[int]
    layers: tuple[Layer, ...]  # уже в порядке LIFO
    prices: Mapping[tuple[int, int, str], Fraction]  # (plan_id, days, currency) → витрина
    extras: tuple[ParallelLayer, ...] = ()
    # Тарифы, которые сейчас в витрине (is_active). None — не знаем, считаем все.
    active_plan_ids: Optional[frozenset[int]] = None


@dataclass(frozen=True)
class CarryResult:
    mode: str
    remaining_seconds: int
    remaining_days: Optional[int]
    value: Fraction
    new_day_price: Optional[Fraction]
    bonus_days: int
    bonus_seconds: int
    lost_days: int
    capped: bool
    source_payment_ids: tuple[str, ...] = ()
    breakdown: tuple[dict, ...] = ()
    # Докупки, чья стоимость НЕ перенесена (другая валюта, режим без переноса).
    extras_lost: int = 0

    @property
    def added_days(self) -> int:
        """Сколько целых дней добавится к оплаченному сроку (для показа)."""
        return self.bonus_days + self.bonus_seconds // DAY


# ── чистые функции ──────────────────────────────────────────────────────────


def remaining_seconds(state: CarryState, now: datetime) -> Optional[int]:
    """Остаток до секунды. На паузе — сохранённый остаток: срок в базе там стоит."""
    if state.is_unlimited:
        return None
    if state.frozen_seconds is not None:
        return max(0, int(state.frozen_seconds))
    if state.expire_at is None:
        return 0
    return max(0, int((state.expire_at - now).total_seconds()))


def state_mode(state: CarryState, now: datetime) -> tuple[str, Optional[int]]:
    """Режим ТЕКУЩЕЙ подписки (одинаков для любой цели) и остаток в секундах."""
    if str(state.status).upper() == "DELETED":
        return "none", 0
    if state.is_unlimited:
        return "lifetime", None
    left = remaining_seconds(state, now) or 0
    if (
        state.reserve_expire_at is not None
        and state.expire_at is not None
        and state.expire_at <= state.reserve_expire_at + RESERVE_SLACK
    ):
        # Резерв — бесплатная страховка истёкшим; оплаченный RENEW поверх открытой
        # выдачи двигает срок дальше резерва и остаётся настоящим.
        return "reserve", 0
    if left <= 0:
        return "none", 0
    if state.refund_recent:
        return "refund", left
    return "carry", left


def _frac(value: Any) -> Optional[Fraction]:
    if value is None:
        return None
    if isinstance(value, Fraction):
        return value
    try:
        return Fraction(Decimal(str(value)))
    except Exception:  # noqa: BLE001
        return None


def _layer_day_price(layer: Layer, currency: str, prices: Mapping[tuple[int, int, str], Fraction]) -> Optional[Fraction]:
    """Цена дня слоя в валюте счёта. None — перевести нельзя."""
    if layer.paid <= 0 or layer.seconds <= 0:
        return None
    if layer.currency == currency:
        return layer.paid * DAY / layer.seconds
    # Другая валюта: доля уплаченного от витрины переносится на витрину в нужной
    # валюте того же тарифа и срока. Курсов нет — только цены владельца.
    if layer.plan_id is None or layer.duration <= 0 or layer.listed <= 0:
        return None
    listed_here = prices.get((layer.plan_id, layer.duration, currency))
    if listed_here is None or listed_here <= 0:
        return None
    return listed_here * (layer.paid / layer.listed) / layer.duration


def cheapest_day_price(state: CarryState, currency: str) -> Optional[Fraction]:
    """Самая низкая витринная цена дня ТЕКУЩЕГО тарифа в валюте счёта.

    Нижняя граница стоимости «дней без оплаты». Тарифа нет в витрине (импорт с id −1,
    удалён, выключен) — None: такие дни не переносятся.
    """
    plan_id = state.old_plan_id
    if plan_id is None or plan_id <= 0:
        return None
    if state.active_plan_ids is not None and plan_id not in state.active_plan_ids:
        return None
    best: Optional[Fraction] = None
    for (pid, days, cur), price in state.prices.items():
        if pid != plan_id or cur != currency or days <= 0 or price is None or price <= 0:
            continue
        per_day = price / days
        if best is None or per_day < best:
            best = per_day
    return best


def _fmt(value: Optional[Fraction], places: int = 4) -> Optional[str]:
    if value is None:
        return None
    return str(to_decimal(value, places))


def to_decimal(value: Fraction, places: int = 4) -> Decimal:
    quant = Decimal(1).scaleb(-places)
    return (Decimal(value.numerator) / Decimal(value.denominator)).quantize(quant, rounding=ROUND_DOWN)


def _value_layers(state: CarryState, left: int, currency: str) -> tuple[Fraction, int, list[dict], list[str]]:
    """Стоимость остатка по слоям LIFO + дни без оплаты. → (value, lost_sec, breakdown, sources)."""
    value = Fraction(0)
    breakdown: list[dict] = []
    sources: list[str] = []
    untranslated = 0
    for layer in state.layers:
        if left <= 0:
            break
        if layer.seconds <= 0:
            continue
        take = min(left, layer.seconds)
        price = _layer_day_price(layer, currency, state.prices)
        if price is None:
            untranslated += take
            breakdown.append(
                {"kind": layer.kind, "payment_id": layer.payment_id, "seconds": take,
                 "day_price": None, "converted": False}
            )
        else:
            value += Fraction(take, DAY) * price
            breakdown.append(
                {"kind": layer.kind, "payment_id": layer.payment_id, "seconds": take,
                 "day_price": _fmt(price), "converted": True, "currency": layer.currency}
            )
            if layer.payment_id and layer.kind in ("renew", "create"):
                sources.append(layer.payment_id)
        left -= take

    free = left + untranslated
    lost_sec = 0
    if free > 0:
        cheapest = cheapest_day_price(state, currency)
        if cheapest is None:
            lost_sec = free
            breakdown.append({"kind": "free", "seconds": free, "day_price": None, "converted": False})
        else:
            value += Fraction(free, DAY) * cheapest
            breakdown.append({"kind": "free", "seconds": free, "day_price": _fmt(cheapest), "converted": True})
    return value, lost_sec, breakdown, sources


def _value_extras(extras: Sequence[ParallelLayer], currency: str) -> tuple[Fraction, int, list[dict]]:
    """Неиспользованная стоимость докупок в валюте счёта. → (value, не перенесено, breakdown)."""
    value = Fraction(0)
    lost = 0
    breakdown: list[dict] = []
    for extra in extras:
        if extra.amount <= 0 or extra.total_seconds <= 0 or extra.remaining_seconds <= 0:
            continue
        remaining = min(extra.remaining_seconds, extra.total_seconds)
        if extra.currency != currency:
            # Рубли на звёзды не делим: докупка в другой валюте не переносится.
            lost += 1
            breakdown.append({"kind": "device", "ref": extra.ref, "seconds": remaining, "converted": False})
            continue
        part = Fraction(extra.amount) * remaining / extra.total_seconds
        value += part
        breakdown.append(
            {"kind": "device", "ref": extra.ref, "seconds": remaining, "amount": _fmt(part), "converted": True}
        )
    return value, lost, breakdown


def _live_extras(extras: Sequence[ParallelLayer]) -> int:
    return sum(1 for x in extras if x.amount > 0 and x.total_seconds > 0 and x.remaining_seconds > 0)


def compute_carryover(
    state: CarryState,
    *,
    new_plan_id: int,
    new_duration: int,
    new_list_amount: Any,
    currency: str,
    now: datetime,
) -> CarryResult:
    """Сколько дней добавить к новому сроку. ЧИСТАЯ функция: всё нужное — в аргументах."""
    mode, left = state_mode(state, now)
    extras_live = _live_extras(state.extras)

    def plain(m: str, *, remaining: int, days: Optional[int], lost: int = 0) -> CarryResult:
        return CarryResult(
            mode=m, remaining_seconds=remaining, remaining_days=days, value=Fraction(0),
            new_day_price=None, bonus_days=0, bonus_seconds=0, lost_days=lost, capped=False,
            extras_lost=extras_live,
        )

    if mode == "lifetime":
        return plain("lifetime", remaining=0, days=None)
    if mode in ("none", "reserve"):
        return plain(mode, remaining=0, days=0)
    assert left is not None
    left_days = left // DAY
    if mode == "refund":
        return plain("refund", remaining=left, days=left_days, lost=left_days)
    if new_duration <= 0:
        # Новый тариф бессрочный — покрывает всё, делить не на что.
        return plain("none", remaining=left, days=left_days)

    listed = _frac(new_list_amount)
    new_day = listed / new_duration if listed is not None and listed > 0 else None

    value, lost_sec, breakdown, sources = _value_layers(state, left, currency)
    extras_value, extras_lost, extras_breakdown = _value_extras(state.extras, currency)
    breakdown.extend(extras_breakdown)
    cap_sec = MAX_BONUS_DAYS * DAY

    if state.old_plan_id is not None and state.old_plan_id == new_plan_id:
        # Тот же тариф — 1:1, как продление: старая скидка не укорачивает срок.
        bonus_seconds = min(left, cap_sec)
        capped = left > cap_sec
        extra_days = 0
        if extras_value > 0:
            if new_day is None:
                extras_lost = extras_live
            else:
                extra_days = math.floor(extras_value / new_day)
        room = (cap_sec - bonus_seconds) // DAY
        if extra_days > room:
            capped = True
            extra_days = room
        lost = math.ceil((left - bonus_seconds) / DAY) if left > bonus_seconds else 0
        return CarryResult(
            mode="same_plan", remaining_seconds=left, remaining_days=left_days,
            value=value + extras_value, new_day_price=new_day, bonus_days=extra_days,
            bonus_seconds=bonus_seconds, lost_days=lost, capped=capped,
            source_payment_ids=tuple(sources), breakdown=tuple(breakdown), extras_lost=extras_lost,
        )

    if new_day is None:
        return CarryResult(
            mode="unpriced", remaining_seconds=left, remaining_days=left_days, value=Fraction(0),
            new_day_price=None, bonus_days=0, bonus_seconds=0, lost_days=left_days, capped=False,
            extras_lost=extras_live,
        )

    total = value + extras_value
    uncapped = math.floor(total / new_day)
    capped = uncapped > MAX_BONUS_DAYS
    bonus = min(uncapped, MAX_BONUS_DAYS)
    lost = math.ceil(lost_sec / DAY) if lost_sec > 0 else 0
    if capped:
        lost += left_days - math.floor(Fraction(left_days * MAX_BONUS_DAYS, uncapped))
    lost = min(lost, math.ceil(left / DAY))
    return CarryResult(
        mode="carry", remaining_seconds=left, remaining_days=left_days, value=total,
        new_day_price=new_day, bonus_days=bonus, bonus_seconds=0, lost_days=lost, capped=capped,
        source_payment_ids=tuple(sources), breakdown=tuple(breakdown), extras_lost=extras_lost,
    )


def failed_result(expire_at: Optional[datetime], now: datetime) -> CarryResult:
    """Состояние не загрузилось: выдача по правилам базы, остаток — для ручного «Продлить»."""
    left = max(0, int((expire_at - now).total_seconds())) if expire_at is not None else 0
    if expire_at is not None and expire_at.year == UNLIMITED_YEAR:
        left = 0
    return CarryResult(
        mode="failed", remaining_seconds=left, remaining_days=left // DAY, value=Fraction(0),
        new_day_price=None, bonus_days=0, bonus_seconds=0, lost_days=left // DAY, capped=False,
    )


def target_expire(now: datetime, new_duration: int, result: CarryResult) -> datetime:
    """Новый срок в момент зачисления. Бессрочный тариф (0 дн.) — забота вызывающего."""
    if result.mode == "same_plan":
        delta = timedelta(days=new_duration + result.bonus_days, seconds=result.bonus_seconds)
    elif result.mode == "carry":
        delta = timedelta(days=new_duration + result.bonus_days)
    else:
        delta = timedelta(days=new_duration)
    return (now + delta).replace(microsecond=0)


def new_day_amount(
    pricing: Any,
    plan_id: int,
    duration: int,
    currency: str,
    prices: Mapping[tuple[int, int, str], Fraction],
) -> Optional[Decimal]:
    """Витринная цена выбранного срока из счёта — основа цены дня нового тарифа.

    Берём `original_amount` из самого счёта: сумма фиксируется при создании, и цена
    пересчёта — тоже. Исключение — скидка 100%: база тогда могла взять
    `original_amount` из ДРУГОЙ валюты (у срока нет цены в валюте счёта), поэтому цену
    ищем в таблице тарифа в валюте счёта; нет — перенос невозможен.
    """

    def read(name: str) -> Any:
        if isinstance(pricing, Mapping):
            return pricing.get(name)
        return getattr(pricing, name, None)

    try:
        discount = int(read("discount_percent") or 0)
    except (TypeError, ValueError):
        discount = 0
    if discount >= 100:
        table = prices.get((plan_id, duration, currency))
        if table is None or table <= 0:
            return None
        return to_decimal(table, 6)
    original = _frac(read("original_amount"))
    if original is None or original <= 0:
        return None
    return to_decimal(original, 6)


def promo_list_amount(
    prices: Mapping[tuple[int, int, str], Fraction], plan_id: int, duration: int, currency: str
) -> Optional[Fraction]:
    """Витринная цена подарка на его срок — основа цены дня при переносе по промокоду.

    Счёта у подарка нет, поэтому цена — из таблицы тарифа в валюте по умолчанию: цена
    ровно этого срока, а если такого срока в витрине нет — самая ВЫСОКАЯ цена дня тарифа
    (короткий срок: цена дня выше — бонус меньше, магазин не дарит лишнего). Тарифа в
    таблице нет — None: перенос невозможен, подарок работает «заменой», как раньше.
    """
    if duration <= 0:
        return None
    exact = prices.get((plan_id, duration, currency))
    if exact is not None and exact > 0:
        return exact
    best: Optional[Fraction] = None
    for (pid, days, cur), price in prices.items():
        if pid != plan_id or cur != currency or days <= 0 or price is None or price <= 0:
            continue
        per_day = price / days
        if best is None or per_day > best:
            best = per_day
    return best * duration if best is not None else None


def promo_loses_days(result: CarryResult) -> bool:
    """Пропадут ли дни при активации подарка другого тарифа (веб подтверждения не спрашивает)."""
    if result.mode == "lifetime":
        return True
    if result.mode in ("refund", "unpriced", "failed"):
        return (result.remaining_days or 0) >= 1
    if result.mode == "carry":
        return result.lost_days > 0
    return False


def promo_block_reason(result: CarryResult) -> str:
    """Почему веб не активирует подарок другого тарифа — человеческим языком."""
    if result.mode == "lifetime":
        why = "бессрочная подписка станет срочной"
    elif result.mode == "refund":
        why = "по подписке был возврат оплаты"
    elif result.mode == "carry" and result.lost_days > 0:
        why = f"{result.lost_days} дн. без известной цены"
    else:
        why = "цена нового тарифа неизвестна"
    return (
        f"Этот подарок заменит ваш тариф, и остаток перенести нельзя: {why}. "
        "Чтобы не потерять дни, обратитесь в поддержку."
    )


@dataclass(frozen=True)
class PromoCarry:
    result: CarryResult
    state: CarryState
    currency: str


# ── SQL ─────────────────────────────────────────────────────────────────────

DEFAULT_CURRENCY_SQL = "SELECT default_currency::text FROM settings ORDER BY id LIMIT 1"

LOCK_CURRENT_SQL = (
    "SELECT s.id, s.expire_at, s.status::text, s.is_trial, "
    "(s.plan_snapshot->>'id')::int, s.created_at "
    "FROM users u JOIN subscriptions s ON s.id = u.current_subscription_id "
    "WHERE u.id = :uid FOR UPDATE OF u"
)

READ_SUBSCRIPTION_SQL = (
    "SELECT s.id, s.expire_at, s.status::text, s.is_trial, "
    "(s.plan_snapshot->>'id')::int, s.created_at "
    "FROM subscriptions s WHERE s.id = :sid"
)

FREEZE_SQL = (
    "SELECT remaining_seconds FROM subscription_freezes "
    "WHERE user_id = :uid AND active = true"
)

RESERVE_SQL = (
    "SELECT max(reserve_expire_at) FROM reserve_grants "
    "WHERE user_id = :uid AND ended = false AND reserve_expire_at > now()"
)

REFUND_SQL = (
    "SELECT EXISTS (SELECT 1 FROM transactions "
    "WHERE user_id = :uid AND status = 'REFUNDED' AND is_test = false "
    "AND updated_at > now() - interval '365 days')"
)

LAST_CARRY_SQL = (
    "SELECT source, created_at, currency, value_amount, bonus_days, bonus_seconds, "
    "new_day_price, mode, capped, remaining_seconds, new_plan_id, new_duration "
    "FROM plan_change_carryovers WHERE subscription_id = :sid "
    "ORDER BY created_at DESC, id DESC LIMIT 1"
)

LAYERS_SQL = (
    "SELECT payment_id::text, purchase_type::text, currency::text, "
    "(pricing->>'final_amount')::numeric, (pricing->>'original_amount')::numeric, "
    "(plan_snapshot->>'id')::int, (plan_snapshot->>'duration')::int, updated_at "
    "FROM transactions "
    "WHERE user_id = :uid AND status = 'COMPLETED' AND is_test = false "
    "AND (plan_snapshot->>'id')::int > 0 AND (plan_snapshot->>'duration')::int > 0 "
    "AND (pricing->>'final_amount')::numeric > 0 "
    "AND payment_id <> CAST(:exclude AS uuid) "
    "AND ( (purchase_type = 'RENEW' AND updated_at > CAST(:cut AS timestamptz)) "
    "OR (CAST(:with_create AS boolean) AND purchase_type IN ('NEW', 'CHANGE') "
    "AND updated_at BETWEEN CAST(:row_created AS timestamptz) - interval '120 seconds' "
    "AND CAST(:row_created AS timestamptz) + interval '5 seconds') ) "
    "ORDER BY updated_at DESC"
)

PRICES_SQL = (
    "SELECT d.plan_id, d.days, p.currency::text, p.price, pl.is_active "
    "FROM plan_durations d "
    "JOIN plan_prices p ON p.plan_duration_id = d.id "
    "JOIN plans pl ON pl.id = d.plan_id "
    "WHERE d.plan_id = ANY(CAST(:ids AS integer[]))"
)

CARRY_APPLIED_SQL = "SELECT 1 FROM plan_change_carryovers WHERE payment_id = CAST(:pid AS uuid)"

CLOSE_FREEZE_SQL = (
    "UPDATE subscription_freezes SET active = false WHERE user_id = :uid AND active = true"
)

INSERT_CARRY_SQL = (
    "INSERT INTO plan_change_carryovers ("
    "payment_id, source, user_id, old_subscription_id, subscription_id, old_plan_id, "
    "new_plan_id, new_duration, mode, remaining_seconds, currency, value_amount, "
    "new_day_price, bonus_days, bonus_seconds, lost_days, capped, source_payment_ids, "
    "breakdown, expire_before, expire_after) VALUES ("
    "CAST(:payment_id AS uuid), :source, :user_id, :old_subscription_id, :subscription_id, "
    ":old_plan_id, :new_plan_id, :new_duration, :mode, :remaining_seconds, :currency, "
    "CAST(:value_amount AS numeric), CAST(:new_day_price AS numeric), :bonus_days, "
    ":bonus_seconds, :lost_days, :capped, CAST(:source_payment_ids AS uuid[]), "
    "CAST(:breakdown AS jsonb), CAST(:expire_before AS timestamptz), "
    "CAST(:expire_after AS timestamptz)) "
    "ON CONFLICT (payment_id) DO NOTHING"
)

CARRY_FOR_SUBSCRIPTION_SQL = (
    "SELECT mode, bonus_days, bonus_seconds, lost_days, remaining_seconds, payment_id::text "
    "FROM plan_change_carryovers WHERE subscription_id = :sid AND source = 'purchase' "
    "ORDER BY created_at DESC, id DESC LIMIT 1"
)

CARRIES_BY_SOURCE_SQL = (
    "SELECT id, payment_id::text, user_id, subscription_id, mode, bonus_days, bonus_seconds, "
    "created_at FROM plan_change_carryovers "
    "WHERE CAST(:pid AS uuid) = ANY(source_payment_ids) OR payment_id = CAST(:pid AS uuid) "
    "ORDER BY created_at"
)

TRANSACTION_STATUS_SQL = "SELECT status::text FROM transactions WHERE payment_id = CAST(:pid AS uuid)"


def _row_to_sub(row: Any) -> Optional[SubRow]:
    if row is None:
        return None
    return SubRow(
        id=int(row[0]),
        expire_at=row[1],
        status=str(row[2] or ""),
        is_trial=bool(row[3]),
        plan_id=int(row[4]) if row[4] is not None else None,
        created_at=row[5],
    )


async def lock_current_subscription(session: Any, user_id: int) -> Optional[SubRow]:
    """Замок на строке users и СВЕЖЕЕ чтение текущей подписки одним запросом.

    Сырым SQL, а не `get_current`: сессия живёт с `expire_on_commit=False`, и
    повторный ORM-select вернул бы объект из identity map со старым сроком —
    продление, закоммиченное соседним процессом, осталось бы невидимым.
    """
    row = (await session.execute(text(LOCK_CURRENT_SQL), {"uid": user_id})).first()
    return _row_to_sub(row)


async def read_subscription_row(session: Any, subscription_id: int) -> Optional[SubRow]:
    row = (await session.execute(text(READ_SUBSCRIPTION_SQL), {"sid": subscription_id})).first()
    return _row_to_sub(row)


def _carry_layer(row: Any) -> Optional[Layer]:
    """Перенос, которым строка создана, как слой: его стоимость — уже в деньгах."""
    (_source, _created, currency, value_amount, bonus_days, bonus_seconds, new_day_price,
     mode, capped, remaining, new_plan_id, new_duration) = row
    seconds = int(bonus_days or 0) * DAY + int(bonus_seconds or 0)
    value = _frac(value_amount) or Fraction(0)
    if seconds <= 0 or value <= 0:
        return None
    if mode == "carry":
        ndp = _frac(new_day_price)
        if ndp is not None and ndp > 0:
            # Округление бонуса вниз отбросило долю дня — её стоимость не переносим.
            value = min(value, ndp * int(bonus_days or 0))
    elif capped and remaining:
        covered = int(remaining) + int(bonus_days or 0) * DAY
        if covered > seconds:
            value = value * seconds / covered
    if value <= 0:
        return None
    return Layer(
        kind="carry", payment_id=None, seconds=seconds, currency=str(currency),
        paid=value, listed=value, plan_id=int(new_plan_id) if new_plan_id is not None else None,
        duration=int(new_duration or 0),
    )


async def load_carry_state(
    session: Any,
    *,
    user_id: int,
    subscription: SubRow,
    now: datetime,
    exclude_payment_id: Any = None,
    extra_plan_ids: Iterable[int] = (),
    extras: Sequence[ParallelLayer] = (),
) -> CarryState:
    """Всё, что нужно чистой функции, — из базы (запросы A–F плана)."""
    uid = {"uid": user_id}
    frozen_row = (await session.execute(text(FREEZE_SQL), uid)).first()
    frozen = int(frozen_row[0]) if frozen_row and frozen_row[0] is not None else None
    reserve = (await session.execute(text(RESERVE_SQL), uid)).scalar()
    refund = bool((await session.execute(text(REFUND_SQL), uid)).scalar())

    created_at = subscription.created_at
    if created_at is None and subscription.id is not None:
        fresh = await read_subscription_row(session, subscription.id)
        created_at = fresh.created_at if fresh else None

    carry_row = (await session.execute(text(LAST_CARRY_SQL), {"sid": subscription.id})).first()
    cut = carry_row[1] if carry_row is not None else created_at
    # Промокод меняет тариф В ТОЙ ЖЕ строке: её создающий счёт уже пересчитан в перенос.
    with_create = created_at is not None and not (carry_row is not None and carry_row[0] == "promocode")

    layer_rows: list[Any] = []
    if cut is not None:
        layer_rows = list(
            (
                await session.execute(
                    text(LAYERS_SQL),
                    {
                        "uid": user_id,
                        "exclude": str(exclude_payment_id) if exclude_payment_id else _ZERO_UUID,
                        "cut": cut,
                        "with_create": with_create,
                        "row_created": created_at or cut,
                    },
                )
            ).all()
        )

    renews: list[Layer] = []
    create: Optional[Layer] = None
    for pid, ptype, cur, final, original, plan_id, duration, _updated in layer_rows:
        final_f = _frac(final) or Fraction(0)
        original_f = _frac(original) or final_f
        layer = Layer(
            kind="renew" if ptype == "RENEW" else "create",
            payment_id=str(pid) if pid else None,
            seconds=int(duration) * DAY,
            currency=str(cur),
            paid=final_f,
            listed=original_f,
            plan_id=int(plan_id) if plan_id is not None else None,
            duration=int(duration),
        )
        if ptype == "RENEW":
            renews.append(layer)  # уже от новых к старым
        elif create is None:
            create = layer  # самый поздний NEW/CHANGE в окне

    layers: list[Layer] = list(renews)
    if create is not None:
        layers.append(create)
    carry_plan_id: Optional[int] = None
    if carry_row is not None:
        carry_layer = _carry_layer(carry_row)
        if carry_layer is not None:
            layers.append(carry_layer)
            carry_plan_id = carry_layer.plan_id

    ids: set[int] = {int(i) for i in extra_plan_ids if i is not None}
    if subscription.plan_id is not None:
        ids.add(int(subscription.plan_id))
    ids.update(l.plan_id for l in layers if l.plan_id is not None)
    if carry_plan_id is not None:
        ids.add(carry_plan_id)

    prices: dict[tuple[int, int, str], Fraction] = {}
    active: set[int] = set()
    if ids:
        for plan_id, days, cur, price, is_active in (
            await session.execute(text(PRICES_SQL), {"ids": sorted(ids)})
        ).all():
            value = _frac(price)
            if value is None:
                continue
            prices[(int(plan_id), int(days), str(cur))] = value
            if is_active:
                active.add(int(plan_id))

    expire_at = subscription.expire_at
    return CarryState(
        status=subscription.status,
        is_unlimited=bool(expire_at is not None and expire_at.year == UNLIMITED_YEAR),
        expire_at=expire_at,
        frozen_seconds=frozen,
        reserve_expire_at=reserve,
        refund_recent=refund,
        old_plan_id=subscription.plan_id,
        layers=tuple(layers),
        prices=prices,
        extras=tuple(extras),
        active_plan_ids=frozenset(active),
    )


async def default_currency(session: Any) -> str:
    row = (await session.execute(text(DEFAULT_CURRENCY_SQL))).first()
    return str(row[0]) if row is not None and row[0] else "RUB"


async def promo_carry(
    session: Any,
    *,
    user_id: int,
    subscription: Any,
    plan_id: int,
    duration: int,
    now: datetime,
) -> Optional[PromoCarry]:
    """Перенос остатка при подарке ДРУГОГО тарифа. None — перенос тут ни при чём.

    Ни при чём: подписки нет, это пробник, тариф тот же (дни и так складываются) или
    подарок бессрочный. Иначе — расчёт той же чистой функцией, что у покупки; решает
    вызывающий: `carry` — пересчитать, остальные режимы — «замена», как раньше.
    """
    if subscription is None or getattr(subscription, "is_trial", False):
        return None
    current_plan = getattr(subscription, "plan_snapshot", None)
    current_id = getattr(current_plan, "id", None) if current_plan is not None else None
    if current_id is not None and int(current_id) == int(plan_id):
        return None
    if duration <= 0:
        return None
    state = await load_carry_state(
        session,
        user_id=user_id,
        subscription=sub_row_from_dto(subscription),
        now=now,
        exclude_payment_id=None,
        extra_plan_ids=(plan_id,),
    )
    currency = await default_currency(session)
    amount = promo_list_amount(state.prices, plan_id, duration, currency)
    result = compute_carryover(
        state,
        new_plan_id=plan_id,
        new_duration=duration,
        new_list_amount=amount,
        currency=currency,
        now=now,
    )
    return PromoCarry(result=result, state=state, currency=currency)


PROMO_CHECK_FAILED = "Не удалось проверить, что будет с остатком подписки. Попробуйте ещё раз позже."


async def promo_web_refusal(
    session: Any,
    *,
    user: Any,
    code: str,
    subscription_dao: Any,
    promocode_dao: Any,
    now: datetime,
) -> Optional[str]:
    """Причина НЕ активировать подарок другого тарифа в вебе — или None.

    В боте подарок подтверждают дважды и видят, что будет с остатком. В вебе
    подтверждения нет: раньше подарок молча сжигал оплаченный остаток. Теперь остаток
    переносится по цене дня, а там, где целиком перенести нельзя (бессрочная, возврат,
    дни без известной цены, нет цены подарка), веб отказывает с понятной причиной.
    Выключатель выключен или правка активации не встала — поведение как раньше.
    """
    if not load_config().get("enabled") or not promo_patch_applied():
        return None
    try:
        promo = await promocode_dao.get_by_code(code)
        reward = getattr(getattr(promo, "reward_type", None), "value", getattr(promo, "reward_type", None))
        if promo is None or str(reward) != "SUBSCRIPTION":
            return None
        snapshot = getattr(promo, "plan_snapshot", None) or {}
        plan_id, duration = snapshot.get("id"), snapshot.get("duration")
        if plan_id is None or duration is None:
            return None
        current = await subscription_dao.get_current(user.id)
        carried = await promo_carry(
            session, user_id=user.id, subscription=current, plan_id=int(plan_id),
            duration=int(duration), now=now,
        )
    except Exception as exc:  # noqa: BLE001
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        logger.warning(f"promocode: перенос остатка user_id={getattr(user, 'id', '?')} не проверен ({exc})")
        # Не знаем, сгорят ли дни, — не активируем молча: деньги человека дороже минуты.
        return PROMO_CHECK_FAILED
    if carried is None or not promo_loses_days(carried.result):
        return None
    return promo_block_reason(carried.result)


async def carry_already_applied(session: Any, payment_id: Any) -> bool:
    if payment_id is None:
        return False
    row = (await session.execute(text(CARRY_APPLIED_SQL), {"pid": str(payment_id)})).first()
    return row is not None


async def close_freeze(session: Any, user_id: int) -> None:
    """Погасить паузу: иначе крон или «возобновить» поставят now + старый остаток поверх."""
    await session.execute(text(CLOSE_FREEZE_SQL), {"uid": user_id})


async def record_carryover(
    session: Any,
    *,
    source: str,
    payment_id: Any,
    user_id: int,
    old_subscription_id: int,
    subscription_id: int,
    old_plan_id: Optional[int],
    new_plan_id: int,
    new_duration: int,
    currency: str,
    result: CarryResult,
    expire_before: Optional[datetime],
    expire_after: datetime,
) -> None:
    """Запись журнала — отсечка следующего переноса, аудит, алерт возврата. Без commit."""
    await session.execute(
        text(INSERT_CARRY_SQL),
        {
            "payment_id": str(payment_id) if payment_id else None,
            "source": source,
            "user_id": user_id,
            "old_subscription_id": old_subscription_id,
            "subscription_id": subscription_id,
            "old_plan_id": old_plan_id,
            "new_plan_id": new_plan_id,
            "new_duration": new_duration,
            "mode": result.mode,
            "remaining_seconds": int(result.remaining_seconds or 0),
            "currency": currency,
            "value_amount": to_decimal(result.value, 4),
            "new_day_price": (
                to_decimal(result.new_day_price, 6) if result.new_day_price is not None else None
            ),
            "bonus_days": int(result.bonus_days),
            "bonus_seconds": int(result.bonus_seconds),
            "lost_days": int(result.lost_days),
            "capped": bool(result.capped),
            "source_payment_ids": [str(UUID(str(p))) for p in result.source_payment_ids],
            "breakdown": json.dumps(list(result.breakdown), ensure_ascii=False),
            "expire_before": expire_before,
            "expire_after": expire_after,
        },
    )


async def carry_for_subscription(session: Any, subscription_id: int) -> Optional[dict]:
    """Перенос, которым создана строка (итог покупки в боте)."""
    row = (await session.execute(text(CARRY_FOR_SUBSCRIPTION_SQL), {"sid": subscription_id})).first()
    if row is None:
        return None
    mode, bonus_days, bonus_seconds, lost_days, remaining, payment_id = row
    return {
        "mode": mode,
        "bonus_days": int(bonus_days or 0),
        "bonus_seconds": int(bonus_seconds or 0),
        "lost_days": int(lost_days or 0),
        "remaining_seconds": int(remaining or 0),
        "payment_id": payment_id,
        "added_days": int(bonus_days or 0) + int(bonus_seconds or 0) // DAY,
    }


async def carries_by_source_payment(session: Any, payment_id: Any) -> list[dict]:
    """Переносы, в которые ушли деньги этого платежа (или сам счёт смены)."""
    rows = (await session.execute(text(CARRIES_BY_SOURCE_SQL), {"pid": str(payment_id)})).all()
    return [
        {
            "id": r[0], "payment_id": r[1], "user_id": r[2], "subscription_id": r[3], "mode": r[4],
            "bonus_days": int(r[5] or 0), "bonus_seconds": int(r[6] or 0), "created_at": r[7],
            "added_days": int(r[5] or 0) + int(r[6] or 0) // DAY,
        }
        for r in rows
    ]


async def transaction_status(session: Any, payment_id: Any) -> Optional[str]:
    """Статус счёта сырым SQL — мимо identity map сессии."""
    row = (await session.execute(text(TRANSACTION_STATUS_SQL), {"pid": str(payment_id)})).first()
    return str(row[0]) if row is not None else None


# ── тексты владельцу ────────────────────────────────────────────────────────

MODE_TITLES = {
    "carry": "пересчёт по цене дня",
    "same_plan": "тот же тариф, 1:1",
    "lifetime": "бессрочная — без переноса",
    "reserve": "резерв — без переноса",
    "refund": "был возврат — перенос отключён",
    "none": "переносить нечего",
    "unpriced": "нет цены нового срока",
    "failed": "расчёт не удался",
}


def admin_carry_text(
    user_log: str,
    old_name: str,
    new_name: str,
    duration: int,
    result: CarryResult,
    payment_id: Any,
) -> str:
    left = result.remaining_days
    left_text = "бессрочная" if left is None else f"{left} дн."
    if result.mode == "refund":
        return (
            "⚠️ <b>Смена тарифа без переноса</b>\n"
            f"{user_log}\n«{old_name}» → «{new_name}», {duration} дн.\n"
            "Перенос отключён: у человека был возврат за последний год. "
            f"Остаток {left_text} не перенесён.\n"
            f"Счёт <code>{payment_id}</code>"
        )
    lines = [
        "🔁 <b>Смена тарифа с пересчётом</b>",
        user_log,
        f"«{old_name}» → «{new_name}», {duration} дн.",
        f"Остаток {left_text} → +{result.added_days} дн. ({MODE_TITLES.get(result.mode, result.mode)})",
    ]
    if result.lost_days:
        lines.append(f"Не перенесено: {result.lost_days} дн.")
    if result.capped:
        lines.append(f"Упёрлось в предел {MAX_BONUS_DAYS} дн.")
    if result.extras_lost:
        lines.append(f"Докупки без переноса: {result.extras_lost}")
    lines.append(f"Счёт <code>{payment_id}</code>")
    return "\n".join(lines)


def admin_failed_text(user_log: str, error: Any, remaining_days: Optional[int]) -> str:
    left = "?" if remaining_days is None else str(remaining_days)
    return (
        "⚠️ <b>Остаток не пересчитан</b>\n"
        f"{user_log}\n"
        f"Смена тарифа прошла без переноса ({error}). У человека было {left} дн. — "
        "добавьте вручную: Пользователи → Подписка → «Продлить»."
    )


def admin_not_applied_text(user_log: str, remaining_days: Optional[int]) -> str:
    left = "?" if remaining_days is None else str(remaining_days)
    return (
        "⚠️ <b>Перенос остатка не применился</b>\n"
        f"{user_log}\n"
        f"Процесс {process_name()}: правка переноса в нём не встала, смена тарифа прошла "
        f"по правилам базы — остаток ~{left} дн. сгорел. Добавьте вручную: Пользователи → "
        "Подписка → «Продлить», и пересоберите этот процесс."
    )


def admin_unrecorded_text(user_log: str, result: CarryResult, error: Any, payment_id: Any) -> str:
    return (
        "⚠️ <b>Перенос выполнен, журнал не записан</b>\n"
        f"{user_log}\n"
        f"+{result.added_days} дн. выданы, но запись о переносе не сохранилась ({error}). "
        "Следующая смена тарифа может оценить эти дни неточно.\n"
        f"Счёт <code>{payment_id}</code>"
    )


def admin_refund_text(payment_id: Any, carries: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "⚠️ <b>Возврат по платежу, из которого переносили дни</b>",
        f"Счёт <code>{payment_id}</code>",
    ]
    for c in carries:
        when = c.get("created_at")
        when_text = when.strftime("%d.%m.%Y") if hasattr(when, "strftime") else str(when)
        lines.append(f"Подписка #{c.get('subscription_id')}: +{c.get('added_days', 0)} дн. ({when_text})")
    lines.append("Если возврат настоящий — уменьшите срок: «Продлить» на −N дн.")
    return "\n".join(lines)
