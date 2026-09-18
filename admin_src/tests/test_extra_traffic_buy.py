"""Докупка трафика: покупка с баланса — порядок денежного пути на подделках.

ЧТО ЗАПИРАЕМ (формулы запирает test_extra_traffic_math.py):

  * первый запрос — замок строки `users` (`FOR UPDATE OF u`), и только потом деньги;
  * порядок: замок → чтение панели → закрытие просроченных прибавок → списание
    условным UPDATE → транзакция «Баланс · трафик» со снимком −5 → запись прибавки →
    заказ → UPDATE лимита в базе → PATCH панели ровно телом `{uuid, trafficLimitBytes}`
    → ОДИН commit → уведомление владельцу;
  * в панель уходит `байты панели + N ГБ`, а не «наши округлённые ГБ + N»;
  * панель ошиблась или вернула не тот лимит — rollback и 502 «деньги не списаны»;
  * commit упал после панели — лимит компенсируется обратно и владелец предупреждён;
  * тот же `request_id` дважды — одно списание и один PATCH;
  * чужой `request_id` — 409 и ни слова о чужом заказе;
  * покупка в минуту после обнуления не даёт двойного объёма (просроченная прибавка
    закрывается в той же транзакции);
  * статус из ответа панели (LIMITED → ACTIVE) записывается в нашу строку.

Числа, даты и идентификаторы синтетические.
"""

import importlib
import uuid as uuid_lib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Optional

import pytest
from fastapi import HTTPException

extra = importlib.import_module("src.infrastructure.services.overlay_extra_traffic")
endpoint = importlib.import_module("src.web.endpoints.public.extra_traffic")

from src.core.enums import Currency, PaymentGatewayType  # noqa: E402
from src.core.utils.converters import gb_to_bytes  # noqa: E402

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
USER_ID = 7
SUB_ID = 100
EXPIRE = NOW + timedelta(days=40)
# Синтетический идентификатор панели: remnapy проверяет формат UUID на входе.
PANEL_UUID = "11111111-1111-1111-1111-111111111111"
# Создан 7-го числа → MONTH_ROLLING обнулит расход 7-го в 00:10 UTC.
PANEL_CREATED = datetime(2025, 5, 7, 14, 30, tzinfo=timezone.utc)
WINDOW = datetime(2026, 10, 7, 0, 10, tzinfo=timezone.utc)

PLAN_GB = 300


class Log(list):
    def names(self) -> list[str]:
        return [name for name, _ in self]

    def index_of(self, name: str) -> int:
        return self.names().index(name)


class FakeResult:
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


