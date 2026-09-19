"""Напоминание об оплате на НАСТОЯЩЕМ Postgres: SQL, замки и гонки.

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ. Подделка сессии отвечает по куску текста запроса: она не знает
ни про enum-колонки, ни про PRIMARY KEY, ни про параллельные прогоны. Именно на этом
в докупке трафика чуть не уехал блокер — `status::text` против enum давал
DatatypeMismatchError только на живой базе. Здесь живая схема с теми же enum-типами,
что создаёт миграция базы 0001, и наши таблицы из миграции 0013.

ЧТО ЗАПЕРТО:
  * выборка кандидатов действительно исключает оплативших, ушедших в другой шлюз,
    персонал, отказавшихся и тех, кому уже писали;
  * повторный захват одного счёта невозможен (PRIMARY KEY), и второй прогон не шлёт;
  * счётчики «за сутки» и «за 30 дней» считаются по отправленным, а не по строкам;
  * отказ от напоминаний пишется один раз и не падает при повторном нажатии.

ЗАПУСК — ПО ЖЕЛАНИЮ: нужен одноразовый Postgres (RS_PG_DSN), иначе тесты пропускаются.
Данные синтетические.
"""

import importlib
import os
import uuid as uuid_lib
from datetime import datetime, timedelta, timezone

import pytest

from _pg_dsn import sqlalchemy_dsn  # noqa: E402 — соседний модуль тестов

pr = importlib.import_module("src.infrastructure.services.overlay_payment_reminder")

DSN = sqlalchemy_dsn()
pytestmark = pytest.mark.skipif(not DSN, reason="нужен RS_PG_DSN (одноразовый Postgres)")

SCHEMA_NAME = "payment_reminder_pg_test"
NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)

SCHEMA = f"""
DROP SCHEMA IF EXISTS {SCHEMA_NAME} CASCADE;
CREATE SCHEMA {SCHEMA_NAME};
CREATE TYPE {SCHEMA_NAME}.transaction_status AS ENUM
    ('PENDING','COMPLETED','FAILED','CANCELED','REFUNDED');
CREATE TYPE {SCHEMA_NAME}.user_role AS ENUM ('USER','ADMIN','DEV','OWNER','PREVIEW');
CREATE TABLE {SCHEMA_NAME}.users (
    id SERIAL PRIMARY KEY,
    role {SCHEMA_NAME}.user_role NOT NULL DEFAULT 'USER',
    telegram_id BIGINT,
    language VARCHAR(8) DEFAULT 'RU',
    is_blocked BOOLEAN NOT NULL DEFAULT false,
    is_bot_blocked BOOLEAN NOT NULL DEFAULT false);
CREATE TABLE {SCHEMA_NAME}.subscriptions (
    id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE {SCHEMA_NAME}.transactions (
    payment_id UUID PRIMARY KEY, user_id INTEGER NOT NULL,
    status {SCHEMA_NAME}.transaction_status NOT NULL DEFAULT 'PENDING',
    is_test BOOLEAN NOT NULL DEFAULT false,
    plan_snapshot JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    pricing JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    currency VARCHAR(8) NOT NULL DEFAULT 'RUB',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE {SCHEMA_NAME}.payment_reminders (
    payment_id UUID PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES {SCHEMA_NAME}.users(id) ON DELETE CASCADE,
    kind VARCHAR(16) NOT NULL, amount NUMERIC(12,2), currency VARCHAR(8),
    status VARCHAR(16) NOT NULL DEFAULT 'new', skip_reason VARCHAR(32),
    invoice_at TIMESTAMPTZ NOT NULL, claimed_at TIMESTAMPTZ, sent_at TIMESTAMPTZ,
    tg_result VARCHAR(16), paid_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_pr_status CHECK (status IN ('new','claimed','sent','skipped','failed')));
CREATE TABLE {SCHEMA_NAME}.notification_optouts (
    user_id INTEGER NOT NULL REFERENCES {SCHEMA_NAME}.users(id) ON DELETE CASCADE,
    kind VARCHAR(32) NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, kind));
"""


