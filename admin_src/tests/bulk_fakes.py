"""Подделки для тестов массовых задач (test_bulk_*.py): мир в памяти.

`FakeWorld` держит людей, подписки, паузы, задачи и строки журнала с настоящей
семантикой транзакции: всё, что процессор поменял после последнего commit,
rollback возвращает как было. Панель (`FakePanel`) — внешний мир, откатом не
возвращается, как и настоящая.

`FakeStore` повторяет интерфейс `overlay_bulk.BulkStore` — тот же набор методов с
тем же смыслом условных UPDATE («захват только из PENDING», «аренда только своя»).
Сам SQL на настоящем Postgres проверяет scripts/migration-e2e.sh.
"""

import copy
import dataclasses
import importlib
from collections import Counter
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable, Optional
from uuid import UUID, uuid4

bulk = importlib.import_module("src.infrastructure.services.overlay_bulk")

from src.application.dto import UserDto  # noqa: E402
from src.core.enums import Locale, Role  # noqa: E402

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
SQUAD = UUID("11111111-1111-1111-1111-111111111111")


@dataclasses.dataclass
class FakeSub:
    """Поля SubscriptionDto, которые читают сверка и запись срока."""

    id: int
    user_id: int
    user_remna_id: UUID
    expire_at: datetime
    status: str = "ACTIVE"
    is_trial: bool = False
    traffic_limit: int = 0
    device_limit: int = 0
    traffic_limit_strategy: str = "NO_RESET"
    tag: Optional[str] = None
    internal_squads: list = dataclasses.field(default_factory=lambda: [SQUAD])
    external_squad: Optional[UUID] = None


