"""Перенос остатка при смене тарифа: смысл чистой функции `compute_carryover`.

ЧТО ЗАПИРАЕМ. Это деньги: сколько дней человек получит за оставшийся срок, когда
переходит на другой тариф. Каждая строка ниже — правило, от которого зависит, не
подарит ли магазин лишнего и не отнимет ли у человека оплаченное:

  * старая цена дня — УПЛАЧЕННАЯ (скидка не превращается в лишние дни), новая —
    витринная из счёта (личная скидка не умножает остаток);
  * слои берутся с конца (LIFO): остаток — это поздние дни;
  * остаток считается до секунды, вниз округляется только бонус;
  * тот же тариф — 1:1; технический предел — 3650 дн.;
  * дни без оплаты — по самой дешёвой цене дня текущего тарифа; тарифа нет в витрине —
    «перенести нельзя»;
  * резерв, бессрочная, удалённая, возврат, бессрочный новый тариф — без переноса;
  * другая валюта — только через витрину владельца, курсов нет;
  * докупки (точка расширения следующей фичи) — в carry и same_plan, только в той же
    валюте; иначе видно в `extras_lost`.

Цены синтетические (кратные — чтобы проверять в уме). Запуск — внутри образа бота.
"""

import importlib
import math
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import pytest

carry = importlib.import_module("src.infrastructure.services.overlay_plan_change")

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
DAY = 86400
OLD, NEW = 10, 20


def layer(kind, paid, days, *, listed=None, currency="RUB", plan_id=OLD, pid=None):
    return carry.Layer(
        kind=kind,
        payment_id=pid,
        seconds=days * DAY,
        currency=currency,
        paid=Fraction(paid),
        listed=Fraction(listed if listed is not None else paid),
        plan_id=plan_id,
        duration=days,
    )


def state(
    *,
    left=None,
    left_seconds=None,
    layers=(),
    prices=None,
    old_plan_id=OLD,
    status="ACTIVE",
    unlimited=False,
    frozen=None,
    reserve=None,
    expire=None,
    refund=False,
    extras=(),
    active=None,
):
    if expire is None:
        seconds = left_seconds if left_seconds is not None else int((left or 0) * DAY)
        expire = NOW + timedelta(seconds=seconds)
    if unlimited:
        expire = expire.replace(year=carry.UNLIMITED_YEAR)
    return carry.CarryState(
        status=status,
        is_unlimited=unlimited,
        expire_at=expire,
        frozen_seconds=frozen,
        reserve_expire_at=reserve,
        refund_recent=refund,
        old_plan_id=old_plan_id,
        layers=tuple(layers),
        prices={k: Fraction(v) for k, v in (prices or {}).items()},
        extras=tuple(extras),
        active_plan_ids=active,
    )


def run(st, *, amount, duration=30, plan_id=NEW, currency="RUB"):
    return carry.compute_carryover(
        st,
        new_plan_id=plan_id,
        new_duration=duration,
        new_list_amount=amount,
        currency=currency,
        now=NOW,
    )


# ── A–M: формула ────────────────────────────────────────────────────────────


def test_a_paid_layer_converted_at_new_day_price():
    """120 ₽ за 30 дн. = 4/день; 29 дн. = 116; новый 240/30 = 8/день → 14 (14,5 вниз)."""
    got = run(state(left=29, layers=[layer("create", 120, 30)]), amount=240)
    assert got.mode == "carry"
    assert got.bonus_days == 14
    assert got.lost_days == 0 and not got.capped
    assert got.value == 116
    assert got.new_day_price == 8


