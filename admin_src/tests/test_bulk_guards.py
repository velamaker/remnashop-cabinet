"""Массовые задачи: проверки ввода, права, повтор запуска и маршруты.

ЧТО ЗАПИРАЕМ.
  * Дни, текст и каналы проверяются с русскими причинами отказа.
  * Запускать может только полный доступ с правом записи: ни модератор с грантом на
    «Пользователей», ни read-only (legacy PREVIEW: full_access=true, can_write=false).
    Проверяется на каждой изменяющей ручке, до первого обращения к базе.
  * Запуск сверяет отпечаток выборки с подтверждённым в предпросмотре: разошлось —
    409, задача не создаётся.
  * «Остановить» у задачи в очереди с начатыми строками досверяет их, а не бросает.
  * Повтор того же request_id с теми же параметрами — тот же запуск, с другими — отказ.
  * `/users/bulk/...` не перехватывается карточкой `/users/{user_id}` и наоборот.
  * Список, «Массово по фильтру» и новые ручки строят выборку одной функцией, и
    массовые действия никогда не трогают персонал.

Запуск — внутри образа бота, как остальные тесты рядом (см. ci.yml).
"""

import importlib
import inspect
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from starlette.routing import Match

bulk = importlib.import_module("src.infrastructure.services.overlay_bulk")
users = importlib.import_module("src.web.endpoints.admin.users")
users_bulk = importlib.import_module("src.web.endpoints.admin.users_bulk")
admin_pkg = importlib.import_module("src.web.endpoints.admin")

from src.core.enums import Role  # noqa: E402
from src.web.permissions import compute_access  # noqa: E402


# ── 1. Ввод ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("days", [0, 366, -1, "abc", None])
def test_validate_days_rejects(days):
    with pytest.raises(ValueError, match="Дней должно быть от 1 до 365"):
        bulk.validate_days(days)


def test_validate_days_accepts_bounds():
    assert bulk.validate_days(1) == 1 and bulk.validate_days("365") == 365


def test_validate_text_and_channels():
    with pytest.raises(ValueError, match="Текст сообщения пуст"):
        bulk.validate_text("   ")
    with pytest.raises(ValueError, match="длиннее 4000"):
        bulk.validate_text("я" * 4001)
    assert bulk.validate_text("  ok  ") == "ok"
    with pytest.raises(ValueError, match="Не выбран ни один канал"):
        bulk.validate_channels([])
    with pytest.raises(ValueError, match="Не выбран ни один канал"):
        bulk.validate_channels(["sms"])
    assert bulk.validate_channels(["email", "telegram", "email"]) == ["email", "telegram"]


# ── 2. Права ─────────────────────────────────────────────────────────────────


def test_require_full_access():
    moderator = compute_access(Role.ADMIN, {"full_access": False, "sections": ["users", "subscriptions"], "can_write": True})
    assert bulk.require_full_access(moderator) == bulk.READONLY_DENIED
    preview = compute_access(Role.PREVIEW, None)
    assert preview["full_access"] is True and preview["can_write"] is False
    assert bulk.require_full_access(preview) == bulk.READONLY_DENIED
    assert bulk.require_full_access(None) == bulk.READONLY_DENIED
    assert bulk.require_full_access(compute_access(Role.OWNER, None)) is None


def test_endpoint_writer_guard_reads_request_state():
    from fastapi import HTTPException

    req = SimpleNamespace(state=SimpleNamespace(admin_access=compute_access(Role.PREVIEW, None)))
    with pytest.raises(HTTPException) as exc:
        users_bulk._require_writer(req)
    assert exc.value.status_code == 403
    users_bulk._require_writer(SimpleNamespace(state=SimpleNamespace(admin_access=compute_access(Role.OWNER, None))))


class _Touched(BaseException):
    """Ручка тронула аргумент до проверки прав. BaseException — чтобы `except Exception`
    в ручке его не проглотил."""


