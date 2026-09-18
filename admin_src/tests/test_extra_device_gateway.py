"""Докупка устройства, оплаченная картой: счёт, вебхук и возврат.

ЧТО ЗАПИРАЕМ:
  * счёт выставляется на ТОЧНУЮ сумму синтетическим снимком −4, а строка заказа
    пишется ДО отдачи ссылки: не записали — 503 и ссылки нет, платить нечем;
  * на вебхуке наша ветка идёт рядом с пополнением и подарком и ВЫХОДИТ до подписки,
    события покупки, рефералки и редиректа — счёт докупки их не касается;
  * деньги сначала на ₽-баланс (свой commit), потом место: панель может молчать
    сколько угодно, оплата от этого не теряется;
  * повтор вебхука не зачисляет второй раз и не трогает панель;
  * подписка сменилась / заказ старше суток / деньги потрачены — заказ `rejected`
    с причиной, деньги остаются на балансе, человеку и владельцу сказано;
  * после продления (RENEW) докупленный лимит возвращается сразу, и падение этого
    шага не срывает оплаченную выдачу;
  * возврат (REFUNDED) по нашему счёту — ровно один алерт и только при РЕАЛЬНОМ
    переходе COMPLETED → REFUNDED.

Числа, даты и идентификаторы синтетические.
"""

import ast
import importlib
import inspect
import textwrap
import uuid as uuid_lib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Optional

import pytest
from fastapi import HTTPException

extra = importlib.import_module("src.infrastructure.services.overlay_extra_device")
endpoint = importlib.import_module("src.web.endpoints.public.extra_device")
gateway_mod = importlib.import_module("overlay_patches.gateway_payment")
payment = importlib.import_module("src.application.use_cases.gateways.commands.payment")

from src.core.enums import Currency, PaymentGatewayType, PurchaseType, TransactionStatus  # noqa: E402

from test_extra_device_buy import (  # noqa: E402
    EXPIRE,
    NOW,
    PANEL_UUID,
    SUB_ID,
    USER,
    USER_ID,
    FakeGatewayDao,
    FakeRemnawave,
    FakeSession,
    Log,
    call_buy,
    config_on,  # noqa: F401 — фикстура включает продажи
)


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
                    o["id"], o["user_id"], o["subscription_id"], o.get("slot_id"), o["status"],
                    o["kind"], o["amount"], o["cov_start"], o["period_end"], o.get("attempts", 0),
                    o["created_at"], o["payment_id"], o.get("price_per_30d", 90),
                )
            )
        if "FROM extra_device_orders o WHERE o.payment_id" in sql:
            # Чтение без замка — им пользуются алерт возврата и счётчик попыток.
            self.log.append(("order_read", None))
            if self.order is None:
                return _Row(None)
            o = self.order
            return _Row((o["id"], o["status"], o["amount"], o.get("slot_id"), o["period_end"], o["user_id"]))
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
        return await super().execute(stmt, params)


class _Row:
    def __init__(self, value: Any) -> None:
        self.value = value

    def first(self):
        return self.value

    def scalar(self):
        if self.value is None:
            return None
        return self.value[0] if isinstance(self.value, tuple) else self.value

    def all(self):
        return []


def order(status="pending", created=None, **over):
    data = {
        "id": 1,
        "user_id": USER_ID,
        "subscription_id": SUB_ID,
        "slot_id": None,
        "status": status,
        "kind": "new",
        "amount": Decimal(60),
        "cov_start": NOW - timedelta(minutes=5),
        "period_end": EXPIRE,
        "attempts": 0,
        "created_at": created or NOW - timedelta(minutes=5),
        "payment_id": uuid_lib.uuid4(),
        "price_per_30d": 90,
    }
    data.update(over)
    return data


# ── 1. счёт ──────────────────────────────────────────────────────────────────