def test_b_layers_are_taken_from_the_end_lifo():
    """Создающий 120/30 (4/день) + продление 270/90 (3/день), остаток 100 дн., цель 10/день.

    LIFO: 90 дн. продления по 3 + 10 дн. создающего по 4 = 310 → 31.
    FIFO дал бы 30×4 + 70×3 = 330 → 33 — человек получил бы дни по цене старых денег.
    """
    st = state(left=100, layers=[layer("renew", 270, 90, pid="r"), layer("create", 120, 30, pid="c")])
    got = run(st, amount=300)
    assert got.value == 310
    assert got.bonus_days == 31
    assert got.source_payment_ids == ("r", "c")


def test_c_old_price_is_paid_new_price_is_listed():
    """Создающий final 96 / original 120 (скидка при оплате), цель original 240.

    Старая по уплаченной: 96 → /8 = 12. По витрине старого было бы 120/8 = 15,
    по цене со скидкой нового (192/30) — 96/6,4 = 15. Обе ошибки дарят дни.
    """
    st = state(left=30, layers=[layer("create", 96, 30, listed=120)])
    got = run(st, amount=Decimal("240"))
    assert got.bonus_days == 12


def test_d_unpaid_days_at_cheapest_showcase_day_price():
    """Слоёв нет (подарок): 10 дн. по самой дешёвой цене дня текущего тарифа (1095/365 = 3)."""
    prices = {(OLD, 30, "RUB"): 120, (OLD, 365, "RUB"): 1095}
    got = run(state(left=10, prices=prices), amount=300)
    assert got.value == 30
    assert got.bonus_days == 3
    assert got.lost_days == 0


def test_d_imported_plan_days_cannot_be_carried():
    """Импорт (id −1): цены нет — «перенести нельзя», честно, а не по чужой цене."""
    got = run(state(left=10, old_plan_id=-1, prices={(OLD, 30, "RUB"): 120}), amount=300)
    assert got.mode == "carry"
    assert got.bonus_days == 0
    assert got.lost_days == 10


def test_d_plan_outside_showcase_days_cannot_be_carried():
    """Решение владельца: тариф выключен из витрины — его дни без оплаты не оцениваются."""
    prices = {(OLD, 30, "RUB"): 120}
    got = run(state(left=10, prices=prices, active=frozenset({NEW})), amount=300)
    assert got.bonus_days == 0 and got.lost_days == 10
    got = run(state(left=10, prices=prices, active=frozenset({OLD, NEW})), amount=300)
    assert got.bonus_days == 4 and got.lost_days == 0


def test_e_foreign_currency_layer_through_owner_showcase():
    """Слой в звёздах final 40 / original 50 за 30 дн.; витрина того же срока в RUB = 120.

    Цена дня слоя в рублях = 120 × 40/50 / 30 = 16/5. Курсов нет — только витрина.
    """
    xtr = layer("create", 40, 30, listed=50, currency="XTR")
    assert carry._layer_day_price(xtr, "RUB", {(OLD, 30, "RUB"): Fraction(120)}) == Fraction(16, 5)
    got = run(state(left=30, layers=[xtr], prices={(OLD, 30, "RUB"): 120}), amount=240)
    assert got.value == 96
    assert got.bonus_days == 12


def test_e_foreign_currency_layer_without_price_becomes_unpaid_days():
    """Рублёвой цены у тарифа слоя нет — его дни идут в «без оплаты» (здесь цены нет вовсе)."""
    xtr = layer("create", 40, 30, listed=50, currency="XTR")
    got = run(state(left=30, layers=[xtr], prices={(OLD, 30, "XTR"): 50}), amount=240)
    assert got.bonus_days == 0
    assert got.lost_days == 30
    assert any(b.get("converted") is False for b in got.breakdown)


def test_f_same_plan_is_one_to_one_to_the_second():
    """CHANGE на свой же тариф: остаток 12,5 дн. сохраняется целиком, как продление."""
    st = state(left_seconds=int(12.5 * DAY), layers=[layer("create", 96, 30, listed=120)], old_plan_id=NEW)
    got = run(st, amount=240)
    assert got.mode == "same_plan"
    assert got.bonus_seconds == int(12.5 * DAY)
    assert got.bonus_days == 0
    assert carry.target_expire(NOW, 30, got) == NOW + timedelta(days=42, hours=12)


