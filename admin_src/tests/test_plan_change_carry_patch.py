"""Перенос остатка при смене тарифа: оркестровка зачисления на подделках.

ЗДЕСЬ ЖЕ проверяется стык с докупкой устройства: её стоимость приходит слоями
`extras`, а сами места гасятся в ТОЙ ЖЕ транзакции — перенос, записанный без гашения,
посчитал бы их второй раз при следующей смене.

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
  * пауза гасится до commit; NEW / RENEW своего тарифа / триал / выключатель — база;
  * RENEW ЧУЖОГО тарифа (счёт создан до смены, оплачен после) — не база: смена с
    пересчётом по стоимости, иначе дни дешёвого тарифа стали бы днями дорогого;
  * сбой проверки журнала или расчёта не срывает оплаченную смену;
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
    def __init__(self, row: Optional[tuple], rows: Optional[list] = None) -> None:
        self.row = row
        self.rows = rows if rows is not None else ([row] if row is not None else [])

    def first(self) -> Optional[tuple]:
        return self.row

    def scalar(self) -> Any:
        return self.row[0] if self.row else None

    def all(self) -> list:
        # Докупка устройства читает и гасит слоты пачкой (RETURNING id).
        return self.rows


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
    def __init__(self, log: Log, row: tuple, frozen_seconds: Optional[int] = None) -> None:
        self.log = log
        self.row = row
        self.frozen_seconds = frozen_seconds

    async def execute(self, stmt: Any, params: Any = None) -> FakeResult:
        sql = str(stmt)
        if "FOR UPDATE" in sql:
            self.log.append(("lock", dict(params)))
            return FakeResult(self.row)
        self.log.append(("sql", sql))
        if "FROM subscription_freezes" in sql and self.frozen_seconds is not None:
            return FakeResult((self.frozen_seconds,))
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
    tx_plan_id: int = NEW_PLAN
    row_frozen_seconds: Optional[int] = None

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
        self.session = FakeSession(self.log, row, self.row_frozen_seconds)
        self.sub_dao = FakeSubscriptionDao(self.log, current)
        self.interactor = purchase.PurchaseSubscription(
            FakeUow(self.log), FakeUserDao(self.log), self.sub_dao, FakeRemnawave(self.log, self.panel_fails),
            self.session,
        )
        self.user = SimpleNamespace(id=42, remna_name="rs_42", purchase_discount=self.discount, log="[USER:42]")
        self.transaction = SimpleNamespace(
            id=1, payment_id=uuid.uuid4(), purchase_type=self.purchase_type,
            plan_snapshot=plan_snapshot(self.tx_plan_id),
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

    # Замок раньше любого ORM-чтения; состояние — от строки из-под замка. Ожидание
    # замка ограничено lock_timeout только на этот запрос.
    assert log.index_of("lock") < log.index_of("load_state")
    sqls = [d for n, d in log if n == "sql"]
    assert any("SET LOCAL lock_timeout = '30000ms'" in q for q in sqls)
    assert any("SET LOCAL lock_timeout TO DEFAULT" in q for q in sqls)
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


async def test_renew_of_same_plan_is_base_under_lock(spies):
    world = World(purchase_type=PurchaseType.RENEW, tx_plan_id=OLD_PLAN).build()
    await execute(world)
    names = world.log.names()
    assert names.index("lock") < names.index("sub_update"), "база продлевает под нашим замком"
    assert "load_state" not in names and "record" not in names and "update_status" not in names
    assert patch_mod.outcome_for(world.interactor, world.transaction.payment_id) is None


async def test_renew_of_other_plan_after_change_is_converted_by_value(spies):
    """BLOCKER скептика: счёт RENEW дорогого тарифа создан ДО смены на дешёвый, оплачен ПОСЛЕ.

    База поставила бы снимок дорогого тарифа поверх срока, набранного переносом в дни
    дешёвого (дни дешёвого → дни дорогого, цикл повторяем). Теперь это смена с пересчётом
    по стоимости: 29 дн. по 4/день = 116 → тариф счёта 240/30 = 8/день → +14 дн.
    """
    world = World(purchase_type=PurchaseType.RENEW, tx_plan_id=NEW_PLAN).build()
    await execute(world)
    names = world.log.names()
    assert "sub_update" not in names, "базовое продление не выполнялось"
    panel = call(world.log, "update_user")
    assert panel["plan"] is None
    assert panel["subscription"].expire_at == NOW + timedelta(days=30 + 14)
    assert panel["subscription"].plan_snapshot.id == NEW_PLAN
    assert call(world.log, "update_status") == (5, SubscriptionStatus.DELETED)
    assert spies["record"][0]["payment_id"] == world.transaction.payment_id
    assert spies["load"][0]["exclude"] == world.transaction.payment_id, "счёт RENEW не слой старой строки"
    outcome = patch_mod.outcome_for(world.interactor, world.transaction.payment_id)
    assert outcome["applied"] and outcome["rerouted_renew"] is True


async def test_renew_other_plan_on_trial_or_switch_off_is_base(spies, monkeypatch):
    world = World(purchase_type=PurchaseType.RENEW, tx_plan_id=NEW_PLAN, trial=True).build()
    await execute(world)
    assert "load_state" not in world.log.names()
    monkeypatch.setattr(carry, "load_config", lambda: {"enabled": False, "notify_admins": True})
    world = World(purchase_type=PurchaseType.RENEW, tx_plan_id=NEW_PLAN).build()
    await execute(world)
    names = world.log.names()
    assert "lock" not in names and "sub_update" in names


async def test_idempotency_check_failure_does_not_fail_paid_change(spies, monkeypatch):
    """Скептик: таблицы журнала нет — проверка «уже переносили?» падает. Смена оплачена:
    выдаём, коммитим, флаг для алерта владельцу."""
    async def boom(session, payment_id):
        raise RuntimeError('relation "plan_change_carryovers" does not exist')

    monkeypatch.setattr(carry, "carry_already_applied", boom)
    world = World().build()
    await execute(world)
    names = world.log.names()
    assert "update_user" in names and "commit" in names
    assert "savepoint_rollback" in names
    outcome = patch_mod.outcome_for(world.interactor, world.transaction.payment_id)
    assert "plan_change_carryovers" in outcome["idempotency_error"]


async def test_compute_failure_grants_base_term(spies, monkeypatch):
    def broken(*args, **kwargs):
        raise ZeroDivisionError("bad price")

    monkeypatch.setattr(carry, "compute_carryover", broken)
    world = World(days_left=29).build()
    await execute(world)
    assert call(world.log, "update_user")["subscription"].expire_at == NOW + timedelta(days=30)
    assert "commit" in world.log.names()
    outcome = patch_mod.outcome_for(world.interactor, world.transaction.payment_id)
    assert outcome["result"].mode == "failed" and "bad price" in outcome["load_error"]


async def test_state_failure_reports_paused_remainder(spies):
    """Состояние не загрузилось, но пауза читается отдельно: алерт называет её остаток."""
    spies["load_raises"] = RuntimeError("timeout")
    world = World(days_left=-3, row_frozen_seconds=17 * DAY).build()
    await execute(world)
    outcome = patch_mod.outcome_for(world.interactor, world.transaction.payment_id)
    assert outcome["result"].mode == "failed" and outcome["result"].remaining_days == 17


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


async def after_change(monkeypatch, outcome: Optional[dict], *, notify=True, enabled=True,
                       purchase_type=PurchaseType.CHANGE, days_before: float = 29):
    monkeypatch.setattr(carry, "load_config", lambda: {"enabled": enabled, "notify_admins": notify})
    pid = uuid.uuid4()
    purchase_subscription = SimpleNamespace()
    if outcome is not None:
        outcome = {"payment_id": pid, **outcome}
    setattr(purchase_subscription, patch_mod.OUTCOME_ATTR, outcome)
    harness = SimpleNamespace(purchase_subscription=purchase_subscription, notifier=FakeNotifier())
    before = SimpleNamespace(is_trial=False, expire_at=datetime.now(timezone.utc) + timedelta(days=days_before),
                             is_unlimited=False, plan_snapshot=SimpleNamespace(name="OLD"))
    transaction = SimpleNamespace(payment_id=pid, plan_snapshot=plan_snapshot(), purchase_type=purchase_type)
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


async def test_routine_zero_change_is_not_reported(monkeypatch):
    """Смена истёкшей подписки «+0 дн.» — рутина, владельцу не пишем."""
    zero = result(mode="none", remaining_seconds=0, remaining_days=0, value=Fraction(0), new_day_price=None,
                  bonus_days=0)
    assert raw_texts(await after_change(monkeypatch, {"applied": True, "result": zero})) == []
    small = result(bonus_days=0)
    assert raw_texts(await after_change(monkeypatch, {"applied": True, "result": small})) == []
    lost = result(bonus_days=0, lost_days=5, lost_reason="old_price")
    texts = raw_texts(await after_change(monkeypatch, {"applied": True, "result": lost}))
    assert len(texts) == 1 and "цена старых дней неизвестна" in texts[0]
    cap = result(bonus_days=3650, lost_days=964, capped=True, lost_reason="cap")
    assert "предел переноса" in raw_texts(await after_change(monkeypatch, {"applied": True, "result": cap}))[0]


async def test_not_applied_alert_only_when_days_burn_and_only_for_change(monkeypatch):
    assert raw_texts(await after_change(monkeypatch, None, days_before=-2)) == []
    assert raw_texts(await after_change(monkeypatch, None, purchase_type=PurchaseType.RENEW)) == []
    assert len(raw_texts(await after_change(monkeypatch, None))) == 1


async def test_idempotency_error_is_reported(monkeypatch):
    texts = raw_texts(
        await after_change(monkeypatch, {"applied": True, "result": result(), "idempotency_error": "no table"})
    )
    assert len(texts) == 1 and "no table" in texts[0]


def test_owner_report_goes_after_purchase_event():
    """Отчёт — после выдачи и события покупки, не перед ними."""
    import ast
    import inspect
    import textwrap

    source = textwrap.dedent(inspect.getsource(gateway_mod.apply))
    tree = ast.parse(source)
    handler = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "ProcessPayment_handle_success")
    body = ast.get_source_segment(source, handler)
    assert body.index("self.event_publisher.publish(event)") < body.index("_after_change(")


def test_gateway_does_not_import_purchase_patch_at_module_level():
    """Импорт правки покупки из правки шлюза — только ленивый, внутри функции."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(gateway_mod))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            assert "plan_change_carryover" not in (node.module or ""), "модульный импорт plan_change_carryover"


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