async def test_checkout_uses_exact_amount_and_records_order_before_url():
    log = Log()
    session = OrderSession(log)
    result = await call_buy(
        session, log, pay="gateway", gateway_type=PaymentGatewayType.YOOMONEY
    )
    assert result["result"] == "pending"
    assert result["payment_url"].startswith("https://")
    created = log[log.index_of("create_payment")][1]
    assert created["plan_id"] == extra.SYNTHETIC_PLAN_ID
    assert created["amount"] == Decimal(60)
    assert created["purchase_type"] == PurchaseType.NEW.value
    # Строка заказа — ДО отдачи ссылки.
    assert log.names().index("insert_order") > log.names().index("create_payment")
    assert log.names().index("insert_order") < len(log)


async def test_checkout_without_order_row_gives_503_and_no_url():
    log = Log()

    class NoInsert(OrderSession):
        async def execute(self, stmt, params=None):
            if "INSERT INTO extra_device_orders" in str(stmt):
                raise RuntimeError("relation extra_device_orders does not exist")
            return await super().execute(stmt, params)

    with pytest.raises(HTTPException) as exc:
        await call_buy(
            NoInsert(log), log, pay="gateway", gateway_type=PaymentGatewayType.YOOMONEY
        )
    assert exc.value.status_code == 503


async def test_checkout_refuses_unconfigured_gateway():
    log = Log()
    with pytest.raises(HTTPException) as exc:
        await call_buy(
            OrderSession(log),
            log,
            pay="gateway",
            gateway_type=PaymentGatewayType.YOOKASSA,
            gateways=FakeGatewayDao(),
        )
    assert exc.value.status_code == 404


# ── 2-3. вебхук ──────────────────────────────────────────────────────────────


async def test_paid_order_credits_then_applies():
    log = Log()
    o = order("pending")
    session = OrderSession(log, o)
    result = await extra.handle_paid_order(session, FakeRemnawave(log).sdk, o["payment_id"], NOW)
    assert result["result"] == "applied"
    names = log.names()
    # Сначала деньги на баланс со своим commit, потом место.
    assert names.index("credit") < names.index("spend")
    assert session.credited == Decimal(60)
    assert o["status"] == "applied"
    patch = dict(log[log.index_of("panel_patch")][1])
    assert set(patch) == {"uuid", "hwidDeviceLimit"}


async def test_repeat_webhook_credits_nothing_and_skips_panel():
    log = Log()
    o = order("applied")
    session = OrderSession(log, o)
    result = await extra.handle_paid_order(session, FakeRemnawave(log).sdk, o["payment_id"], NOW)
    assert result["repeat"] is True
    assert "credit" not in log.names()
    assert "panel_patch" not in log.names()


async def test_foreign_payment_is_not_ours():
    log = Log()
    session = OrderSession(log, None)
    assert await extra.handle_paid_order(session, FakeRemnawave(log).sdk, uuid_lib.uuid4(), NOW) is None


# ── 4-6. отказы после оплаты ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "kwargs, session_kw, reason",
    [
        ({"subscription_id": SUB_ID + 1}, {}, "subscription_changed"),
        ({"created": NOW - timedelta(hours=30)}, {}, "stale"),
        ({}, {"enough": False}, "balance_spent"),
        ({}, {"frozen_at": NOW - timedelta(days=1)}, "frozen"),
        ({"period_end": EXPIRE + timedelta(days=30)}, {}, "subscription_shortened"),
    ],
)
async def test_paid_order_rejected_with_reason_money_stays_on_balance(kwargs, session_kw, reason):
    log = Log()
    o = order("credited", **kwargs)
    session = OrderSession(log, o, **session_kw)
    result = await extra.handle_paid_order(session, FakeRemnawave(log).sdk, o["payment_id"], NOW)
    assert result["result"] == "rejected"
    assert result["reason"] == reason
    assert o["status"] == "rejected"
    assert "panel_patch" not in log.names()


async def test_disabled_switch_does_not_stop_a_paid_order():
    """Деньги уже взяты: выключенный в этот момент тумблер не повод их не отработать."""
    log = Log()
    o = order("credited")
    session = OrderSession(log, o)
    result = await extra.handle_paid_order(session, FakeRemnawave(log).sdk, o["payment_id"], NOW)
    assert result["result"] == "applied"