class _Poison:
    def __getattr__(self, name):
        raise _Touched(f"обращение к .{name} до проверки прав")


_POST_HANDLERS = sorted(
    (route.endpoint.__name__, route.endpoint) for route in users_bulk.router.routes if "POST" in route.methods
)


def test_every_post_handler_is_listed():
    # Новая изменяющая ручка сама попадёт в проверку ниже; этот список — чтобы её
    # появление было замечено, а не прошло молча.
    assert [name for name, _ in _POST_HANDLERS] == [
        "cancel_bulk_job",
        "resume_bulk_job",
        "start_bulk_days",
        "start_bulk_message",
        "test_bulk_message",
    ]


@pytest.mark.parametrize(
    "access",
    [
        compute_access(Role.ADMIN, {"full_access": False, "sections": ["users"], "can_write": True}),
        compute_access(Role.PREVIEW, None),
    ],
    ids=["moderator-with-users-write", "readonly"],
)
@pytest.mark.parametrize("name, endpoint", _POST_HANDLERS, ids=[n for n, _ in _POST_HANDLERS])
async def test_post_handlers_refuse_without_full_write_access_before_touching_anything(access, name, endpoint):
    from fastapi import HTTPException

    func = _inner(endpoint)
    request = SimpleNamespace(state=SimpleNamespace(admin_access=access, audit_actor="@mod"))
    kwargs = {p: (request if p == "request" else _Poison()) for p in inspect.signature(func).parameters}
    with pytest.raises(HTTPException) as exc:
        await func(**kwargs)
    assert exc.value.status_code == 403
    assert exc.value.detail == bulk.READONLY_DENIED


# ── 2a. Отпечаток выборки при запуске ────────────────────────────────────────


class _StartStore:
    def __init__(self):
        self.created = []

    async def job_by_request(self, request_id):
        return None

    async def recent_days(self, ids):
        return {"count": 0, "job_id": None, "days": None}

    async def recent_text(self, ids, sha):
        return 0

    async def active_jobs(self):
        return {}

    async def create_job(self, **kwargs):
        self.created.append(kwargs)
        return 41


def _owner_request():
    return SimpleNamespace(state=SimpleNamespace(admin_access=compute_access(Role.OWNER, None), audit_actor="@owner"))


@pytest.fixture
def start_env(monkeypatch):
    store = _StartStore()
    kicked = []

    async def segment_ids(session, filters):
        return [1, 2]

    async def evaluate_days(store_, ids, **kwargs):
        return bulk.DaysEvaluation(matched=2, categories={1: bulk.Category.APPLY, 2: bulk.Category.APPLY}, sample=[])

    async def evaluate_message(store_, ids, **kwargs):
        return bulk.MessageEvaluation(matched=2, recipients=[1, 2], skipped={}, by_channel={})

    async def kick(job_id):
        kicked.append(job_id)

    monkeypatch.setattr(users_bulk, "BulkStore", lambda session: store)
    monkeypatch.setattr(users_bulk, "_segment_ids", segment_ids)
    monkeypatch.setattr(users_bulk, "evaluate_days", evaluate_days)
    monkeypatch.setattr(users_bulk, "evaluate_message", evaluate_message)
    monkeypatch.setattr(users_bulk, "_kick", kick)
    return SimpleNamespace(store=store, kicked=kicked)


async def _start_days(segment):
    body = users_bulk.BulkDaysBody(days=3, segment_hash=segment, expected_apply=2, request_id=str(uuid4()))
    return await _inner(users_bulk.start_bulk_days)(
        body=body,
        request=_owner_request(),
        response=SimpleNamespace(status_code=202),
        admin=SimpleNamespace(id=1),
        session=_Session(),
    )


async def _start_message(segment):
    body = users_bulk.BulkMessageBody(
        text="Привет", channels=["telegram"], segment_hash=segment, expected_recipients=2, request_id=str(uuid4())
    )
    return await _inner(users_bulk.start_bulk_message)(
        body=body,
        request=_owner_request(),
        response=SimpleNamespace(status_code=202),
        admin=SimpleNamespace(id=1),
        session=_Session(),
        email_sender=SimpleNamespace(is_enabled=True),
    )


