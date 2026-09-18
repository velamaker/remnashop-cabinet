"""Перенос остатка на настоящем Postgres: какие счета становятся слоями.

ЗАЧЕМ ОТДЕЛЬНО. Строковые стражи (test_plan_change_carry_sql_guards.py) видят только,
что условия на месте, а не то, что они значат. Здесь — ровно то, на чём держатся деньги:
  * слоями строки становятся продления ПОСЛЕ её создания и создающий счёт в окне
    [создание − 120 с; создание + 5 с]; продление за минуту ДО создания (уже ушло в
    перенос прошлой строки) и счёт прошлой строки — нет;
  * текущий счёт смены, тестовый счёт и счёт со скидкой 100% — не слои;
  * перенос из промокода отключает создающий слой, а продления берёт только после
    себя; перенос из покупки — создающий слой оставляет и сам ложится слоем;
  * резерв, пауза, возврат за год и цены по `ANY(:ids)` читаются как надо;
  * миграция 0010 идемпотентна, `payment_id` UNIQUE пускает несколько NULL;
  * запись журнала, поиск по источнику платежа, гашение паузы и замок — живым SQL.

Прод-таблицы не трогаются: всё во временной схеме со своими минимальными таблицами и
enum-типами базы и DDL, взятым из самой миграции 0010. Гонять против одноразового
Postgres, не против боевой базы.

Запуск (opt-in):
  RS_PG_DSN=postgresql://postgres:…@<одноразовый-pg>:5432/postgres \\
      pytest tests/test_plan_change_carry_pg.py -v --asyncio-mode=auto
"""

import importlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from types import SimpleNamespace

import pytest

asyncpg = pytest.importorskip("asyncpg")

from _pg_dsn import asyncpg_dsn  # noqa: E402 — соседний модуль тестов

# Строку под SQLAlchemy этот файл делает сам (_asyncpg_url ниже) — здесь нужен asyncpg.
DSN = asyncpg_dsn()
pytestmark = pytest.mark.skipif(not DSN, reason="RS_PG_DSN не задан — нет связи с Postgres")

carry = importlib.import_module("src.infrastructure.services.overlay_plan_change")
migration = importlib.import_module(
    "src.infrastructure.database.migrations_overlay.versions.0010_plan_change_carryovers"
)

DAY = 86400

_BASE_DDL = (
    "CREATE TYPE transaction_status AS ENUM ('PENDING', 'COMPLETED', 'CANCELED', 'REFUNDED', 'FAILED')",
    "CREATE TYPE purchase_type AS ENUM ('NEW', 'RENEW', 'CHANGE')",
    "CREATE TYPE currency AS ENUM ('USD', 'XTR', 'RUB')",
    "CREATE TYPE subscription_status AS ENUM ('ACTIVE', 'DISABLED', 'LIMITED', 'EXPIRED', 'DELETED')",
    "CREATE TABLE users (id serial PRIMARY KEY, current_subscription_id integer)",
    "CREATE TABLE subscriptions ("
    "  id serial PRIMARY KEY, user_id integer NOT NULL, status subscription_status NOT NULL,"
    "  is_trial boolean NOT NULL DEFAULT false, expire_at timestamptz NOT NULL,"
    "  plan_snapshot jsonb NOT NULL, created_at timestamptz NOT NULL)",
    "CREATE TABLE transactions ("
    "  id serial PRIMARY KEY, payment_id uuid NOT NULL UNIQUE, user_id integer NOT NULL,"
    "  status transaction_status NOT NULL, is_test boolean NOT NULL DEFAULT false,"
    "  purchase_type purchase_type NOT NULL, currency currency NOT NULL, pricing jsonb NOT NULL,"
    "  plan_snapshot jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),"
    "  updated_at timestamptz NOT NULL, gateway_display_name varchar(255))",
    "CREATE TABLE gift_payments (payment_id uuid PRIMARY KEY, user_id integer)",
    "CREATE TABLE plans (id integer PRIMARY KEY, is_active boolean NOT NULL)",
    "CREATE TABLE plan_durations (id serial PRIMARY KEY, plan_id integer NOT NULL, days integer NOT NULL)",
    "CREATE TABLE plan_prices (id serial PRIMARY KEY, plan_duration_id integer NOT NULL,"
    "  currency currency NOT NULL, price numeric(10,2) NOT NULL)",
    "CREATE TABLE subscription_freezes (user_id integer PRIMARY KEY, remaining_seconds bigint NOT NULL,"
    "  active boolean NOT NULL DEFAULT true)",
    "CREATE TABLE reserve_grants (id bigserial PRIMARY KEY, user_id integer NOT NULL,"
    "  reserve_expire_at timestamptz NOT NULL, ended boolean NOT NULL DEFAULT false)",
)