def test_g_technical_cap_3650_days():
    """1000 дн. по 100/день на тариф за 1/день: 100000 дн. → предел 3650, потеряно 964."""
    st = state(left=1000, layers=[layer("create", 100000, 1000)])
    got = run(st, amount=30)
    assert got.capped
    assert got.bonus_days == carry.MAX_BONUS_DAYS
    assert got.lost_days == 1000 - math.floor(1000 * 3650 / 100000)
    assert got.lost_days == 964


def test_h_remainder_worth_less_than_one_new_day():
    got = run(state(left=1, layers=[layer("create", 30, 30)]), amount=300)
    assert got.mode == "carry"
    assert got.bonus_days == 0
    assert got.lost_days == 0


def test_i_reserve_window_is_not_carried():
    """Открытый резерв и срок = срок резерва: бесплатная страховка не переносится."""
    reserve_until = NOW + timedelta(days=5)
    st = state(expire=reserve_until, reserve=reserve_until, layers=[layer("create", 120, 30)])
    got = run(st, amount=240)
    assert got.mode == "reserve"
    assert got.remaining_seconds == 0 and got.bonus_days == 0
    # В пределах часа — всё ещё резерв.
    st = state(expire=reserve_until + timedelta(minutes=59), reserve=reserve_until)
    assert run(st, amount=240).mode == "reserve"


def test_i_paid_renewal_over_open_reserve_is_real():
    """Продлился, пока выдача ещё «открыта»: срок дальше резерва на 30 дн. — дни настоящие."""
    reserve_until = NOW + timedelta(days=5)
    st = state(expire=reserve_until + timedelta(days=30), reserve=reserve_until, layers=[layer("renew", 120, 30)])
    got = run(st, amount=240)
    assert got.mode == "carry"
    assert got.remaining_days == 35
    assert got.bonus_days > 0


def test_j_paused_subscription_uses_saved_remainder():
    """На паузе срок в базе стоит (и уже в прошлом) — остаток из remaining_seconds."""
    st = state(expire=NOW - timedelta(days=3), frozen=int(30.5 * DAY), layers=[layer("create", 120, 30)])
    got = run(st, amount=240)
    assert got.remaining_seconds == int(30.5 * DAY)
    # 30 дн. слоя по 4 + 0,5 дн. без оплаты (цен тарифа нет → потеряны).
    assert got.value == 120
    assert got.bonus_days == 15
    assert got.lost_days == 1


def test_k_random_bonus_never_rounds_in_favour_beyond_a_day():
    rnd = random.Random(20260917)
    for _ in range(500):
        layers = [
            layer(rnd.choice(["renew", "create"]), rnd.randint(1, 5000), rnd.choice([7, 30, 90, 180, 365]))
            for _ in range(rnd.randint(0, 4))
        ]
        prices = {(OLD, d, "RUB"): rnd.randint(1, 5000) for d in rnd.sample([30, 90, 365], rnd.randint(0, 3))}
        st = state(left_seconds=rnd.randint(1, 800 * DAY), layers=layers, prices=prices)
        amount = rnd.randint(1, 10000)
        duration = rnd.choice([7, 30, 90, 365])
        got = run(st, amount=amount, duration=duration)
        new_day = Fraction(amount, duration)
        assert got.bonus_days >= 0
        assert got.lost_days <= math.ceil(got.remaining_seconds / DAY)
        if not got.capped:
            assert got.bonus_days * new_day <= got.value < (got.bonus_days + 1) * new_day
        else:
            assert got.bonus_days == carry.MAX_BONUS_DAYS


