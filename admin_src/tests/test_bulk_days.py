"""Массовое «Добавить N дней»: кому, сверка с панелью и однократность.

ЧТО ЗАПИРАЕМ.
  * Правила «кому»: истёкшим, резерву, бессрочным, пробным и LIMITED без галочки
    дни не добавляются; пауза получает дни в остаток, а не через панель.
  * Расчёт срока тот же, что у кнопки «Продлить», и кнопка ведёт себя как раньше.
  * Разошлась наша копия с панелью — в панель не пишем ничего; почту, которая есть
    только в панели, полный PATCH не стирает.
  * Дни выдаются ровно один раз: повторный прогон, падение между записью target и
    итогом, сбой панели и остановка не дают ни второго PATCH, ни пересчёта срока.
  * Итог и финальная сверка доходят до админов.

Подделки — tests/bulk_fakes.py: мир в памяти с семантикой транзакции. SQL-гарантии
(захват строки, аренда, одна активная задача) проверяет migration-e2e на Postgres.

Запуск — внутри образа бота, как остальные тесты рядом (см. ci.yml).
"""

import importlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from bulk_fakes import (
    NOW,
    FakePanel,
    FakeStore,
    FakeSubscriptionDao,
    FakeUserDao,
    FakeWorld,
    Recorder,
)

bulk = importlib.import_module("src.infrastructure.services.overlay_bulk")
extend = importlib.import_module("src.infrastructure.services.overlay_extend")
task = importlib.import_module("src.infrastructure.taskiq.tasks.bulk_jobs")
subs_endpoint = importlib.import_module("src.web.endpoints.admin.subscriptions")

C = bulk.Category
DAY = timedelta(days=1)


def row(**over):
    base = {
        "role": "USER",
        "is_blocked": False,
        "sub_id": 1,
        "sub_status": "ACTIVE",
        "is_trial": False,
        "expire_at": NOW + 10 * DAY,
        "user_remna_id": UUID(int=1),
        "sub_updated_at": NOW - DAY,
        "frozen_remaining": None,
        "reserve_expire_at": None,
        "recent_payment": False,
    }
    base.update(over)
    return base


def classify(r, trial=False, limited=False):
    return bulk.classify_days(r, now=NOW, include_trial=trial, include_limited=limited)


# ── 1. Кому добавлять ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "overrides, trial, limited, expected",
    [
        # Оплатил во время резерва: срок уже настоящий, дальше резерва — дни положены.
        ({"reserve_expire_at": NOW + 2 * DAY, "expire_at": NOW + 30 * DAY}, False, False, C.APPLY),
        ({"reserve_expire_at": NOW + 2 * DAY, "expire_at": NOW + 2 * DAY + timedelta(minutes=30)}, False, False, C.RESERVE),
        ({"reserve_expire_at": NOW + 2 * DAY, "expire_at": NOW + 2 * DAY - timedelta(minutes=30)}, False, False, C.RESERVE),
        # Пауза: срок в прошлом не мешает — дни идут в остаток.
        ({"frozen_remaining": 3600, "expire_at": NOW - DAY}, False, False, C.APPLY_FROZEN),
        ({"frozen_remaining": 3600, "is_trial": True}, False, False, C.TRIAL),
        ({"frozen_remaining": 3600, "expire_at": NOW.replace(year=2099)}, False, False, C.UNLIMITED),
        ({"expire_at": NOW.replace(year=2099)}, False, False, C.UNLIMITED),
        ({"expire_at": NOW - timedelta(minutes=1)}, False, False, C.EXPIRED),
        ({"sub_status": "DISABLED"}, False, False, C.DISABLED),
        ({"sub_status": "LIMITED"}, False, False, C.LIMITED),
        ({"sub_status": "LIMITED"}, False, True, C.APPLY),
        ({"is_trial": True}, False, False, C.TRIAL),
        ({"is_trial": True}, True, False, C.APPLY),
        ({"is_blocked": True}, False, False, C.BLOCKED),
        ({"role": "OWNER"}, False, False, C.STAFF),
        ({"sub_id": None}, False, False, C.NO_SUBSCRIPTION),
        ({"sub_status": "DELETED"}, False, False, C.NO_SUBSCRIPTION),
        # recent_payment считает SQL: PENDING ≤30 мин или COMPLETED с updated_at ≤10 мин.
        ({"recent_payment": True}, False, False, C.RECENT_CHANGE),
        ({"sub_updated_at": NOW - timedelta(minutes=1)}, False, False, C.RECENT_CHANGE),
        ({"sub_updated_at": NOW - timedelta(hours=1)}, False, False, C.APPLY),
    ],
)
def test_classify_days(overrides, trial, limited, expected):
    assert classify(row(**overrides), trial, limited) == expected


