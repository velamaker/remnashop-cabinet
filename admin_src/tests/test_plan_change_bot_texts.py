"""Тексты смены тарифа в боте: подтверждение и итог говорят правду о переносе.

ЧТО ЗАПИРАЕМ.
  * В базовых `messages.ftl` каждая строка, которую мы заменяем, есть РОВНО один раз —
    иначе fail-closed в translations_ru не пустит правку, а мы узнаем об этом тут.
  * Бандл, собранный тем же `fluent_compiler` из базовых переводов после замены,
    рендерит каждый вариант `$carry_state` с числами без разделителей тысяч.
  * Вариант ПО УМОЛЧАНИЮ — «без пересчета»: переменной нет (обёртка геттера не встала)
    или перенос выключен — бот не пообещает перенос, которого не будет.
  * Итог покупки: строка переноса появляется только при явном `carry_added = YES`; без
    переменных (обёртка не встала) итог рендерится без чисел и не падает.
  * Обёртка окна подтверждения: сбой расчёта → OFF; переменные есть и у RENEW.
"""

import importlib
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest
from fluent_compiler.bundle import FluentBundle

tr = importlib.import_module("overlay_patches.translations_ru")
bot = importlib.import_module("overlay_patches.plan_change_bot")
carry = importlib.import_module("src.infrastructure.services.overlay_plan_change")

from src.core.constants import ASSETS_DEFAULT_DIR  # noqa: E402

RU = Path(ASSETS_DEFAULT_DIR) / "translations" / "ru"
PLAN_CHANGE = [p for p in tr.PATCHES if p[1] in (tr.PLAN_CHANGE_LINK_OLD, tr.PLAN_CHANGE_CONFIRM_OLD, tr.PLAN_CHANGE_SUCCESS_OLD)]


def base_texts() -> list[str]:
    return [f.read_text("utf8") for f in sorted(RU.rglob("*.ftl"))]


@pytest.fixture(scope="module")
def bundle() -> FluentBundle:
    return FluentBundle.from_string(locale="ru", text="\n".join(tr._fix(base_texts())), use_isolating=False)


def test_each_replaced_line_exists_exactly_once_in_base():
    assert len(PLAN_CHANGE) == 3
    messages = (RU / "messages.ftl").read_text("utf8")
    for _file, old, new, title in PLAN_CHANGE:
        assert messages.count(old) == 1, title
    assert tr._missing_in_defaults() == []


CONFIRM_ARGS = {
    "purchase_type": "CHANGE",
    "plan": "DUO",
    "description": 0,
    "type": "BOTH",
    "devices": "3",
    "traffic": "100",
    "period": "30",
    "final_amount": 0,
    "discount_percent": 0,
    "is_personal_discount": 0,
    "original_amount": 0,
    "currency": "RUB",
    "plan_is_modified": 0,
}


def confirm(bundle: FluentBundle, **carry_vars) -> str:
    text, _errors = bundle.format("msg-subscription-confirm", {**CONFIRM_ARGS, **carry_vars})
    return text


def test_confirm_carry_numbers_without_grouping(bundle):
    text = confirm(bundle, carry_state="CARRY", carry_left=1200, carry_bonus=3650, carry_lost=0)
    assert "Остаток 1200 дн. пересчитаем по цене дня" in text
    assert "<b>3650 дн.</b>" in text
    assert "без пересчета" not in text


@pytest.mark.parametrize(
    "state, expected",
    [
        ("SMALL", "меньше одного дня нового плана"),
        ("LOST", "а ещё 9 дн. перенести нельзя — цена прежних дней неизвестна"),
        ("CAPPED", "это предел переноса, ещё 9 дн. не поместятся"),
        ("NOPRICE", "Остаток 5 дн. перенести нельзя: у выбранного срока нет цены"),
        ("LIFETIME", "Бессрочная подписка будет"),
        ("NONE", "будет <u>заменена</u> выбранной.</i>"),
        ("OFF", "без пересчета оставшегося срока"),
    ],
)
def test_confirm_each_state(bundle, state, expected):
    text = confirm(bundle, carry_state=state, carry_left=5, carry_bonus=2, carry_lost=9)
    assert expected in text


def test_confirm_without_variable_falls_back_to_no_carry_text(bundle):
    text, errors = bundle.format("msg-subscription-confirm", dict(CONFIRM_ARGS))
    assert "без пересчета оставшегося срока" in text
    assert "пересчитаем по цене дня" not in text
    assert errors, "Fluent сообщает об отсутствующей переменной, но не падает"


def test_renew_text_untouched(bundle):
    text = confirm(bundle, purchase_type="RENEW", carry_state="CARRY", carry_left=5, carry_bonus=2, carry_lost=0)
    assert "<u>продлена</u>" in text and "пересчитаем" not in text


def test_plan_link_text_no_longer_promises_burn(bundle):
    text, _ = bundle.format("msg-subscription-plan", {"name": "DUO", "description": 0, "purchase_type": "CHANGE"})
    assert "покажем перед оплатой" in text and "без пересчета" not in text


SUCCESS_ARGS = {
    "purchase_type": "CHANGE",
    "plan_name": "DUO",
    "traffic_limit": "100",
    "device_limit": "3",
    "expire_time": "30",
    "added_duration": "30",
    "is_mini_app": 0,
    "is_mini_app_reserve": 0,
    "connection_url": "https://example.test",
    "subscription_url": "https://example.test",
    "connectable": 1,
}


