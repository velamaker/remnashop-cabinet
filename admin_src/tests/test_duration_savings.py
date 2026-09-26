"""Выгода длинного срока на кнопках сроков в боте (overlay_patches/duration_savings.py).

Цены — боевые после «лестницы сроков» 26.09 (SOLO: 129 / 289 / 499 / 939 ₽): выгода
против помесячной оплаты должна выйти ровно 25 / 35 / 40 % — те же цифры, что видит
владелец в админке и человек на кнопке срока в кабинете.
"""

import asyncio
from decimal import Decimal

from overlay_patches import duration_savings as ds


def _item(days, amount, period=None):
    return {"days": days, "period": period or f"{days} дней", "final_amount": Decimal(amount),
            "discount_percent": 0, "original_amount": Decimal(amount), "currency": "₽"}


def test_боевые_цены_дают_25_35_40():
    assert ds.savings_percent(Decimal("129"), Decimal("289"), 90) == 25
    assert ds.savings_percent(Decimal("129"), Decimal("499"), 180) == 35
    assert ds.savings_percent(Decimal("129"), Decimal("939"), 365) == 40


def test_месяц_и_короче_без_выгоды():
    assert ds.savings_percent(129, 129, 30) is None
    assert ds.savings_percent(129, 60, 14) is None


def test_мелкая_выгода_не_показывается():
    # 60 дней за 250 при месяце 129: 3 % — шум округления, не выгода.
    assert ds.savings_percent(129, 250, 60) is None


def test_мусор_на_входе_не_роняет():
    for month, term, days in [(None, 100, 90), (129, None, 90), (0, 100, 90), ("abc", 1, 90),
                              (129, -5, 90), (129, 100, "90"), (129, 100, None)]:
        assert ds.savings_percent(month, term, days) is None


def test_подпись_сроков_дополняется():
    rows = [_item(30, "129"), _item(90, "289"), _item(180, "499"), _item(365, "939")]
    out = ds.with_savings(rows)
    assert [r["period"] for r in out] == ["30 дней", "90 дней · −25%", "180 дней · −35%", "365 дней · −40%"]
    # Вход не тронут: геттер базы мог бы переиспользовать список.
    assert rows[1]["period"] == "90 дней"


def test_без_месячного_срока_подписи_как_у_базы():
    rows = [_item(90, "289"), _item(365, "939")]
    assert ds.with_savings(rows) == rows


def test_личная_скидка_на_процент_не_влияет():
    # Скидка 10 % уменьшает обе цены одинаково.
    rows = [_item(30, "116.10"), _item(90, "260.10")]
    assert ds.with_savings(rows)[1]["period"].endswith("−25%")


def test_обёртка_дописывает_и_переживает_сбой(monkeypatch):
    async def base(**kwargs):
        return {"durations": [_item(30, "129"), _item(90, "289")], "plan": "SOLO"}

    monkeypatch.setitem(ds._BASE, "duration_getter", base)
    data = asyncio.run(ds.duration_getter(dialog_manager=None))
    assert data["durations"][1]["period"] == "90 дней · −25%"
    assert data["plan"] == "SOLO"

    def boom(_):
        raise RuntimeError("сбой расчёта")

    monkeypatch.setattr(ds, "with_savings", boom)
    data = asyncio.run(ds.duration_getter(dialog_manager=None))
    assert data["durations"][1]["period"] == "90 дней"