def test_recent_payment_windows_are_in_sql():
    """Окна «недавней оплаты» живут в SQL: PENDING 30 минут, COMPLETED — 10 по updated_at."""
    sql = bulk.CLASSIFY_SQL
    assert "t.status::text = 'PENDING'" in sql and "interval '30 minutes'" in sql
    assert "GREATEST(t.created_at, t.updated_at) > now() - interval '10 minutes'" in sql


# ── 2. Расчёт срока — тот же, что у «Продлить» ────────────────────────────────


def test_compute_new_expire_matches_extend_button():
    future = NOW + 5 * DAY
    past = NOW - 5 * DAY
    assert extend.compute_new_expire(future, 3, NOW) == future + 3 * DAY
    assert extend.compute_new_expire(past, 3, NOW) == NOW + 3 * DAY
    assert extend.compute_new_expire(future, -2, NOW) == future - 2 * DAY
    assert extend.compute_new_expire(NOW + DAY, -5, NOW) == NOW


def _extend_func():
    import inspect

    return inspect.getclosurevars(subs_endpoint.extend_subscription).nonlocals["func"]


class _Session:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


async def test_extend_button_still_panel_first_and_502_on_panel_error():
    world = FakeWorld()
    panel = FakePanel()
    world.add_person(1, panel=panel)
    # Кнопка считает от настоящего «сейчас», а не от NOW подделок.
    real_expire = datetime.now(timezone.utc).replace(microsecond=0) + 5 * DAY
    world.subs[1].expire_at = real_expire
    panel.users[world.subs[1].user_remna_id].expire_at = real_expire
    sub_dao = FakeSubscriptionDao(world)
    session = _Session()
    func = _extend_func()

    panel.update_error = RuntimeError("panel down")
    with pytest.raises(HTTPException) as exc:
        await func(
            user_id=1,
            body=subs_endpoint.ExtendRequest(days=3),
            _admin=None,
            user_dao=FakeUserDao(world),
            subscription_dao=sub_dao,
            remnawave=panel,
            session=session,
        )
    assert exc.value.status_code == 502
    assert sub_dao.updates == [] and session.commits == 0

    panel.update_error = None
    sub_dao.updates.clear()
    # Возвращаемый DTO у карточки — настоящий SubscriptionDto; подменяем сериализацию.
    original = subs_endpoint._sub_to_dict
    subs_endpoint._sub_to_dict = lambda s: {"expire_at": s.expire_at.isoformat()}
    try:
        result = await func(
            user_id=1,
            body=subs_endpoint.ExtendRequest(days=3),
            _admin=None,
            user_dao=FakeUserDao(world),
            subscription_dao=sub_dao,
            remnawave=panel,
            session=session,
        )
    finally:
        subs_endpoint._sub_to_dict = original
    target = real_expire + 3 * DAY
    assert panel.updates[-1]["expire_at"] == target
    assert sub_dao.updates == [(1, target)]
    assert session.commits == 1
    assert result["success"] is True


# ── 3. Сверка с панелью ──────────────────────────────────────────────────────


def _panel_user(sub, **over):
    data = {
        "uuid": sub.user_remna_id,
        "status": "ACTIVE",
        "expire_at": sub.expire_at,
        "subscription_url": "https://x",
        "traffic_limit_bytes": 0,
        "hwid_device_limit": None,
        "traffic_limit_strategy": "NO_RESET",
        "tag": None,
        "active_internal_squads": [SimpleNamespace(uuid=s) for s in sub.internal_squads],
        "external_squad_uuid": None,
        "email": None,
        "telegram_id": None,
    }
    data.update(over)
    return SimpleNamespace(**data)


def _sub(**over):
    world = FakeWorld()
    world.add_person(1)
    sub = world.subs[1]
    for k, v in over.items():
        setattr(sub, k, v)
    return sub, world.users[1]


