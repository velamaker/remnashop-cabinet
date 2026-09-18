"""Докупка «+N ГБ к текущему окну трафика» (overlay).

ЧТО ПРОДАЁМ. Разовую прибавку к лимиту трафика ТЕКУЩЕГО окна: +50 ГБ за 50 ₽ по
умолчанию, цена ФИКСИРОВАННАЯ и от времени не зависит (в отличие от места под
устройство, которое продаётся периодом и считается пропорционально остатку).

ДО КАКОГО МОМЕНТА ЖИВЁТ — ГЛАВНОЕ ПРАВИЛО. До ближайшего обнуления расхода
панелью, а не «до конца подписки». Панель сбрасывает РАСХОД по своему расписанию
(у боевых тарифов — MONTH_ROLLING, то есть каждый месяц ВНУТРИ оплаченного
периода), но ЛИМИТ при этом не трогает. Оставить лимит поднятым до конца подписки
значило бы дарить +50 ГБ каждый месяц за один платёж — на годовой подписке
двенадцать раз. Поэтому момент конца считает next_traffic_reset() — наша копия
формулы панели (scheduler.js), а не базовая core.utils.time.get_traffic_reset_delta:
у той другие часы и другой якорь (см. её починку в overlay_patches/traffic_reset).

ПОЧЕМУ ОТДЕЛЬНЫЙ СЕРВИС, А НЕ ОБЩИЙ С overlay_extra_device. Единицы разные:
устройство — это ВРЕМЯ (слот 30 дней, продление, снятие лишних аппаратов), трафик —
ОБЪЁМ (продлевать нечего, отключать нечего). И граница жизни разная: у слота конец
назначаем мы, у прибавки — панель, и он может сдвинуться, если админ сменит
стратегию. Общие у них только конечный автомат заказа (pending → credited →
applied|rejected) и синтетический снимок тарифа — это осознанный дубль
overlay_extra_device.py; извлекать общий журнал докупок имеет смысл, когда появится
третья, а не вторая.

ПОЧЕМУ ДЕНЬГИ ИДУТ ЧЕРЕЗ БАЛАНС. Оплата картой зачисляется на ₽-баланс и ТЕМ ЖЕ
кодом, что и покупка с баланса, превращается в прибавку. Отдельного возврата в
шлюз, второй идемпотентности и состояния «оплачено, но не выдано и не возвращено»
в коде нет.

LIMITED — НЕ ПОМЕХА, А АДРЕСАТ. Панель сама переводит LIMITED → ACTIVE, когда новый
trafficLimitBytes больше прежнего, и заново раздаёт человека нодам. Поэтому статус
из ОТВЕТА панели пишем в свою строку той же транзакцией: иначе до вебхука кабинет
показывал бы «трафик исчерпан» тому, кому уже всё включили.

Конфиг — assets/extra_traffic.json, читается на каждый вызов (админка правит на
лету). Файла нет — продажи ВЫКЛЮЧЕНЫ. Выключатель гасит продажи, но НЕ крон:
купленные прибавки обязаны дожить до обнуления и кончиться.

Модульный уровень — только stdlib/sqlalchemy/loguru: этот модуль импортирует правка
шлюза, а она грузится посреди денежного пути (урок overlay_topup).
"""

from __future__ import annotations

import calendar
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Sequence

from loguru import logger
from sqlalchemy import text

if TYPE_CHECKING:  # pragma: no cover — только для подсказок типов
    from sqlalchemy.ext.asyncio import AsyncSession

DAY = 86_400
# Синтетический «тариф» счёта докупки трафика. Заняты: −1 импорт, −2 пополнение,
# −3 подарок, −4 устройство. Все денежные отчёты уже фильтруют снимки с id > 0,
# поэтому −5 не попадёт ни в MRR, ни в топ тарифов, ни в право на резерв.
SYNTHETIC_PLAN_ID = -5
# Сколько часов крон повторяет применение оплаченного заказа, пока панель молчит.
CREDIT_RETRY_HOURS = 2
# Заказ старше суток применять нельзя: окно трафика, за которое платили, уже не то.
STALE_HOURS = 24
# Сколько подряд неудач применения лимита терпим до алерта владельцу.
FAIL_ALERT_AT = 3
# Год «бессрочной» подписки в панели.
UNLIMITED_YEAR = 2099

# ЧАСЫ КАЛЕНДАРНОГО СБРОСА В ПАНЕЛИ (UTC), прочитаны из scheduler.js Remnawave 3.4.4.
# Именно они, а не часы базовой get_traffic_reset_delta (00:00 / 00:05 / 00:10):
# расхождение в часах означало бы, что мы снимаем прибавку не тогда, когда панель
# обнуляет расход, и человек либо теряет оплаченное, либо получает лишнее.
RESET_AT_UTC: dict[str, tuple[int, int]] = {
    "DAY": (0, 5),
    "MONTH_ROLLING": (0, 10),
    "WEEK": (0, 15),
    "MONTH": (0, 20),
}

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
CONFIG_PATH = ASSETS_DIR / "extra_traffic.json"

DEFAULT_CONFIG: dict[str, Any] = {
    # Продажи выключены, пока владелец не откроет их в админке: выкатка образа не
    # должна сама по себе начать брать деньги.
    "enabled": False,
    # Решение владельца: 50 ГБ за 50 ₽, цена фиксированная.
    "gb_per_purchase": 50,
    "price_rub": 50,
    "min_amount_rub": 10,
    # С какого процента расхода показывать предложение на Главной (решение Р-2).
    "show_from_percent": 70,
    # Меньше этого времени до обнуления — не продаём: платить 50 ₽ за час нечестно.
    "min_hours_left": 2,
    # Технический потолок на окно, не коммерческий: защита от опечатки в цене и от
    # зацикленного клиента, а не ограничение числа покупок (их владелец не лимитирует).
    "max_gb_per_window": 1000,
    "notify_users": True,
    "notify_admins": True,
    # Своё сообщение «трафик закончился — можно докупить». Базовое уведомление бота
    # оно не подавляет и не заменяет.
    "notify_limited": True,
    # Отзыв прибавки владельцем: возвращать ли ₽ на баланс. По умолчанию НЕТ
    # (решение Р-4) — кнопка в админке спрашивает каждый раз.
    "refund_on_revoke": False,
}

_LIMITS = {
    "gb_per_purchase": (1, 10_000),
    "price_rub": (1, 100_000),
    "min_amount_rub": (1, 100_000),
    "show_from_percent": (0, 99),
    "min_hours_left": (0, 720),
    "max_gb_per_window": (1, 100_000),
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
    price_raw = data.get("price_rub", DEFAULT_CONFIG["price_rub"])
    price: Optional[int]
    if price_raw is None or price_raw == "":
        price = None
    else:
        try:
            value = int(price_raw)
        except (TypeError, ValueError):
            price = DEFAULT_CONFIG["price_rub"]
        else:
            # Ноль и отрицательная цена — это «продавать нельзя», а не «бесплатно»:
            # счёт на 0 ₽ шлюз не примет, а с баланса это была бы раздача трафика.
            price = None if value <= 0 else min(value, _LIMITS["price_rub"][1])
    return {
        "enabled": bool(data.get("enabled", False)),
        "gb_per_purchase": _norm_int(
            data.get("gb_per_purchase"),
            DEFAULT_CONFIG["gb_per_purchase"],
            _LIMITS["gb_per_purchase"],
        ),
        "price_rub": price,
        "min_amount_rub": _norm_int(
            data.get("min_amount_rub"), DEFAULT_CONFIG["min_amount_rub"], _LIMITS["min_amount_rub"]
        ),
        "show_from_percent": _norm_int(
            data.get("show_from_percent"),
            DEFAULT_CONFIG["show_from_percent"],
            _LIMITS["show_from_percent"],
        ),
        "min_hours_left": _norm_int(
            data.get("min_hours_left"), DEFAULT_CONFIG["min_hours_left"], _LIMITS["min_hours_left"]
        ),
        "max_gb_per_window": _norm_int(
            data.get("max_gb_per_window"),
            DEFAULT_CONFIG["max_gb_per_window"],
            _LIMITS["max_gb_per_window"],
        ),
        "notify_users": bool(data.get("notify_users", True)),
        "notify_admins": bool(data.get("notify_admins", True)),
        "notify_limited": bool(data.get("notify_limited", True)),
        "refund_on_revoke": bool(data.get("refund_on_revoke", False)),
    }


def load_config() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text("utf-8"))
    except FileNotFoundError:
        # Файла нет — продажи выключены. Это и есть состояние сразу после выкатки.
        return dict(DEFAULT_CONFIG)
    except Exception as exc:  # noqa: BLE001 — битый конфиг не имеет права ронять оплату
        logger.warning(f"extra_traffic: конфиг не прочитан ({exc}) — беру дефолт")
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
    """Продаём ли сейчас. Тумблер без цены или без объёма — это «не продаём»."""
    return (
        bool(config.get("enabled"))
        and bool(config.get("price_rub"))
        and int(config.get("gb_per_purchase") or 0) > 0
    )