class FakeSession:
    """Журнал SQL. Каждый запрос узнаётся по куску текста — как в жизни, по смыслу."""

    def __init__(
        self,
        log: Log,
        *,
        balance: Decimal = Decimal("500"),
        traffic_limit: int = PLAN_GB,
        plan_traffic_limit: int = PLAN_GB,
        strategy: str = "MONTH_ROLLING",
        grants: tuple = (),
        saved_order: Optional[tuple] = None,
        enough: bool = True,
        commit_fails: bool = False,
        status: str = "ACTIVE",
        expire_at: datetime = EXPIRE,
        frozen_at=None,
        reserve_expire_at=None,
        is_trial: bool = False,
    ) -> None:
        self.log = log
        self.balance = balance
        self.traffic_limit = traffic_limit
        self.plan_traffic_limit = plan_traffic_limit
        self.strategy = strategy
        self.grants = grants
        self.saved_order = saved_order
        self.enough = enough
        self.commit_fails = commit_fails
        self.status = status
        self.expire_at = expire_at
        self.frozen_at = frozen_at
        self.reserve_expire_at = reserve_expire_at
        self.is_trial = is_trial
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, stmt: Any, params: Any = None) -> FakeResult:
        sql = " ".join(str(stmt).split())
        if "FROM users u LEFT JOIN subscriptions" in sql:
            self.log.append(("lock" if "FOR UPDATE OF u" in sql else "read", dict(params or {})))
            return FakeResult(
                (
                    self.balance,
                    SUB_ID,
                    self.status,
                    self.is_trial,
                    self.expire_at,
                    self.traffic_limit,
                    self.strategy,
                    7,
                    self.plan_traffic_limit,
                    PANEL_UUID,
                    NOW - timedelta(days=5),
                )
            )
        if "FROM extra_traffic_grants WHERE user_id" in sql:
            self.log.append(("grants", None))
            return FakeResult(rows=list(self.grants))
        if "subscription_freezes" in sql:
            self.log.append(("pause", None))
            return FakeResult((self.frozen_at, self.reserve_expire_at))
        if "WHERE request_id = :rid" in sql:
            self.log.append(("order_by_request", None))
            return FakeResult(self.saved_order)
        if "SET status = 'ended', end_reason = 'reset'" in sql:
            self.log.append(("close_due", dict(params)))
            return FakeResult(None)
        if "cabinet_balance = cabinet_balance - :amount" in sql:
            self.log.append(("spend", dict(params)))
            if not self.enough:
                return FakeResult(None)
            return FakeResult(self.balance - Decimal(str(params["amount"])))
        if "INSERT INTO extra_traffic_grants" in sql:
            self.log.append(("insert_grant", dict(params)))
            return FakeResult(55)
        if "INSERT INTO extra_traffic_orders" in sql:
            self.log.append(("insert_order", dict(params)))
            return FakeResult(1)
        if "UPDATE subscriptions SET traffic_limit" in sql:
            self.log.append(("set_limit_db", dict(params)))
            return FakeResult(None)
        if "UPDATE subscriptions SET status" in sql:
            self.log.append(("set_status_db", dict(params)))
            return FakeResult(None)
        if "SET last_applied_at" in sql:
            self.log.append(("touch_grants", None))
            return FakeResult(None)
        self.log.append(("sql", sql))
        return FakeResult(None)

    async def commit(self) -> None:
        if self.commit_fails:
            self.log.append(("commit_failed", None))
            raise RuntimeError("база отвалилась")
        self.commits += 1
        self.log.append(("commit", None))

    async def rollback(self) -> None:
        self.rollbacks += 1
        self.log.append(("rollback", None))


class FakeUsers:
    def __init__(
        self,
        log: Log,
        *,
        fail: bool = False,
        returns: Optional[int] = None,
        status: str = "ACTIVE",
    ) -> None:
        self.log = log
        self.fail = fail
        self.returns = returns
        self.status = status

    async def update_user(self, body: Any) -> Any:
        payload = body.model_dump(exclude_unset=True, by_alias=True)
        self.log.append(("panel_patch", payload))
        if self.fail:
            raise RuntimeError("панель недоступна")
        limit = self.returns if self.returns is not None else body.traffic_limit_bytes
        return SimpleNamespace(traffic_limit_bytes=limit, status=self.status)


class FakeSdk:
    def __init__(self, log: Log, **kw) -> None:
        self.users = FakeUsers(log, **kw)


class FakeRemnawave:
    """Панель: чтение пользователя и SDK для узкого PATCH."""

    def __init__(
        self,
        log: Log,
        *,
        limit_bytes: Optional[int] = None,
        used_bytes: int = 0,
        panel_status: str = "ACTIVE",
        created_at: Optional[datetime] = PANEL_CREATED,
        read_fails: bool = False,
        **kw,
    ) -> None:
        self.log = log
        self.sdk = FakeSdk(log, **kw)
        self.limit_bytes = gb_to_bytes(PLAN_GB) if limit_bytes is None else limit_bytes
        self.used_bytes = used_bytes
        self.panel_status = panel_status
        self.created_at = created_at
        self.read_fails = read_fails

    async def get_user_by_uuid(self, uuid: Any) -> Any:
        self.log.append(("panel_read", str(uuid)))
        if self.read_fails:
            raise RuntimeError("панель недоступна")
        return SimpleNamespace(
            uuid=uuid,
            traffic_limit_bytes=self.limit_bytes,
            status=self.panel_status,
            created_at=self.created_at,
            user_traffic=SimpleNamespace(used_traffic_bytes=self.used_bytes),
        )