@pytest.mark.parametrize("start", [_start_days, _start_message], ids=["days", "message"])
async def test_start_refuses_when_selection_changed_since_preview(start_env, start):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await start("отпечаток-из-предпросмотра")
    assert exc.value.status_code == 409 and "Выборка изменилась" in exc.value.detail
    assert start_env.store.created == [] and start_env.kicked == []

    # Контроль: с тем же отпечатком, что у выборки сейчас, задача создаётся.
    result = await start(bulk.segment_hash([1, 2]))
    assert result["job_id"] == 41 and len(start_env.store.created) == 1 and start_env.kicked == [41]


# ── 2b. «Остановить» у задачи в очереди ──────────────────────────────────────


class _WorldSession:
    def __init__(self, world):
        self.world = world

    async def commit(self):
        self.world.commit()

    async def rollback(self):
        self.world.rollback()


@pytest.fixture
def cancel_env(monkeypatch):
    from bulk_fakes import FakeStore, FakeWorld

    world = FakeWorld()
    kicked = []

    async def kick(job_id):
        kicked.append(job_id)

    monkeypatch.setattr(users_bulk, "BulkStore", lambda session: FakeStore(world))
    monkeypatch.setattr(users_bulk, "_kick", kick)

    async def cancel(job_id):
        return await _inner(users_bulk.cancel_bulk_job)(
            job_id=job_id, request=_owner_request(), _admin=None, session=_WorldSession(world)
        )

    return SimpleNamespace(world=world, kicked=kicked, cancel=cancel)


async def test_cancel_queued_job_with_started_rows_reconciles_them(cancel_env):
    """Пауза → «Продолжить» (QUEUED) → «Остановить» до того, как воркер взял задачу."""
    world = cancel_env.world
    job = world.add_job("days", {"days": 3}, [(1, "RETRY", "APPLY", False), (2, "PENDING", None, False)])
    world.items[(job, 1)]["target_expire_at"] = world.now
    assert await cancel_env.cancel(job) == {"job_id": job, "status": "CANCELING"}
    assert world.jobs[job]["status"] == "CANCELING"
    assert cancel_env.kicked == [job]
    # Строку на полпути не бросили — её досверит воркер в режиме остановки.
    assert world.statuses(job) == {1: "RETRY", 2: "PENDING"}


async def test_cancel_queued_job_without_started_rows_is_immediate(cancel_env):
    world = cancel_env.world
    job = world.add_job("days", {"days": 3}, [(1, "PENDING", None, False), (2, "DONE", "APPLY", False)])
    assert await cancel_env.cancel(job) == {"job_id": job, "status": "CANCELED"}
    assert world.statuses(job) == {1: "SKIPPED", 2: "DONE"}
    assert cancel_env.kicked == []


# ── 3. Повтор запуска ────────────────────────────────────────────────────────


def test_params_hash_and_replay_decision():
    filters = {"search": None, "blocked": None, "role": None, "expiring_days": 7}
    a = bulk.days_params_hash(filters, 3, False, False, None)
    b = bulk.days_params_hash(dict(filters), 3, False, False, None)
    c = bulk.days_params_hash(filters, 4, False, False, None)
    assert a == b and a != c
    assert bulk.replay_decision(None, a) is None
    assert bulk.replay_decision(a, b) == "duplicate"
    assert bulk.replay_decision(a, c) == "conflict"
    m1 = bulk.message_params_hash(filters, None, "текст", ["telegram", "cabinet"])
    m2 = bulk.message_params_hash(filters, None, "текст", ["cabinet", "telegram"])
    m3 = bulk.message_params_hash(filters, None, "другой", ["telegram", "cabinet"])
    assert m1 == m2 and m1 != m3


