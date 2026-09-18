"""Докупка устройства: админские ручки и снятие паузы после ревью.

ЧТО ЗАПИРАЕМ:
  * отмена докупки с возвратом пишет строку возврата в transactions — иначе деньги
    «вернулись», а в выручке и плитке возвратов покупка стоит целиком;
  * PREVIEW-админу суммы не показываем, как и в соседних ручках;
  * сдвиг конца места после паузы идёт в SAVEPOINT: пауза в панели УЖЕ снята, и сбой
    вспомогательной таблицы не имеет права оставить человека «на паузе» в базе.

Числа и даты синтетические.
"""

import importlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Optional

import pytest

extra = importlib.import_module("src.infrastructure.services.overlay_extra_device")
admin_subs = importlib.import_module("src.web.endpoints.admin.subscriptions")
admin_extra = importlib.import_module("src.web.endpoints.admin.extra_device")
freeze_public = importlib.import_module("src.web.endpoints.public.freeze")

from src.core.enums import TransactionStatus  # noqa: E402

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
PANEL_UUID = "11111111-1111-1111-1111-111111111111"
USER_ID = 7
SUB_ID = 100


class Row:
    def __init__(self, value: Any = None, rows: Optional[list] = None) -> None:
        self.value = value
        self.rows = rows or []

    def first(self):
        return self.value

    def scalar(self):
        if self.value is None:
            return None
        return self.value[0] if isinstance(self.value, tuple) else self.value

    def all(self):
        return self.rows


class Nested:
    def __init__(self, log: list) -> None:
        self.log = log

    async def __aenter__(self):
        self.log.append(("savepoint", None))
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.log.append(("savepoint_rollback" if exc_type else "savepoint_release", None))
        # Как у SQLAlchemy: откат к точке сохранения, исключение уходит наружу.
        return False


class Session:
    def __init__(self, log: list, *, fail_on: Optional[str] = None) -> None:
        self.log = log
        self.fail_on = fail_on
        self.commits = 0

    async def execute(self, stmt: Any, params: Any = None):
        sql = " ".join(str(stmt).split())
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("relation extra_device_slots does not exist")
        if "FROM users u LEFT JOIN subscriptions" in sql:
            self.log.append(("state", None))
            return Row(
                (Decimal(0), SUB_ID, "ACTIVE", False, NOW + timedelta(days=20), 3, 7, 2, PANEL_UUID, NOW)
            )
        if "FROM extra_device_slots WHERE user_id" in sql and "status = 'active'" in sql:
            self.log.append(("slots", None))
            return Row(rows=[(1, SUB_ID, 7, NOW - timedelta(days=10), NOW + timedelta(days=20), NOW, None)])
        if "subscription_freezes" in sql and "SELECT" in sql.upper()[:10]:
            self.log.append(("pause", None))
            return Row((None, None))
        if "JOIN extra_device_slots" in sql:
            self.log.append(("carry_orders", None))
            return Row(rows=[(Decimal(90), "RUB", NOW - timedelta(days=10), NOW + timedelta(days=20), 1)])
        self.log.append(("sql", sql))
        return Row(None)

    def begin_nested(self):
        return Nested(self.log)

    async def commit(self):
        self.commits += 1
        self.log.append(("commit", None))

    async def rollback(self):
        self.log.append(("rollback", None))


class Panel:
    def __init__(self, log: list) -> None:
        self.log = log
        self.sdk = SimpleNamespace(users=self)

    async def update_user(self, body: Any):
        self.log.append(("panel_patch", body.model_dump(exclude_unset=True, by_alias=True)))
        return SimpleNamespace(hwid_device_limit=body.hwid_device_limit)


class TxDao:
    def __init__(self, log: list) -> None:
        self.log = log

    async def create(self, transaction: Any):
        self.log.append(
            (
                "transaction",
                {
                    "status": transaction.status,
                    "amount": transaction.pricing.final_amount,
                    "display": transaction.gateway_display_name,
                    "plan_id": transaction.plan_snapshot.id,
                },
            )
        )
        return transaction


ADMIN = SimpleNamespace(id=1, role=SimpleNamespace(value="OWNER"), log="[ADMIN:1]")


async def revoke(log: list, *, refund: bool, admin=ADMIN):
    raw = admin_subs.revoke_extra_device.__dishka_orig_func__
    return await raw(
        user_id=USER_ID,
        slot_id=1,
        body=admin_subs.RevokeExtraRequest(refund_unused=refund),
        admin=admin,
        session=Session(log),
        remnawave=Panel(log),
        transaction_dao=TxDao(log),
    )