# ── момент обнуления трафика (ЕДИНСТВЕННАЯ реализация на весь проект) ───────


def strategy_name(strategy: Any) -> str:
    """Имя стратегии строкой: в БД это enum, в панели — строка, в тестах — обе."""
    raw = getattr(strategy, "value", strategy)
    return str(raw or "").upper()


def needs_anchor(strategy: Any) -> bool:
    """Нужна ли дата создания пользователя ПАНЕЛИ, чтобы посчитать момент сброса."""
    return strategy_name(strategy) == "MONTH_ROLLING"


def _add_month(moment: datetime) -> datetime:
    """`+ interval '1 month'` как в postgres: день зажимается по длине месяца."""
    year = moment.year + (1 if moment.month == 12 else 0)
    month = 1 if moment.month == 12 else moment.month + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def _at(day_value: date, hhmm: tuple[int, int]) -> datetime:
    return datetime.combine(day_value, time(hhmm[0], hhmm[1]), tzinfo=timezone.utc)


def next_traffic_reset(
    strategy: Any, panel_created_at: Optional[datetime], now: datetime
) -> Optional[datetime]:
    """Когда панель обнулит РАСХОД этого человека. None — никогда/неизвестно.

    ЭТО ЕДИНСТВЕННЫЙ расчёт даты обновления трафика во всём проекте: им живут срок
    прибавки, тексты кабинета, сообщение «трафик закончился» и карточка подписки в
    меню бота. Разные даты в этих местах человек читает как обман, а нас они лишают
    возможности объяснить, за что он заплатил.

    Формулы сняты с панели 3.4.4 (scheduler.js, getNextTrafficResetAt +
    TH.RESET_USER_TRAFFIC), время — UTC:
      DAY           — ежедневно 00:05;
      MONTH_ROLLING — ежедневный проход 00:10 берёт тех, у кого
                      LEAST(day(created_at), последний день месяца) = day(текущей даты)
                      И created_at + 1 месяц <= текущая дата;
      WEEK          — понедельник 00:15;
      MONTH         — 1-е число 00:20.

    `panel_created_at` — дата создания пользователя В ПАНЕЛИ, а не нашей строки
    подписки: наша пересоздаётся при каждой смене тарифа, панельная — никогда.
    Подставить не ту значит назвать человеку чужое число (это и была ошибка базовой
    get_traffic_reset_delta).
    """
    name = strategy_name(strategy)
    if name not in RESET_AT_UTC:
        # NO_RESET и всё незнакомое: сброса нет — прибавка живёт до продления,
        # смены тарифа или ручного сброса, то есть до первого обнуления расхода.
        return None
    hhmm = RESET_AT_UTC[name]
    today = now.astimezone(timezone.utc).date()

    if name == "DAY":
        moment = _at(today, hhmm)
        return moment if moment > now else _at(today + timedelta(days=1), hhmm)

    if name == "WEEK":
        # 0 = понедельник. Ближайший понедельник, а сегодняшний — только если 00:15
        # ещё не наступили.
        ahead = (7 - today.weekday()) % 7
        moment = _at(today + timedelta(days=ahead), hhmm)
        return moment if moment > now else _at(today + timedelta(days=ahead + 7), hhmm)

    if name == "MONTH":
        first = date(today.year, today.month, 1)
        moment = _at(first, hhmm)
        if moment > now:
            return moment
        year = today.year + (1 if today.month == 12 else 0)
        month = 1 if today.month == 12 else today.month + 1
        return _at(date(year, month, 1), hhmm)

    # MONTH_ROLLING
    if panel_created_at is None:
        # Без якоря день сброса не вычислить. Врать числом нельзя: лучше «не знаем»,
        # и тогда продажа отказывается (reset_unknown), а крон прибавку не трогает.
        return None
    anchor = panel_created_at.astimezone(timezone.utc)
    # Порог панели: `created_at + 1 месяц <= CURRENT_DATE`, где CURRENT_DATE
    # приводится к полуночи. То есть первый сброс возможен только со дня, полночь
    # которого уже не раньше «дата создания + месяц» ВМЕСТЕ СО ВРЕМЕНЕМ.
    not_before = _add_month(anchor)
    year, month = today.year, today.month
    for _ in range(0, 26):  # два года запаса — дальше искать нечего
        day_of_reset = min(anchor.day, calendar.monthrange(year, month)[1])
        candidate = date(year, month, day_of_reset)
        moment = _at(candidate, hhmm)
        if moment > now and datetime.combine(candidate, time.min, tzinfo=timezone.utc) >= not_before:
            return moment
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return None


# ── состояние ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GrantRow:
    id: int
    subscription_id: int
    plan_id: int
    gb: int
    strategy: str
    panel_created_at: Optional[datetime]
    granted_at: datetime
    ends_at: Optional[datetime]
    last_applied_at: datetime


@dataclass(frozen=True)
class UserState:
    user_id: int
    balance: Decimal
    sub_id: Optional[int]
    sub_status: Optional[str]
    is_trial: bool
    expire_at: Optional[datetime]
    traffic_limit: int  # ГБ, наше округлённое зеркало панели
    strategy: str
    plan_id: Optional[int]
    plan_traffic_limit: int
    remna_uuid: Optional[str]
    sub_updated_at: Optional[datetime]
    frozen_at: Optional[datetime]
    reserve_expire_at: Optional[datetime]
    grants: tuple[GrantRow, ...] = ()

    @property
    def is_unlimited_term(self) -> bool:
        return self.expire_at is not None and self.expire_at.year >= UNLIMITED_YEAR

    @property
    def active_gb(self) -> int:
        """Сколько ГБ докуплено и ещё действует в ЭТОЙ строке подписки."""
        return sum(g.gb for g in self.grants if g.subscription_id == self.sub_id)


@dataclass(frozen=True)
class PanelView:
    """Что панель говорит о человеке прямо сейчас. Источник правды по байтам."""

    limit_bytes: int
    used_bytes: int
    status: str
    created_at: Optional[datetime]


@dataclass(frozen=True)
class Quote:
    gb: int
    amount: Decimal
    window_end: Optional[datetime]
    days: int


# ── чистые функции ──────────────────────────────────────────────────────────