@pytest.fixture
async def db():
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy import text

    engine = create_async_engine(DSN)
    async with engine.begin() as conn:
        for statement in SCHEMA.strip().split(";\n"):
            if statement.strip():
                await conn.execute(text(statement))
    session_engine = create_async_engine(
        DSN, connect_args={"server_settings": {"search_path": SCHEMA_NAME}}
    )
    async with AsyncSession(session_engine) as session:
        yield session
    await session_engine.dispose()
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA_NAME} CASCADE"))
    await engine.dispose()


async def _user(session, **over) -> int:
    from sqlalchemy import text

    fields = {"role": "USER", "telegram_id": 111, "is_blocked": False, "is_bot_blocked": False}
    fields.update(over)
    row = await session.execute(
        text(
            "INSERT INTO users (role, telegram_id, is_blocked, is_bot_blocked) "
            "VALUES (CAST(:role AS user_role), :telegram_id, :is_blocked, :is_bot_blocked) RETURNING id"
        ),
        fields,
    )
    await session.commit()
    return int(row.scalar_one())


async def _invoice(session, user_id: int, *, minutes_ago: int = 12, status: str = "PENDING",
                   plan_id: int = 5, is_test: bool = False, amount: str = "339") -> str:
    from sqlalchemy import text

    payment_id = str(uuid_lib.uuid4())
    await session.execute(
        text(
            "INSERT INTO transactions (payment_id, user_id, status, is_test, plan_snapshot, pricing, created_at, updated_at) "
            "VALUES (CAST(:pid AS uuid), :uid, CAST(:st AS transaction_status), :test, "
            "CAST(:plan AS jsonb), CAST(:pricing AS jsonb), :created, :created)"
        ),
        {
            "pid": payment_id, "uid": user_id, "st": status, "test": is_test,
            "plan": f'{{"id": {plan_id}, "name": "HOME"}}',
            "pricing": f'{{"final_amount": "{amount}"}}',
            "created": NOW - timedelta(minutes=minutes_ago),
        },
    )
    await session.commit()
    return payment_id


async def _candidates(session, cfg=None):
    from sqlalchemy import text

    cfg = cfg or pr.normalize({**pr.DEFAULT_CONFIG, "enabled": True})
    start, end = pr.window_bounds(cfg, NOW)
    rows = (
        await session.execute(
            text(pr.CANDIDATES_SQL),
            {
                "window_start": start, "window_end": end, "optout_kind": pr.OPTOUT_KIND,
                "cooldown_since": NOW - timedelta(hours=int(cfg["cooldown_hours"])),
                "month_since": NOW - timedelta(days=30), "limit": 100,
            },
        )
    ).all()
    await session.rollback()
    return [dict(r._mapping) for r in rows]


async def test_plain_invoice_is_a_candidate(db):
    user_id = await _user(db)
    payment_id = await _invoice(db, user_id)
    rows = await _candidates(db)
    assert [r["payment_id"] for r in rows] == [payment_id]
    row = rows[0]
    assert row["paid_after"] is False and row["newer_txn"] is False and row["already_row"] is False
    assert str(row["amount"]) == "339" and row["plan_id"] == 5


async def test_paid_person_is_excluded(db):
    """Оплата соседнего счёта не гасит этот — и именно поэтому смотрим на человека."""
    user_id = await _user(db)
    await _invoice(db, user_id)
    await _invoice(db, user_id, minutes_ago=11, status="COMPLETED")
    rows = await _candidates(db)
    assert all(r["paid_after"] for r in rows)


async def test_newer_attempt_is_visible(db):
    user_id = await _user(db)
    await _invoice(db, user_id, minutes_ago=20)
    await _invoice(db, user_id, minutes_ago=12)
    rows = sorted(await _candidates(db), key=lambda r: r["created_at"])
    assert rows[0]["newer_txn"] is True
    assert rows[-1]["newer_txn"] is False


