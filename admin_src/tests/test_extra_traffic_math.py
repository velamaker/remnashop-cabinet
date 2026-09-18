"""Докупка трафика: арифметика лимита, право на покупку и цена.

ЧТО ЗАПИРАЕМ:
  * `target_limit`/`target_bytes` — пол «тариф + ещё действующие», безлимит не
    трогаем, ручная щедрость админа не пропадает;
  * порядок проверок `eligibility` (он часть смысла: LIMITED продаём, резерв и
    пауза — нет, безлимит проверяется в ДВУХ местах);
  * цена фиксированная и от времени не зависит, а `duration` счёта никогда не 0
    (у базы 0 означает «бессрочный тариф»).

Числа синтетические.
"""

import importlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

extra = importlib.import_module("src.infrastructure.services.overlay_extra_traffic")

from src.core.utils.converters import gb_to_bytes  # noqa: E402

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
EXPIRE = NOW + timedelta(days=40)
WINDOW = NOW + timedelta(days=12)

CFG = {
    "enabled": True,
    "gb_per_purchase": 50,
    "price_rub": 50,
    "min_amount_rub": 10,
    "show_from_percent": 70,
    "min_hours_left": 2,
    "max_gb_per_window": 1000,
}


def state(**kw):
    base = dict(
        user_id=7,
        balance=Decimal("500"),
        sub_id=100,
        sub_status="ACTIVE",
        is_trial=False,
        expire_at=EXPIRE,
        traffic_limit=300,
        strategy="MONTH_ROLLING",
        plan_id=7,
        plan_traffic_limit=300,
        remna_uuid="11111111-1111-1111-1111-111111111111",
        sub_updated_at=NOW - timedelta(days=1),
        frozen_at=None,
        reserve_expire_at=None,
        grants=(),
    )
    base.update(kw)
    return extra.UserState(**base)


def grant(gb=50, ends_at=WINDOW, sub_id=100, grant_id=1):
    return extra.GrantRow(
        id=grant_id,
        subscription_id=sub_id,
        plan_id=7,
        gb=gb,
        strategy="MONTH_ROLLING",
        panel_created_at=NOW - timedelta(days=200),
        granted_at=NOW - timedelta(days=1),
        ends_at=ends_at,
        last_applied_at=NOW - timedelta(days=1),
    )


# ── лимит ───────────────────────────────────────────────────────────────────


def test_ended_grant_lowers_the_limit_by_its_volume():
    assert extra.target_limit(350, 300, 0, 50) == 300


def test_floor_keeps_the_still_active_grants():
    """Кончилась одна прибавка из двух — снимаем только её объём."""
    assert extra.target_limit(400, 300, 50, 50) == 350


def test_floor_does_not_take_away_manual_generosity():
    """Админ поднял лимит руками до 500: кончившаяся прибавка снимает 50, не 200."""
    assert extra.target_limit(500, 300, 0, 50) == 450


def test_floor_lifts_back_after_renewal_reset_the_limit():
    """Лимит уже сброшен до тарифа, а прибавка жива — возвращаем её сверху."""
    assert extra.target_limit(300, 300, 50, 0) == 350


def test_unlimited_is_never_touched():
    assert extra.target_limit(0, 300, 50, 50) == 0
    assert extra.target_limit(350, 0, 50, 50) == 350
    assert extra.target_bytes(0, 300, 0, 50) == 0


def test_bytes_arithmetic_starts_from_the_panel_value_not_from_rounded_gb():
    """Лимит панели не кратен ГБ: считаем в байтах, чтобы не сдвинуть его округлением."""
    odd = gb_to_bytes(300) + 12345
    assert extra.target_bytes(odd, 300, 0, 0) == odd
    # Кончившиеся 50 ГБ снимаются РОВНО пятьюдесятью гигабайтами.
    assert extra.target_bytes(odd + gb_to_bytes(50), 300, 0, 50) == odd


# ── право на покупку ────────────────────────────────────────────────────────


def ok(st, cfg=None, now=NOW, window=WINDOW, known=True, **kw):
    return extra.eligibility(
        st, cfg or CFG, now, window_end=window, window_known=known, **kw
    )


def test_ordinary_active_subscription_can_buy():
    assert ok(state()) is None


def test_limited_is_the_main_buyer_not_a_reject():
    """Именно «трафик кончился» и есть наш покупатель — панель сама снимет LIMITED."""
    assert ok(state(sub_status="LIMITED")) is None


def test_disabled_sales_come_first():
    cfg = dict(CFG, enabled=False)
    assert ok(state(), cfg) == "disabled"
    # Но уже оплаченный заказ отрабатываем даже при выключенном тумблере.
    assert ok(state(), cfg, check_enabled=False) is None