async def test_late_order_whose_window_already_closed_is_rejected_not_crashed():
    """Оплаченный период кончился, пока счёт лежал: слот с концом раньше начала не
    создать (ck_eds_period). Честный `rejected`, деньги на балансе — а не исключение
    и вечные повторы крона."""
    log = Log()
    o = order("credited", period_end=NOW - timedelta(minutes=1))
    session = OrderSession(log, o, expire_at=NOW + timedelta(days=30))
    result = await extra.handle_paid_order(session, FakeRemnawave(log).sdk, o["payment_id"], NOW)
    assert result["result"] == "rejected"
    assert result["reason"] == "window_closed"
    assert "insert_slot" not in log.names()


async def test_panel_failure_keeps_order_credited_for_retry():
    log = Log()
    o = order("credited")
    session = OrderSession(log, o)
    with pytest.raises(Exception):
        await extra.handle_paid_order(
            session, FakeRemnawave(log, fail=True).sdk, o["payment_id"], NOW
        )
    # Применение не закоммичено: в базе заказ остаётся credited, деньги на балансе,
    # и крон повторит. Считаем коммиты после зачисления — их быть не должно.
    assert log.names().count("commit") == 0
    assert session.commits == 0


# ── ветка в обработчике успешной оплаты ──────────────────────────────────────


class FakeNotifier:
    def __init__(self, log: Log) -> None:
        self.log = log

    async def notify_user(self, user, payload=None, **kw):
        self.log.append(("notify_user", payload.i18n_kwargs["content"]))

    async def notify_admins(self, payload):
        self.log.append(("notify_admins", payload.i18n_kwargs["content"]))


class Boom:
    def __getattr__(self, name):
        raise AssertionError(f"ветка докупки не должна трогать {name}")


def fake_transaction(payment_id, purchase_type=PurchaseType.NEW):
    return SimpleNamespace(
        payment_id=payment_id,
        is_test=False,
        purchase_type=purchase_type,
        gateway_type=PaymentGatewayType.YOOMONEY,
        currency=Currency.RUB,
        pricing=SimpleNamespace(final_amount=Decimal(60), original_amount=Decimal(60), discount_percent=0, is_free=False),
        plan_snapshot=extra.synthetic_snapshot(20),
    )


async def test_handle_success_exits_before_subscription_for_our_order(monkeypatch):
    log = Log()
    o = order("pending")
    session = OrderSession(log, o)
    live = dict(extra.DEFAULT_CONFIG, enabled=True, price_rub_30d=90)
    monkeypatch.setattr(extra, "load_config", lambda: live)
    me = SimpleNamespace(
        session=session,
        remnawave=FakeRemnawave(log),
        notifier=FakeNotifier(log),
        # Любое обращение к подписке, рефералке или редиректу — провал теста.
        subscription_dao=Boom(),
        purchase_subscription=Boom(),
        event_publisher=Boom(),
        assign_referral_rewards=Boom(),
        redirect=Boom(),
        user_dao=Boom(),
        uow=Boom(),
        transaction_dao=Boom(),
    )
    await payment.ProcessPayment._handle_success(me, USER, fake_transaction(o["payment_id"]))
    assert o["status"] == "applied"
    assert any(name == "notify_user" and "Устройство добавлено" in text for name, text in log)


async def test_renew_restores_bought_limit_and_failure_does_not_break_purchase(monkeypatch):
    """Продление ставит тарифный лимит — докупленное место возвращаем сразу, в try."""
    source = textwrap.dedent(inspect.getsource(gateway_mod.apply))
    tree = ast.parse(source)
    handler = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "ProcessPayment_handle_success"
    )
    body = ast.get_source_segment(source, handler)
    # Восстановление — ПОСЛЕ выдачи и внутри try: оплаченная покупка важнее лимита.
    assert body.index("purchase_subscription.system") < body.index("reapply_only")
    reapply = body[body.index("if transaction.purchase_type == PurchaseType.RENEW:") :]
    assert reapply.index("try:") < reapply.index("reconcile_user")
    assert "after_renew=True" in reapply


