"""Скидка на продление: порядок записи и доставки в кроне (`run_once`).

ЧТО ЗАПИРАЕМ. Правила «кому» проверяет test_renewal_discount_rules.py. Здесь —
то, что ломается не в правилах, а во времени и в сбоях:
  * повторный прогон ничего не выдаёт и не шлёт;
  * скидка, появившаяся у человека между отбором и выдачей, — откат целиком:
    ни строки выдачи, ни сообщения;
  * сбой отправки одному человеку не мешает следующему и остаётся в журнале;
  * захваченное сообщение не шлётся второй раз, незахваченное досылается ровно
    однажды, сгоревшее не досылается;
  * выключенная фича всё равно гасит просроченные скидки — и только свои;
  * не больше 50 выдач за прогон;
  * «воспользовались» — только оплата со скидкой, включая опоздавший платёж;
  * сообщение с кнопками «Продлить» и «Закрыть» и не самоудаляется;
  * «пример себе» уходит копией админа с ролью клиента и ничего не выдаёт.

База подделана: `FakeDb` узнаёт запросы по константам модуля и держит выдачи и
людей в памяти с настоящей семантикой транзакции (rollback возвращает всё как
было). Настоящий SQL на настоящей схеме гоняет scripts/drills/renewal_discount_drill.py.

Запуск — внутри образа бота, как остальные тесты рядом (см. ci.yml).
"""

import copy
import importlib
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import pytest

rd = importlib.import_module("src.infrastructure.services.overlay_renewal_discount")
task = importlib.import_module("src.infrastructure.taskiq.tasks.renewal_discount")

from src.core.enums import Locale, Role  # noqa: E402
from src.application.dto import UserDto  # noqa: E402
from src.telegram.keyboards import get_renew_keyboard  # noqa: E402

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
ENV = rd.Env(email_enabled=True, autopay_enabled=True)


def cfg(**over: Any) -> dict:
    return rd._normalize({**rd.DEFAULT_CONFIG, "enabled": True, **over})


# ── Подделки ──────────────────────────────────────────────────────────────────


class FakeResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def first(self) -> Optional[tuple]:
        return tuple(self._rows[0].values()) if self._rows else None

    def all(self) -> list:
        return list(self._rows)

    def mappings(self) -> "FakeResult":
        return self

    def scalar_one(self) -> Any:
        return next(iter(self._rows[0].values()))