class FakeWorld:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now
        self.users: dict[int, UserDto] = {}
        self.people: dict[int, dict] = {}
        self.subs: dict[int, FakeSub] = {}
        self.freezes: dict[int, dict] = {}
        self.jobs: dict[int, dict] = {}
        self.items: dict[tuple[int, int], dict] = {}
        self.feed: list[tuple[int, dict]] = []
        self.pushed: list[tuple[int, dict]] = []
        # Проведённые оплаты (transactions COMPLETED): (user_id, момент проведения).
        self.payments: list[tuple[int, datetime]] = []
        self.next_job = 1
        self.snapshot: Optional[tuple] = None
        self.commits = 0
        self.rollbacks = 0

    # транзакция

    def touch(self) -> None:
        if self.snapshot is None:
            self.snapshot = copy.deepcopy(
                (self.subs, self.freezes, self.jobs, self.items, self.feed, self.people, self.next_job)
            )

    def commit(self) -> None:
        self.snapshot = None
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1
        if self.snapshot is not None:
            (self.subs, self.freezes, self.jobs, self.items, self.feed, self.people, self.next_job) = self.snapshot
            self.snapshot = None

    # наполнение

    def add_person(
        self,
        uid: int,
        *,
        expire_in: Optional[timedelta] = timedelta(days=10),
        status: str = "ACTIVE",
        is_trial: bool = False,
        role: str = "USER",
        is_blocked: bool = False,
        telegram_id: Optional[int] = 5000,
        is_bot_blocked: bool = False,
        email: Optional[str] = None,
        is_email_verified: bool = False,
        has_push: bool = False,
        frozen_remaining: Optional[int] = None,
        reserve_expire_at: Optional[datetime] = None,
        recent_payment: bool = False,
        sub_updated_at: Optional[datetime] = None,
        language: str = "ru",
        panel: Optional["FakePanel"] = None,
        panel_overrides: Optional[dict] = None,
    ) -> None:
        tg = None if telegram_id is None else telegram_id + uid
        self.users[uid] = UserDto(
            id=uid,
            name=f"Человек {uid}",
            telegram_id=tg,
            email=email,
            is_email_verified=is_email_verified,
            role=Role[role],
            language=Locale.RU,
            is_blocked=is_blocked,
            is_bot_blocked=is_bot_blocked,
        )
        sub = None
        if expire_in is not None:
            sub = FakeSub(
                id=100 + uid,
                user_id=uid,
                user_remna_id=uuid4(),
                expire_at=self.now + expire_in,
                status=status,
                is_trial=is_trial,
            )
            self.subs[uid] = sub
        if frozen_remaining is not None and sub is not None:
            self.freezes[uid] = {
                "active": True,
                "remaining_seconds": frozen_remaining,
                "remna_uuid": str(sub.user_remna_id),
            }
        self.people[uid] = {
            "id": uid,
            "role": role,
            "is_blocked": is_blocked,
            "name": f"Человек {uid}",
            "telegram_id": tg,
            "is_bot_blocked": is_bot_blocked,
            "email": email,
            "is_email_verified": is_email_verified,
            "language": language,
            "sub_id": sub.id if sub else None,
            "sub_status": status if sub else None,
            "is_trial": is_trial,
            "expire_at": sub.expire_at if sub else None,
            "user_remna_id": sub.user_remna_id if sub else None,
            "sub_updated_at": sub_updated_at or (self.now - timedelta(days=1)),
            "frozen_remaining": frozen_remaining,
            "reserve_expire_at": reserve_expire_at,
            "recent_payment": recent_payment,
            "has_push": has_push,
        }
        if panel is not None and sub is not None:
            panel.mirror(sub, self.users[uid], **(panel_overrides or {}))

    def add_job(self, kind: str, params: dict, items: list[tuple], *, status: str = "QUEUED", **extra: Any) -> int:
        job_id = self.next_job
        self.next_job += 1
        self.jobs[job_id] = {
            "id": job_id,
            "kind": kind,
            "status": status,
            "params": params,
            "parent_job_id": extra.get("parent_job_id"),
            "created_by": 1,
            "created_by_label": "@owner",
            "request_id": extra.get("request_id") or uuid4(),
            "lease_owner": extra.get("lease_owner"),
            "lease_alive": extra.get("lease_alive", False),
            "pause_reason": None,
            "finished": False,
            "segment_hash": "",
            "total": len(items),
            "done_count": 0,
            "applied_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "unknown_count": 0,
            "verify_flagged": 0,
        }
        for uid, st, cat, deferred in items:
            self.items[(job_id, uid)] = {
                "user_id": uid,
                "status": st,
                "category": cat,
                "subscription_id": None,
                "old_expire_at": None,
                "target_expire_at": None,
                "added_seconds": None,
                "deferred": bool(deferred),
                "channels": None,
                "tg_message_id": None,
                "text_sha256": None,
                "verify_note": None,
                "error": None,
                "attempts": 0,
                # Как у базы: now() транзакции, в которой строку меняли в последний раз.
                "updated_at": None,
            }
        return job_id

    def item(self, job_id: int, uid: int) -> dict:
        return self.items[(job_id, uid)]

    def statuses(self, job_id: int) -> dict[int, str]:
        return {uid: it["status"] for (j, uid), it in sorted(self.items.items()) if j == job_id}


