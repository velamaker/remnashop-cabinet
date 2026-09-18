"""Докупка трафика, оплаченная картой: предфильтр, вебхук, повтор и возврат.

ЧТО ЗАПИРАЕМ:
  * ПРЕДФИЛЬТР ПО СНИМКУ ТАРИФА. Ветка стоит в пути КАЖДОГО платежа, а её таблицы
    приносит миграция, которую накатывает только контейнер бота. Обычный платёж,
    пришедший в непересобранный воркер, не имеет права даже коснуться новых таблиц:
    иначе «оплата принята, подписка не выдана». Мутация «убрать предфильтр» роняет
    test_ordinary_payment_never_touches_our_tables;
  * наша ветка ВЫХОДИТ до подписки, события покупки, рефералки и редиректа;
  * деньги сначала на ₽-баланс (свой commit), потом трафик: панель может молчать
    сколько угодно, оплата от этого не теряется;
  * повтор вебхука не зачисляет второй раз и не трогает панель;
  * опоздавший платёж: окно закрылось — отказ, окно сдвинулось вперёд — применяем;
  * исключение внутри ветки наружу не выходит (иначе шлюз получит 500 и начнёт
    повторять вебхук по уже зачисленным деньгам);
  * после RENEW прибавки ГАСНУТ без единого вызова панели — в отличие от докупки
    устройства, где лимит, наоборот, ВОЗВРАЩАЮТ.

Числа, даты и идентификаторы синтетические.
"""

import importlib
import inspect
import uuid as uuid_lib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Optional

import pytest

extra = importlib.import_module("src.infrastructure.services.overlay_extra_traffic")
gateway_mod = importlib.import_module("overlay_patches.gateway_payment")

from src.core.utils.converters import gb_to_bytes  # noqa: E402

from test_extra_traffic_buy import (  # noqa: E402
    EXPIRE,
    NOW,
    PANEL_CREATED,
    PLAN_GB,
    SUB_ID,
    USER,
    USER_ID,
    WINDOW,
    FakeRemnawave,
    FakeSession,
    Log,
    config_on,  # noqa: F401 — фикстура включает продажи
    grant_row,
)


class _Row:
    def __init__(self, value: Any, rows: Optional[list] = None) -> None:
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


class OrderSession(FakeSession):
    """FakeSession плюс журнал заказов: вебхук и крон ищут заказ по payment_id."""

    def __init__(self, log: Log, order: Optional[dict] = None, **kw) -> None:
        super().__init__(log, **kw)
        self.order = order
        self.credited = Decimal(0)

    async def execute(self, stmt: Any, params: Any = None):
        sql = " ".join(str(stmt).split())
        if "WHERE payment_id = :pid FOR UPDATE" in sql:
            self.log.append(("order_lock", None))
            if self.order is None:
                return _Row(None)
            o = self.order
            return _Row(
                (
                    o["id"], o["user_id"], o["subscription_id"], o.get("grant_id"), o["status"],
                    o["gb"], o["amount"], o["window_end"], o.get("panel_created_at"),
                    o.get("attempts", 0), o["created_at"], o["payment_id"],
                )
            )
        if "FROM extra_traffic_orders WHERE payment_id" in sql:
            self.log.append(("order_read", None))
            if self.order is None:
                return _Row(None)
            o = self.order
            return _Row(
                (o["id"], o["status"], o["amount"], o["gb"], o.get("grant_id"),
                 o["window_end"], o["user_id"])
            )
        if "cabinet_balance = cabinet_balance + :a" in sql:
            self.credited += Decimal(str(params["a"]))
            self.log.append(("credit", str(params["a"])))
            return _Row(None)
        if "SET status = 'credited'" in sql:
            self.log.append(("mark_credited", None))
            if self.order is not None:
                self.order["status"] = "credited"
            return _Row(None)
        if "SET status = 'rejected'" in sql:
            self.log.append(("reject", params["r"]))
            if self.order is not None:
                self.order["status"] = "rejected"
            return _Row(None)
        if "SET status = 'applied'" in sql:
            self.log.append(("apply_order_row", None))
            if self.order is not None:
                self.order["status"] = "applied"
            return _Row(None)
        if "attempts = attempts + 1" in sql:
            self.log.append(("attempt", None))
            return _Row(None)
        if "end_reason = 'renew'" in sql:
            self.log.append(("burn_renew", None))
            return _Row(None, rows=[(1,)])
        return await super().execute(stmt, params)