def eligibility(
    st: UserState,
    config: dict[str, Any],
    now: datetime,
    *,
    window_end: Optional[datetime],
    window_known: bool = True,
    check_enabled: bool = True,
) -> Optional[str]:
    """Почему НЕЛЬЗЯ (код причины) или None. Порядок проверок — часть смысла.

    `check_enabled=False` — для уже оплаченного заказа: деньги взяты, и выключенный
    в этот момент тумблер не повод их не отработать.

    LIMITED здесь ПРОХОДИТ, в отличие от докупки устройства, где требовался строго
    ACTIVE. «Кончился трафик» — это и есть наш покупатель, а панель сама вернёт его
    в ACTIVE, когда лимит поднимется.
    """
    if check_enabled and not effective_enabled(config):
        return "disabled"
    if st.sub_id is None or st.expire_at is None:
        return "no_subscription"
    if st.is_trial:
        return "trial"
    # Безлимит проверяем в ДВУХ местах: строка — текущее состояние, снимок тарифа —
    # что человек купил. Ноль в строке означает «добавлять нечего к бесконечности»;
    # ноль только в тарифе (лимит выставил админ руками) продавать не мешает.
    if st.traffic_limit == 0:
        return "unlimited_traffic"
    if st.plan_traffic_limit <= 0:
        return "unlimited_traffic"
    if st.is_unlimited_term:
        return "unlimited_term"
    if st.frozen_at is not None:
        return "frozen"
    # Резерв сдвигает срок строки и оставляет статус ACTIVE: без этой проверки
    # истёкший на бесплатной страховке выглядел бы платящим, а резерв в панели —
    # это лимит 1 ГБ, к которому нам добавлять нечего.
    if st.reserve_expire_at is not None and st.expire_at <= st.reserve_expire_at + timedelta(hours=1):
        return "reserve"
    if (st.sub_status or "").upper() not in ("ACTIVE", "LIMITED") or st.expire_at <= now:
        return "not_active"
    if not window_known:
        # Стратегия требует якоря (MONTH_ROLLING), а даты создания в панели нет.
        # Продать значит пообещать срок, которого мы не знаем.
        return "reset_unknown"
    if st.active_gb + int(config.get("gb_per_purchase") or 0) > int(config["max_gb_per_window"]):
        return "window_cap"
    if window_end is not None:
        hours_left = (window_end - now).total_seconds() / 3600
        if hours_left < int(config["min_hours_left"]):
            return "reset_too_soon"
    return None


