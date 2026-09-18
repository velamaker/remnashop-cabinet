"""Докупка устройства: покупка с баланса — порядок денежного пути на подделках.

ЧТО ЗАПИРАЕМ (формулу запирает test_extra_device_math.py):

  * первый запрос — замок строки `users` (`FOR UPDATE OF u`), и только потом деньги;
  * порядок: списание условным UPDATE → транзакция «Баланс · устройство» со
    снимком −4 → слот → заказ → UPDATE лимита → PATCH панели ровно телом
    `{uuid, hwidDeviceLimit}` → ОДИН commit → уведомление владельцу;
  * панель ошиблась или вернула не тот лимит — rollback и 502 «деньги не списаны»;
  * тот же `request_id` дважды — одно списание и один PATCH;
  * чужой `request_id` — 409 и ни слова о чужом заказе;
  * `expected_amount` меньше серверной цены — `price_changed`, денег не трогаем;
  * причины отказа приходят как 200 с кодом, а не как HTTP-ошибка: `ApiError.detail`
    в кабинете — строка, и переводить код там нечем.

Числа и даты синтетические.
"""

import importlib
import uuid as uuid_lib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Optional

import pytest
from fastapi import HTTPException

extra = importlib.import_module("src.infrastructure.services.overlay_extra_device")
endpoint = importlib.import_module("src.web.endpoints.public.extra_device")

from src.core.enums import Currency, PaymentGatewayType  # noqa: E402

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
DAY = 86400
USER_ID = 7
SUB_ID = 100
EXPIRE = NOW + timedelta(days=20)
# Синтетический идентификатор панели: remnapy проверяет формат UUID на входе.
PANEL_UUID = "11111111-1111-1111-1111-111111111111"


# ── подделки ─────────────────────────────────────────────────────────────────


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
        device_limit: int = 2,
        plan_device_limit: int = 2,
        slots: tuple = (),
        ended_slots: int = 0,
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
        self.device_limit = device_limit
        self.plan_device_limit = plan_device_limit
        self.slots = slots
        self.ended_slots = ended_slots
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
            # Тот же запрос читает и покупка (под замком), и показ (без замка).
            self.log.append(("lock" if "FOR UPDATE OF u" in sql else "read", dict(params or {})))
            return FakeResult(
                (
                    self.balance,
                    SUB_ID,
                    self.status,
                    self.is_trial,
                    self.expire_at,
                    self.device_limit,
                    7,
                    self.plan_device_limit,
                    PANEL_UUID,
                    NOW - timedelta(days=5),
                )
            )
        if "FROM extra_device_slots WHERE user_id" in sql and "status = 'active'" in sql:
            self.log.append(("slots", None))
            return FakeResult(rows=list(self.slots))
        if "subscription_freezes" in sql:
            self.log.append(("pause", None))
            return FakeResult((self.frozen_at, self.reserve_expire_at))
        if "status = 'ended'" in sql and "count(*)" in sql:
            self.log.append(("ended_count", None))
            return FakeResult(self.ended_slots)
        if "WHERE request_id" in sql:
            self.log.append(("order_by_request", None))
            return FakeResult(self.saved_order)
        if "cabinet_balance = cabinet_balance - :amount" in sql:
            self.log.append(("spend", dict(params)))
            if not self.enough:
                return FakeResult(None)
            return FakeResult(self.balance - Decimal(str(params["amount"])))
        if "INSERT INTO extra_device_slots" in sql:
            self.log.append(("insert_slot", dict(params)))
            return FakeResult(55)
        if "UPDATE extra_device_slots SET ends_at" in sql:
            self.log.append(("extend_slot", dict(params)))
            return FakeResult(params["id"] if self.slots else None)
        if "INSERT INTO extra_device_orders" in sql:
            self.log.append(("insert_order", dict(params)))
            return FakeResult(1)
        if "UPDATE subscriptions SET device_limit" in sql:
            self.log.append(("set_limit_db", dict(params)))
            return FakeResult(None)
        if "SET last_applied_at" in sql:
            self.log.append(("touch_slots", None))
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
    def __init__(self, log: Log, *, fail: bool = False, returns: Optional[int] = None) -> None:
        self.log = log
        self.fail = fail
        self.returns = returns

    async def update_user(self, body: Any) -> Any:
        payload = body.model_dump(exclude_unset=True, by_alias=True)
        self.log.append(("panel_patch", payload))
        if self.fail:
            raise RuntimeError("панель недоступна")
        limit = self.returns if self.returns is not None else body.hwid_device_limit
        return SimpleNamespace(hwid_device_limit=limit)