def an_order(**kw) -> dict:
    order = {
        "id": 11,
        "user_id": USER_ID,
        "subscription_id": SUB_ID,
        "grant_id": None,
        "status": "pending",
        "gb": 50,
        "amount": Decimal(40),
        "window_end": WINDOW,
        "panel_created_at": PANEL_CREATED,
        "attempts": 0,
        "created_at": NOW - timedelta(minutes=5),
        "payment_id": uuid_lib.uuid4(),
    }
    order.update(kw)
    return order


class ExplodingSession:
    """Любой SQL — взрыв. Так выглядит воркер, где миграции ещё не накатили."""

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, *a, **kw):
        self.calls += 1
        raise RuntimeError('relation "extra_traffic_orders" does not exist')

    async def commit(self):
        raise RuntimeError("не должно быть вызвано")

    async def rollback(self):
        return None


def transaction(plan_id: int, payment_id: Any = None) -> Any:
    return SimpleNamespace(
        payment_id=payment_id or uuid_lib.uuid4(),
        plan_snapshot=SimpleNamespace(id=plan_id),
    )


class FakeNotifierSpy:
    def __init__(self, log: Log) -> None:
        self.log = log

    async def notify_admins(self, payload: Any) -> None:
        self.log.append(("notify_admins", payload.i18n_kwargs["content"]))

    async def notify_user(self, user: Any, payload: Any = None, **kw) -> None:
        self.log.append(("notify_user", payload.i18n_kwargs["content"]))


def handler(session: Any, log: Log, remnawave: Any = None) -> Any:
    """Подделка ProcessPayment: ровно те атрибуты, которых касается наша ветка."""
    return SimpleNamespace(
        session=session,
        remnawave=remnawave,
        notifier=FakeNotifierSpy(log),
        user_dao=SimpleNamespace(get_by_id=_get_user),
    )


async def _get_user(user_id: int) -> Any:
    return USER


# ── предфильтр ──────────────────────────────────────────────────────────────


async def test_ordinary_payment_never_touches_our_tables():
    """Главная мутация файла: убрать проверку снимка тарифа перед запросом в базу."""
    session = ExplodingSession()
    log = Log()

    result = await gateway_mod._handle_extra_traffic(
        handler(session, log), USER, transaction(plan_id=3)
    )

    assert result is None, "не наш счёт — обычный путь базы идёт дальше"
    assert session.calls == 0, "обращение к новым таблицам раньше предфильтра = деньги без подписки"


async def test_topup_and_gift_snapshots_are_not_ours():
    session = ExplodingSession()
    log = Log()
    for plan_id in (-1, -2, -3, -4):
        assert (
            await gateway_mod._handle_extra_traffic(
                handler(session, log), USER, transaction(plan_id=plan_id)
            )
            is None
        )
    assert session.calls == 0


def test_branch_stands_before_the_subscription_and_returns():
    """Порядок в _handle_success: наша ветка идёт до выдачи подписки и выходит."""
    source = inspect.getsource(gateway_mod.apply)
    ours = source.index("_handle_extra_traffic(self, user, transaction)")
    purchase = source.index("self.purchase_subscription.system")
    assert ours < purchase
    tail = source[ours : ours + 200]
    assert "return" in tail


def test_prefilter_is_the_first_statement_of_the_branch():
    source = inspect.getsource(gateway_mod._handle_extra_traffic)
    body = source.split('"""')[2]
    first = next(line.strip() for line in body.splitlines() if line.strip())
    assert first.startswith("if getattr(transaction.plan_snapshot")
    assert "SYNTHETIC_PLAN_ID" in first


# ── вебхук ──────────────────────────────────────────────────────────────────


async def test_money_lands_on_balance_before_the_panel_is_asked():
    log = Log()
    order = an_order()
    session = OrderSession(log, order=order)
    remnawave = FakeRemnawave(log)

    result = await extra.handle_paid_order(
        session, remnawave.sdk, remnawave, order["payment_id"], now=NOW
    )

    assert result["result"] == "applied"
    names = log.names()
    assert names.index("credit") < names.index("panel_patch")
    assert log[log.index_of("credit")][1] == "40"
    assert log[log.index_of("panel_patch")][1]["trafficLimitBytes"] == gb_to_bytes(PLAN_GB + 50)


async def test_repeat_webhook_credits_once_and_leaves_the_panel_alone():
    log = Log()
    order = an_order(status="applied")
    session = OrderSession(log, order=order)
    remnawave = FakeRemnawave(log)

    result = await extra.handle_paid_order(
        session, remnawave.sdk, remnawave, order["payment_id"], now=NOW
    )

    assert result["repeat"] is True and result["result"] == "applied"
    assert "credit" not in log.names()
    assert "panel_patch" not in log.names()