def test_no_price_means_no_sales():
    assert ok(state(), dict(CFG, price_rub=None)) == "disabled"
    assert ok(state(), dict(CFG, gb_per_purchase=0)) == "disabled"


def test_unlimited_traffic_is_checked_in_both_places():
    """Ноль в строке — добавлять нечего; ноль в тарифе — тоже безлимит."""
    assert ok(state(traffic_limit=0)) == "unlimited_traffic"
    assert ok(state(plan_traffic_limit=0)) == "unlimited_traffic"


def test_trial_pause_reserve_and_lifetime_are_rejected():
    assert ok(state(is_trial=True)) == "trial"
    assert ok(state(expire_at=datetime(2099, 1, 1, tzinfo=timezone.utc))) == "unlimited_term"
    assert ok(state(frozen_at=NOW - timedelta(days=1))) == "frozen"
    # Резерв: срок строки сдвинут страховкой, статус остался ACTIVE.
    assert ok(state(reserve_expire_at=EXPIRE - timedelta(minutes=30))) == "reserve"


def test_expired_or_disabled_subscription_is_rejected():
    assert ok(state(sub_status="DISABLED")) == "not_active"
    assert ok(state(expire_at=NOW - timedelta(days=1))) == "not_active"
    assert ok(state(sub_id=None, expire_at=None)) == "no_subscription"


def test_window_cap_counts_already_bought_volume():
    cfg = dict(CFG, max_gb_per_window=100)
    assert ok(state(grants=(grant(gb=50, grant_id=1),))) is None
    assert ok(state(grants=(grant(gb=50, grant_id=1), grant(gb=50, grant_id=2))), cfg) == "window_cap"


def test_reset_too_soon_protects_from_paying_for_an_hour():
    assert ok(state(), window=NOW + timedelta(hours=1)) == "reset_too_soon"
    assert ok(state(), window=NOW + timedelta(hours=3)) is None
    # NO_RESET: окна нет вовсе, и запрет «слишком скоро» к нему не применим.
    assert ok(state(strategy="NO_RESET"), window=None) is None


def test_unknown_window_is_not_sold():
    """Стратегия требует якоря, а даты создания в панели нет — срок назвать нечем."""
    assert ok(state(), known=False) == "reset_unknown"


# ── цена ────────────────────────────────────────────────────────────────────


def test_price_is_fixed_and_does_not_depend_on_time_left():
    near = extra.quote(CFG, NOW, NOW + timedelta(hours=5), EXPIRE)
    far = extra.quote(CFG, NOW, NOW + timedelta(days=29), EXPIRE)
    assert near.amount == far.amount == Decimal(50)
    assert near.gb == far.gb == 50


def test_invoice_duration_is_never_zero_and_tells_the_real_term():
    """У базы duration=0 означает «бессрочный тариф» — такой счёт нельзя выставлять."""
    q = extra.quote(CFG, NOW, NOW + timedelta(hours=3), EXPIRE)
    assert q.days == 1
    assert extra.quote(CFG, NOW, NOW + timedelta(days=12), EXPIRE).days == 12
    # NO_RESET: срок считаем до конца подписки, но не бесконечный.
    assert extra.quote(CFG, NOW, None, EXPIRE).days == 40


def test_minimum_invoice_amount_wins_over_a_tiny_price():
    q = extra.quote(dict(CFG, price_rub=3, min_amount_rub=10), NOW, WINDOW, EXPIRE)
    assert q.amount == Decimal(10)


def test_snapshot_is_synthetic_minus_five_and_carries_the_volume():
    snapshot = extra.synthetic_snapshot(50, 12)
    assert snapshot.id == extra.SYNTHETIC_PLAN_ID == -5
    assert snapshot.traffic_limit == 50
    assert snapshot.duration == 12
    assert snapshot.is_trial is False


def test_used_percent_survives_a_zero_limit():
    assert extra.used_percent(0, 0) == 0
    assert extra.used_percent(gb_to_bytes(35), gb_to_bytes(50)) == 70


@pytest.mark.parametrize("raw, expected", [(-5, None), (0, None), ("", None), ("70", 70)])
def test_config_price_normalisation(raw, expected):
    """Ноль и минус — это «продавать нельзя», а не «бесплатно»."""
    assert extra._normalize({"price_rub": raw})["price_rub"] == expected


def test_default_config_sells_nothing():
    """Файла assets/extra_traffic.json нет — значит продажи выключены."""
    assert extra.DEFAULT_CONFIG["enabled"] is False
    assert extra.effective_enabled(extra.DEFAULT_CONFIG) is False
