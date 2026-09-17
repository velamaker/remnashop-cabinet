"""Массовые задачи (миграция 0009) на настоящем Postgres: гарантии держит сама база.

Юнит-тесты (admin_src/tests/test_bulk_*.py) гоняют процессоры на подделке хранилища —
она повторяет смысл условных UPDATE, но не может доказать, что SQL и схема его
действительно дают. Здесь — ровно то, на чём держится «дни не выдаются дважды»:
  * второй запуск с тем же request_id отбивается уникальным ключом;
  * вторая активная задача того же вида — частичным уникальным индексом, а после
    завершения первой новая проходит;
  * строку человека захватывает только одна попытка;
  * чужой воркер не перехватывает живую аренду, а истёкшую — перехватывает;
плюс каждый запрос `BulkStore` хотя бы раз выполняется на настоящей схеме: опечатка
в SQL или неверный тип параметра иначе всплыли бы только под живым админом.

Запускается из scripts/migration-e2e.sh ВНУТРИ образа бота против одноразового
Postgres (после `alembic upgrade head`). Боевую базу не трогать: скрипт создаёт
минимальные таблицы базы (subscriptions, transactions) и пишет тестовые строки.
"""

import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.core.config import AppConfig
from src.infrastructure.services import overlay_bulk as bulk

BASE_DDL = (
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS name VARCHAR(100) NOT NULL DEFAULT 'e2e'",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_blocked BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_bot_blocked BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(255)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_email_verified BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS language VARCHAR(8) NOT NULL DEFAULT 'RU'",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS current_subscription_id INTEGER",
    "CREATE TABLE IF NOT EXISTS subscriptions ("
    " id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, status VARCHAR(20) NOT NULL,"
    " is_trial BOOLEAN NOT NULL DEFAULT false, expire_at TIMESTAMPTZ NOT NULL, user_remna_id UUID,"
    " updated_at TIMESTAMPTZ NOT NULL DEFAULT now())",
    "CREATE TABLE IF NOT EXISTS transactions ("
    " id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, status VARCHAR(20) NOT NULL,"
    " created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())",
)


def check(cond: bool, message: str) -> None:
    if not cond:
        print(f"BULK-JOBS SQL FAIL: {message}", file=sys.stderr)
        sys.exit(1)
    print(f"  ok: {message}")


