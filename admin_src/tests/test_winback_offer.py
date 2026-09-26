"""Возврат ушедших: «90 дней по цене двух месяцев» вместо ещё одной скидки.

Цены — боевые после «лестницы сроков» 26.09: SOLO 60 дней — 239 ₽, 90 дней — 289 ₽;
HOME 649 / 759 ₽. Скидка обязана сделать 90 дней НЕ ДОРОЖЕ 60 — иначе обещание
«по цене двух месяцев» ложь.
"""

import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.infrastructure.services import overlay_winback as wb
from src.infrastructure.taskiq.tasks import winback as task

CFG = {**wb.DEFAULT_CONFIG, "enabled": True, "mode": "term"}


@pytest.mark.parametrize("term, pay", [("289", "239"), ("759", "649"), ("1789", "1499"), ("100", "1")])
def test_скидка_делает_длинный_срок_не_дороже(term, pay):
    pct = wb.term_percent(Decimal(term), Decimal(pay))
    if pct is None:
        assert Decimal(pay) * 100 / Decimal(term) < 10, "None — только при абсурдной разнице"
        return
    assert Decimal(term) * (100 - pct) / 100 <= Decimal(pay)
    # И не щедрее нужного больше чем на процент.
    assert Decimal(term) * (100 - pct + 1) / 100 > Decimal(pay)


def test_скидка_не_считается_без_смысла():
    assert wb.term_percent(None, 1) is None
    assert wb.term_percent(289, 289) is None, "длинный срок и так не дороже"
    assert wb.term_percent(289, 300) is None
    assert wb.term_percent(0, 1) is None
    assert wb.term_percent("abc", 1) is None


def test_предложение_по_тарифу_человека():
    offer = task.offer_for(
        lang="ru", plan_code="SOLO1", plan_name="SOLO <1>",
        prices={(90, "RUB"): Decimal("289"), (60, "RUB"): Decimal("239"),
                (90, "USD"): Decimal("3.49"), (60, "USD"): Decimal("2.99")},
        cfg=CFG,
    )
    assert offer["kind"] == "term"
    assert offer["percent"] == 18
    assert offer["path"] == "/billing?plan=SOLO1&days=90"
    assert "90 дней по цене двух месяцев" in offer["title"]
    assert "239 ₽" in offer["body"] and "289 ₽" in offer["body"], "рубли — в первую очередь"
    assert "&lt;1&gt;" in offer["body"], "имя тарифа экранируется — сообщение идёт в HTML"


def test_нет_цены_на_срок_прежняя_скидка():
    offer = task.offer_for(lang="en", plan_code="X", plan_name="X",
                           prices={(90, "RUB"): Decimal("289")}, cfg=CFG)
    assert offer["kind"] == "percent" and offer["percent"] == CFG["percent"]
    assert offer["path"] == "/billing"
    assert "20% off" in offer["title"]


def test_нет_тарифа_или_режим_скидки():
    assert task.offer_for(lang="ru", plan_code=None, plan_name=None, prices={}, cfg=CFG)["kind"] == "percent"
    prices = {(90, "RUB"): 289, (60, "RUB"): 239}
    cfg = {**CFG, "mode": "percent"}
    assert task.offer_for(lang="ru", plan_code="S", plan_name="S", prices=prices, cfg=cfg)["kind"] == "percent"


def test_цены_в_другой_валюте_если_рублей_нет():
    offer = task.offer_for(lang="en", plan_code="S", plan_name="S",
                           prices={(90, "USD"): Decimal("3.49"), (60, "USD"): Decimal("2.99")}, cfg=CFG)
    assert offer["kind"] == "term" and "2.99 $" in offer["body"]


def test_группировка_строк_выборки():
    r = lambda uid, days, cur, price: SimpleNamespace(
        user_id=uid, lang="ru", telegram_id=1, plan_code="S", plan_name="S", days=days, currency=cur, price=price)
    people = task.group_candidates([r(1, 90, "RUB", 289), r(1, 60, "RUB", 239), r(2, None, None, None)])
    assert people[0]["prices"] == {(90, "RUB"): 289, (60, "RUB"): 239}
    assert people[1]["prices"] == {}


def test_старый_файл_настроек_остаётся_на_скидке(tmp_path, monkeypatch):
    path = tmp_path / "winback.json"
    monkeypatch.setattr(wb, "CONFIG_PATH", path)
    assert wb.load_config()["mode"] == "term", "новая установка — «90 по цене 60»"
    path.write_text(json.dumps({"enabled": True, "percent": 20, "days_after": 7, "lifetime_hours": 168}), "utf-8")
    cfg = wb.load_config()
    assert cfg["mode"] == "percent" and cfg["enabled"] is True, "включённое владельцем не меняется молча"
    saved = wb.save_config({**cfg, "mode": "term"})
    assert saved["mode"] == "term" and wb.load_config()["mode"] == "term"


def test_сроки_зажимаются_и_не_переворачиваются():
    assert wb._normalize({"term_days": 60, "pay_days": 90})["term_days"] == 90
    n = wb._normalize({"term_days": 180, "pay_days": 90, "mode": "что-то"})
    assert (n["term_days"], n["pay_days"], n["mode"]) == (180, 90, "term")
