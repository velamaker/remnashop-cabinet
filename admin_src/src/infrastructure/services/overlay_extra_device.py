"""Докупка «+1 устройство до конца срока подписки» (overlay).

ЧТО ПРОДАЁМ. Слот устройства к ТЕКУЩЕЙ подписке на период «сейчас → конец её срока»
по цене «₽ за 1 устройство в 30 дней» × остаток, вверх до рубля, не меньше минимума.
Слот — строка журнала с концом; лимит в базе и в панели остаётся ОДНИМ числом
(`subscriptions.device_limit`), потому что именно его читают бот, кабинет, админка и
синхронизация панели. Докупка — дельта к этому числу, конец слота — дельта обратно.

ПОЧЕМУ УЗКОЕ ТЕЛО PATCH, А НЕ `UpdateDeviceLimit`. Штатный сценарий шлёт в панель
ВЕСЬ снимок подписки из базы. Возобновление паузы и резерв пишут срок только в панель,
и до вебхука в базе лежит старый срок: полный PATCH докупки вернул бы человеку старую
дату и съел дни паузы. Поэтому `UPDATE subscriptions SET device_limit` + PATCH ровно
`{uuid, hwidDeviceLimit}` (приём из taskiq/tasks/reserve.py и public/freeze.py).

ПОЧЕМУ ДЕНЬГИ ИДУТ ЧЕРЕЗ БАЛАНС. Оплата картой зачисляется на ₽-баланс и ТЕМ ЖЕ кодом,
что и покупка с баланса, превращается в слот. Откат после получения денег сводится к
«деньги уже на балансе»: отдельного возврата в шлюз, второй идемпотентности и состояния
«оплачено, но не выдано и не возвращено» в коде нет.

РЕШЕНИЯ ВЛАДЕЛЬЦА (17.09), они же — отличия от первой редакции плана:
  * цена по умолчанию 100 ₽ за устройство на 30 дней;
  * по окончании слота устройства НЕ отключаем, а повторную докупку ЭТОЙ подписке
    больше не предлагаем (`already_used`) — кабинет ведёт на тариф побольше;
  * отключение лишних устройств осталось настройкой, ПО УМОЛЧАНИЮ ВЫКЛЮЧЕННОЙ, и
    отключает только то, что подключено ПОСЛЕ покупки места (см. pick_excess);
  * максимум 2 слота на подписку.

Конфиг — assets/extra_device.json, читается на каждый вызов (админка правит на лету).
Выключатель гасит продажи и кнопки, но НЕ крон: действующие слоты обязаны дожить свой
срок и кончиться, а оплаченные заказы — примениться.

Модульный уровень — только stdlib/sqlalchemy/loguru: этот модуль импортирует правка
шлюза, а она грузится посреди денежного пути (урок overlay_topup).
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Optional, Sequence

from loguru import logger
from sqlalchemy import text

if TYPE_CHECKING:  # pragma: no cover — только для подсказок типов
    from sqlalchemy.ext.asyncio import AsyncSession

# 30 суток в секундах — единица цены. Ровно 30 × 86400, без календарных месяцев:
# цена не должна зависеть от того, в каком месяце человек нажал кнопку.
SEC30 = 2_592_000
DAY = 86_400
# Синтетический «тариф» счёта докупки. Пополнение уже занимает −2, подарок −3,
# импорт −1; enum PurchaseType базы не трогаем — счёт идёт как NEW.
SYNTHETIC_PLAN_ID = -4
# За сколько дней до конца слота напоминаем, что лимит вернётся.
REMIND_DAYS = 3
# Сколько часов крон повторяет применение оплаченного заказа, пока панель молчит.
CREDIT_RETRY_HOURS = 2
# Заказ старше суток применять нельзя: цена была за период, половина которого прошла.
STALE_HOURS = 24
# Сколько подряд неудач применения лимита терпим до алерта владельцу.
FAIL_ALERT_AT = 3
# Год «бессрочной» подписки в панели.
UNLIMITED_YEAR = 2099

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
CONFIG_PATH = ASSETS_DIR / "extra_device.json"

DEFAULT_CONFIG: dict[str, Any] = {
    # Продажи выключены, пока владелец не откроет их в админке: цена уже стоит, но
    # выкатка образа не должна сама по себе начать брать деньги.
    "enabled": False,
    # Решение владельца: 100 ₽ за одно устройство на 30 дней.
    "price_rub_30d": 100,
    "min_amount_rub": 10,
    "min_days_left": 3,
    "max_extra": 2,
    # ВЫКЛЮЧЕНО по решению владельца: конец слота снижает лимит, но подключённое
    # устройство панель пускает дальше. Взамен повторная докупка этой подписке не
    # предлагается (see eligibility → already_used).
    "remove_excess_devices": False,
    "notify_users": True,
    "notify_admins": True,
}

_LIMITS = {
    "price_rub_30d": (1, 100_000),
    "min_amount_rub": (1, 100_000),
    "min_days_left": (1, 30),
    "max_extra": (1, 10),
}


# ── конфиг ──────────────────────────────────────────────────────────────────


def _norm_int(raw: Any, default: int, bounds: tuple[int, int]) -> int:
    low, high = bounds
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return min(max(value, low), high)


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    price_raw = data.get("price_rub_30d", DEFAULT_CONFIG["price_rub_30d"])
    price: Optional[int]
    if price_raw is None or price_raw == "":
        price = None
    else:
        try:
            value = int(price_raw)
        except (TypeError, ValueError):
            price = DEFAULT_CONFIG["price_rub_30d"]
        else:
            # Ноль и отрицательная цена — это «продавать нельзя», а не «бесплатно»:
            # счёт на 0 ₽ шлюз не примет, а с баланса это была бы раздача мест.
            price = None if value <= 0 else min(value, _LIMITS["price_rub_30d"][1])
    # Минимум счёта НЕ зависит от цены: цены может не быть вовсе, а минимум шлюза есть.
    return {
        "enabled": bool(data.get("enabled", False)),
        "price_rub_30d": price,
        "min_amount_rub": _norm_int(
            data.get("min_amount_rub"), DEFAULT_CONFIG["min_amount_rub"], _LIMITS["min_amount_rub"]
        ),
        "min_days_left": _norm_int(
            data.get("min_days_left"), DEFAULT_CONFIG["min_days_left"], _LIMITS["min_days_left"]
        ),
        "max_extra": _norm_int(data.get("max_extra"), DEFAULT_CONFIG["max_extra"], _LIMITS["max_extra"]),
        "remove_excess_devices": bool(data.get("remove_excess_devices", False)),
        "notify_users": bool(data.get("notify_users", True)),
        "notify_admins": bool(data.get("notify_admins", True)),
    }


def load_config() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text("utf-8"))
    except FileNotFoundError:
        return dict(DEFAULT_CONFIG)
    except Exception as exc:  # noqa: BLE001 — битый конфиг не имеет права ронять оплату
        logger.warning(f"extra_device: конфиг не прочитан ({exc}) — беру дефолт")
        return dict(DEFAULT_CONFIG)
    if not isinstance(data, dict):
        return dict(DEFAULT_CONFIG)
    return _normalize(data)


def save_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize(config)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), "utf-8")
    return normalized


def effective_enabled(config: dict[str, Any]) -> bool:
    """Продаём ли сейчас. Тумблер без цены — это «не продаём»: цену ставит владелец."""
    return bool(config.get("enabled")) and bool(config.get("price_rub_30d"))


# ── состояние ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SlotRow:
    id: int
    subscription_id: int
    plan_id: int
    starts_at: datetime
    ends_at: datetime
    last_applied_at: datetime
    reminded_at: Optional[datetime] = None


@dataclass(frozen=True)
class UserState:
    user_id: int
    balance: Decimal
    sub_id: Optional[int]
    sub_status: Optional[str]
    is_trial: bool
    expire_at: Optional[datetime]
    device_limit: int
    plan_id: Optional[int]
    plan_device_limit: int
    remna_uuid: Optional[str]
    sub_updated_at: Optional[datetime]
    frozen_at: Optional[datetime]
    reserve_expire_at: Optional[datetime]
    slots: tuple[SlotRow, ...] = ()
    # Сколько слотов ЭТОЙ подписки уже отработали свой срок. Решение владельца:
    # второй раз докупку такой подписке не предлагаем — ведём на тариф побольше.
    ended_slots: int = 0

    @property
    def is_unlimited(self) -> bool:
        return self.expire_at is not None and self.expire_at.year >= UNLIMITED_YEAR

    def slot(self, slot_id: Optional[int]) -> Optional[SlotRow]:
        for row in self.slots:
            if row.id == slot_id:
                return row
        return None


@dataclass(frozen=True)
class Quote:
    kind: str
    amount: Decimal
    cov_start: datetime
    period_end: datetime
    days: int
    slot_id: Optional[int] = None


# ── чистые функции ──────────────────────────────────────────────────────────


def eligibility(
    st: UserState,
    config: dict[str, Any],
    now: datetime,
    kind: str,
    slot_id: Optional[int] = None,
    *,
    check_enabled: bool = True,
    check_used: bool = True,
) -> Optional[str]:
    """Почему НЕЛЬЗЯ (код причины) или None. Порядок проверок — часть смысла.

    `check_enabled=False` — для уже оплаченного заказа: деньги взяты, и выключенный
    в этот момент тумблер не повод их не отработать.
    `check_used=False` — там же: правило «второй раз не предлагаем» относится к
    витрине, а не к счёту, выставленному, когда предложение было.
    """
    if check_enabled and not effective_enabled(config):
        return "disabled"
    if st.sub_id is None or st.expire_at is None:
        return "no_subscription"
    if st.is_trial:
        return "trial"
    if st.is_unlimited:
        return "unlimited_term"
    # 0 — безлимит устройств: докупать нечего ни в базе, ни в тарифе.
    if st.device_limit == 0 or st.plan_device_limit <= 0:
        return "unlimited_devices"
    if st.frozen_at is not None:
        return "frozen"
    # Резерв сдвигает срок строки и оставляет статус ACTIVE: без этой проверки
    # истёкший на бесплатной страховке выглядел бы платящим, и мы продали бы ему
    # слот «до конца резерва».
    if st.reserve_expire_at is not None and st.expire_at <= st.reserve_expire_at + timedelta(hours=1):
        return "reserve"
    if (st.sub_status or "").upper() != "ACTIVE" or st.expire_at <= now:
        return "not_active"

    active = [s for s in st.slots if s.subscription_id == st.sub_id]
    if kind == "extend":
        slot = st.slot(slot_id)
        if slot is None or slot.subscription_id != st.sub_id or slot.ends_at <= now:
            return "nothing_to_extend"
        # Меньше суток разницы — продлевать нечего: счёт был бы на минимум ради часов.
        if (st.expire_at - slot.ends_at).total_seconds() < DAY:
            return "nothing_to_extend"
        return None

    if (st.expire_at - now).total_seconds() < int(config["min_days_left"]) * DAY:
        return "too_late"
    if check_used and st.ended_slots > 0:
        return "already_used"
    if len(active) >= int(config["max_extra"]):
        return "max_reached"
    return None


def quote(
    st: UserState,
    config: dict[str, Any],
    now: datetime,
    kind: str,
    slot_id: Optional[int] = None,
) -> Quote:
    """Цена по остатку. Считаем ДО СЕКУНДЫ и округляем вверх до рубля.

    Вверх — потому что цены витрины целые, а магазин на округлении выигрывает меньше
    рубля. По секундам, а не по суткам: сутки в пользу магазина — это до 3 ₽ разницы
    на коротком остатке, и человек видит цену, не совпадающую с «осталось N дней».
    """
    price = config.get("price_rub_30d")
    if not price:
        raise ValueError("цена докупки не задана")
    if kind == "extend":
        slot = st.slot(slot_id)
        if slot is None:
            raise ValueError("слот для продления не найден")
        cov_start = slot.ends_at
    else:
        cov_start = now
    period_end = st.expire_at
    if period_end is None:
        raise ValueError("у подписки нет срока")
    secs = max(0, int((period_end - cov_start).total_seconds()))
    raw = Fraction(int(price)) * secs / SEC30
    amount = max(int(config["min_amount_rub"]), math.ceil(raw))
    return Quote(
        kind=kind,
        amount=Decimal(amount),
        cov_start=cov_start,
        period_end=period_end,
        days=max(1, -(-secs // DAY)),
        slot_id=slot_id if kind == "extend" else None,
    )


def target_limit(cur: int, plan_lim: int, active_after: int, ended_k: int, reset: bool) -> int:
    """Каким должен стать лимит устройств.

    Пол `plan_lim + active_after` не даёт уйти ниже тарифа, когда лимит уже сбросили
    (RENEW), и не отнимает ручную щедрость админа: 6 при тарифе 3 станет 5, а не 3.
    """
    if cur == 0 or plan_lim <= 0:  # безлимит (админ или промокод) — не трогаем
        return cur
    if reset:
        return plan_lim + active_after
    return max(cur - ended_k, plan_lim + active_after)


def detect_reset(st: UserState, after_renew: bool = False) -> bool:
    """Сбросил ли кто-то лимит на тарифный, пока слоты действуют.

    `after_renew` передаёт хук продления: он ЗНАЕТ, что лимит только что переписан.
    Сравнение времени проиграло бы гонке «слот куплен за секунду до commit RENEW»,
    поэтому знание важнее времени. Само сравнение строгое, без запаса: база в UTC, а
    наш UPDATE ставит `updated_at` и `last_applied_at` одним `now()`.
    """
    active = [s for s in st.slots if s.subscription_id == st.sub_id]
    if not active or st.device_limit != st.plan_device_limit:
        return False
    if after_renew:
        return True
    if st.sub_updated_at is None:
        return False
    return st.sub_updated_at > max(s.last_applied_at for s in active)


def _device_created(device: Any) -> Optional[datetime]:
    return getattr(device, "created_at", None) or getattr(device, "updated_at", None)


def pick_excess(devices: Sequence[Any], limit: int, since: Optional[datetime]) -> list[Any]:
    """Какие устройства отключить, когда лимит снизился (настройка, по умолчанию ВЫКЛ).

    Решение владельца: трогаем ТОЛЬКО то, что человек подключил ПОСЛЕ покупки места, —
    аппараты, которые работали до неё, он оплачивал тарифом и терять их не должен. Из
    них — в обратном порядке подключения (самые новые первыми) и ровно столько, сколько
    сверх лимита. Кандидатов меньше, чем перебор, — снимаем сколько есть: панель пускает
    уже известный HWID без сверки с лимитом, и лишнее доживёт до следующей покупки.
    """
    if limit <= 0:
        return []
    k = len(devices) - limit
    if k <= 0:
        return []
    if since is None:
        return []
    fresh = [d for d in devices if (_device_created(d) or since) > since]
    fresh.sort(key=lambda d: (_device_created(d) or since), reverse=True)
    return fresh[:k]


def _as_dt(value: Any) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    return None


def carry_layers(orders: Iterable[dict], ref: datetime) -> list[tuple[Fraction, str, int, int]]:
    """Неиспользованная стоимость слотов → слои для переноса остатка при смене тарифа.

    Форма кортежа совпадает с `ParallelLayer(amount, currency, total_seconds,
    remaining_seconds)` соседнего сервиса — его мы не переписываем, только кормим.
    `ref` — точка отсчёта «уже прожито»: на паузе это `frozen_at`, иначе `now`.
    Продлённый слот приходит двумя заказами, у каждого своё окно; будущая часть
    продления (`cov_start > ref`) засчитывается целиком — она ещё не начиналась.
    """
    layers: list[tuple[Fraction, str, int, int]] = []
    for order in orders:
        amount = Fraction(Decimal(str(order["amount"])))
        cov_start = _as_dt(order["cov_start"])
        period_end = _as_dt(order["period_end"])
        if amount <= 0 or cov_start is None or period_end is None:
            continue
        total = int((period_end - cov_start).total_seconds())
        if total <= 0:
            continue
        used_from = max(ref, cov_start)
        remaining = int((period_end - used_from).total_seconds())
        remaining = min(max(remaining, 0), total)
        layers.append((amount, str(order.get("currency") or "RUB"), total, remaining))
    return layers


def unused_value(orders: Iterable[dict], ref: datetime) -> Decimal:
    """Сколько ₽ из оплаченного ещё не прожито (возврат админом). Вниз до копейки."""
    total = Fraction(0)
    for amount, _currency, total_seconds, remaining in carry_layers(orders, ref):
        total += amount * remaining / total_seconds
    value = Decimal(total.numerator) / Decimal(total.denominator)
    return value.quantize(Decimal("0.01"), rounding=ROUND_DOWN)


def synthetic_snapshot(days: int) -> Any:
    """Снимок «тарифа» для счёта. Ленивый импорт: application-слой — не модульный."""
    from src.application.dto import PlanSnapshotDto
    from src.core.enums import PlanType

    return PlanSnapshotDto(
        id=SYNTHETIC_PLAN_ID,
        name="Дополнительное устройство",
        type=PlanType.UNLIMITED,
        traffic_limit=0,
        device_limit=1,
        duration=max(1, int(days)),
        is_trial=False,
    )


# ── SQL ─────────────────────────────────────────────────────────────────────

# Замок берём на строке users (не на подписке): её же запирает перенос остатка при
# смене тарифа, и общий замок делает «купил слот» и «сменил тариф» взаимно
# последовательными. LEFT JOIN — чтобы человек без подписки не проваливал запрос.
LOCK_STATE_SQL = (
    "SELECT u.cabinet_balance, s.id, s.status::text, s.is_trial, s.expire_at, s.device_limit, "
    "(s.plan_snapshot->>'id')::int, (s.plan_snapshot->>'device_limit')::int, "
    "s.user_remna_id, s.updated_at "
    "FROM users u LEFT JOIN subscriptions s ON s.id = u.current_subscription_id "
    "WHERE u.id = :uid FOR UPDATE OF u"
)

# То же самое без замка — для показа (кнопка в боте, витрина): держать строку под
# FOR UPDATE ради рисования кнопки незачем.
READ_STATE_SQL = LOCK_STATE_SQL.replace(" FOR UPDATE OF u", "")

ACTIVE_SLOTS_SQL = (
    "SELECT id, subscription_id, plan_id, starts_at, ends_at, last_applied_at, reminded_at "
    "FROM extra_device_slots WHERE user_id = :uid AND status = 'active' ORDER BY ends_at"
)

# Пауза и резерв одним запросом: оба — причины не продавать (см. eligibility).
PAUSE_RESERVE_SQL = (
    "SELECT (SELECT frozen_at FROM subscription_freezes WHERE user_id = :uid AND active = true), "
    "(SELECT max(reserve_expire_at) FROM reserve_grants "
    " WHERE user_id = :uid AND ended = false AND reserve_expire_at > now())"
)

ENDED_SLOTS_SQL = (
    "SELECT count(*) FROM extra_device_slots "
    "WHERE user_id = :uid AND subscription_id = :sid AND status = 'ended'"
)

# Стоимость действующих слотов подписки — то, что перенос остатка превращает в дни.
CARRY_ORDERS_SQL = (
    "SELECT o.amount, o.currency, o.cov_start, o.period_end, o.slot_id "
    "FROM extra_device_orders o JOIN extra_device_slots s ON s.id = o.slot_id "
    "WHERE s.subscription_id = :sid AND s.status = 'active' AND o.status = 'applied'"
)

SPEND_SQL = (
    "UPDATE users SET cabinet_balance = cabinet_balance - :amount "
    "WHERE id = :uid AND cabinet_balance >= :amount RETURNING cabinet_balance"
)

SPEND_SQL_TEXT = text(SPEND_SQL)

ORDER_BY_REQUEST_SQL = (
    "SELECT id, user_id, status, kind, amount, payment_id, payment_url, slot_id, period_end "
    "FROM extra_device_orders WHERE request_id = :rid"
)

ORDER_FOR_UPDATE_SQL = (
    "SELECT id, user_id, subscription_id, slot_id, status, kind, amount, cov_start, period_end, "
    "attempts, created_at, payment_id, price_per_30d "
    "FROM extra_device_orders WHERE payment_id = :pid FOR UPDATE"
)


def _row_slot(row: Any) -> SlotRow:
    return SlotRow(
        id=int(row[0]),
        subscription_id=int(row[1]),
        plan_id=int(row[2]),
        starts_at=row[3],
        ends_at=row[4],
        last_applied_at=row[5],
        reminded_at=row[6],
    )


async def lock_state(session: "AsyncSession", user_id: int, *, lock: bool = True) -> UserState:
    """Замок строки человека + всё состояние сырым SQL.

    Сырым, а не через ORM: сессия живёт с `expire_on_commit=False`, и объект из
    identity map под замком оказался бы устаревшим — ровно тем снимком, из-за
    которого мы и не шлём в панель полное тело.

    `lock=False` — только показать (кнопка в боте): менять ничего не будем, и держать
    строку человека запертой всё время отрисовки не за что.
    """
    sql = LOCK_STATE_SQL if lock else READ_STATE_SQL
    row = (await session.execute(text(sql), {"uid": user_id})).first()
    if row is None:
        raise ValueError(f"пользователь id={user_id} не найден")
    balance = Decimal(str(row[0] if row[0] is not None else 0))
    sub_id = int(row[1]) if row[1] is not None else None
    slots = [
        _row_slot(r) for r in (await session.execute(text(ACTIVE_SLOTS_SQL), {"uid": user_id})).all()
    ]
    pause = (await session.execute(text(PAUSE_RESERVE_SQL), {"uid": user_id})).first()
    ended = 0
    if sub_id is not None:
        ended = int(
            (await session.execute(text(ENDED_SLOTS_SQL), {"uid": user_id, "sid": sub_id})).scalar() or 0
        )
    return UserState(
        user_id=user_id,
        balance=balance,
        sub_id=sub_id,
        sub_status=row[2],
        is_trial=bool(row[3]),
        expire_at=row[4],
        device_limit=int(row[5] or 0),
        plan_id=int(row[6]) if row[6] is not None else None,
        plan_device_limit=int(row[7] or 0),
        remna_uuid=str(row[8]) if row[8] else None,
        sub_updated_at=row[9],
        frozen_at=pause[0] if pause else None,
        reserve_expire_at=pause[1] if pause else None,
        slots=tuple(slots),
        ended_slots=ended,
    )


async def order_by_request(session: "AsyncSession", request_id: Any) -> Optional[dict]:
    row = (await session.execute(text(ORDER_BY_REQUEST_SQL), {"rid": str(request_id)})).first()
    if row is None:
        return None
    return {
        "id": int(row[0]),
        "user_id": int(row[1]),
        "status": row[2],
        "kind": row[3],
        "amount": Decimal(str(row[4])),
        "payment_id": row[5],
        "payment_url": row[6],
        "slot_id": row[7],
        "period_end": row[8],
    }


async def order_for_update(session: "AsyncSession", payment_id: Any) -> Optional[dict]:
    row = (await session.execute(text(ORDER_FOR_UPDATE_SQL), {"pid": str(payment_id)})).first()
    if row is None:
        return None
    return {
        "id": int(row[0]),
        "user_id": int(row[1]),
        "subscription_id": int(row[2]),
        "slot_id": row[3],
        "status": row[4],
        "kind": row[5],
        "amount": Decimal(str(row[6])),
        "cov_start": row[7],
        "period_end": row[8],
        "attempts": int(row[9] or 0),
        "created_at": row[10],
        "payment_id": row[11],
        "price_per_30d": Decimal(str(row[12])) if row[12] is not None else None,
    }


async def load_carry_orders(session: "AsyncSession", subscription_id: int) -> list[dict]:
    rows = (await session.execute(text(CARRY_ORDERS_SQL), {"sid": subscription_id})).all()
    return [
        {
            "amount": Decimal(str(r[0])),
            "currency": str(r[1] or "RUB"),
            "cov_start": r[2],
            "period_end": r[3],
            "slot_id": r[4],
        }
        for r in rows
    ]


# ── панель ──────────────────────────────────────────────────────────────────


class PanelRejected(RuntimeError):
    """Панель не приняла новый лимит (ошибка или вернула не то число)."""


class SlotChanged(RuntimeError):
    """Слот продления изменился между расчётом цены и записью."""


async def set_limit(session: "AsyncSession", sdk: Any, st: UserState, new_limit: int) -> None:
    """Лимит в базу и в панель УЗКИМ телом. Без commit — им распоряжается вызвавший.

    Тело PATCH — ровно `{uuid, hwidDeviceLimit}`: `model_dump(exclude_unset=True)`
    remnapy не добавит ни срока, ни сквадов, а значит нечему и откатить панель к
    устаревшему снимку базы. Ответ сверяем: молчаливое «принял, но не применил»
    оставило бы человека с оплаченным, но не работающим местом.
    """
    await session.execute(
        text("UPDATE subscriptions SET device_limit = :n, updated_at = now() WHERE id = :sid"),
        {"n": int(new_limit), "sid": st.sub_id},
    )
    if not st.remna_uuid:
        raise PanelRejected("у подписки нет идентификатора в панели")

    from remnapy.models import UpdateUserRequestDto

    try:
        updated = await sdk.users.update_user(
            UpdateUserRequestDto(uuid=str(st.remna_uuid), hwid_device_limit=int(new_limit))
        )
    except Exception as exc:  # noqa: BLE001 — наверх уходит одна понятная ошибка
        raise PanelRejected(f"{type(exc).__name__}: {exc}") from exc
    got = getattr(updated, "hwid_device_limit", None)
    if got is None or int(got) != int(new_limit):
        raise PanelRejected(f"панель вернула лимит {got}, ожидали {new_limit}")


# ── покупка ─────────────────────────────────────────────────────────────────


def new_limit_for_purchase(st: UserState) -> int:
    """Лимит после покупки одного места: пол «тариф + действующие», затем +1.

    Пол нужен на случай, когда лимит уже сброшен продлением, а восстановление ещё не
    прошло: иначе человек заплатил бы за место, которого не прибавилось.
    """
    active = len([s for s in st.slots if s.subscription_id == st.sub_id])
    return max(st.device_limit, st.plan_device_limit + active) + 1


async def buy_from_balance(
    session: "AsyncSession",
    *,
    sdk: Any,
    transaction_dao: Any,
    gateway_type: Any,
    st: UserState,
    q: Quote,
    request_id: Any,
    source: str,
    price_per_30d: int,
    order_id: Optional[int] = None,
    payment_id: Any = None,
) -> dict:
    """Списать, записать слот и заказ, поднять лимит. БЕЗ commit — его делает вызвавший.

    Порядок шагов важен: деньги списываются условным UPDATE (без «сначала прочитали,
    потом списали»), панель идёт ПОСЛЕДНЕЙ, и любая её ошибка откатывает всю
    транзакцию — списания, транзакции, слота и лимита не останется.
    """
    import uuid as _uuid

    from src.application.dto import PriceDetailsDto, TransactionDto
    from src.core.enums import Currency, PurchaseType, TransactionStatus

    amount = q.amount
    new_balance = (
        await session.execute(SPEND_SQL_TEXT, {"amount": amount, "uid": st.user_id})
    ).scalar()
    if new_balance is None:
        return {"result": "insufficient_balance", "need": amount, "balance": st.balance}

    transaction = None
    if payment_id is None:
        # Оплата с баланса: своя завершённая транзакция — её видят «Потрачено»,
        # выручка и история кабинета, как у обычной покупки с баланса.
        transaction = TransactionDto(
            payment_id=_uuid.uuid4(),
            user_id=st.user_id,
            status=TransactionStatus.COMPLETED,
            purchase_type=PurchaseType.NEW,
            gateway_type=gateway_type,
            gateway_display_name="Баланс · устройство",
            pricing=PriceDetailsDto(original_amount=amount, discount_percent=0, final_amount=amount),
            currency=Currency.RUB,
            plan_snapshot=synthetic_snapshot(q.days),
        )
        await transaction_dao.create(transaction)
        payment_id = transaction.payment_id

    if q.kind == "extend":
        updated = (
            await session.execute(
                text(
                    "UPDATE extra_device_slots SET ends_at = :end "
                    "WHERE id = :id AND status = 'active' AND ends_at = :cov RETURNING id"
                ),
                {"end": q.period_end, "id": q.slot_id, "cov": q.cov_start},
            )
        ).scalar()
        if updated is None:
            # Слот успел кончиться или сдвинуться между расчётом и записью.
            raise SlotChanged("слот изменился, пока считали цену")
        slot_id = int(updated)
    else:
        slot_id = int(
            (
                await session.execute(
                    text(
                        "INSERT INTO extra_device_slots "
                        "(user_id, subscription_id, plan_id, status, starts_at, ends_at, last_applied_at) "
                        "VALUES (:uid, :sid, :pid, 'active', :start, :end, now()) RETURNING id"
                    ),
                    {
                        "uid": st.user_id,
                        "sid": st.sub_id,
                        "pid": st.plan_id if st.plan_id is not None else -1,
                        "start": q.cov_start,
                        "end": q.period_end,
                    },
                )
            ).scalar()
        )

    if order_id is None:
        await session.execute(
            text(
                "INSERT INTO extra_device_orders "
                "(request_id, payment_id, source, kind, user_id, subscription_id, slot_id, status, "
                " amount, currency, price_per_30d, cov_start, period_end, applied_at) "
                "VALUES (:rid, :pid, :src, :kind, :uid, :sid, :slot, 'applied', :amount, 'RUB', "
                " :price, :cov, :end, now())"
            ),
            {
                "rid": str(request_id),
                "pid": str(payment_id),
                "src": source,
                "kind": q.kind,
                "uid": st.user_id,
                "sid": st.sub_id,
                "slot": slot_id,
                "amount": amount,
                "price": price_per_30d,
                "cov": q.cov_start,
                "end": q.period_end,
            },
        )
    else:
        await session.execute(
            text(
                "UPDATE extra_device_orders SET status = 'applied', applied_at = now(), "
                "slot_id = :slot, cov_start = :cov WHERE id = :id"
            ),
            {"slot": slot_id, "cov": q.cov_start, "id": order_id},
        )

    limit_before = st.device_limit
    if q.kind == "new":
        target = new_limit_for_purchase(st)
        await set_limit(session, sdk, st, target)
        await session.execute(
            text(
                "UPDATE extra_device_slots SET last_applied_at = now() "
                "WHERE subscription_id = :sid AND status = 'active'"
            ),
            {"sid": st.sub_id},
        )
    else:
        # Продление не добавляет места — лимит уже стоит, панель трогать незачем.
        target = limit_before

    return {
        "result": "applied",
        "slot_id": slot_id,
        "device_limit": target,
        "limit_before": limit_before,
        "until": q.period_end,
        "spent": amount,
        "balance": Decimal(str(new_balance)),
        "payment_id": payment_id,
        "transaction": transaction,
    }


# ── оплата картой ───────────────────────────────────────────────────────────


async def record_order(
    session: "AsyncSession",
    *,
    request_id: Any,
    payment_id: Any,
    payment_url: Optional[str],
    user_id: int,
    st: UserState,
    q: Quote,
    price_per_30d: int,
) -> bool:
    """Заказ `pending` ДО отдачи ссылки. False — ключ уже занят двойником."""
    inserted = (
        await session.execute(
            text(
                "INSERT INTO extra_device_orders "
                "(request_id, payment_id, source, kind, user_id, subscription_id, slot_id, status, "
                " amount, currency, price_per_30d, cov_start, period_end, payment_url) "
                "VALUES (:rid, :pid, 'gateway', :kind, :uid, :sid, :slot, 'pending', :amount, 'RUB', "
                " :price, :cov, :end, :url) ON CONFLICT (request_id) DO NOTHING RETURNING id"
            ),
            {
                "rid": str(request_id),
                "pid": str(payment_id),
                "kind": q.kind,
                "uid": user_id,
                "sid": st.sub_id,
                "slot": q.slot_id,
                "amount": q.amount,
                "price": price_per_30d,
                "cov": q.cov_start,
                "end": q.period_end,
                "url": payment_url,
            },
        )
    ).scalar()
    await session.commit()
    return inserted is not None


async def credit_order(session: "AsyncSession", order: dict) -> None:
    """Деньги счёта — на ₽-баланс. Отдельным шагом и со своим commit.

    Так «получили деньги» и «выдали место» не зависят друг от друга: даже если панель
    не ответит вовсе, человек не потеряет оплату — она лежит на балансе.
    """
    await session.execute(
        text("UPDATE users SET cabinet_balance = cabinet_balance + :a WHERE id = :u"),
        {"a": order["amount"], "u": order["user_id"]},
    )
    await session.execute(
        text(
            "UPDATE extra_device_orders SET status = 'credited', credited_at = now() "
            "WHERE id = :id AND status = 'pending'"
        ),
        {"id": order["id"]},
    )
    await session.commit()


async def reject_order(session: "AsyncSession", order_id: int, reason: str) -> None:
    await session.execute(
        text(
            "UPDATE extra_device_orders SET status = 'rejected', reason = :r, rejected_at = now() "
            "WHERE id = :id AND status IN ('pending', 'credited')"
        ),
        {"r": reason[:32], "id": order_id},
    )
    await session.commit()


async def note_attempt(session: "AsyncSession", order_id: int, error: str) -> None:
    await session.execute(
        text(
            "UPDATE extra_device_orders SET attempts = attempts + 1, last_error = :e WHERE id = :id"
        ),
        {"e": error[:300], "id": order_id},
    )
    await session.commit()


async def apply_order(
    session: "AsyncSession",
    *,
    sdk: Any,
    config: dict[str, Any],
    order: dict,
    now: datetime,
) -> dict:
    """Оплаченный заказ → слот. Замок заказа уже взят вызвавшим (порядок: заказ → users).

    Порядок замков «строка заказа, затем строка users» одинаков в вебхуке и в кроне:
    обратный порядок дал бы взаимоблокировку двух процессов на одном человеке.
    """
    st = await lock_state(session, order["user_id"])
    why = eligibility(
        st, config, now, order["kind"], order["slot_id"], check_enabled=False, check_used=False
    )
    if why is None:
        created = _as_dt(order["created_at"])
        if created is not None and (now - created).total_seconds() > STALE_HOURS * 3600:
            why = "stale"
        elif st.sub_id != order["subscription_id"]:
            why = "subscription_changed"
        elif st.expire_at is None or st.expire_at < order["period_end"] - timedelta(hours=1):
            why = "subscription_shortened"
        elif order["kind"] == "extend":
            slot = st.slot(order["slot_id"])
            if slot is None or slot.ends_at != order["cov_start"]:
                why = "slot_changed"
    if why is not None:
        await session.rollback()
        await reject_order(session, order["id"], why)
        return {"result": "rejected", "reason": why}

    # Цена уже оплачена — пересчёт запрещён: берём окно из заказа. Для нового слота
    # покрытие начинается СЕЙЧАС (за время ожидания вебхука часть периода прошла, и
    # брать деньги за неё второй раз нечестно, а сдвигать конец — некому платить).
    cov_start = now if order["kind"] == "new" else order["cov_start"]
    q = Quote(
        kind=order["kind"],
        amount=order["amount"],
        cov_start=cov_start,
        period_end=order["period_end"],
        days=max(1, -(-int((order["period_end"] - cov_start).total_seconds()) // DAY)),
        slot_id=order["slot_id"],
    )
    try:
        result = await buy_from_balance(
            session,
            sdk=sdk,
            transaction_dao=None,
            gateway_type=None,
            st=st,
            q=q,
            request_id=None,
            source="gateway",
            price_per_30d=int(order.get("price_per_30d") or 0),
            order_id=order["id"],
            payment_id=order["payment_id"],
        )
    except SlotChanged:
        await session.rollback()
        await reject_order(session, order["id"], "slot_changed")
        return {"result": "rejected", "reason": "slot_changed"}
    if result.get("result") == "insufficient_balance":
        # Деньги зачислены, но человек успел потратить их сам (продление, автоплатёж).
        await session.rollback()
        await reject_order(session, order["id"], "balance_spent")
        return {"result": "rejected", "reason": "balance_spent"}
    await session.commit()
    return result


async def handle_paid_order(
    session: "AsyncSession", sdk: Any, payment_id: Any, now: Optional[datetime] = None
) -> Optional[dict]:
    """Вебхук шлюза: это наш счёт? Тогда деньги — на баланс, затем место.

    None — счёт не наш, обработчик оплаты идёт дальше своим путём. Два шага с
    отдельными commit намеренно: зачисление не должно зависеть от того, ответит ли
    панель. Идемпотентность держат замок строки заказа и переходы только вперёд
    (`pending → credited → applied|rejected`).
    """
    now = now or now_utc()
    order = await order_for_update(session, payment_id)
    if order is None:
        return None
    if order["status"] == "pending":
        await credit_order(session, order)
        order = await order_for_update(session, payment_id) or order
    if order["status"] != "credited":
        # Уже применён или отклонён — повтор вебхука ничего не меняет.
        await session.rollback()
        return {"result": order["status"], "order": order, "repeat": True}
    result = await apply_order(session, sdk=sdk, config=load_config(), order=order, now=now)
    result["order"] = order
    return result


async def order_by_payment(session: "AsyncSession", payment_id: Any) -> Optional[dict]:
    """Тот же заказ, но БЕЗ замка — для алертов после чужого commit (возврат)."""
    row = (
        await session.execute(
            text(
                "SELECT o.id, o.status, o.amount, o.slot_id, o.period_end, o.user_id "
                "FROM extra_device_orders o WHERE o.payment_id = :pid"
            ),
            {"pid": str(payment_id)},
        )
    ).first()
    if row is None:
        return None
    return {
        "id": int(row[0]),
        "status": row[1],
        "amount": Decimal(str(row[2])),
        "slot_id": row[3],
        "period_end": row[4],
        "user_id": int(row[5]),
    }


# ── жизнь слотов ────────────────────────────────────────────────────────────


async def burn_for_change(
    session: "AsyncSession", old_subscription_id: int, carried: Optional[Decimal] = None
) -> int:
    """Слоты старой строки сгорают при смене тарифа: лимит ставит новый тариф.

    Стоимость не пропадает — она уже посчитана переносом остатка в дни (carry_layers),
    и сюда приходит числом только ради журнала и разбора с владельцем.
    """
    rows = (
        await session.execute(
            text(
                "UPDATE extra_device_slots SET status = 'burned', end_reason = 'change', "
                "ended_at = now(), carried_value = :v "
                "WHERE subscription_id = :sid AND status = 'active' RETURNING id"
            ),
            {"v": carried, "sid": old_subscription_id},
        )
    ).all()
    return len(rows)


async def shift_on_unfreeze(session: "AsyncSession", user_id: int, frozen_at: Any) -> None:
    """Пауза сдвигает конец слота ровно на свою длину — как и срок самой подписки."""
    if frozen_at is None:
        return
    await session.execute(
        text(
            "UPDATE extra_device_slots SET ends_at = ends_at + (now() - :frozen) "
            "WHERE user_id = :uid AND status = 'active'"
        ),
        {"frozen": frozen_at, "uid": user_id},
    )


async def reconcile_user(
    session: "AsyncSession",
    *,
    sdk: Any,
    remnawave: Any,
    user_id: int,
    config: dict[str, Any],
    now: datetime,
    after_renew: bool = False,
    mode: str = "full",
) -> dict:
    """Свести лимит с действующими слотами: кончить просроченные, вернуть сброшенное.

    `mode="reapply_only"` — вызов из денежного пути (сразу после продления): только
    восстановление лимита, без конца слотов, отключений и напоминаний. Лишние вызовы
    панели в оплаченной выдаче не нужны — остальное доделает крон.
    """
    st = await lock_state(session, user_id)
    out: dict[str, Any] = {"ended": [], "burned": [], "limit": st.device_limit, "reminded": []}
    if st.sub_id is None:
        await session.rollback()
        return out

    ended_ids: list[int] = []
    burned: list[tuple[int, str]] = []
    active_after: list[SlotRow] = []
    ended_since: Optional[datetime] = None
    for slot in st.slots:
        if slot.subscription_id != st.sub_id:
            burned.append((slot.id, "subscription_replaced"))
            continue
        if st.plan_id is not None and slot.plan_id != st.plan_id:
            burned.append((slot.id, "plan_replaced"))
            continue
        if mode != "reapply_only" and st.frozen_at is None and slot.ends_at <= now:
            ended_ids.append(slot.id)
            ended_since = slot.starts_at if ended_since is None else min(ended_since, slot.starts_at)
            continue
        active_after.append(slot)

    if mode == "reapply_only":
        # Сгоревшие слоты (другая строка/тариф) пометить всё же нужно: иначе они
        # вечно участвовали бы в поле лимита.
        ended_ids = []

    reset = detect_reset(st, after_renew=after_renew)
    target = target_limit(st.device_limit, st.plan_device_limit, len(active_after), len(ended_ids), reset)

    for slot_id, reason in burned:
        await session.execute(
            text(
                "UPDATE extra_device_slots SET status = 'burned', end_reason = :r, ended_at = now() "
                "WHERE id = :id AND status = 'active'"
            ),
            {"r": reason, "id": slot_id},
        )
    if ended_ids:
        await session.execute(
            text(
                "UPDATE extra_device_slots SET status = 'ended', end_reason = 'expired', "
                "ended_at = now(), removal_done = :done WHERE id = ANY(:ids)"
            ),
            {"ids": ended_ids, "done": not bool(config.get("remove_excess_devices"))},
        )

    changed = False
    deleted_panel = (st.sub_status or "").upper() == "DELETED"
    if target != st.device_limit and not deleted_panel:
        try:
            await set_limit(session, sdk, st, target)
            changed = True
        except PanelRejected as exc:
            if "NotFound" in str(exc):
                # Пользователя в панели нет (удалён): база остаётся источником правды,
                # слоты кончаем, но повторять вечно нечего.
                logger.warning(f"extra_device: user_id={user_id} — в панели нет, правлю только базу")
                await session.execute(
                    text("UPDATE subscriptions SET device_limit = :n, updated_at = now() WHERE id = :sid"),
                    {"n": target, "sid": st.sub_id},
                )
                out["panel_missing"] = True
                changed = True
            else:
                await session.rollback()
                if ended_ids or burned:
                    await _bump_fail(session, ended_ids or [s for s, _ in burned])
                out["error"] = str(exc)
                return out
    if changed or active_after:
        await session.execute(
            text(
                "UPDATE extra_device_slots SET last_applied_at = now(), fail_count = 0 "
                "WHERE subscription_id = :sid AND status = 'active'"
            ),
            {"sid": st.sub_id},
        )
    await session.commit()

    out["ended"] = ended_ids
    out["burned"] = burned
    out["limit"] = target
    out["limit_before"] = st.device_limit
    out["ended_since"] = ended_since
    out["plan_device_limit"] = st.plan_device_limit
    out["expire_at"] = st.expire_at
    out["remna_uuid"] = st.remna_uuid
    out["frozen"] = st.frozen_at is not None

    if mode != "reapply_only" and ended_ids and config.get("remove_excess_devices") and not deleted_panel:
        out["removed"] = await _remove_excess(
            session, remnawave=remnawave, st=st, limit=target, since=ended_since, slot_ids=ended_ids
        )

    if mode != "reapply_only":
        out["reminded"] = await _claim_reminders(session, st, active_after, now)
    return out


async def _bump_fail(session: "AsyncSession", slot_ids: Sequence[int]) -> None:
    if not slot_ids:
        return
    await session.execute(
        text("UPDATE extra_device_slots SET fail_count = fail_count + 1 WHERE id = ANY(:ids)"),
        {"ids": list(slot_ids)},
    )
    await session.commit()


async def _remove_excess(
    session: "AsyncSession",
    *,
    remnawave: Any,
    st: UserState,
    limit: int,
    since: Optional[datetime],
    slot_ids: Sequence[int],
) -> list[dict]:
    """Отключить лишние устройства (настройка, по умолчанию ВЫКЛ). После commit лимита.

    Порядок именно такой: лимит уже снижен и записан, а неудача отключения — это
    `removal_done=false` и повтор следующим проходом, а не потерянный commit.
    """
    removed: list[dict] = []
    try:
        devices = await remnawave.get_devices(st.remna_uuid) or []
        for device in pick_excess(devices, limit, since):
            await remnawave.delete_device(st.remna_uuid, device.hwid)
            removed.append(
                {
                    "hwid": device.hwid,
                    "name": (getattr(device, "device_model", None) or getattr(device, "platform", None) or "")[:32],
                }
            )
        await session.execute(
            text(
                "UPDATE extra_device_slots SET removal_done = true, devices_removed = :n "
                "WHERE id = ANY(:ids)"
            ),
            {"n": len(removed), "ids": list(slot_ids)},
        )
        await session.commit()
    except Exception as exc:  # noqa: BLE001 — лимит уже снижен, повторим следующим проходом
        await session.rollback()
        logger.warning(f"extra_device: не удалил лишние устройства user_id={st.user_id}: {exc}")
    return removed


async def _claim_reminders(
    session: "AsyncSession", st: UserState, active: Sequence[SlotRow], now: datetime
) -> list[dict]:
    """Напоминание за 3 дня — с захватом строки: повторный проход не шлёт второго."""
    claimed: list[dict] = []
    if st.frozen_at is not None or st.expire_at is None:
        return claimed
    for slot in active:
        if slot.reminded_at is not None:
            continue
        if (slot.ends_at - now).total_seconds() > REMIND_DAYS * DAY:
            continue
        # Напоминаем, только если подписка переживёт слот: иначе человеку не о чем
        # беспокоиться — лимит и подписка кончатся вместе.
        if st.expire_at <= slot.ends_at + timedelta(days=1):
            continue
        got = (
            await session.execute(
                text(
                    "UPDATE extra_device_slots SET reminded_at = now() "
                    "WHERE id = :id AND reminded_at IS NULL RETURNING id"
                ),
                {"id": slot.id},
            )
        ).scalar()
        if got is not None:
            claimed.append({"slot_id": slot.id, "ends_at": slot.ends_at})
    if claimed:
        await session.commit()
    return claimed


# ── тексты ──────────────────────────────────────────────────────────────────

_REASON_RU = {
    "subscription_changed": "тариф сменился",
    "subscription_shortened": "срок подписки изменился",
    "not_active": "подписка не активна",
    "no_subscription": "подписки нет",
    "frozen": "подписка на паузе",
    "reserve": "подписка закончилась",
    "trial": "это пробная подписка",
    "unlimited_term": "у подписки нет срока",
    "unlimited_devices": "у подписки нет лимита устройств",
    "max_reached": "докуплено максимум устройств",
    "already_used": "докупка к этой подписке уже была",
    "slot_changed": "докупка уже закончилась",
    "nothing_to_extend": "продлевать нечего",
    "too_late": "до конца срока слишком мало времени",
    "stale": "с момента счёта прошло больше суток",
    "panel_timeout": "сервер не ответил",
    "balance_spent": "деньги уже потрачены",
}


def reason_ru(code: str) -> str:
    return _REASON_RU.get(code, code)


def _dm(value: Any) -> str:
    return value.strftime("%d.%m.%Y") if isinstance(value, datetime) else str(value)


def user_text(key: str, **kw: Any) -> str:
    """Сообщения человеку. Python-строки, не ftl: фигурные скобки Fluent тут ни к чему."""
    if key == "applied":
        return (
            f"✅ Устройство добавлено: теперь можно подключить {kw['limit']} устр. "
            f"Докупка действует до {_dm(kw['until'])}."
        )
    if key == "not_applied":
        return (
            f"💳 Оплата {kw['amount']} ₽ получена, но добавить устройство не получилось: "
            f"{reason_ru(kw['reason'])}. Деньги лежат на балансе в кабинете — там же можно "
            "докупить одной кнопкой или продлить подписку."
        )
    if key == "balance_spent":
        return (
            f"💳 Оплата {kw['amount']} ₽ зачислена на баланс и уже использована, "
            "поэтому устройство не добавлено."
        )
    if key == "reminder":
        return (
            f"⏳ Докупленное устройство работает до {_dm(kw['ends_at'])}, а подписка — до "
            f"{_dm(kw['until'])}. Продлите устройство в кабинете → «Устройства», иначе лимит "
            f"вернётся к {kw['limit']}."
        )
    if key == "ended":
        removed = ""
        if kw.get("removed"):
            names = ", ".join(f"«{n}»" for n in kw["removed"] if n) or "лишнее устройство"
            removed = f"Отключили {names} — они подключены после покупки места. "
        return (
            f"Срок докупленного устройства закончился — теперь можно подключить "
            f"{kw['limit']} устр. {removed}Если мест не хватает, в кабинете есть тариф побольше."
        )
    raise KeyError(key)


def admin_text(key: str, **kw: Any) -> str:
    """Сообщения владельцу. Все — после commit и в try: они не участвуют в деньгах."""
    if key == "bought":
        kind = "+1 до" if kw["kind"] == "new" else "продление до"
        source = "баланс" if kw["source"] == "balance" else "шлюз"
        return (
            "🧩 <b>Докупка устройства</b>\n"
            f"{kw['user']}\n{kind} {_dm(kw['until'])}\n{kw['amount']} ₽ · {source}"
        )
    if key == "rejected":
        return (
            "⚠️ <b>Докупка не применена</b>\n"
            f"{kw['user']}\nОплата {kw['amount']} ₽ (<code>{kw['payment_id']}</code>) "
            f"на балансе: {reason_ru(kw['reason'])}."
        )
    if key == "refunded":
        tail = (
            f"Слот #{kw['slot_id']} действует до {_dm(kw['until'])}. "
            "Отменить: Пользователи → карточка → «Отменить докупку»."
            if kw.get("slot_id")
            else "Сумма зачислена на баланс — спишите вручную."
        )
        return (
            "⚠️ <b>Возврат по докупке устройства</b>\n"
            f"{kw['user']}\nСчёт <code>{kw['payment_id']}</code>. {tail}"
        )
    if key == "cron_failed":
        what = "снять лимит" if kw["what"] == "limit" else "отключить лишние устройства"
        return (
            f"⚠️ <b>Докупка: не удалось {what}</b>\n{kw['user']}\n"
            f"Слот #{kw['slot_id']}: {kw['error']}."
        )
    if key == "plan_replaced":
        return (
            "⚠️ <b>Докупка сгорела при замене тарифа</b>\n"
            f"{kw['user']}\nСлот #{kw['slot_id']} до {_dm(kw['until'])}, тариф в строке сменился "
            "без переноса. Если это подарок или выдача — возместите вручную."
        )
    if key == "commit_failed":
        return (
            "🚨 <b>Докупка: commit упал после панели</b>\n"
            f"{kw['user']}\nЛимит в панели откатываем на {kw['limit']}. Проверьте карточку."
        )
    raise KeyError(key)


def process_name() -> str:
    """Чей это процесс — для логов о непересобранном воркере."""
    import sys

    return Path(sys.argv[0]).name or "python"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
