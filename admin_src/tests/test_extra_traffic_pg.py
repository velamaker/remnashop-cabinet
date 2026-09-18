"""Докупка трафика на НАСТОЯЩЕМ Postgres: то, чего подделки не видят вовсе.

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ, И ПОЧЕМУ ОН ПОЯВИЛСЯ ПОСЛЕ РЕВЬЮ. Подделка сессии отвечает
на запрос по куску текста: она не проверяет ни типы, ни замки, ни UNIQUE. На бою
это стоило бы каждой покупки — `UPDATE subscriptions SET status = :s WHERE ... AND
status::text <> :s` подставлял ОДИН bind и как enum, и как text, а asyncpg отвечает
на такое `DatatypeMismatchError`. Подделка молчала, потому что для неё это просто
строка. А в жизни покупка падала бы ПОСЛЕ успешного PATCH в панель: лимит поднят,
денег не взяли, записи о прибавке нет — крон её никогда не опустит, а повтор кнопки
поднимет ещё раз.

Поэтому здесь живая схема с НАСТОЯЩИМИ enum-типами (`subscription_status`,
`transaction_status`, `plan_traffic_limit_strategy`) — ровно теми, что создаёт
миграция базы 0001, — и покупка идёт через настоящую ручку, а не через копию её
шагов: копия не поймала бы ни потерянный `CAST`, ни пропавший повторный поиск
`request_id` под замком.

ЗАПУСК — ПО ЖЕЛАНИЮ: нужен одноразовый Postgres, поэтому тесты включаются только
переменной RS_PG_DSN и в обычном прогоне пропускаются (как test_extra_device_pg).

    docker run -d --name pg -e POSTGRES_PASSWORD=x -p 5432:5432 postgres:17
    RS_PG_DSN=postgresql+asyncpg://postgres:x@localhost/postgres pytest test_extra_traffic_pg.py

Схему создаём сами и минимальную: цель — поведение базы, а не миграции (их
проверяет scripts/migration-e2e.sh). Данные синтетические.
"""

import asyncio
import contextlib
import importlib
import os
import uuid as uuid_lib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

extra = importlib.import_module("src.infrastructure.services.overlay_extra_traffic")

from src.core.utils.converters import gb_to_bytes  # noqa: E402

