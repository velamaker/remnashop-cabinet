"""Витрина поверх «Бедолаги» не обещает перенос остатка при смене тарифа.

ПОЧЕМУ ЗАМОК. Перенос остатка по цене дня — логика НАШЕГО бэкенда (overlay
plan_change_carryover). Как смена тарифа устроена у их бота, адаптер не знает. Кабинет
пишет «остаток перенесём, добавится N дн.» только при `plan_change_keeps_days: true` и
таблице `plan_change_carry`. Появись эти поля в ответе адаптера — человек поверх чужого
бота увидел бы обещание дней, которых ему никто не начислит.

Без полей кабинет ведёт себя как раньше: не предупреждает и не предлагает смену сам
(заперто тестами кабинета: BillingPage, planChange, pickDeviceUpgrade).

Запуск — внутри образа адаптера (см. другие тесты рядом).
"""

import importlib
import inspect

compose = importlib.import_module("compose")


def test_offers_handler_never_claims_carryover():
    source = inspect.getsource(compose.subscription_offers)
    for field in ("plan_change_keeps_days", "plan_change_carry", "carry_mode"):
        assert field not in source, f"адаптер начал отдавать {field} — кабинет пообещает перенос дней"


def test_no_other_handler_adds_carry_fields():
    whole = inspect.getsource(compose)
    assert "plan_change_carry" not in whole
    assert "carry_mode" not in whole