async def main() -> None:
    engine = create_async_engine(AppConfig.get().database.dsn)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    async with maker() as s:
        for ddl in BASE_DDL:
            await s.execute(text(ddl))
        uids = []
        for i in range(4):
            uids.append(
                (await s.execute(text("INSERT INTO users (telegram_id, role) VALUES (:t, 'USER') RETURNING id"), {"t": 9000 + i})).scalar_one()
            )
        for n, uid in enumerate(uids):
            sub_id = (
                await s.execute(
                    text(
                        "INSERT INTO subscriptions (user_id, status, expire_at, user_remna_id, updated_at) "
                        "VALUES (:u, 'ACTIVE', :e, :r, now() - interval '1 day') RETURNING id"
                    ),
                    {"u": uid, "e": now + timedelta(days=10 + n), "r": uuid.uuid4()},
                )
            ).scalar_one()
            await s.execute(text("UPDATE users SET current_subscription_id = :s WHERE id = :u"), {"s": sub_id, "u": uid})
        await s.execute(text("INSERT INTO transactions (user_id, status) VALUES (:u, 'PENDING')"), {"u": uids[1]})
        await s.execute(
            text("INSERT INTO subscription_freezes (user_id, remna_uuid, remaining_seconds) VALUES (:u, 'x', 3600)"),
            {"u": uids[2]},
        )
        await s.execute(
            # Срок подписки совпадает с концом резерва — это человек «на резерве».
            text("INSERT INTO reserve_grants (user_id, remna_uuid, reserve_expire_at) VALUES (:u, 'x', :e)"),
            {"u": uids[3], "e": now + timedelta(days=13)},
        )
        await s.commit()

    print("[bulk] выборка и классификация")
    async with maker() as s:
        store = bulk.BulkStore(s)
        rows = await store.classify_rows(uids)
        check(len(rows) == 4, "CLASSIFY_SQL отдаёт по строке на человека без дублей")
        cats = {uid: bulk.classify_days(rows[uid], now=now, include_trial=False, include_limited=False) for uid in uids}
        check(
            [cats[u] for u in uids]
            == [bulk.Category.APPLY, bulk.Category.RECENT_CHANGE, bulk.Category.APPLY_FROZEN, bulk.Category.RESERVE],
            "оплата в полёте, пауза и резерв распознаются по настоящим таблицам",
        )
        ev = await bulk.evaluate_days(store, uids, days=3, include_trial=False, include_limited=False, now=now)
        check(ev.eligible_ids == sorted(uids[:3]), "в выборку дней попали APPLY, APPLY_FROZEN и отложенные")
        await s.rollback()

    print("[bulk] однократность задачи")
    rid = uuid.uuid4()
    async with maker() as s:
        store = bulk.BulkStore(s)
        job1 = await store.create_job(
            kind="days", request_id=rid, params_hash="h", parent_job_id=None, created_by=uids[0],
            created_by_label="@e2e", params={"days": 3, "notify": None}, segment_hash=ev.segment_hash,
            items=bulk.days_items(ev),
        )
        await s.commit()
        job = await store.get_job(job1)
        check(job is not None and job["params"]["days"] == 3, "params JSONB читается обратно словарём")
        check(job["total"] == 4, "в задаче строки на всех из выборки")

    async with maker() as s:
        try:
            await s.execute(
                text(bulk.CREATE_JOB_SQL),
                {"kind": "message", "request_id": rid, "params_hash": "h", "parent_job_id": None, "created_by": None,
                 "created_by_label": "x", "params": "{}", "segment_hash": "", "total": 0},
            )
            await s.commit()
            check(False, "второй CREATE_JOB_SQL с тем же request_id должен падать")
        except IntegrityError as exc:
            check("request_id" in str(exc), "повтор request_id — unique_violation")
            await s.rollback()

    async with maker() as s:
        store = bulk.BulkStore(s)
        try:
            await store.create_job(
                kind="days", request_id=uuid.uuid4(), params_hash="h2", parent_job_id=None, created_by=None,
                created_by_label="@e2e", params={"days": 1}, segment_hash="", items=[],
            )
            check(False, "вторая активная задача days должна отбиваться")
        except bulk.ActiveJobExists as exc:
            check(exc.job_id == job1, "вторая активная задача days — отказ с номером активной")
        try:
            await store.create_job(
                kind="days", request_id=rid, params_hash="h", parent_job_id=None, created_by=None,
                created_by_label="@e2e", params={}, segment_hash="", items=[],
            )
            check(False, "повтор request_id через BulkStore должен отбиваться")
        except bulk.DuplicateRequest as exc:
            check(exc.job_id == job1, "повтор request_id через BulkStore — DuplicateRequest с номером задачи")

    print("[bulk] аренда и захват строк")
    async with maker() as s:
        store = bulk.BulkStore(s)
        check(await store.acquire_lease(job1, "w1") == "PROCESSING", "первый воркер берёт аренду, QUEUED → PROCESSING")
        await s.commit()
        check(await store.acquire_lease(job1, "w2") is None, "чужой воркер не берёт живую аренду")
        await s.commit()
        check(await store.renew_lease(job1, "w1") == "PROCESSING", "своя аренда продлевается и отдаёт статус")
        check(await store.renew_lease(job1, "w2") is None, "чужая аренда не продлевается")
        await s.commit()
        check(await store.claim(job1, uids[0], "RUNNING") is True, "первая попытка захватывает строку")
        await s.commit()
        check(await store.claim(job1, uids[0], "RUNNING") is False, "вторая попытка захвата — 0 строк")
        await s.commit()
        await store.set_item(job1, uids[0], old_expire_at=now, target_expire_at=now + timedelta(days=3), subscription_id=1, error=None)
        await s.commit()
        running = await store.items(job1, ("RUNNING",))
        check(len(running) == 1 and running[0]["target_expire_at"] is not None, "target пишется и читается")
        check(len(await store.items(job1, ("PENDING",), deferred=True)) == 1, "отложенные выбираются отдельно")
        check(len(await store.items(job1, ("PENDING",), deferred=False)) == 1, "неотложенные выбираются отдельно")
        check(await store.add_frozen_seconds(uids[2], 86400) is True, "дни уходят в остаток паузы")
        check((await store.freeze_state(uids[2]))["remaining_seconds"] == 3600 + 86400, "остаток паузы увеличен")
        state = await store.sub_state(int(rows[uids[0]]["sub_id"]))
        check(state is not None and state["status"] == "ACTIVE", "срок перечитывается из subscriptions")
        await store.set_item(job1, uids[0], status="DONE")
        await store.recount(job1)
        await s.commit()
        # «Проверить вручную» при оплате рядом с записью срока: окно считается от
        # updated_at строки, и давняя оплата в него не попадает.
        check(await store.payment_near_item(job1, uids[0]) is False, "без оплаты рядом с записью срока — не помечаем")
        await s.execute(text("INSERT INTO transactions (user_id, status) VALUES (:u, 'COMPLETED')"), {"u": uids[0]})
        check(await store.payment_near_item(job1, uids[0]) is True, "оплата рядом с записью срока — «проверить вручную»")
        await s.execute(
            text(
                "UPDATE transactions SET created_at = now() - interval '1 hour', updated_at = now() - interval '1 hour' "
                "WHERE user_id = :u AND status = 'COMPLETED'"
            ),
            {"u": uids[0]},
        )
        check(await store.payment_near_item(job1, uids[0]) is False, "давняя оплата не помечается")
        await s.commit()
        job = await store.get_job(job1)
        check(job["applied_count"] == 1 and job["skipped_count"] == 1, "счётчики пересчитываются из строк")
        totals = await store.totals(job1)
        check(totals["applied"] == 1, "итоги по строкам")
        check((await store.breakdown([job1]))[job1].get("RESERVE") == 1, "разбивка по причинам")
        total, page = await store.items_page(job1, ["SKIPPED"], 10, 0)
        check(total == 1 and page[0]["name"] == "e2e", "страница «Кто не получил» с фильтром")
        total, page = await store.items_page(job1, None, 10, 0)
        check(total == 4, "страница без фильтра")
        check((await store.recent_days(uids))["count"] == 1, "«уже получали дни за 24 часа»")
        check(await store.recent_text(uids, "nope") == 0, "«уже получали этот текст» без совпадений")
        check(await store.inflight(job1, ("PENDING", "RUNNING")) == 2, "незавершённые строки считаются")
        await store.unclaim_unstarted(job1)
        await s.commit()
        check(await store.record_feed(uids[0], {"title": "t", "body": "b"}) is True, "лента кабинета пишется")
        await s.commit()

        await s.execute(text("UPDATE bulk_jobs SET lease_until = now() - interval '1 second' WHERE id = :id"), {"id": job1})
        await s.commit()
        check(await store.acquire_lease(job1, "w2") == "PROCESSING", "истёкшую аренду перехватывает другой воркер")
        await s.commit()
        check(job1 not in await store.stalled_jobs(), "живая задача не считается зависшей")
        check(await store.release(job1, "w1", "COMPLETED", finished=True) is False, "бывший владелец не завершает чужую аренду")
        check(await store.skip_pending(job1, "CANCELED") == 2, "остаток PENDING уходит в SKIPPED")
        check(await store.release(job1, "w2", "COMPLETED", finished=True) is True, "владелец аренды завершает задачу")
        await s.commit()

    async with maker() as s:
        store = bulk.BulkStore(s)
        job2 = await store.create_job(
            kind="days", request_id=uuid.uuid4(), params_hash="h3", parent_job_id=None, created_by=None,
            created_by_label="@e2e", params={"days": 1}, segment_hash="", items=[(uids[0], "PENDING", None, False)],
        )
        await s.commit()
        check(job2 != job1, "после завершения первой новая задача days проходит")
        check(await store.cancel_now(job2, "QUEUED", "@e2e") is True, "остановка задачи из очереди")
        await s.commit()
        check((await store.get_job(job2))["status"] == "CANCELED", "остановленная задача CANCELED")
        check(await store.requeue(job2, "PAUSED") is False, "повторная постановка — только из ожидаемого статуса")
        await s.rollback()

    await engine.dispose()
    print("BULK-JOBS SQL OK")


if __name__ == "__main__":
    asyncio.run(main())
