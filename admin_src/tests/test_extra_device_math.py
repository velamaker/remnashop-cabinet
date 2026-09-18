"""Докупка устройства: чистые функции — цена, право на покупку, лимит, устройства.

ЧТО ЗАПИРАЕМ. Здесь нет ни базы, ни панели — только смысл, за который берут деньги:

  * цена считается ПО СЕКУНДАМ и округляется ВВЕРХ до рубля (по суткам она была бы
    до трёх рублей больше и не сходилась бы с «осталось N дней» на экране);
  * порядок причин отказа: он определяет, что человек увидит, когда подходят сразу
    две (на паузе и триал — «триал», на резерве и активная — «резерв»);
  * решения владельца 17.09: цена 100 ₽/30 дн. по умолчанию, второй раз докупку той
    же подписке не предлагаем, отключение устройств ВЫКЛ и трогает только то, что
    подключено ПОСЛЕ покупки места;
  * пол лимита `тариф + действующие`: он не даёт продлению сбросить оплаченное место
    и не отнимает ручную щедрость админа;
  * стоимость, уходящая днями при смене тарифа, и остаток для возврата админом.

Числа синтетические.
"""

import importlib
import json
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from types import SimpleNamespace

import pytest

extra = importlib.import_module("src.infrastructure.services.overlay_extra_device")

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
DAY = 86400


def cfg(**over):
    base = dict(extra.DEFAULT_CONFIG)
    base["enabled"] = True
    base.update(over)
    return base


def slot(slot_id=1, sub_id=100, plan_id=7, starts=None, ends=None, applied=None, reminded=None):
    return extra.SlotRow(
        id=slot_id,
        subscription_id=sub_id,
        plan_id=plan_id,
        starts_at=starts or NOW - timedelta(days=5),
        ends_at=ends or NOW + timedelta(days=10),
        last_applied_at=applied or NOW - timedelta(days=5),
        reminded_at=reminded,
    )


def state(**over):
    data = dict(
        user_id=1,
        balance=Decimal("500"),
        sub_id=100,
        sub_status="ACTIVE",
        is_trial=False,
        expire_at=NOW + timedelta(days=20),
        device_limit=2,
        plan_id=7,
        plan_device_limit=2,
        remna_uuid="uuid-1",
        sub_updated_at=NOW - timedelta(days=5),
        frozen_at=None,
        reserve_expire_at=None,
        slots=(),
        ended_slots=0,
    )
    data.update(over)
    return extra.UserState(**data)


# ── A. цена по остатку ───────────────────────────────────────────────────────


def test_price_is_proportional_and_rounds_up():
    config = cfg(price_rub_30d=90)
    # Ровно 20 суток из 30 → 60 ₽ без остатка.
    q = extra.quote(state(expire_at=NOW + timedelta(days=20)), config, NOW, "new")
    assert q.amount == Decimal(60)
    assert q.days == 20
    # Секунда сверх — вверх до целого рубля: магазин выигрывает меньше рубля.
    q = extra.quote(state(expire_at=NOW + timedelta(days=20, seconds=1)), config, NOW, "new")
    assert q.amount == Decimal(61)
    # Сутки по 90 ₽ за 30 дней — это 3 ₽, но минимум счёта 10 ₽.
    q = extra.quote(state(expire_at=NOW + timedelta(days=1)), config, NOW, "new")
    assert q.amount == Decimal(10)


def test_owner_default_price_is_100_and_sales_are_off():
    """Решение владельца: цена 100 ₽/30 дн. стоит сразу, продажи включает он сам."""
    assert extra.DEFAULT_CONFIG["price_rub_30d"] == 100
    assert extra.DEFAULT_CONFIG["enabled"] is False
    assert extra.DEFAULT_CONFIG["max_extra"] == 2
    assert extra.DEFAULT_CONFIG["remove_excess_devices"] is False
    assert extra.effective_enabled(dict(extra.DEFAULT_CONFIG)) is False
    assert extra.effective_enabled(cfg()) is True
    # Цены нет — не продаём даже при включённом тумблере.
    assert extra.effective_enabled(cfg(price_rub_30d=None)) is False