def quote(
    config: dict[str, Any], now: datetime, window_end: Optional[datetime], expire_at: datetime
) -> Quote:
    """Сколько ГБ и за сколько. Цена ФИКСИРОВАННАЯ — ни от остатка, ни от времени.

    `days` нужен только для описания счёта в банке: CreatePayment печатает «название
    + N дней», и человек должен увидеть настоящий срок действия прибавки, а не «1 день».
    Ноль недопустим: у базы `duration = 0` означает «бессрочный тариф».
    """
    price = config.get("price_rub")
    if not price:
        raise ValueError("цена докупки трафика не задана")
    gb = int(config["gb_per_purchase"])
    if gb <= 0:
        raise ValueError("объём докупки не задан")
    amount = Decimal(max(int(config["min_amount_rub"]), int(price)))
    end = window_end if window_end is not None else expire_at
    secs = max(0, int((end - now).total_seconds()))
    return Quote(gb=gb, amount=amount, window_end=window_end, days=min(3650, max(1, -(-secs // DAY))))


def target_limit(cur_gb: int, plan_gb: int, active_extra_gb: int, ended_gb: int) -> int:
    """Каким должен стать лимит трафика (в ГБ) после окончания прибавок.

    Пол `plan_gb + active_extra_gb` не даёт уйти ниже тарифа, если лимит уже
    обнулили продлением, и не отнимает ручную щедрость админа: 500 при тарифе 300 и
    одной кончившейся прибавке 50 станет 450, а не 300.
    """
    if cur_gb == 0 or plan_gb <= 0:  # безлимит — не трогаем вообще
        return cur_gb
    return max(cur_gb - ended_gb, plan_gb + active_extra_gb)


def target_bytes(cur_bytes: int, plan_gb: int, active_extra_gb: int, ended_gb: int) -> int:
    """То же правило, но в БАЙТАХ — для арифметики от значения панели.

    Считать «наше округлённое число ГБ + 50» нельзя: если лимит в панели не кратен
    гигабайту (руками или из чужого импорта), покупка незаметно сдвинула бы человеку
    лимит на величину округления, и каждая следующая — ещё раз.
    """
    from src.core.utils.converters import gb_to_bytes

    if cur_bytes == 0 or plan_gb <= 0:
        return cur_bytes
    floor_bytes = gb_to_bytes(plan_gb + active_extra_gb)
    return max(cur_bytes - gb_to_bytes(ended_gb), floor_bytes)


def used_percent(used_bytes: int, limit_bytes: int) -> int:
    if limit_bytes <= 0:
        return 0
    return min(999, int(used_bytes * 100 / limit_bytes))


def synthetic_snapshot(gb: int, days: int) -> Any:
    """Снимок «тарифа» для счёта. Ленивый импорт: application-слой — не модульный."""
    from src.application.dto import PlanSnapshotDto
    from src.core.enums import PlanType

    return PlanSnapshotDto(
        id=SYNTHETIC_PLAN_ID,
        name=f"Дополнительный трафик {int(gb)} ГБ",
        type=PlanType.TRAFFIC,
        traffic_limit=int(gb),
        device_limit=0,
        duration=max(1, int(days)),
        is_trial=False,
    )


# ── SQL ─────────────────────────────────────────────────────────────────────

# Замок берём на строке users (не на подписке): её же запирает перенос остатка при
# смене тарифа и докупка устройства — общий замок делает эти операции взаимно
# последовательными. LEFT JOIN — чтобы человек без подписки не проваливал запрос.
LOCK_STATE_SQL = (
    "SELECT u.cabinet_balance, s.id, s.status::text, s.is_trial, s.expire_at, s.traffic_limit, "
    "s.traffic_limit_strategy::text, (s.plan_snapshot->>'id')::int, "
    "(s.plan_snapshot->>'traffic_limit')::int, s.user_remna_id, s.updated_at "
    "FROM users u LEFT JOIN subscriptions s ON s.id = u.current_subscription_id "
    "WHERE u.id = :uid FOR UPDATE OF u"
)

# То же самое без замка — для показа: держать строку под FOR UPDATE ради рисования
# карточки незачем, и это мешало бы покупке из соседней вкладки.
READ_STATE_SQL = LOCK_STATE_SQL.replace(" FOR UPDATE OF u", "")

ACTIVE_GRANTS_SQL = (
    "SELECT id, subscription_id, plan_id, gb, strategy, panel_created_at, granted_at, "
    "ends_at, last_applied_at FROM extra_traffic_grants "
    "WHERE user_id = :uid AND status = 'active' ORDER BY id"
)

# Пауза и резерв одним запросом: оба — причины не продавать (см. eligibility).
PAUSE_RESERVE_SQL = (
    "SELECT (SELECT frozen_at FROM subscription_freezes WHERE user_id = :uid AND active = true), "
    "(SELECT max(reserve_expire_at) FROM reserve_grants "
    " WHERE user_id = :uid AND ended = false AND reserve_expire_at > now())"
)

SPEND_SQL = (
    "UPDATE users SET cabinet_balance = cabinet_balance - :amount "
    "WHERE id = :uid AND cabinet_balance >= :amount RETURNING cabinet_balance"
)

SPEND_SQL_TEXT = text(SPEND_SQL)

ORDER_BY_REQUEST_SQL = (
    "SELECT id, user_id, status, gb, amount, payment_id, payment_url, grant_id, window_end "
    "FROM extra_traffic_orders WHERE request_id = :rid"
)

ORDER_FOR_UPDATE_SQL = (
    "SELECT id, user_id, subscription_id, grant_id, status, gb, amount, window_end, "
    "panel_created_at, attempts, created_at, payment_id "
    "FROM extra_traffic_orders WHERE payment_id = :pid FOR UPDATE"
)


def _row_grant(row: Any) -> GrantRow:
    return GrantRow(
        id=int(row[0]),
        subscription_id=int(row[1]),
        plan_id=int(row[2]),
        gb=int(row[3]),
        strategy=str(row[4] or ""),
        panel_created_at=row[5],
        granted_at=row[6],
        ends_at=row[7],
        last_applied_at=row[8],
    )


async def lock_state(session: "AsyncSession", user_id: int, *, lock: bool = True) -> UserState:
    """Замок строки человека + всё состояние сырым SQL.

    Сырым, а не через ORM: сессия живёт с `expire_on_commit=False`, и объект из
    identity map под замком оказался бы устаревшим — ровно тем снимком, из-за
    которого мы и не шлём в панель полное тело.
    """
    sql = LOCK_STATE_SQL if lock else READ_STATE_SQL
    row = (await session.execute(text(sql), {"uid": user_id})).first()
    if row is None:
        raise ValueError(f"пользователь id={user_id} не найден")
    grants = [
        _row_grant(r) for r in (await session.execute(text(ACTIVE_GRANTS_SQL), {"uid": user_id})).all()
    ]
    pause = (await session.execute(text(PAUSE_RESERVE_SQL), {"uid": user_id})).first()
    return UserState(
        user_id=user_id,
        balance=Decimal(str(row[0] if row[0] is not None else 0)),
        sub_id=int(row[1]) if row[1] is not None else None,
        sub_status=row[2],
        is_trial=bool(row[3]),
        expire_at=row[4],
        traffic_limit=int(row[5] or 0),
        strategy=str(row[6] or ""),
        plan_id=int(row[7]) if row[7] is not None else None,
        plan_traffic_limit=int(row[8] or 0),
        remna_uuid=str(row[9]) if row[9] else None,
        sub_updated_at=row[10],
        frozen_at=pause[0] if pause else None,
        reserve_expire_at=pause[1] if pause else None,
        grants=tuple(grants),
    )


async def order_by_request(session: "AsyncSession", request_id: Any) -> Optional[dict]:
    row = (await session.execute(text(ORDER_BY_REQUEST_SQL), {"rid": str(request_id)})).first()
    if row is None:
        return None
    return {
        "id": int(row[0]),
        "user_id": int(row[1]),
        "status": row[2],
        "gb": int(row[3]),
        "amount": Decimal(str(row[4])),
        "payment_id": row[5],
        "payment_url": row[6],
        "grant_id": row[7],
        "window_end": row[8],
    }


async def order_for_update(session: "AsyncSession", payment_id: Any) -> Optional[dict]:
    row = (await session.execute(text(ORDER_FOR_UPDATE_SQL), {"pid": str(payment_id)})).first()
    if row is None:
        return None
    return {
        "id": int(row[0]),
        "user_id": int(row[1]),
        "subscription_id": int(row[2]),
        "grant_id": row[3],
        "status": row[4],
        "gb": int(row[5]),
        "amount": Decimal(str(row[6])),
        "window_end": row[7],
        "panel_created_at": row[8],
        "attempts": int(row[9] or 0),
        "created_at": row[10],
        "payment_id": row[11],
    }


async def order_by_payment(session: "AsyncSession", payment_id: Any) -> Optional[dict]:
    """Тот же заказ, но БЕЗ замка — для алертов после чужого commit (возврат)."""
    row = (
        await session.execute(
            text(
                "SELECT id, status, amount, gb, grant_id, window_end, user_id "
                "FROM extra_traffic_orders WHERE payment_id = :pid"
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
        "gb": int(row[3]),
        "grant_id": row[4],
        "window_end": row[5],
        "user_id": int(row[6]),
    }


# ── панель ──────────────────────────────────────────────────────────────────


class PanelRejected(RuntimeError):
    """Панель не приняла новый лимит (ошибка или вернула не то число)."""


# Дата создания пользователя ПАНЕЛИ не меняется никогда — ни ручным сбросом, ни
# продлением. Поэтому её можно держать в процессе: это снимает лишний поход в
# панель с каждой отрисовки карточки трафика и с каждого открытия меню бота.
_CREATED_CACHE: dict[str, datetime] = {}
_CREATED_CACHE_MAX = 5000


def remember_created_at(uuid: Any, created_at: Optional[datetime]) -> None:
    if not uuid or created_at is None:
        return
    if len(_CREATED_CACHE) >= _CREATED_CACHE_MAX:
        _CREATED_CACHE.clear()
    _CREATED_CACHE[str(uuid)] = created_at


def cached_created_at(uuid: Any) -> Optional[datetime]:
    return _CREATED_CACHE.get(str(uuid)) if uuid else None


def panel_view(remna_user: Any) -> PanelView:
    """Разобрать ответ панели в то, что нам нужно: байты, расход, статус, дата."""
    traffic = getattr(remna_user, "user_traffic", None)
    used = getattr(traffic, "used_traffic_bytes", None)
    if used is None:
        used = getattr(remna_user, "used_traffic_bytes", 0)
    status = getattr(remna_user, "status", None)
    created = getattr(remna_user, "created_at", None)
    remember_created_at(getattr(remna_user, "uuid", None), created)
    return PanelView(
        limit_bytes=int(getattr(remna_user, "traffic_limit_bytes", 0) or 0),
        used_bytes=int(used or 0),
        status=str(getattr(status, "value", status) or ""),
        created_at=created,
    )


async def read_panel(remnawave: Any, st: UserState) -> Optional[PanelView]:
    """Прочитать человека из панели. None — панель молчит (предложение прячем)."""
    if remnawave is None or not st.remna_uuid:
        return None
    try:
        remna_user = await remnawave.get_user_by_uuid(st.remna_uuid)
    except Exception as exc:  # noqa: BLE001 — недоступная панель не ломает страницу
        logger.warning(f"extra_traffic: панель не ответила о user_id={st.user_id}: {exc}")
        return None
    if remna_user is None:
        return None
    return panel_view(remna_user)


async def set_traffic_limit(
    session: "AsyncSession", sdk: Any, st: UserState, new_bytes: int
) -> str:
    """Лимит в базу и в панель УЗКИМ телом. Без commit — им распоряжается вызвавший.

    Тело PATCH — ровно `{uuid, trafficLimitBytes}`. Полное тело веткой `subscription`
    вернуло бы человеку `expire_at` из нашей базы, а её пишут не все: возобновление
    паузы и резерв кладут новый срок ТОЛЬКО в панель — прибавка трафика съела бы дни
    паузы. Плюс полное тело переписало бы стратегию сброса и сквады, которых мы
    не трогаем.

    Ответ сверяем В БАЙТАХ (не в ГБ: bytes_to_gb округляет): молчаливое «принял, но
    не применил» оставило бы оплаченный, но не работающий трафик. Возвращаем статус
    из ответа — панель сама снимает LIMITED при поднятии лимита, и это надо записать.
    """
    from src.core.utils.converters import bytes_to_gb

    await session.execute(
        text("UPDATE subscriptions SET traffic_limit = :n, updated_at = now() WHERE id = :sid"),
        {"n": int(bytes_to_gb(int(new_bytes))), "sid": st.sub_id},
    )
    if not st.remna_uuid:
        raise PanelRejected("у подписки нет идентификатора в панели")

    from remnapy.models import UpdateUserRequestDto

    try:
        updated = await sdk.users.update_user(
            UpdateUserRequestDto(uuid=str(st.remna_uuid), traffic_limit_bytes=int(new_bytes))
        )
    except Exception as exc:  # noqa: BLE001 — наверх уходит одна понятная ошибка
        raise PanelRejected(f"{type(exc).__name__}: {exc}") from exc
    got = getattr(updated, "traffic_limit_bytes", None)
    if got is None or int(got) != int(new_bytes):
        raise PanelRejected(f"панель вернула лимит {got}, ожидали {new_bytes}")
    status = getattr(updated, "status", None)
    return str(getattr(status, "value", status) or "")


async def compensate_limit(sdk: Any, st: UserState, limit_before_bytes: int) -> bool:
    """Вернуть панели прежний лимит. Best-effort, наружу ничего не бросает.

    ЗАЧЕМ БЕЗУСЛОВНО, А НЕ «СНАЧАЛА ПРОВЕРИМ». После неудачи мы не знаем, применился
    ли наш PATCH: таймаут и разорванное соединение выглядят одинаково с «не дошло» и
    с «дошло, ответ потерялся». Повторная установка ПРЕЖНЕГО значения безвредна, если
    ничего не менялось, и чинит панель, если менялось. Без неё вебхук `user.modified`
    поднял бы traffic_limit уже в нашей базе, и ПОВТОР того же заказа дал бы +100 ГБ
    за одну оплату: «+50 к текущему» считается от значения, которое мы сами и подняли.
    """
    if sdk is None or not st.remna_uuid:
        return False
    try:
        from remnapy.models import UpdateUserRequestDto

        await sdk.users.update_user(
            UpdateUserRequestDto(
                uuid=str(st.remna_uuid), traffic_limit_bytes=int(limit_before_bytes)
            )
        )
        return True
    except Exception as exc:  # noqa: BLE001 — это уже аварийная ветка, хуже не сделаем
        logger.critical(
            f"extra_traffic: НЕ вернул лимит {limit_before_bytes} Б в панель "
            f"user_id={st.user_id}: {exc}"
        )
        return False


# ── покупка ─────────────────────────────────────────────────────────────────


async def close_due_grants(session: "AsyncSession", st: UserState, now: datetime) -> int:
    """Пометить просроченные прибавки ЭТОГО человека прямо в транзакции покупки.

    ЗАЧЕМ ЭТО ЗДЕСЬ, А НЕ ТОЛЬКО В КРОНЕ. Между обнулением расхода панелью и
    проходом крона проходит до 17 минут. Покупка в это окно видела бы ещё не
    закрытую прибавку и ещё поднятый лимит — и дала бы человеку +2 объёма за одну
    оплату, а потолок окна посчитала бы не от того числа.

    Панель здесь НЕ трогаем: лимит мы всё равно пересчитаем ниже одним PATCH от
    значения панели, и лишний вызов только добавил бы точку отказа.
    """
    due = [g.id for g in st.grants if g.ends_at is not None and g.ends_at <= now]
    if not due:
        return 0
    await session.execute(
        text(
            "UPDATE extra_traffic_grants SET status = 'ended', end_reason = 'reset', "
            "ended_at = now() WHERE id = ANY(:ids) AND status = 'active'"
        ),
        {"ids": due},
    )
    return len(due)


def state_after_due(st: UserState, now: datetime) -> UserState:
    """Состояние без просроченных прибавок — то, от чего считаются цена и потолок."""
    from dataclasses import replace

    alive = tuple(g for g in st.grants if g.ends_at is None or g.ends_at > now)
    if len(alive) == len(st.grants):
        return st
    return replace(st, grants=alive)


async def buy_from_balance(
    session: "AsyncSession",
    *,
    sdk: Any,
    transaction_dao: Any,
    gateway_type: Any,
    st: UserState,
    panel: PanelView,
    q: Quote,
    request_id: Any,
    source: str,
    now: datetime,
    ended_gb: int = 0,
    order_id: Optional[int] = None,
    payment_id: Any = None,
) -> dict:
    """Списать, записать прибавку и заказ, поднять лимит. БЕЗ commit — его делает вызвавший.

    Порядок шагов важен: деньги списываются условным UPDATE (без «сначала прочитали,
    потом списали»), панель идёт ПОСЛЕДНЕЙ, и любая её ошибка откатывает всю
    транзакцию — списания, транзакции, записи и лимита не останется.
    """
    import uuid as _uuid

    from src.application.dto import PriceDetailsDto, TransactionDto
    from src.core.enums import Currency, PurchaseType, TransactionStatus
    from src.core.utils.converters import bytes_to_gb, gb_to_bytes

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
            gateway_display_name="Баланс · трафик",
            pricing=PriceDetailsDto(original_amount=amount, discount_percent=0, final_amount=amount),
            currency=Currency.RUB,
            plan_snapshot=synthetic_snapshot(q.gb, q.days),
        )
        await transaction_dao.create(transaction)
        payment_id = transaction.payment_id

    grant_id = int(
        (
            await session.execute(
                text(
                    "INSERT INTO extra_traffic_grants "
                    "(user_id, subscription_id, plan_id, status, gb, strategy, panel_created_at, "
                    " granted_at, ends_at, last_applied_at) "
                    "VALUES (:uid, :sid, :pid, 'active', :gb, :strategy, :created, :now, :ends, now()) "
                    "RETURNING id"
                ),
                {
                    "uid": st.user_id,
                    "sid": st.sub_id,
                    "pid": st.plan_id if st.plan_id is not None else -1,
                    "gb": q.gb,
                    "strategy": strategy_name(st.strategy),
                    "created": panel.created_at,
                    "now": now,
                    "ends": q.window_end,
                },
            )
        ).scalar()
    )

    if order_id is None:
        await session.execute(
            text(
                "INSERT INTO extra_traffic_orders "
                "(request_id, payment_id, source, user_id, subscription_id, grant_id, status, "
                " gb, amount, currency, window_end, panel_created_at, applied_at) "
                "VALUES (:rid, :pid, :src, :uid, :sid, :grant, 'applied', :gb, :amount, 'RUB', "
                " :ends, :created, now())"
            ),
            {
                "rid": str(request_id),
                "pid": str(payment_id),
                "src": source,
                "uid": st.user_id,
                "sid": st.sub_id,
                "grant": grant_id,
                "gb": q.gb,
                "amount": amount,
                "ends": q.window_end,
                "created": panel.created_at,
            },
        )
    else:
        await session.execute(
            text(
                "UPDATE extra_traffic_orders SET status = 'applied', applied_at = now(), "
                "grant_id = :grant, window_end = :ends WHERE id = :id"
            ),
            {"grant": grant_id, "ends": q.window_end, "id": order_id},
        )

    # ЛИМИТ СЧИТАЕМ ОТ БАЙТОВ ПАНЕЛИ, а не от нашего округлённого числа ГБ (Р13).
    # Просроченные прибавки уже закрыты (close_due_grants), поэтому их объём из
    # текущего значения вычитаем здесь же: иначе покупка в окно «сброс прошёл, крон
    # не добежал» подняла бы лимит от завышенного основания.
    base_bytes = target_bytes(
        panel.limit_bytes, st.plan_traffic_limit, st.active_gb, ended_gb
    )
    new_bytes = int(base_bytes) + gb_to_bytes(q.gb)
    status = await set_traffic_limit(session, sdk, st, new_bytes)
    if status:
        # Панель сама снимает LIMITED при поднятии лимита. Пишем её статус к себе,
        # чтобы кабинет и бот не показывали «трафик исчерпан» тому, кому уже включили.
        await session.execute(
            text("UPDATE subscriptions SET status = :s WHERE id = :sid AND status::text <> :s"),
            {"s": status, "sid": st.sub_id},
        )
    await session.execute(
        text(
            "UPDATE extra_traffic_grants SET last_applied_at = now(), fail_count = 0 "
            "WHERE subscription_id = :sid AND status = 'active'"
        ),
        {"sid": st.sub_id},
    )

    return {
        "result": "applied",
        "grant_id": grant_id,
        "gb": q.gb,
        "traffic_limit_gb": bytes_to_gb(new_bytes),
        "traffic_limit_bytes": new_bytes,
        "limit_before_bytes": panel.limit_bytes,
        "status": status,
        "until": q.window_end,
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
    panel: PanelView,
) -> bool:
    """Заказ `pending` ДО отдачи ссылки. False — ключ уже занят двойником.

    `panel_created_at` кладём в строку намеренно: вебхук приходит в другой процесс,
    где кеша нет, а без якоря окно для MONTH_ROLLING не пересчитать.
    """
    inserted = (
        await session.execute(
            text(
                "INSERT INTO extra_traffic_orders "
                "(request_id, payment_id, source, user_id, subscription_id, status, gb, amount, "
                " currency, window_end, panel_created_at, payment_url) "
                "VALUES (:rid, :pid, 'gateway', :uid, :sid, 'pending', :gb, :amount, 'RUB', "
                " :ends, :created, :url) ON CONFLICT (request_id) DO NOTHING RETURNING id"
            ),
            {
                "rid": str(request_id),
                "pid": str(payment_id),
                "uid": user_id,
                "sid": st.sub_id,
                "gb": q.gb,
                "amount": q.amount,
                "ends": q.window_end,
                "created": panel.created_at,
                "url": payment_url,
            },
        )
    ).scalar()
    await session.commit()
    return inserted is not None


async def credit_order(session: "AsyncSession", order: dict) -> None:
    """Деньги счёта — на ₽-баланс. Отдельным шагом и со своим commit.

    Так «получили деньги» и «выдали трафик» не зависят друг от друга: даже если
    панель не ответит вовсе, человек не потеряет оплату — она лежит на балансе.
    """
    await session.execute(
        text("UPDATE users SET cabinet_balance = cabinet_balance + :a WHERE id = :u"),
        {"a": order["amount"], "u": order["user_id"]},
    )
    await session.execute(
        text(
            "UPDATE extra_traffic_orders SET status = 'credited', credited_at = now() "
            "WHERE id = :id AND status = 'pending'"
        ),
        {"id": order["id"]},
    )
    await session.commit()


async def reject_order(session: "AsyncSession", order_id: int, reason: str) -> None:
    await session.execute(
        text(
            "UPDATE extra_traffic_orders SET status = 'rejected', reason = :r, rejected_at = now() "
            "WHERE id = :id AND status IN ('pending', 'credited')"
        ),
        {"r": reason[:32], "id": order_id},
    )
    await session.commit()


async def note_attempt(session: "AsyncSession", order_id: int, error: str) -> None:
    await session.execute(
        text(
            "UPDATE extra_traffic_orders SET attempts = attempts + 1, last_error = :e WHERE id = :id"
        ),
        {"e": error[:300], "id": order_id},
    )
    await session.commit()


async def apply_order(
    session: "AsyncSession",
    *,
    sdk: Any,
    remnawave: Any,
    config: dict[str, Any],
    order: dict,
    now: datetime,
) -> dict:
    """Оплаченный заказ → прибавка. Замок заказа уже взят вызвавшим (заказ → users).

    Порядок замков «строка заказа, затем строка users» одинаков в вебхуке и в кроне:
    обратный порядок дал бы взаимоблокировку двух процессов на одном человеке.
    """
    st = await lock_state(session, order["user_id"])
    panel = await read_panel(remnawave, st)
    if panel is None:
        await session.rollback()
        raise PanelRejected("панель не ответила — заказ доведём позже")

    ended_gb = sum(g.gb for g in st.grants if g.ends_at is not None and g.ends_at <= now)
    await close_due_grants(session, st, now)
    st = state_after_due(st, now)

    anchor = panel.created_at or order.get("panel_created_at")
    window_end = next_traffic_reset(st.strategy, anchor, now)
    known = not (needs_anchor(st.strategy) and anchor is None)
    why = eligibility(
        st, config, now, window_end=window_end, window_known=known, check_enabled=False
    )
    if why is None:
        created = order.get("created_at")
        if isinstance(created, datetime) and (now - created).total_seconds() > STALE_HOURS * 3600:
            why = "stale"
        elif st.sub_id != order["subscription_id"]:
            why = "subscription_changed"
        elif order["window_end"] is not None and order["window_end"] <= now:
            # Окно, за которое платили, успело обновиться само. Деньги остаются на
            # балансе — человеку честно «купите ещё раз, если нужно».
            why = "window_closed"
    if why is not None:
        await session.rollback()
        await reject_order(session, order["id"], why)
        return {"result": "rejected", "reason": why}

    # Окно МОГЛО сдвинуться вперёд (подписку продлили, сменили стратегию) — это НЕ
    # отказ: объём фиксирован, поэтому применяем к новому окну и пишем фактический
    # `ends_at`, а не тот, что был в счёте.
    q = Quote(
        gb=int(order["gb"]),
        amount=order["amount"],
        window_end=window_end,
        days=max(1, -(-int(((window_end or st.expire_at) - now).total_seconds()) // DAY)),
    )
    limit_before = panel.limit_bytes
    try:
        result = await buy_from_balance(
            session,
            sdk=sdk,
            transaction_dao=None,
            gateway_type=None,
            st=st,
            panel=panel,
            q=q,
            request_id=None,
            source="gateway",
            now=now,
            ended_gb=ended_gb,
            order_id=order["id"],
            payment_id=order["payment_id"],
        )
    except PanelRejected:
        # Панель ответила ошибкой ИЛИ не тем лимитом — но могла применить его до того
        # (таймаут, потерянный ответ). Записи у нас не останется, значит крон такую
        # прибавку никогда не увидит: возвращаем прежнее значение сами.
        await session.rollback()
        await compensate_limit(sdk, st, limit_before)
        raise
    if result.get("result") == "insufficient_balance":
        # Деньги зачислены, но человек успел потратить их сам (продление, автоплатёж).
        await session.rollback()
        await reject_order(session, order["id"], "balance_spent")
        return {"result": "rejected", "reason": "balance_spent"}
    try:
        await session.commit()
    except Exception:
        # Панель приняла лимит, база — нет. Компенсация обязательна (см. док-строку
        # compensate_limit): без неё повтор заказа дал бы двойной объём.
        await session.rollback()
        await compensate_limit(sdk, st, limit_before)
        logger.critical(
            f"extra_traffic: commit после панели упал (счёт '{order['payment_id']}'), "
            f"лимит в панели возвращён на {limit_before} Б"
        )
        raise
    return result


async def handle_paid_order(
    session: "AsyncSession",
    sdk: Any,
    remnawave: Any,
    payment_id: Any,
    now: Optional[datetime] = None,
) -> Optional[dict]:
    """Вебхук шлюза: это наш счёт? Тогда деньги — на баланс, затем трафик.

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
    result = await apply_order(
        session, sdk=sdk, remnawave=remnawave, config=load_config(), order=order, now=now
    )
    result["order"] = order
    return result


# ── жизнь прибавок ──────────────────────────────────────────────────────────


async def mark_grants(
    session: "AsyncSession", ids: Sequence[int], status: str, reason: str
) -> None:
    if not ids:
        return
    await session.execute(
        text(
            "UPDATE extra_traffic_grants SET status = :st, end_reason = :r, ended_at = now() "
            "WHERE id = ANY(:ids) AND status = 'active'"
        ),
        {"st": status, "r": reason, "ids": list(ids)},
    )


async def burn_on_renew(session: "AsyncSession", user_id: int) -> int:
    """После продления прибавки ГАСНУТ — и панель при этом НЕ трогаем.

    ВНИМАНИЕ, ЭТО ПРОТИВОПОЛОЖНО ДОКУПКЕ УСТРОЙСТВА. Там после RENEW лимит устройств
    ВОЗВРАЩАЮТ (место оплачено вперёд и обязано пережить продление). Здесь база уже
    поставила ТАРИФНЫЙ лимит трафика и обнулила расход — то есть человек начал период
    с нуля и полным объёмом тарифа, и прибавка, сгорая в этот момент, ничего у него
    не отнимает. Любой вызов панели тут только сломал бы правильный лимит.
    """
    rows = (
        await session.execute(
            text(
                "UPDATE extra_traffic_grants SET status = 'ended', end_reason = 'renew', "
                "ended_at = now() WHERE user_id = :uid AND status = 'active' RETURNING id"
            ),
            {"uid": user_id},
        )
    ).all()
    if rows:
        await session.commit()
    return len(rows)


async def reconcile_user(
    session: "AsyncSession",
    *,
    sdk: Any,
    user_id: int,
    config: dict[str, Any],
    now: datetime,
) -> dict:
    """Свести лимит трафика с действующими прибавками. Один человек, один commit.

    Порядок веток — часть смысла, и первая из них не оптимизация, а защита денег:
    резерв ставит в панели лимит 1 ГБ, и общая формула «не ниже тарифа + действующие»
    вернула бы истёкшему человеку полноценный объём. Поэтому резерв и пауза закрывают
    записи БЕЗ обращения к панели.
    """
    st = await lock_state(session, user_id)
    out: dict[str, Any] = {
        "ended": [],
        "burned": [],
        "limit": st.traffic_limit,
        "limit_before": st.traffic_limit,
        "expire_at": st.expire_at,
        "plan_traffic_limit": st.plan_traffic_limit,
    }
    if st.sub_id is None or not st.grants:
        await session.rollback()
        return out

    frozen = st.frozen_at is not None
    on_reserve = (
        st.reserve_expire_at is not None
        and st.expire_at is not None
        and st.expire_at <= st.reserve_expire_at + timedelta(hours=1)
    )
    if frozen or on_reserve:
        # Человек выключен (пауза) или сидит на страховочном гигабайте (резерв):
        # трафика он не тратит, лимит трогать незачем, а оставить запись `active`
        # значило бы однажды опустить ему лимит «за просроченную прибавку».
        reason = "frozen" if frozen else "reserve"
        await mark_grants(session, [g.id for g in st.grants], "ended", reason)
        await session.commit()
        out["ended"] = [g.id for g in st.grants]
        out["reason"] = reason
        out["silent"] = True
        return out

    ended_ids: list[int] = []
    burned: list[tuple[int, str]] = []
    alive: list[GrantRow] = []
    for grant in st.grants:
        if grant.subscription_id != st.sub_id:
            burned.append((grant.id, "subscription_replaced"))
            continue
        if st.plan_id is not None and grant.plan_id != st.plan_id:
            burned.append((grant.id, "plan_replaced"))
            continue
        # Окно пересчитываем КАЖДЫЙ проход из ТЕКУЩЕЙ стратегии строки и сохранённого
        # якоря: админ мог сменить стратегию тарифа, и вебхук `user.modified` уже
        # положил новую в нашу базу.
        if needs_anchor(st.strategy) and grant.panel_created_at is None:
            # Якоря нет — считать нечем. Молча опускать лимит по догадке нельзя.
            alive.append(grant)
            continue
        ends_at = next_traffic_reset(st.strategy, grant.panel_created_at, grant.granted_at)
        if ends_at is not None and ends_at <= now:
            ended_ids.append(grant.id)
        elif ends_at is None and grant.ends_at is not None and grant.ends_at <= now:
            # Стратегию сменили на NO_RESET уже после покупки: обещанный срок всё
            # равно назван человеку, и он должен кончиться.
            ended_ids.append(grant.id)
        else:
            alive.append(grant)

    expired_long_ago = st.expire_at is not None and st.expire_at + timedelta(days=3) <= now
    # Лимит в базе стал РОВНО тарифным, а прибавки ещё числятся — значит объём уже
    # переписали продлением, админским «Выдать» или промокодом-подпиской. Признак
    # надёжен сам по себе: живая прибавка всегда держит лимит выше тарифного (мы её
    # так и записываем), поэтому равенство возможно только после чужого сброса.
    plan_reset = st.plan_traffic_limit > 0 and st.traffic_limit == st.plan_traffic_limit
    if plan_reset:
        # Гасим ВСЕ прибавки этой строки, а не только просроченные: расход обнулён,
        # человек начал период с нуля и полным объёмом тарифа. Панель не зовём —
        # лимит уже правильный (см. burn_on_renew: то же правило в денежном пути).
        ended_ids = [g.id for g in alive] + ended_ids
        alive = []

    active_gb = sum(g.gb for g in alive)
    ended_gb = sum(g.gb for g in st.grants if g.id in ended_ids)
    target = target_limit(st.traffic_limit, st.plan_traffic_limit, active_gb, ended_gb)

    for grant_id, reason in burned:
        # Лимит НЕ трогаем: его уже поставил новый тариф (CHANGE, «Выдать», промокод).
        await mark_grants(session, [grant_id], "burned", reason)
    if ended_ids:
        reason = "expired" if expired_long_ago else ("renew" if plan_reset else "reset")
        await mark_grants(session, ended_ids, "ended", reason)
        out["reason"] = reason

    changed = False
    deleted_panel = (st.sub_status or "").upper() == "DELETED"
    need_panel = (
        target != st.traffic_limit
        and not plan_reset  # лимит уже тарифный: продление всё сделало за нас
        and not expired_long_ago
        and not deleted_panel
    )
    if need_panel:
        from src.core.utils.converters import gb_to_bytes

        try:
            await set_traffic_limit(session, sdk, st, gb_to_bytes(target))
            changed = True
        except PanelRejected as exc:
            if "NotFound" in str(exc):
                logger.warning(
                    f"extra_traffic: user_id={user_id} — в панели нет, правлю только базу"
                )
                await session.execute(
                    text(
                        "UPDATE subscriptions SET traffic_limit = :n, updated_at = now() "
                        "WHERE id = :sid"
                    ),
                    {"n": target, "sid": st.sub_id},
                )
                out["panel_missing"] = True
                changed = True
            else:
                await session.rollback()
                await _bump_fail(session, ended_ids or [g.id for g in alive])
                out["error"] = str(exc)
                return out
    if changed:
        await session.execute(
            text(
                "UPDATE extra_traffic_grants SET last_applied_at = now(), fail_count = 0 "
                "WHERE subscription_id = :sid AND status = 'active'"
            ),
            {"sid": st.sub_id},
        )
    await session.commit()

    out["ended"] = ended_ids
    out["ended_gb"] = ended_gb
    out["burned"] = burned
    out["limit"] = target if changed or plan_reset else st.traffic_limit
    out["panel_called"] = changed and not out.get("panel_missing")
    out["silent"] = expired_long_ago
    return out


async def _bump_fail(session: "AsyncSession", grant_ids: Sequence[int]) -> None:
    if not grant_ids:
        return
    await session.execute(
        text("UPDATE extra_traffic_grants SET fail_count = fail_count + 1 WHERE id = ANY(:ids)"),
        {"ids": list(grant_ids)},
    )
    await session.commit()


# ── сообщение «трафик закончился» ───────────────────────────────────────────


def limited_dedup_key(user_id: int, window_end: Optional[datetime]) -> str:
    """Ключ дедупа в Redis: один раз на человека и окно трафика.

    В REDIS, А НЕ В assets/*.json. Сообщение рождается в ДВУХ процессах — в боте
    (вебхук `user.limited`) и в воркере (догоняющий крон), и файловое состояние они
    затирали бы друг другу. Redis есть у обоих.
    """
    stamp = window_end.strftime("%Y%m%d%H%M") if window_end is not None else "noreset"
    return f"extra_traffic:limited:{int(user_id)}:{stamp}"


def limited_ttl(window_end: Optional[datetime], now: datetime) -> int:
    """До конца окна плюс сутки: после обновления трафика ключ другой, и это верно."""
    if window_end is None:
        return 30 * DAY
    left = int((window_end - now).total_seconds())
    return max(DAY, min(left + DAY, 400 * DAY))


async def claim_limited(redis: Any, user_id: int, window_end: Optional[datetime], now: datetime) -> bool:
    """Занять право отправить сообщение. False — уже отправляли в этом окне."""
    if redis is None:
        return False
    key = limited_dedup_key(user_id, window_end)
    try:
        # SET NX — атомарный захват: вебхук и крон могут прийти в одну секунду.
        got = await redis.set(key, "1", ex=limited_ttl(window_end, now), nx=True)
    except Exception as exc:  # noqa: BLE001 — без дедупа лучше промолчать, чем спамить
        logger.warning(f"extra_traffic: дедуп в Redis не сработал ({exc}) — сообщение не шлю")
        return False
    return bool(got)


async def send_raw_telegram(config: Any, telegram_id: Optional[int], text_html: str) -> bool:
    """Отправить своё сообщение человеку. Best-effort, свой Bot, приём из traffic_alert.

    Своим Bot, а не через Notifier: эта функция зовётся и из вебхука панели (там
    Notifier не выдают), и из крона. Недостижимый человек — не ошибка (память
    notification-chat-not-found).
    """
    if not telegram_id:
        return False
    bot = None
    try:
        from aiogram import Bot

        bot = Bot(config.bot.token.get_secret_value())
        await bot.send_message(int(telegram_id), text_html)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"extra_traffic: сообщение в TG {telegram_id} не доставлено: {exc}")
        return False
    finally:
        if bot is not None:
            try:
                await bot.session.close()
            except Exception:  # noqa: BLE001
                pass


# ── тексты ──────────────────────────────────────────────────────────────────

_REASON_RU = {
    "disabled": "докупка выключена",
    "no_subscription": "подписки нет",
    "trial": "это пробная подписка",
    "unlimited_traffic": "у подписки безлимитный трафик",
    "unlimited_term": "у подписки нет срока",
    "frozen": "подписка на паузе",
    "reserve": "подписка закончилась",
    "not_active": "подписка не активна",
    "reset_unknown": "не удалось определить дату обновления трафика",
    "window_cap": "докуплен максимум трафика на этот период",
    "reset_too_soon": "трафик обновится совсем скоро",
    "subscription_changed": "тариф сменился",
    "window_closed": "период трафика успел обновиться",
    "stale": "с момента счёта прошло больше суток",
    "balance_spent": "деньги уже потрачены",
    "panel_timeout": "сервер не ответил",
    "panel_unavailable": "сервер не ответил",
}


def reason_ru(code: str) -> str:
    return _REASON_RU.get(code, code)


def _dm(value: Any) -> str:
    return value.strftime("%d.%m.%Y в %H:%M") if isinstance(value, datetime) else str(value)


def _date(value: Any) -> str:
    return value.strftime("%d.%m.%Y") if isinstance(value, datetime) else str(value)


def user_text(key: str, **kw: Any) -> str:
    """Сообщения человеку. Python-строки, не ftl: фигурные скобки Fluent тут ни к чему."""
    if key == "applied":
        tail = (
            f"Прибавка действует до обновления трафика {_dm(kw['until'])}."
            if kw.get("until")
            else "Прибавка действует до ближайшего обновления трафика."
        )
        head = (
            "✅ Трафик добавлен, доступ восстановлен"
            if kw.get("unlocked")
            else "✅ Трафик добавлен"
        )
        return f"{head}: +{kw['gb']} ГБ, всего {kw['limit']} ГБ.\n{tail}"
    if key == "not_applied":
        return (
            f"💳 Оплата {kw['amount']} ₽ получена, но добавить трафик не получилось: "
            f"{reason_ru(kw['reason'])}. Деньги лежат на балансе в кабинете — там же можно "
            "попробовать ещё раз или продлить подписку."
        )
    if key == "balance_spent":
        return (
            f"💳 Оплата {kw['amount']} ₽ зачислена на баланс и уже использована, "
            "поэтому трафик не добавлен."
        )
    if key == "limited_offer":
        when = (
            f"Он обновится {_dm(kw['until'])}."
            if kw.get("until")
            else "Он обновится при продлении подписки."
        )
        return (
            f"🚦 Трафик закончился. {when}\n"
            f"Можно не ждать: +{kw['gb']} ГБ за {kw['price']} ₽ — {kw['url']}\n"
            "Или продлить подписку либо перейти на тариф побольше."
        )
        # Базовое уведомление бота про LIMITED это сообщение НЕ отменяет: оно рядом
        # и говорит другое — что делать прямо сейчас.
    if key == "almost_out":
        when = f" до обновления трафика {_date(kw['until'])}" if kw.get("until") else ""
        return f"Можно докупить {kw['gb']} ГБ за {kw['price']} ₽ — прибавка действует{when}."
    if key == "ended":
        return (
            f"Докупленные {kw['gb']} ГБ закончились вместе с периодом трафика — "
            f"лимит вернулся к {kw['limit']} ГБ. Трафик уже обновлён, он снова считается с нуля."
        )
    raise KeyError(key)


def admin_text(key: str, **kw: Any) -> str:
    """Сообщения владельцу. Все — после commit и в try: они не участвуют в деньгах."""
    if key == "bought":
        source = "баланс" if kw["source"] == "balance" else "шлюз"
        until = f"до {_dm(kw['until'])}" if kw.get("until") else "до ближайшего обновления"
        return (
            "📶 <b>Докупка трафика</b>\n"
            f"{kw['user']}\n+{kw['gb']} ГБ {until}\n{kw['amount']} ₽ · {source}"
        )
    if key == "rejected":
        return (
            "⚠️ <b>Докупка трафика не применена</b>\n"
            f"{kw['user']}\nОплата {kw['amount']} ₽ (<code>{kw['payment_id']}</code>) "
            f"на балансе: {reason_ru(kw['reason'])}."
        )
    if key == "refunded":
        tail = (
            f"Прибавка #{kw['grant_id']} действует до {_dm(kw['until'])}. "
            "Отменить: Пользователи → карточка → «Отменить докупку трафика»."
            if kw.get("grant_id")
            else "Сумма зачислена на баланс — спишите вручную."
        )
        return (
            "⚠️ <b>Возврат по докупке трафика</b>\n"
            f"{kw['user']}\nСчёт <code>{kw['payment_id']}</code>. {tail}"
        )
    if key == "cron_failed":
        return (
            "⚠️ <b>Докупка трафика: не удалось снять лимит</b>\n"
            f"{kw['user']}\nПрибавка #{kw['grant_id']}: {kw['error']}."
        )
    if key == "deferred":
        return (
            "⚠️ <b>Докупка трафика: не выдана сразу</b>\n"
            f"{kw['user']}\nСчёт <code>{kw['payment_id']}</code>: {kw['error']}.\n"
            "Деньги на балансе, заказ доведёт крон в ближайшие 17 минут — "
            "если через два часа не вышло, придёт «Докупка трафика не применена»."
        )
    if key == "subscription_replaced":
        return (
            "⚠️ <b>Докупка трафика сгорела: сменилась подписка</b>\n"
            f"{kw['user']}\nПрибавка #{kw['grant_id']} привязана к прежней строке подписки. "
            "Если смену делали вручную (выдача, промокод), докупку стоит возместить."
        )
    if key == "plan_replaced":
        return (
            "⚠️ <b>Докупка трафика сгорела при замене тарифа</b>\n"
            f"{kw['user']}\nПрибавка #{kw['grant_id']}: тариф в строке сменился без переноса. "
            "Если это подарок или выдача — возместите вручную."
        )
    if key == "commit_failed":
        return (
            "🚨 <b>Докупка трафика: commit упал после панели</b>\n"
            f"{kw['user']}\nЛимит в панели откатываем на {kw['limit']} Б. Проверьте карточку."
        )
    raise KeyError(key)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
