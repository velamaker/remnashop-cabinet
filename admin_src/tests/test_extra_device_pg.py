"""Докупка устройства на НАСТОЯЩЕМ Postgres: гонки, которые подделки не ловят.

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ. Подделка сессии отвечает на запрос по куску текста и ничего не
знает ни про замки строк, ни про UNIQUE, ни про изоляцию: мутацию «убрать повторный
поиск request_id под замком» соседние тесты не видят вовсе — у них нет второй сессии.
Здесь две живые сессии дерутся за одного человека, как две вкладки кабинета.

ЗАПУСК — ПО ЖЕЛАНИЮ: нужен одноразовый Postgres, поэтому тесты включаются только
переменной RS_PG_DSN и в обычном прогоне пропускаются (как test_digest_email_pg).

    docker run -d --name pg -e POSTGRES_PASSWORD=x -p 5432:5432 postgres:17
    RS_PG_DSN=postgresql+asyncpg://postgres:x@localhost/postgres pytest test_extra_device_pg.py

Схему создаём сами и минимальную: цель — поведение базы, а не миграции (их проверяет
scripts/migration-e2e.sh). Данные синтетические.
"""

import asyncio
import contextlib
import importlib
import os
import uuid as uuid_lib
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

extra = importlib.import_module("src.infrastructure.services.overlay_extra_device")

from _pg_dsn import sqlalchemy_dsn  # noqa: E402 — соседний модуль тестов

DSN = sqlalchemy_dsn()
pytestmark = pytest.mark.skipif(not DSN, reason="нужен RS_PG_DSN (одноразовый Postgres)")

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
PANEL_UUID = "11111111-1111-1111-1111-111111111111"

# ОТДЕЛЬНАЯ СХЕМА, а не public: на том же RS_PG_DSN гоняются соседние opt-in тесты
# (перенос остатка, письма сводки), и уронить их таблицы своим DROP недопустимо.
SCHEMA_NAME = "extra_device_pg_test"

SCHEMA = """
DROP SCHEMA IF EXISTS extra_device_pg_test CASCADE;
CREATE SCHEMA extra_device_pg_test;
CREATE TABLE extra_device_pg_test.users (
    id SERIAL PRIMARY KEY, role VARCHAR(20) DEFAULT 'USER',
    cabinet_balance NUMERIC(12,2) NOT NULL DEFAULT 0, current_subscription_id INTEGER);
CREATE TABLE extra_device_pg_test.subscriptions (
    id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    is_trial BOOLEAN NOT NULL DEFAULT false, expire_at TIMESTAMPTZ, device_limit INTEGER NOT NULL,
    plan_snapshot JSONB NOT NULL, user_remna_id VARCHAR(64), updated_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE extra_device_pg_test.transactions (
    payment_id UUID PRIMARY KEY, user_id INTEGER NOT NULL, status VARCHAR(20) NOT NULL,
    is_test BOOLEAN NOT NULL DEFAULT false, currency VARCHAR(8) NOT NULL DEFAULT 'RUB',
    pricing JSONB NOT NULL, plan_snapshot JSONB NOT NULL, created_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE extra_device_pg_test.subscription_freezes (
    user_id INTEGER PRIMARY KEY, frozen_at TIMESTAMPTZ, active BOOLEAN NOT NULL DEFAULT false);
CREATE TABLE extra_device_pg_test.reserve_grants (
    id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, ended BOOLEAN NOT NULL DEFAULT false,
    reserve_expire_at TIMESTAMPTZ);
CREATE TABLE extra_device_pg_test.extra_device_slots (
    id BIGSERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES extra_device_pg_test.users(id) ON DELETE CASCADE,
    subscription_id INTEGER NOT NULL, plan_id INTEGER NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'active', starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ NOT NULL, last_applied_at TIMESTAMPTZ NOT NULL, reminded_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ, end_reason VARCHAR(32), devices_removed INTEGER NOT NULL DEFAULT 0,
    removal_done BOOLEAN NOT NULL DEFAULT true, fail_count INTEGER NOT NULL DEFAULT 0,
    carried_value NUMERIC(12,2), created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_eds_period CHECK (ends_at > starts_at));
CREATE TABLE extra_device_pg_test.extra_device_orders (
    id BIGSERIAL PRIMARY KEY, request_id UUID NOT NULL UNIQUE, payment_id UUID UNIQUE,
    source VARCHAR(16) NOT NULL, kind VARCHAR(8) NOT NULL,
    user_id INTEGER NOT NULL REFERENCES extra_device_pg_test.users(id) ON DELETE CASCADE,
    subscription_id INTEGER NOT NULL, slot_id BIGINT REFERENCES extra_device_pg_test.extra_device_slots(id),
    status VARCHAR(16) NOT NULL, amount NUMERIC(12,2) NOT NULL CHECK (amount > 0),
    currency VARCHAR(8) NOT NULL DEFAULT 'RUB', price_per_30d NUMERIC(12,2) NOT NULL,
    cov_start TIMESTAMPTZ NOT NULL, period_end TIMESTAMPTZ NOT NULL, payment_url TEXT,
    attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, reason VARCHAR(32),
    credited_at TIMESTAMPTZ, applied_at TIMESTAMPTZ, rejected_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now());
"""