def test_price_counts_seconds_not_days():
    """По суткам цена была бы «ceil(дней)» — на 10 дн. и 1 с это лишние 3 ₽."""
    config = cfg(price_rub_30d=90)
    q = extra.quote(state(expire_at=NOW + timedelta(days=10, seconds=1)), config, NOW, "new")
    # По секундам: 90 × (10 сут + 1 с) / 30 сут = 30,00003… → 31, а не 33 (11 суток).
    assert q.amount == Decimal(31)


# ── B. продление слота ───────────────────────────────────────────────────────


def test_extend_counts_from_slot_end_not_now():
    config = cfg(price_rub_30d=90)
    st = state(
        expire_at=NOW + timedelta(days=35),
        slots=(slot(ends=NOW + timedelta(days=5)),),
    )
    q = extra.quote(st, config, NOW, "extend", 1)
    assert q.cov_start == NOW + timedelta(days=5)
    assert q.amount == Decimal(90)  # ровно 30 суток
    assert q.slot_id == 1


# ── C/D. право на покупку ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "over, expected",
    [
        ({}, None),
        ({"sub_id": None, "expire_at": None}, "no_subscription"),
        ({"is_trial": True}, "trial"),
        ({"expire_at": datetime(2099, 1, 1, tzinfo=timezone.utc)}, "unlimited_term"),
        ({"device_limit": 0}, "unlimited_devices"),
        ({"plan_device_limit": 0}, "unlimited_devices"),
        ({"frozen_at": NOW - timedelta(days=1)}, "frozen"),
        ({"sub_status": "LIMITED"}, "not_active"),
        ({"expire_at": NOW - timedelta(days=1)}, "not_active"),
        ({"expire_at": NOW + timedelta(days=2)}, "too_late"),
    ],
)
def test_eligibility_table(over, expected):
    assert extra.eligibility(state(**over), cfg(), NOW, "new") == expected


def test_eligibility_order_trial_beats_pause():
    """Порядок важен: у триальщика на паузе причина — «триал», а не «пауза»."""
    st = state(is_trial=True, frozen_at=NOW - timedelta(days=1))
    assert extra.eligibility(st, cfg(), NOW, "new") == "trial"


def test_reserve_blocks_sale_but_real_term_does_not():
    """Резерв сдвигает срок и оставляет ACTIVE — истёкший выглядел бы платящим."""
    reserve_end = NOW + timedelta(days=7)
    st = state(expire_at=reserve_end, reserve_expire_at=reserve_end)
    assert extra.eligibility(st, cfg(), NOW, "new") == "reserve"
    # Срок дальше резерва на 30 суток — это настоящая подписка, продаём.
    st = state(expire_at=reserve_end + timedelta(days=30), reserve_expire_at=reserve_end)
    assert extra.eligibility(st, cfg(), NOW, "new") is None


def test_disabled_and_max_reached():
    assert extra.eligibility(state(), cfg(enabled=False), NOW, "new") == "disabled"
    st = state(slots=(slot(1), slot(2, ends=NOW + timedelta(days=9))))
    assert extra.eligibility(st, cfg(max_extra=2), NOW, "new") == "max_reached"
    assert extra.eligibility(st, cfg(max_extra=3), NOW, "new") is None


def test_second_purchase_not_offered_after_slot_expired():
    """Решение владельца: место кончилось — второй раз не предлагаем, ведём на тариф."""
    st = state(ended_slots=1)
    assert extra.eligibility(st, cfg(), NOW, "new") == "already_used"
    # Но уже оплаченный заказ этим правилом не отклоняем: деньги взяты.
    assert extra.eligibility(st, cfg(), NOW, "new", check_used=False) is None


def test_extend_needs_a_live_slot_and_a_day_of_room():
    st = state(expire_at=NOW + timedelta(days=35), slots=(slot(ends=NOW + timedelta(days=5)),))
    assert extra.eligibility(st, cfg(), NOW, "extend", 1) is None
    # Чужой слот.
    assert extra.eligibility(st, cfg(), NOW, "extend", 999) == "nothing_to_extend"
    # Зазор меньше суток — продлевать нечего.
    st = state(
        expire_at=NOW + timedelta(days=5, hours=3), slots=(slot(ends=NOW + timedelta(days=5)),)
    )
    assert extra.eligibility(st, cfg(), NOW, "extend", 1) == "nothing_to_extend"


