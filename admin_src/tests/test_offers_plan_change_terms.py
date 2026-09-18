"""Витрина сообщает кабинету, что будет с остатком при смене тарифа.

ИСТОРИЯ. У базы смена тарифа (CHANGE) — срок с нуля: ветка «CHANGE или триал» зовёт
`update_user(plan=…)`, а `_build_update_request` по тарифу ставит
`expire_at = сейчас + длительность`. Сначала кабинет честно предупреждал о сгорании.
Теперь остаток переносится по цене дня (overlay_patches/plan_change_carryover.py), и
витрина отдаёт, сколько дней добавится, — таблицей по (тариф, срок, валюта).

ЧТО ЗАПИРАЕМ.
  * `plan_change_terms` считает остаток теми же полными сутками, что кабинет,
    а на паузе берёт сохранённый остаток — `expire_at` там стоит на месте;
  * `/offers` отдаёт поля именно через оверлей-схему: FastAPI режет ответ по
    `response_model`, и с базовой схемой условия молча пропали бы;
  * `plan_change_carry_active` = перенос РЕАЛЬНО случится (`overlay_active`): правка
    встала и выключатель включён; выключено — поля нет, кабинет предупреждает по-старому;
  * `plan_change_keeps_days` — для СТАРЫХ сборок кабинета: true, только если ничего не
    пропадёт; бессрочная, возврат, потеря дней хоть на одной цели — false, чтобы старая
    сборка предупредила;
  * сбой чтения паузы или состояния переноса витрину не роняет (это единственный
    путь к покупке), а «не знаем» не выдаётся за «перенесём»;
  * резерв не переносится: `current_days_left` = 0;
  * СИГНАЛИЗАЦИЯ: база до сих пор сжигает остаток — поэтому наша правка нужна. Начнёт
    переносить сама — наша перенесёт второй раз; тест обязан упасть.

Запуск — внутри образа бота, как остальные тесты рядом (см. ci.yml).
"""

import importlib
import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Optional

import pytest

# Порядок важен: сначала раздел публичных ручек — при его импорте overlay
# подставляет наш роутер. Импорт модуля правки первым ловил его недостроенным
# (циклический импорт через public/__init__.py).
importlib.import_module("src.web.endpoints.public")
offers_mod = importlib.import_module("overlay_patches.public_subscription")

carry = importlib.import_module("src.infrastructure.services.overlay_plan_change")

from decimal import Decimal  # noqa: E402
from fractions import Fraction  # noqa: E402

from src.application.dto import PlanDto, PlanDurationDto, PlanPriceDto  # noqa: E402
from src.application.services import PricingService  # noqa: E402
from src.core.enums import Currency, PaymentGatewayType, SubscriptionStatus  # noqa: E402

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
DAY = 86400


@pytest.fixture(autouse=True)
def carry_switched_on(monkeypatch):
    """По умолчанию перенос выключен — витрину проверяем с явно включённым выключателем."""
    monkeypatch.setattr(carry, "load_config", lambda: {"enabled": True, "promo_enabled": False, "notify_admins": True})


def terms(**over: Any) -> dict:
    args: dict[str, Any] = {
        "expire_at": NOW + timedelta(days=30),
        "is_unlimited": False,
        "is_trial": False,
        "frozen_seconds": None,
        "now": NOW,
    }
    args.update(over)
    return offers_mod.plan_change_terms(**args)


# ── plan_change_terms ───────────────────────────────────────────────────────

def test_partial_day_is_not_counted():
    """40 дн. 23 ч — это 40: кабинет обещает «осталось N дн.» полными сутками."""
    got = terms(expire_at=NOW + timedelta(days=40, hours=23))
    assert got["current_days_left"] == 40
    assert got["current_frozen"] is False
    assert got["current_is_unlimited"] is False


def test_expired_subscription_loses_nothing():
    assert terms(expire_at=NOW - timedelta(days=3))["current_days_left"] == 0


def test_unlimited_has_no_day_count_but_is_flagged():
    got = terms(expire_at=NOW.replace(year=2099), is_unlimited=True)
    assert got["current_days_left"] is None
    assert got["current_is_unlimited"] is True