def test_panel_mismatch_normalizes_like_sync():
    sub, _ = _sub(device_limit=0, internal_squads=[UUID(int=2), UUID(int=3)])
    same = _panel_user(sub, hwid_device_limit=None, active_internal_squads=[SimpleNamespace(uuid=UUID(int=3)), SimpleNamespace(uuid=UUID(int=2))])
    assert bulk.panel_mismatch(sub, same) == []
    other_squad = _panel_user(sub, active_internal_squads=[SimpleNamespace(uuid=UUID(int=9)), SimpleNamespace(uuid=UUID(int=3))])
    assert bulk.panel_mismatch(sub, other_squad) == ["internal_squads"]
    drift = _panel_user(sub, expire_at=sub.expire_at + timedelta(seconds=3))
    assert bulk.panel_mismatch(sub, drift) == ["expire_at"]
    assert bulk.panel_mismatch(sub, drift, check_expire=False) == []


def test_protected_user_keeps_panel_only_email_without_touching_original():
    sub, user = _sub()
    user.email = None
    panel_user = _panel_user(sub, email="only@panel.test", telegram_id=user.telegram_id)
    protected = bulk.protected_user(user, panel_user)
    assert protected is not user
    assert protected.email == "only@panel.test"
    assert user.email is None
    assert "email" not in user.changed_data

    user.email = "ours@test"
    assert bulk.protected_user(user, _panel_user(sub, email="theirs@test")) is None
    assert bulk.protected_user(user, _panel_user(sub, email="OURS@test")) is not None


# ── Процессор: общая обвязка ─────────────────────────────────────────────────


class Env:
    def __init__(self):
        self.world = FakeWorld()
        self.panel = FakePanel()
        self.store = FakeStore(self.world)
        self.user_dao = FakeUserDao(self.world)
        self.sub_dao = FakeSubscriptionDao(self.world)
        self.rec = Recorder()

    def person(self, uid, **kw):
        overrides = kw.pop("panel_overrides", None)
        self.world.add_person(uid, panel=self.panel, panel_overrides=overrides, **kw)

    def job(self, uids, *, days=3, status="QUEUED", deferred=(), notify=None, **extra):
        items = [(u, "PENDING", None, u in deferred) for u in uids]
        params = {"days": days, "include_trial": False, "include_limited": False, "notify": notify}
        return self.world.add_job("days", params, items, status=status, **extra)

    async def run(self, job_id, token="w1", clock=None):
        return await task.process_days_job(
            job_id,
            store=self.store,
            user_dao=self.user_dao,
            subscription_dao=self.sub_dao,
            remnawave=self.panel,
            notify_admins=self.rec.notify_admins,
            kick=self.rec.kick,
            token=token,
            clock=clock or (lambda: NOW),
            sleep=self.rec.sleep,
        )


# ── 4–6. Расхождение с панелью ───────────────────────────────────────────────


async def test_panel_mismatch_fails_row_without_any_patch():
    env = Env()
    env.person(1, panel_overrides={"active_internal_squads": [SimpleNamespace(uuid=UUID(int=77))]})
    job = env.job([1])
    assert await env.run(job) == "COMPLETED"
    item = env.world.item(job, 1)
    assert (item["status"], item["category"]) == ("FAILED", "MISMATCH")
    assert "сквады" in item["error"]
    assert env.panel.updates == []
    assert "сквады" in bulk.item_to_dict(item, readonly=False)["reason"]


async def test_email_only_in_panel_survives_the_patch():
    env = Env()
    env.person(1, email=None, panel_overrides={"email": "kept@panel.test"})
    job = env.job([1])
    assert await env.run(job) == "COMPLETED"
    assert env.panel.updates[0]["email"] == "kept@panel.test"
    assert env.world.users[1].email is None


async def test_payment_already_in_panel_is_not_overwritten():
    env = Env()
    env.person(1)
    sub = env.world.subs[1]
    env.panel.users[sub.user_remna_id].expire_at = sub.expire_at + 30 * DAY
    job = env.job([1])
    await env.run(job)
    item = env.world.item(job, 1)
    assert (item["status"], item["category"], item["error"]) == ("FAILED", "MISMATCH", "срок")
    assert env.panel.updates == []