def test_l_round_trip_does_not_grow_value():
    """A → B → A без течения времени: итоговая стоимость не больше исходной плюс уплаченное.

    Перенос ложится слоем новой строки (как его отдаст журнал) — цена его дня не выше
    той, по которой перенос считался; округление вниз на каждом шаге только уменьшает.
    """
    a_price, b_price = 310, 170  # за 30 дн. — некратные, чтобы округления были
    st1 = state(left=30, layers=[layer("create", a_price, 30, plan_id=OLD)])
    to_b = run(st1, amount=b_price, plan_id=NEW)
    journal_row = (
        "purchase", NOW, "RUB", carry.to_decimal(to_b.value), to_b.bonus_days, to_b.bonus_seconds,
        carry.to_decimal(to_b.new_day_price, 6), to_b.mode, to_b.capped, to_b.remaining_seconds, NEW, 30,
    )
    carried = carry._carry_layer(journal_row)
    assert carried is not None and carried.seconds == to_b.bonus_days * DAY
    left2 = 30 + to_b.bonus_days
    st2 = state(left=left2, layers=[layer("create", b_price, 30, plan_id=NEW), carried], old_plan_id=NEW)
    back = run(st2, amount=a_price, plan_id=OLD)
    assert back.value <= to_b.value + b_price
    assert back.value <= Fraction(a_price) + b_price
    assert (back.bonus_days + 30) * Fraction(a_price, 30) <= Fraction(a_price) + b_price + a_price


def test_m_remainder_is_valued_to_the_second_not_whole_days():
    """29,9 дн. по 4/день на 3/день: 119,6/3 = 39,87 → 39. До целых дней было бы 38."""
    st = state(left_seconds=int(29.9 * DAY), layers=[layer("create", 120, 30)])
    got = run(st, amount=90)
    assert got.bonus_days == 39


# ── N: режимы без переноса ───────────────────────────────────────────────────


def test_n_modes_without_carry():
    paid = [layer("create", 120, 30)]
    lifetime = run(state(left=30, layers=paid, unlimited=True), amount=240)
    assert lifetime.mode == "lifetime" and lifetime.remaining_days is None and lifetime.bonus_days == 0

    deleted = run(state(left=30, layers=paid, status="DELETED"), amount=240)
    assert deleted.mode == "none" and deleted.remaining_seconds == 0

    refund = run(state(left=20, layers=paid, refund=True), amount=240)
    assert refund.mode == "refund" and refund.bonus_days == 0 and refund.lost_days == 20

    unlimited_target = run(state(left=20, layers=paid), amount=0, duration=0)
    assert unlimited_target.mode == "none" and unlimited_target.lost_days == 0

    no_price = run(state(left=20, layers=paid), amount=0)
    assert no_price.mode == "unpriced" and no_price.lost_days == 20 and no_price.bonus_days == 0

    expired = run(state(expire=NOW - timedelta(days=1), layers=paid), amount=240)
    assert expired.mode == "none"


def test_n_state_mode_is_target_independent():
    assert carry.state_mode(state(left=5), NOW) == ("carry", 5 * DAY)
    assert carry.state_mode(state(left=5, refund=True), NOW) == ("refund", 5 * DAY)
    assert carry.state_mode(state(left=5, unlimited=True), NOW) == ("lifetime", None)


def test_unlimited_year_matches_base():
    from src.core.constants import UNLIMITED_EXPIRE_YEAR

    assert carry.UNLIMITED_YEAR == UNLIMITED_EXPIRE_YEAR


# ── O: скидка 100% и цена нового срока ──────────────────────────────────────


def test_o_full_discount_takes_list_price_from_invoice_currency_table():
    """База при 100% и без цены в валюте берёт original из ДРУГОЙ валюты — не верим ему."""
    prices = {(NEW, 30, "RUB"): Fraction(240)}
    free = {"original_amount": "5", "discount_percent": 100, "final_amount": "0"}
    assert carry.new_day_amount(free, NEW, 30, "RUB", prices) == Decimal("240")
    assert carry.new_day_amount(free, NEW, 30, "RUB", {}) is None
    paid = {"original_amount": "240", "discount_percent": 20, "final_amount": "192"}
    assert carry.new_day_amount(paid, NEW, 30, "RUB", {}) == Decimal("240")

    st = state(left=30, layers=[layer("create", 120, 30)])
    assert run(st, amount=carry.new_day_amount(free, NEW, 30, "RUB", {})).mode == "unpriced"


