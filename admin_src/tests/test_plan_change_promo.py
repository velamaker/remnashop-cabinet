"""Подарок ДРУГОГО тарифа по промокоду: остаток пересчитывается, а не сгорает.

ЧТО ЗАПИРАЕМ.
  * Цена дня подарка — витрина его срока в валюте по умолчанию; срока нет в витрине —
    самая ВЫСОКАЯ цена дня тарифа (бонус меньше, магазин не дарит лишнего); тарифа нет в
    таблице цен — перенос невозможен, «замена» как раньше.
  * Активация (`ActivatePromocode._apply_subscription`): другой тариф и перенос
    возможен — панель получает срок «сейчас + срок подарка + бонус» веткой subscription,
    журнал `source=promocode` пишется В ТОЙ ЖЕ сессии без commit (закоммитит база вместе
    с активацией); нельзя перенести, выключено или расчёт упал — «замена» (`plan=`);
    тот же тариф — дни складываются, как было; пауза гасится.
  * Веб подтверждения не спрашивает, поэтому подарок, при котором дни пропадут,
    не активирует: 409 с понятной причиной. Без потерь — активирует.
  * Бот: всплывающее предупреждение перед активацией называет бонус и влезает в 200 символов.
"""

import importlib
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from types import SimpleNamespace
from typing import Any

import pytest

importlib.import_module("src.web.endpoints.public")
offers_mod = importlib.import_module("overlay_patches.public_subscription")
cabinet_promo = importlib.import_module("src.web.endpoints.public.promocode")
activate = importlib.import_module("src.application.use_cases.promocode.commands.activate")
gift_days = importlib.import_module("overlay_patches.promocode_gift_days")
confirm_mod = importlib.import_module("overlay_patches.promocode_gift_confirm")
carry = importlib.import_module("src.infrastructure.services.overlay_plan_change")
tr = importlib.import_module("overlay_patches.translations_ru")

from remnapy.enums.users import TrafficLimitStrategy  # noqa: E402

from src.application.dto import PlanSnapshotDto  # noqa: E402
from src.core.enums import AuthType, PlanType, PromocodeRewardType, SubscriptionStatus  # noqa: E402

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
DAY = 86400
OLD, GIFT = 10, 20


# ── чистые правила ──────────────────────────────────────────────────────────


def test_list_amount_exact_term_else_most_expensive_day():
    prices = {(GIFT, 30, "RUB"): Fraction(300), (GIFT, 365, "RUB"): Fraction(2190), (GIFT, 30, "XTR"): Fraction(9)}
    assert carry.promo_list_amount(prices, GIFT, 30, "RUB") == 300
    # 45 дн. в витрине нет: самая высокая цена дня — 10/день (месяц), не 6 (год).
    assert carry.promo_list_amount(prices, GIFT, 45, "RUB") == 450
    assert carry.promo_list_amount(prices, OLD, 30, "RUB") is None
    assert carry.promo_list_amount(prices, GIFT, 30, "USD") is None
    assert carry.promo_list_amount(prices, GIFT, 0, "RUB") is None


def result(mode="carry", **over):
    base = dict(mode=mode, remaining_seconds=29 * DAY, remaining_days=29, value=Fraction(116),
                new_day_price=Fraction(10), bonus_days=11, bonus_seconds=0, lost_days=0, capped=False)
    base.update(over)
    return carry.CarryResult(**base)


def test_web_blocks_only_when_days_are_lost():
    assert carry.promo_loses_days(result()) is False
    assert carry.promo_loses_days(result(lost_days=3)) is True
    assert carry.promo_loses_days(result("lifetime", remaining_days=None)) is True
    assert carry.promo_loses_days(result("refund", bonus_days=0)) is True
    assert carry.promo_loses_days(result("unpriced", bonus_days=0)) is True
    assert carry.promo_loses_days(result("none", remaining_days=0, bonus_days=0)) is False
    assert carry.promo_loses_days(result("reserve", remaining_days=0, bonus_days=0)) is False
    assert "3 дн." in carry.promo_block_reason(result(lost_days=3))
    assert "бессрочная" in carry.promo_block_reason(result("lifetime"))


