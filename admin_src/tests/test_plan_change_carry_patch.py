"""Перенос остатка при смене тарифа: оркестровка зачисления на подделках.

ЧТО ЗАПИРАЕМ. Формулу запирает test_plan_change_carry_math.py. Здесь — порядок
денежного пути в правке `PurchaseSubscription._execute` (plan_change_carryover.py):

  * замок строки users и СВЕЖЕЕ чтение подписки сырым SQL — раньше любого ORM-чтения;
    устаревший срок из identity map не должен стать основой расчёта;
  * панель получает ОДИН вызов `update_user(subscription=черновик)` со сроком
    «сейчас + длительность + бонус» и сбросом трафика — никакого `plan=`, при
    котором база сама ставит срок с нуля;
  * старая строка DELETED, новая — со сроком из ответа панели, журнал со строкой
    новой подписки и текущим счётом в исключениях, скидка погашена, один commit;
  * повтор по тому же счёту — ни панели, ни строк, ни commit;
  * упала панель — исключение наружу, commit и журнала нет;
  * упал журнал или загрузка состояния — выдача всё равно закоммичена, итог с ошибкой
    лежит для отчёта владельцу;
  * пауза гасится до commit; NEW / RENEW / триал / выключатель — нетронутая база;
  * отчёт владельцу и алерт возврата (обёртка ProcessPayment).

Подделки ведут общий журнал вызовов — порядок проверяется по нему. Запуск — внутри
образа бота, как остальные тесты рядом.
"""

import importlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from types import SimpleNamespace
from typing import Any, Optional

import pytest

purchase = importlib.import_module("src.application.use_cases.subscription.commands.purchase")
payment = importlib.import_module("src.application.use_cases.gateways.commands.payment")
patch_mod = importlib.import_module("overlay_patches.plan_change_carryover")
gateway_mod = importlib.import_module("overlay_patches.gateway_payment")
carry = importlib.import_module("src.infrastructure.services.overlay_plan_change")

from remnapy.enums.users import TrafficLimitStrategy  # noqa: E402

from src.application.dto import PlanSnapshotDto  # noqa: E402
from src.core.enums import (  # noqa: E402
    Currency,
    PaymentGatewayType,
    PlanType,
    PurchaseType,
    SubscriptionStatus,
    TransactionStatus,
)

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
DAY = 86400
OLD_PLAN, NEW_PLAN = 10, 20


# ── подделки ─────────────────────────────────────────────────────────────────


class Log(list):
    def names(self) -> list[str]:
        return [name for name, _ in self]

    def index_of(self, name: str) -> int:
        return self.names().index(name)


class FakeResult:
    def __init__(self, row: Optional[tuple]) -> None:
        self.row = row

    def first(self) -> Optional[tuple]:
        return self.row


class FakeNested:
    def __init__(self, log: Log) -> None:
        self.log = log

    async def __aenter__(self) -> "FakeNested":
        self.log.append(("savepoint", None))
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self.log.append(("savepoint_rollback" if exc_type else "savepoint_release", None))
        return False  # как у SQLAlchemy: откат к точке сохранения, исключение — наружу


class FakeSession:
    def __init__(self, log: Log, row: tuple) -> None:
        self.log = log
        self.row = row

    async def execute(self, stmt: Any, params: Any = None) -> FakeResult:
        sql = str(stmt)
        if "FOR UPDATE" in sql:
            self.log.append(("lock", dict(params)))
            return FakeResult(self.row)
        self.log.append(("sql", sql))
        return FakeResult(None)

    def begin_nested(self) -> FakeNested:
        return FakeNested(self.log)

    async def rollback(self) -> None:
        self.log.append(("rollback", None))


class FakeUow:
    def __init__(self, log: Log) -> None:
        self.log = log

    async def __aenter__(self) -> "FakeUow":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if exc_type:
            self.log.append(("uow_rollback", None))

    async def commit(self) -> None:
        self.log.append(("commit", None))


