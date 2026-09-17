"""Оплата с баланса: деньги возвращаются, только если покупка НЕ выдана.

ЧТО БЫЛО. `pay_with_balance` накрывал одним `try` и выдачу, и последний шаг успешного
пути — живой вызов Telegram (`redirect.to_success_payment`) у заблокировавшего бота.
Любое исключение возвращало деньги. С переносом остатка это стало «бесплатной сменой
тарифа с бонусом»: смена выдана, дни перенесены, деньги вернулись.

ПОРЯДОК УСЛОВИЙ. «Выдано» — только если выдача не упала (`PurchaseError`) и счёт
COMPLETED; подписка сверяется ВТОРЫМ условием. Одна сверка строки врёт при гонке:
соседняя покупка сменила строку, пока наша упала, — и деньги не вернулись бы.

ПОЧЕМУ НЕ ПО СРОКУ. `was_subscription_granted` судит по сдвигу срока — для RENEW верно.
Смена тарифа создаёт НОВУЮ строку, и её срок бывает раньше старого (полгода дешёвого
тарифа → два месяца дорогого). Поэтому `was_change_granted` сначала смотрит, сменилась
ли строка, и только потом — срок.

Запуск — внутри образа бота, как остальные тесты рядом.
"""

import importlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

importlib.import_module("src.web.endpoints.public")
offers_mod = importlib.import_module("overlay_patches.public_subscription")
balance = importlib.import_module("src.infrastructure.services.overlay_balance")

from src.application.dto import PlanDto, PlanDurationDto, PlanPriceDto  # noqa: E402
from src.application.services import PricingService  # noqa: E402
from src.core.enums import AuthType, Currency, PaymentGatewayType  # noqa: E402

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def sub(sid: int, days: float):
    return SimpleNamespace(id=sid, expire_at=NOW + timedelta(days=days), plan_snapshot=SimpleNamespace(id=10))


class Dao:
    def __init__(self, after=None, raises: bool = False) -> None:
        self.after = after
        self.raises = raises

    async def get_current(self, user_id: int):
        if self.raises:
            raise RuntimeError("база недоступна")
        return self.after


# ── was_change_granted ───────────────────────────────────────────────────────


async def test_new_row_is_granted():
    assert await balance.was_change_granted(Dao(after=sub(2, 30)), 1, sub(1, 29)) is True


async def test_change_with_earlier_expiry_is_still_granted():
    """Полгода дешёвого → два месяца дорогого: срок раньше, строка другая — выдано."""
    assert await balance.was_change_granted(Dao(after=sub(2, 60)), 1, sub(1, 180)) is True


async def test_renew_moves_expiry_forward():
    assert await balance.was_change_granted(Dao(after=sub(1, 59)), 1, sub(1, 29)) is True


async def test_same_row_same_expiry_is_not_granted():
    assert await balance.was_change_granted(Dao(after=sub(1, 29)), 1, sub(1, 29)) is False


async def test_new_purchase_first_subscription():
    assert await balance.was_change_granted(Dao(after=sub(1, 30)), 1, None) is True
    assert await balance.was_change_granted(Dao(after=None), 1, None) is False


async def test_unknown_when_dao_fails_or_subscription_vanished():
    assert await balance.was_change_granted(Dao(raises=True), 1, sub(1, 29)) is None
    assert await balance.was_change_granted(Dao(after=None), 1, sub(1, 29)) is None


# ── ручка целиком ────────────────────────────────────────────────────────────


class Result:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def first(self):
        return None if self.value is None else (self.value,)


class Session:
    def __init__(self, balance_after: Decimal, tx_status: "str | None" = "COMPLETED") -> None:
        self.balance_after = balance_after
        self.tx_status = tx_status
        self.refunds = 0
        self.debits = 0
        self.commits = 0

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        if "cabinet_balance - :amt" in sql:
            self.debits += 1
            return Result(self.balance_after)
        if "cabinet_balance + :amt" in sql:
            self.refunds += 1
        if "SELECT status::text FROM transactions" in sql:
            return Result(self.tx_status)
        return Result(None)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        return None


class Uow:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def commit(self):
        return None


class SubscriptionDao:
    """Отдаёт подписку ДО оплаты, пока процесс оплаты не «выдал» новую."""

    def __init__(self, before, after, after_raises: bool = False) -> None:
        self.before = before
        self.after = after
        self.after_raises = after_raises
        self.granted = False

    async def get_current(self, user_id):
        if not self.granted:
            return self.before
        if self.after_raises:
            raise RuntimeError("база недоступна")
        return self.after


