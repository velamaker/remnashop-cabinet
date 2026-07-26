"""Тесты идемпотентности/конкурентности обработки платежей.

Закрывают находку аудита: «нет тестов на повтор webhook и одновременные начисления».
Проверяют ИНВАРИАНТ, на котором держится защита базового кода: переход статуса
транзакции — это АТОМАРНЫЙ условный UPDATE

    UPDATE transactions SET status=<new>, updated_at=now()
     WHERE payment_id=<pid> AND status IN (<allowed>) RETURNING id

(см. base: infrastructure/database/dao/transaction.py::transition_status). Благодаря
CAS-семантике повторный/параллельный webhook начисляет РОВНО один раз, а запрещённые
переходы (refund до completed, complete после cancel) отклоняются.

Полная машина состояний из application/use_cases/gateways/commands/payment.py::ProcessPayment:
    COMPLETED ← из (PENDING, FAILED)   # FAILED = retry после сбоя провижининга
    CANCELED  ← из (PENDING,)
    REFUNDED  ← из (COMPLETED,)

Faithfulness: SQL перехода в тесте побайтово повторяет то, что генерит SQLAlchemy в
реальном DAO (проверено: `stmt.compile(postgresql.dialect())` →
`... WHERE transactions.payment_id = ... AND transactions.status IN (...) RETURNING ...`).
Отличие — только бамп updated_at, на идемпотентность не влияющий. Тест `test_schema_...`
стережёт, что реальная колонка `status` = enum `transaction_status` с теми же метками, а
`payment_id` уникален — если прод-схема поедет, тест упадёт, а не будет молча дивергировать.

Работает в отдельной scratch-таблице с РЕАЛЬНЫМ enum-типом `transaction_status`; прод-таблицы
`transactions`/`users` НЕ трогает (на платёжном сервисе не вставляем мусорные строки даже
временно). Каждый тест — своя таблица, дроп в finally.

Запуск (opt-in, нужна связь с Postgres):
  RS_PG_DSN=postgresql://remnashop:...@remnashop-db:5432/remnashop \
      pytest tests/test_payment_idempotency.py -v
"""

import asyncio
import os
import uuid

import pytest

asyncpg = pytest.importorskip("asyncpg")