class FakeSubscriptionDao:
    def __init__(self, log: Log, current: Any, new_id: int = 77) -> None:
        self.log = log
        self.current = current
        self.new_id = new_id

    async def get_current(self, user_id: int) -> Any:
        self.log.append(("get_current", user_id))
        return self.current

    async def update_status(self, subscription_id: int, status: Any) -> None:
        self.log.append(("update_status", (subscription_id, status)))

    async def create(self, subscription: Any, user_id: int) -> Any:
        self.log.append(("create", subscription))
        return SimpleNamespace(id=self.new_id)

    async def update(self, subscription: Any) -> Any:
        self.log.append(("sub_update", subscription))
        return subscription


class FakeUserDao:
    def __init__(self, log: Log) -> None:
        self.log = log

    async def update(self, user: Any) -> None:
        self.log.append(("user_update", user.purchase_discount))

    async def set_trial_available(self, user_id: int, value: bool) -> None:
        self.log.append(("set_trial_available", value))


class FakeRemnawave:
    def __init__(self, log: Log, fail: bool = False) -> None:
        self.log = log
        self.fail = fail

    async def update_user(self, user, uuid, plan=None, subscription=None, reset_traffic=False):
        self.log.append(
            ("update_user", {"plan": plan, "subscription": subscription, "reset_traffic": reset_traffic, "uuid": uuid})
        )
        if self.fail:
            raise RuntimeError("панель недоступна")
        expire = subscription.expire_at if subscription is not None else NOW + timedelta(days=plan.duration)
        # Панель отвечает своим сроком — строка обязана взять именно его.
        return SimpleNamespace(
            uuid=uuid, status="ACTIVE", expire_at=expire + timedelta(seconds=1),
            subscription_url="https://example.test/s/new",
        )

    async def create_user(self, user, plan=None):
        self.log.append(("create_user", plan))
        return SimpleNamespace(
            uuid=uuid.uuid4(), status="ACTIVE", expire_at=NOW + timedelta(days=plan.duration),
            subscription_url="https://example.test/s/created",
        )


def plan_snapshot(plan_id: int = NEW_PLAN, duration: int = 30) -> PlanSnapshotDto:
    return PlanSnapshotDto(
        id=plan_id, name=f"P{plan_id}", type=PlanType.BOTH,
        traffic_limit_strategy=TrafficLimitStrategy.NO_RESET,
        traffic_limit=100, device_limit=3, duration=duration,
    )


@dataclass
class World:
    log: Log = field(default_factory=Log)
    days_left: float = 29
    fresh_days_left: Optional[float] = None  # срок под замком, если отличается от DTO
    row_id: int = 5
    dto_id: int = 5
    trial: bool = False
    purchase_type: Any = PurchaseType.CHANGE
    panel_fails: bool = False
    discount: int = 20

    def build(self):
        remna = uuid.UUID("00000000-0000-0000-0000-00000000abcd")
        expire_dto = NOW + timedelta(days=self.days_left)
        fresh = self.fresh_days_left if self.fresh_days_left is not None else self.days_left
        row = (self.row_id, NOW + timedelta(days=fresh), "ACTIVE", self.trial, OLD_PLAN, NOW - timedelta(days=60))
        current = SimpleNamespace(
            id=self.dto_id, is_trial=self.trial, user_remna_id=remna, url="https://example.test/s/1",
            expire_at=expire_dto, plan_snapshot=SimpleNamespace(id=OLD_PLAN, name="OLD"),
            status=SubscriptionStatus.ACTIVE,
        )
        self.session = FakeSession(self.log, row)
        self.sub_dao = FakeSubscriptionDao(self.log, current)
        self.interactor = purchase.PurchaseSubscription(
            FakeUow(self.log), FakeUserDao(self.log), self.sub_dao, FakeRemnawave(self.log, self.panel_fails),
            self.session,
        )
        self.user = SimpleNamespace(id=42, remna_name="rs_42", purchase_discount=self.discount, log="[USER:42]")
        self.transaction = SimpleNamespace(
            id=1, payment_id=uuid.uuid4(), purchase_type=self.purchase_type, plan_snapshot=plan_snapshot(),
            pricing=SimpleNamespace(original_amount=Decimal("240"), discount_percent=20, final_amount=Decimal("192")),
            currency=Currency.RUB,
        )
        self.data = purchase.PurchaseSubscriptionDto(self.user, self.transaction, current)
        return self