# ── E/F. лимит устройств ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "args, expected",
    [
        ((3, 2, 0, 1, False), 2),  # обычный конец слота
        ((6, 3, 0, 1, False), 5),  # щедрость админа не отнимаем
        ((0, 3, 0, 1, False), 0),  # безлимит не трогаем
        ((3, 3, 1, 0, True), 4),  # лимит сбросили продлением — вернули место
        ((3, 3, 1, 1, True), 4),
        ((2, 3, 0, 1, False), 3),  # пол не даёт уйти ниже тарифа
    ],
)
def test_target_limit(args, expected):
    assert extra.target_limit(*args) == expected


def test_purchase_from_reset_limit_still_adds_a_place():
    """Лимит сбросили продлением, а место действует: покупка обязана дать +1 сверх пола."""
    st = state(device_limit=3, plan_device_limit=3, slots=(slot(1),))
    assert extra.new_limit_for_purchase(st) == 5


# ── G. признак сброса ────────────────────────────────────────────────────────


def test_detect_reset():
    applied = NOW - timedelta(days=1)
    st = state(device_limit=2, plan_device_limit=2, sub_updated_at=applied, slots=(slot(applied=applied),))
    assert extra.detect_reset(st) is False
    # Строку подписки тронули ПОЗЖЕ нашего применения — лимит переписали.
    st2 = state(
        device_limit=2,
        plan_device_limit=2,
        sub_updated_at=applied + timedelta(microseconds=1),
        slots=(slot(applied=applied),),
    )
    assert extra.detect_reset(st2) is True
    # Хук продления ЗНАЕТ, что лимит только что сброшен: время могло не успеть.
    assert extra.detect_reset(st, after_renew=True) is True
    # Лимит не равен тарифному — ничего не сбрасывали.
    assert extra.detect_reset(state(device_limit=3, plan_device_limit=2, slots=(slot(),))) is False
    # Слотов нет — возвращать нечего.
    assert extra.detect_reset(state(device_limit=2, plan_device_limit=2)) is False


# ── H. какие устройства отключать ────────────────────────────────────────────


def dev(name, created):
    return SimpleNamespace(hwid=name, created_at=created, updated_at=created, device_model=name)


def test_pick_excess_touches_only_devices_added_after_purchase():
    """Решение владельца: трогаем только то, что подключено ПОСЛЕ покупки места."""
    since = NOW - timedelta(days=5)
    devices = [
        dev("old-1", since - timedelta(days=30)),
        dev("old-2", since - timedelta(days=10)),
        dev("new-1", since + timedelta(days=1)),
        dev("new-2", since + timedelta(days=2)),
    ]
    # Лимит 3 при четырёх устройствах — одно лишнее, и это САМОЕ НОВОЕ.
    picked = extra.pick_excess(devices, 3, since)
    assert [d.hwid for d in picked] == ["new-2"]
    # Два лишних — в обратном порядке подключения.
    assert [d.hwid for d in extra.pick_excess(devices, 2, since)] == ["new-2", "new-1"]
    # Лишних больше, чем «новых»: старые не трогаем, снимаем сколько есть.
    assert [d.hwid for d in extra.pick_excess(devices, 1, since)] == ["new-2", "new-1"]
    # Лимит не превышен — никого.
    assert extra.pick_excess(devices, 4, since) == []
    # Момент покупки неизвестен — не гадаем.
    assert extra.pick_excess(devices, 1, None) == []


# ── I/J. стоимость, уходящая днями и на баланс ───────────────────────────────


def order(amount, cov_start, period_end, currency="RUB"):
    return {
        "amount": Decimal(amount),
        "currency": currency,
        "cov_start": cov_start,
        "period_end": period_end,
        "slot_id": 1,
    }