def test_segment_hash_ignores_order_and_duplicates():
    assert bulk.segment_hash([3, 1, 2, 2]) == bulk.segment_hash([1, 2, 3])
    assert bulk.segment_hash([1, 2]) != bulk.segment_hash([1, 2, 3])


# ── 4. Маршруты ──────────────────────────────────────────────────────────────


def _resolve(method: str, path: str):
    app = FastAPI()
    app.include_router(admin_pkg.router)
    scope = {"type": "http", "method": method, "path": path, "root_path": "", "path_params": {}, "query_string": b"", "headers": []}
    for route in app.router.routes:
        if hasattr(route, "_match"):
            match, child, found, ctx = route._match(scope)
            while match == Match.FULL and found is not None and hasattr(found, "_match"):
                match, child, found, ctx = found._match({**scope, **child})
            if match == Match.FULL:
                return getattr(getattr(ctx, "original_route", found), "endpoint", None)
        else:
            match, _child = route.matches(scope)
            if match == Match.FULL:
                return route.endpoint
    return None


@pytest.mark.parametrize(
    "method, path, name",
    [
        ("GET", "/api/v1/admin/users/bulk/jobs", "list_bulk_jobs"),
        ("GET", "/api/v1/admin/users/bulk/jobs/12", "get_bulk_job"),
        ("GET", "/api/v1/admin/users/bulk/jobs/12/items", "list_bulk_job_items"),
        ("GET", "/api/v1/admin/users/bulk/days/preview", "preview_bulk_days"),
        ("GET", "/api/v1/admin/users/bulk/message/preview", "preview_bulk_message"),
        ("POST", "/api/v1/admin/users/bulk/days", "start_bulk_days"),
        ("POST", "/api/v1/admin/users/bulk/message", "start_bulk_message"),
        ("POST", "/api/v1/admin/users/bulk/message/test", "test_bulk_message"),
        ("POST", "/api/v1/admin/users/bulk/jobs/12/cancel", "cancel_bulk_job"),
        ("POST", "/api/v1/admin/users/bulk/jobs/12/resume", "resume_bulk_job"),
        ("GET", "/api/v1/admin/users/123", "get_user"),
        ("POST", "/api/v1/admin/users/bulk-action", "bulk_action"),
    ],
)
def test_routes_resolve_to_the_right_handler(method, path, name):
    endpoint = _resolve(method, path)
    assert endpoint is not None and endpoint.__name__ == name


# ── 5. Выборка ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "search, blocked, role, expiring",
    [(None, None, None, None), ("ivan", True, 3, 7), ("42", None, None, None)],
)
def test_build_segment_users_only_always_excludes_staff(search, blocked, role, expiring):
    _join, where_all, _ = users.build_segment(search, blocked, role, expiring, users_only=True)
    assert "u.role = 'USER'" in where_all
    _join, where_list, params = users.build_segment(search, blocked, role, expiring, users_only=False)
    assert "u.role = 'USER'" not in where_list
    assert ("JOIN (SELECT user_id" in _join) == bool(expiring)
    if expiring:
        assert params["exp_days"] == expiring


class _Rows:
    def __init__(self, rows=None, scalar=0):
        self._rows = rows or []
        self._scalar = scalar

    def all(self):
        return self._rows

    def scalar_one(self):
        return self._scalar


class _Session:
    def __init__(self):
        self.sql = []

    async def execute(self, statement, params=None):
        self.sql.append(str(statement))
        return _Rows()

    async def commit(self):
        pass

    async def rollback(self):
        pass


def _inner(endpoint):
    return inspect.getclosurevars(endpoint).nonlocals["func"]