class Gateways:
    async def get_by_type(self, kind):
        return SimpleNamespace(type=kind, currency=Currency.RUB)


class Plans:
    def __init__(self, plan) -> None:
        self.plan = plan

    async def system(self, *args):
        return [self.plan]


class NoMatch:
    async def system(self, *args):
        return None


class Transactions:
    async def create(self, transaction):
        return transaction


class ProcessPayment:
    """Выдаёт (или нет) и падает ПОСЛЕ — как Telegram у заблокировавшего бота."""

    def __init__(self, dao: SubscriptionDao, grant: bool, error: "BaseException | None" = None) -> None:
        self.dao = dao
        self.grant = grant
        self.error = error or RuntimeError("Forbidden: bot was blocked by the user")

    @property
    def system(self):
        async def run(data):
            if self.grant:
                self.dao.granted = True
            raise self.error

        return run


PLAN = PlanDto(
    id=20, public_code="DUO2", name="DUO2",
    durations=[PlanDurationDto(days=30, prices=[PlanPriceDto(currency=Currency.RUB, price=Decimal("240"))])],
)
USER = SimpleNamespace(
    id=42, auth_type=AuthType.TELEGRAM, is_email_verified=False, purchase_discount=0, personal_discount=0,
    remna_name="rs_42", log="[USER:42]",
)


async def pay(monkeypatch, *, grant: bool, after, after_raises: bool = False, tx_status="COMPLETED", error=None):
    alerts: list[str] = []

    async def alert(message, title="x"):
        alerts.append(message)

    monkeypatch.setattr(balance, "_alert_admins_balance", alert)
    dao = SubscriptionDao(before=sub(5, 180), after=after, after_raises=after_raises)
    session = Session(Decimal("760"), tx_status)
    raw = offers_mod.pay_with_balance.__dishka_orig_func__
    outcome = None
    try:
        outcome = await raw(
            body=offers_mod.PayWithBalanceRequest(plan_code="DUO2", duration_days=30, gateway_type=PaymentGatewayType.YOOMONEY),
            user=USER, session=session, uow=Uow(), subscription_dao=dao, payment_gateway_dao=Gateways(),
            pricing_service=PricingService(), get_available_plans=Plans(PLAN), match_plan=NoMatch(),
            transaction_dao=Transactions(), process_payment=ProcessPayment(dao, grant, error),
        )
    except Exception as exc:  # noqa: BLE001
        outcome = exc
    return session, alerts, outcome


async def test_granted_change_with_earlier_expiry_keeps_money(monkeypatch):
    """Смена выдана (строка новая, срок раньше старого), упал Telegram — деньги НЕ возвращаем."""
    session, alerts, outcome = await pay(monkeypatch, grant=True, after=sub(6, 60))
    assert session.debits == 1
    assert session.refunds == 0, "возврат поверх выданной смены — бесплатный тариф с бонусом"
    assert isinstance(outcome, dict) and outcome["success"] is True and outcome["purchase_type"] == "CHANGE"
    assert len(alerts) == 1


async def test_not_granted_refunds(monkeypatch):
    session, alerts, outcome = await pay(monkeypatch, grant=False, after=sub(5, 180))
    assert session.refunds == 1
    assert getattr(outcome, "status_code", None) == 502
    assert alerts == []


async def test_unknown_refunds_and_alerts(monkeypatch):
    session, alerts, outcome = await pay(monkeypatch, grant=True, after=None, after_raises=True)
    assert session.refunds == 1
    assert getattr(outcome, "status_code", None) == 502
    assert len(alerts) == 1


async def test_purchase_error_refunds_even_if_subscription_row_changed(monkeypatch):
    """Выдача упала (PurchaseError, счёт FAILED), а строка подписки сменилась соседней
    покупкой — это НЕ наша выдача: деньги возвращаем."""
    from src.core.exceptions import PurchaseError

    session, alerts, outcome = await pay(monkeypatch, grant=True, after=sub(6, 60), tx_status="FAILED",
                                         error=PurchaseError(RuntimeError("panel down")))
    assert session.refunds == 1
    assert getattr(outcome, "status_code", None) == 502


async def test_invoice_not_completed_refunds(monkeypatch):
    for status in ("PENDING", "FAILED", None):
        session, alerts, outcome = await pay(monkeypatch, grant=True, after=sub(6, 60), tx_status=status)
        assert session.refunds == 1, status