def test_frozen_remaining_wins_over_stale_expire_at():
    """На паузе срок в базе стоит (и уже в прошлом), а остаток лежит отдельно."""
    got = terms(expire_at=NOW - timedelta(days=5), frozen_seconds=int(30.5 * DAY))
    assert got["current_days_left"] == 30
    assert got["current_frozen"] is True


def test_trial_flag_is_passed_through():
    assert terms(is_trial=True)["current_is_trial"] is True


# ── маршрут и схема ─────────────────────────────────────────────────────────

def test_offers_route_serializes_overlay_fields():
    route = next(
        r for r in offers_mod.router.routes
        if getattr(r, "path", None) == "/subscription/offers" and "GET" in r.methods
    )
    assert route.response_model is offers_mod.SubscriptionOffersOverlayResponse

    empty = offers_mod.SubscriptionOffersOverlayResponse(
        gateways=[], plans=[], has_current_subscription=False,
    ).model_dump()
    assert empty["plan_change_keeps_days"] is False
    for key in ("current_days_left", "current_is_trial", "current_is_unlimited", "current_frozen"):
        assert key in empty and empty[key] is None


# ── ручка целиком, на подделках ─────────────────────────────────────────────

class FakeResult:
    def __init__(self, row: Optional[tuple]) -> None:
        self._row = row

    def first(self) -> Optional[tuple]:
        return self._row


class FakeSession:
    def __init__(self, row: Optional[tuple] = None, fail: bool = False) -> None:
        self.row = row
        self.fail = fail
        self.rollbacks = 0
        self.commits = 0
        self.sql: list[str] = []

    async def execute(self, stmt: Any, params: Any = None) -> FakeResult:
        self.sql.append(str(stmt))
        if self.fail:
            raise RuntimeError("relation subscription_freezes does not exist")
        return FakeResult(self.row)

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def commit(self) -> None:
        self.commits += 1


class FakeSubscriptionDao:
    def __init__(self, sub: Any) -> None:
        self.sub = sub

    async def get_current(self, user_id: int) -> Any:
        return self.sub


class NoGateways:
    async def get_active(self) -> list:
        return []


class NoPlans:
    async def system(self, *args: Any) -> list:
        return []


class NoMatch:
    async def system(self, *args: Any) -> None:
        return None


def subscription(days: float = 29.5, trial: bool = False) -> Any:
    expire = datetime.now(timezone.utc) + timedelta(days=days)
    return SimpleNamespace(
        id=5,
        expire_at=expire,
        is_unlimited=False,
        is_trial=trial,
        status=SubscriptionStatus.ACTIVE,
        current_status=SubscriptionStatus.ACTIVE,
        plan_snapshot=SimpleNamespace(id=10),
        created_at=None,
    )


def fake_loader(calls: Optional[list] = None, *, fail: bool = False, reserve_days: Optional[float] = None,
                layers: tuple = ()):
    """Подмена загрузки состояния переноса: FakeSession не знает его SQL.

    Пауза читается из того же FakeSession, что и витрина, — как в жизни.
    """

    async def load(session, *, user_id, subscription, now, exclude_payment_id=None, extra_plan_ids=(), extras=()):
        if calls is not None:
            calls.append({"subscription": subscription, "ids": tuple(extra_plan_ids)})
        if fail:
            raise RuntimeError("relation plan_change_carryovers does not exist")
        row = session.row
        return carry.CarryState(
            status=subscription.status, is_unlimited=subscription.is_unlimited,
            expire_at=subscription.expire_at,
            frozen_seconds=int(row[0]) if row else None,
            reserve_expire_at=(subscription.expire_at if reserve_days is not None else None),
            refund_recent=False, old_plan_id=subscription.plan_id, layers=layers, prices={},
        )

    return load


async def call_offers(session: FakeSession, sub: Any, *, loader=None, plans=None, gateways=None) -> Any:
    raw = offers_mod.get_subscription_offers.__dishka_orig_func__
    original = carry.load_carry_state
    carry.load_carry_state = loader or fake_loader()
    try:
        return await raw(
            user=SimpleNamespace(id=7, purchase_discount=0, personal_discount=0, remna_name="rs_7", log="[USER:7]"),
            session=session,
            subscription_dao=FakeSubscriptionDao(sub),
            payment_gateway_dao=gateways or NoGateways(),
            pricing_service=PricingService(),
            get_available_plans=plans or NoPlans(),
            match_plan=NoMatch(),
        )
    finally:
        carry.load_carry_state = original