async def test_promo_carry_not_involved_for_same_plan_trial_or_no_subscription(monkeypatch):
    async def must_not_load(*args, **kwargs):
        raise AssertionError("состояние грузить не нужно")

    monkeypatch.setattr(carry, "load_carry_state", must_not_load)
    same = SimpleNamespace(is_trial=False, plan_snapshot=SimpleNamespace(id=GIFT))
    trial = SimpleNamespace(is_trial=True, plan_snapshot=SimpleNamespace(id=OLD))
    for sub in (same, trial, None):
        assert await carry.promo_carry(object(), user_id=1, subscription=sub, plan_id=GIFT, duration=30, now=NOW) is None
    other = SimpleNamespace(is_trial=False, plan_snapshot=SimpleNamespace(id=OLD))
    assert await carry.promo_carry(object(), user_id=1, subscription=other, plan_id=GIFT, duration=0, now=NOW) is None


# ── активация на подделках ──────────────────────────────────────────────────


class Log(list):
    def names(self):
        return [n for n, _ in self]


class Nested:
    def __init__(self, log):
        self.log = log

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.log.append(("savepoint_rollback" if exc_type else "savepoint_release", None))
        return False


class Session:
    def __init__(self, log):
        self.log = log

    def begin_nested(self):
        return Nested(self.log)

    async def commit(self):
        self.log.append(("commit", None))


class Panel:
    def __init__(self, log):
        self.log = log

    async def update_user(self, user, uuid, plan=None, subscription=None, reset_traffic=False):
        self.log.append(("update_user", {"plan": plan, "expire": getattr(subscription, "expire_at", None),
                                         "status": getattr(subscription, "status", None)}))
        expire = subscription.expire_at if subscription is not None else NOW + timedelta(days=plan.duration)
        return SimpleNamespace(status="ACTIVE", expire_at=expire, subscription_url="https://example.test/s/1")


def gift_plan(plan_id=GIFT, duration=30):
    return PlanSnapshotDto(id=plan_id, name=f"P{plan_id}", type=PlanType.BOTH,
                           traffic_limit_strategy=TrafficLimitStrategy.NO_RESET,
                           traffic_limit=100, device_limit=3, duration=duration)


def paid_state(frozen=None, unlimited=False, prices=None):
    async def load(session, *, user_id, subscription, now, exclude_payment_id=None, extra_plan_ids=(), extras=()):
        session.log.append(("load_state", tuple(extra_plan_ids)))
        expire = subscription.expire_at
        return carry.CarryState(
            status="ACTIVE", is_unlimited=unlimited, expire_at=expire, frozen_seconds=frozen,
            reserve_expire_at=None, refund_recent=False, old_plan_id=subscription.plan_id,
            layers=(carry.Layer("create", "p", 30 * DAY, "RUB", Fraction(120), Fraction(120), OLD, 30),),
            prices=prices if prices is not None else {(GIFT, 30, "RUB"): Fraction(240)},
        )

    return load


@pytest.fixture
def world(monkeypatch):
    log = Log()
    records: list = []

    async def record(session, **kwargs):
        session.log.append(("record", kwargs))
        records.append(kwargs)

    async def currency(session):
        return "RUB"

    async def close_freeze(session, user_id):
        session.log.append(("close_freeze", user_id))

    monkeypatch.setattr(carry, "load_carry_state", paid_state())
    monkeypatch.setattr(carry, "record_carryover", record)
    monkeypatch.setattr(carry, "default_currency", currency)
    monkeypatch.setattr(carry, "close_freeze", close_freeze)
    monkeypatch.setattr(carry, "load_config", lambda: {"enabled": True, "notify_admins": True})
    monkeypatch.setattr(gift_days, "datetime_now", lambda: NOW)

    plan = gift_plan()
    interactor = activate.ActivatePromocode(
        None, None, None, None, Panel(log), None, None,
        SimpleNamespace(load=lambda raw, cls: plan), Session(log),
    )
    subscription = SimpleNamespace(
        id=5, is_trial=False, user_remna_id=uuid.uuid4(), expire_at=NOW + timedelta(days=29),
        plan_snapshot=SimpleNamespace(id=OLD, name="OLD"), status=SubscriptionStatus.ACTIVE,
        traffic_limit=0, device_limit=1, traffic_limit_strategy=None, tag=None,
        internal_squads=[], external_squad=None, url="u", created_at=None,
    )
    return SimpleNamespace(log=log, records=records, interactor=interactor, sub=subscription, plan=plan,
                           monkeypatch=monkeypatch)


async def apply_gift(w):
    promo = SimpleNamespace(plan_snapshot={"id": w.plan.id})
    user = SimpleNamespace(id=42, log="[USER:42]")
    return await w.interactor._apply_subscription(SimpleNamespace(log="[USER:42]"), user, promo, w.sub)


def panel_call(w):
    return next(d for n, d in w.log if n == "update_user")