class FakeStore:
    def __init__(self, world: FakeWorld) -> None:
        self.w = world
        self.sub_state_hook: Optional[Callable[[int, dict], dict]] = None
        self.feed_fails = False

    async def commit(self) -> None:
        self.w.commit()

    async def rollback(self) -> None:
        self.w.rollback()

    # задачи

    async def acquire_lease(self, job_id: int, token: str) -> Optional[str]:
        job = self.w.jobs.get(job_id)
        if job is None or job["status"] not in ("QUEUED", "PROCESSING", "CANCELING"):
            return None
        if job["lease_owner"] not in (None, token) and job["lease_alive"]:
            return None
        self.w.touch()
        if job["status"] == "QUEUED":
            job["status"] = "PROCESSING"
        job["lease_owner"] = token
        job["lease_alive"] = True
        return job["status"]

    async def renew_lease(self, job_id: int, token: str) -> Optional[str]:
        job = self.w.jobs.get(job_id)
        if job is None or job["lease_owner"] != token or job["status"] not in ("PROCESSING", "CANCELING"):
            return None
        return job["status"]

    async def release(self, job_id: int, token: str, status: str, *, reason: Optional[str] = None, finished: bool) -> bool:
        job = self.w.jobs[job_id]
        if job["lease_owner"] != token:
            return False
        self.w.touch()
        job.update(status=status, pause_reason=reason, finished=finished, lease_owner=None, lease_alive=False)
        return True

    async def set_reason(self, job_id: int, reason: Optional[str]) -> None:
        self.w.touch()
        self.w.jobs[job_id]["pause_reason"] = reason

    async def get_job(self, job_id: int) -> Optional[dict]:
        job = self.w.jobs.get(job_id)
        return copy.deepcopy(job) if job else None

    async def recount(self, job_id: int) -> None:
        self.w.touch()
        c = Counter(it["status"] for (j, _), it in self.w.items.items() if j == job_id)
        flagged = sum(1 for (j, _), it in self.w.items.items() if j == job_id and it["verify_note"])
        self.w.jobs[job_id].update(
            done_count=c["DONE"] + c["SKIPPED"] + c["FAILED"] + c["UNKNOWN"],
            applied_count=c["DONE"],
            skipped_count=c["SKIPPED"],
            failed_count=c["FAILED"],
            unknown_count=c["UNKNOWN"],
            verify_flagged=flagged,
        )

    async def totals(self, job_id: int) -> dict[str, int]:
        rows = [it for (j, _), it in self.w.items.items() if j == job_id]
        ch = lambda name: sum(1 for it in rows if it["channels"] and name in it["channels"])  # noqa: E731
        return {
            "applied": sum(1 for it in rows if it["status"] == "DONE"),
            "frozen": sum(1 for it in rows if it["status"] == "DONE" and it["added_seconds"] is not None),
            "skipped": sum(1 for it in rows if it["status"] == "SKIPPED"),
            "failed": sum(1 for it in rows if it["status"] == "FAILED"),
            "unknown": sum(1 for it in rows if it["status"] == "UNKNOWN"),
            "flagged": sum(1 for it in rows if it["verify_note"]),
            "tg": ch("telegram"),
            "push": ch("push"),
            "email": ch("email"),
            "cabinet": sum(1 for it in rows if it["category"] == "CABINET_ONLY"),
        }

    async def create_job(self, *, kind, request_id, params_hash, parent_job_id, created_by, created_by_label, params, segment_hash, items) -> int:
        for job in self.w.jobs.values():
            if job["request_id"] == request_id:
                raise bulk.DuplicateRequest(job["id"])
        for job in self.w.jobs.values():
            if job["kind"] == kind and job["status"] in bulk.ACTIVE_STATUSES:
                raise bulk.ActiveJobExists(job["id"])
        self.w.touch()
        return self.w.add_job(kind, dict(params), list(items), parent_job_id=parent_job_id, request_id=request_id)

    # строки

    async def items(self, job_id: int, statuses, *, deferred: Optional[bool] = None) -> list[dict]:
        return [
            copy.deepcopy(it)
            for (j, uid), it in sorted(self.w.items.items())
            if j == job_id and it["status"] in statuses and (deferred is None or it["deferred"] == deferred)
        ]

    async def claim(self, job_id: int, user_id: int, to: str) -> bool:
        it = self.w.items.get((job_id, user_id))
        if it is None or it["status"] != "PENDING":
            return False
        self.w.touch()
        it["status"] = to
        it["attempts"] += 1
        it["updated_at"] = self.w.now
        return True

    async def set_item(self, job_id: int, user_id: int, **fields: Any) -> None:
        unknown = set(fields) - bulk._ITEM_FIELDS
        assert not unknown, unknown
        self.w.touch()
        self.w.items[(job_id, user_id)].update(fields, updated_at=self.w.now)

    async def skip_pending(self, job_id: int, category: str) -> int:
        self.w.touch()
        n = 0
        for (j, _), it in self.w.items.items():
            if j == job_id and it["status"] == "PENDING":
                it.update(status="SKIPPED", category=category)
                n += 1
        return n

    async def unclaim_unstarted(self, job_id: int) -> None:
        self.w.touch()
        for (j, _), it in self.w.items.items():
            if j == job_id and it["status"] == "RUNNING" and it["target_expire_at"] is None and it["added_seconds"] is None:
                it["status"] = "PENDING"

    async def done_user_ids(self, job_id: int) -> list[int]:
        return [uid for (j, uid), it in sorted(self.w.items.items()) if j == job_id and it["status"] == "DONE"]

    async def payment_near_item(self, job_id: int, user_id: int) -> bool:
        """PAYMENT_NEAR_ITEM_SQL: оплата от RECENT_TX_MIN минут до записи до PAYMENT_AFTER_MIN после."""
        at = self.w.items[(job_id, user_id)]["updated_at"]
        if at is None:
            return False
        low = at - timedelta(minutes=bulk.RECENT_TX_MIN)
        high = at + timedelta(minutes=bulk.PAYMENT_AFTER_MIN)
        return any(uid == user_id and low < paid < high for uid, paid in self.w.payments)

    async def inflight(self, job_id: int, statuses) -> int:
        return sum(1 for (j, _), it in self.w.items.items() if j == job_id and it["status"] in statuses)

    async def cancel_now(self, job_id: int, expected: str, by: str) -> bool:
        job = self.w.jobs.get(job_id)
        if job is None or job["status"] != expected:
            return False
        self.w.touch()
        job.update(status="CANCELED", finished=True, lease_owner=None, lease_alive=False, canceled_by_label=by)
        await self.skip_pending(job_id, bulk.Category.CANCELED.value)
        await self.recount(job_id)
        return True

    async def mark_canceling(self, job_id: int, expected: str, by: str) -> bool:
        job = self.w.jobs.get(job_id)
        if job is None or job["status"] != expected:
            return False
        self.w.touch()
        job.update(status="CANCELING", canceled_by_label=by, finished=False)
        return True

    async def active_jobs(self) -> dict[str, int]:
        return {j["kind"]: j["id"] for j in self.w.jobs.values() if j["status"] in bulk.ACTIVE_STATUSES}

    # данные людей

    async def classify_rows(self, ids) -> dict[int, dict]:
        out = {}
        for uid in ids:
            row = self.w.people.get(int(uid))
            if row is not None:
                out[int(uid)] = dict(row)
        return out

    async def sub_state(self, sub_id: int) -> Optional[dict]:
        for sub in self.w.subs.values():
            if sub.id == sub_id:
                state = {"expire_at": sub.expire_at, "status": sub.status, "updated_at": None}
                return self.sub_state_hook(sub_id, state) if self.sub_state_hook else state
        return None

    async def add_frozen_seconds(self, user_id: int, seconds: int) -> bool:
        fr = self.w.freezes.get(user_id)
        if not fr or not fr["active"]:
            return False
        self.w.touch()
        fr["remaining_seconds"] += int(seconds)
        return True

    async def freeze_state(self, user_id: int) -> Optional[dict]:
        fr = self.w.freezes.get(user_id)
        return dict(fr) if fr else None

    async def record_feed(self, user_id: int, payload: dict) -> bool:
        if self.feed_fails:
            return False
        self.w.touch()
        self.w.feed.append((user_id, dict(payload)))
        return True

    async def push(self, user_id: int, payload: dict) -> int:
        self.w.pushed.append((user_id, dict(payload)))
        return 1