# ── 7. Перечитывание срока прямо перед PATCH ─────────────────────────────────


async def test_expire_changed_between_write_ahead_and_patch_defers_then_manual():
    env = Env()
    env.person(1)
    seen = []

    def moved(sub_id, state):
        seen.append(sub_id)
        return {**state, "expire_at": state["expire_at"] + 30 * DAY}

    env.store.sub_state_hook = moved
    job = env.job([1])
    assert await env.run(job) == "COMPLETED"
    item = env.world.item(job, 1)
    assert (item["status"], item["category"]) == ("FAILED", "MANUAL")
    assert item["target_expire_at"] is None
    assert len(seen) == 2  # первая попытка отложила, вторая — последняя
    assert env.panel.updates == []


# ── 8. Однократность ─────────────────────────────────────────────────────────


async def test_each_person_patched_exactly_once_and_rerun_patches_nobody():
    env = Env()
    for uid in (1, 2, 3):
        env.person(uid)
    job = env.job([1, 2, 3], days=3)
    assert await env.run(job) == "COMPLETED"
    assert len(env.panel.updates) == 3
    for uid in (1, 2, 3):
        item = env.world.item(job, uid)
        assert item["status"] == "DONE" and item["verify_note"] is None
        assert env.world.subs[uid].expire_at == item["target_expire_at"] == item["old_expire_at"] + 3 * DAY

    # Завершённую задачу повторный запуск не берёт вовсе.
    assert await env.run(job, token="w2") is None
    # И даже если задачу вернуть в работу, DONE-строки не трогаются.
    env.world.jobs[job].update(status="PROCESSING", lease_owner=None, lease_alive=False)
    assert await env.run(job, token="w3") == "COMPLETED"
    assert len(env.panel.updates) == 3
    assert env.world.jobs[job]["applied_count"] == 3


async def test_second_worker_cannot_take_a_live_lease():
    env = Env()
    env.person(1)
    job = env.job([1], status="PROCESSING", lease_owner="other", lease_alive=True)
    assert await env.run(job) is None
    assert env.panel.updates == [] and env.world.item(job, 1)["status"] == "PENDING"


# ── 9. Пауза ─────────────────────────────────────────────────────────────────


async def test_frozen_subscription_gets_days_into_remaining_without_panel():
    env = Env()
    env.person(1, frozen_remaining=5 * 86400, expire_in=-DAY)
    job = env.job([1], days=3)
    assert await env.run(job) == "COMPLETED"
    assert env.world.freezes[1]["remaining_seconds"] == 8 * 86400
    item = env.world.item(job, 1)
    assert (item["status"], item["category"], item["added_seconds"]) == ("DONE", "APPLY_FROZEN", 3 * 86400)
    assert env.panel.updates == [] and env.panel.other_calls == []
    assert "из них на паузе 1" in env.rec.admin_messages[-1]


# ── 10. Сверка при возобновлении ─────────────────────────────────────────────


def _stuck(env, job, uid, *, old, target, status="RUNNING"):
    env.world.items[(job, uid)].update(
        status=status,
        old_expire_at=old,
        target_expire_at=target,
        subscription_id=env.world.subs[uid].id,
        category="APPLY",
    )


async def test_recovery_panel_already_at_target_is_done_without_patch():
    env = Env()
    env.person(1)
    sub = env.world.subs[1]
    old, target = sub.expire_at, sub.expire_at + 3 * DAY
    env.panel.users[sub.user_remna_id].expire_at = target
    job = env.job([1], status="PROCESSING")
    _stuck(env, job, 1, old=old, target=target)
    assert await env.run(job) == "COMPLETED"
    assert env.panel.updates == []
    assert env.world.item(job, 1)["status"] == "DONE"
    # Наша база догнала панель — финальная сверка чиста.
    assert env.world.subs[1].expire_at == target
    assert env.world.item(job, 1)["verify_note"] is None


async def test_recovery_panel_at_old_retries_once_with_saved_target():
    env = Env()
    env.person(1)
    sub = env.world.subs[1]
    old, target = sub.expire_at, sub.expire_at + 3 * DAY
    job = env.job([1], status="PROCESSING")
    _stuck(env, job, 1, old=old, target=target, status="RETRY")
    later = NOW + timedelta(hours=5)  # «сейчас» сдвинулось — target пересчитываться не должен
    assert await env.run(job, clock=lambda: later) == "COMPLETED"
    assert [u["expire_at"] for u in env.panel.updates] == [target]
    assert env.world.item(job, 1)["status"] == "DONE"