class FakeTransactionDao:
    def __init__(self, log: Log) -> None:
        self.log = log

    async def create(self, transaction: Any) -> Any:
        self.log.append(
            (
                "transaction",
                {
                    "status": transaction.status.value,
                    "plan_id": transaction.plan_snapshot.id,
                    "amount": transaction.pricing.final_amount,
                    "display": transaction.gateway_display_name,
                    "traffic": transaction.plan_snapshot.traffic_limit,
                },
            )
        )
        return transaction


class FakeGateway:
    def __init__(self, gtype=PaymentGatewayType.YOOMONEY, currency=Currency.RUB, configured=True) -> None:
        self.type = gtype
        self.currency = currency
        self.settings = SimpleNamespace(is_configured=configured, display_name=None)


class FakeGatewayDao:
    def __init__(self, active=None, every=None) -> None:
        self._active = active if active is not None else [FakeGateway()]
        self._all = every if every is not None else [FakeGateway()]

    async def get_active(self) -> list:
        return list(self._active)

    async def get_all(self, only_active: bool = False, sorted: bool = True) -> list:
        return list(self._all)


class FakeNotifier:
    def __init__(self, log: Log) -> None:
        self.log = log

    async def notify_admins(self, payload: Any) -> None:
        self.log.append(("notify_admins", payload.i18n_kwargs["content"]))

    async def notify_user(self, user: Any, payload: Any = None, **kw) -> None:
        self.log.append(("notify_user", payload.i18n_kwargs["content"]))


class FakeCreatePayment:
    def __init__(self, log: Log, fail: bool = False) -> None:
        self.log = log
        self.fail = fail

    async def __call__(self, user: Any, data: Any) -> Any:
        self.log.append(
            (
                "create_payment",
                {
                    "plan_id": data.plan_snapshot.id,
                    "amount": data.pricing.final_amount,
                    "duration": data.plan_snapshot.duration,
                    "traffic": data.plan_snapshot.traffic_limit,
                },
            )
        )
        if self.fail:
            raise RuntimeError("шлюз недоступен")
        return SimpleNamespace(id=uuid_lib.uuid4(), url="https://pay.example.test/1")


USER = SimpleNamespace(
    id=USER_ID,
    log="[USER:7]",
    auth_type=None,
    email=None,
    email_verified=True,
    remna_name="rs_web_7",
    telegram_id=None,
    name="Тест",
    username=None,
)


@pytest.fixture(autouse=True)
def config_on(tmp_path, monkeypatch):
    """Продажи включены, 50 ГБ за 40 ₽ — цифры теста, не боевые."""
    path = tmp_path / "extra_traffic.json"
    path.write_text(
        '{"enabled": true, "gb_per_purchase": 50, "price_rub": 40, "min_amount_rub": 10,'
        ' "min_hours_left": 2, "max_gb_per_window": 1000, "notify_admins": true}',
        "utf-8",
    )
    monkeypatch.setattr(extra, "CONFIG_PATH", path)
    return path


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    monkeypatch.setattr(endpoint, "datetime_now", lambda: NOW)
    return NOW


@pytest.fixture(autouse=True)
def no_email_gate(monkeypatch):
    monkeypatch.setattr(endpoint, "_assert_email_verified", lambda user: None)


def grant_row(gb=50, ends_at=WINDOW, grant_id=1, sub_id=SUB_ID):
    """Строка прибавки в том виде, в котором её отдаёт ACTIVE_GRANTS_SQL."""
    return (
        grant_id,
        sub_id,
        7,
        gb,
        "MONTH_ROLLING",
        PANEL_CREATED,
        NOW - timedelta(days=1),
        ends_at,
        NOW - timedelta(days=1),
    )