async def test_foreign_payment_is_not_ours():
    log = Log()
    session = OrderSession(log, order=None)
    remnawave = FakeRemnawave(log)
    assert (
        await extra.handle_paid_order(session, remnawave.sdk, remnawave, uuid_lib.uuid4(), now=NOW)
        is None
    )


async def test_stale_order_is_rejected_and_money_stays_on_balance():
    log = Log()
    order = an_order(status="credited", created_at=NOW - timedelta(hours=30))
    session = OrderSession(log, order=order)
    remnawave = FakeRemnawave(log)

    result = await extra.handle_paid_order(
        session, remnawave.sdk, remnawave, order["payment_id"], now=NOW
    )

    assert result["result"] == "rejected" and result["reason"] == "stale"
    assert log[log.index_of("reject")][1] == "stale"
    assert "panel_patch" not in log.names()


async def test_closed_window_is_rejected_not_applied_to_a_new_one():
    """Окно, за которое платили, успело обновиться — деньги остаются на балансе."""
    log = Log()
    order = an_order(status="credited", window_end=NOW - timedelta(minutes=10))
    session = OrderSession(log, order=order)
    remnawave = FakeRemnawave(log)

    result = await extra.handle_paid_order(
        session, remnawave.sdk, remnawave, order["payment_id"], now=NOW
    )

    assert result["reason"] == "window_closed"
    assert "panel_patch" not in log.names()


async def test_window_moved_forward_is_applied_to_the_new_window():
    """Подписку продлили, окно уехало вперёд — объём фиксирован, применяем."""
    log = Log()
    # Счёт выписан на окно 20 сентября, а к моменту оплаты оно уехало на 7 октября.
    order = an_order(status="credited", window_end=NOW + timedelta(days=2))
    session = OrderSession(log, order=order)
    remnawave = FakeRemnawave(log)

    result = await extra.handle_paid_order(
        session, remnawave.sdk, remnawave, order["payment_id"], now=NOW
    )

    assert result["result"] == "applied"
    assert log[log.index_of("insert_grant")][1]["ends"] == WINDOW


async def test_subscription_changed_is_rejected():
    log = Log()
    order = an_order(status="credited", subscription_id=SUB_ID + 1)
    session = OrderSession(log, order=order)
    remnawave = FakeRemnawave(log)

    result = await extra.handle_paid_order(
        session, remnawave.sdk, remnawave, order["payment_id"], now=NOW
    )
    assert result["reason"] == "subscription_changed"


async def test_spent_balance_is_rejected_with_its_own_reason():
    log = Log()
    order = an_order(status="credited")
    session = OrderSession(log, order=order, enough=False)
    remnawave = FakeRemnawave(log)

    result = await extra.handle_paid_order(
        session, remnawave.sdk, remnawave, order["payment_id"], now=NOW
    )
    assert result["reason"] == "balance_spent"


async def test_panel_failure_compensates_the_limit_and_raises():
    log = Log()
    order = an_order(status="credited")
    session = OrderSession(log, order=order, commit_fails=True)
    remnawave = FakeRemnawave(log)

    with pytest.raises(Exception):
        await extra.handle_paid_order(
            session, remnawave.sdk, remnawave, order["payment_id"], now=NOW
        )

    patches = [body for name, body in log if name == "panel_patch"]
    assert len(patches) == 2
    assert patches[1]["trafficLimitBytes"] == gb_to_bytes(PLAN_GB), "прежний лимит вернулся"


async def test_exception_inside_the_branch_does_not_escape_to_the_gateway():
    """500 в ответ шлюзу — это повтор вебхука по уже зачисленным деньгам."""
    log = Log()
    order = an_order(status="credited")
    session = OrderSession(log, order=order)
    remnawave = FakeRemnawave(log, read_fails=True)

    result = await gateway_mod._handle_extra_traffic(
        handler(session, log, remnawave),
        USER,
        transaction(plan_id=extra.SYNTHETIC_PLAN_ID, payment_id=order["payment_id"]),
    )

    assert result == {"result": "deferred"}
    assert any(name == "notify_admins" for name, _ in log)


# ── RENEW: антоним докупки устройства ───────────────────────────────────────


async def test_renew_burns_traffic_grants_without_calling_the_panel():
    log = Log()
    session = OrderSession(log, order=None)

    burned = await extra.burn_on_renew(session, USER_ID)

    assert burned == 1
    assert "burn_renew" in log.names()
    assert "panel_patch" not in log.names(), "лимит после продления уже тарифный"