class FakeDb:
    """Люди, выдачи и оплаты в памяти; запросы узнаются по тексту констант модуля."""

    def __init__(self) -> None:
        self.users: dict[int, dict] = {}
        self.candidates: dict[int, dict] = {}
        self.payments: list[dict] = []
        self.completed: list[dict] = []
        self.grants: list[dict] = []
        self.next_id = 1
        self.snapshot: Optional[tuple] = None
        self.executed: list[str] = []
        self.hooks: dict[str, Callable[["FakeDb", dict], None]] = {}
        self.handlers = {
            rd.CANDIDATES_SQL: self._candidates,
            rd.PAYMENTS_SQL: self._payments,
            rd.INSERT_GRANT_SQL: self._insert,
            rd.APPLY_DISCOUNT_SQL: self._apply,
            rd.CLAIM_SQL: self._claim,
            rd.NOTIFY_RESULT_SQL: self._notify_result,
            rd.PENDING_NOTIFY_SQL: self._pending,
            rd.USAGE_GRANTS_SQL: self._usage_grants,
            rd.USAGE_PAYMENTS_SQL: self._usage_payments,
            rd.MARK_USED_SQL: self._mark_used,
            rd.DUE_EXPIRE_SQL: self._due,
            rd.ACTIVE_GRANTS_SQL: self._active,
            rd.MARK_CLOSED_SQL: self._mark_closed,
            rd.CLEAR_DISCOUNT_SQL: self._clear,
        }

    # транзакция

    def _touch(self) -> None:
        if self.snapshot is None:
            self.snapshot = copy.deepcopy((self.users, self.grants, self.next_id))

    async def commit(self) -> None:
        self.snapshot = None

    async def rollback(self) -> None:
        if self.snapshot is not None:
            self.users, self.grants, self.next_id = self.snapshot
            self.snapshot = None

    def concurrently(self, fn: Callable[[dict], None]) -> None:
        """Чужая уже закоммиченная правка людей: наш откат её не отменяет."""
        fn(self.users)
        if self.snapshot is not None:
            fn(self.snapshot[0])

    async def execute(self, statement: Any, params: Any = None) -> FakeResult:
        sql = str(statement)
        p = dict(params or {})
        self.executed.append(sql)
        hook = self.hooks.pop(sql, None)
        if hook is not None:
            hook(self, p)
        handler = self.handlers.get(sql)
        assert handler is not None, f"незнакомый запрос: {sql[:80]}"
        return FakeResult(handler(p))

    # наполнение

    def add_client(
        self,
        uid: int,
        *,
        expire_in: timedelta = timedelta(days=5) - timedelta(hours=1),
        telegram_id: Optional[int] = 1000,
        paid: bool = True,
        purchase_discount: int = 0,
        **row: Any,
    ) -> None:
        self.users[uid] = {
            "purchase_discount": purchase_discount,
            "personal_discount": 0,
            "autopay_enabled": False,
            "is_blocked": False,
            "is_bot_blocked": False,
            "telegram_id": telegram_id,
            "lang": "ru",
        }
        self.candidates[uid] = {
            "user_id": uid,
            "role": "USER",
            "email": None,
            "is_email_verified": False,
            "subscription_id": uid * 10,
            "expire_at": NOW + expire_in,
            "is_trial": False,
            "has_push": False,
            "on_reserve": False,
            "frozen": False,
            "payment_in_progress": False,
            "last_gift_at": None,
            "other_offer_until": None,
            **row,
        }
        if paid:
            self.payments.append(
                {
                    "user_id": uid,
                    "created_at": NOW - timedelta(days=25),
                    "purchase_type": "NEW",
                    "duration_days": 30,
                    "discount_percent": 0,
                }
            )

    def add_grant(self, uid: int, **over: Any) -> dict:
        grant = {
            "id": self.next_id,
            "user_id": uid,
            "subscription_id": uid * 10,
            "sub_expire_at": NOW + timedelta(days=4),
            "percent": 10,
            "granted_at": NOW - timedelta(hours=1),
            "expires_at": NOW + timedelta(days=3),
            "status": "active",
            "closed_at": None,
            "transaction_id": None,
            "notified_at": None,
            "tg_status": None,
            "push_sent": 0,
            "notify_error": None,
            **over,
        }
        self.next_id += 1
        self.grants.append(grant)
        return grant

    def grant_of(self, uid: int) -> list[dict]:
        return [g for g in self.grants if g["user_id"] == uid]

    # запросы

    def _candidates(self, p: dict) -> list[dict]:
        rows = []
        for uid, base in self.candidates.items():
            if not (p["lo"] <= base["expire_at"] < p["hi"]):
                continue
            user = self.users[uid]
            mine = self.grant_of(uid)
            latest = max(mine, key=lambda g: g["granted_at"]) if mine else None
            open_until = [g["expires_at"] for g in mine if g["status"] == "active"]
            rows.append(
                {
                    **base,
                    "is_blocked": user["is_blocked"],
                    "is_bot_blocked": user["is_bot_blocked"],
                    "telegram_id": user["telegram_id"],
                    "lang": user["lang"],
                    "purchase_discount": user["purchase_discount"],
                    "personal_discount": user["personal_discount"],
                    "autopay_enabled": user["autopay_enabled"],
                    "last_granted_at": latest["granted_at"] if latest else None,
                    "last_grant_sub_expire_at": latest["sub_expire_at"] if latest else None,
                    "open_grant_until": max(open_until) if open_until else None,
                }
            )
        return sorted(rows, key=lambda r: r["expire_at"])

    def _payments(self, p: dict) -> list[dict]:
        return [x for x in self.payments if x["user_id"] in p["ids"]]

    def _insert(self, p: dict) -> list[dict]:
        for g in self.grants:
            if g["user_id"] == p["u"] and (
                g["sub_expire_at"] == p["sub_expire_at"] or g["status"] == "active"
            ):
                return []  # ON CONFLICT DO NOTHING
        self._touch()
        grant = self.add_grant(
            p["u"],
            subscription_id=p["sub"],
            sub_expire_at=p["sub_expire_at"],
            percent=p["p"],
            granted_at=p["now"],
            expires_at=p["expires_at"],
        )
        return [{"id": grant["id"]}]

    def _apply(self, p: dict) -> list[dict]:
        u = self.users[p["u"]]
        if (
            u["purchase_discount"] == 0
            and u["personal_discount"] < p["p"]
            and not u["is_blocked"]
            and (not u["autopay_enabled"] or not p["autopay_guard"])
        ):
            self._touch()
            u["purchase_discount"] = p["p"]
            return [{"id": p["u"]}]
        return []

    def _grant(self, gid: int) -> Optional[dict]:
        return next((g for g in self.grants if g["id"] == gid), None)

    def _claim(self, p: dict) -> list[dict]:
        g = self._grant(p["id"])
        if g is None or g["notified_at"] is not None:
            return []
        self._touch()
        g = self._grant(p["id"])
        g["notified_at"] = p["now"]
        return [{"id": g["id"]}]

    def _notify_result(self, p: dict) -> list[dict]:
        self._touch()
        g = self._grant(p["id"])
        g.update(tg_status=p["tg"], push_sent=p["push"], notify_error=p["err"])
        return []

    def _pending(self, p: dict) -> list[dict]:
        out = []
        for g in self.grants:
            if (
                g["status"] == "active"
                and g["notified_at"] is None
                and g["granted_at"] < p["stale_before"]
                and g["expires_at"] > p["now"]
            ):
                u = self.users[g["user_id"]]
                out.append(
                    {
                        "id": g["id"],
                        "user_id": g["user_id"],
                        "percent": g["percent"],
                        "sub_expire_at": g["sub_expire_at"],
                        "expires_at": g["expires_at"],
                        "lang": u["lang"],
                        "telegram_id": u["telegram_id"],
                        "is_bot_blocked": u["is_bot_blocked"],
                    }
                )
        return out

    def _usage_grants(self, p: dict) -> list[dict]:
        return [
            {k: g[k] for k in ("id", "user_id", "percent", "granted_at", "expires_at", "status")}
            for g in self.grants
            if g["status"] == "active"
            or (g["status"] == "expired" and g["closed_at"] and g["closed_at"] > p["late_since"])
        ]

    def _usage_payments(self, p: dict) -> list[dict]:
        return sorted(
            (x for x in self.completed if x["user_id"] in p["ids"] and x["created_at"] >= p["since"]),
            key=lambda x: x["created_at"],
        )

    def _mark_used(self, p: dict) -> list[dict]:
        g = self._grant(p["id"])
        if g is None or g["status"] != p["was"]:
            return []
        self._touch()
        g = self._grant(p["id"])
        g.update(status="used", transaction_id=p["tx"], closed_at=p["now"])
        return [{"id": g["id"]}]

    def _due(self, p: dict) -> list[dict]:
        return [
            {"id": g["id"], "user_id": g["user_id"], "percent": g["percent"]}
            for g in self.grants
            if g["status"] == "active" and g["expires_at"] <= p["now"]
        ]

    def _active(self, p: dict) -> list[dict]:
        return [
            {"id": g["id"], "user_id": g["user_id"], "percent": g["percent"]}
            for g in self.grants
            if g["status"] == "active"
        ]

    def _mark_closed(self, p: dict) -> list[dict]:
        g = self._grant(p["id"])
        if g is None or g["status"] != "active":
            return []
        self._touch()
        g = self._grant(p["id"])
        g.update(status=p["to"], closed_at=p["now"])
        return [{"id": g["id"]}]

    def _clear(self, p: dict) -> list[dict]:
        u = self.users.get(p["u"])
        if u and u["purchase_discount"] == p["p"]:
            self._touch()
            self.users[p["u"]]["purchase_discount"] = 0
        return []