async def call_buy(
    session: FakeSession,
    remnawave: FakeRemnawave,
    log: Log,
    *,
    pay: str = "balance",
    request_id: Any = None,
    expected_amount: str = "40",
    expected_gb: int = 50,
    gateway_dao: Optional[FakeGatewayDao] = None,
    create_payment: Any = None,
):
    body = endpoint.BuyRequest(
        request_id=request_id or uuid_lib.uuid4(),
        pay=pay,
        gateway_type=PaymentGatewayType.YOOMONEY if pay == "gateway" else None,
        expected_amount=Decimal(expected_amount),
        expected_gb=expected_gb,
    )
    # `@inject` dishka прячет нашу функцию — берём исходную, как в соседнем наборе.
    raw = endpoint.buy_extra_traffic.__dishka_orig_func__
    return await raw(
        body=body,
        user=USER,
        session=session,
        remnawave=remnawave,
        payment_gateway_dao=gateway_dao or FakeGatewayDao(),
        transaction_dao=FakeTransactionDao(log),
        create_payment=create_payment or FakeCreatePayment(log),
        notifier=FakeNotifier(log),
    )


# ── порядок денежного пути ──────────────────────────────────────────────────


async def test_money_path_order_and_narrow_panel_body():
    log = Log()
    session = FakeSession(log)
    remnawave = FakeRemnawave(log)

    result = await call_buy(session, remnawave, log)

    assert result["result"] == "applied"
    assert result["gb"] == 50
    assert result["traffic_limit_gb"] == PLAN_GB + 50
    names = log.names()
    # Первым делом ищем заказ по request_id (ДО замка), потом берём замок, и только
    # после него трогаем деньги.
    assert names[0] == "order_by_request"
    assert names[1] == "lock", "деньги не трогаем, пока строка человека не заперта"
    assert names.index("lock") < names.index("spend")
    assert names.index("panel_read") < names.index("spend")
    assert names.index("spend") < names.index("transaction") < names.index("insert_grant")
    assert names.index("insert_grant") < names.index("insert_order")
    assert names.index("insert_order") < names.index("set_limit_db") < names.index("panel_patch")
    assert names.index("panel_patch") < names.index("commit")
    assert session.commits == 1

    body = dict(log[log.index_of("panel_patch")][1])
    assert set(body) == {"uuid", "trafficLimitBytes"}, "полное тело съело бы дни паузы"
    assert body["trafficLimitBytes"] == gb_to_bytes(PLAN_GB) + gb_to_bytes(50)

    transaction = log[log.index_of("transaction")][1]
    assert transaction["plan_id"] == extra.SYNTHETIC_PLAN_ID
    assert transaction["display"] == "Баланс · трафик"
    assert transaction["amount"] == Decimal(40)
    assert transaction["traffic"] == 50

    grant = log[log.index_of("insert_grant")][1]
    assert grant["gb"] == 50
    assert grant["ends"] == WINDOW, "срок прибавки — ближайшее обнуление, а не конец подписки"
    assert grant["created"] == PANEL_CREATED, "якорь окна обязан уехать в строку"


async def test_panel_bytes_not_our_rounded_gb():
    """Лимит в панели не кратен ГБ — прибавка не должна сдвинуть его округлением."""
    log = Log()
    odd = gb_to_bytes(PLAN_GB) + 700_000_000
    session = FakeSession(log)
    remnawave = FakeRemnawave(log, limit_bytes=odd)

    await call_buy(session, remnawave, log)

    body = dict(log[log.index_of("panel_patch")][1])
    assert body["trafficLimitBytes"] == odd + gb_to_bytes(50)


async def test_limited_becomes_active_and_status_is_saved():
    """Панель сама снимает LIMITED — её ответ пишем к себе, не дожидаясь вебхука."""
    log = Log()
    session = FakeSession(log, status="LIMITED")
    remnawave = FakeRemnawave(log, panel_status="LIMITED", status="ACTIVE")

    result = await call_buy(session, remnawave, log)

    assert result["result"] == "applied"
    assert result["unlocked"] is True
    assert log[log.index_of("set_status_db")][1]["s"] == "ACTIVE"


