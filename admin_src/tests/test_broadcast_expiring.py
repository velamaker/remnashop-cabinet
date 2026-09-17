"""Рассылка «Истекают скоро» (TG_EXPIRING) через базовый конвейер.

ЧТО ЗАПИРАЕМ. Сегмент едет по чужим рельсам — аудитория PLAN с условным
plan_id −(1000+N), — и цена ошибки здесь «сообщение не тем людям»:
  * настоящие и служебные id тарифов правкой не перехватываются, а условный
    разбирается ровно в N;
  * после правки PLAN/−1007 берёт получателей из выборки «истекают за 7 дней»,
    а PLAN/5 и любая другая аудитория уходят в код базы;
  * повторное применение правки ничего не делает;
  * запуск: без дней — 400, правка не встала — 409 и НИ ОДНОГО запуска,
    соседний канал не отправляется раньше отказа;
  * счётчик сегмента отдаётся, только если правка встала (по нему кабинет
    решает, показывать ли пункт), история узнаёт такую рассылку по payload.

Сверку исходника базы (expect_source) и разрешимость имён проверяет общий
CI-гейт «Правки overlay применяются».

Запуск — внутри образа бота, как остальные тесты рядом (см. ci.yml).
"""

import importlib
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException

segment = importlib.import_module("src.infrastructure.services.overlay_expiring_segment")
audience_mod = importlib.import_module("src.application.use_cases.broadcast.queries.audience")
patch = importlib.import_module("overlay_patches.broadcast_expiring")
broadcasts = importlib.import_module("src.web.endpoints.admin.broadcasts")

from src.application.dto import MessagePayloadDto  # noqa: E402
from src.core.enums import BroadcastAudience, BroadcastStatus  # noqa: E402

Users = audience_mod.GetBroadcastAudienceUsers
UsersDto = audience_mod.GetBroadcastAudienceUsersDto
ACTOR = SimpleNamespace(log="[SYSTEM:test]")


@pytest.fixture(autouse=True)
def patched() -> None:
    # В образе правку ставит хук на импорт; вне его — применяем сами.
    patch.apply()
    assert Users._overlay_expiring is True


# ── условный plan_id ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("days", [1, 3, 7, 14, 30, 60])
def test_plan_id_round_trip(days: int):
    plan_id = segment.encode_expiring_plan_id(days)
    assert plan_id == -(1000 + days)
    assert segment.days_from_plan_id(plan_id) == days


@pytest.mark.parametrize("plan_id", [5, 1, 0, -1, -2, -3, -1000, -1366, None, True, "−1007"])
def test_real_and_service_ids_are_not_intercepted(plan_id: Any):
    assert segment.days_from_plan_id(plan_id) is None


@pytest.mark.parametrize("days", [0, 61, -7, None, True, 7.0])
def test_form_limits(days: Any):
    with pytest.raises(ValueError):
        segment.encode_expiring_plan_id(days)


# ── правка аудитории ─────────────────────────────────────────────────────────


class FakeUserDao:
    def __init__(self) -> None:
        self.session = object()
        self.calls: list[tuple] = []

    async def get_by_ids(self, ids: list[int]) -> list[str]:
        self.calls.append(("get_by_ids", tuple(ids)))
        return [f"user{i}" for i in ids]

    async def get_active_by_plan(self, plan_id: int) -> list[str]:
        self.calls.append(("get_active_by_plan", plan_id))
        return ["plan-user"]

    async def get_with_active_subscription(self) -> list[str]:
        self.calls.append(("get_with_active_subscription",))
        return ["subscribed-user"]


def interactor(dao: FakeUserDao) -> Any:
    obj = object.__new__(Users)
    obj.user_dao = dao
    return obj


@pytest.mark.asyncio
async def test_sentinel_plan_takes_expiring_users(monkeypatch: pytest.MonkeyPatch):
    seen: list[tuple] = []

    async def fake_ids(session: Any, days: int) -> list[int]:
        seen.append((session, days))
        return [3, 4]

    monkeypatch.setattr(segment, "expiring_user_ids", fake_ids)
    dao = FakeUserDao()

    users = await interactor(dao)._execute(ACTOR, UsersDto(BroadcastAudience.PLAN, -1007))

    assert users == ["user3", "user4"]
    assert seen == [(dao.session, 7)]
    assert dao.calls == [("get_by_ids", (3, 4))]


@pytest.mark.asyncio
async def test_real_plan_and_other_audiences_go_to_base(monkeypatch: pytest.MonkeyPatch):
    async def must_not_run(session: Any, days: int) -> list[int]:
        raise AssertionError("выборка «истекают скоро» не для этой аудитории")

    monkeypatch.setattr(segment, "expiring_user_ids", must_not_run)
    dao = FakeUserDao()

    assert await interactor(dao)._execute(ACTOR, UsersDto(BroadcastAudience.PLAN, 5)) == ["plan-user"]
    subscribed = UsersDto(BroadcastAudience.SUBSCRIBED, -1007)
    assert await interactor(dao)._execute(ACTOR, subscribed) == ["subscribed-user"]
    assert dao.calls == [("get_active_by_plan", 5), ("get_with_active_subscription",)]