async def test_offers_report_extra_devices_of_current_row():
    """Докупленные места витрина считает по своей строке подписки — для BillingPage."""
    session = FakeSession(row=(1, datetime(2026, 10, 1, tzinfo=timezone.utc)))
    resp = await call_offers(session, subscription(days=20))
    assert resp.current_extra_devices == 1
    assert resp.current_extra_until == "2026-10-01T00:00:00+00:00"
    assert any("extra_device_slots" in q for q in session.sql)


async def test_offers_report_days_left_for_active_subscription():
    session = FakeSession(row=None)
    resp = await call_offers(session, subscription(days=29.5))
    assert resp.plan_change_keeps_days is carry.overlay_active()
    assert resp.plan_change_keeps_days is True, "в образе правка встала, выключатель включён фикстурой"
    assert resp.plan_change_carry_active is True
    assert resp.carry_mode == "carry"
    assert resp.current_days_left == 29
    assert resp.current_frozen is False
    assert resp.current_is_trial is False
    # Только чтение: витрина ничего не коммитит.
    assert session.commits == 0
    assert any("subscription_freezes" in q for q in session.sql)


async def test_offers_use_frozen_remaining():
    session = FakeSession(row=(12 * DAY + 100,))
    resp = await call_offers(session, subscription(days=-2))
    assert resp.current_days_left == 12
    assert resp.current_frozen is True


async def test_freeze_lookup_failure_keeps_showcase_and_says_unknown():
    session = FakeSession(fail=True)
    resp = await call_offers(session, subscription(days=10.2))
    # Три независимых вспомогательных чтения (пауза, докупленные устройства и
    # докупленный трафик), у каждого свой откат: витрина — единственный путь к
    # покупке и падать не должна ни из-за одного из них.
    assert session.rollbacks == 3
    assert resp.current_days_left == 10
    assert resp.current_frozen is None
    # Про докупленные места ничего не знаем — значит и кабинету не обещаем.
    assert resp.current_extra_devices is None
    assert resp.current_extra_until is None


async def test_offers_without_subscription_leave_terms_empty():
    session = FakeSession()
    resp = await call_offers(session, None)
    assert resp.has_current_subscription is False
    assert resp.plan_change_keeps_days is False
    assert resp.current_days_left is None
    assert resp.current_frozen is None
    # Без подписки паузу и не спрашиваем.
    assert session.sql == []


# ── перенос остатка в витрине ───────────────────────────────────────────────


class OneGateway:
    async def get_active(self) -> list:
        return [
            SimpleNamespace(
                type=PaymentGatewayType.YOOMONEY, currency=Currency.RUB,
                settings=SimpleNamespace(is_configured=True),
            )
        ]


class Showcase:
    """DUO2: 30 дн. — 240 ₽ (8/день); у 90 дн. цены в рублях нет."""

    async def system(self, *args: Any) -> list:
        return [
            PlanDto(
                id=20, public_code="DUO2", name="DUO2",
                durations=[
                    PlanDurationDto(days=30, prices=[PlanPriceDto(currency=Currency.RUB, price=Decimal("240"))]),
                    PlanDurationDto(days=90, prices=[PlanPriceDto(currency=Currency.USD, price=Decimal("9"))]),
                ],
            )
        ]


class ShowcaseSafe(Showcase):
    """Та же витрина, но с рублёвой ценой у обоих сроков (базовый цикл цен её требует)."""

    async def system(self, *args: Any) -> list:
        plans = await super().system()
        plans[0].durations[1].prices.append(PlanPriceDto(currency=Currency.RUB, price=Decimal("600")))
        return plans