class Senders:
    def __init__(self) -> None:
        self.tg: list[tuple[int, Any]] = []
        self.push: list[tuple[int, str, dict]] = []
        self.tg_result: dict[int, Any] = {}
        self.sleeps: list[float] = []

    async def send_tg(self, user_id: int, payload: Any) -> str:
        self.tg.append((user_id, payload))
        result = self.tg_result.get(user_id, rd.TG_SENT)
        if isinstance(result, BaseException):
            raise result
        return result

    async def send_push(self, user_id: int, lang: str, messages: dict) -> int:
        self.push.append((user_id, lang, messages))
        return 1

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


async def run(db: FakeDb, s: Senders, *, now: datetime = NOW, conf: Any = None) -> dict:
    return await task.run_once(
        db,
        send_tg=s.send_tg,
        send_push=s.send_push,
        now=now,
        cfg=conf or cfg(),
        env=ENV,
        sleep=s.sleep,
    )


# ── Выдача ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_second_run_grants_and_sends_nothing():
    db, s = FakeDb(), Senders()
    db.add_client(1)

    first = await run(db, s)
    assert first["granted"] == 1
    assert db.users[1]["purchase_discount"] == 10
    [grant] = db.grants
    assert grant["tg_status"] == "sent" and grant["notified_at"] == NOW
    assert grant["expires_at"] <= db.candidates[1]["expire_at"]

    second = await run(db, s)
    assert second["granted"] == 0 and second["resent"] == 0
    assert len(db.grants) == 1 and len(s.tg) == 1 and len(s.push) == 1
    assert second["skipped"] == {"open_grant": 1}