# ── стык с докупкой устройства ───────────────────────────────────────────────


async def test_bought_device_slots_burn_in_the_same_transaction(spies):
    """Места сгорают ДО commit выдачи.

    Иначе перенос записан, а места живы: следующая смена тарифа посчитала бы их
    стоимость второй раз — деньги, которые магазин уже отдал днями.
    """
    world = World(days_left=29).build()
    await execute(world)
    names = world.log.names()
    burn = [
        q
        for n, q in world.log
        if n == "sql" and "extra_device_slots" in str(q) and "'burned'" in str(q)
    ]
    assert burn, "слоты докупки не погашены при смене тарифа"
    assert "change" in burn[0]
    assert names.index("sql") < names.index("commit")
    # Гасим слоты СТАРОЙ строки: новую только что создали, её мест ещё нет.
    burn_params = [p for n, p in world.log if n == "lock"]
    assert burn_params, "замок не брался"


async def test_device_value_comes_in_as_parallel_layers(monkeypatch):
    """Стоимость мест приходит слоями `extras` — той же формы, что ждёт расчёт."""
    extra = importlib.import_module("src.infrastructure.services.overlay_extra_device")
    orders = [
        {
            "amount": Decimal("90"),
            "currency": "RUB",
            "cov_start": NOW - timedelta(days=20),
            "period_end": NOW + timedelta(days=10),
            "slot_id": 1,
        }
    ]

    async def load_orders(session, subscription_id):
        assert subscription_id == 5
        return orders

    monkeypatch.setattr(extra, "load_carry_orders", load_orders)

    class Session:
        log = Log()

        async def execute(self, stmt, params=None):
            # Пауза: точка отсчёта «уже прожито». Здесь её нет — считаем до «сейчас».
            return FakeResult(None)

    layers = await patch_mod._device_extras(Session(), 42, 5, NOW)
    assert len(layers) == 1
    layer = layers[0]
    assert isinstance(layer, carry.ParallelLayer)
    assert layer.currency == "RUB"
    # 90 ₽ за 30 суток, прожито 20 → остаётся треть.
    assert Fraction(layer.amount) * layer.remaining_seconds / layer.total_seconds == Fraction(30)