class FakeUserDao:
    def __init__(self, world: FakeWorld) -> None:
        self.w = world
        self.fail_on: set[int] = set()

    async def get_by_id(self, uid: int) -> Optional[UserDto]:
        if uid in self.fail_on:
            raise RuntimeError("база недоступна")
        user = self.w.users.get(uid)
        return dataclasses.replace(user) if user else None


class FakeSubscriptionDao:
    def __init__(self, world: FakeWorld) -> None:
        self.w = world
        self.updates: list[tuple[int, datetime]] = []
        self.fail_update = False

    async def get_current(self, uid: int) -> Optional[FakeSub]:
        sub = self.w.subs.get(uid)
        return copy.deepcopy(sub) if sub else None

    async def update(self, sub: FakeSub) -> Optional[FakeSub]:
        if self.fail_update:
            raise RuntimeError("база отвалилась после панели")
        self.w.touch()
        self.w.subs[sub.user_id] = copy.deepcopy(sub)
        self.w.people[sub.user_id]["expire_at"] = sub.expire_at
        self.w.people[sub.user_id]["sub_updated_at"] = self.w.now
        self.updates.append((sub.user_id, sub.expire_at))
        return copy.deepcopy(sub)


class FakePanel:
    """Remnawave: GET и PATCH пользователя. Сбои и подмены задаются снаружи."""

    def __init__(self) -> None:
        self.users: dict[UUID, SimpleNamespace] = {}
        self.updates: list[dict] = []
        self.gets = 0
        self.get_error: Optional[Exception] = None
        self.update_error: Optional[Exception] = None
        self.after_update: Optional[Callable[[SimpleNamespace], None]] = None
        self.other_calls: list[str] = []

    def mirror(self, sub: FakeSub, user: UserDto, **overrides: Any) -> None:
        data = {
            "uuid": sub.user_remna_id,
            "status": sub.status,
            "expire_at": sub.expire_at,
            "subscription_url": "https://sub.example/x",
            "traffic_limit_bytes": sub.traffic_limit * 1024**3,
            "hwid_device_limit": sub.device_limit or None,
            "traffic_limit_strategy": sub.traffic_limit_strategy,
            "tag": sub.tag,
            "active_internal_squads": [SimpleNamespace(uuid=s) for s in sub.internal_squads],
            "external_squad_uuid": sub.external_squad,
            "email": user.email,
            "telegram_id": user.telegram_id,
        }
        data.update(overrides)
        self.users[sub.user_remna_id] = SimpleNamespace(**data)

    async def get_user_by_uuid(self, uuid: UUID) -> Optional[SimpleNamespace]:
        self.gets += 1
        if self.get_error is not None:
            raise self.get_error
        found = self.users.get(UUID(str(uuid)))
        return copy.deepcopy(found) if found else None

    async def update_user(self, user: Any, uuid: UUID, plan: Any = None, subscription: Any = None, reset_traffic: bool = False) -> Any:
        if self.update_error is not None:
            raise self.update_error
        self.updates.append(
            {"uuid": uuid, "expire_at": subscription.expire_at, "email": user.email, "telegram_id": user.telegram_id}
        )
        panel_user = self.users[UUID(str(uuid))]
        panel_user.expire_at = subscription.expire_at
        panel_user.email = user.email
        panel_user.telegram_id = user.telegram_id
        if self.after_update:
            self.after_update(panel_user)
        return panel_user

    async def enable_user(self, uuid: UUID) -> None:
        self.other_calls.append("enable_user")

    async def disable_user(self, uuid: UUID) -> None:
        self.other_calls.append("disable_user")


class Recorder:
    """Собирает уведомления админам, постановки в очередь и сны."""

    def __init__(self) -> None:
        self.admin_messages: list[str] = []
        self.kicked: list[int] = []
        self.sleeps: list[float] = []

    async def notify_admins(self, content: str) -> None:
        self.admin_messages.append(content)

    async def kick(self, job_id: int) -> None:
        self.kicked.append(job_id)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