def test_renew_branches_of_device_and_traffic_are_opposites():
    """Мутация «поменять ветки местами» обязана быть видна прямо в исходнике.

    У устройства после RENEW лимит ВОЗВРАЩАЮТ (`reconcile_user`), у трафика —
    ГАСЯТ (`burn_on_renew`). Перепутать легко, поэтому обе ветки проверяются здесь.
    """
    source = inspect.getsource(gateway_mod.apply)
    renew = source.index("PurchaseType.RENEW")
    tail = source[renew : renew + 3000]
    assert "extra.reconcile_user" in tail
    assert "etraffic.burn_on_renew" in tail
    assert tail.index("extra.reconcile_user") < tail.index("etraffic.burn_on_renew")
    # У трафика в этой ветке нет ни одного обращения к панели.
    burn = inspect.getsource(extra.burn_on_renew)
    assert "update_user" not in burn and "set_traffic_limit" not in burn


# ── синтетический счёт без строки заказа ────────────────────────────────────


class OrphanSession(OrderSession):
    """Заказа нет, но пишущие запросы (зачисление на баланс) считаем."""

    def __init__(self, log: Log, **kw) -> None:
        super().__init__(log, order=None, **kw)
        self.topups = 0

    async def execute(self, stmt: Any, params: Any = None):
        sql = " ".join(str(stmt).split())
        if "INSERT INTO balance_topups" in sql:
            self.topups += 1
            self.log.append(("topup_insert", dict(params)))
            return _Row((params["p"],))
        return await super().execute(stmt, params)


async def test_synthetic_invoice_without_an_order_never_becomes_a_subscription():
    """Снимок докупки есть, строки заказа нет — подписку по такому счёту НЕ выдаём.

    Раньше такой платёж возвращал None и проваливался в ОБЫЧНЫЙ путь: человеку
    выдавалась «подписка» по синтетическому тарифу (ноль устройств, срок «дней до
    конца окна»). Чинить такую выдачу приходится руками и по одному. Теперь деньги
    честно уходят на ₽-баланс, а владелец получает алерт.
    """
    log = Log()
    session = OrphanSession(log)
    remnawave = FakeRemnawave(log)
    tx = SimpleNamespace(
        payment_id=uuid_lib.uuid4(),
        plan_snapshot=SimpleNamespace(id=extra.SYNTHETIC_PLAN_ID),
        pricing=SimpleNamespace(final_amount=Decimal(50)),
    )

    result = await gateway_mod._handle_extra_traffic(handler(session, log, remnawave), USER, tx)

    assert result is not None, "None отправил бы счёт в обычный путь — к выдаче подписки"
    assert result["result"] == "orphan" and result["credited"] is True
    assert session.topups == 1
    assert any(name == "notify_admins" for name, _ in log)
    assert any(name == "credit" or name == "topup_insert" for name, _ in log)


async def test_orphan_credit_happens_once_on_a_webhook_repeat():
    """Повтор вебхука по тому же счёту второй раз денег не добавляет."""
    log = Log()
    session = OrphanSession(log)

    class OnceSession(OrphanSession):
        async def execute(self, stmt: Any, params: Any = None):
            sql = " ".join(str(stmt).split())
            if "INSERT INTO balance_topups" in sql:
                self.topups += 1
                # ON CONFLICT DO NOTHING: второй раз строка не появляется.
                return _Row(None if self.topups > 1 else (params["p"],))
            return await OrderSession.execute(self, stmt, params)

    session = OnceSession(log)
    remnawave = FakeRemnawave(log)
    payment_id = uuid_lib.uuid4()
    tx = SimpleNamespace(
        payment_id=payment_id,
        plan_snapshot=SimpleNamespace(id=extra.SYNTHETIC_PLAN_ID),
        pricing=SimpleNamespace(final_amount=Decimal(50)),
    )

    first = await gateway_mod._handle_extra_traffic(handler(session, log, remnawave), USER, tx)
    second = await gateway_mod._handle_extra_traffic(handler(session, log, remnawave), USER, tx)

    assert first["credited"] is True
    assert second["credited"] is False, "повтор не должен зачислять деньги второй раз"


def test_device_purchase_has_the_same_guard():
    """У докупки УСТРОЙСТВА та же дыра была — закрыта тем же способом."""
    source = inspect.getsource(gateway_mod._handle_extra_device)
    assert "_orphan_synthetic_payment" in source
    assert "return None\n    if result.get" not in source