async def test_device_value_is_used_from_the_pause_moment(monkeypatch):
    """На паузе срок стоит: прожитым считаем до момента паузы, а не до «сейчас»."""
    extra = importlib.import_module("src.infrastructure.services.overlay_extra_device")
    frozen_at = NOW - timedelta(days=10)

    async def load_orders(session, subscription_id):
        return [
            {
                "amount": Decimal("90"),
                "currency": "RUB",
                "cov_start": NOW - timedelta(days=20),
                "period_end": NOW + timedelta(days=10),
                "slot_id": 1,
            }
        ]

    monkeypatch.setattr(extra, "load_carry_orders", load_orders)

    class Session:
        log = Log()

        async def execute(self, stmt, params=None):
            return FakeResult((frozen_at,))

    layers = await patch_mod._device_extras(Session(), 42, 5, NOW)
    assert Fraction(layers[0].amount) * layers[0].remaining_seconds / layers[0].total_seconds == Fraction(60)


def test_extras_are_passed_into_the_calculation():
    """Загруженные слои обязаны уехать в load_carry_state, иначе стоимость пропадёт."""
    import ast
    import inspect
    import textwrap

    source = textwrap.dedent(inspect.getsource(patch_mod.change_with_carryover))
    tree = ast.parse(source)
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "load_carry_state"
    ]
    assert calls, "load_carry_state больше не вызывается"
    assert any(kw.arg == "extras" for kw in calls[0].keywords), "extras не передаются в расчёт"