async def test_list_bulk_action_and_bulk_jobs_share_build_segment(monkeypatch):
    calls = []
    original = users.build_segment

    def spy(*args, **kwargs):
        calls.append(kwargs["users_only"])
        return original(*args, **kwargs)

    monkeypatch.setattr(users, "build_segment", spy)
    monkeypatch.setattr(users_bulk, "build_segment", spy)
    assert users_bulk.build_segment is spy

    session = _Session()
    await _inner(users.list_users)(
        admin=SimpleNamespace(role=Role.OWNER),
        user_dao=None,
        session=session,
        limit=25,
        offset=0,
        search="x",
        blocked=None,
        role=None,
        sort="created_at",
        order="desc",
        expiring_days=7,
    )
    result = await _inner(users.bulk_action)(
        body=users.BulkActionRequest(action="block", expiring_days=7),
        admin=SimpleNamespace(id=1),
        user_dao=None,
        session=session,
    )
    assert result == {"matched": 0, "applied": 0}
    await users_bulk._segment_ids(session, users_bulk._filters(None, None, None, 7))
    assert calls == [False, True, True]
    assert any("u.role = 'USER'" in s and "LIMIT :cap" in s for s in session.sql)


async def test_segment_over_limit_is_refused_not_truncated():
    from fastapi import HTTPException

    class Big(_Session):
        async def execute(self, statement, params=None):
            sql = str(statement)
            if "count(*)" in sql:
                return _Rows(scalar=7000)
            return _Rows(rows=[(i,) for i in range(bulk.MAX_USERS + 1)])

    with pytest.raises(HTTPException) as exc:
        await users_bulk._segment_ids(Big(), users_bulk._filters(None, None, None, None))
    assert exc.value.status_code == 400
    assert "Слишком большая выборка: 7000 человек (максимум 5000)" in exc.value.detail


def test_job_and_item_payloads_hide_identities_for_readonly():
    job = {
        "id": 5,
        "kind": "days",
        "status": "COMPLETED",
        "created_by_label": "@owner",
        "params": {"days": 3, "notify": {"text": "<b>Привет</b>", "channels": ["telegram"]}},
        "total": 2,
        "applied_count": 1,
    }
    assert bulk.job_to_dict(job, {}, readonly=True)["created_by"] is None
    full = bulk.job_to_dict(job, {"APPLY": 1}, readonly=False)
    assert full["created_by"] == "@owner" and full["params"]["text_preview"] == "Привет"
    item = {"user_id": 42, "name": "Иван", "status": "FAILED", "category": "MISMATCH", "error": "сквады"}
    hidden = bulk.item_to_dict(item, readonly=True)
    assert hidden["user_id"] is None and hidden["error"] is None and hidden["name"] == "Иван"
    assert "сквады" in hidden["reason"]


def test_days_items_and_preview_payload():
    ev = bulk.DaysEvaluation(
        matched=5,
        categories={1: bulk.Category.APPLY, 2: bulk.Category.APPLY_FROZEN, 3: bulk.Category.RECENT_CHANGE, 4: bulk.Category.RESERVE, 5: bulk.Category.LIMITED},
        sample=[{"name": "A", "expire_at": "x", "new_expire_at": "y"}],
    )
    assert bulk.days_items(ev) == [
        (1, "PENDING", None, False),
        (2, "PENDING", None, False),
        (3, "PENDING", None, True),
        (4, "SKIPPED", "RESERVE", False),
        (5, "SKIPPED", "LIMITED", False),
    ]
    payload = bulk.days_preview_payload(ev, recently={"count": 0, "job_id": None, "days": None}, active_job_id=None, readonly=True)
    assert (payload["apply"], payload["apply_frozen"], payload["deferred"]) == (1, 1, 1)
    assert payload["skipped"]["RESERVE"] == 1 and payload["skipped"]["EXPIRED"] == 0
    assert payload["sample"] == [{"name": "A"}]
    assert payload["segment_hash"] == bulk.segment_hash([1, 2, 3])
    assert payload["limits"] == {"max_days": 365, "max_users": 5000}


def test_request_id_is_uuid_in_body():
    body = users_bulk.BulkDaysBody(days=3, segment_hash="h", request_id=str(uuid4()))
    assert body.notify is None and body.allow_repeat is False