async def test_other_plan_carries_remainder_into_gift_days(world):
    pending = await apply_gift(world)
    # 29 дн. по 4/день = 116; подарок 240/30 = 8/день → 14 дн.
    call = panel_call(world)
    assert call["plan"] is None, "срок пишем сами — ветка subscription"
    assert call["expire"] == NOW + timedelta(days=30 + 14)
    assert pending.subscription_update.expire_at == NOW + timedelta(days=44)
    assert pending.subscription_update.plan_snapshot.id == GIFT
    rec = world.records[0]
    assert rec["source"] == "promocode" and rec["payment_id"] is None
    assert rec["subscription_id"] == 5 and rec["old_subscription_id"] == 5
    assert rec["result"].bonus_days == 14 and rec["old_plan_id"] == OLD
    assert "commit" not in world.log.names(), "журнал закоммитит база вместе с активацией"


async def test_lifetime_is_replaced_as_before_but_journal_cuts(world):
    world.monkeypatch.setattr(carry, "load_carry_state", paid_state(unlimited=True))
    world.sub.expire_at = NOW.replace(year=2099)
    await apply_gift(world)
    assert panel_call(world)["plan"] is not None, "перенести нельзя — замена, как раньше"
    assert world.records[0]["result"].mode == "lifetime"


async def test_unpriced_gift_is_replaced(world):
    world.monkeypatch.setattr(carry, "load_carry_state", paid_state(prices={}))
    await apply_gift(world)
    assert panel_call(world)["plan"] is not None
    assert world.records[0]["result"].mode == "unpriced"


async def test_switch_off_is_plain_replace_without_journal(world):
    world.monkeypatch.setattr(carry, "load_config", lambda: {"enabled": False, "notify_admins": True})
    await apply_gift(world)
    assert panel_call(world)["plan"] is not None
    assert "load_state" not in world.log.names() and world.records == []


async def test_same_plan_days_still_add_up(world):
    world.sub.plan_snapshot = SimpleNamespace(id=GIFT, name="P20")
    await apply_gift(world)
    call = panel_call(world)
    assert call["plan"] is None and call["expire"] == NOW + timedelta(days=29 + 30)
    assert "load_state" not in world.log.names() and world.records == []


async def test_paused_subscription_is_unpaused_on_carry(world):
    world.monkeypatch.setattr(carry, "load_carry_state", paid_state(frozen=10 * DAY))
    world.sub.status = SubscriptionStatus.DISABLED
    await apply_gift(world)
    assert "close_freeze" in world.log.names()
    assert panel_call(world)["status"] == SubscriptionStatus.ACTIVE


async def test_state_failure_falls_back_to_replace(world):
    async def broken(*args, **kwargs):
        raise RuntimeError("db down")

    world.monkeypatch.setattr(carry, "load_carry_state", broken)
    await apply_gift(world)
    assert panel_call(world)["plan"] is not None
    assert "savepoint_rollback" in world.log.names()
    assert world.records == []


async def test_journal_failure_does_not_break_activation(world):
    async def broken(session, **kwargs):
        raise RuntimeError('relation "plan_change_carryovers" does not exist')

    world.monkeypatch.setattr(carry, "record_carryover", broken)
    pending = await apply_gift(world)
    assert pending.subscription_update is world.sub
    assert "savepoint_rollback" in world.log.names()


def test_constructor_and_method_are_ours_and_names_resolve():
    from overlay_patches import expect_names_resolve

    assert getattr(activate.ActivatePromocode.__init__, "_overlay_wrapped", False)
    assert carry.promo_patch_applied()
    expect_names_resolve("src.application.use_cases.promocode.commands.activate", "подарок")


# ── веб: подарок с потерей дней не активируется ─────────────────────────────


class Activator:
    def __init__(self):
        self.calls = 0

    async def __call__(self, user, dto):
        self.calls += 1
        return SimpleNamespace(reward_type=PromocodeRewardType.SUBSCRIPTION, code="GIFT1", reward=None)


class Promos:
    def __init__(self, promo):
        self.promo = promo

    async def get_by_code(self, code):
        return self.promo


class Subs:
    def __init__(self, sub):
        self.sub = sub

    async def get_current(self, user_id):
        return self.sub


class RollbackSession:
    def __init__(self):
        self.rollbacks = 0

    async def rollback(self):
        self.rollbacks += 1


