"""Удаление человека на НАСТОЯЩЕМ Postgres: каскады и развилка «платил / нет».

ЗАЧЕМ ЖИВАЯ БАЗА. Здесь всё интересное — в самой базе. Подделка сессии не знает
ни про ON DELETE CASCADE (а денежные таблицы ссылаются на пользователя именно
так, и физическое удаление уносит отчётность), ни про САВЕПОЙНТЫ (а на них
держится терпимость к отсутствующим overlay-таблицам: без савепойнта первый же
запрос к несуществующей таблице переводит транзакцию в «только откат», и дальше
падает всё). Поэтому схему поднимаем настоящую.

ЧТО ЗАПЕРТО ЗДЕСЬ:
  * за человеком есть платёж → строка остаётся, но обезличенной, а платёж цел;
  * платежей нет → строки нет вовсе, и всё связанное ушло каскадом;
  * панель не ответила → НИЧЕГО не изменилось (иначе «удалённый» человек
    остаётся с работающим VPN, а админ уверен, что удалил);
  * личные данные (входы, привязки внешних входов) вычищаются в обоих случаях;
  * подписка не остаётся активной у удалённого человека.

ЗАПУСК — ПО ЖЕЛАНИЮ, нужен одноразовый Postgres:

    docker run -d --name pg -e POSTGRES_PASSWORD=x -p 5432:5432 postgres:17
    RS_PG_DSN=postgresql+asyncpg://postgres:x@localhost/postgres pytest test_user_purge_pg.py
"""

import importlib
import uuid as uuid_lib

import pytest

from _pg_dsn import sqlalchemy_dsn

purge = importlib.import_module("src.infrastructure.services.overlay_user_purge")

DSN = sqlalchemy_dsn()
pytestmark = pytest.mark.skipif(not DSN, reason="нужен RS_PG_DSN (одноразовый Postgres)")

SCHEMA_NAME = "user_purge_pg_test"
PANEL_UUID = "22222222-2222-2222-2222-222222222222"

SCHEMA = f"""
DROP SCHEMA IF EXISTS {SCHEMA_NAME} CASCADE;
CREATE SCHEMA {SCHEMA_NAME};
CREATE TABLE {SCHEMA_NAME}.users (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100),
    username VARCHAR(100),
    email VARCHAR(255),
    pending_email VARCHAR(255),
    password_hash VARCHAR(255),
    email_verification_code_hash VARCHAR(255),
    email_verification_expires_at TIMESTAMPTZ,
    is_email_verified BOOLEAN NOT NULL DEFAULT false,
    telegram_id BIGINT,
    referral_code VARCHAR(64),
    auth_type VARCHAR(32) NOT NULL DEFAULT 'email',
    is_blocked BOOLEAN NOT NULL DEFAULT false,
    cabinet_balance NUMERIC(12,2) NOT NULL DEFAULT 0,
    points INTEGER NOT NULL DEFAULT 0,
    autopay_enabled BOOLEAN NOT NULL DEFAULT false,
    current_subscription_id INTEGER,
    updated_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE {SCHEMA_NAME}.subscriptions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES {SCHEMA_NAME}.users(id) ON DELETE CASCADE,
    user_remna_id UUID NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    updated_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE {SCHEMA_NAME}.transactions (
    payment_id UUID PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES {SCHEMA_NAME}.users(id) ON DELETE CASCADE,
    status VARCHAR(20) NOT NULL DEFAULT 'COMPLETED');
CREATE TABLE {SCHEMA_NAME}.login_events (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES {SCHEMA_NAME}.users(id) ON DELETE CASCADE,
    ip VARCHAR(64));
CREATE TABLE {SCHEMA_NAME}.user_oauth_providers (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES {SCHEMA_NAME}.users(id) ON DELETE CASCADE,
    provider VARCHAR(32) NOT NULL,
    provider_id VARCHAR(128) NOT NULL);
CREATE TABLE {SCHEMA_NAME}.session_invalidations (
    user_id INTEGER PRIMARY KEY REFERENCES {SCHEMA_NAME}.users(id) ON DELETE CASCADE,
    invalidated_at TIMESTAMPTZ NOT NULL);
CREATE TABLE {SCHEMA_NAME}.remna_identity_map (
    panel_uuid UUID PRIMARY KEY,
    panel_id BIGINT NOT NULL,
    telegram_id BIGINT,
    email VARCHAR(255))
"""
# Сознательно НЕ создаём push_subscriptions, known_devices, hwid_devices, admin_2fa,
# user_notifications, balance_topups, gift_payments: на свежей установке их может не
# быть, и удаление обязано пережить это молча. Ровно это здесь и проверяется.


class FakePanel:
    """Панель, которая всё удаляет. `seen` — кого попросили удалить."""

    def __init__(self, fail: bool = False, present: bool = True):
        self.fail = fail
        self.present = present
        self.seen: list[str] = []

    async def delete_user(self, uuid):
        self.seen.append(str(uuid))
        if self.fail:
            raise RuntimeError("panel is down")
        return self.present


@pytest.fixture
async def engine():
    from sqlalchemy import text as sa_text
    from sqlalchemy.ext.asyncio import create_async_engine

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