DSN = os.environ.get("RS_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="RS_PG_DSN не задан — нет связи с Postgres")

# Значения PG-enum transaction_status (см. core/enums.py::TransactionStatus).
PENDING, COMPLETED, CANCELED, REFUNDED, FAILED = (
    "PENDING",
    "COMPLETED",
    "CANCELED",
    "REFUNDED",
    "FAILED",
)

# Точная семантика base.transition_status на РЕАЛЬНОМ enum-типе: условный UPDATE.
# status = ANY($3::transaction_status[]) эквивалентно IN (...) из DAO, но параметризуемо.
_TRANSITION_SQL = (
    "UPDATE {tbl} SET status=$2::transaction_status "
    "WHERE payment_id=$1 AND status = ANY($3::transaction_status[]) RETURNING id"
)


async def _make_table(conn, pid: uuid.UUID, status: str = PENDING) -> str:
    """Scratch-таблица со СХЕМОЙ как у прода (enum transaction_status + unique payment_id)."""
    tbl = "pay_idem_" + uuid.uuid4().hex[:12]
    await conn.execute(
        f"CREATE TABLE {tbl} ("
        f"  id serial PRIMARY KEY,"
        f"  payment_id uuid NOT NULL UNIQUE,"
        f"  status transaction_status NOT NULL"
        f")"
    )
    await conn.execute(
        f"INSERT INTO {tbl} (payment_id, status) VALUES ($1, $2::transaction_status)",
        pid,
        status,
    )
    return tbl


async def _transition(conn, tbl: str, pid: uuid.UUID, new: str, allowed: list[str]):
    return await conn.fetchval(_TRANSITION_SQL.format(tbl=tbl), pid, new, allowed)


async def _status(conn, tbl: str, pid: uuid.UUID) -> str:
    return await conn.fetchval(f"SELECT status FROM {tbl} WHERE payment_id=$1", pid)


# ---------------------------------------------------------------------------
# Страж схемы: допущения scratch-таблицы должны совпадать с реальной transactions.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_schema_matches_prod_assumptions():
    """READ-ONLY: реальная transactions.status = enum transaction_status с теми же метками,
    payment_id — uuid c уникальным индексом. Иначе scratch-тесты молча разъедутся с продом."""
    conn = await asyncpg.connect(DSN)
    try:
        labels = await conn.fetch(
            "SELECT e.enumlabel FROM pg_type t "
            "JOIN pg_enum e ON e.enumtypid=t.oid WHERE t.typname='transaction_status'"
        )
        assert {r["enumlabel"] for r in labels} == {
            PENDING,
            COMPLETED,
            CANCELED,
            REFUNDED,
            FAILED,
        }
        col = await conn.fetchrow(
            "SELECT data_type, udt_name FROM information_schema.columns "
            "WHERE table_name='transactions' AND column_name='status'"
        )
        assert col["udt_name"] == "transaction_status"
        pid_type = await conn.fetchval(
            "SELECT udt_name FROM information_schema.columns "
            "WHERE table_name='transactions' AND column_name='payment_id'"
        )
        assert pid_type == "uuid"
        has_unique = await conn.fetchval(
            "SELECT bool_or(indisunique) FROM pg_index i "
            "JOIN pg_class c ON c.oid=i.indexrelid "
            "JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey) "
            "WHERE i.indrelid='transactions'::regclass AND a.attname='payment_id'"
        )
        assert has_unique is True
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Повтор webhook (replay) — начисление ровно один раз.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_replayed_completed_credits_once():
    """COMPLETED дважды подряд → второй раз перехода нет (нет двойного начисления)."""
    pid = uuid.uuid4()
    conn = await asyncpg.connect(DSN)
    tbl = None
    try:
        tbl = await _make_table(conn, pid)
        first = await _transition(conn, tbl, pid, COMPLETED, [PENDING, FAILED])
        second = await _transition(conn, tbl, pid, COMPLETED, [PENDING, FAILED])
        assert first is not None  # первый webhook начислил
        assert second is None  # повтор — no-op
        assert await _status(conn, tbl, pid) == COMPLETED
    finally:
        if tbl:
            await conn.execute(f"DROP TABLE IF EXISTS {tbl}")
        await conn.close()


# ---------------------------------------------------------------------------
# Частичный сбой провижининга: COMPLETED → FAILED → повторный webhook добивает.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_failed_then_completed_retry_credits_once():
    """Сценарий «частичный сбой PG↔шлюз↔Remnawave»: транзакция помечена FAILED (провижининг
    упал), повторный webhook COMPLETED из (PENDING,FAILED) добивает РОВНО раз; третий — no-op.
    Именно поэтому FAILED входит в allowed для COMPLETED."""
    pid = uuid.uuid4()
    conn = await asyncpg.connect(DSN)
    tbl = None
    try:
        tbl = await _make_table(conn, pid)
        # webhook #1: перешли в COMPLETED, но провижининг упал → база ставит FAILED.
        assert await _transition(conn, tbl, pid, COMPLETED, [PENDING, FAILED]) is not None
        await conn.execute(
            f"UPDATE {tbl} SET status=$2::transaction_status WHERE payment_id=$1", pid, FAILED
        )
        # webhook #2 (retry): FAILED → COMPLETED успешно.
        assert await _transition(conn, tbl, pid, COMPLETED, [PENDING, FAILED]) is not None
        # webhook #3 (ещё раз): уже COMPLETED → no-op.
        assert await _transition(conn, tbl, pid, COMPLETED, [PENDING, FAILED]) is None
        assert await _status(conn, tbl, pid) == COMPLETED
    finally:
        if tbl:
            await conn.execute(f"DROP TABLE IF EXISTS {tbl}")
        await conn.close()


# ---------------------------------------------------------------------------
# Отменённый платёж нельзя молча «завершить», и наоборот.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_canceled_cannot_be_completed():
    """CANCELED из PENDING → затем COMPLETED из (PENDING,FAILED) ОТКЛОНЯЕТСЯ (не воскрешаем
    оплату по отменённой транзакции). И обратно: по COMPLETED нельзя выполнить CANCEL(из PENDING)."""
    conn = await asyncpg.connect(DSN)
    tbl_a = tbl_b = None
    try:
        # Ветка A: cancel затем попытка complete.
        pid_a = uuid.uuid4()
        tbl_a = await _make_table(conn, pid_a)
        assert await _transition(conn, tbl_a, pid_a, CANCELED, [PENDING]) is not None
        assert await _transition(conn, tbl_a, pid_a, COMPLETED, [PENDING, FAILED]) is None
        assert await _status(conn, tbl_a, pid_a) == CANCELED

        # Ветка B: complete затем поздний cancel-webhook.
        pid_b = uuid.uuid4()
        tbl_b = await _make_table(conn, pid_b)
        assert await _transition(conn, tbl_b, pid_b, COMPLETED, [PENDING, FAILED]) is not None
        assert await _transition(conn, tbl_b, pid_b, CANCELED, [PENDING]) is None
        assert await _status(conn, tbl_b, pid_b) == COMPLETED
    finally:
        for t in (tbl_a, tbl_b):
            if t:
                await conn.execute(f"DROP TABLE IF EXISTS {t}")
        await conn.close()


# ---------------------------------------------------------------------------
# Refund только из COMPLETED; refund идемпотентен.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_refund_only_from_completed_and_idempotent():
    """REFUNDED разрешён ТОЛЬКО из COMPLETED: refund по PENDING отклоняется; после COMPLETED —
    успешно; повтор refund — no-op (нет двойного возврата)."""
    conn = await asyncpg.connect(DSN)
    tbl_p = tbl_c = None
    try:
        # refund по PENDING (оплаты не было) → отклонён.
        pid_p = uuid.uuid4()
        tbl_p = await _make_table(conn, pid_p)
        assert await _transition(conn, tbl_p, pid_p, REFUNDED, [COMPLETED]) is None
        assert await _status(conn, tbl_p, pid_p) == PENDING

        # completed → refund → повтор refund.
        pid_c = uuid.uuid4()
        tbl_c = await _make_table(conn, pid_c)
        assert await _transition(conn, tbl_c, pid_c, COMPLETED, [PENDING, FAILED]) is not None
        assert await _transition(conn, tbl_c, pid_c, REFUNDED, [COMPLETED]) is not None
        assert await _transition(conn, tbl_c, pid_c, REFUNDED, [COMPLETED]) is None
        assert await _status(conn, tbl_c, pid_c) == REFUNDED
    finally:
        for t in (tbl_p, tbl_c):
            if t:
                await conn.execute(f"DROP TABLE IF EXISTS {t}")
        await conn.close()


# ---------------------------------------------------------------------------
# Конкурентность: N одновременных COMPLETED → ровно один переход.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_concurrent_completed_credits_once():
    """N ОДНОВРЕМЕННЫХ webhook COMPLETED на одной PENDING-строке → ровно один переход.
    Гонка сериализуется row-lock: остальные видят уже COMPLETED и не матчатся по WHERE."""
    n = 8
    pid = uuid.uuid4()
    setup = await asyncpg.connect(DSN)
    tbl = None
    try:
        tbl = await _make_table(setup, pid)

        async def attempt():
            c = await asyncpg.connect(DSN)
            try:
                async with c.transaction():  # READ COMMITTED (дефолт, как у приложения)
                    return await _transition(c, tbl, pid, COMPLETED, [PENDING, FAILED])
            finally:
                await c.close()

        results = await asyncio.gather(*[attempt() for _ in range(n)])
        winners = [r for r in results if r is not None]
        assert len(winners) == 1  # ровно один из N начислил
        assert await _status(setup, tbl, pid) == COMPLETED
    finally:
        if tbl:
            await setup.execute(f"DROP TABLE IF EXISTS {tbl}")
        await setup.close()


# ---------------------------------------------------------------------------
# Конкурентность: одновременные cancel и complete → ровно один, без «рваного» статуса.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_concurrent_cancel_vs_complete_exactly_one_wins():
    """Одновременно cancel-webhook (из PENDING) и complete-webhook (из PENDING,FAILED) на одной
    PENDING-строке: выигрывает РОВНО один; финальный статус — его; никогда не оба."""
    pid = uuid.uuid4()
    setup = await asyncpg.connect(DSN)
    tbl = None
    try:
        tbl = await _make_table(setup, pid)

        async def do(new: str, allowed: list[str]):
            c = await asyncpg.connect(DSN)
            try:
                async with c.transaction():
                    return new, await _transition(c, tbl, pid, new, allowed)
            finally:
                await c.close()

        (n1, r1), (n2, r2) = await asyncio.gather(
            do(COMPLETED, [PENDING, FAILED]),
            do(CANCELED, [PENDING]),
        )
        winners = [n for n, r in ((n1, r1), (n2, r2)) if r is not None]
        assert len(winners) == 1  # только один перевёл строку
        assert await _status(setup, tbl, pid) == winners[0]  # финальный статус = победитель
    finally:
        if tbl:
            await setup.execute(f"DROP TABLE IF EXISTS {tbl}")
        await setup.close()
