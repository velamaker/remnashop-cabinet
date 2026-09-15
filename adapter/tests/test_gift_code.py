"""Код подарка, который кабинет показывает покупателю.

ЧТО БЫЛО. Адаптер печатал человеку `purchase_token` — а это у них `token[:12]`,
обрезок. Подарок по нему не активируется НИГДЕ: ручной ввод в боте требует
полный код (`allow_legacy_short=False`), а в их сборке 4.0 ручного ввода не было
вовсе. То есть человек платил за подарок и получал набор символов, который
некуда деть.

С их v4.11 рядом приехало настоящее поле `gift_code` — «GIFT_» плюс 59 символов
токена. Его и показываем.

ПОЧЕМУ НЕТ ФОЛБЭКА НА `purchase_token`. Показать нерабочий код хуже, чем не
показать никакого: человек будет вводить обрезок и думать, что сломан бот, —
вместо того чтобы спросить. Поэтому при отсутствии `gift_code` поле пустое.

Запуск — внутри образа адаптера (см. другие тесты рядом).
"""

import asyncio
import importlib
from typing import Any

import httpx
import pytest

compose = importlib.import_module("compose")

# Настоящий вид их кода: GIFT_ + 59 символов токена.
REAL_CODE = "GIFT_" + "a" * 59
TOKEN_12 = "abcdefghijkl"


class FakeCtx:
    """Контекст ровно в том объёме, в каком его читают ручки подарков."""

    def __init__(self, body: dict, purchase: dict, config: dict | None = None,
                 sent: list | None = None, status: int = 200) -> None:
        self._body = body
        self._purchase = purchase
        self._status = status
        self._config = config if config is not None else {
            "is_enabled": True,
            "tariffs": [{"id": 7, "name": "Про", "periods": [{"days": 30, "price_kopeks": 29900}]}],
        }
        self._sent = sent or []

    def payload(self) -> dict:
        return self._body

    async def json(self, path: str, default: Any, **kw: Any) -> Any:
        if path == "/cabinet/gift/config":
            return self._config
        if path == "/cabinet/gift/sent":
            return self._sent
        return default

    async def call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        assert path == "/cabinet/gift/purchase"
        return httpx.Response(self._status, json=self._purchase)


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


BUY = {"plan_code": "7", "duration_days": 30}


def test_balance_purchase_shows_the_real_code():
    """Оплата с баланса: код готов сразу — и это `gift_code`, а не обрезок."""
    ctx = FakeCtx(BUY, {"status": "ok", "purchase_token": TOKEN_12, "gift_code": REAL_CODE})
    out = run(compose.gift_create(ctx))
    assert out["code"] == REAL_CODE
    # Обрезок остаётся идентификатором платежа — по нему кабинет ищет покупку.
    assert out["payment_id"] == TOKEN_12
    assert out["paid_by"] == "balance"


def test_truncated_token_never_leaks_into_the_code():
    """Та самая поломка: их ответ без `gift_code` не должен давать обрезок."""
    ctx = FakeCtx(BUY, {"status": "ok", "purchase_token": TOKEN_12})
    out = run(compose.gift_create(ctx))
    assert out["code"] is None
    assert out["payment_id"] == TOKEN_12


def test_card_purchase_has_no_code_yet():
    """Оплата картой: подарок ещё не оплачен, и код у них отдаётся как None.

    Показать код здесь значило бы сказать «подарок готов» до оплаты.
    """
    ctx = FakeCtx(
        {**BUY, "gateway_type": "YOOMONEY"},
        {"status": "created", "purchase_token": TOKEN_12,
         "gift_code": None, "payment_url": "https://pay.example/1"},
    )
    out = run(compose.gift_create(ctx))
    assert out["code"] is None
    assert out["payment_url"] == "https://pay.example/1"
    assert out["paid_by"] == "gateway"


def test_history_shows_the_real_code_for_paid_gifts():
    """История подарков: оплаченный картой подарок забирает код отсюда."""
    ctx = FakeCtx(BUY, {}, sent=[{
        "token": TOKEN_12, "tariff_name": "Про", "period_days": 30,
        "status": "paid", "gift_code": REAL_CODE, "created_at": "2026-09-15T00:00:00Z",
    }])
    item = run(compose.gift_my(ctx))["items"][0]
    assert item["code"] == REAL_CODE
    assert item["issued"] is True
    assert item["payment_id"] == TOKEN_12


def test_history_hides_the_code_until_paid():
    """Неоплаченный подарок кодом не хвастается."""
    ctx = FakeCtx(BUY, {}, sent=[{
        "token": TOKEN_12, "status": "pending", "gift_code": REAL_CODE, "period_days": 30,
    }])
    item = run(compose.gift_my(ctx))["items"][0]
    assert item["code"] is None
    assert item["issued"] is False


def test_history_without_gift_code_shows_nothing():
    """Их старая сборка поля не шлёт — обрезок вместо кода не подставляем."""
    ctx = FakeCtx(BUY, {}, sent=[{
        "token": TOKEN_12, "status": "paid", "period_days": 30,
    }])
    item = run(compose.gift_my(ctx))["items"][0]
    assert item["code"] is None
    # Подарок при этом остаётся выданным — просто код показать нечем.
    assert item["issued"] is True