async def _seed(engine, *, paid: bool, telegram_id: int = 500100, email: str = "Kto@To.ru"):
    from sqlalchemy import text as sa_text

    async with engine.begin() as conn:
        uid = (
            await conn.execute(
                sa_text(
                    "INSERT INTO users (name, email, telegram_id, referral_code, auth_type) "
                    "VALUES ('Кто-то', :e, :t, 'abc123', 'telegram') RETURNING id"
                ),
                {"e": email, "t": telegram_id},
            )
        ).scalar()
        sid = (
            await conn.execute(
                sa_text(
                    "INSERT INTO subscriptions (user_id, user_remna_id) "
                    "VALUES (:u, CAST(:r AS uuid)) RETURNING id"
                ),
                {"u": uid, "r": PANEL_UUID},
            )
        ).scalar()
        await conn.execute(
            sa_text("UPDATE users SET current_subscription_id = :s WHERE id = :u"),
            {"s": sid, "u": uid},
        )
        await conn.execute(
            sa_text("INSERT INTO login_events (user_id, ip) VALUES (:u, '10.0.0.1')"), {"u": uid}
        )
        await conn.execute(
            sa_text(
                "INSERT INTO user_oauth_providers (user_id, provider, provider_id) "
                "VALUES (:u, 'telegram', '500100')"
            ),
            {"u": uid},
        )
        await conn.execute(
            sa_text(
                "INSERT INTO remna_identity_map (panel_uuid, panel_id, telegram_id, email) "
                "VALUES (CAST(:r AS uuid), 77, :t, :e)"
            ),
            {"r": PANEL_UUID, "t": telegram_id, "e": email},
        )
        if paid:
            await conn.execute(
                sa_text("INSERT INTO transactions (payment_id, user_id) VALUES (:p, :u)"),
                {"p": str(uuid_lib.uuid4()), "u": uid},
            )
    return uid


async def _run_purge(engine, uid, panel):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        report = await purge.purge_user(session, panel, uid)
        await session.commit()
    return report


async def _scalar(engine, sql, params=None):
    from sqlalchemy import text as sa_text

    async with engine.begin() as conn:
        return (await conn.execute(sa_text(sql), params or {})).scalar()


async def test_платившего_обезличиваем_а_платёж_сохраняем(engine):
    uid = await _seed(engine, paid=True)
    panel = FakePanel()

    report = await _run_purge(engine, uid, panel)

    assert report["mode"] == "anonymized"
    assert panel.seen == [PANEL_UUID], "аккаунт в панели обязан быть удалён"

    row = await _scalar(
        engine,
        "SELECT name || '|' || COALESCE(email,'—') || '|' || COALESCE(telegram_id::text,'—') "
        "|| '|' || auth_type || '|' || is_blocked::text FROM users WHERE id = :u",
        {"u": uid},
    )
    assert row == "Удалённый аккаунт|—|—|deleted|true"
    assert await _scalar(engine, "SELECT count(*) FROM transactions WHERE user_id = :u", {"u": uid}) == 1
    assert await _scalar(engine, "SELECT count(*) FROM login_events WHERE user_id = :u", {"u": uid}) == 0
    assert (
        await _scalar(engine, "SELECT count(*) FROM user_oauth_providers WHERE user_id = :u", {"u": uid})
        == 0
    ), "внешняя личность обязана исчезнуть вместе с человеком"
    assert await _scalar(engine, "SELECT status FROM subscriptions WHERE user_id = :u", {"u": uid}) == "DELETED"
    assert (
        await _scalar(engine, "SELECT count(*) FROM session_invalidations WHERE user_id = :u", {"u": uid})
        == 1
    ), "открытые вкладки обязаны перестать работать"


async def test_не_платившего_удаляем_совсем(engine):
    uid = await _seed(engine, paid=False)

    report = await _run_purge(engine, uid, FakePanel())

    assert report["mode"] == "purged"
    assert await _scalar(engine, "SELECT count(*) FROM users WHERE id = :u", {"u": uid}) == 0
    assert await _scalar(engine, "SELECT count(*) FROM subscriptions WHERE user_id = :u", {"u": uid}) == 0
    assert await _scalar(engine, "SELECT count(*) FROM login_events WHERE user_id = :u", {"u": uid}) == 0


async def test_панель_молчит_значит_не_удаляем_ничего(engine):
    uid = await _seed(engine, paid=False)

    with pytest.raises(purge.PanelUnavailable):
        await _run_purge(engine, uid, FakePanel(fail=True))

    # Главное: человек на месте и НЕ обезличен — иначе админ уверен, что удалил,
    # а доступ к VPN продолжает работать.
    assert await _scalar(engine, "SELECT auth_type FROM users WHERE id = :u", {"u": uid}) == "telegram"
    assert await _scalar(engine, "SELECT count(*) FROM login_events WHERE user_id = :u", {"u": uid}) == 1


async def test_личность_в_карте_панели_чистится_а_строка_остаётся(engine):
    uid = await _seed(engine, paid=True)

    await _run_purge(engine, uid, FakePanel())

    row = await _scalar(
        engine,
        "SELECT COALESCE(telegram_id::text,'—') || '|' || COALESCE(email,'—') || '|' || panel_id::text "
        "FROM remna_identity_map WHERE panel_uuid = CAST(:r AS uuid)",
        {"r": PANEL_UUID},
    )
    # Сопоставление uuid↔id панели живо (по нему работает слой совместимости с 3.x),
    # а личные данные из него стёрты.
    assert row == "—|—|77"


async def test_его_уже_нет_в_панели_это_успех(engine):
    uid = await _seed(engine, paid=False)

    report = await _run_purge(engine, uid, FakePanel(present=False))

    assert report["panel_accounts_removed"] == 0
    assert await _scalar(engine, "SELECT count(*) FROM users WHERE id = :u", {"u": uid}) == 0


async def test_слово_подтверждения_принимает_оба_языка():
    assert purge.confirm_matches("удалить")
    assert purge.confirm_matches("  УДАЛИТЬ ")
    assert purge.confirm_matches("delete")
    assert not purge.confirm_matches("")
    assert not purge.confirm_matches(None)
    assert not purge.confirm_matches("удали")