@pytest.mark.asyncio
async def test_discount_appearing_between_selection_and_grant_rolls_back():
    db, s = FakeDb(), Senders()
    db.add_client(1)
    # Пока шла выборка, человеку выдали win-back 20 %.
    db.hooks[rd.APPLY_DISCOUNT_SQL] = lambda d, p: d.concurrently(
        lambda users: users[1].update(purchase_discount=20)
    )

    report = await run(db, s)

    assert report["granted"] == 0 and report["lost_race"] == 1
    assert db.grants == []
    assert s.tg == [] and s.push == []
    assert db.users[1]["purchase_discount"] == 20


@pytest.mark.asyncio
async def test_failed_send_is_recorded_and_next_person_still_gets_message():
    db, s = FakeDb(), Senders()
    db.add_client(1, expire_in=timedelta(days=4, hours=20))
    db.add_client(2, expire_in=timedelta(days=4, hours=21))
    db.add_client(3, expire_in=timedelta(days=4, hours=22))
    s.tg_result[1] = RuntimeError("Bad Request: can't parse entities")
    s.tg_result[3] = rd.TG_BLOCKED

    report = await run(db, s)

    assert report["granted"] == 3
    by_user = {g["user_id"]: g for g in db.grants}
    assert by_user[1]["tg_status"] == "failed"
    assert "can't parse entities" in by_user[1]["notify_error"]
    assert len(by_user[1]["notify_error"]) <= 300
    assert by_user[2]["tg_status"] == "sent" and by_user[2]["notify_error"] is None
    assert by_user[3]["tg_status"] == "blocked"
    assert [uid for uid, _ in s.tg] == [1, 2, 3]


@pytest.mark.asyncio
async def test_flood_wait_is_retried_once():
    db, s = FakeDb(), Senders()
    db.add_client(1)

    class RetryAfter(Exception):
        retry_after = 7

    calls = {"n": 0}

    async def flaky(user_id: int, payload: Any) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RetryAfter("flood")
        return rd.TG_SENT

    await task.run_once(db, send_tg=flaky, send_push=s.send_push, now=NOW, cfg=cfg(), env=ENV, sleep=s.sleep)

    assert calls["n"] == 2
    assert 8.0 in s.sleeps
    assert db.grants[0]["tg_status"] == "sent"