async def test_offers_carry_table_per_plan_term_currency():
    """29,5 дн. по 4/день = 118 → /8 = 14 дн. (30 дн.); 90 дн. за 600 → /(20/3) = 17."""
    paid = (carry.Layer("create", "p", 30 * DAY, "RUB", Fraction(120), Fraction(120), 10, 30),)
    calls: list = []
    resp = await call_offers(
        FakeSession(row=None), subscription(days=29.5), loader=fake_loader(calls, layers=paid),
        plans=ShowcaseSafe(), gateways=OneGateway(),
    )
    assert resp.plan_change_keeps_days is True
    assert calls[0]["ids"] == (20,), "цены целевых тарифов грузятся одним запросом"
    entries = [e.model_dump() for e in resp.plan_change_carry]
    assert entries == [
        {"plan_code": "DUO2", "duration_days": 30, "currency": "RUB", "mode": "carry",
         "bonus_days": 14, "lost_days": 0, "capped": False, "extras_lost": 0, "lost_reason": None},
        {"plan_code": "DUO2", "duration_days": 90, "currency": "RUB", "mode": "carry",
         "bonus_days": 17, "lost_days": 0, "capped": False, "extras_lost": 0, "lost_reason": None},
    ]
    dumped = resp.model_dump()
    assert dumped["carry_mode"] == "carry" and dumped["plan_change_carry"][0]["bonus_days"] == 14


def test_carry_entry_skips_state_modes():
    """В таблицу попадают только режимы цели; режим состояния — в `carry_mode`."""
    reserve = carry.CarryResult("reserve", 0, 0, Fraction(0), None, 0, 0, 0, False)
    assert offers_mod.carry_entry("DUO2", 30, "RUB", reserve) is None
    same = carry.CarryResult("same_plan", 10 * DAY, 10, Fraction(0), None, 0, 10 * DAY, 0, False)
    assert offers_mod.carry_entry("DUO2", 30, "RUB", same).bonus_days == 10


async def test_offers_reserve_is_not_carried():
    resp = await call_offers(FakeSession(row=None), subscription(days=3), loader=fake_loader(reserve_days=3))
    assert resp.plan_change_keeps_days is True
    assert resp.carry_mode == "reserve"
    assert resp.current_days_left == 0
    assert resp.plan_change_carry == []


async def test_offers_state_failure_rolls_back_and_says_no_carry():
    session = FakeSession(row=None)
    resp = await call_offers(session, subscription(days=20.5), loader=fake_loader(fail=True),
                             plans=ShowcaseSafe(), gateways=OneGateway())
    assert resp.plan_change_keeps_days is False
    assert session.rollbacks == 1
    assert resp.carry_mode is None and resp.plan_change_carry is None
    assert resp.current_days_left == 20, "витрина жива, прежние условия на месте"
    assert len(resp.plans) == 1


async def test_offers_switch_off_means_no_promise(monkeypatch):
    monkeypatch.setattr(carry, "load_config", lambda: {"enabled": False, "notify_admins": True})
    calls: list = []
    resp = await call_offers(FakeSession(row=None), subscription(days=20.5), loader=fake_loader(calls))
    assert resp.plan_change_keeps_days is False
    assert calls == [], "выключено — состояние даже не читаем"
    assert resp.carry_mode is None


async def test_offers_default_switch_is_off(monkeypatch):
    """Без файла выключателя — перенос выключен: витрина ничего не обещает."""
    monkeypatch.undo()
    monkeypatch.setattr(carry, "CONFIG_PATH", carry.ASSETS_DIR / "__нет_такого_файла__.json")
    resp = await call_offers(FakeSession(row=None), subscription(days=20.5))
    assert resp.plan_change_keeps_days is False and resp.plan_change_carry_active is None


def _entry(**over):
    base = dict(plan_code="DUO2", duration_days=30, currency="RUB", mode="carry", bonus_days=14, lost_days=0)
    base.update(over)
    return offers_mod.PlanChangeCarryEntry(**base)