async def test_expired_grant_is_closed_in_the_same_transaction():
    """Покупка через минуту после обнуления не должна дать двойной объём.

    Мутация «убрать close_due_grants/ended_gb» поднимет лимит на 100 ГБ вместо 50.
    """
    log = Log()
    stale = grant_row(gb=50, ends_at=NOW - timedelta(minutes=1))
    session = FakeSession(log, traffic_limit=PLAN_GB + 50, grants=(stale,))
    remnawave = FakeRemnawave(log, limit_bytes=gb_to_bytes(PLAN_GB + 50))

    result = await call_buy(session, remnawave, log)

    assert "close_due" in log.names()
    assert log[log.index_of("panel_patch")][1]["trafficLimitBytes"] == gb_to_bytes(PLAN_GB + 50)
    assert result["traffic_limit_gb"] == PLAN_GB + 50


async def test_second_active_grant_adds_up():
    """Докупок за окно неограниченно — объёмы складываются."""
    log = Log()
    alive = grant_row(gb=50, ends_at=WINDOW)
    session = FakeSession(log, traffic_limit=PLAN_GB + 50, grants=(alive,))
    remnawave = FakeRemnawave(log, limit_bytes=gb_to_bytes(PLAN_GB + 50))

    result = await call_buy(session, remnawave, log)

    assert result["traffic_limit_gb"] == PLAN_GB + 100


# ── отказы панели и базы ────────────────────────────────────────────────────


async def test_panel_failure_rolls_everything_back():
    log = Log()
    session = FakeSession(log)
    remnawave = FakeRemnawave(log, fail=True)

    with pytest.raises(HTTPException) as exc:
        await call_buy(session, remnawave, log)

    assert exc.value.status_code == 502
    assert "не списаны" in exc.value.detail
    assert session.commits == 0 and session.rollbacks >= 1


async def test_panel_that_confirms_a_different_limit_is_a_failure():
    """Молчаливое «принял, но не применил» оставило бы оплаченный, но мёртвый трафик."""
    log = Log()
    session = FakeSession(log)
    remnawave = FakeRemnawave(log, returns=gb_to_bytes(PLAN_GB))

    with pytest.raises(HTTPException) as exc:
        await call_buy(session, remnawave, log)
    assert exc.value.status_code == 502
    assert session.commits == 0


async def test_commit_failure_compensates_the_panel_and_alerts_owner():
    log = Log()
    session = FakeSession(log, commit_fails=True)
    remnawave = FakeRemnawave(log)

    with pytest.raises(HTTPException) as exc:
        await call_buy(session, remnawave, log)

    assert exc.value.status_code == 500
    patches = [body for name, body in log if name == "panel_patch"]
    assert len(patches) == 2, "прежний лимит обязан вернуться в панель"
    assert patches[1]["trafficLimitBytes"] == gb_to_bytes(PLAN_GB)
    assert any(name == "notify_admins" for name, _ in log)


async def test_unreachable_panel_is_a_business_refusal_not_a_purchase():
    log = Log()
    session = FakeSession(log)
    remnawave = FakeRemnawave(log, read_fails=True)

    result = await call_buy(session, remnawave, log)

    assert result == {"result": "not_available", "reason": "panel_unavailable"}
    assert "spend" not in log.names()


async def test_not_enough_balance_is_answered_not_charged():
    log = Log()
    session = FakeSession(log, enough=False, balance=Decimal("5"))
    result = await call_buy(session, FakeRemnawave(log), log)
    assert result["result"] == "insufficient_balance"
    assert session.commits == 0


# ── идемпотентность ─────────────────────────────────────────────────────────


async def test_same_request_id_twice_buys_once():
    log = Log()
    rid = uuid_lib.uuid4()
    saved = (1, USER_ID, "applied", 50, Decimal(40), uuid_lib.uuid4(), None, 55, WINDOW)
    session = FakeSession(log, saved_order=saved)

    result = await call_buy(session, FakeRemnawave(log), log, request_id=rid)

    assert result["result"] == "applied" and result["repeat"] is True
    assert "spend" not in log.names() and "panel_patch" not in log.names()