ACTOR = SimpleNamespace(log="[SYSTEM]")


@pytest.fixture
def spies(monkeypatch):
    """Загрузка/журнал/пауза — шпионы. Состояние строится из ПЕРЕДАННОЙ строки."""
    calls: dict[str, Any] = {"load": [], "record": [], "freeze": [], "applied": False,
                             "load_raises": None, "record_raises": None, "frozen": None}

    async def load_carry_state(session, *, user_id, subscription, now, exclude_payment_id, extra_plan_ids=(), extras=()):
        session.log.append(("load_state", subscription.expire_at))
        calls["load"].append({"subscription": subscription, "exclude": exclude_payment_id, "ids": tuple(extra_plan_ids)})
        if calls["load_raises"]:
            raise calls["load_raises"]
        # Один оплаченный слой 120/30 (4/день): бонус зависит от срока из строки.
        return carry.CarryState(
            status=subscription.status, is_unlimited=False, expire_at=subscription.expire_at,
            frozen_seconds=calls["frozen"], reserve_expire_at=None, refund_recent=False,
            old_plan_id=subscription.plan_id,
            layers=(carry.Layer("create", "p-old", 120 * DAY, "RUB", Fraction(480), Fraction(480), OLD_PLAN, 120),),
            prices={}, active_plan_ids=None,
        )

    async def carry_already_applied(session, payment_id):
        session.log.append(("already_applied?", payment_id))
        return calls["applied"]

    async def record_carryover(session, **kwargs):
        session.log.append(("record", kwargs))
        calls["record"].append(kwargs)
        if calls["record_raises"]:
            raise calls["record_raises"]

    async def close_freeze(session, user_id):
        session.log.append(("close_freeze", user_id))
        calls["freeze"].append(user_id)

    monkeypatch.setattr(carry, "load_carry_state", load_carry_state)
    monkeypatch.setattr(carry, "carry_already_applied", carry_already_applied)
    monkeypatch.setattr(carry, "record_carryover", record_carryover)
    monkeypatch.setattr(carry, "close_freeze", close_freeze)
    monkeypatch.setattr(carry, "load_config", lambda: {"enabled": True, "notify_admins": True})
    monkeypatch.setattr(patch_mod, "datetime_now", lambda: NOW)
    return calls


async def execute(world: World) -> None:
    await world.interactor._execute(ACTOR, world.data)


def call(log: Log, name: str) -> Any:
    return next(detail for n, detail in log if n == name)


# ── 1. Главный путь ──────────────────────────────────────────────────────────


async def test_change_single_panel_call_with_bonus_and_one_commit(spies):
    world = World(days_left=29).build()
    await execute(world)
    log = world.log

    # Замок раньше любого ORM-чтения; состояние — от строки из-под замка.
    assert log.index_of("lock") < log.index_of("load_state")
    assert "get_current" not in log.names()
    assert spies["load"][0]["exclude"] == world.transaction.payment_id
    assert NEW_PLAN in spies["load"][0]["ids"]

    # 29 дн. по 4/день = 116; новый 240/30 = 8/день → 14 дн. (цена дня — витрина, не 192).
    panel_calls = [d for n, d in log if n == "update_user"]
    assert len(panel_calls) == 1
    panel = panel_calls[0]
    assert panel["plan"] is None
    assert panel["reset_traffic"] is True
    assert panel["subscription"].status == SubscriptionStatus.ACTIVE
    assert panel["subscription"].expire_at == NOW + timedelta(days=30 + 14)
    assert panel["subscription"].traffic_limit == 100 and panel["subscription"].device_limit == 3

    assert call(log, "update_status") == (5, SubscriptionStatus.DELETED)
    assert log.index_of("update_status") < log.index_of("update_user") < log.index_of("create")
    created = call(log, "create")
    assert created.expire_at == NOW + timedelta(days=44, seconds=1), "срок строки — из ответа панели"

    record = spies["record"][0]
    assert record["subscription_id"] == 77 and record["old_subscription_id"] == 5
    assert record["payment_id"] == world.transaction.payment_id
    assert record["result"].bonus_days == 14 and record["result"].mode == "carry"
    assert record["source"] == "purchase"

    assert call(log, "user_update") == 0, "скидка на покупку погашена, как у базы"
    assert log.names().count("commit") == 1
    assert log.index_of("record") < log.index_of("commit")
    assert spies["freeze"] == [], "паузы нет — гасить нечего"

    outcome = patch_mod.outcome_for(world.interactor, world.transaction.payment_id)
    assert outcome["applied"] and outcome["result"].bonus_days == 14


