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


# ── валюты: скидка одна, обещание — в каждой ─────────────────────────────────


def paid(price, currency: str, pct: int) -> Decimal:
    """Цена со скидкой так, как её возьмёт денежный путь базы (PricingService.calculate:
    скидка в процентах, затем правила валюты — рубли и звёзды округляются вниз)."""
    from src.application.services.pricing import PricingService
    from src.core.enums import Currency

    user = SimpleNamespace(purchase_discount=pct, personal_discount=0, remna_name="t", log="t")
    return PricingService().calculate(user, Decimal(price), Currency(currency)).final_amount


# Живые цены DUO: plan_prices.price — Numeric(10, 2), валюта — текст enum currency.
DUO = {(60, "RUB"): Decimal("449.00"), (90, "RUB"): Decimal("529.00"),
       (60, "XTR"): Decimal("256.00"), (90, "XTR"): Decimal("302.00")}


def test_живые_цены_DUO_90_дней_не_дороже_60_в_каждой_валюте():
    offer = task.offer_for(lang="ru", plan_code="DUO", plan_name="DUO", prices=DUO, cfg=CFG)
    assert offer["kind"] == "term"
    for cur in ("RUB", "XTR"):
        assert paid(DUO[(90, cur)], cur, offer["percent"]) <= DUO[(60, cur)], cur
    assert "449 ₽ вместо 529 ₽" in offer["body"], "без валюты магазина — рубли"

    stars = task.offer_for(lang="ru", plan_code="DUO", plan_name="DUO", prices=DUO, cfg=CFG, currency="XTR")
    assert "256 ⭐ вместо 302 ⭐" in stars["body"], "валюта магазина по умолчанию — звёзды"
    assert stars["percent"] == offer["percent"], "скидка не зависит от валюты текста"
    usd = task.offer_for(lang="ru", plan_code="DUO", plan_name="DUO", prices=DUO, cfg=CFG, currency="USD")
    assert "449 ₽" in usd["body"], "у тарифа нет цен в валюте магазина — рубли"


def test_скидка_по_самой_требовательной_валюте():
    """В рублях хватает 16 %, в звёздах нужно 18 %: со «рублёвой» скидкой 90 дней за
    звёзды вышли бы дороже 60 — обещание «по цене двух месяцев» было бы ложью."""
    prices = {**DUO, (60, "XTR"): Decimal("250.00")}
    rub_only = wb.term_percent(prices[(90, "RUB")], prices[(60, "RUB")])
    assert paid(prices[(90, "XTR")], "XTR", rub_only) > prices[(60, "XTR")], "пример не про то"

    offer = task.offer_for(lang="ru", plan_code="DUO", plan_name="DUO", prices=prices, cfg=CFG)
    assert offer["percent"] == wb.term_percent(prices[(90, "XTR")], prices[(60, "XTR")]) > rub_only
    for cur in ("RUB", "XTR"):
        assert paid(prices[(90, cur)], cur, offer["percent"]) <= prices[(60, cur)], cur
    assert "449 ₽ вместо 529 ₽" in offer["body"]


def test_валюта_без_выгоды_не_показывается_и_не_мешает():
    # В рублях 90 дней и так не дороже 60 — показывать «500 ₽ вместо 500 ₽» нельзя,
    # но скидка всё равно нужна: в звёздах 90 дней дороже.
    prices = {(60, "RUB"): Decimal("500"), (90, "RUB"): Decimal("500"),
              (60, "XTR"): Decimal("250"), (90, "XTR"): Decimal("302")}
    offer = task.offer_for(lang="ru", plan_code="S", plan_name="S", prices=prices, cfg=CFG, currency="RUB")
    assert offer["kind"] == "term" and offer["percent"] == wb.term_percent(302, 250)
    assert "250 ⭐ вместо 302 ⭐" in offer["body"]


def test_абсурдные_цены_в_одной_валюте_прежняя_скидка():
    # Звёзды: 60 дней за 1 ⭐ — опечатка. Честно пообещать «по цене двух месяцев» нельзя.
    prices = {**DUO, (60, "XTR"): Decimal("1")}
    offer = task.offer_for(lang="ru", plan_code="DUO", plan_name="DUO", prices=prices, cfg=CFG)
    assert offer["kind"] == "percent"


def test_валюта_магазина_читается_из_настроек():
    class Session:
        def __init__(self, value):
            self.value = value

        async def execute(self, query):
            assert "default_currency" in str(query)
            return SimpleNamespace(first=lambda: (self.value,) if self.value else None)

    import asyncio

    assert asyncio.run(task._default_currency(Session("XTR"))) == "XTR"
    assert asyncio.run(task._default_currency(Session(None))) is None


# ── склонение ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "n, words",
    [(1, "1 день"), (2, "2 дня"), (4, "4 дня"), (5, "5 дней"), (11, "11 дней"), (12, "12 дней"),
     (14, "14 дней"), (21, "21 день"), (22, "22 дня"), (25, "25 дней"), (90, "90 дней"),
     (91, "91 день"), (111, "111 дней"), (112, "112 дней"), (122, "122 дня")],
)
def test_склонение_дней(n, words):
    assert task._days(n, "ru") == words


def test_нестандартный_срок_в_тексте():
    cfg = {**CFG, "term_days": 91, "pay_days": 31}
    prices = {(91, "RUB"): Decimal("300"), (31, "RUB"): Decimal("120")}
    offer = task.offer_for(lang="ru", plan_code="S", plan_name="S", prices=prices, cfg=cfg)
    assert "91 день по цене 31 дня" in offer["title"]
    assert "S на 91 день —" in offer["body"]
    assert offer["button"] == "Вернуться на 91 день"

    cfg = {**CFG, "term_days": 92, "pay_days": 45}
    prices = {(92, "RUB"): Decimal("300"), (45, "RUB"): Decimal("150")}
    offer = task.offer_for(lang="ru", plan_code="S", plan_name="S", prices=prices, cfg=cfg)
    assert "92 дня по цене 45 дней" in offer["title"] and offer["button"] == "Вернуться на 92 дня"
    en = task.offer_for(lang="en", plan_code="S", plan_name="S", prices=prices, cfg=cfg)
    assert "92 days for the price of 45 days" in en["title"]


def test_изменение_названо_в_итоге_update_sh():
    from src.web import cabinet_capabilities as caps

    assert "winback_term" in caps.BOT_ONLY_CHANGES
