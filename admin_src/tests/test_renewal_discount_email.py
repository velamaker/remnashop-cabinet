"""Скидка на продление в письме «подписка скоро закончится» (email_expiry_reminders).

ЧТО ЗАПИРАЕМ. Людям только с почтой о скидке сообщает лишь письмо за 72 ч. Срок
скидки короче срока до конца подписки (например, «за 7 дней» при сроке 120 ч) —
и она сгорает за двое суток до конца. Письмо, пообещавшее её «пока подписка не
закончилась», отправило бы человека платить в последний день полную цену. Здесь
проверяется вся цепочка задачи: выборка подписок → действующие выдачи со сроком →
текст письма.
  * скидка сгорает раньше подписки — письмо называет дату, а не «до конца»;
  * скидка живёт до конца подписки — «пока подписка не закончилась»;
  * в письме «сегодня» строки про скидку нет;
  * сгоревшая, но ещё не погашенная выдача в письмо не попадает.

База подделана: выборка подписок фильтруется по окну из самого запроса, выдачи
отдаются по тексту ACTIVE_BY_USER_SQL. Запуск — внутри образа бота (см. ci.yml).
"""

import importlib
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

rd = importlib.import_module("src.infrastructure.services.overlay_renewal_discount")
task = importlib.import_module("src.infrastructure.taskiq.tasks.email_expiry_reminders")

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return list(self._rows)

    def mappings(self) -> "_Result":
        return self


class FakeSession:
    """Подписки (user_id, email, expire_at) и выдачи (user_id, percent, expires_at)."""

    def __init__(self, subs: list[tuple], grants: list[dict]) -> None:
        self.subs = subs
        self.grants = grants

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        if str(statement) == rd.ACTIVE_BY_USER_SQL:
            p = dict(params or {})
            rows = [
                g for g in self.grants if g["user_id"] in p["ids"] and g["expires_at"] > p["now"]
            ]
            return _Result(rows)
        # Выборка подписок: окно точки напоминания берём из параметров самого запроса.
        bounds = sorted(v for v in statement.compile().params.values() if isinstance(v, datetime))
        assert len(bounds) == 2, f"ожидалось окно [lo; hi), а не {bounds}"
        lo, hi = bounds
        return _Result([s for s in self.subs if lo <= s[2] < hi])

    async def rollback(self) -> None:  # pragma: no cover — только при сбое чтения
        pass


class FakeSender:
    def __init__(self) -> None:
        self.sent: dict[str, dict] = {}

    async def send(self, *, to: str, subject: str, body: str) -> None:
        self.sent[to] = {"subject": subject, "body": body}


@pytest.fixture(autouse=True)
def brand(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task, "_brand", lambda: "Test VPN")


async def run(subs: list[tuple], grants: list[dict]) -> dict[str, dict]:
    sender = FakeSender()
    await task.send_reminders(FakeSession(subs, grants), sender, NOW)
    return sender.sent


def grant_for(user_id: int, conf: dict, expire_at: datetime) -> dict:
    """Выдача так, как её сделал бы крон за `days_before` до конца подписки."""
    granted_at = expire_at - timedelta(days=int(conf["days_before"])) + timedelta(hours=1)
    return {
        "user_id": user_id,
        "percent": int(conf["percent"]),
        "expires_at": rd.grant_expires_at(granted_at, conf, expire_at),
    }


def conf(**over: Any) -> dict:
    return rd._normalize({**rd.DEFAULT_CONFIG, "enabled": True, **over})


@pytest.mark.asyncio
async def test_letter_names_real_deadline_when_discount_burns_before_subscription_end():
    expire_at = NOW + timedelta(hours=72, minutes=30)
    c = conf(days_before=7, lifetime_hours=120)
    g = grant_for(1, c, expire_at)
    assert expire_at - g["expires_at"] > timedelta(hours=24)  # сценарий: сгорает сильно раньше

    sent = await run([(1, "short@example.test", expire_at)], [g])

    body = sent["short@example.test"]["body"]
    assert "скидка 10% на продление" in body
    assert "пока подписка не закончилась" not in body
    assert f"до {rd._email_deadline_ru(g['expires_at'])}." in body


@pytest.mark.asyncio
async def test_letter_promises_until_end_when_discount_lives_until_subscription_end():
    expire_at = NOW + timedelta(hours=72, minutes=30)
    c = conf(days_before=5, lifetime_hours=120)
    g = grant_for(2, c, expire_at)
    assert g["expires_at"] == expire_at

    sent = await run([(2, "full@example.test", expire_at)], [g])

    assert sent["full@example.test"]["body"].endswith("пока подписка не закончилась.")


@pytest.mark.asyncio
async def test_last_day_letter_and_burnt_grant_carry_no_discount():
    today = NOW + timedelta(hours=4, minutes=10)
    in_three_days = NOW + timedelta(hours=72, minutes=10)
    grants = [
        {"user_id": 3, "percent": 10, "expires_at": today},
        # Срок вышел, погашение ещё не прошло — скидку в письме не обещаем.
        {"user_id": 4, "percent": 10, "expires_at": NOW - timedelta(minutes=5)},
    ]
    sent = await run(
        [(3, "today@example.test", today), (4, "burnt@example.test", in_three_days)], grants
    )

    assert set(sent) == {"today@example.test", "burnt@example.test"}
    for letter in sent.values():
        assert "скидка" not in letter["body"]