@pytest.mark.parametrize(
    "panel_shift, target_shift, expected",
    [
        (timedelta(days=30), 3 * DAY, "MANUAL"),  # в панели третье значение
        (timedelta(0), -2 * DAY, "MANUAL"),  # target уже в прошлом
    ],
)
async def test_recovery_manual_cases_do_not_patch(panel_shift, target_shift, expected):
    env = Env()
    env.person(1, expire_in=DAY)
    sub = env.world.subs[1]
    old = sub.expire_at
    env.panel.users[sub.user_remna_id].expire_at = old + panel_shift
    job = env.job([1], status="PROCESSING")
    _stuck(env, job, 1, old=old, target=old + target_shift)
    await env.run(job)
    item = env.world.item(job, 1)
    assert (item["status"], item["category"]) == ("FAILED", expected)
    assert env.panel.updates == []


async def test_recovery_panel_lost_user_is_not_in_panel():
    env = Env()
    env.person(1)
    sub = env.world.subs[1]
    del env.panel.users[sub.user_remna_id]
    job = env.job([1], status="PROCESSING")
    _stuck(env, job, 1, old=sub.expire_at, target=sub.expire_at + DAY)
    await env.run(job)
    item = env.world.item(job, 1)
    assert (item["status"], item["category"]) == ("FAILED", "NOT_IN_PANEL")


def test_resolve_recovery_table():
    old = NOW + DAY
    target = old + 3 * DAY
    assert bulk.resolve_recovery(old, target, target + timedelta(seconds=1), NOW) == "DONE"
    assert bulk.resolve_recovery(old, target, old, NOW) == "RETRY"
    assert bulk.resolve_recovery(old, target, old + 10 * DAY, NOW) == "MANUAL"
    assert bulk.resolve_recovery(old, NOW - DAY, old, NOW) == "MANUAL"


# ── 11. Панель лежит ─────────────────────────────────────────────────────────


async def test_ten_panel_errors_in_a_row_pause_the_job():
    env = Env()
    for uid in range(1, 13):
        env.person(uid)
    env.panel.get_error = RuntimeError("connect timeout")
    job = env.job(list(range(1, 13)))
    assert await env.run(job) == "PAUSED"
    statuses = env.world.statuses(job)
    assert set(statuses.values()) == {"PENDING"}
    assert env.panel.updates == []
    reason = env.world.jobs[job]["pause_reason"]
    assert reason.startswith("Панель VPN не отвечает: RuntimeError: connect timeout")
    assert "на паузе" in env.rec.admin_messages[-1]

    # Панель ожила — «Продолжить» доводит дело, и каждый получает дни один раз.
    env.panel.get_error = None
    env.world.jobs[job].update(status="QUEUED", pause_reason=None)
    assert await env.run(job, token="w2") == "COMPLETED"
    assert len(env.panel.updates) == 12


async def test_patch_error_keeps_row_retry_and_never_done():
    env = Env()
    env.person(1)
    env.panel.update_error = RuntimeError("502 from panel")
    job = env.job([1])
    assert await env.run(job) == "PAUSED"
    item = env.world.item(job, 1)
    assert item["status"] == "RETRY" and item["target_expire_at"] is not None
    assert not any(it["status"] == "DONE" for it in env.world.items.values())


# ── 12. Недавние изменения ───────────────────────────────────────────────────


async def test_recent_change_goes_last_and_fails_if_still_changing():
    env = Env()
    env.person(1, recent_payment=True)
    env.person(2)
    order = []
    original = env.store.classify_rows

    async def spy(ids):
        order.extend(ids)
        return await original(ids)

    env.store.classify_rows = spy
    job = env.job([1, 2])
    assert await env.run(job) == "COMPLETED"
    assert order[:3] == [1, 2, 1]
    item = env.world.item(job, 1)
    assert (item["status"], item["category"]) == ("FAILED", "RECENT_CHANGE")
    assert [u["uuid"] for u in env.panel.updates] == [env.world.subs[2].user_remna_id]
    assert "добавьте вручную позже" in bulk.item_to_dict(item, readonly=False)["reason"]