# ── 14. Возврат виден в деньгах, а не только на балансе ──────────────────────


async def test_refund_writes_a_transaction_row():
    log: list = []
    result = await revoke(log, refund=True)
    assert result["success"] is True
    assert result["refunded"] is not None and result["refunded"] > 0
    tx = [d for n, d in log if n == "transaction"]
    assert len(tx) == 1, "возврат зачислен на баланс без строки в transactions"
    assert tx[0]["status"] == TransactionStatus.REFUNDED
    assert tx[0]["display"] == "Возврат · устройство"
    # Снимок синтетический: возврат не должен попасть в топ тарифов и в MRR.
    assert tx[0]["plan_id"] == extra.SYNTHETIC_PLAN_ID
    # Сумма — ровно непрожитый остаток, поэтому исходный счёт не помечаем целиком.
    assert float(tx[0]["amount"]) == result["refunded"]


async def test_revoke_without_refund_writes_nothing_to_money():
    log: list = []
    result = await revoke(log, refund=False)
    assert result["refunded"] is None
    assert "transaction" not in [n for n, _ in log]


async def test_revoke_lowers_the_limit_in_the_panel():
    log: list = []
    await revoke(log, refund=False)
    patch = dict([p for n, p in log if n == "panel_patch"][0])
    # Тариф 2 + оставшихся мест 0 = 2; ручная щедрость (3) отнимается ровно на одно.
    assert patch["hwidDeviceLimit"] == 2


# ── 17. PREVIEW-админ и деньги ───────────────────────────────────────────────


class PlanDaoStub:
    async def get_all(self, only_active: bool = False):
        return []


async def test_preview_admin_does_not_see_amounts(monkeypatch):
    log: list = []
    monkeypatch.setattr(admin_extra, "is_readonly_admin", lambda admin: True)
    raw = admin_extra.get_extra_device_config.__dishka_orig_func__
    payload = await raw(admin=ADMIN, session=Session(log), plan_dao=PlanDaoStub())
    assert payload["summary"].get("amount_30d") is None


async def test_owner_sees_amounts(monkeypatch):
    log: list = []
    monkeypatch.setattr(admin_extra, "is_readonly_admin", lambda admin: False)
    raw = admin_extra.get_extra_device_config.__dishka_orig_func__
    payload = await raw(admin=ADMIN, session=Session(log), plan_dao=PlanDaoStub())
    assert payload["summary"].get("amount_30d") == 0.0


# ── 4. Пауза: сдвиг места не срывает возобновление ──────────────────────────


class FreezeSession(Session):
    """Сессия, у которой падает именно сдвиг конца места."""

    async def execute(self, stmt: Any, params: Any = None):
        sql = " ".join(str(stmt).split())
        if "SET ends_at = ends_at + (now() - :frozen)" in sql:
            raise RuntimeError("relation extra_device_slots does not exist")
        if "FROM subscription_freezes" in sql:
            self.log.append(("freeze_row", None))
            return Row((PANEL_UUID, 12 * 86400, NOW - timedelta(days=3)))
        self.log.append(("sql", sql))
        return Row(None)


class FreezePanel:
    def __init__(self, log: list) -> None:
        self.log = log
        self.sdk = SimpleNamespace(users=self, enable_user=self.enable_user)

    async def update_user(self, body: Any):
        self.log.append(("panel_expire", None))
        return SimpleNamespace()

    async def enable_user(self, uuid: str):
        self.log.append(("enable", None))


async def test_unfreeze_survives_a_broken_slot_shift():
    """Пауза в панели уже снята — сбой вспомогательной таблицы не имеет права
    оставить человека «на паузе» в нашей базе."""
    log: list = []
    session = FreezeSession(log)
    raw = freeze_public.unfreeze.__dishka_orig_func__
    result = await raw(
        user=SimpleNamespace(id=USER_ID, log="[USER:7]"),
        session=session,
        remnawave=FreezePanel(log),
    )
    assert result["frozen"] is False
    assert session.commits == 1, "снятие паузы не закоммичено из-за сбоя сдвига места"
    assert ("savepoint_rollback", None) in log, "сдвиг идёт не в SAVEPOINT"


def test_cron_unfreeze_also_uses_a_savepoint():
    """В кроне сбой на одном человеке не должен ронять весь проход."""
    import inspect

    source = inspect.getsource(importlib.import_module("src.infrastructure.taskiq.tasks.freeze"))
    body = source[source.index("shift_on_unfreeze(session, uid") - 400 :]
    assert "begin_nested" in body