class FakeSdk:
    def __init__(self, log: Log, **kw) -> None:
        self.users = FakeUsers(log, **kw)


class FakeRemnawave:
    def __init__(self, log: Log, **kw) -> None:
        self.sdk = FakeSdk(log, **kw)


class FakeTransactionDao:
    def __init__(self, log: Log) -> None:
        self.log = log

    async def create(self, transaction: Any) -> Any:
        self.log.append(
            (
                "transaction",
                {
                    "status": transaction.status.value,
                    "purchase_type": transaction.purchase_type.value,
                    "plan_id": transaction.plan_snapshot.id,
                    "amount": transaction.pricing.final_amount,
                    "display": transaction.gateway_display_name,
                    "currency": transaction.currency.value,
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
                    "purchase_type": data.purchase_type.value,
                    "duration": data.plan_snapshot.duration,
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
    # Синтетические поля: событие покупки собирает их у обычного платежа.
    telegram_id=None,
    name="Тест",
    username=None,
)


@pytest.fixture(autouse=True)
def config_on(tmp_path, monkeypatch):
    """Продажи включены, цена 90 ₽/30 дн. — цифры теста, не боевые."""
    path = tmp_path / "extra_device.json"
    path.write_text(
        '{"enabled": true, "price_rub_30d": 90, "min_amount_rub": 10, "min_days_left": 3, '
        '"max_extra": 2, "remove_excess_devices": false}',
        "utf-8",
    )
    monkeypatch.setattr(extra, "CONFIG_PATH", path)
    monkeypatch.setattr(endpoint, "_assert_email_verified", lambda user: None)
    monkeypatch.setattr(endpoint, "datetime_now", lambda: NOW)


async def call_buy(session: FakeSession, log: Log, **over) -> Any:
    raw = endpoint.buy_extra_device.__dishka_orig_func__
    body = endpoint.BuyRequest(
        request_id=over.pop("request_id", uuid_lib.uuid4()),
        kind=over.pop("kind", "new"),
        slot_id=over.pop("slot_id", None),
        pay=over.pop("pay", "balance"),
        gateway_type=over.pop("gateway_type", None),
        expected_amount=over.pop("expected_amount", Decimal(60)),
    )
    return await raw(
        body=body,
        user=USER,
        session=session,
        remnawave=over.pop("remnawave", None) or FakeRemnawave(log),
        payment_gateway_dao=over.pop("gateways", None) or FakeGatewayDao(),
        transaction_dao=FakeTransactionDao(log),
        create_payment=over.pop("create_payment", None) or FakeCreatePayment(log),
        notifier=FakeNotifier(log),
    )


def slot_row(slot_id=1, ends=None):
    return (slot_id, SUB_ID, 7, NOW - timedelta(days=5), ends or NOW + timedelta(days=10), NOW, None)


# ── 1. порядок денежного пути ────────────────────────────────────────────────


async def test_balance_purchase_order_and_narrow_patch():
    log = Log()
    session = FakeSession(log)
    result = await call_buy(session, log)

    assert result["result"] == "applied"
    assert result["device_limit"] == 3
    assert result["spent"] == "60"
    names = log.names()
    # Замок — раньше любых денег.
    assert names.index("lock") < names.index("spend")
    for earlier, later in [
        ("spend", "transaction"),
        ("transaction", "insert_slot"),
        ("insert_slot", "insert_order"),
        ("insert_order", "set_limit_db"),
        ("set_limit_db", "panel_patch"),
        ("panel_patch", "commit"),
        ("commit", "notify_admins"),
    ]:
        assert names.index(earlier) < names.index(later), f"{earlier} должен идти до {later}"
    # Ровно один commit: слот, деньги и лимит живут одной транзакцией.
    assert session.commits == 1
    # Тело PATCH — ТОЛЬКО uuid и лимит: полный снимок откатил бы срок паузы.
    patch = dict(log[log.index_of("panel_patch")][1])
    assert set(patch) == {"uuid", "hwidDeviceLimit"}
    assert patch["hwidDeviceLimit"] == 3
    # Транзакция — синтетический снимок −4, рубли, «Баланс · устройство».
    tx = log[log.index_of("transaction")][1]
    assert tx["plan_id"] == extra.SYNTHETIC_PLAN_ID
    assert tx["status"] == "COMPLETED"
    assert tx["display"] == "Баланс · устройство"
    assert tx["currency"] == "RUB"


# ── 2-4. отказы ──────────────────────────────────────────────────────────────


async def test_not_enough_money_touches_no_panel():
    log = Log()
    session = FakeSession(log, enough=False)
    result = await call_buy(session, log)
    assert result["result"] == "insufficient_balance"
    assert "panel_patch" not in log.names()
    assert session.commits == 0
    assert session.rollbacks == 1


async def test_panel_error_rolls_everything_back():
    log = Log()
    session = FakeSession(log)
    with pytest.raises(HTTPException) as exc:
        await call_buy(session, log, remnawave=FakeRemnawave(log, fail=True))
    assert exc.value.status_code == 502
    assert "не списаны" in exc.value.detail
    assert session.commits == 0
    assert session.rollbacks == 1


async def test_panel_returning_other_limit_is_an_error():
    """Молчаливое «принял, но не применил» оставило бы оплаченное место нерабочим."""
    log = Log()
    session = FakeSession(log)
    with pytest.raises(HTTPException) as exc:
        await call_buy(session, log, remnawave=FakeRemnawave(log, returns=2))
    assert exc.value.status_code == 502
    assert session.commits == 0


# ── 5. идемпотентность ───────────────────────────────────────────────────────


async def test_same_request_id_replays_saved_result():
    log = Log()
    order = (1, USER_ID, "applied", "new", Decimal(60), uuid_lib.uuid4(), None, 55, EXPIRE)
    session = FakeSession(log, saved_order=order)
    result = await call_buy(session, log)
    assert result["result"] == "applied"
    assert result["repeat"] is True
    # Ни замка, ни денег, ни панели: ответ взят из журнала заказов.
    assert log.names() == ["order_by_request"]


async def test_foreign_request_id_is_409_and_tells_nothing():
    log = Log()
    order = (1, USER_ID + 1, "applied", "new", Decimal(999), uuid_lib.uuid4(), None, 55, EXPIRE)
    session = FakeSession(log, saved_order=order)
    with pytest.raises(HTTPException) as exc:
        await call_buy(session, log)
    assert exc.value.status_code == 409
    assert "999" not in str(exc.value.detail)
    assert "spend" not in log.names()


# ── 6. максимум мест и правило «второй раз не предлагаем» ────────────────────


async def test_max_extra_blocks_third_place():
    log = Log()
    session = FakeSession(log, slots=(slot_row(1), slot_row(2)))
    result = await call_buy(session, log)
    assert result == {
        "result": "not_available",
        "reason": "max_reached",
        "quote": result["quote"],
    }
    assert "spend" not in log.names()


async def test_expired_place_does_not_block_a_new_purchase():
    """Решение владельца «отключить и предложить снова»: вечного запрета нет.

    Кончившихся мест у человека сколько угодно — в состоянии их нет, и продажа идёт.
    """
    log = Log()
    session = FakeSession(log, ended_slots=3)
    result = await call_buy(session, log)
    assert result["result"] == "applied"
    assert "spend" in log.names()


# ── 7. продление места ───────────────────────────────────────────────────────


async def test_extend_moves_end_and_does_not_touch_panel():
    log = Log()
    session = FakeSession(
        log, slots=(slot_row(1, ends=NOW + timedelta(days=5)),), expire_at=NOW + timedelta(days=35)
    )
    result = await call_buy(
        session, log, kind="extend", slot_id=1, expected_amount=Decimal(90)
    )
    assert result["result"] == "applied"
    # Мест не прибавилось — лимит и панель не трогаем.
    assert "panel_patch" not in log.names()
    assert "set_limit_db" not in log.names()
    moved = log[log.index_of("extend_slot")][1]
    assert moved["cov"] == NOW + timedelta(days=5)
    assert moved["end"] == NOW + timedelta(days=35)


# ── 8. цена изменилась ───────────────────────────────────────────────────────


async def test_price_changed_when_server_wants_more():
    log = Log()
    session = FakeSession(log)
    result = await call_buy(session, log, expected_amount=Decimal(50))
    assert result["result"] == "price_changed"
    assert result["quote"]["new"]["amount"] == "60"
    assert "spend" not in log.names()


async def test_server_price_wins_when_it_is_lower():
    """Прошли минуты — период короче, цена ниже: берём серверную, а не ожидаемую."""
    log = Log()
    session = FakeSession(log)
    result = await call_buy(session, log, expected_amount=Decimal(80))
    assert result["spent"] == "60"


# ── 9. кому не продаём ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "over, reason",
    [
        ({"frozen_at": NOW - timedelta(days=1)}, "frozen"),
        ({"reserve_expire_at": EXPIRE}, "reserve"),
        ({"is_trial": True}, "trial"),
        ({"status": "LIMITED"}, "not_active"),
    ],
)
async def test_not_available_codes_come_as_200(over, reason):
    log = Log()
    session = FakeSession(log, **over)
    result = await call_buy(session, log)
    assert result["result"] == "not_available"
    assert result["reason"] == reason
    assert "spend" not in log.names()


# ── 10. commit упал после панели ─────────────────────────────────────────────


async def test_commit_failure_after_panel_rolls_panel_back_and_alerts():
    log = Log()
    session = FakeSession(log, commit_fails=True)
    with pytest.raises(HTTPException) as exc:
        await call_buy(session, log)
    assert exc.value.status_code == 500
    patches = [p for name, p in log if name == "panel_patch"]
    # Второй PATCH возвращает панели прежний лимит.
    assert len(patches) == 2
    assert patches[1]["hwidDeviceLimit"] == 2
    assert any(name == "notify_admins" and "commit" in text for name, text in log)


# ── 11-12. гейт почты и выключатель ──────────────────────────────────────────


async def test_email_gate_runs_before_any_sql(monkeypatch):
    log = Log()
    session = FakeSession(log)

    def deny(user):
        raise HTTPException(status_code=409, detail="Подтвердите почту")

    monkeypatch.setattr(endpoint, "_assert_email_verified", deny)
    with pytest.raises(HTTPException) as exc:
        await call_buy(session, log)
    assert exc.value.status_code == 409
    assert log == []


async def test_disabled_hides_price_and_refuses_purchase(tmp_path, monkeypatch):
    path = tmp_path / "off.json"
    path.write_text('{"enabled": false, "price_rub_30d": 90}', "utf-8")
    monkeypatch.setattr(extra, "CONFIG_PATH", path)
    log = Log()
    session = FakeSession(log)
    raw_get = endpoint.get_extra_device.__dishka_orig_func__
    assert await raw_get(user=USER, session=session, payment_gateway_dao=FakeGatewayDao()) == {
        "enabled": False
    }
    result = await call_buy(session, log)
    assert result["reason"] == "disabled"
    assert "spend" not in log.names()


# ── 14. транзакция с баланса без активных рублёвых шлюзов ────────────────────


async def test_balance_purchase_uses_any_rub_gateway_row():
    """gateway_type в транзакции NOT NULL: активных нет — берём строку из таблицы."""
    log = Log()
    session = FakeSession(log)
    result = await call_buy(session, log, gateways=FakeGatewayDao(active=[], every=[FakeGateway()]))
    assert result["result"] == "applied"


async def test_no_rub_gateway_rows_at_all_is_503_before_spending():
    log = Log()
    session = FakeSession(log)
    with pytest.raises(HTTPException) as exc:
        await call_buy(session, log, gateways=FakeGatewayDao(active=[], every=[]))
    assert exc.value.status_code == 503
    assert "spend" not in log.names()


# ── 15. чужой слот ───────────────────────────────────────────────────────────


async def test_foreign_slot_cannot_be_extended():
    log = Log()
    session = FakeSession(log, slots=(slot_row(1),))
    result = await call_buy(session, log, kind="extend", slot_id=999)
    assert result["reason"] == "nothing_to_extend"
    assert "spend" not in log.names()


# ── предложение (GET) ────────────────────────────────────────────────────────


async def test_offer_shows_price_and_slots():
    log = Log()
    session = FakeSession(log, slots=(slot_row(1, ends=NOW + timedelta(days=5)),), expire_at=NOW + timedelta(days=35))
    raw = endpoint.get_extra_device.__dishka_orig_func__
    payload = await raw(user=USER, session=session, payment_gateway_dao=FakeGatewayDao())
    assert payload["new"]["available"] is True
    assert payload["new"]["amount"] == "105"  # 90 × 35/30
    assert payload["extra_count"] == 1
    assert payload["slots"][0]["extend"]["amount"] == "90"
    assert payload["gateways"][0]["gateway_type"] == PaymentGatewayType.YOOMONEY.value
    # Только чтение: замок сразу отпускаем, ничего не коммитим.
    assert session.commits == 0


# ── кнопка в боте ────────────────────────────────────────────────────────────


class FakeAppConfig(SimpleNamespace):
    """Адрес кабинета приходит из конфига приложения (dishka), а не из окружения."""

    def __init__(self) -> None:
        super().__init__(web_cabinet_url="https://cabinet.example.test")


async def test_bot_button_appears_only_when_the_limit_is_full(monkeypatch):
    """Оплаты в боте нет — только ссылка в кабинет, и только когда мест не осталось."""
    menu = importlib.import_module("overlay_patches.menu_dialog")
    log = Log()

    async def base_getter(**kwargs):
        return {"current_count": 2, "max_count": 2, "devices": []}

    monkeypatch.setattr(menu, "devices_getter", base_getter)
    raw = getattr(menu.devices_getter_overlay, "__dishka_orig_func__", menu.devices_getter_overlay)
    data = await raw(session=FakeSession(log), config=FakeAppConfig(), user=SimpleNamespace(id=USER_ID))
    assert data["extra_device_button"] is True
    assert data["extra_device_url"] == "https://cabinet.example.test/devices"
    assert data["extra_device_text"] == menu.EXTRA_DEVICE_BUY_TEXT


async def test_bot_button_is_absent_when_places_are_free(monkeypatch):
    menu = importlib.import_module("overlay_patches.menu_dialog")
    log = Log()

    async def base_getter(**kwargs):
        return {"current_count": 0, "max_count": 2, "devices": []}

    monkeypatch.setattr(menu, "devices_getter", base_getter)
    raw = getattr(menu.devices_getter_overlay, "__dishka_orig_func__", menu.devices_getter_overlay)
    data = await raw(session=FakeSession(log), config=FakeAppConfig(), user=SimpleNamespace(id=USER_ID))
    assert data["extra_device_button"] is False


async def test_bot_button_never_breaks_the_window(monkeypatch):
    """Окно «Устройства» открывают, когда что-то не подключается: падать тут нельзя."""
    menu = importlib.import_module("overlay_patches.menu_dialog")

    async def base_getter(**kwargs):
        return {"current_count": 2, "max_count": 2, "devices": []}

    class Broken:
        async def execute(self, *a, **kw):
            raise RuntimeError("relation extra_device_slots does not exist")

        async def rollback(self):
            return None

    monkeypatch.setattr(menu, "devices_getter", base_getter)
    raw = getattr(menu.devices_getter_overlay, "__dishka_orig_func__", menu.devices_getter_overlay)
    data = await raw(session=Broken(), config=FakeAppConfig(), user=SimpleNamespace(id=USER_ID))
    assert data["extra_device_button"] is False


# ── проверки после ревью ─────────────────────────────────────────────────────


async def test_offer_does_not_hold_the_row_lock():
    """Витрина только показывает: замок строки человека мешал бы параллельной покупке."""
    log = Log()
    session = FakeSession(log)
    raw = endpoint.get_extra_device.__dishka_orig_func__
    await raw(user=USER, session=session, payment_gateway_dao=FakeGatewayDao())
    assert "read" in log.names(), "чтение состояния взяло замок вместо простого SELECT"
    assert "lock" not in log.names()


async def test_offer_tells_whether_devices_will_be_disconnected():
    """Отключение — настройка владельца, и человек обязан знать о ней ДО оплаты."""
    log = Log()
    raw = endpoint.get_extra_device.__dishka_orig_func__
    payload = await raw(user=USER, session=FakeSession(log), payment_gateway_dao=FakeGatewayDao())
    assert payload["removes_excess"] is False  # фикстура теста выключает отключение


async def test_gateway_outage_is_503_not_500():
    """Шлюз не ответил — «попробуйте ещё раз», а не «у них всё сломалось»."""
    log = Log()
    with pytest.raises(HTTPException) as exc:
        await call_buy(
            FakeSession(log),
            log,
            pay="gateway",
            gateway_type=PaymentGatewayType.YOOMONEY,
            create_payment=FakeCreatePayment(log, fail=True),
        )
    assert exc.value.status_code == 503