def test_our_branch_runs_after_topup_and_gift_and_returns():
    """Порядок веток и обязательный return: иначе счёт докупки выдал бы подписку."""
    source = textwrap.dedent(inspect.getsource(gateway_mod.apply))
    tree = ast.parse(source)
    handler = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "ProcessPayment_handle_success"
    )
    body = ast.get_source_segment(source, handler)
    assert body.index("try_credit_topup") < body.index("_handle_extra_device")
    assert body.index("try_issue_gift") < body.index("_handle_extra_device")
    assert body.index("_handle_extra_device") < body.index("subscription_dao.get_current")
    tail = body[body.index("_handle_extra_device") :]
    assert tail.index("return") < tail.index("subscription_dao.get_current")


def test_service_is_imported_at_module_level():
    """Урок NameError: функции правки ищут имена ЗДЕСЬ, а не в шапке модуля базы."""
    assert gateway_mod.extra is extra
    tree = ast.parse(inspect.getsource(gateway_mod))
    modules = [n.module for n in tree.body if isinstance(n, ast.ImportFrom)]
    assert any(m and "overlay_extra_device" in str(m) or m == "src.infrastructure.services" for m in modules)


def test_names_of_our_functions_resolve():
    """expect_names_resolve: наши функции не должны искать глобали в чужом модуле."""
    op = importlib.import_module("overlay_patches")
    assert op.failures() == []


# ── возврат ──────────────────────────────────────────────────────────────────


async def test_refund_alert_only_on_real_transition(monkeypatch):
    log = Log()
    pid = uuid_lib.uuid4()
    carry = importlib.import_module("src.infrastructure.services.overlay_plan_change")

    async def status(session, payment_id):
        return TransactionStatus.REFUNDED.value

    async def found(session, payment_id):
        return {
            "id": 1,
            "status": "applied",
            "amount": Decimal(60),
            "slot_id": 55,
            "period_end": EXPIRE,
            "user_id": USER_ID,
        }

    monkeypatch.setattr(carry, "transaction_status", status)
    monkeypatch.setattr(extra, "order_by_payment", found)
    me = SimpleNamespace(
        session=OrderSession(log),
        notifier=FakeNotifier(log),
        user_dao=SimpleNamespace(get_by_id=lambda uid: _async(USER)),
    )
    data = SimpleNamespace(payment_id=pid)
    await gateway_mod._alert_refunded_device(me, data, TransactionStatus.COMPLETED.value)
    alerts = [t for n, t in log if n == "notify_admins"]
    assert len(alerts) == 1 and "Возврат по докупке" in alerts[0] and "#55" in alerts[0]

    # Переход не совпал (счёт не был COMPLETED) — второго алерта нет.
    log.clear()
    await gateway_mod._alert_refunded_device(me, data, TransactionStatus.PENDING.value)
    assert log == []


async def _async(value):
    return value


async def test_panel_failure_on_webhook_does_not_grant_a_subscription(monkeypatch):
    """Панель молчит — заказ ждёт крона, но подписку по этому счёту НЕ выдаём.

    Пустить исключение наружу нельзя вдвойне: обычная ветка обработчика выдала бы по
    счёту докупки подписку синтетического тарифа, а шлюз получил бы 500 и начал
    повторять вебхук по уже зачисленным деньгам.
    """
    log = Log()
    o = order("credited")
    session = OrderSession(log, o)
    live = dict(extra.DEFAULT_CONFIG, enabled=True, price_rub_30d=90)
    monkeypatch.setattr(extra, "load_config", lambda: live)
    me = SimpleNamespace(
        session=session,
        remnawave=FakeRemnawave(log, fail=True),
        notifier=FakeNotifier(log),
        subscription_dao=Boom(),
        purchase_subscription=Boom(),
        event_publisher=Boom(),
        assign_referral_rewards=Boom(),
        redirect=Boom(),
        user_dao=Boom(),
        uow=Boom(),
        transaction_dao=Boom(),
    )
    # Ни исключения наружу, ни обращения к подписке — обработчик просто выходит
    # (любое поле Boom бросило бы AssertionError).
    await payment.ProcessPayment._handle_success(me, USER, fake_transaction(o["payment_id"]))
    names = log.names()
    # Применение не закоммичено — в базе заказ остаётся `credited` для крона.
    assert "commit" not in names[: names.index("panel_patch")]
    assert "rollback" in names, "сессию после сбоя не откатили — следующий шаг упал бы"
    assert "attempt" in names, "неудачная попытка не отмечена — крон не поймёт, когда сдаваться"