@pytest.mark.asyncio
async def test_no_telegram_goes_to_push_only():
    db, s = FakeDb(), Senders()
    db.add_client(1, telegram_id=None, has_push=True)

    await run(db, s)

    assert s.tg == []
    assert db.grants[0]["tg_status"] == "no_telegram" and db.grants[0]["push_sent"] == 1


@pytest.mark.asyncio
async def test_no_more_than_fifty_grants_per_run():
    db, s = FakeDb(), Senders()
    for uid in range(1, 61):
        db.add_client(uid, expire_in=timedelta(days=4, hours=13, minutes=uid))

    report = await run(db, s)

    assert report["granted"] == 50
    assert len(db.grants) == 50 and len(s.tg) == 50
    assert report["skipped"]["run_limit"] == 10


# ── Досылка ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claimed_message_is_never_sent_again():
    db, s = FakeDb(), Senders()
    db.add_client(1, expire_in=timedelta(days=20))  # вне окна — только выдача
    db.users[1]["purchase_discount"] = 10
    db.add_grant(1, notified_at=NOW - timedelta(hours=1), tg_status=None)

    await run(db, s)
    assert s.tg == [] and s.push == []


@pytest.mark.asyncio
async def test_unclaimed_grant_is_resent_exactly_once():
    db, s = FakeDb(), Senders()
    db.add_client(1, expire_in=timedelta(days=20))
    db.users[1]["purchase_discount"] = 10
    db.add_grant(1, granted_at=NOW - timedelta(minutes=30))
    # Свежую (моложе 10 минут) не трогаем: её, может быть, шлёт соседний прогон.
    db.add_client(2, expire_in=timedelta(days=20))
    db.users[2]["purchase_discount"] = 10
    db.add_grant(2, granted_at=NOW - timedelta(minutes=3))

    first = await run(db, s)
    second = await run(db, s)

    assert first["resent"] == 1 and second["resent"] == 0
    assert [uid for uid, _ in s.tg] == [1]
    assert db.grant_of(1)[0]["tg_status"] == "sent"
    assert db.grant_of(2)[0]["notified_at"] is None


@pytest.mark.asyncio
async def test_claim_lost_to_parallel_run_sends_nothing():
    db, s = FakeDb(), Senders()
    db.add_client(1, expire_in=timedelta(days=20))
    db.users[1]["purchase_discount"] = 10
    grant = db.add_grant(1, granted_at=NOW - timedelta(minutes=30))
    db.hooks[rd.CLAIM_SQL] = lambda d, p: d._grant(grant["id"]).update(notified_at=NOW)

    report = await run(db, s)

    assert report["already_claimed"] == 1
    assert s.tg == [] and s.push == []


@pytest.mark.asyncio
async def test_burnt_grant_is_not_resent():
    db, s = FakeDb(), Senders()
    db.add_client(1, expire_in=timedelta(days=20))
    db.users[1]["purchase_discount"] = 10
    db.add_grant(1, granted_at=NOW - timedelta(days=5), expires_at=NOW - timedelta(minutes=1))

    report = await run(db, s)

    assert s.tg == [] and report["resent"] == 0
    assert db.grants[0]["status"] == "expired"
    assert db.users[1]["purchase_discount"] == 0


# ── Погашение ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_disabled_feature_still_burns_own_discounts_only():
    db, s = FakeDb(), Senders()
    db.add_client(1)  # подходит под выдачу — но фича выключена
    db.add_client(2, expire_in=timedelta(days=20))
    db.users[2]["purchase_discount"] = 10
    db.add_grant(2, granted_at=NOW - timedelta(days=5), expires_at=NOW - timedelta(hours=1))
    # Нашу 10 % у человека уже сменил win-back на 20 %.
    db.add_client(3, expire_in=timedelta(days=20))
    db.users[3]["purchase_discount"] = 20
    db.add_grant(3, granted_at=NOW - timedelta(days=5), expires_at=NOW - timedelta(hours=1))

    report = await run(db, s, conf=cfg(enabled=False))

    assert report["granted"] == 0 and report["expired"] == 2
    assert db.grant_of(1) == [] and s.tg == [] and s.push == []
    assert db.users[2]["purchase_discount"] == 0
    assert db.users[3]["purchase_discount"] == 20
    assert {g["status"] for g in db.grants} == {"expired"}


