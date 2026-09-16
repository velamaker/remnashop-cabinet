"""Сводка письмом на настоящем Postgres: выборки и журнал отправок.

ЗАЧЕМ ОТДЕЛЬНО. Строковый страж в test_digest_email.py видит только, что условия
на месте, но не то, что они значат. Здесь — ровно то, на чём держится «одному
человеку сводка приходит один раз»:
  * выборка писем и выборка Telegram-части на одних и тех же людях не
    пересекаются, и письмо получает ровно тот, кому оно положено;
  * два одновременных прохода занимают строку журнала ровно один раз
    (INSERT … ON CONFLICT DO NOTHING RETURNING под уникальным ключом).

Прод-таблицы не трогаются: всё во временной схеме `digest_t_<hex>` со своими
минимальными users/subscriptions/push_subscriptions и DDL, взятым из самой
миграции 0007. Первый тест целиком в транзакции с ROLLBACK; второму нужны два
соединения, поэтому схема удаляется в finally. Гонять против одноразового
Postgres, а не против боевой базы.

Запуск (opt-in):
  RS_PG_DSN=postgresql://postgres:…@<одноразовый-pg>:5432/postgres \\
      pytest tests/test_digest_email_pg.py -v --asyncio-mode=auto
"""

import asyncio
import importlib
import os
import uuid
from types import SimpleNamespace

import pytest

asyncpg = pytest.importorskip("asyncpg")

DSN = os.environ.get("RS_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="RS_PG_DSN не задан — нет связи с Postgres")

de = importlib.import_module("src.infrastructure.services.overlay_digest_email")
migration = importlib.import_module(
    "src.infrastructure.database.migrations_overlay.versions.0007_digest_email"
)

MONTH = "2026-10"

_BASE_DDL = (
    "CREATE TABLE users ("
    "  id serial PRIMARY KEY, role varchar(20) NOT NULL, language varchar(8) NOT NULL DEFAULT 'RU',"
    "  telegram_id bigint, email varchar(255), is_email_verified boolean NOT NULL DEFAULT false,"
    "  is_blocked boolean NOT NULL DEFAULT false, current_subscription_id integer)",
    "CREATE TABLE subscriptions ("
    "  id serial PRIMARY KEY, user_id integer NOT NULL, status varchar(20) NOT NULL, user_remna_id uuid)",
    "CREATE TABLE push_subscriptions (id bigserial PRIMARY KEY, user_id integer NOT NULL)",
)


def _migration_ddl() -> list[str]:
    """DDL ровно из 0007: подменяем alembic.op сборщиком и зовём upgrade()."""
    statements: list[str] = []
    original = migration.op
    migration.op = SimpleNamespace(execute=statements.append)
    try:
        migration.upgrade()
    finally:
        migration.op = original
    return statements


async def _create_schema(conn, schema: str) -> None:
    await conn.execute(f"CREATE SCHEMA {schema}")
    await conn.execute(f"SET search_path TO {schema}")
    for ddl in _BASE_DDL:
        await conn.execute(ddl)
    # Дважды — миграция обязана быть идемпотентной (adopt-existing в migration-e2e).
    for ddl in _migration_ddl() + _migration_ddl():
        await conn.execute(ddl)


async def _person(
    conn,
    name: str,
    *,
    role: str = "USER",
    telegram_id=None,
    email: str | None = None,
    verified: bool = False,
    blocked: bool = False,
    status: str = "ACTIVE",
    remna: bool = True,
    push: bool = False,
    opted_out: bool = False,
) -> tuple[str, int]:
    uid = await conn.fetchval(
        "INSERT INTO users (role, telegram_id, email, is_email_verified, is_blocked) "
        "VALUES ($1, $2, $3, $4, $5) RETURNING id",
        role, telegram_id, email, verified, blocked,
    )
    sid = await conn.fetchval(
        "INSERT INTO subscriptions (user_id, status, user_remna_id) VALUES ($1, $2, $3) RETURNING id",
        uid, status, uuid.uuid4() if remna else None,
    )
    await conn.execute("UPDATE users SET current_subscription_id = $1 WHERE id = $2", sid, uid)
    if push:
        await conn.execute("INSERT INTO push_subscriptions (user_id) VALUES ($1)", uid)
    if opted_out:
        await conn.execute("INSERT INTO email_opt_outs (user_id, kind) VALUES ($1, 'digest')", uid)
    return name, uid


@pytest.mark.asyncio
async def test_audiences_disjoint_and_exact() -> None:
    conn = await asyncpg.connect(DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        await _create_schema(conn, "digest_t_" + uuid.uuid4().hex[:10])
        people = dict(
            [
                await _person(conn, "email", email="a@example.test", verified=True),
                await _person(conn, "tg", telegram_id=777, email="b@example.test", verified=True),
                await _person(conn, "push", email="c@example.test", verified=True, push=True),
                await _person(conn, "unverified", email="d@example.test"),
                await _person(conn, "blocked", email="e@example.test", verified=True, blocked=True),
                await _person(conn, "opted_out", email="f@example.test", verified=True, opted_out=True),
                await _person(conn, "admin", role="ADMIN", email="g@example.test", verified=True),
                await _person(conn, "expired", status="EXPIRED", email="h@example.test", verified=True),
                await _person(conn, "no_remna", remna=False, email="i@example.test", verified=True),
                await _person(conn, "no_email"),
            ]
        )
        email_ids = {r[0] for r in await conn.fetch(de.EMAIL_AUDIENCE_SQL)}
        tg_ids = {r[0] for r in await conn.fetch(de.TG_AUDIENCE_SQL)}

        assert email_ids == {people["email"]}
        assert tg_ids == {people["tg"], people["push"]}
        assert email_ids.isdisjoint(tg_ids)
        # Счётчик карточки считает ту же выборку.
        assert await conn.fetchval(f"SELECT count(*) FROM ({de.EMAIL_AUDIENCE_SQL}) a") == 1
    finally:
        await tr.rollback()
        await conn.close()


@pytest.mark.asyncio
async def test_db_ledger_claim_once_concurrently() -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    schema = "digest_t_" + uuid.uuid4().hex[:10]
    conn = await asyncpg.connect(DSN)
    engine = None
    try:
        await _create_schema(conn, schema)
        _, uid = await _person(conn, "email", email="a@example.test", verified=True)

        dsn = DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
        engine = create_async_engine(dsn, connect_args={"server_settings": {"search_path": schema}})
        async with AsyncSession(engine) as first, AsyncSession(engine) as second:
            results = await asyncio.gather(
                de.DbLedger(first).claim(uid, MONTH),
                de.DbLedger(second).claim(uid, MONTH),
            )
            assert sorted(results) == [False, True]

            ledger = de.DbLedger(first)
            assert await ledger.claim(uid, MONTH) is False
            await ledger.finish(uid, MONTH, de.FAILED, "x" * 1000)
            assert await ledger.status(uid, MONTH) == de.FAILED
            # Над занятой строкой over_limit ничего не перетирает.
            assert await ledger.mark_if_absent(uid, MONTH, de.OVER_LIMIT) is False
            assert await ledger.status(uid, MONTH) == de.FAILED

            summary = await de.status_summary(first)
            assert summary is not None and summary["month"] == MONTH and summary[de.FAILED] == 1

            await de.set_opt_out(first, uid, True)
            await de.set_opt_out(first, uid, True)
            await first.commit()
            assert await de.is_opted_out(first, uid) is True
            assert await de.opted_out_count(first) == 1
            assert await de.audience_count(first) == 0
    finally:
        if engine is not None:
            await engine.dispose()
        await conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await conn.close()