async def test_optout_and_staff_and_test_are_flagged(db):
    staff = await _user(db, role="OWNER")
    await _invoice(db, staff)
    quiet = await _user(db)
    await _invoice(db, quiet)
    from sqlalchemy import text

    await db.execute(text(pr.OPTOUT_SQL), {"user_id": quiet, "kind": pr.OPTOUT_KIND})
    # Повторное нажатие «Не напоминать» не падает.
    await db.execute(text(pr.OPTOUT_SQL), {"user_id": quiet, "kind": pr.OPTOUT_KIND})
    await db.commit()
    tester = await _user(db)
    await _invoice(db, tester, is_test=True, plan_id=-1)

    by_user = {r["user_id"]: r for r in await _candidates(db)}
    assert by_user[staff]["role"] == "OWNER"
    assert by_user[quiet]["opted_out"] is True
    assert by_user[tester]["is_test"] is True


async def test_claim_is_taken_once_even_if_the_run_repeats(db):
    from sqlalchemy import text

    user_id = await _user(db)
    payment_id = await _invoice(db, user_id)
    params = {
        "payment_id": payment_id, "user_id": user_id, "kind": "plan",
        "amount": 339, "currency": "RUB", "invoice_at": NOW - timedelta(minutes=12),
    }
    await db.execute(text(pr.CLAIM_SQL), params)
    await db.commit()
    # Второй захват того же счёта не создаёт второй строки — это PRIMARY KEY, а не проверка в коде.
    await db.execute(text(pr.CLAIM_SQL), params)
    await db.commit()
    total = (await db.execute(text("SELECT count(*) FROM payment_reminders"))).scalar_one()
    assert total == 1
    # И выборка теперь честно говорит «уже разобрано».
    assert (await _candidates(db))[0]["already_row"] is True


async def test_cooldown_and_month_counters_count_sent_only(db):
    from sqlalchemy import text

    user_id = await _user(db)
    # Отправленное вчера — в месячный счётчик попадает, в суточный нет.
    await db.execute(
        text(
            "INSERT INTO payment_reminders (payment_id, user_id, kind, status, invoice_at, sent_at) "
            "VALUES (gen_random_uuid(), :uid, 'plan', 'sent', :t, :t)"
        ),
        {"uid": user_id, "t": NOW - timedelta(days=2)},
    )
    # Пропущенное не считается вовсе: человек ничего не получал.
    await db.execute(
        text(
            "INSERT INTO payment_reminders (payment_id, user_id, kind, status, skip_reason, invoice_at) "
            "VALUES (gen_random_uuid(), :uid, 'plan', 'skipped', 'paid', :t)"
        ),
        {"uid": user_id, "t": NOW - timedelta(hours=1)},
    )
    await db.commit()
    await _invoice(db, user_id)
    row = (await _candidates(db))[0]
    assert row["reminded_24h"] is False
    assert row["reminded_30d"] == 1


async def test_result_update_marks_sent(db):
    from sqlalchemy import text

    user_id = await _user(db)
    payment_id = await _invoice(db, user_id)
    await db.execute(
        text(pr.CLAIM_SQL),
        {"payment_id": payment_id, "user_id": user_id, "kind": "plan", "amount": 339,
         "currency": "RUB", "invoice_at": NOW - timedelta(minutes=12)},
    )
    await db.execute(
        text(pr.RESULT_SQL),
        {"payment_id": payment_id, "status": "sent", "tg_result": "sent", "sent": True},
    )
    await db.commit()
    row = (
        await db.execute(text("SELECT status, sent_at FROM payment_reminders"))
    ).first()
    assert row[0] == "sent" and row[1] is not None


async def test_failed_result_does_not_set_sent_at(db):
    """`failed` не считается отправленным: иначе кулдаун запретил бы честную попытку."""
    from sqlalchemy import text

    user_id = await _user(db)
    payment_id = await _invoice(db, user_id)
    await db.execute(
        text(pr.CLAIM_SQL),
        {"payment_id": payment_id, "user_id": user_id, "kind": "plan", "amount": 339,
         "currency": "RUB", "invoice_at": NOW - timedelta(minutes=12)},
    )
    await db.execute(
        text(pr.RESULT_SQL),
        {"payment_id": payment_id, "status": "failed", "tg_result": "failed", "sent": False},
    )
    await db.commit()
    row = (await db.execute(text("SELECT status, sent_at FROM payment_reminders"))).first()
    assert row[0] == "failed" and row[1] is None