def test_success_shows_carry_line_only_when_days_added(bundle):
    with_days, _ = bundle.format("msg-subscription-success", {**SUCCESS_ARGS, "carry_added": "YES", "carry_days": 14})
    assert "Остаток прежнего плана пересчитан: <b>+14 дн.</b>" in with_days
    without, _ = bundle.format("msg-subscription-success", {**SUCCESS_ARGS, "carry_added": "NO", "carry_days": 0})
    assert "пересчитан" not in without
    big, _ = bundle.format("msg-subscription-success", {**SUCCESS_ARGS, "carry_added": "YES", "carry_days": 3650})
    assert "+3650 дн." in big


def test_success_without_carry_variables_does_not_crash(bundle):
    """Скептик: обёртка геттера не встала — переменных нет. Итог покупки обязан отрисоваться."""
    text, _errors = bundle.format("msg-subscription-success", dict(SUCCESS_ARGS))
    assert "Ваша подписка была изменена" in text
    assert "пересчитан" not in text


# ── обёртка геттера ─────────────────────────────────────────────────────────


NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


class Retort:
    def load(self, raw, cls):
        return raw


class Dao:
    def __init__(self, current):
        self.current = current

    async def get_current(self, user_id):
        return self.current


class Gateways:
    async def get_by_type(self, kind):
        return SimpleNamespace(currency=SimpleNamespace(value="RUB"))


class Session:
    rollbacks = 0

    async def rollback(self):
        Session.rollbacks += 1


def manager(purchase_type="CHANGE"):
    plan = SimpleNamespace(id=20)
    return SimpleNamespace(
        dialog_data={
            "purchase_type": purchase_type,
            "PlanDto": plan,
            "selected_duration": 30,
            "selected_payment_method": "YOOMONEY",
            "final_pricing": {"original_amount": "240", "discount_percent": 0},
        }
    )


CURRENT = SimpleNamespace(
    id=5, is_trial=False, expire_at=NOW + timedelta(days=29), status="ACTIVE",
    plan_snapshot=SimpleNamespace(id=10), created_at=NOW - timedelta(days=1),
)


async def vars_with(monkeypatch, *, loader, purchase_type="CHANGE", active=True, current=CURRENT):
    monkeypatch.setattr(carry, "overlay_active", lambda: active)
    monkeypatch.setattr(carry, "load_carry_state", loader)
    monkeypatch.setattr(bot, "datetime_now", lambda: NOW)
    return await bot.carry_vars(
        session=Session(), subscription_dao=Dao(current), payment_gateway_dao=Gateways(),
        retort=Retort(), dialog_manager=manager(purchase_type), user=SimpleNamespace(id=42),
    )


async def paid_state(session, *, user_id, subscription, now, **kwargs):
    return carry.CarryState(
        status="ACTIVE", is_unlimited=False, expire_at=subscription.expire_at, frozen_seconds=None,
        reserve_expire_at=None, refund_recent=False, old_plan_id=10,
        layers=(carry.Layer("create", "p", 30 * 86400, "RUB", Fraction(120), Fraction(120), 10, 30),),
        prices={},
    )


async def test_confirm_vars_carry(monkeypatch):
    got = await vars_with(monkeypatch, loader=paid_state)
    assert got == {"carry_state": "CARRY", "carry_left": 29, "carry_bonus": 14, "carry_lost": 0}


async def test_confirm_vars_failure_is_off(monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("db down")

    got = await vars_with(monkeypatch, loader=broken)
    assert got["carry_state"] == "OFF"
    assert set(got) == {"carry_state", "carry_left", "carry_bonus", "carry_lost"}


async def test_confirm_vars_present_for_renew_and_trial_and_switch_off(monkeypatch):
    renew = await vars_with(monkeypatch, loader=paid_state, purchase_type="RENEW")
    assert renew["carry_state"] == "NONE" and "carry_bonus" in renew
    trial = await vars_with(monkeypatch, loader=paid_state, current=SimpleNamespace(**{**vars(CURRENT), "is_trial": True}))
    assert trial["carry_state"] == "NONE"
    off = await vars_with(monkeypatch, loader=paid_state, active=False)
    assert off["carry_state"] == "OFF"


def test_vars_for_result_mapping():
    def res(**over):
        base = dict(mode="carry", remaining_seconds=0, remaining_days=10, value=Fraction(0), new_day_price=None,
                    bonus_days=3, bonus_seconds=0, lost_days=0, capped=False)
        base.update(over)
        return carry.CarryResult(**base)

    assert bot.vars_for_result(res())["carry_state"] == "CARRY"
    assert bot.vars_for_result(res(bonus_days=0))["carry_state"] == "SMALL"
    assert bot.vars_for_result(res(lost_days=2))["carry_state"] == "LOST"
    assert bot.vars_for_result(res(capped=True, lost_days=5, lost_reason="cap"))["carry_state"] == "CAPPED"
    assert bot.vars_for_result(res(lost_days=5, lost_reason="old_price"))["carry_state"] == "LOST"
    assert bot.vars_for_result(res(mode="same_plan", bonus_days=0, bonus_seconds=10 * 86400))["carry_bonus"] == 10
    assert bot.vars_for_result(res(mode="unpriced", bonus_days=0, lost_days=10))["carry_state"] == "NOPRICE"
    assert bot.vars_for_result(res(mode="lifetime"))["carry_state"] == "LIFETIME"
    assert bot.vars_for_result(res(mode="reserve"))["carry_state"] == "NONE"
    assert bot.vars_for_result(res(mode="refund"))["carry_state"] == "OFF"


def test_getters_are_wrapped_and_names_resolve():
    from overlay_patches import expect_names_resolve

    getters = importlib.import_module(bot.TARGET_MODULE)
    assert getattr(getters.confirm_getter, "_overlay_wrapped", False)
    assert getattr(getters.success_payment_getter, "_overlay_wrapped", False)
    expect_names_resolve(bot.TARGET_MODULE, "тексты смены тарифа")
