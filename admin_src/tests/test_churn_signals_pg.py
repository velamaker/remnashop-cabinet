"""«Сигналы до ухода» на НАСТОЯЩЕМ Postgres: миграция, индексы, SQL и проход целиком.

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ. Подделка сессии отвечает по куску текста запроса и не знает
ни про частичные уникальные индексы, на которых держится «один вопрос на человека»,
ни про то, как asyncpg выводит типы параметров (на этом уже спотыкалось напоминание
об оплате: один bind в двух ролях — и на бою ни одна отправка не записала итог).
Здесь таблица создаётся ТЕМ ЖЕ DDL, что в миграции 0015, — не копией.

ЧТО ЗАПЕРТО:
  * выборка берёт только действующие подписки и видит отказ любого из двух видов;
  * выборка видит активную заморозку (и не видит снятую);
  * второй вопрос тому же человеку невозможен (индекс), «давно не был» — один на
    одну и ту же отметку последнего онлайна, а новая отметка — новое право;
  * итог отправки пишется, и `sent_at` — только у доставленных;
  * ответ меняет только свою строку и только вопрос;
  * сводка считает ответы и долю «не работает»;
  * проход целиком: второй прогон не шлёт ничего;
  * удаление человека уносит его строки (FK).

ЗАПУСК — ПО ЖЕЛАНИЮ: нужен одноразовый Postgres (RS_PG_DSN), иначе тесты пропускаются.
Данные синтетические.
"""

import importlib
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from _pg_dsn import sqlalchemy_dsn  # noqa: E402 — соседний модуль тестов

cs = importlib.import_module("src.infrastructure.services.overlay_churn_signals")
task = importlib.import_module("src.infrastructure.taskiq.tasks.churn_signals")

DSN = sqlalchemy_dsn()
pytestmark = pytest.mark.skipif(not DSN, reason="нужен RS_PG_DSN (одноразовый Postgres)")

SCHEMA_NAME = "churn_signals_pg_test"
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
UUID_A = "aaaaaaaa-1111-1111-1111-111111111111"

SCHEMA = f"""
DROP SCHEMA IF EXISTS {SCHEMA_NAME} CASCADE;
CREATE SCHEMA {SCHEMA_NAME};
CREATE TYPE {SCHEMA_NAME}.user_role AS ENUM ('USER','ADMIN','DEV','OWNER','PREVIEW');
CREATE TYPE {SCHEMA_NAME}.subscription_status AS ENUM ('ACTIVE','DISABLED','LIMITED','EXPIRED','DELETED');
CREATE TYPE {SCHEMA_NAME}.locale AS ENUM ('RU','EN');
CREATE TABLE {SCHEMA_NAME}.users (
    id SERIAL PRIMARY KEY,
    role {SCHEMA_NAME}.user_role NOT NULL DEFAULT 'USER',
    telegram_id BIGINT,
    language {SCHEMA_NAME}.locale NOT NULL DEFAULT 'RU',
    is_blocked BOOLEAN NOT NULL DEFAULT false,
    is_bot_blocked BOOLEAN NOT NULL DEFAULT false,
    current_subscription_id INTEGER);
CREATE TABLE {SCHEMA_NAME}.subscriptions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES {SCHEMA_NAME}.users(id) ON DELETE CASCADE,
    user_remna_id UUID NOT NULL,
    status {SCHEMA_NAME}.subscription_status NOT NULL DEFAULT 'ACTIVE',
    is_trial BOOLEAN NOT NULL DEFAULT false,
    expire_at TIMESTAMPTZ NOT NULL);
CREATE TABLE {SCHEMA_NAME}.notification_optouts (
    user_id INTEGER NOT NULL REFERENCES {SCHEMA_NAME}.users(id) ON DELETE CASCADE,
    kind VARCHAR(32) NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, kind));
"""


def _freeze_statements() -> list[str]:
    """Таблица заморозок — тем DDL, которым её создаёт бот при старте, а не копией."""
    baseline = importlib.import_module("src.infrastructure.database.overlay_baseline_ddl")
    return [ddl for ddl in baseline.BASELINE_DDL if "subscription_freezes" in ddl]