def test_legacy_keeps_days_false_whenever_something_is_lost():
    """Старая сборка кабинета при keeps_days=true не предупреждает вовсе — даём true, только
    если НИЧЕГО не пропадёт."""
    assert offers_mod.legacy_keeps_days("carry", [_entry()]) is True
    assert offers_mod.legacy_keeps_days("reserve", []) is True
    assert offers_mod.legacy_keeps_days("none", []) is True
    assert offers_mod.legacy_keeps_days("lifetime", []) is False
    assert offers_mod.legacy_keeps_days("refund", []) is False
    assert offers_mod.legacy_keeps_days("carry", [_entry(), _entry(lost_days=3, lost_reason="old_price")]) is False
    assert offers_mod.legacy_keeps_days("carry", [_entry(mode="unpriced", bonus_days=0, lost_days=0)]) is False
    assert offers_mod.legacy_keeps_days("carry", [_entry(capped=True)]) is False
    assert offers_mod.legacy_keeps_days("carry", [_entry(extras_lost=1)]) is False


async def test_offers_lost_days_turn_legacy_flag_off_but_table_stays():
    """Дни без известной цены (тариф вне витрины): старая сборка предупредит, новая — по таблице."""
    unknown = carry.Layer("create", "p", 30 * DAY, "XTR", Fraction(50), Fraction(50), 10, 30)
    resp = await call_offers(
        FakeSession(row=None), subscription(days=29.5), loader=fake_loader(layers=(unknown,)),
        plans=ShowcaseSafe(), gateways=OneGateway(),
    )
    assert resp.plan_change_carry_active is True
    assert resp.plan_change_keeps_days is False
    assert resp.plan_change_carry[0].lost_days > 0 and resp.plan_change_carry[0].lost_reason == "old_price"


async def test_offers_lifetime_turns_legacy_flag_off():
    sub = subscription(days=30)
    sub.expire_at = sub.expire_at.replace(year=2099)
    sub.is_unlimited = True
    resp = await call_offers(FakeSession(row=None), sub)
    assert resp.carry_mode == "lifetime"
    assert resp.plan_change_keeps_days is False and resp.plan_change_carry_active is True


async def test_offers_patch_not_applied_means_no_promise(monkeypatch):
    monkeypatch.setattr(carry, "patch_applied", lambda: False)
    resp = await call_offers(FakeSession(row=None), subscription(days=20.5))
    assert resp.plan_change_keeps_days is False


async def test_offers_trial_is_not_carried():
    calls: list = []
    resp = await call_offers(FakeSession(row=None), subscription(days=3, trial=True), loader=fake_loader(calls))
    assert resp.carry_mode == "none"
    assert calls == []


# ── сигнализация: база всё ещё сжигает остаток ─────────────────────────────

def test_base_update_request_is_the_one_we_rely_on():
    """Правка пишет срок веткой subscription у `_build_update_request` — её форма часть контракта."""
    import src.infrastructure.services.remnawave as remnawave_module
    from overlay_patches import expect_source

    expect_source(
        remnawave_module,
        "RemnawaveImpl._build_update_request",
        "b7ea09b0f54bf1c2cb6fc51da95201382cc20da0838da4ab941d86f2e6e54300",
        "RemnawaveImpl._build_update_request",
    )


def test_base_still_restarts_term_on_plan_change():
    """База ВСЁ ЕЩЁ сжигает остаток — значит наша правка нужна. Перестанет — упадём тут.

    Если база начнёт переносить сама, наша правка перенесёт остаток второй раз.
    Смотрим ИСХОДНИК модуля: сам метод обёрнут нашей правкой.
    """
    import src.application.use_cases.subscription.commands.purchase as purchase_module
    from src.infrastructure.services.remnawave import RemnawaveImpl

    build = inspect.getsource(RemnawaveImpl._build_update_request)
    assert "days_to_datetime(plan.duration)" in build, (
        "база больше не ставит срок «с нуля» по тарифу — проверьте перенос остатка "
        "и переверните plan_change_keeps_days в public_subscription.py"
    )

    source = inspect.getsource(purchase_module)
    marker = "elif purchase_type == PurchaseType.CHANGE"
    assert marker in source, "ветка CHANGE в PurchaseSubscription перестроена — сверить перенос остатка"
    change_branch = source.split(marker, 1)[1].split("\n            else:", 1)[0]
    assert "update_user(" in change_branch and "plan=plan" in change_branch, (
        "CHANGE больше не отдаёт панели тариф — возможно, остаток теперь переносится; "
        "сверить и перевернуть plan_change_keeps_days"
    )