def test_second_apply_changes_nothing():
    before = Users._execute
    assert patch.apply() == "уже применено"
    assert Users._execute is before
    assert segment.expiring_patch_ready() is True


# ── запуск из кабинета ───────────────────────────────────────────────────────


class Recorder:
    def __init__(self) -> None:
        self.started: list[tuple[Any, Any]] = []
        self.created: list[Any] = []
        self.commits = 0

    # dispatcher
    async def start(self, broadcast: Any, plan_id: Any) -> None:
        self.started.append((broadcast, plan_id))

    # broadcast_dao
    async def create(self, broadcast: Any) -> None:
        self.created.append(broadcast)

    # uow
    async def __aenter__(self) -> "Recorder":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1


class CountSession:
    def __init__(self, expiring: int = 5) -> None:
        self.expiring = expiring
        self.days: list[int] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        sql = str(statement)
        value = 0
        if segment.EXPIRING_WHERE in sql:
            self.days.append(dict(params or {})["days"])
            value = self.expiring
        return SimpleNamespace(scalar_one=lambda: value)

    async def commit(self) -> None:
        return None


class Counts:
    async def count_active_by_plan(self, plan_id: int) -> int:
        return 3

    def __getattr__(self, name: str) -> Any:
        async def zero(*args: Any, **kwargs: Any) -> int:
            return 0

        return zero


async def create(body: dict, rec: Recorder, session: CountSession) -> dict:
    raw = broadcasts.create_broadcast.__dishka_orig_func__
    return await raw(
        body=broadcasts.CreateBroadcastBody(**body),
        _admin=SimpleNamespace(id=1),
        uow=rec,
        broadcast_dao=rec,
        dispatcher=rec,
        user_dao=Counts(),
        subscription_dao=Counts(),
        session=session,
    )


@pytest.mark.asyncio
async def test_expiring_and_plan_start_two_broadcasts():
    rec, session = Recorder(), CountSession(expiring=5)

    result = await create(
        {"text": "Продлите", "channels": ["TG_EXPIRING", "TG_PLAN"], "plan_id": 12, "expiring_days": 14},
        rec,
        session,
    )

    assert [plan_id for _, plan_id in rec.started] == [-1014, 12]
    expiring, _ = rec.started[0]
    assert expiring.audience == BroadcastAudience.PLAN
    assert expiring.total_count == 5 and session.days == [14]
    assert expiring.payload.delete_after is None
    assert expiring.payload.i18n_kwargs == {
        "content": "Продлите",
        "overlay_segment": "TG_EXPIRING",
        "overlay_days": 14,
    }
    assert len(result["telegram"]) == 2


@pytest.mark.asyncio
async def test_expiring_without_days_is_rejected_before_any_start():
    rec = Recorder()
    with pytest.raises(HTTPException) as err:
        await create({"text": "x", "channels": ["TG_ALL", "TG_EXPIRING"]}, rec, CountSession())
    assert err.value.status_code == 400
    assert rec.started == [] and rec.created == []


@pytest.mark.asyncio
async def test_expiring_refused_when_patch_is_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(Users, "_overlay_expiring", False)
    rec = Recorder()
    with pytest.raises(HTTPException) as err:
        await create(
            {"text": "x", "channels": ["TG_ALL", "TG_EXPIRING"], "expiring_days": 7}, rec, CountSession()
        )
    assert err.value.status_code == 409
    assert "Ничего не отправлено" in err.value.detail
    assert rec.started == [] and rec.created == []


@pytest.mark.asyncio
async def test_counter_key_only_when_patch_applied(monkeypatch: pytest.MonkeyPatch):
    raw = broadcasts.audience_counts.__dishka_orig_func__
    session = CountSession(expiring=9)

    counts = await raw(
        _admin=None, user_dao=Counts(), subscription_dao=Counts(), session=session, plan_id=None, expiring_days=14
    )
    assert counts["TG_EXPIRING"] == 9 and session.days == [14]

    monkeypatch.setattr(Users, "_overlay_expiring", False)
    counts = await raw(
        _admin=None, user_dao=Counts(), subscription_dao=Counts(), session=session, plan_id=None, expiring_days=None
    )
    assert "TG_EXPIRING" not in counts


def test_history_labels_expiring_broadcast():
    def item(kwargs: dict) -> Any:
        return SimpleNamespace(
            task_id=uuid4(),
            status=BroadcastStatus.COMPLETED,
            audience=BroadcastAudience.PLAN,
            total_count=4,
            success_count=4,
            failed_count=0,
            created_at=None,
            payload=MessagePayloadDto(i18n_key="raw-message", i18n_kwargs=kwargs, delete_after=None),
        )

    expiring = broadcasts._broadcast_to_dict(
        item({"content": "x", "overlay_segment": "TG_EXPIRING", "overlay_days": 14})
    )
    assert expiring["audience"] == "TG_EXPIRING" and expiring["expiring_days"] == 14

    plain = broadcasts._broadcast_to_dict(item({"content": "x"}))
    assert plain["audience"] == "PLAN" and "expiring_days" not in plain
