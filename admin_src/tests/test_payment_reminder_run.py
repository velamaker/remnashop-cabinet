"""Прогон крона напоминаний: порядок записи, отправка и сбои.

ЧТО ЗАПИРАЕМ (правила «кому» — в test_payment_reminder_rules.py):
  * выключенная фича не читает базу и ничего не шлёт;
  * по одному счёту сообщение уходит РОВНО один раз, даже если прогон повторить;
  * захват идёт ДО отправки: упали на отправке — второго сообщения не будет;
  * сбой у одного человека не мешает следующему;
  * потолок на прогон соблюдается;
  * окончательные отказы записываются с причиной, временные (`too_early`) — нет,
    иначе счёт, до которого просто не дошло время, никогда бы не получил сообщения;
  * кнопка ведёт в кабинет, а не на старый счёт.

База подделана: запросы узнаются по константам модуля, строки живут в памяти.
Настоящий SQL на настоящей схеме гоняет test_payment_reminder_pg.py.
"""

import importlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

import pytest

pr = importlib.import_module("src.infrastructure.services.overlay_payment_reminder")
task = importlib.import_module("src.infrastructure.taskiq.tasks.payment_reminder")

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


def cfg(**over):
    return pr.normalize({**pr.DEFAULT_CONFIG, "enabled": True, **over})


def row(**over) -> dict:
    base = dict(
        payment_id="11111111-1111-1111-1111-111111111111",
        user_id=7,
        telegram_id=123456,
        lang="ru",
        is_blocked=False,
        is_bot_blocked=False,
        is_test=False,
        role="USER",
        plan_id=5,
        plan_name="HOME",
        amount=Decimal("339"),
        currency="RUB",
        created_at=NOW - timedelta(minutes=12),
        paid_after=False,
        newer_txn=False,
        sub_touched=False,
        opted_out=False,
        reminded_24h=False,
        reminded_30d=0,
        already_row=False,
    )
    base.update(over)
    return base


class FakeResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = [_Row(r) for r in rows]

    def all(self) -> list:
        return list(self._rows)


class _Row:
    def __init__(self, data: dict) -> None:
        self._mapping = data


class FakeSession:
    """Помнит, что и в каком порядке исполнялось, и умеет ломаться по команде."""

    def __init__(self, rows: list[dict], fail_on: Optional[str] = None) -> None:
        self.rows = rows
        self.fail_on = fail_on
        self.log: list[tuple[str, dict]] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement: Any, params: Optional[dict] = None):
        sql = str(getattr(statement, "text", statement))
        if sql.strip().startswith("SELECT t.payment_id"):
            self.log.append(("candidates", params or {}))
            return FakeResult(self.rows)
        name = (
            "claim" if "status, \n" in sql or "'claimed'" in sql
            else "skip" if "'skipped'" in sql
            else "result" if sql.strip().startswith("UPDATE payment_reminders")
            else "other"
        )
        self.log.append((name, params or {}))
        if self.fail_on == name:
            raise RuntimeError("база отказала")
        return FakeResult([])

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


async def noop_sleep(_seconds: float) -> None:
    return None


def kinds(session: FakeSession) -> list[str]:
    return [name for name, _ in session.log]


async def test_disabled_does_not_touch_the_database():
    session = FakeSession([row()])
    sent: list = []

    async def send(*args):
        sent.append(args)
        return task.TG_SENT

    report = await task.run_once(session, send_tg=send, now=NOW, cfg=pr.normalize({}), sleep=noop_sleep)
    assert report["disabled"] is True
    assert session.log == [] and sent == []


async def test_one_message_and_it_is_claimed_before_sending():
    session = FakeSession([row()])
    order: list[str] = []

    async def send(user_id, html, url):
        order.append("send")
        assert url.endswith("/billing")
        assert "Оплата не завершилась" in html
        return task.TG_SENT

    report = await task.run_once(
        session, send_tg=send, cabinet_url="https://cab.example", now=NOW, cfg=cfg(), sleep=noop_sleep
    )
    assert report["sent"] == 1
    assert kinds(session) == ["candidates", "claim", "result"]
    # Захват записан и закоммичен ДО отправки — рестарт не даст второго сообщения.
    assert session.log[1][0] == "claim" and order == ["send"]