def test_carry_layers_value_and_future_window():
    # 90 ₽ за 30 суток, прожито 20 → остаток 10 суток = 30 ₽.
    layers = extra.carry_layers(
        [order(90, NOW - timedelta(days=20), NOW + timedelta(days=10))], NOW
    )
    amount, currency, total, remaining = layers[0]
    assert currency == "RUB"
    assert Fraction(amount) * remaining / total == Fraction(30)
    # Продление, которое ещё не началось, засчитывается целиком.
    layers = extra.carry_layers(
        [order(90, NOW + timedelta(days=10), NOW + timedelta(days=40))], NOW
    )
    amount, _c, total, remaining = layers[0]
    assert remaining == total


def test_carry_layers_use_pause_moment_as_reference():
    """На паузе срок стоит: считать прожитым надо до момента паузы, а не до «сейчас»."""
    frozen = NOW - timedelta(days=10)
    layers = extra.carry_layers(
        [order(90, NOW - timedelta(days=20), NOW + timedelta(days=10))], frozen
    )
    amount, _c, total, remaining = layers[0]
    assert Fraction(amount) * remaining / total == Fraction(60)


def test_unused_value_rounds_down_to_kopeck():
    value = extra.unused_value([order(10, NOW - timedelta(days=1), NOW + timedelta(days=2))], NOW)
    # 10 × 2/3 = 6,666… → вниз до копейки.
    assert value == Decimal("6.66")


# ── K. цена не уплывает на случайных наборах ─────────────────────────────────


def test_price_invariants_on_random_periods():
    rnd = random.Random(20260918)
    config = cfg(price_rub_30d=100, min_amount_rub=10)
    for _ in range(500):
        secs = rnd.randint(1, 400 * DAY)
        st = state(expire_at=NOW + timedelta(seconds=secs))
        amount = int(extra.quote(st, config, NOW, "new").amount)
        exact = Fraction(100 * secs, extra.SEC30)
        assert amount >= exact  # округляем только вверх
        if amount > config["min_amount_rub"]:
            assert amount - exact < 1  # и меньше чем на рубль


# ── L. конфиг ────────────────────────────────────────────────────────────────


def test_load_config_survives_broken_file(tmp_path, monkeypatch):
    broken = tmp_path / "extra_device.json"
    broken.write_text("{не json", "utf-8")
    monkeypatch.setattr(extra, "CONFIG_PATH", broken)
    assert extra.load_config() == extra.DEFAULT_CONFIG
    # Нет файла — тоже дефолт, без предупреждений.
    monkeypatch.setattr(extra, "CONFIG_PATH", tmp_path / "нет.json")
    assert extra.load_config() == extra.DEFAULT_CONFIG


def test_config_normalisation(tmp_path, monkeypatch):
    path = tmp_path / "extra_device.json"
    path.write_text(
        json.dumps({"enabled": True, "price_rub_30d": 0, "max_extra": 99, "min_days_left": 0}),
        "utf-8",
    )
    monkeypatch.setattr(extra, "CONFIG_PATH", path)
    config = extra.load_config()
    # Ноль — это «продавать нельзя», а не «бесплатно».
    assert config["price_rub_30d"] is None
    assert extra.effective_enabled(config) is False
    assert config["max_extra"] == 10
    assert config["min_days_left"] == 1


def test_save_config_writes_normalised(tmp_path, monkeypatch):
    monkeypatch.setattr(extra, "ASSETS_DIR", tmp_path)
    monkeypatch.setattr(extra, "CONFIG_PATH", tmp_path / "extra_device.json")
    saved = extra.save_config({"enabled": True, "price_rub_30d": "150", "max_extra": -3})
    assert saved["price_rub_30d"] == 150
    assert saved["max_extra"] == 1
    assert json.loads((tmp_path / "extra_device.json").read_text("utf-8"))["price_rub_30d"] == 150


# ── тексты ──────────────────────────────────────────────────────────────────


def test_user_text_mentions_bigger_plan_not_a_second_purchase():
    """После конца места мы предлагаем тариф побольше, а не «докупите ещё раз»."""
    message = extra.user_text("ended", limit=2, removed=[])
    assert "тариф побольше" in message
    assert "докупить" not in message.lower()