async def test_recent_change_that_settles_gets_days_at_the_end():
    env = Env()
    env.person(1, recent_payment=True)
    original = env.store.classify_rows
    calls = {"n": 0}

    async def settle(ids):
        calls["n"] += 1
        rows = await original(ids)
        if calls["n"] > 1:
            rows[1]["recent_payment"] = False
        return rows

    env.store.classify_rows = settle
    job = env.job([1])
    await env.run(job)
    assert env.world.item(job, 1)["status"] == "DONE"
    assert len(env.panel.updates) == 1


# ── 13. Остановка посреди ────────────────────────────────────────────────────


async def test_canceling_mid_run_skips_rest_without_new_patches():
    env = Env()
    for uid in (1, 2, 3, 4):
        env.person(uid)
    job = env.job([1, 2, 3, 4])

    def stop_after_second(_panel_user):
        if len(env.panel.updates) == 2:
            env.world.jobs[job]["status"] = "CANCELING"

    env.panel.after_update = stop_after_second
    assert await env.run(job) == "CANCELED"
    assert len(env.panel.updates) == 2
    assert env.world.statuses(job) == {1: "DONE", 2: "DONE", 3: "SKIPPED", 4: "SKIPPED"}
    assert env.world.item(job, 3)["category"] == "CANCELED"


# ── 14. Финальная сверка ─────────────────────────────────────────────────────


async def test_final_verify_flags_panel_that_did_not_keep_target():
    env = Env()
    env.person(1)
    env.person(2)

    def panel_ignores_patch(panel_user):
        if panel_user.uuid == env.world.subs[1].user_remna_id:
            panel_user.expire_at = panel_user.expire_at - 3 * DAY

    env.panel.after_update = panel_ignores_patch
    job = env.job([1, 2])
    assert await env.run(job) == "COMPLETED"
    assert env.world.item(job, 1)["verify_note"] == bulk.VERIFY_NOTE
    assert env.world.item(job, 2)["verify_note"] is None
    assert env.world.jobs[job]["verify_flagged"] == 1
    summary = env.rec.admin_messages[-1]
    assert "Массовое продление №" in summary and "проверить вручную 1" in summary
    assert "Запустил: @owner" in summary


# ── 15. Исключение внутри процессора ─────────────────────────────────────────


async def test_unexpected_error_marks_job_error_and_keeps_rows_pending():
    env = Env()
    env.person(1)
    env.person(2)
    env.user_dao.fail_on = {1}
    job = env.job([1, 2])
    assert await env.run(job) == "ERROR"
    assert env.world.jobs[job]["status"] == "ERROR"
    assert "Внутренняя ошибка" in env.world.jobs[job]["pause_reason"]
    assert env.world.statuses(job) == {1: "PENDING", 2: "PENDING"}
    assert env.panel.updates == []
    assert "остановлено" in env.rec.admin_messages[-1]


# ── «Сообщить им об этом» ────────────────────────────────────────────────────


async def test_notify_creates_child_message_job_for_done_rows_only():
    env = Env()
    env.person(1)
    env.person(2, expire_in=-DAY)  # истекла — дней не получит, сообщения тоже
    notify = {"text": "Добавили дни", "channels": ["telegram", "cabinet"]}
    job = env.job([1, 2], notify=notify)
    assert await env.run(job) == "COMPLETED"
    children = [j for j in env.world.jobs.values() if j.get("parent_job_id") == job]
    assert len(children) == 1
    child = children[0]
    assert child["kind"] == "message" and child["params"]["text"] == "Добавили дни"
    assert env.world.statuses(child["id"]) == {1: "PENDING"}
    assert env.rec.kicked == [child["id"]]


async def test_notify_blocked_by_active_message_job_leaves_reason():
    env = Env()
    env.person(1)
    busy = env.world.add_job("message", {"text": "x", "channels": ["telegram"]}, [], status="PROCESSING")
    job = env.job([1], notify={"text": "Добавили дни", "channels": ["telegram"]})
    assert await env.run(job) == "COMPLETED"
    assert f"идёт другая задача №{busy}" in env.world.jobs[job]["pause_reason"]
    assert env.rec.kicked == []