DSN = os.environ.get("RS_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="нужен RS_PG_DSN (одноразовый Postgres)")

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
PANEL_UUID = "11111111-1111-1111-1111-111111111111"
# Создан 7-го числа: MONTH_ROLLING обнулит расход 7-го в 00:10 UTC.
PANEL_CREATED = datetime(2025, 5, 7, 14, 30, tzinfo=timezone.utc)
PLAN_GB = 200

# ОТДЕЛЬНАЯ СХЕМА, а не public: на том же RS_PG_DSN гоняются соседние opt-in тесты,
# и уронить их таблицы своим DROP недопустимо.
SCHEMA_NAME = "extra_traffic_pg_test"

# Enum-типы называются ТОЧНО так же, как в миграции базы 0001: на этом и держится
# проверка `CAST(:s AS subscription_status)`. Создаём их в своей схеме.
SCHEMA = f"""
DROP SCHEMA IF EXISTS {SCHEMA_NAME} CASCADE;
CREATE SCHEMA {SCHEMA_NAME};
CREATE TYPE {SCHEMA_NAME}.subscription_status AS ENUM
    ('ACTIVE','LIMITED','EXPIRED','DISABLED','DELETED');
CREATE TYPE {SCHEMA_NAME}.transaction_status AS ENUM
    ('PENDING','COMPLETED','FAILED','CANCELED','REFUNDED');
CREATE TYPE {SCHEMA_NAME}.plan_traffic_limit_strategy AS ENUM
    ('NO_RESET','DAY','WEEK','MONTH','MONTH_ROLLING');
CREATE TABLE {SCHEMA_NAME}.users (
    id SERIAL PRIMARY KEY, role VARCHAR(20) DEFAULT 'USER',
    cabinet_balance NUMERIC(12,2) NOT NULL DEFAULT 0, current_subscription_id INTEGER);
CREATE TABLE {SCHEMA_NAME}.subscriptions (
    id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL,
    status {SCHEMA_NAME}.subscription_status NOT NULL DEFAULT 'ACTIVE',
    is_trial BOOLEAN NOT NULL DEFAULT false, expire_at TIMESTAMPTZ,
    traffic_limit INTEGER NOT NULL,
    traffic_limit_strategy {SCHEMA_NAME}.plan_traffic_limit_strategy NOT NULL DEFAULT 'MONTH_ROLLING',
    plan_snapshot JSONB NOT NULL, user_remna_id VARCHAR(64),
    updated_at TIMESTAMPTZ DEFAULT now(), created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE {SCHEMA_NAME}.transactions (
    payment_id UUID PRIMARY KEY, user_id INTEGER NOT NULL,
    status {SCHEMA_NAME}.transaction_status NOT NULL DEFAULT 'PENDING',
    created_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE {SCHEMA_NAME}.subscription_freezes (
    user_id INTEGER PRIMARY KEY, frozen_at TIMESTAMPTZ, active BOOLEAN NOT NULL DEFAULT false);
CREATE TABLE {SCHEMA_NAME}.reserve_grants (
    id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, ended BOOLEAN NOT NULL DEFAULT false,
    reserve_expire_at TIMESTAMPTZ);
CREATE TABLE {SCHEMA_NAME}.extra_traffic_grants (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES {SCHEMA_NAME}.users(id) ON DELETE CASCADE,
    subscription_id INTEGER NOT NULL, plan_id INTEGER NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'active', gb INTEGER NOT NULL CHECK (gb > 0),
    strategy VARCHAR(16) NOT NULL, panel_created_at TIMESTAMPTZ,
    granted_at TIMESTAMPTZ NOT NULL, ends_at TIMESTAMPTZ, last_applied_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ, end_reason VARCHAR(32), fail_count INTEGER NOT NULL DEFAULT 0,
    carried_value NUMERIC(12,2), created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_etg_status CHECK (status IN ('active','ended','burned','revoked')));
CREATE TABLE {SCHEMA_NAME}.extra_traffic_orders (
    id BIGSERIAL PRIMARY KEY, request_id UUID NOT NULL UNIQUE, payment_id UUID UNIQUE,
    source VARCHAR(16) NOT NULL,
    user_id INTEGER NOT NULL REFERENCES {SCHEMA_NAME}.users(id) ON DELETE CASCADE,
    subscription_id INTEGER NOT NULL,
    grant_id BIGINT REFERENCES {SCHEMA_NAME}.extra_traffic_grants(id) ON DELETE SET NULL,
    status VARCHAR(16) NOT NULL, gb INTEGER NOT NULL CHECK (gb > 0),
    amount NUMERIC(12,2) NOT NULL CHECK (amount > 0),
    currency VARCHAR(8) NOT NULL DEFAULT 'RUB', window_end TIMESTAMPTZ,
    panel_created_at TIMESTAMPTZ, payment_url TEXT, attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT, reason VARCHAR(32), credited_at TIMESTAMPTZ, applied_at TIMESTAMPTZ,
    rejected_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_eto_status CHECK (status IN ('pending','credited','applied','rejected')));
"""

PATCHED: list[int] = []


class FakeUsers:
    def __init__(self, status: str = "ACTIVE") -> None:
        self.status = status

    async def update_user(self, body):
        limit = int(body.traffic_limit_bytes)
        PATCHED.append(limit)
        return SimpleNamespace(traffic_limit_bytes=limit, status=self.status)


class FakeSdk:
    """Панель принимает любой лимит: здесь проверяется база, а не она."""

    def __init__(self, status: str = "ACTIVE") -> None:
        self.users = FakeUsers(status)


class FakeRemnawave:
    def __init__(self, *, panel_status: str = "LIMITED", limit_gb: int = PLAN_GB) -> None:
        self.sdk = FakeSdk("ACTIVE")
        self.panel_status = panel_status
        self.limit_gb = limit_gb

    async def get_user_by_uuid(self, uuid):
        return SimpleNamespace(
            uuid=uuid,
            traffic_limit_bytes=gb_to_bytes(self.limit_gb),
            status=self.panel_status,
            created_at=PANEL_CREATED,
            user_traffic=SimpleNamespace(used_traffic_bytes=gb_to_bytes(self.limit_gb)),
        )


class _Gateways:
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
    """Пишет транзакцию в живую таблицу: она с enum, и её тоже надо проверить."""

    def __init__(self, engine) -> None:
        self.engine = engine

    async def create(self, transaction):
        from sqlalchemy import text as sa_text

        async with self.engine.begin() as conn:
            await conn.execute(
                sa_text(
                    "INSERT INTO transactions (payment_id, user_id, status) "
                    "VALUES (:p, :u, 'COMPLETED')"
                ),
                {"p": str(transaction.payment_id), "u": transaction.user_id},
            )
        return transaction


class _Notifier:
    async def notify_admins(self, payload):
        return None

    async def notify_user(self, user, payload=None, **kw):
        return None


class _CreatePayment:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, user, data):
        self.calls += 1
        return SimpleNamespace(id=uuid_lib.uuid4(), url="https://pay.example.test/1")


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