async def test_second_run_sends_nothing_because_the_row_exists():
    """Во второй раз строка уже есть — выборка отдаёт already_row."""
    session = FakeSession([row(already_row=True)])

    async def send(*args):
        raise AssertionError("второго сообщения быть не должно")

    report = await task.run_once(session, send_tg=send, now=NOW, cfg=cfg(), sleep=noop_sleep)
    assert report["sent"] == 0 and report["skipped"]["already_handled"] == 1
    assert kinds(session) == ["candidates"]


async def test_failed_delivery_is_written_and_not_retried():
    session = FakeSession([row()])

    async def send(*args):
        raise RuntimeError("телеграм молчит")

    report = await task.run_once(session, send_tg=send, now=NOW, cfg=cfg(), sleep=noop_sleep)
    assert report["failed"] == 1 and report["sent"] == 0
    assert kinds(session) == ["candidates", "claim", "result"]
    assert session.log[-1][1]["status"] == "failed"


async def test_one_broken_person_does_not_stop_the_rest():
    rows = [row(payment_id=f"1111111{i}-1111-1111-1111-111111111111", user_id=i) for i in (1, 2)]
    session = FakeSession(rows)
    calls: list[int] = []

    async def send(user_id, html, url):
        calls.append(user_id)
        if user_id == 1:
            raise RuntimeError("не дошло")
        return task.TG_SENT

    report = await task.run_once(session, send_tg=send, now=NOW, cfg=cfg(), sleep=noop_sleep)
    assert calls == [1, 2]
    assert report["sent"] == 1 and report["failed"] == 1


async def test_final_skips_are_recorded_but_temporary_are_not():
    rows = [
        row(payment_id="a1111111-1111-1111-1111-111111111111", paid_after=True),
        row(payment_id="b1111111-1111-1111-1111-111111111111", created_at=NOW - timedelta(minutes=2)),
    ]
    session = FakeSession(rows)

    async def send(*args):
        raise AssertionError("никому писать не надо")

    report = await task.run_once(session, send_tg=send, now=NOW, cfg=cfg(), sleep=noop_sleep)
    assert report["skipped"]["paid"] == 1 and report["skipped"]["too_early"] == 1
    # Записан ровно один отказ — «уже заплатил». «Рано» вернётся через пять минут.
    assert kinds(session).count("skip") == 1
    assert session.log[1][1]["skip_reason"] == "paid"


async def test_run_cap():
    rows = [
        row(payment_id=f"{i:08d}-1111-1111-1111-111111111111", user_id=i)
        for i in range(task.MAX_PER_RUN + 5)
    ]
    session = FakeSession(rows)

    async def send(*args):
        return task.TG_SENT

    report = await task.run_once(session, send_tg=send, now=NOW, cfg=cfg(), sleep=noop_sleep)
    assert report["sent"] == task.MAX_PER_RUN
    assert report["skipped"]["run_cap"] == 5


async def test_claim_failure_skips_the_person_without_sending():
    session = FakeSession([row()], fail_on="claim")

    async def send(*args):
        raise AssertionError("без захвата слать нельзя")

    report = await task.run_once(session, send_tg=send, now=NOW, cfg=cfg(), sleep=noop_sleep)
    assert report["errors"] == 1 and report["sent"] == 0
    assert session.rollbacks >= 1


async def test_window_parameters_reach_the_query():
    session = FakeSession([])

    async def send(*args):
        return task.TG_SENT

    await task.run_once(session, send_tg=send, now=NOW, cfg=cfg(delay_minutes=7, max_age_minutes=30), sleep=noop_sleep)
    params = session.log[0][1]
    assert params["window_end"] == NOW - timedelta(minutes=7)
    assert params["window_start"] == NOW - timedelta(minutes=30)
    assert params["optout_kind"] == pr.OPTOUT_KIND