async def test_pause_is_closed_before_commit(spies):
    spies["frozen"] = int(10 * DAY)
    world = World(days_left=-3).build()
    await execute(world)
    assert spies["freeze"] == [42]
    assert world.log.index_of("close_freeze") < world.log.index_of("commit")
    # Панель получает ACTIVE — пауза снята вместе со сменой.
    assert call(world.log, "update_user")["subscription"].status == SubscriptionStatus.ACTIVE


# ── 3–4. Всё прочее — база ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "world_kwargs, marker",
    [
        ({"trial": True}, "update_user"),
        ({"purchase_type": PurchaseType.RENEW}, "sub_update"),
        ({"purchase_type": PurchaseType.NEW}, "create_user"),
    ],
)
async def test_trial_new_renew_go_to_untouched_base(spies, world_kwargs, marker):
    world = World(**world_kwargs).build()
    if world_kwargs.get("purchase_type") == PurchaseType.NEW:
        world.data = purchase.PurchaseSubscriptionDto(world.user, world.transaction, None)
    await execute(world)
    names = world.log.names()
    assert "lock" not in names and "load_state" not in names and "record" not in names
    assert marker in names
    if world_kwargs.get("trial"):
        assert call(world.log, "update_user")["plan"] is not None, "триал — ветка базы с plan="


async def test_switch_off_returns_base_behaviour(spies, monkeypatch):
    monkeypatch.setattr(carry, "load_config", lambda: {"enabled": False, "notify_admins": True})
    world = World().build()
    await execute(world)
    names = world.log.names()
    assert "lock" not in names and "record" not in names
    assert call(world.log, "update_user")["plan"] is not None
    assert patch_mod.outcome_for(world.interactor, world.transaction.payment_id) is None


# ── 5–9. Отказы и гонки ─────────────────────────────────────────────────────


async def test_already_recorded_payment_does_nothing(spies):
    spies["applied"] = True
    world = World().build()
    await execute(world)
    names = world.log.names()
    assert "update_user" not in names and "create" not in names and "update_status" not in names
    assert "commit" not in names
    assert "rollback" in names, "замок отпускаем сразу"


async def test_panel_failure_propagates_without_commit_or_journal(spies):
    world = World(panel_fails=True).build()
    with pytest.raises(RuntimeError):
        await execute(world)
    names = world.log.names()
    assert "commit" not in names
    assert "record" not in names
    assert "uow_rollback" in names


async def test_journal_failure_does_not_block_paid_grant(spies):
    spies["record_raises"] = RuntimeError('relation "plan_change_carryovers" does not exist')
    world = World().build()
    await execute(world)
    names = world.log.names()
    assert "commit" in names
    assert "savepoint_rollback" in names, "запись журнала — в SAVEPOINT"
    outcome = patch_mod.outcome_for(world.interactor, world.transaction.payment_id)
    assert "plan_change_carryovers" in outcome["record_error"]


async def test_state_failure_grants_base_term_and_flags_owner(spies):
    spies["load_raises"] = RuntimeError("timeout")
    world = World(days_left=29).build()
    await execute(world)
    panel = call(world.log, "update_user")
    assert panel["subscription"].expire_at == NOW + timedelta(days=30), "бонус 0 — как у базы"
    assert "commit" in world.log.names()
    outcome = patch_mod.outcome_for(world.interactor, world.transaction.payment_id)
    assert outcome["load_error"] and outcome["result"].mode == "failed"
    assert outcome["result"].remaining_days == 29
    # Пауза неизвестна — гасим на всякий случай, иначе крон перетрёт срок.
    assert spies["freeze"] == [42]