async def test_foreign_request_id_says_nothing_about_the_order():
    log = Log()
    saved = (1, USER_ID + 1, "applied", 50, Decimal(40), uuid_lib.uuid4(), None, 55, WINDOW)
    session = FakeSession(log, saved_order=saved)

    with pytest.raises(HTTPException) as exc:
        await call_buy(session, FakeRemnawave(log), log)
    assert exc.value.status_code == 409
    assert "40" not in str(exc.value.detail)


async def test_price_or_volume_change_stops_the_purchase():
    """Владелец поменял условия между показом и нажатием — денег не трогаем."""
    log = Log()
    session = FakeSession(log)
    result = await call_buy(session, FakeRemnawave(log), log, expected_amount="30")
    assert result["result"] == "price_changed"
    assert "spend" not in log.names()

    log2 = Log()
    session2 = FakeSession(log2)
    result2 = await call_buy(session2, FakeRemnawave(log2), log2, expected_gb=10)
    assert result2["result"] == "price_changed"
    assert "spend" not in log2.names()


# ── отказы по праву на покупку ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "kwargs, reason",
    [
        ({"is_trial": True}, "trial"),
        ({"traffic_limit": 0}, "unlimited_traffic"),
        ({"plan_traffic_limit": 0}, "unlimited_traffic"),
        ({"frozen_at": NOW - timedelta(days=1)}, "frozen"),
        ({"reserve_expire_at": EXPIRE - timedelta(minutes=30)}, "reserve"),
        ({"expire_at": NOW - timedelta(days=1)}, "not_active"),
    ],
)
async def test_refusals_are_200_with_a_reason_code(kwargs, reason):
    """Коду причины кабинету нужен перевод, а `ApiError.detail` — строка."""
    log = Log()
    session = FakeSession(log, **kwargs)
    result = await call_buy(session, FakeRemnawave(log), log)
    assert result["result"] == "not_available"
    assert result["reason"] == reason
    assert "spend" not in log.names()


async def test_sale_is_refused_right_before_the_reset():
    """Час до обнуления — платить 40 ₽ не за что."""
    log = Log()
    # Создан 18-го: ближайшее обнуление — сегодня в 00:10, следующее — через месяц.
    created = datetime(2025, 5, 19, 3, 0, tzinfo=timezone.utc)
    session = FakeSession(log)
    remnawave = FakeRemnawave(log, created_at=created)
    # Двигаем «сейчас» на 23:30 18 сентября: до сброса 19-го в 00:10 — 40 минут.
    import src.web.endpoints.public.extra_traffic as mod

    mod.datetime_now = lambda: datetime(2026, 9, 18, 23, 30, tzinfo=timezone.utc)
    try:
        result = await call_buy(session, remnawave, log)
    finally:
        mod.datetime_now = lambda: NOW
    assert result["result"] == "not_available"
    assert result["reason"] == "reset_too_soon"


# ── оплата картой ───────────────────────────────────────────────────────────


async def test_checkout_writes_the_order_before_returning_the_link():
    log = Log()
    session = FakeSession(log)
    result = await call_buy(session, FakeRemnawave(log), log, pay="gateway")

    assert result["result"] == "pending" and result["payment_url"]
    names = log.names()
    assert names.index("create_payment") < names.index("insert_order")
    invoice = log[log.index_of("create_payment")][1]
    assert invoice["plan_id"] == extra.SYNTHETIC_PLAN_ID
    assert invoice["amount"] == Decimal(40)
    assert invoice["duration"] >= 1, "duration=0 база читает как «бессрочный тариф»"
    order = log[log.index_of("insert_order")][1]
    assert order["created"] == PANEL_CREATED, "без якоря вебхук не пересчитает окно"
    assert order["ends"] == WINDOW


async def test_gateway_failure_is_503_and_no_order():
    log = Log()
    session = FakeSession(log)
    with pytest.raises(HTTPException) as exc:
        await call_buy(
            session,
            FakeRemnawave(log),
            log,
            pay="gateway",
            create_payment=FakeCreatePayment(log, fail=True),
        )
    assert exc.value.status_code == 503
    assert "insert_order" not in log.names()