@pytest.mark.asyncio
async def test_usage_needs_discounted_payment_and_catches_late_one():
    db, s = FakeDb(), Senders()
    for uid in (1, 2, 3):
        db.add_client(uid, expire_in=timedelta(days=20))
    used = db.add_grant(1, granted_at=NOW - timedelta(days=1), notified_at=NOW)
    plain = db.add_grant(2, granted_at=NOW - timedelta(days=1), notified_at=NOW)
    db.users[2]["purchase_discount"] = 10
    # Счёт со скидкой выставлен до сгорания, оплачен после: выдача уже expired.
    late = db.add_grant(
        3,
        granted_at=NOW - timedelta(days=6),
        expires_at=NOW - timedelta(days=1),
        status="expired",
        closed_at=NOW - timedelta(days=1),
        notified_at=NOW - timedelta(days=6),
    )
    db.users[3]["purchase_discount"] = 0
    db.completed += [
        {"id": 501, "user_id": 1, "created_at": NOW - timedelta(hours=5), "discount_percent": 10},
        {"id": 502, "user_id": 2, "created_at": NOW - timedelta(hours=5), "discount_percent": 0},
        {"id": 503, "user_id": 3, "created_at": NOW - timedelta(days=1, hours=2), "discount_percent": 10},
    ]

    report = await run(db, s, conf=cfg(enabled=False))

    assert report["used"] == 2
    assert db._grant(used["id"])["status"] == "used"
    assert db._grant(used["id"])["transaction_id"] == 501
    assert db._grant(plain["id"])["status"] == "active"
    assert db._grant(late["id"])["status"] == "used"
    assert db._grant(late["id"])["transaction_id"] == 503
    assert db.users[3]["purchase_discount"] == 0
    assert db.users[2]["purchase_discount"] == 10


# ── Сообщение ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_telegram_payload_has_renew_and_close_buttons_and_stays():
    db, s = FakeDb(), Senders()
    db.add_client(1)

    await run(db, s, conf=cfg(percent=15))

    [(_, payload)] = s.tg
    assert payload.i18n_key == "raw-message"
    assert payload.disable_default_markup is False
    assert payload.delete_after is None
    assert payload.reply_markup == get_renew_keyboard()
    assert "15%" in payload.i18n_kwargs["content"]
    [(_, lang, messages)] = s.push
    title, body = messages["ru"]
    assert "15%" in title and "{" not in title + body


@pytest.mark.asyncio
async def test_example_goes_to_admin_as_client_and_grants_nothing():
    db = FakeDb()
    db.add_client(7)
    admin = UserDto(id=7, name="Владелец", role=Role.OWNER, telegram_id=4242, language=Locale.RU)
    got: list[Any] = []
    pushed: list[Any] = []

    async def notify_user(user: Any, payload: Any) -> Any:
        got.append((user, payload))
        return object()

    async def send_push(user: Any, messages: dict) -> int:
        pushed.append((user, messages))
        return 2

    result = await rd.send_example(admin, cfg(), notify_user=notify_user, send_push=send_push, now=NOW)

    assert result == {"telegram": "sent", "push": 2}
    [(user, payload)] = got
    assert user.role == Role.USER and user.id == 7 and user.telegram_id == 4242
    assert admin.role == Role.OWNER
    assert payload.delete_after is None and payload.reply_markup == get_renew_keyboard()
    assert db.executed == [] and db.grants == []
    assert db.users[7]["purchase_discount"] == 0

    no_tg = UserDto(id=8, name="Админ", role=Role.ADMIN, telegram_id=None)
    got.clear()
    result = await rd.send_example(no_tg, cfg(), notify_user=notify_user, send_push=send_push, now=NOW)
    assert result["telegram"] == "no_telegram" and got == []