async def test_renew_reapply_failure_leaves_the_session_usable(monkeypatch):
    """Сбой восстановления лимита не должен завалить следующий шаг обработчика.

    После него по ТОЙ ЖЕ сессии читает отчёт о переносе остатка: оборванная
    транзакция превратила бы одну незаметную проблему в две.
    """
    log = Log()
    session = OrderSession(log, None)
    calls: list[str] = []

    async def boom(*a, **kw):
        calls.append("reconcile")
        raise RuntimeError("панель недоступна")

    monkeypatch.setattr(extra, "reconcile_user", boom)
    me = SimpleNamespace(session=session, remnawave=FakeRemnawave(log))
    try:
        await extra.reconcile_user(session, sdk=None, remnawave=None, user_id=1, config={}, now=NOW)
    except RuntimeError:
        pass
    assert calls == ["reconcile"]
    # Сам откат заперт разбором исходника: подделка сессии его не почувствует.
    source = textwrap.dedent(inspect.getsource(gateway_mod.apply))
    tail = source[source.index("PurchaseType.RENEW:") :]
    tail = tail[: tail.index("# OVERLAY: отчёт о переносе остатка")]
    assert "rollback" in tail, "после сбоя восстановления сессию не откатывают"


# ── проверка после ревью: ветка докупки стоит в пути КАЖДОГО платежа ─────────


def test_our_branch_is_filtered_by_plan_snapshot_before_any_sql():
    """Предфильтр по снимку тарифа — ДО первого обращения к новым таблицам.

    Порядок здесь важнее читаемости: таблицы докупки создаёт миграция, которую
    накатывает только контейнер бота, а вебхук исполняет taskiq-воркер. В окне
    выкатки чтение `extra_device_orders` у воркера падало бы на КАЖДОМ платеже.
    """
    source = textwrap.dedent(inspect.getsource(gateway_mod._handle_extra_device))
    body = source[source.index('"""', source.index('"""') + 3) + 3 :]
    assert body.index("SYNTHETIC_PLAN_ID") < body.index("handle_paid_order")


async def test_ordinary_payment_is_granted_even_if_extra_tables_are_broken(monkeypatch):
    """Обычная покупка доходит до выдачи, даже когда докупка полностью сломана.

    Раньше первым делом читались новые таблицы: одно исключение (их ещё нет) —
    и `_handle_success` выходил ДО выдачи. Деньги приняты, подписки нет, никто не
    уведомлён. Самый дорогой из возможных отказов, поэтому проверка отдельная.
    """
    log = Log()

    async def boom(*a, **kw):
        raise RuntimeError("relation extra_device_orders does not exist")

    monkeypatch.setattr(extra, "handle_paid_order", boom)

    granted: list = []

    class Purchase:
        async def system(self, dto):
            granted.append(dto)

    class Publisher:
        async def publish(self, event):
            log.append(("event", None))

    class Subs:
        async def get_current(self, uid):
            return None

    plan = SimpleNamespace(
        id=10, name="Тариф", type="BASE", traffic_limit=100, device_limit=2,
        duration=30, is_trial=False,
    )
    tx = fake_transaction(uuid_lib.uuid4())
    tx.plan_snapshot = plan
    me = SimpleNamespace(
        session=OrderSession(log),
        remnawave=FakeRemnawave(log),
        notifier=FakeNotifier(log),
        subscription_dao=Subs(),
        purchase_subscription=Purchase(),
        event_publisher=Publisher(),
        assign_referral_rewards=SimpleNamespace(system=lambda dto: _async(None)),
        redirect=SimpleNamespace(to_success_payment=lambda *a: _async(None)),
        user_dao=SimpleNamespace(get_by_id=lambda uid: _async(USER)),
        uow=Boom(),
        transaction_dao=Boom(),
    )
    await payment.ProcessPayment._handle_success(me, USER, tx)
    assert len(granted) == 1, "обычная покупка не выдана — оплата принята впустую"