async def test_other_row_under_lock_is_used(spies):
    world = World(row_id=6, dto_id=5).build()
    await execute(world)
    assert "get_current" in world.log.names()
    assert spies["load"][0]["subscription"].id == 6
    assert call(world.log, "update_status") == (6, SubscriptionStatus.DELETED)


async def test_stale_identity_map_expire_is_not_trusted(spies):
    """DTO говорит 29 дн., а под замком (сосед только что продлил) — 119: считаем от 119."""
    world = World(days_left=29, fresh_days_left=119).build()
    await execute(world)
    assert spies["load"][0]["subscription"].expire_at == NOW + timedelta(days=119)
    # 119 дн. по 4/день = 476 → /8 = 59.
    assert spies["record"][0]["result"].bonus_days == 59


# ── 10. Правка на месте и находит свои имена ────────────────────────────────


def test_patch_applied_and_names_resolve():
    from overlay_patches import expect_names_resolve

    assert getattr(purchase.PurchaseSubscription._execute, "_overlay_wrapped", False)
    assert carry.patch_applied()
    expect_names_resolve(patch_mod.TARGET_MODULE, "перенос остатка")
    expect_names_resolve("src.application.use_cases.gateways.commands.payment", "шлюз")
    assert patch_mod.apply() == "уже обёрнута"


def test_draft_is_active_even_for_disabled_row():
    current = SimpleNamespace(user_remna_id=uuid.uuid4(), url="u", status=SubscriptionStatus.DISABLED)
    draft = patch_mod._draft(current, plan_snapshot(), NOW)
    assert draft.status == SubscriptionStatus.ACTIVE and draft.is_trial is False


# ── 12–13. Отчёт владельцу и возврат ────────────────────────────────────────


class FakeNotifier:
    def __init__(self) -> None:
        self.admin: list[Any] = []

    async def notify_admins(self, payload: Any, roles: Any = None) -> None:
        self.admin.append(payload)

    async def notify_user(self, *args: Any, **kwargs: Any) -> None:
        return None


def raw_texts(notifier: FakeNotifier) -> list[str]:
    return [p.i18n_kwargs["content"] for p in notifier.admin if p.i18n_key == "raw-message"]


def result(**over: Any) -> Any:
    base = dict(
        mode="carry", remaining_seconds=29 * DAY, remaining_days=29, value=Fraction(116),
        new_day_price=Fraction(8), bonus_days=14, bonus_seconds=0, lost_days=0, capped=False,
    )
    base.update(over)
    return carry.CarryResult(**base)


async def after_change(monkeypatch, outcome: Optional[dict], *, notify=True, enabled=True):
    monkeypatch.setattr(carry, "load_config", lambda: {"enabled": enabled, "notify_admins": notify})
    pid = uuid.uuid4()
    purchase_subscription = SimpleNamespace()
    if outcome is not None:
        outcome = {"payment_id": pid, **outcome}
    setattr(purchase_subscription, patch_mod.OUTCOME_ATTR, outcome)
    harness = SimpleNamespace(purchase_subscription=purchase_subscription, notifier=FakeNotifier())
    before = SimpleNamespace(is_trial=False, expire_at=NOW + timedelta(days=29), is_unlimited=False,
                             plan_snapshot=SimpleNamespace(name="OLD"))
    transaction = SimpleNamespace(payment_id=pid, plan_snapshot=plan_snapshot())
    user = SimpleNamespace(log="[USER:42]")
    await gateway_mod._after_change(harness, user, transaction, before)
    return harness.notifier


async def test_owner_notified_about_each_carry(monkeypatch):
    notifier = await after_change(monkeypatch, {"applied": True, "result": result(), "old_plan_name": "OLD"})
    texts = raw_texts(notifier)
    assert len(texts) == 1 and "+14 дн." in texts[0] and "29 дн." in texts[0]
    assert notifier.admin[0].delete_after is None