ENDPOINTS = {
    # Кабинет зовёт /promocode/activate; /subscription/promocode — ручка базы для клиентов.
    "cabinet": lambda: cabinet_promo.activate_promocode_endpoint.__dishka_orig_func__,
    "subscription": lambda: offers_mod.activate_promocode_web.__dishka_orig_func__,
}


async def web(monkeypatch, *, carried, enabled=True, promo_plan=GIFT, current_plan=OLD, endpoint="subscription"):
    async def promo_carry(session, **kwargs):
        if isinstance(carried, Exception):
            raise carried
        return carried

    monkeypatch.setattr(carry, "promo_carry", promo_carry)
    monkeypatch.setattr(carry, "load_config", lambda: {"enabled": enabled, "notify_admins": True})
    activator = Activator()
    promo = SimpleNamespace(reward_type=PromocodeRewardType.SUBSCRIPTION, plan_snapshot={"id": promo_plan, "duration": 30})
    sub = SimpleNamespace(is_trial=False, plan_snapshot=SimpleNamespace(id=current_plan))
    user = SimpleNamespace(id=42, auth_type=AuthType.TELEGRAM, is_email_verified=False)
    raw = ENDPOINTS[endpoint]()
    activator.code = "GIFT1"
    try:
        await raw(body=SimpleNamespace(code="GIFT1"), user=user, activate_promocode=activator,
                  session=RollbackSession(), subscription_dao=Subs(sub), promocode_dao=Promos(promo))
        return activator.calls, None
    except Exception as exc:  # noqa: BLE001
        return activator.calls, exc


def state_stub():
    return carry.CarryState("ACTIVE", False, NOW, None, None, False, OLD, (), {})


@pytest.mark.parametrize("endpoint", sorted(ENDPOINTS))
async def test_web_refuses_gift_that_loses_days(monkeypatch, endpoint):
    calls, exc = await web(monkeypatch, carried=carry.PromoCarry(result(lost_days=3), state_stub(), "RUB"), endpoint=endpoint)
    assert calls == 0
    assert getattr(exc, "status_code", None) == 409 and "перенести нельзя" in exc.detail


@pytest.mark.parametrize("endpoint", sorted(ENDPOINTS))
async def test_web_activates_gift_with_full_carry_or_when_not_involved(monkeypatch, endpoint):
    calls, exc = await web(monkeypatch, carried=carry.PromoCarry(result(), state_stub(), "RUB"), endpoint=endpoint)
    assert (calls, exc) == (1, None)
    calls, exc = await web(monkeypatch, carried=None, endpoint=endpoint)
    assert (calls, exc) == (1, None)
    calls, exc = await web(monkeypatch, carried=carry.PromoCarry(result(lost_days=3), state_stub(), "RUB"), enabled=False,
                           endpoint=endpoint)
    assert (calls, exc) == (1, None), "выключено — как раньше"


@pytest.mark.parametrize("endpoint", sorted(ENDPOINTS))
async def test_web_state_failure_does_not_burn_silently(monkeypatch, endpoint):
    calls, exc = await web(monkeypatch, carried=RuntimeError("db down"), endpoint=endpoint)
    assert calls == 0 and getattr(exc, "status_code", None) == 409


# ── бот: предупреждение перед активацией ────────────────────────────────────


def test_bot_alert_names_bonus_and_fits_telegram():
    current = SimpleNamespace(plan_snapshot=SimpleNamespace(name="Очень длинное название текущего тарифа", id=OLD),
                              expire_at=NOW + timedelta(days=29))
    plan = SimpleNamespace(name="Очень длинное название подарочного тарифа", duration=30, id=GIFT)
    text = confirm_mod.gift_warning(current, plan, carry.PromoCarry(result(bonus_days=14), state_stub(), "RUB"))
    assert "добавится 14 дн." in text and "к 30 дн." in text
    assert len(text) <= 200
    lost = confirm_mod.gift_warning(current, plan, carry.PromoCarry(result(bonus_days=11, lost_days=3), state_stub(), "RUB"))
    assert "3 дн. перенести нельзя" in lost and len(lost) <= 200
    old = confirm_mod.gift_warning(current, plan, None)
    assert "НЕ переносится" in old


def test_ftl_promo_text_no_longer_promises_reset():
    from pathlib import Path

    from src.core.constants import ASSETS_DEFAULT_DIR

    messages = (Path(ASSETS_DEFAULT_DIR) / "translations" / "ru" / "messages.ftl").read_text("utf8")
    assert messages.count(tr.PROMO_REPLACE_OLD) == 1
    assert "будут сброшены" not in tr._fix([messages])[0].split("will_replace_subscription ->", 1)[1][:400]