class FakeSdk:
    """Панель принимает любой лимит: здесь проверяется база, а не она."""

    class _Users:
        async def update_user(self, body):
            from types import SimpleNamespace

            return SimpleNamespace(hwid_device_limit=body.hwid_device_limit)

    users = _Users()


@pytest.fixture
async def engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    from sqlalchemy import text as sa_text

    # Схему создаём отдельным подключением: собственно тесты уже работают внутри неё.
    setup = create_async_engine(DSN)
    async with setup.begin() as conn:
        for stmt in SCHEMA.strip().split(";\n"):
            if stmt.strip():
                await conn.execute(sa_text(stmt))
    await setup.dispose()

    eng = create_async_engine(
        DSN, pool_size=5, connect_args={"server_settings": {"search_path": SCHEMA_NAME}}
    )
    yield eng
    await eng.dispose()

    cleanup = create_async_engine(DSN)
    async with cleanup.begin() as conn:
        await conn.execute(sa_text(f"DROP SCHEMA IF EXISTS {SCHEMA_NAME} CASCADE"))
    await cleanup.dispose()


async def _seed(engine, *, balance="1000", device_limit=2, plan_devices=2):
    from sqlalchemy import text as sa_text

    async with engine.begin() as conn:
        uid = (
            await conn.execute(
                sa_text("INSERT INTO users (cabinet_balance) VALUES (:b) RETURNING id"),
                {"b": Decimal(balance)},
            )
        ).scalar()
        sid = (
            await conn.execute(
                sa_text(
                    "INSERT INTO subscriptions (user_id, expire_at, device_limit, plan_snapshot, "
                    "user_remna_id) VALUES (:u, :e, :d, CAST(:p AS jsonb), :r) RETURNING id"
                ),
                {
                    "u": uid,
                    "e": NOW + timedelta(days=20),
                    "d": device_limit,
                    "p": '{"id": 7, "device_limit": %d}' % plan_devices,
                    "r": PANEL_UUID,
                },
            )
        ).scalar()
        await conn.execute(
            sa_text("UPDATE users SET current_subscription_id = :s WHERE id = :u"),
            {"s": sid, "u": uid},
        )
    return uid, sid


def _cfg(**over):
    return dict(extra.DEFAULT_CONFIG, enabled=True, price_rub_30d=90, **over)


class _Gateways:
    """Один настроенный рублёвый шлюз: транзакции нужен непустой gateway_type."""

    async def get_active(self):
        from src.core.enums import Currency, PaymentGatewayType

        return [
            SimpleNamespace(
                type=PaymentGatewayType.YOOMONEY,
                currency=Currency.RUB,
                settings=SimpleNamespace(is_configured=True, display_name=None),
            )
        ]

    async def get_all(self, only_active: bool = False, sorted: bool = True):
        return await self.get_active()


class _TxDao:
    """Транзакция здесь не проверяется — важны слоты, заказы и баланс."""

    async def create(self, transaction):
        return transaction


class _Notifier:
    async def notify_admins(self, payload):
        return None

    async def notify_user(self, user, payload=None, **kw):
        return None


async def _buy(engine, uid, request_id, *, cfg=None):
    """Одна покупка с баланса СВОЕЙ сессией — ровно так, как её делает кабинет.

    Зовём НАСТОЯЩУЮ ручку, а не повторяем её шаги: иначе мутация «убрать повторный
    поиск request_id под замком» осталась бы зелёной — в копии шагов её просто нет.
    """
    import src.web.endpoints.public.extra_device as endpoint
    from sqlalchemy.ext.asyncio import async_sessionmaker

    maker = async_sessionmaker(engine, expire_on_commit=False)
    live = cfg or _cfg()
    raw = endpoint.buy_extra_device.__dishka_orig_func__
    async with maker() as session:
        with _patched(endpoint, live):
            result = await raw(
                body=endpoint.BuyRequest(
                    request_id=request_id, kind="new", pay="balance", expected_amount=Decimal(60)
                ),
                user=SimpleNamespace(id=uid, log=f"[USER:{uid}]", auth_type=None),
                session=session,
                remnawave=SimpleNamespace(sdk=FakeSdk()),
                payment_gateway_dao=_Gateways(),
                transaction_dao=_TxDao(),
                create_payment=None,
                notifier=_Notifier(),
            )
    if result.get("repeat"):
        return "replayed"
    if result.get("result") == "not_available":
        return result["reason"]
    return result.get("result")


@contextlib.contextmanager
def _patched(endpoint, live):
    """Время и конфиг — фиксированные: цена не должна зависеть от часов машины."""
    saved = (endpoint.datetime_now, endpoint._assert_email_verified, extra.load_config)
    endpoint.datetime_now = lambda: NOW
    endpoint._assert_email_verified = lambda user: None
    extra.load_config = lambda: live
    try:
        yield
    finally:
        endpoint.datetime_now, endpoint._assert_email_verified, extra.load_config = saved