async def test_notify_admins_off_silences_only_routine(monkeypatch):
    assert raw_texts(await after_change(monkeypatch, {"applied": True, "result": result()}, notify=False)) == []
    failed = carry.failed_result(NOW + timedelta(days=12), NOW)
    texts = raw_texts(
        await after_change(monkeypatch, {"applied": True, "result": failed, "load_error": "timeout"}, notify=False)
    )
    assert len(texts) == 1 and "не пересчитан" in texts[0] and "Продлить" in texts[0]


async def test_process_without_patch_alerts_owner(monkeypatch):
    """Правка покупки не встала в этом процессе (непересобранный воркер) — алерт в момент вреда."""
    texts = raw_texts(await after_change(monkeypatch, None))
    assert len(texts) == 1 and "не применился" in texts[0]
    assert raw_texts(await after_change(monkeypatch, None, enabled=False)) == []


async def test_journal_failure_is_reported(monkeypatch):
    texts = raw_texts(
        await after_change(monkeypatch, {"applied": True, "result": result(), "record_error": "no table"})
    )
    assert len(texts) == 1 and "журнал не записан" in texts[0]


@dataclass
class RefundTx:
    payment_id: Any
    status: Any
    gateway_type: Any = PaymentGatewayType.YOOMONEY
    user_id: int = 42
    pricing: Any = field(default_factory=lambda: SimpleNamespace(final_amount=1, original_amount=1, discount_percent=0))
    currency: Any = Currency.RUB


class RefundDao:
    def __init__(self, tx: RefundTx) -> None:
        self.tx = tx

    async def get_by_payment_id(self, payment_id):
        return self.tx

    async def transition_status(self, payment_id, new, allowed):
        if self.tx.status not in allowed:
            return None
        self.tx.status = new
        return self.tx


class RefundUow:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def commit(self):
        return None


async def refund(monkeypatch, status, carries):
    tx = RefundTx(payment_id=uuid.uuid4(), status=status)

    async def transaction_status(session, payment_id):
        return tx.status.value

    async def carries_by_source_payment(session, payment_id):
        return carries

    monkeypatch.setattr(carry, "transaction_status", transaction_status)
    monkeypatch.setattr(carry, "carries_by_source_payment", carries_by_source_payment)

    class Users:
        async def get_by_id(self, user_id):
            return SimpleNamespace(id=user_id, log="[USER:42]", remna_name="rs_42", telegram_id=None,
                                   username=None, name="x", email=None)

    harness = SimpleNamespace(
        uow=RefundUow(), transaction_dao=RefundDao(tx), user_dao=Users(), notifier=FakeNotifier(),
        session=object(),
    )
    data = SimpleNamespace(payment_id=tx.payment_id, new_transaction_status=TransactionStatus.REFUNDED,
                           gateway_type=PaymentGatewayType.YOOMONEY)
    await payment.ProcessPayment._execute(harness, None, data)
    return harness.notifier


CARRY_ROW = [{"subscription_id": 77, "added_days": 14, "created_at": NOW, "payment_id": None}]


async def test_refund_of_carry_source_alerts_once(monkeypatch):
    texts = raw_texts(await refund(monkeypatch, TransactionStatus.COMPLETED, CARRY_ROW))
    assert len(texts) == 1
    assert "Подписка #77: +14 дн." in texts[0]


async def test_refund_transition_not_matched_no_alert(monkeypatch):
    """База не провела переход (счёт не COMPLETED) — молча вышла; алерта тоже нет."""
    assert raw_texts(await refund(monkeypatch, TransactionStatus.CANCELED, CARRY_ROW)) == []
    assert raw_texts(await refund(monkeypatch, TransactionStatus.REFUNDED, CARRY_ROW)) == []


async def test_refund_of_unrelated_payment_no_alert(monkeypatch):
    assert raw_texts(await refund(monkeypatch, TransactionStatus.COMPLETED, [])) == []