# ── P: докупки (точка расширения) ───────────────────────────────────────────


def extra(amount, total_days, left_days, currency="RUB"):
    return carry.ParallelLayer(
        amount=Fraction(amount), currency=currency,
        total_seconds=total_days * DAY, remaining_seconds=left_days * DAY, ref="slot",
    )


def test_p_extras_add_unused_value_in_carry():
    """Докупка 90 за 30 дн., осталось 10 → +30 к стоимости → на 3/день +10 дн."""
    st = state(left=10, old_plan_id=-1, extras=[extra(90, 30, 10)])
    got = run(st, amount=90)
    assert got.value == 30
    assert got.bonus_days == 10
    assert got.lost_days == 10  # основной остаток без цены — честно потерян
    assert got.extras_lost == 0


def test_p_extras_add_days_in_same_plan_too():
    st = state(left=5, old_plan_id=NEW, extras=[extra(90, 30, 10)])
    got = run(st, amount=90)
    assert got.mode == "same_plan"
    assert got.bonus_seconds == 5 * DAY
    assert got.bonus_days == 10
    assert got.added_days == 15
    assert carry.target_expire(NOW, 30, got) == NOW + timedelta(days=45)


def test_p_extras_in_other_currency_are_not_converted_and_visible():
    for old_plan in (OLD, NEW):  # carry и same_plan
        st = state(left=10, old_plan_id=old_plan, layers=[layer("create", 90, 30, plan_id=old_plan)],
                   extras=[extra(90, 30, 10, currency="XTR")])
        got = run(st, amount=90)
        assert got.extras_lost == 1
        assert got.value == 30
        assert any(b.get("kind") == "device" and b.get("converted") is False for b in got.breakdown)


def test_p_extras_are_not_carried_in_other_modes():
    ex = [extra(90, 30, 10)]
    for st in (
        state(left=10, unlimited=True, extras=ex),
        state(left=10, refund=True, extras=ex),
        state(left=10, extras=ex, layers=[layer("create", 90, 30)]),
    ):
        amount = 0 if not st.refund_recent and not st.is_unlimited else 90
        got = run(st, amount=amount)
        assert got.added_days == 0
        assert got.extras_lost == 1


# ── срок и слой переноса ────────────────────────────────────────────────────


def test_target_expire_per_mode():
    carried = run(state(left=29, layers=[layer("create", 120, 30)]), amount=240)
    assert carry.target_expire(NOW, 30, carried) == NOW + timedelta(days=44)
    lifetime = run(state(left=29, unlimited=True), amount=240)
    assert carry.target_expire(NOW, 30, lifetime) == NOW + timedelta(days=30)
    failed = carry.failed_result(NOW + timedelta(days=12), NOW)
    assert failed.mode == "failed" and failed.remaining_days == 12 and failed.bonus_days == 0
    assert carry.target_expire(NOW, 30, failed) == NOW + timedelta(days=30)


def test_carry_layer_value_never_exceeds_converted_days():
    """Доля дня, отброшенная округлением, не переезжает в следующий перенос."""
    row = ("purchase", NOW, "RUB", Decimal("116"), 14, 0, Decimal("8"), "carry", False, 29 * DAY, NEW, 30)
    got = carry._carry_layer(row)
    assert got.paid == 112 and got.seconds == 14 * DAY
    assert carry._carry_layer(("purchase", NOW, "RUB", Decimal("0"), 0, 0, None, "none", False, 0, NEW, 30)) is None