def _migration_ddl() -> list[str]:
    statements: list[str] = []
    original = migration.op
    migration.op = SimpleNamespace(execute=statements.append)
    try:
        migration.upgrade()
    finally:
        migration.op = original
    return statements


def _asyncpg_url(dsn: str) -> str:
    return dsn.replace("postgresql://", "postgresql+asyncpg://", 1).replace("postgres://", "postgresql+asyncpg://", 1)


@pytest.fixture
async def db():
    """Временная схема; сессия SQLAlchemy с search_path на неё. Схема удаляется после."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    schema = "carry_t_" + uuid.uuid4().hex[:10]
    conn = await asyncpg.connect(DSN)
    await conn.execute(f"CREATE SCHEMA {schema}")
    await conn.execute(f"SET search_path TO {schema}")
    for ddl in _BASE_DDL:
        await conn.execute(ddl)
    # Дважды — миграция обязана быть идемпотентной (adopt-existing в migration-e2e).
    for ddl in _migration_ddl() + _migration_ddl():
        await conn.execute(ddl)
    engine = create_async_engine(
        _asyncpg_url(DSN), connect_args={"server_settings": {"search_path": schema}}
    )
    session = AsyncSession(engine, expire_on_commit=False)
    try:
        yield SimpleNamespace(conn=conn, session=session, schema=schema)
    finally:
        await session.close()
        await engine.dispose()
        await conn.execute(f"DROP SCHEMA {schema} CASCADE")
        await conn.close()


NOW = datetime.now(timezone.utc).replace(microsecond=0)


async def person(conn, *, days_left: float = 100, created_ago: float = 20, plan_id: int = 10, status="ACTIVE"):
    uid = await conn.fetchval("INSERT INTO users DEFAULT VALUES RETURNING id")
    created = NOW - timedelta(days=created_ago)
    sid = await conn.fetchval(
        "INSERT INTO subscriptions (user_id, status, expire_at, plan_snapshot, created_at) "
        "VALUES ($1, $2, $3, $4, $5) RETURNING id",
        uid, status, NOW + timedelta(days=days_left), json.dumps({"id": plan_id, "duration": 30}), created,
    )
    await conn.execute("UPDATE users SET current_subscription_id = $1 WHERE id = $2", sid, uid)
    return uid, sid, created


async def invoice(conn, uid, *, kind, at, final="120", original=None, plan_id=10, duration=30,
                  status="COMPLETED", is_test=False, currency="RUB", payment_id=None, display=None):
    pid = payment_id or uuid.uuid4()
    await conn.execute(
        "INSERT INTO transactions (payment_id, user_id, status, is_test, purchase_type, currency, pricing, "
        "plan_snapshot, updated_at, gateway_display_name) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
        pid, uid, status, is_test, kind, currency,
        json.dumps({"final_amount": final, "original_amount": original or final, "discount_percent": 0}),
        json.dumps({"id": plan_id, "duration": duration}), at, display,
    )
    return pid


async def load(db, uid, *, exclude=None, extra=()):
    row = await carry.read_subscription_row(db.session, await db.conn.fetchval(
        "SELECT current_subscription_id FROM users WHERE id = $1", uid))
    state = await carry.load_carry_state(
        db.session, user_id=uid, subscription=row, now=NOW, exclude_payment_id=exclude, extra_plan_ids=extra,
    )
    await db.session.rollback()
    return row, state


async def test_layers_are_this_rows_renewals_and_its_creating_invoice(db):
    uid, sid, created = await person(db.conn, created_ago=20)
    renew_after = await invoice(db.conn, uid, kind="RENEW", at=created + timedelta(days=5), final="270", duration=90)
    create = await invoice(db.conn, uid, kind="NEW", at=created - timedelta(seconds=3))
    await invoice(db.conn, uid, kind="RENEW", at=created - timedelta(seconds=60))  # ДО строки — чужой
    await invoice(db.conn, uid, kind="NEW", at=created - timedelta(hours=1))  # прошлая строка

    _row, state = await load(db, uid)
    assert [(l.kind, l.payment_id) for l in state.layers] == [("renew", str(renew_after)), ("create", str(create))]
    assert state.layers[0].paid == 270 and state.layers[0].seconds == 90 * DAY


async def test_current_test_and_free_invoices_are_not_layers(db):
    uid, sid, created = await person(db.conn, created_ago=20)
    current = await invoice(db.conn, uid, kind="RENEW", at=created + timedelta(days=1))
    await invoice(db.conn, uid, kind="RENEW", at=created + timedelta(days=2), is_test=True)
    await invoice(db.conn, uid, kind="RENEW", at=created + timedelta(days=3), final="0", original="120")
    await invoice(db.conn, uid, kind="RENEW", at=created + timedelta(days=4), status="PENDING")
    _row, state = await load(db, uid, exclude=current)
    assert state.layers == ()
    _row, state = await load(db, uid)
    assert [l.payment_id for l in state.layers] == [str(current)]


async def test_promocode_carry_disables_create_and_cuts_renewals(db):
    uid, sid, created = await person(db.conn, created_ago=20)
    await invoice(db.conn, uid, kind="NEW", at=created - timedelta(seconds=2))
    await invoice(db.conn, uid, kind="RENEW", at=created + timedelta(days=2))  # до переноса — уже в нём
    renew_late = await invoice(db.conn, uid, kind="RENEW", at=created + timedelta(days=10))
    await db.conn.execute(
        "INSERT INTO plan_change_carryovers (payment_id, source, user_id, old_subscription_id, subscription_id, "
        "new_plan_id, new_duration, mode, currency, value_amount, new_day_price, bonus_days, expire_after, created_at) "
        "VALUES (NULL, 'promocode', $1, $2, $2, 20, 30, 'carry', 'RUB', 80, 8, 10, $3, $4)",
        uid, sid, NOW + timedelta(days=40), created + timedelta(days=5),
    )
    _row, state = await load(db, uid)
    assert [l.kind for l in state.layers] == ["renew", "carry"]
    assert state.layers[0].payment_id == str(renew_late)
    assert state.layers[1].paid == 80 and state.layers[1].seconds == 10 * DAY


async def test_purchase_carry_keeps_create_layer_and_rides_last(db):
    uid, sid, created = await person(db.conn, created_ago=20)
    create = await invoice(db.conn, uid, kind="CHANGE", at=created - timedelta(seconds=1))
    await db.conn.execute(
        "INSERT INTO plan_change_carryovers (payment_id, source, user_id, old_subscription_id, subscription_id, "
        "new_plan_id, new_duration, mode, currency, value_amount, new_day_price, bonus_days, expire_after, created_at) "
        "VALUES ($1, 'purchase', $2, 1, $3, 10, 30, 'carry', 'RUB', 116, 8, 14, $4, $5)",
        create, uid, sid, NOW + timedelta(days=44), created,
    )
    _row, state = await load(db, uid)
    assert [(l.kind, l.payment_id) for l in state.layers] == [("create", str(create)), ("carry", None)]
    assert state.layers[1].paid == 112, "доля дня, отброшенная округлением, не переносится"


async def test_reserve_pause_refund_and_prices_are_read(db):
    uid, sid, created = await person(db.conn, days_left=3)
    await db.conn.execute("INSERT INTO reserve_grants (user_id, reserve_expire_at) VALUES ($1, $2)", uid, NOW + timedelta(days=3))
    await db.conn.execute("INSERT INTO reserve_grants (user_id, reserve_expire_at, ended) VALUES ($1, $2, true)", uid, NOW + timedelta(days=9))
    await db.conn.execute("INSERT INTO subscription_freezes (user_id, remaining_seconds) VALUES ($1, $2)", uid, 12 * DAY)
    await invoice(db.conn, uid, kind="RENEW", at=NOW - timedelta(days=400), status="REFUNDED")
    await db.conn.execute("INSERT INTO plans (id, is_active) VALUES (10, true), (20, false), (30, true)")
    for plan_id, days, price in ((10, 30, "120.00"), (10, 365, "1095.00"), (20, 30, "240.00"), (30, 30, "999.00")):
        did = await db.conn.fetchval("INSERT INTO plan_durations (plan_id, days) VALUES ($1, $2) RETURNING id", plan_id, days)
        await db.conn.execute("INSERT INTO plan_prices (plan_duration_id, currency, price) VALUES ($1, 'RUB', $2)", did, Decimal(price))

    _row, state = await load(db, uid, extra=[20])
    assert state.reserve_expire_at is not None and abs((state.reserve_expire_at - (NOW + timedelta(days=3))).total_seconds()) < 1
    assert state.frozen_seconds == 12 * DAY
    assert state.refund_recent is False, "возврат старше года не отключает перенос"
    assert state.prices == {(10, 30, "RUB"): Fraction(120), (10, 365, "RUB"): Fraction(1095), (20, 30, "RUB"): Fraction(240)}
    assert state.active_plan_ids == frozenset({10})

    await invoice(db.conn, uid, kind="RENEW", at=NOW - timedelta(days=30), status="REFUNDED")
    _row, state = await load(db, uid)
    assert state.refund_recent is True


async def test_journal_write_lookup_freeze_and_lock(db):
    uid, sid, created = await person(db.conn, days_left=29)
    await db.conn.execute("INSERT INTO subscription_freezes (user_id, remaining_seconds) VALUES ($1, $2)", uid, DAY)
    source = uuid.uuid4()
    pid = uuid.uuid4()
    result = carry.CarryResult(
        mode="carry", remaining_seconds=29 * DAY, remaining_days=29, value=Fraction(116),
        new_day_price=Fraction(8), bonus_days=14, bonus_seconds=0, lost_days=0, capped=False,
        source_payment_ids=(str(source),), breakdown=({"kind": "create", "seconds": 29 * DAY},),
    )
    session = db.session
    row = await carry.lock_current_subscription(session, uid)
    assert row.id == sid and row.status == "ACTIVE" and row.plan_id == 10
    assert not await carry.carry_already_applied(session, pid)
    await carry.close_freeze(session, uid)
    for _ in range(2):  # повтор по тому же счёту не задваивает и не падает
        await carry.record_carryover(
            session, source="purchase", payment_id=pid, user_id=uid, old_subscription_id=sid,
            subscription_id=sid + 100, old_plan_id=10, new_plan_id=20, new_duration=30, currency="RUB",
            result=result, expire_before=row.expire_at, expire_after=NOW + timedelta(days=44),
        )
    await session.commit()

    assert await carry.carry_already_applied(session, pid)
    assert await db.conn.fetchval("SELECT count(*) FROM plan_change_carryovers") == 1
    assert await db.conn.fetchval("SELECT active FROM subscription_freezes WHERE user_id = $1", uid) is False
    found = await carry.carries_by_source_payment(session, source)
    assert [(c["subscription_id"], c["added_days"]) for c in found] == [(sid + 100, 14)]
    assert await carry.carries_by_source_payment(session, pid)  # сам счёт смены тоже источник
    assert await carry.carries_by_source_payment(session, uuid.uuid4()) == []
    after = await carry.carry_for_subscription(session, sid + 100)
    assert after["added_days"] == 14 and after["mode"] == "carry"
    stored = await db.conn.fetchrow(
        "SELECT value_amount, new_day_price, breakdown FROM plan_change_carryovers WHERE payment_id = $1", pid
    )
    assert stored["value_amount"] == Decimal("116.0000") and stored["new_day_price"] == Decimal("8.000000")
    await session.rollback()

    # Несколько переносов из промокодов (payment_id NULL) — UNIQUE им не мешает.
    for _ in range(2):
        await carry.record_carryover(
            session, source="promocode", payment_id=None, user_id=uid, old_subscription_id=sid,
            subscription_id=sid, old_plan_id=10, new_plan_id=20, new_duration=30, currency="RUB",
            result=result, expire_before=None, expire_after=NOW,
        )
    await session.commit()
    assert await db.conn.fetchval("SELECT count(*) FROM plan_change_carryovers WHERE payment_id IS NULL") == 2
    assert await carry.transaction_status(session, uuid.uuid4()) is None


async def test_promo_default_currency_and_prices(db):
    """Подарок: цена дня — из таблицы тарифа в валюте по умолчанию из settings."""
    await db.conn.execute("CREATE TABLE settings (id serial PRIMARY KEY, default_currency currency NOT NULL)")
    assert await carry.default_currency(db.session) == "RUB", "настроек нет — рубли"
    await db.session.rollback()
    await db.conn.execute("INSERT INTO settings (default_currency) VALUES ('XTR')")
    assert await carry.default_currency(db.session) == "XTR"
    await db.session.rollback()

    uid, sid, created = await person(db.conn, days_left=29, plan_id=10)
    await invoice(db.conn, uid, kind="NEW", at=created - timedelta(seconds=1), final="120", currency="XTR")
    await db.conn.execute("INSERT INTO plans (id, is_active) VALUES (10, true), (20, true)")
    did = await db.conn.fetchval("INSERT INTO plan_durations (plan_id, days) VALUES (20, 30) RETURNING id")
    await db.conn.execute("INSERT INTO plan_prices (plan_duration_id, currency, price) VALUES ($1, 'XTR', 240)", did)
    sub = SimpleNamespace(
        id=sid, expire_at=NOW + timedelta(days=29), status="ACTIVE", is_trial=False,
        plan_snapshot=SimpleNamespace(id=10), created_at=created,
    )
    carried = await carry.promo_carry(db.session, user_id=uid, subscription=sub, plan_id=20, duration=30, now=NOW)
    await db.session.rollback()
    assert carried.currency == "XTR"
    # 29 дн. по 4/день = 116 → подарок 240/30 = 8/день → 14 дн.
    assert carried.result.mode == "carry" and carried.result.bonus_days == 14


async def test_savepoint_isolates_failed_read_and_keeps_outer_work(db):
    """Как в зачислении: сбой в SAVEPOINT откатывает только его, внешняя работа коммитится."""
    uid, sid, created = await person(db.conn, days_left=29)
    session = db.session
    row = await carry.lock_current_subscription(session, uid)  # внешняя транзакция уже идёт
    try:
        async with session.begin_nested():
            await carry.record_carryover(
                session, source="purchase", payment_id=uuid.uuid4(), user_id=uid, old_subscription_id=sid,
                subscription_id=sid, old_plan_id=10, new_plan_id=20, new_duration=30, currency="RUB",
                result=carry.CarryResult("carry", 0, 0, Fraction(0), None, 0, 0, 0, False,
                                         source_payment_ids=("not-a-uuid",)),
                expire_before=row.expire_at, expire_after=NOW,
            )
    except Exception:  # noqa: BLE001 — битый uuid в источниках: запись не легла
        pass
    async with session.begin_nested():
        state = await carry.load_carry_state(session, user_id=uid, subscription=row, now=NOW)
    await carry.close_freeze(session, uid)
    await session.commit()
    assert state.old_plan_id == 10
    assert await db.conn.fetchval("SELECT count(*) FROM plan_change_carryovers") == 0


async def _real_prices(conn):
    await conn.execute("INSERT INTO plans (id, is_active) VALUES (12, true), (16, true)")
    for plan_id, days, price in ((12, 30, "129"), (12, 365, "1249"), (16, 30, "519"), (16, 365, "5249")):
        did = await conn.fetchval("INSERT INTO plan_durations (plan_id, days) VALUES ($1, $2) RETURNING id", plan_id, days)
        await conn.execute("INSERT INTO plan_prices (plan_duration_id, currency, price) VALUES ($1, 'RUB', $2)", did, Decimal(price))


async def test_gift_right_after_row_creation_is_not_create_layer(db):
    """Скептик C: подарок ДРУГОМУ человеку (NEW, настоящий тариф, final > 0) через 3 с после
    создания строки становился «создающим» слоем — строка стоила бы как подарок, а не как 129 ₽."""
    await _real_prices(db.conn)
    uid, sid, created = await person(db.conn, days_left=30, created_ago=0.0001, plan_id=12)
    change = await invoice(db.conn, uid, kind="CHANGE", at=created - timedelta(seconds=1), final="129", plan_id=12)
    # Подарок с баланса — по подписи; подарок через шлюз — по gift_payments; тот же тариф.
    await invoice(db.conn, uid, kind="NEW", at=created + timedelta(seconds=3), final="1249", plan_id=12,
                  duration=365, display="Баланс · подарок")
    gift = await invoice(db.conn, uid, kind="NEW", at=created + timedelta(seconds=4), final="1249", plan_id=12, duration=365)
    await db.conn.execute("INSERT INTO gift_payments (payment_id, user_id) VALUES ($1, $2)", gift, uid)
    # Чужой тариф в окне — не создающий этой строки.
    await invoice(db.conn, uid, kind="NEW", at=created + timedelta(seconds=2), final="5249", plan_id=16, duration=365)
    _row, state = await load(db, uid, extra=[16])
    assert [(l.kind, l.payment_id) for l in state.layers] == [("create", str(change))]


async def test_concurrent_changes_create_layer_comes_from_journal(db):
    """Скептик D: две смены почти одновременно — окно по времени указало бы на ЧУЖОЙ счёт.
    Строка, созданная переносом, знает свой счёт по журналу."""
    await _real_prices(db.conn)
    T = NOW - timedelta(days=1)
    uid = await db.conn.fetchval("INSERT INTO users DEFAULT VALUES RETURNING id")
    r2 = await db.conn.fetchval(
        "INSERT INTO subscriptions (user_id, status, expire_at, plan_snapshot, created_at) "
        "VALUES ($1, 'DELETED', $2, $3, $4) RETURNING id",
        uid, NOW + timedelta(days=500), json.dumps({"id": 16, "duration": 365}), T + timedelta(seconds=0.45))
    r3 = await db.conn.fetchval(
        "INSERT INTO subscriptions (user_id, status, expire_at, plan_snapshot, created_at) "
        "VALUES ($1, 'ACTIVE', $2, $3, $4) RETURNING id",
        uid, T + timedelta(days=30 + 1220), json.dumps({"id": 12, "duration": 30}), T + timedelta(seconds=0.35))
    await db.conn.execute("UPDATE users SET current_subscription_id = $1 WHERE id = $2", r3, uid)
    await invoice(db.conn, uid, kind="CHANGE", at=T + timedelta(seconds=0.40), final="5249", plan_id=12, duration=30)
    tx_b = await invoice(db.conn, uid, kind="CHANGE", at=T + timedelta(seconds=0.30), final="129", plan_id=12, duration=30)
    await db.conn.execute(
        "INSERT INTO plan_change_carryovers (payment_id, source, user_id, old_subscription_id, subscription_id, "
        "new_plan_id, new_duration, mode, currency, value_amount, new_day_price, bonus_days, expire_after, created_at) "
        "VALUES ($1, 'purchase', $2, $3, $4, 12, 30, 'carry', 'RUB', 5249, 4.3, 1220, $5, $6)",
        tx_b, uid, r2, r3, T + timedelta(days=1250), T + timedelta(seconds=0.35))
    _row, state = await load(db, uid, extra=[16])
    assert [(l.kind, l.payment_id) for l in state.layers] == [("create", str(tx_b)), ("carry", None)]
    assert state.layers[0].paid == 129


async def test_rerouted_renew_is_create_layer_by_journal(db):
    """RENEW чужого тарифа, пересчитанный как смена, — создающий слой новой строки по журналу."""
    uid, sid, created = await person(db.conn, days_left=60, created_ago=1, plan_id=20)
    renew = await invoice(db.conn, uid, kind="RENEW", at=created - timedelta(minutes=10), final="240", plan_id=20)
    await db.conn.execute(
        "INSERT INTO plan_change_carryovers (payment_id, source, user_id, old_subscription_id, subscription_id, "
        "new_plan_id, new_duration, mode, currency, value_amount, new_day_price, bonus_days, expire_after, created_at) "
        "VALUES ($1, 'purchase', $2, 1, $3, 20, 30, 'carry', 'RUB', 240, 8, 30, $4, $5)",
        renew, uid, sid, NOW + timedelta(days=60), created)
    _row, state = await load(db, uid)
    assert [(l.kind, l.payment_id) for l in state.layers] == [("create", str(renew)), ("carry", None)]


async def test_refunds_of_topups_and_gifts_do_not_disable_carry(db):
    """Скептик H: возврат пополнения (id −2) и подарка другому — не возврат подписки."""
    uid, sid, created = await person(db.conn, days_left=100, plan_id=12)
    await invoice(db.conn, uid, kind="NEW", at=NOW - timedelta(days=10), status="REFUNDED", plan_id=-2, duration=0, final="500")
    gift = await invoice(db.conn, uid, kind="NEW", at=NOW - timedelta(days=9), status="REFUNDED", plan_id=12, final="129")
    await db.conn.execute("INSERT INTO gift_payments (payment_id, user_id) VALUES ($1, $2)", gift, uid)
    _row, state = await load(db, uid)
    assert state.refund_recent is False
    await invoice(db.conn, uid, kind="RENEW", at=NOW - timedelta(days=8), status="REFUNDED", plan_id=12, final="129")
    _row, state = await load(db, uid)
    assert state.refund_recent is True


async def test_lock_wait_is_bounded(db):
    """Замок строки users под оплатой не ждёт бесконечно: lock_timeout срабатывает."""
    import asyncio

    uid, sid, created = await person(db.conn, days_left=30)
    holder = await asyncpg.connect(DSN)
    try:
        await holder.execute(f"SET search_path TO {db.schema}")
        tr = holder.transaction()
        await tr.start()
        await holder.execute("SELECT 1 FROM users WHERE id = $1 FOR UPDATE", uid)
        with pytest.raises(Exception) as err:
            await asyncio.wait_for(carry.lock_current_subscription(db.session, uid, timeout_ms=300), 5)
        assert "lock" in str(err.value).lower() and not isinstance(err.value, asyncio.TimeoutError)
        await tr.rollback()
    finally:
        await holder.close()
    await db.session.rollback()
    # После отката — обычное ожидание по умолчанию, замок берётся.
    row = await carry.lock_current_subscription(db.session, uid)
    assert row.id == sid
    assert (await db.session.execute(carry.text("SHOW lock_timeout"))).scalar() == "0"
    await db.session.rollback()


async def test_user_fk_is_declared_for_duplicate_merge(db):
    """merge-duplicate.py переносит таблицы по FK на users(id) — журнал обязан его иметь."""
    fk = await db.conn.fetchval(
        "SELECT count(*) FROM pg_constraint c WHERE c.conrelid = to_regclass($1) AND c.contype = 'f' "
        "AND c.confrelid = to_regclass($2)",
        f"{db.schema}.plan_change_carryovers", f"{db.schema}.users",
    )
    assert fk == 1
