"""Запрос плитки «Возвраты (30 дн)» на настоящем Postgres.

ЗАЧЕМ ОТДЕЛЬНО. test_metrics_refunds.py проверяет текст запроса и обработку ответа
на подделанной сессии, но не то, что Postgres по этому тексту отберёт ровно нужные
строки: приведение `pricing->>'final_amount'` к numeric, сравнение с окном по
`updated_at`, подзапрос по ролям. Здесь запрос `compute_refunds_30d` выполняется
как есть — на временных таблицах той же формы, где рядом с двумя настоящими
возвратами лежат пять строк, которые обязаны выпасть.

Таблицы — TEMP: они живут только в этом соединении и перекрывают одноимённые, а в
конце всё откатывается. И всё равно гонять ТОЛЬКО на одноразовом Postgres, не на
боевой базе.

Инвариант «REFUNDED только из COMPLETED» на уровне SQL уже стережёт
test_payment_idempotency.py::test_refund_only_from_completed_and_idempotent.

Запуск (opt-in, без RS_PG_DSN пропускается), в отдельной сети:

  docker network create refunds-test-net
  docker run -d --rm --name pg-refunds-test --network refunds-test-net \
    -e POSTGRES_PASSWORD=t postgres:17
  docker run --rm --network refunds-test-net --env-file ci.env \
    -e RS_PG_DSN=postgresql://postgres:t@pg-refunds-test:5432/postgres \
    -v /opt/remnashop/admin_src/tests:/tmp/tests:ro remnashop-ci-local \
    sh -c 'pip install -q --target /tmp/pylibs pytest pytest-asyncio \
      && PYTHONPATH=/tmp/pylibs python -m pytest /tmp/tests/test_metrics_refunds_sql.py \
         -v --asyncio-mode=auto'
  docker stop pg-refunds-test && docker network rm refunds-test-net
"""

import importlib
import os

import pytest

from _pg_dsn import sqlalchemy_dsn  # noqa: E402 — соседний модуль тестов

DSN = sqlalchemy_dsn()
pytestmark = pytest.mark.skipif(not DSN, reason="RS_PG_DSN не задан — нет связи с Postgres")

statistics = importlib.import_module("src.web.endpoints.admin.statistics")

SCHEMA = (
    "CREATE TEMP TABLE users (id int PRIMARY KEY, role text NOT NULL)",
    "CREATE TEMP TABLE transactions ("
    "  id serial PRIMARY KEY, status text NOT NULL, is_test bool NOT NULL,"
    "  user_id int, currency text NOT NULL, pricing jsonb NOT NULL,"
    "  created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL)",
    "CREATE TEMP TABLE payment_gateways (type text PRIMARY KEY, is_active bool NOT NULL)",
)

# (status, is_test, user_id, currency, final_amount, created дней назад, updated дней назад)
CLIENT, OWNER = 1, 2
ROWS = (
    # Считаются: возврат СТАРОЙ оплаты (оплачено 60 дней назад, отозвано 10 дней
    # назад) — ради него окно и стоит на дате возврата.
    ("REFUNDED", False, CLIENT, "RUB", "499", 60, 10),
    ("REFUNDED", False, CLIENT, "USD", "5", 5, 3),
    # Не считаются:
    ("REFUNDED", False, CLIENT, "RUB", "700", 45, 40),  # вернули раньше окна
    ("COMPLETED", False, CLIENT, "RUB", "300", 1, 1),  # не возврат
    ("REFUNDED", True, CLIENT, "RUB", "10", 2, 2),  # проверочная оплата
    ("REFUNDED", False, OWNER, "RUB", "900", 2, 2),  # покупка владельца
    ("REFUNDED", False, CLIENT, "RUB", "0", 2, 2),  # бесплатная выдача
)


@pytest.mark.asyncio
async def test_refunds_query_on_postgres() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(
        DSN.replace("postgresql://", "postgresql+asyncpg://", 1), poolclass=NullPool
    )
    try:
        async with engine.connect() as conn:
            try:
                for ddl in SCHEMA:
                    await conn.execute(text(ddl))
                await conn.execute(
                    text("INSERT INTO users (id, role) VALUES (:c, 'USER'), (:o, 'OWNER')"),
                    {"c": CLIENT, "o": OWNER},
                )
                await conn.execute(
                    text(
                        "INSERT INTO payment_gateways (type, is_active) VALUES "
                        "('YOOMONEY', true), ('VALUTIX', true), ('PLATEGA', false)"
                    )
                )
                for status, is_test, user_id, currency, amount, created, updated in ROWS:
                    await conn.execute(
                        text(
                            "INSERT INTO transactions "
                            "(status, is_test, user_id, currency, pricing, created_at, updated_at) "
                            "VALUES (:s, :t, :u, :c, jsonb_build_object('final_amount', CAST(:a AS text)), "
                            "now() - make_interval(days => :cd), now() - make_interval(days => :ud))"
                        ),
                        {
                            "s": status, "t": is_test, "u": user_id, "c": currency,
                            "a": amount, "cd": created, "ud": updated,
                        },
                    )

                session = AsyncSession(bind=conn)
                refunds = await statistics.compute_refunds_30d(session)
                await session.close()
            finally:
                await conn.rollback()
    finally:
        await engine.dispose()

    assert refunds["count_30d"] == 2
    assert refunds["by_currency"] == [
        {"currency": "RUB", "count": 1, "amount": 499.0},
        {"currency": "USD", "count": 1, "amount": 5.0},
    ]
    # Выключенная Platega в списки не попадает: её вебхуки база отбивает.
    assert refunds["reporting_gateways"] == ["VALUTIX"]
    assert refunds["silent_gateways"] == ["YOOMONEY"]