def _migration_statements() -> list[str]:
    """DDL миграции 0015 — ровно тот, что уедет на бой, а не его пересказ."""
    here = Path(__file__).resolve()
    candidates = [
        Path("/opt/remnashop/src/infrastructure/database/migrations_overlay/versions/0015_churn_signals.py"),
        here.parents[1] / "src/infrastructure/database/migrations_overlay/versions/0015_churn_signals.py",
    ]
    path = next(p for p in candidates if p.exists())
    spec = importlib.util.spec_from_file_location("churn_migration_0015", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    collected: list[str] = []
    module.op = SimpleNamespace(execute=collected.append)
    module.upgrade()
    return collected


@pytest.fixture
async def db():
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    engine = create_async_engine(DSN)
    async with engine.begin() as conn:
        for statement in SCHEMA.strip().split(";\n"):
            if statement.strip():
                await conn.execute(text(statement))
        await conn.execute(text(f"SET search_path TO {SCHEMA_NAME}"))
        for statement in _freeze_statements() + _migration_statements():
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


async def _person(session, *, remna=UUID_A, status="ACTIVE", trial=False, days_left=20,
                  role="USER", telegram_id=555) -> int:
    from sqlalchemy import text

    uid = (
        await session.execute(
            text("INSERT INTO users (role, telegram_id) VALUES (CAST(:r AS user_role), :tg) RETURNING id"),
            {"r": role, "tg": telegram_id},
        )
    ).scalar_one()
    sid = (
        await session.execute(
            text(
                "INSERT INTO subscriptions (user_id, user_remna_id, status, is_trial, expire_at) "
                "VALUES (:u, CAST(:r AS uuid), CAST(:s AS subscription_status), :t, :e) RETURNING id"
            ),
            {"u": uid, "r": remna, "s": status, "t": trial, "e": NOW + timedelta(days=days_left)},
        )
    ).scalar_one()
    await session.execute(
        text("UPDATE users SET current_subscription_id = :s WHERE id = :u"), {"s": sid, "u": uid}
    )
    await session.commit()
    return int(uid)


async def _candidates(session):
    from sqlalchemy import text

    rows = (
        await session.execute(
            text(cs.CANDIDATES_SQL),
            {
                "optout_check": cs.OPTOUT_CHECK,
                "optout_idle": cs.OPTOUT_IDLE,
                "returned_since": NOW - timedelta(days=30),
                "now": NOW,
                "limit": 100,
            },
        )
    ).all()
    await session.rollback()
    return {r._mapping["user_id"]: dict(r._mapping) for r in rows}


async def _claim(session, user_id, kind, seen_at):
    from sqlalchemy import text

    row = (
        await session.execute(
            text(cs.CLAIM_SQL),
            {"user_id": user_id, "kind": kind, "seen_at": seen_at, "now": NOW},
        )
    ).first()
    await session.commit()
    return None if row is None else int(row[0])


async def _result(session, signal_id, sent: bool):
    from sqlalchemy import text

    await session.execute(
        text(cs.RESULT_SQL),
        {"id": signal_id, "status": "sent" if sent else "failed",
         "tg_result": "sent" if sent else "blocked", "sent": sent, "now": NOW},
    )
    await session.commit()


async def test_candidates_are_people_with_a_working_subscription(db):
    active = await _person(db)
    await _person(db, remna="bbbbbbbb-1111-1111-1111-111111111111", status="EXPIRED")
    # Статус ещё ACTIVE, но срок уже прошёл — синхрон статусов не успел; не кандидат.
    await _person(db, remna="cccccccc-1111-1111-1111-111111111111", days_left=-1)
    rows = await _candidates(db)
    assert list(rows) == [active]
    row = rows[active]
    assert row["remna_uuid"] == UUID_A and row["lang"] == "ru"
    assert row["opted_out"] is False and row["check_done"] is False
    assert row["idle_last_sent"] is None and row["idle_open_id"] is None


async def test_optout_of_either_kind_is_seen(db):
    from sqlalchemy import text

    first = await _person(db)
    second = await _person(db, remna="bbbbbbbb-1111-1111-1111-111111111111")
    for uid, kind in ((first, cs.OPTOUT_CHECK), (second, cs.OPTOUT_IDLE)):
        await db.execute(text(cs.OPTOUT_SQL), {"user_id": uid, "kind": kind})
        # Повторное нажатие «Не присылать такое» не падает.
        await db.execute(text(cs.OPTOUT_SQL), {"user_id": uid, "kind": kind})
    await db.commit()
    rows = await _candidates(db)
    assert rows[first]["opted_out"] is True and rows[second]["opted_out"] is True


async def _freeze(session, user_id: int, *, active: bool) -> None:
    from sqlalchemy import text

    await session.execute(
        text(
            "INSERT INTO subscription_freezes (user_id, remna_uuid, remaining_seconds, active) "
            "VALUES (:u, :r, 86400, :a)"
        ),
        {"u": user_id, "r": UUID_A, "a": active},
    )
    await session.commit()


async def test_active_freeze_is_seen_and_a_lifted_one_is_not(db):
    frozen = await _person(db)
    lifted = await _person(db, remna="bbbbbbbb-1111-1111-1111-111111111111")
    plain = await _person(db, remna="cccccccc-1111-1111-1111-111111111111")
    await _freeze(db, frozen, active=True)
    await _freeze(db, lifted, active=False)
    rows = await _candidates(db)
    assert rows[frozen]["frozen"] is True
    assert rows[lifted]["frozen"] is False and rows[plain]["frozen"] is False


async def test_check_is_asked_once_per_person_by_the_index(db):
    uid = await _person(db)
    first = await _claim(db, uid, "check", NOW - timedelta(hours=25))
    assert first is not None
    # Даже с другой отметкой первого подключения (панель пересоздала пользователя) —
    # второй вопрос не вставляется.
    assert await _claim(db, uid, "check", NOW - timedelta(hours=26)) is None
    assert (await _candidates(db))[uid]["check_done"] is True


async def test_idle_is_once_per_offline_stretch(db):
    uid = await _person(db)
    stretch = NOW - timedelta(days=8)
    first = await _claim(db, uid, "idle", stretch)
    assert first is not None
    assert await _claim(db, uid, "idle", stretch) is None
    # Человек подключился и снова пропал — это новый простой и новое право (кулдаун
    # решает крон, а не индекс).
    assert await _claim(db, uid, "idle", NOW - timedelta(days=2)) is not None
    # Вопрос и «давно не был» друг другу не мешают.
    assert await _claim(db, uid, "check", NOW - timedelta(days=40)) is not None


async def test_result_marks_sent_and_failed_does_not_set_sent_at(db):
    from sqlalchemy import text

    uid = await _person(db)
    good = await _claim(db, uid, "idle", NOW - timedelta(days=8))
    bad = await _claim(db, uid, "idle", NOW - timedelta(days=9))
    await _result(db, good, True)
    await _result(db, bad, False)
    rows = {
        r[0]: (r[1], r[2])
        for r in (await db.execute(text("SELECT id, status, sent_at FROM churn_signals"))).all()
    }
    assert rows[good] == ("sent", NOW)
    assert rows[bad][0] == "failed" and rows[bad][1] is None
    # Кулдаун считает только ДОСТАВЛЕННОЕ.
    row = (await _candidates(db))[uid]
    assert row["idle_last_sent"] == NOW
    assert row["idle_open_id"] == good


async def test_returned_is_marked_once(db):
    from sqlalchemy import text

    uid = await _person(db)
    sid = await _claim(db, uid, "idle", NOW - timedelta(days=8))
    await _result(db, sid, True)
    back = NOW + timedelta(hours=2)
    await db.execute(text(cs.RETURNED_SQL), {"id": sid, "returned_at": back})
    await db.execute(text(cs.RETURNED_SQL), {"id": sid, "returned_at": back + timedelta(days=1)})
    await db.commit()
    got = (await db.execute(text("SELECT returned_at FROM churn_signals WHERE id = :i"), {"i": sid})).scalar_one()
    assert got == back
    # Вернувшийся больше не «открытый» — второй раз его не отмечаем.
    assert (await _candidates(db))[uid]["idle_open_id"] is None


async def test_answer_changes_only_own_check_row(db):
    from sqlalchemy import text

    owner = await _person(db)
    stranger = await _person(db, remna="bbbbbbbb-1111-1111-1111-111111111111")
    check = await _claim(db, owner, "check", NOW - timedelta(hours=25))
    idle = await _claim(db, owner, "idle", NOW - timedelta(days=8))

    async def answer(signal_id, user_id, value):
        row = (
            await db.execute(text(cs.ANSWER_SQL), {"answer": value, "id": signal_id, "user_id": user_id})
        ).first()
        await db.commit()
        return row

    assert await answer(check, stranger, "broken") is None
    assert await answer(idle, owner, "broken") is None
    assert await answer(check, owner, "works") is not None
    # Передумал — второй ответ переписывает первый.
    assert await answer(check, owner, "broken") is not None
    got = (await db.execute(text("SELECT answer, answered_at FROM churn_signals WHERE id = :i"),
                            {"i": check})).first()
    assert got[0] == "broken" and got[1] is not None


async def test_stats_count_answers_and_broken(db):
    from sqlalchemy import text

    people = [await _person(db, remna=f"{i:08d}-1111-1111-1111-111111111111") for i in range(4)]
    answers = ["works", "broken", None, None]
    for uid, value in zip(people, answers):
        sid = await _claim(db, uid, "check", NOW - timedelta(hours=25))
        await _result(db, sid, True)
        if value:
            await db.execute(text(cs.ANSWER_SQL), {"answer": value, "id": sid, "user_id": uid})
    idle = await _claim(db, people[0], "idle", NOW - timedelta(days=8))
    await _result(db, idle, True)
    await db.execute(text(cs.RETURNED_SQL), {"id": idle, "returned_at": NOW})
    await db.commit()

    row = (await db.execute(text(cs.STATS_SQL), {"days": 30})).first()
    assert tuple(int(v) for v in row) == (4, 2, 1, 1, 0, 1, 1, 0)
    broken = (await db.execute(text(cs.BROKEN_SQL), {"limit": 10})).all()
    assert [r[0] for r in broken] == [people[1]]


async def test_optouts_summary(db):
    from sqlalchemy import text

    uid = await _person(db)
    await db.execute(text(cs.OPTOUT_SQL), {"user_id": uid, "kind": cs.OPTOUT_IDLE})
    await db.execute(text(cs.OPTOUT_SQL), {"user_id": uid, "kind": "payment_reminder"})
    await db.commit()
    rows = (await db.execute(text(cs.OPTOUTS_SQL),
                             {"optout_check": cs.OPTOUT_CHECK, "optout_idle": cs.OPTOUT_IDLE})).all()
    assert {r[0]: int(r[1]) for r in rows} == {cs.OPTOUT_IDLE: 1}


async def test_whole_run_asks_once_and_second_run_is_silent(db):
    uid = await _person(db)
    panel = {UUID_A: cs.PanelSeen(NOW - timedelta(hours=25), NOW - timedelta(hours=1), "ACTIVE")}
    sent: list = []

    async def fetch():
        return panel

    async def send(c, kind, html, signal_id):
        sent.append((c.user_id, kind, signal_id))
        return task.TG_SENT

    async def noop(_s):
        return None

    config = cs.normalize({"check_enabled": True, "idle_enabled": True})
    first = await task.run_once(db, fetch_panel=fetch, send_tg=send, now=NOW, cfg=config, sleep=noop)
    second = await task.run_once(db, fetch_panel=fetch, send_tg=send, now=NOW, cfg=config, sleep=noop)
    assert first["sent"]["check"] == 1
    assert len(sent) == 1 and sent[0][:2] == (uid, "check")
    assert second["skipped_check"]["already_asked"] == 1 and not second["sent"]


async def test_whole_run_skips_a_frozen_person(db):
    """Настоящая выборка + настоящий проход: на паузе — не пишем, даже в окне."""
    uid = await _person(db)
    await _freeze(db, uid, active=True)
    panel = {UUID_A: cs.PanelSeen(NOW - timedelta(hours=25), NOW - timedelta(days=8), "ACTIVE")}
    sent: list = []

    async def fetch():
        return panel

    async def send(c, kind, html, signal_id):
        sent.append((c.user_id, kind))
        return task.TG_SENT

    async def noop(_s):
        return None

    config = cs.normalize({"check_enabled": True, "idle_enabled": True})
    report = await task.run_once(db, fetch_panel=fetch, send_tg=send, now=NOW, cfg=config, sleep=noop)
    assert sent == []
    assert report["skipped_check"]["frozen"] == 1 and report["skipped_idle"]["frozen"] == 1


async def test_deleting_a_person_removes_their_rows(db):
    from sqlalchemy import text

    uid = await _person(db)
    await _claim(db, uid, "check", NOW - timedelta(hours=25))
    await db.execute(text("DELETE FROM users WHERE id = :u"), {"u": uid})
    await db.commit()
    assert (await db.execute(text("SELECT count(*) FROM churn_signals"))).scalar_one() == 0