async def _seed(engine, *, balance="1000", traffic_limit=PLAN_GB, status="LIMITED"):
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
                    "INSERT INTO subscriptions (user_id, status, expire_at, traffic_limit, "
                    "plan_snapshot, user_remna_id) VALUES (:u, CAST(:s AS subscription_status), "
                    ":e, :t, CAST(:p AS jsonb), :r) RETURNING id"
                ),
                {
                    "u": uid,
                    "s": status,
                    "e": NOW + timedelta(days=40),
                    "t": traffic_limit,
                    "p": '{"id": 7, "traffic_limit": %d}' % PLAN_GB,
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
    return dict(extra.DEFAULT_CONFIG, enabled=True, price_rub=50, gb_per_purchase=50, **over)


@contextlib.contextmanager
def _patched(endpoint, live):
    """Время и конфиг — фиксированные: срок не должен зависеть от часов машины."""
    saved = (endpoint.datetime_now, endpoint._assert_email_verified, extra.load_config)
    endpoint.datetime_now = lambda: NOW
    endpoint._assert_email_verified = lambda user: None
    extra.load_config = lambda: live
    try:
        yield
    finally:
        endpoint.datetime_now, endpoint._assert_email_verified, extra.load_config = saved


async def _buy(engine, uid, request_id, *, cfg=None, pay="balance", remnawave=None, payments=None):
    """Одна покупка СВОЕЙ сессией — ровно так, как её делает кабинет.

    Зовём НАСТОЯЩУЮ ручку, а не повторяем её шаги: иначе ни потерянный `CAST`, ни
    пропавший повторный поиск `request_id` под замком тест бы не увидел.
    """
    import src.web.endpoints.public.extra_traffic as endpoint
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from src.core.enums import PaymentGatewayType

    maker = async_sessionmaker(engine, expire_on_commit=False)
    live = cfg or _cfg()
    raw = endpoint.buy_extra_traffic.__dishka_orig_func__
    async with maker() as session:
        with _patched(endpoint, live):
            result = await raw(
                body=endpoint.BuyRequest(
                    request_id=request_id,
                    pay=pay,
                    gateway_type=PaymentGatewayType.YOOMONEY if pay == "gateway" else None,
                    expected_amount=Decimal(50),
                    expected_gb=50,
                ),
                user=SimpleNamespace(id=uid, log=f"[USER:{uid}]", auth_type=None),
                session=session,
                remnawave=remnawave or FakeRemnawave(),
                payment_gateway_dao=_Gateways(),
                transaction_dao=_TxDao(engine),
                create_payment=payments or _CreatePayment(),
                notifier=_Notifier(),
            )
    if result.get("repeat"):
        return "replayed"
    if result.get("result") == "not_available":
        return result["reason"]
    return result.get("result")


async def _scalar(engine, sql, **params):
    from sqlalchemy import text as sa_text

    async with engine.connect() as conn:
        return (await conn.execute(sa_text(sql), params)).scalar()


# ── BLOCKER: покупка на живой схеме ─────────────────────────────────────────


async def test_balance_purchase_survives_real_enum_columns(engine):
    """Главный случай файла: покупка проходит ЦЕЛИКОМ на настоящих типах.

    Мутация «убрать CAST(:s AS subscription_status)» роняет тест: asyncpg отвечает
    DatatypeMismatchError, покупка падает ПОСЛЕ успешного PATCH — лимит поднят,
    денег нет, записи нет.
    """
    PATCHED.clear()
    uid, sid = await _seed(engine)

    assert await _buy(engine, uid, uuid_lib.uuid4()) == "applied"

    # Деньги списаны ровно один раз.
    assert await _scalar(engine, "SELECT cabinet_balance FROM users WHERE id = :u", u=uid) == Decimal(
        "950.00"
    )
    # Прибавка и заказ записаны, и оба ссылаются друг на друга.
    assert await _scalar(
        engine, "SELECT count(*) FROM extra_traffic_grants WHERE user_id = :u AND status='active'", u=uid
    ) == 1
    assert await _scalar(
        engine, "SELECT count(*) FROM extra_traffic_orders WHERE user_id = :u AND status='applied'", u=uid
    ) == 1
    assert await _scalar(engine, "SELECT grant_id IS NOT NULL FROM extra_traffic_orders WHERE user_id = :u", u=uid)
    # Лимит поднят ровно на объём покупки — и в панели, и в нашей строке.
    assert PATCHED == [gb_to_bytes(PLAN_GB) + gb_to_bytes(50)]
    assert await _scalar(engine, "SELECT traffic_limit FROM subscriptions WHERE id = :s", s=sid) == PLAN_GB + 50
    # Панель сняла LIMITED — статус из её ответа записан в строку (это и есть enum).
    assert await _scalar(engine, "SELECT status::text FROM subscriptions WHERE id = :s", s=sid) == "ACTIVE"
    # Срок прибавки — ближайшее обнуление, а не конец подписки.
    ends = await _scalar(engine, "SELECT ends_at FROM extra_traffic_grants WHERE user_id = :u", u=uid)
    assert ends == datetime(2026, 10, 7, 0, 10, tzinfo=timezone.utc)
    # Якорь окна обязан уехать в строку: без него крон прибавку не закроет.
    assert await _scalar(
        engine, "SELECT panel_created_at FROM extra_traffic_grants WHERE user_id = :u", u=uid
    ) == PANEL_CREATED


async def test_gateway_purchase_then_webhook_on_real_schema(engine):
    """Картой: счёт → строка заказа → вебхук → прибавка. Повтор вебхука — без второй."""
    PATCHED.clear()
    uid, sid = await _seed(engine)
    payments = _CreatePayment()

    assert await _buy(engine, uid, uuid_lib.uuid4(), pay="gateway", payments=payments) == "pending"
    assert payments.calls == 1
    payment_id = await _scalar(
        engine, "SELECT payment_id FROM extra_traffic_orders WHERE user_id = :u", u=uid
    )
    assert await _scalar(engine, "SELECT status FROM extra_traffic_orders WHERE user_id = :u", u=uid) == "pending"

    from sqlalchemy.ext.asyncio import async_sessionmaker

    maker = async_sessionmaker(engine, expire_on_commit=False)
    remnawave = FakeRemnawave()
    saved = extra.load_config
    extra.load_config = _cfg
    try:
        async with maker() as session:
            result = await extra.handle_paid_order(
                session, remnawave.sdk, remnawave, payment_id, now=NOW
            )
            assert result["result"] == "applied"
        # Повтор вебхука: денег второй раз не зачисляем, панель не трогаем.
        before = len(PATCHED)
        async with maker() as session:
            again = await extra.handle_paid_order(
                session, remnawave.sdk, remnawave, payment_id, now=NOW
            )
        assert again["repeat"] is True
        assert len(PATCHED) == before
    finally:
        extra.load_config = saved

    assert await _scalar(engine, "SELECT status FROM extra_traffic_orders WHERE user_id = :u", u=uid) == "applied"
    assert await _scalar(engine, "SELECT count(*) FROM extra_traffic_grants WHERE user_id = :u", u=uid) == 1
    # Деньги пришли на баланс и тут же ушли в покупку: итог — исходные 1000.
    assert await _scalar(engine, "SELECT cabinet_balance FROM users WHERE id = :u", u=uid) == Decimal("1000.00")
    assert await _scalar(engine, "SELECT traffic_limit FROM subscriptions WHERE id = :s", s=sid) == PLAN_GB + 50


async def test_same_request_id_in_two_sessions_buys_once(engine):
    """Две вкладки с одним ключом — одна прибавка и одно списание.

    Мутация «убрать повторный поиск request_id под замком» краснеет именно здесь:
    без него обе сессии проходят проверку до замка и покупают дважды.
    """
    PATCHED.clear()
    uid, _sid = await _seed(engine)
    rid = uuid_lib.uuid4()

    results = await asyncio.gather(_buy(engine, uid, rid), _buy(engine, uid, rid))

    assert sorted(results) == ["applied", "replayed"], results
    assert await _scalar(engine, "SELECT count(*) FROM extra_traffic_grants WHERE user_id = :u", u=uid) == 1
    assert await _scalar(engine, "SELECT count(*) FROM extra_traffic_orders WHERE user_id = :u", u=uid) == 1
    assert await _scalar(engine, "SELECT cabinet_balance FROM users WHERE id = :u", u=uid) == Decimal("950.00")


async def test_two_different_clicks_race_for_the_row_lock(engine):
    """Два РАЗНЫХ ключа подряд — две прибавки и ровно два объёма, не больше.

    Замок строки `users` делает покупки последовательными: без него обе прочитали бы
    один и тот же лимит панели и вторая затёрла бы первую.
    """
    PATCHED.clear()
    uid, sid = await _seed(engine)

    results = await asyncio.gather(
        _buy(engine, uid, uuid_lib.uuid4()), _buy(engine, uid, uuid_lib.uuid4())
    )

    assert results == ["applied", "applied"], results
    assert await _scalar(engine, "SELECT count(*) FROM extra_traffic_grants WHERE user_id = :u", u=uid) == 2
    assert await _scalar(engine, "SELECT cabinet_balance FROM users WHERE id = :u", u=uid) == Decimal("900.00")
    # Вторая покупка считает лимит от значения панели, поэтому здесь важно только
    # то, что объёмы не потерялись: сумма прибавок ровно 100 ГБ.
    assert await _scalar(engine, "SELECT sum(gb) FROM extra_traffic_grants WHERE user_id = :u", u=uid) == 100


async def test_disabled_sales_do_not_touch_the_row_or_the_panel(engine):
    """Выключенные продажи — это состояние по умолчанию: ни замка, ни похода в панель."""

    class ExplodingPanel(FakeRemnawave):
        async def get_user_by_uuid(self, uuid):
            raise AssertionError("выключенная докупка не должна ходить в панель")

    uid, _sid = await _seed(engine)
    reason = await _buy(
        engine,
        uid,
        uuid_lib.uuid4(),
        cfg=dict(extra.DEFAULT_CONFIG, enabled=False),
        remnawave=ExplodingPanel(),
    )
    assert reason == "disabled"
    assert await _scalar(engine, "SELECT count(*) FROM extra_traffic_orders WHERE user_id = :u", u=uid) == 0


async def test_cron_lowers_the_limit_on_the_real_schema(engine):
    """Крон доживает прибавку до конца: записи закрыты, лимит опущен ровно на объём."""
    PATCHED.clear()
    uid, sid = await _seed(engine)
    assert await _buy(engine, uid, uuid_lib.uuid4()) == "applied"
    PATCHED.clear()

    from sqlalchemy.ext.asyncio import async_sessionmaker

    maker = async_sessionmaker(engine, expire_on_commit=False)
    # «Сейчас» — уже после обнуления 7 октября.
    later = datetime(2026, 10, 7, 0, 30, tzinfo=timezone.utc)
    async with maker() as session:
        out = await extra.reconcile_user(
            session,
            sdk=FakeSdk(),
            user_id=uid,
            config=_cfg(),
            now=later,
            remnawave=FakeRemnawave(limit_gb=PLAN_GB + 50, panel_status="ACTIVE"),
        )

    assert out["ended"] and out["ended_gb"] == 50
    assert PATCHED == [gb_to_bytes(PLAN_GB)]
    assert await _scalar(engine, "SELECT traffic_limit FROM subscriptions WHERE id = :s", s=sid) == PLAN_GB
    assert await _scalar(
        engine, "SELECT count(*) FROM extra_traffic_grants WHERE user_id = :u AND status='active'", u=uid
    ) == 0


async def test_paused_subscription_gets_the_limit_back(engine):
    """ПАУЗА НЕ ДАРИТ ОБЪЁМ. Раньше записи гасились, а лимит оставался поднятым.

    Двадцать минут паузы — и прибавка оставалась в панели навсегда, повторяясь
    каждый месяц. Мутация «гасить записи на паузе без PATCH» краснеет здесь.
    """
    from sqlalchemy import text as sa_text

    PATCHED.clear()
    uid, sid = await _seed(engine)
    assert await _buy(engine, uid, uuid_lib.uuid4()) == "applied"
    PATCHED.clear()

    async with engine.begin() as conn:
        await conn.execute(
            sa_text(
                "INSERT INTO subscription_freezes (user_id, frozen_at, active) "
                "VALUES (:u, :f, true)"
            ),
            {"u": uid, "f": NOW},
        )

    from sqlalchemy.ext.asyncio import async_sessionmaker

    maker = async_sessionmaker(engine, expire_on_commit=False)
    later = datetime(2026, 10, 7, 0, 30, tzinfo=timezone.utc)
    async with maker() as session:
        await extra.reconcile_user(
            session,
            sdk=FakeSdk(),
            user_id=uid,
            config=_cfg(),
            now=later,
            remnawave=FakeRemnawave(limit_gb=PLAN_GB + 50, panel_status="DISABLED"),
        )

    assert PATCHED == [gb_to_bytes(PLAN_GB)], "лимит на паузе обязан вернуться к тарифному"
    assert await _scalar(engine, "SELECT traffic_limit FROM subscriptions WHERE id = :s", s=sid) == PLAN_GB
    assert await _scalar(
        engine, "SELECT count(*) FROM extra_traffic_grants WHERE user_id = :u AND status='active'", u=uid
    ) == 0


async def test_reserve_keeps_its_safety_gigabyte(engine):
    """РЕЗЕРВ — исключение: записи гасятся, панель не трогаем.

    Резерв ставит там лимит 1 ГБ, и любой PATCH вернул бы истёкшему человеку
    полноценный объём.
    """
    from sqlalchemy import text as sa_text

    PATCHED.clear()
    uid, _sid = await _seed(engine)
    assert await _buy(engine, uid, uuid_lib.uuid4()) == "applied"
    PATCHED.clear()

    async with engine.begin() as conn:
        await conn.execute(
            sa_text(
                "INSERT INTO reserve_grants (user_id, ended, reserve_expire_at) "
                "VALUES (:u, false, :e)"
            ),
            {"u": uid, "e": NOW + timedelta(days=40)},
        )
        await conn.execute(sa_text("UPDATE subscriptions SET traffic_limit = 1 WHERE user_id = :u"), {"u": uid})

    from sqlalchemy.ext.asyncio import async_sessionmaker

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        out = await extra.reconcile_user(
            session,
            sdk=FakeSdk(),
            user_id=uid,
            config=_cfg(),
            now=NOW + timedelta(days=1),
            remnawave=FakeRemnawave(limit_gb=1),
        )

    assert out["reason"] == "reserve"
    assert PATCHED == [], "лимит резерва (1 ГБ) трогать нельзя"


async def test_cron_keeps_the_fractional_part_of_the_panel_limit(engine):
    """Крон считает в БАЙТАХ: лимит 300,4 ГБ не должен стать ровно 300.

    Наша колонка хранит округлённые ГБ. Если крон отдаст в панель `gb_to_bytes(300)`,
    человек тихо потеряет 0,4 ГБ — и ещё столько же на следующей покупке. Мутация
    «считать от округлённых ГБ» краснеет здесь.
    """
    PATCHED.clear()
    uid, _sid = await _seed(engine)
    assert await _buy(engine, uid, uuid_lib.uuid4()) == "applied"
    PATCHED.clear()

    # В панели лимит НЕ кратен гигабайту: 300 ГБ тарифа + 0,4 ГБ + 50 докупленных.
    odd_plan = gb_to_bytes(PLAN_GB) + 400_000_000
    panel_now = odd_plan + gb_to_bytes(50)

    class OddPanel(FakeRemnawave):
        async def get_user_by_uuid(self, uuid):
            return SimpleNamespace(
                uuid=uuid,
                traffic_limit_bytes=panel_now,
                status="ACTIVE",
                created_at=PANEL_CREATED,
                user_traffic=SimpleNamespace(used_traffic_bytes=0),
            )

    from sqlalchemy.ext.asyncio import async_sessionmaker

    maker = async_sessionmaker(engine, expire_on_commit=False)
    later = datetime(2026, 10, 7, 0, 30, tzinfo=timezone.utc)
    async with maker() as session:
        await extra.reconcile_user(
            session,
            sdk=FakeSdk(),
            user_id=uid,
            config=_cfg(),
            now=later,
            remnawave=OddPanel(),
        )

    assert PATCHED == [odd_plan], "дробная часть лимита обязана уцелеть"


async def test_anchor_from_the_process_cache_lands_in_the_grant_row(engine):
    """Якорь мог прийти из кеша процесса, а не из ответа панели — пишем ЕГО.

    С пустым `panel_created_at` крон не посчитает момент обнуления, и прибавка
    останется в панели до следующего продления. Мутация «писать panel.created_at как
    есть» краснеет здесь.
    """
    PATCHED.clear()
    uid, _sid = await _seed(engine)

    class SilentPanel(FakeRemnawave):
        """Панель ответила, но даты создания в ответе нет (обрезанный ответ 3.x)."""

        async def get_user_by_uuid(self, uuid):
            return SimpleNamespace(
                uuid=uuid,
                traffic_limit_bytes=gb_to_bytes(PLAN_GB),
                status="ACTIVE",
                created_at=None,
                user_traffic=SimpleNamespace(used_traffic_bytes=0),
            )

    # Кеш процесса знает дату: её положил любой прошлый успешный ответ панели.
    extra.remember_created_at(PANEL_UUID, PANEL_CREATED)
    try:
        assert await _buy(engine, uid, uuid_lib.uuid4(), remnawave=SilentPanel()) == "applied"
    finally:
        extra._CREATED_CACHE.clear()

    assert await _scalar(
        engine, "SELECT panel_created_at FROM extra_traffic_grants WHERE user_id = :u", u=uid
    ) == PANEL_CREATED