async def _count(engine, table, uid):
    from sqlalchemy import text as sa_text

    async with engine.connect() as conn:
        return (
            await conn.execute(
                sa_text(f"SELECT count(*) FROM {table} WHERE user_id = :u"), {"u": uid}
            )
        ).scalar()


async def test_same_request_id_in_two_sessions_buys_one_place(engine):
    """Две вкладки с одним request_id — одно место и одно списание.

    Мутация «убрать повторный поиск под замком» краснеет именно здесь: без второго
    поиска обе сессии проходят проверку до замка и создают по слоту.
    """
    uid, _sid = await _seed(engine, device_limit=2, plan_devices=2)
    rid = uuid_lib.uuid4()
    results = await asyncio.gather(_buy(engine, uid, rid), _buy(engine, uid, rid))
    assert sorted(results) == ["applied", "replayed"], results
    assert await _count(engine, "extra_device_slots", uid) == 1
    assert await _count(engine, "extra_device_orders", uid) == 1

    from sqlalchemy import text as sa_text

    async with engine.connect() as conn:
        balance = (
            await conn.execute(sa_text("SELECT cabinet_balance FROM users WHERE id = :u"), {"u": uid})
        ).scalar()
        limit = (
            await conn.execute(
                sa_text("SELECT device_limit FROM subscriptions WHERE user_id = :u"), {"u": uid}
            )
        ).scalar()
    assert Decimal(balance) == Decimal("940")  # ровно одно списание 60 ₽
    assert limit == 3  # ровно одно место


async def test_two_different_requests_respect_the_maximum(engine):
    """Разные ключи, максимум 1 — замок строки не даёт купить два места разом."""
    uid, _sid = await _seed(engine)
    cfg = _cfg(max_extra=1)
    results = await asyncio.gather(
        _buy(engine, uid, uuid_lib.uuid4(), cfg=cfg),
        _buy(engine, uid, uuid_lib.uuid4(), cfg=cfg),
    )
    assert sorted(results) == ["applied", "max_reached"], results
    assert await _count(engine, "extra_device_slots", uid) == 1


async def test_expired_place_does_not_block_a_new_purchase(engine):
    """Решение владельца «отключить и предложить снова»: кончившееся место освобождает
    счётчик.

    Проверяется на живой базе намеренно: сломать это можно не только правилом в
    `eligibility`, но и выборкой действующих мест — стоит ей начать возвращать
    `ended`, и человек навсегда упрётся в максимум.
    """
    from sqlalchemy import text as sa_text

    uid, sid = await _seed(engine)
    cfg = _cfg(max_extra=1)
    assert await _buy(engine, uid, uuid_lib.uuid4(), cfg=cfg) == "applied"
    assert await _buy(engine, uid, uuid_lib.uuid4(), cfg=cfg) == "max_reached"
    async with engine.begin() as conn:
        await conn.execute(
            sa_text("UPDATE extra_device_slots SET status = 'ended' WHERE user_id = :u"), {"u": uid}
        )
    assert await _buy(engine, uid, uuid_lib.uuid4(), cfg=cfg) == "applied"


async def test_mrr_ignores_synthetic_payments(engine):
    """MRR считает только тарифные платежи — и не падает на нечисловом id."""
    from sqlalchemy import text as sa_text

    uid, _sid = await _seed(engine)
    async with engine.begin() as conn:
        for snapshot, amount in (
            ('{"id": 7, "duration": 30}', 500),
            ('{"id": -4, "duration": 20}', 60),
            ('{"id": "сломанный", "duration": 30}', 1),
        ):
            await conn.execute(
                sa_text(
                    "INSERT INTO transactions (payment_id, user_id, status, pricing, plan_snapshot) "
                    "VALUES (:p, :u, 'COMPLETED', CAST(:pr AS jsonb), CAST(:ps AS jsonb))"
                ),
                {
                    "p": uuid_lib.uuid4(),
                    "u": uid,
                    "pr": '{"final_amount": %d}' % amount,
                    "ps": snapshot,
                },
            )
        rows = (
            await conn.execute(
                sa_text(
                    "SELECT (t.pricing->>'final_amount')::numeric FROM transactions t "
                    "WHERE t.status = 'COMPLETED' AND t.currency = 'RUB' "
                    "AND (t.pricing->>'final_amount')::numeric > 0 "
                    "AND (CASE WHEN t.plan_snapshot->>'id' ~ '^-?[0-9]+$' "
                    "     THEN (t.plan_snapshot->>'id')::int ELSE 0 END) > 0 "
                    "ORDER BY t.created_at DESC"
                )
            )
        ).all()
    # Остался только тарифный платёж: докупка отсеяна, нечисловой id не уронил запрос.
    assert [int(r[0]) for r in rows] == [500]