async def test_deferred_extra_order_alerts_the_owner(monkeypatch):
    """Место не выдалось сразу — владелец узнаёт об этом, а не только лог."""
    log = Log()
    o = order("credited")
    session = OrderSession(log, o)
    live = dict(extra.DEFAULT_CONFIG, enabled=True, price_rub_30d=90)
    monkeypatch.setattr(extra, "load_config", lambda: live)
    me = SimpleNamespace(
        session=session,
        remnawave=FakeRemnawave(log, fail=True),
        notifier=FakeNotifier(log),
        subscription_dao=Boom(),
        purchase_subscription=Boom(),
        event_publisher=Boom(),
        assign_referral_rewards=Boom(),
        redirect=Boom(),
        user_dao=Boom(),
        uow=Boom(),
        transaction_dao=Boom(),
    )
    await payment.ProcessPayment._handle_success(me, USER, fake_transaction(o["payment_id"]))
    alerts = [t for n, t in log if n == "notify_admins"]
    assert any("не выдано сразу" in a for a in alerts)


# ── компенсация лимита в панели ──────────────────────────────────────────────


async def test_commit_failure_after_panel_returns_the_limit_back():
    """Панель приняла лимит, база — нет: без компенсации повтор дал бы +2 за одну оплату.

    Вебхук панели поднимает `device_limit` в нашей базе, а цель считается «+1 к
    текущему» — второй заход того же заказа прибавил бы ещё одно место.
    """
    log = Log()
    o = order("credited")
    session = OrderSession(log, o, commit_fails=True)
    with pytest.raises(Exception):
        await extra.handle_paid_order(session, FakeRemnawave(log).sdk, o["payment_id"], NOW)
    patches = [dict(p) for n, p in log if n == "panel_patch"]
    assert len(patches) == 2, "компенсирующего PATCH нет"
    assert patches[0]["hwidDeviceLimit"] == 3
    assert patches[1]["hwidDeviceLimit"] == 2, "лимит в панели не вернули на прежний"


async def test_panel_mismatch_after_apply_is_compensated():
    """Панель ответила не тем лимитом — но могла применить его до того (таймаут).

    Слота у нас не останется, значит крон такой +1 никогда не увидит: возвращаем сами.
    """
    log = Log()
    o = order("credited")
    session = OrderSession(log, o)
    with pytest.raises(Exception):
        await extra.handle_paid_order(
            session, FakeRemnawave(log, returns=99).sdk, o["payment_id"], NOW
        )
    patches = [dict(p) for n, p in log if n == "panel_patch"]
    assert patches[-1]["hwidDeviceLimit"] == 2, "прежний лимит в панель не вернули"


async def test_compensation_never_raises():
    """Компенсация — аварийная ветка: её собственный сбой не должен ничего ломать."""
    log = Log()
    st = extra.UserState(
        user_id=USER_ID, balance=Decimal(0), sub_id=SUB_ID, sub_status="ACTIVE", is_trial=False,
        expire_at=EXPIRE, device_limit=3, plan_id=7, plan_device_limit=2, remna_uuid=PANEL_UUID,
        sub_updated_at=NOW, frozen_at=None, reserve_expire_at=None, slots=(),
    )
    assert await extra.compensate_limit(FakeRemnawave(log, fail=True).sdk, st, 2) is False
    assert await extra.compensate_limit(None, st, 2) is False
    assert await extra.compensate_limit(FakeRemnawave(log).sdk, st, 2) is True
