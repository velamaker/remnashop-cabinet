"""Семейные профили на НАСТОЯЩЕМ Postgres: гонки, каскады и сбои панели.

ЗАЧЕМ ЖИВАЯ БАЗА. Всё, на чём держится семья, живёт в самой базе: замок строки
владельца (двойной клик «создать» из двух вкладок), UNIQUE по request_id и имени,
каскады FK (удаление теневого аккаунта уносит подписку и строку профиля, удаление
владельца — всю семью), перечисления базы в колонках подписки. Подделка сессии
ничего этого не знает.

Схема — НАСТОЯЩАЯ: таблицы базы из её ORM-моделей, наши служебные таблицы тем же
DDL, которым их создаёт бот при старте, и миграция 0016 — ровно та, что уедет на
бой. Теневые аккаунты и подписки создают базовые DAO — так же, как в проде.

Панель — подделка SDK, как в тестах докупки устройства: она помнит пользователей,
считает вызовы и умеет «падать» по команде теста. В сеть ничего не уходит.

ЗАПУСК — ПО ЖЕЛАНИЮ, нужен одноразовый Postgres:

    docker run -d --name pg -e POSTGRES_PASSWORD=x -p 5432:5432 postgres:17
    RS_PG_DSN=postgresql+asyncpg://postgres:x@localhost/postgres pytest test_family_pg.py
"""

import ast
import asyncio
import importlib
import importlib.util
import uuid as uuid_lib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from _pg_dsn import sqlalchemy_dsn  # noqa: E402 — соседний модуль тестов

family = importlib.import_module("src.infrastructure.services.overlay_family")

DSN = sqlalchemy_dsn()
pytestmark = pytest.mark.skipif(not DSN, reason="нужен RS_PG_DSN (одноразовый Postgres)")

# Отдельная схема: на том же RS_PG_DSN гоняются соседние opt-in тесты.
SCHEMA_NAME = "family_pg_test"
SQUAD = "11111111-2222-3333-4444-555555555555"


def now() -> datetime:
    """Настоящие часы: срок сравнивают и база (now()), и код — фиксированная дата
    протухла бы через сутки."""
    return datetime.now(timezone.utc)


# ── подделка панели ─────────────────────────────────────────────────────────


class NotFoundError(Exception):
    """Как в remnapy: код узнаёт её по имени класса."""

    status_code = 404


class ConflictError(Exception):
    status_code = 409


class BadRequestError(Exception):
    """Панель ответила и отказала: пользователь точно не создан."""

    status_code = 400


class Panel:
    """Панель в памяти. `calls` — что у неё просили, по порядку."""

    def __init__(self) -> None:
        self.store: dict[str, SimpleNamespace] = {}
        self.calls: list[tuple] = []
        self.fail_create: Exception | None = None
        self.lose_create_response = False
        self.fail_lookup = False
        self.fail_delete = False
        self.fail_update = False
        # «Медленная панель»: вызовы update/disable ждут, пока тест не откроет ворота.
        self.gate: asyncio.Event | None = None
        self.entered = asyncio.Event()
        # uuid, которые «слой 3.x не сопоставил»: по uuid — 404, по имени — есть.
        self.unmapped: set[str] = set()
        self.users = _Users(self)
        self.hwid = _Hwid(self)
        self.ip_control = _Connections()

    def by_name(self, name: str) -> SimpleNamespace | None:
        return next((u for u in self.store.values() if u.username == name), None)

    def count(self, kind: str) -> int:
        return sum(1 for c in self.calls if c[0] == kind)


class _Users:
    def __init__(self, panel: Panel) -> None:
        self.p = panel

    def _get(self, uuid) -> SimpleNamespace:
        if str(uuid) in self.p.unmapped:
            raise NotFoundError(f"uuid {uuid} не сопоставлен")
        user = self.p.store.get(str(uuid))
        if user is None:
            raise NotFoundError(str(uuid))
        return user

    async def create_user(self, body):
        from remnapy.enums.users import UserStatus

        self.p.calls.append(("create", body.username))
        if self.p.fail_create is not None:
            raise self.p.fail_create
        if self.p.by_name(body.username) is not None:
            raise ConflictError(body.username)
        uid = uuid_lib.uuid4()
        user = SimpleNamespace(
            uuid=uid,
            username=body.username,
            status=UserStatus.ACTIVE,
            expire_at=body.expire_at,
            subscription_url=f"https://sub.example/{uid.hex[:8]}",
            traffic_limit_bytes=body.traffic_limit_bytes,
            hwid_device_limit=body.hwid_device_limit,
            traffic_limit_strategy=body.traffic_limit_strategy,
            tag=body.tag,
            active_internal_squads=[SimpleNamespace(uuid=s) for s in body.active_internal_squads or []],
            external_squad_uuid=body.external_squad_uuid,
            telegram_id=None,
            description=body.description,
            used_traffic_bytes=123,
        )
        self.p.store[str(uid)] = user
        if self.p.lose_create_response:
            raise TimeoutError("read timeout")
        return user

    async def get_user_by_username(self, name):
        if self.p.fail_lookup:
            raise RuntimeError("panel is down")
        user = self.p.by_name(name)
        if user is None:
            raise NotFoundError(name)
        return user

    async def get_user_by_uuid(self, uuid):
        return self._get(uuid)

    async def _slow(self) -> None:
        self.p.entered.set()
        if self.p.gate is not None:
            await self.p.gate.wait()

    async def update_user(self, body):
        fields = sorted(body.model_dump(exclude_unset=True).keys())
        self.p.calls.append(("update", str(body.uuid), fields, body.expire_at))
        await self._slow()
        if self.p.fail_update:
            raise RuntimeError("panel is down")
        user = self._get(body.uuid)
        for name in body.model_fields_set:
            if name == "uuid":
                continue
            value = getattr(body, name)
            if name == "active_internal_squads":
                value = [SimpleNamespace(uuid=s) for s in value or []]
            setattr(user, name, value)
        return user

    # Действия включения/выключения — как у панели 3.4.4: повтор на уже включённом
    # или выключенном пользователе отвечает ошибкой (A030 / A029).
    async def enable_user(self, uuid):
        from remnapy.enums.users import UserStatus

        self.p.calls.append(("enable", str(uuid)))
        user = self._get(uuid)
        if user.status == UserStatus.ACTIVE:
            raise ConflictError("A030: User already enabled")
        user.status = UserStatus.ACTIVE
        return user

    async def disable_user(self, uuid):
        from remnapy.enums.users import UserStatus

        self.p.calls.append(("disable", str(uuid)))
        await self._slow()
        user = self._get(uuid)
        if user.status == UserStatus.DISABLED:
            raise ConflictError("A029: User already disabled")
        user.status = UserStatus.DISABLED
        return user

    async def reset_user_traffic(self, uuid):
        self.p.calls.append(("reset", str(uuid)))
        user = self._get(uuid)
        user.used_traffic_bytes = 0
        return user

    async def delete_user(self, uuid):
        self.p.calls.append(("delete", str(uuid)))
        if self.p.fail_delete:
            raise RuntimeError("panel is down")
        self._get(uuid)
        del self.p.store[str(uuid)]
        return SimpleNamespace(is_deleted=True)

    async def get_all_users(self, start=0, size=25):
        users = list(self.p.store.values())
        return SimpleNamespace(users=users[start : start + size], total=len(users))


class _Hwid:
    def __init__(self, panel: Panel) -> None:
        self.p = panel

    async def get_hwid_user(self, uuid):
        return SimpleNamespace(total=1, devices=[SimpleNamespace(hwid="h1")])

    async def delete_all_hwid_user(self, body):
        self.p.calls.append(("hwid_reset", str(body.user_uuid)))
        return SimpleNamespace(total=0, devices=[])


class _Connections:
    async def drop_connections(self, body=None):
        return None


class RemnawaveFacade:
    """То, что `purge_user` получает вместо RemnawaveImpl: удаление по uuid."""

    def __init__(self, panel: Panel, fail: bool = False) -> None:
        self.sdk = panel
        self.fail = fail

    async def delete_user(self, uuid) -> bool:
        if self.fail:
            raise RuntimeError("panel is down")
        try:
            await self.sdk.users.delete_user(uuid)
        except NotFoundError:
            return False
        return True


# ── схема ───────────────────────────────────────────────────────────────────


def _migration_statements() -> list[str]:
    """DDL миграции 0016 — ровно тот, что уедет на бой, а не его пересказ."""
    here = Path(__file__).resolve()
    name = "src/infrastructure/database/migrations_overlay/versions/0016_family_profiles.py"
    candidates = [Path("/opt/remnashop") / name, here.parents[1] / name]
    path = next(p for p in candidates if p.exists())
    spec = importlib.util.spec_from_file_location("family_migration_0016", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    collected: list[str] = []
    module.op = SimpleNamespace(execute=collected.append)
    module.upgrade()
    return collected


class Db:
    def __init__(self, engine, panel: Panel) -> None:
        self.engine = engine
        self.panel = panel

    def session(self):
        from sqlalchemy.ext.asyncio import AsyncSession

        return AsyncSession(self.engine, expire_on_commit=False)

    @staticmethod
    def daos(session):
        from src.infrastructure.database.dao.subscription import SubscriptionDaoImpl
        from src.infrastructure.database.dao.user import UserDaoImpl
        from src.infrastructure.di.providers.retort import RetortProvider

        provider = RetortProvider()
        retort = RetortProvider.__dict__["get_retort"].origin(provider)
        conversion = RetortProvider.__dict__["get_conversion_retort"].origin(provider, retort, object())
        users = UserDaoImpl(session, retort, conversion, None)
        subs = SubscriptionDaoImpl(session, retort, conversion, None, users)
        return users, subs

    async def scalar(self, sql: str, **params):
        from sqlalchemy import text

        async with self.session() as s:
            return (await s.execute(text(sql), params)).scalar()

    async def rows(self, sql: str, **params):
        from sqlalchemy import text

        async with self.session() as s:
            return (await s.execute(text(sql), params)).all()

    async def run(self, sql: str, **params) -> None:
        from sqlalchemy import text

        async with self.session() as s:
            await s.execute(text(sql), params)
            await s.commit()

    async def plan(self, *, family_terms=(2, 2), name=None, trial=False) -> int:
        from sqlalchemy import text

        async with self.session() as s:
            plan_id = (
                await s.execute(
                    text(
                        "INSERT INTO plans (public_code, name, type, availability, "
                        "traffic_limit_strategy, traffic_limit, device_limit, internal_squads, "
                        "order_index, is_active, is_trial) VALUES (:code, :name, 'BOTH', 'ALL', "
                        "'MONTH', 200, 3, ARRAY[CAST(:sq AS uuid)], 1, true, :trial) RETURNING id"
                    ),
                    {
                        "code": uuid_lib.uuid4().hex[:8],
                        "name": name or f"План {uuid_lib.uuid4().hex[:6]}",
                        "sq": SQUAD,
                        "trial": trial,
                    },
                )
            ).scalar()
            if family_terms:
                await s.execute(
                    text(
                        "INSERT INTO family_plan_terms (plan_id, max_profiles, devices_per_profile) "
                        "VALUES (:p, :m, :d)"
                    ),
                    {"p": plan_id, "m": family_terms[0], "d": family_terms[1]},
                )
            await s.commit()
        return int(plan_id)

    async def owner(self, plan_id: int, *, days: int = 30, telegram_id: int | None = None) -> int:
        from remnapy.enums import TrafficLimitStrategy

        from src.application.dto import PlanSnapshotDto, SubscriptionDto, UserDto
        from src.core.enums import Locale, PlanType, Role, SubscriptionStatus

        async with self.session() as s:
            users, subs = self.daos(s)
            owner = await users.create(
                UserDto(
                    telegram_id=telegram_id or int(uuid_lib.uuid4().int % 10**9),
                    referral_code=uuid_lib.uuid4().hex[:10],
                    name="Владелец",
                    role=Role.USER,
                    language=Locale.RU,
                    is_rules_accepted=True,
                    is_trial_available=False,
                )
            )
            await subs.create(
                SubscriptionDto(
                    user_remna_id=uuid_lib.uuid4(),
                    status=SubscriptionStatus.ACTIVE,
                    traffic_limit=200,
                    device_limit=3,
                    traffic_limit_strategy=TrafficLimitStrategy.MONTH,
                    internal_squads=[uuid_lib.UUID(SQUAD)],
                    expire_at=now() + timedelta(days=days),
                    url="https://sub.example/owner",
                    plan_snapshot=PlanSnapshotDto(
                        id=plan_id,
                        name="Семейный",
                        type=PlanType.BOTH,
                        traffic_limit=200,
                        device_limit=3,
                        duration=30,
                        traffic_limit_strategy=TrafficLimitStrategy.MONTH,
                        internal_squads=[uuid_lib.UUID(SQUAD)],
                    ),
                ),
                owner.id,
            )
            await s.commit()
        return int(owner.id)

    async def create(self, owner_id: int, label: str, request_id=None) -> dict:
        async with self.session() as s:
            users, subs = self.daos(s)
            return await family.create_profile(
                s,
                self.panel,
                owner_id=owner_id,
                label=label,
                request_id=request_id or uuid_lib.uuid4(),
                user_dao=users,
                subscription_dao=subs,
                actor="test",
            )

    async def reconcile(self, owner_id: int, **kw) -> dict:
        async with self.session() as s:
            return await family.reconcile_owner(s, self.panel, owner_id, **kw)

    async def sweep(self) -> dict:
        async with self.session() as s:
            _users, subs = self.daos(s)
            return await family.sweep_pending(s, self.panel, subscription_dao=subs)

    async def profiles(self, owner_id: int):
        async with self.session() as s:
            return await family.load_profiles(s, owner_id)

    async def age_pending(self) -> None:
        await self.run("UPDATE family_profiles SET updated_at = now() - interval '10 minutes'")


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    import src.infrastructure.database.models as models

    baseline = importlib.import_module("src.infrastructure.database.overlay_baseline_ddl")

    monkeypatch.setattr(family, "ASSETS_DIR", tmp_path)
    monkeypatch.setattr(family, "CONFIG_PATH", tmp_path / "family.json")
    monkeypatch.setattr(family, "ORPHANS_STATE_PATH", tmp_path / "family_orphans_state.json")
    family.save_config({"enabled": True, "suspend_grace_days": 30})

    setup = create_async_engine(DSN)
    async with setup.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA_NAME} CASCADE"))
        await conn.execute(text(f"CREATE SCHEMA {SCHEMA_NAME}"))
        await conn.execute(text(f"SET search_path TO {SCHEMA_NAME}"))
        await conn.run_sync(models.BaseSql.metadata.create_all)
        for ddl in baseline.BASELINE_DDL:
            await conn.execute(text(ddl))
        for statement in _migration_statements():
            await conn.execute(text(statement))
    await setup.dispose()

    engine = create_async_engine(
        DSN, pool_size=5, connect_args={"server_settings": {"search_path": SCHEMA_NAME}}
    )
    yield Db(engine, Panel())
    await engine.dispose()

    cleanup = create_async_engine(DSN)
    async with cleanup.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA_NAME} CASCADE"))
    await cleanup.dispose()


async def _family(db: Db, n: int = 2, terms=(2, 2)) -> tuple[int, int, list[int]]:
    plan_id = await db.plan(family_terms=terms)
    owner_id = await db.owner(plan_id)
    ids = []
    for i in range(n):
        result = await db.create(owner_id, f"Профиль {i + 1}")
        assert result["result"] == "created", result
        ids.append(result["profile_id"])
    return plan_id, owner_id, ids


# ── создание ────────────────────────────────────────────────────────────────


async def test_created_profile_is_a_shadow_account_with_its_own_subscription(db):
    _plan, owner_id, (pid,) = await _family(db, 1)
    p = (await db.profiles(owner_id))[0]
    assert p.id == pid and p.status == "active"
    panel_user = db.panel.by_name(p.panel_username)
    assert panel_user is not None and p.panel_username == f"rs_fam_{p.profile_user_id}"
    # Устройства — условие тарифа (2), трафик — тарифный целиком, телеграма нет.
    assert panel_user.hwid_device_limit == 2
    assert panel_user.traffic_limit_bytes == 200 * 1024**3
    assert panel_user.telegram_id is None
    owner_tg = await db.scalar("SELECT telegram_id FROM users WHERE id = :u", u=owner_id)
    # Описание — номера, без имени профиля (его пишет человек, а описание — HTML).
    assert panel_user.description == f"Семья rs_{owner_tg} · профиль {pid}"
    # Теневой аккаунт: войти нельзя, пробника нет, код с префиксом, подписка своя.
    shadow = (
        await db.rows(
            "SELECT telegram_id, email, password_hash, is_trial_available, is_rules_accepted, "
            "referral_code, current_subscription_id FROM users WHERE id = :u",
            u=p.profile_user_id,
        )
    )[0]
    assert shadow[0] is None and shadow[1] is None and shadow[2] is None
    assert shadow[3] is False and shadow[4] is True
    assert shadow[5].startswith("fam_") and shadow[6] == p.sub_id
    assert p.sub_remna_id == str(panel_user.uuid)
    # Снимок «тарифа» профиля — синтетический: в выборки по тарифу он не попадает.
    plan_id = await db.scalar(
        "SELECT (plan_snapshot->>'id')::int FROM subscriptions WHERE id = :s", s=p.sub_id
    )
    assert plan_id == family.FAMILY_PLAN_ID
    # Лимит владельца не тронут: семья не отнимает его устройства.
    assert await db.scalar(
        "SELECT s.device_limit FROM users u JOIN subscriptions s ON s.id = u.current_subscription_id "
        "WHERE u.id = :u",
        u=owner_id,
    ) == 3


async def test_double_request_id_gives_one_profile_and_one_panel_call(db):
    plan_id = await db.plan()
    owner_id = await db.owner(plan_id)
    rid = uuid_lib.uuid4()
    first, second = await asyncio.gather(
        db.create(owner_id, "Мама", rid), db.create(owner_id, "Мама", rid)
    )
    assert {first["result"], second["result"]} <= {"created", "pending"}
    assert "created" in {first["result"], second["result"]}
    assert db.panel.count("create") == 1
    assert await db.scalar("SELECT count(*) FROM family_profiles") == 1
    # Повтор после — тот же профиль, панель не зовётся.
    again = await db.create(owner_id, "Мама", rid)
    assert again["result"] == "created" and again.get("repeat")
    assert db.panel.count("create") == 1
    # Чужой ключ — «conflict» без подробностей.
    other = await db.owner(plan_id)
    assert (await db.create(other, "Мама", rid))["result"] == "conflict"


async def test_two_tabs_cannot_overrun_the_limit(db):
    """Последнее место, две вкладки, разные ключи: заводит одна, вторая видит лимит.

    Держит замок строки владельца: без него обе прошли бы проверку «профилей меньше
    максимума» до того, как любая из них записала свою строку.
    """
    plan_id = await db.plan(family_terms=(1, 2))
    owner_id = await db.owner(plan_id)
    results = await asyncio.gather(db.create(owner_id, "Мама"), db.create(owner_id, "Папа"))
    assert sorted(r["result"] for r in results) == ["created", "not_available"]
    assert next(r for r in results if r["result"] == "not_available")["reason"] == "max_reached"
    assert db.panel.count("create") == 1


async def test_limit_and_duplicate_name_are_enforced(db):
    _plan, owner_id, _ids = await _family(db, 2, terms=(2, 2))
    assert (await db.create(owner_id, "Третий"))["result"] == "not_available"
    await db.run("UPDATE family_plan_terms SET max_profiles = 3")
    assert (await db.create(owner_id, "профиль 1"))["result"] == "label_taken"
    assert (await db.create(owner_id, "Третий"))["result"] == "created"


async def test_panel_refusal_marks_failed_and_leaves_nothing(db):
    plan_id = await db.plan()
    owner_id = await db.owner(plan_id)
    users_before = await db.scalar("SELECT count(*) FROM users")
    db.panel.fail_create = BadRequestError("validation failed")
    rid = uuid_lib.uuid4()
    result = await db.create(owner_id, "Мама", rid)
    assert result["result"] == "failed"
    assert db.panel.store == {}
    row = (await db.rows("SELECT status, profile_user_id FROM family_profiles"))[0]
    assert row[0] == "failed" and row[1] is None
    assert await db.scalar("SELECT count(*) FROM users") == users_before
    # Повтор с тем же ключом — честное «не вышло», а не новый профиль.
    assert (await db.create(owner_id, "Мама", rid))["result"] == "failed"
    # Имя неудачная попытка не занимает.
    db.panel.fail_create = None
    assert (await db.create(owner_id, "Мама"))["result"] == "created"


async def test_lost_panel_answer_is_finished_by_cron_by_name(db):
    plan_id = await db.plan()
    owner_id = await db.owner(plan_id)
    db.panel.lose_create_response = True
    db.panel.fail_lookup = True
    result = await db.create(owner_id, "Мама")
    assert result["result"] == "pending"
    assert (await db.profiles(owner_id))[0].status == "creating"
    assert len(db.panel.store) == 1  # панель создала, ответ потерялся

    # Свежую строку крон не трогает: её, возможно, доводит веб-процесс.
    db.panel.fail_lookup = False
    assert (await db.sweep())["created"] == []
    await db.age_pending()
    out = await db.sweep()
    assert out["created"] == [result["profile_id"]]
    p = (await db.profiles(owner_id))[0]
    assert p.status == "active" and p.sub_remna_id == next(iter(db.panel.store))
    assert db.panel.count("create") == 1


async def test_late_web_request_does_not_delete_what_cron_already_adopted(db):
    """Веб-запрос завис дольше отсрочки, крон довёл профиль по имени, и только потом
    веб дошёл до записи. Он обязан признать готовый профиль, а не удалить его
    в панели как «сироту» — иначе у семьи пропала бы работающая ссылка."""
    plan_id = await db.plan()
    owner_id = await db.owner(plan_id)
    db.panel.lose_create_response = True
    db.panel.fail_lookup = True
    pending = await db.create(owner_id, "Мама")
    db.panel.fail_lookup = False
    await db.age_pending()
    assert (await db.sweep())["created"] == [pending["profile_id"]]
    panel_user = next(iter(db.panel.store.values()))
    async with db.session() as s:
        _users, subs = db.daos(s)
        late = await family._adopt(
            s,
            db.panel,
            owner_id=owner_id,
            profile_id=pending["profile_id"],
            panel_user=panel_user,
            subscription_dao=subs,
            actor="test",
        )
    assert late["result"] == "created"
    assert db.panel.count("delete") == 0 and len(db.panel.store) == 1
    assert (await db.profiles(owner_id))[0].status == "active"


async def test_stuck_create_that_panel_never_made_becomes_failed(db):
    plan_id = await db.plan()
    owner_id = await db.owner(plan_id)
    db.panel.fail_create = TimeoutError("connect timeout")
    db.panel.fail_lookup = True
    assert (await db.create(owner_id, "Мама"))["result"] == "pending"
    db.panel.fail_lookup = False
    await db.age_pending()
    out = await db.sweep()
    assert len(out["failed"]) == 1
    assert await db.scalar("SELECT status FROM family_profiles") == "failed"
    assert await db.scalar("SELECT count(*) FROM users WHERE name LIKE 'Семья #%'") == 0


# ── синхрон панели ──────────────────────────────────────────────────────────


def _remna(panel_user) -> SimpleNamespace:
    """Пользователь панели в том виде, в каком его отдаёт синхрону SDK."""
    return SimpleNamespace(
        uuid=panel_user.uuid,
        username=panel_user.username,
        status=panel_user.status,
        expire_at=panel_user.expire_at,
        subscription_url=panel_user.subscription_url,
        traffic_limit_bytes=panel_user.traffic_limit_bytes,
        hwid_device_limit=panel_user.hwid_device_limit,
        traffic_limit_strategy=panel_user.traffic_limit_strategy,
        tag=panel_user.tag,
        active_internal_squads=panel_user.active_internal_squads,
        external_squad_uuid=panel_user.external_squad_uuid,
        telegram_id=None,
    )


async def test_panel_sync_does_not_create_a_twin_for_family_profiles(db):
    from src.application.use_cases.remnawave.commands.synchronization import (
        SyncRemnaUser,
        SyncRemnaUserDto,
    )
    from src.infrastructure.database.uow import UnitOfWorkImpl
    from src.infrastructure.services.remnawave import RemnawaveImpl

    assert getattr(SyncRemnaUser, "_overlay_family_guard", False), "правка синхрона не встала"
    _plan, owner_id, _ids = await _family(db, 1)
    live = db.panel.by_name((await db.profiles(owner_id))[0].panel_username)
    # Сирота: пользователь панели rs_fam_* без строки у нас.
    orphan = SimpleNamespace(**{**vars(_remna(live)), "uuid": uuid_lib.uuid4(), "username": "rs_fam_999999"})
    users_before = await db.scalar("SELECT count(*) FROM users")

    async with db.session() as s:
        users, subs = db.daos(s)
        sync = SyncRemnaUser(UnitOfWorkImpl(s), users, subs, None, RemnawaveImpl(None), None)
        assert await sync._execute(None, SyncRemnaUserDto(orphan, True)) is False
    assert await db.scalar("SELECT count(*) FROM users") == users_before

    # Живой профиль идёт обычным путём базы: подписка обновляется, двойника нет.
    later = live.expire_at + timedelta(days=5)
    updated = SimpleNamespace(**{**vars(_remna(live)), "expire_at": later})
    async with db.session() as s:
        users, subs = db.daos(s)
        sync = SyncRemnaUser(UnitOfWorkImpl(s), users, subs, None, RemnawaveImpl(None), None)
        await sync._execute(None, SyncRemnaUserDto(updated, True))
    assert await db.scalar("SELECT count(*) FROM users") == users_before
    p = (await db.profiles(owner_id))[0]
    assert abs(p.sub_expire_at - later) < timedelta(seconds=1)


# ── удаление ────────────────────────────────────────────────────────────────


async def test_delete_with_panel_down_stays_deleting_and_cron_retries(db):
    _plan, owner_id, (pid,) = await _family(db, 1)
    p = (await db.profiles(owner_id))[0]
    db.panel.fail_delete = True
    async with db.session() as s:
        result = await family.delete_profile(s, db.panel, owner_id=owner_id, profile_id=pid, actor="test")
    assert result["result"] == "pending"
    assert await db.scalar("SELECT status FROM family_profiles WHERE id = :i", i=pid) == "deleting"
    assert await db.scalar("SELECT count(*) FROM users WHERE id = :u", u=p.profile_user_id) == 1

    db.panel.fail_delete = False
    await db.age_pending()
    out = await db.sweep()
    assert out["deleted"] == [pid]
    assert await db.scalar("SELECT count(*) FROM family_profiles") == 0
    assert await db.scalar("SELECT count(*) FROM users WHERE id = :u", u=p.profile_user_id) == 0
    assert await db.scalar("SELECT count(*) FROM subscriptions WHERE user_id = :u", u=p.profile_user_id) == 0
    assert db.panel.store == {}
    # Журнал переживает профиль: владелец видит, что и когда удалили.
    assert await db.scalar(
        "SELECT count(*) FROM family_events WHERE kind = 'deleted' AND owner_user_id = :o", o=owner_id
    ) == 1


async def test_someone_elses_profile_is_not_found(db):
    plan_id, _owner, (pid,) = await _family(db, 1)
    stranger = await db.owner(plan_id)
    async with db.session() as s:
        result = await family.delete_profile(s, db.panel, owner_id=stranger, profile_id=pid, actor="test")
    assert result["result"] == "not_found"
    async with db.session() as s:
        result = await family.reset_profile_devices(
            s, db.panel, owner_id=stranger, profile_id=pid, actor="test"
        )
    assert result["result"] == "not_found"
    assert db.panel.count("delete") == 0 and db.panel.count("hwid_reset") == 0


async def test_reset_devices_respects_cooldown(db):
    _plan, owner_id, (pid,) = await _family(db, 1)
    async with db.session() as s:
        first = await family.reset_profile_devices(
            s, db.panel, owner_id=owner_id, profile_id=pid, actor="test", cooldown_hours=24
        )
    async with db.session() as s:
        second = await family.reset_profile_devices(
            s, db.panel, owner_id=owner_id, profile_id=pid, actor="test", cooldown_hours=24
        )
    assert first["result"] == "reset" and second["result"] == "cooldown"
    assert db.panel.count("hwid_reset") == 1


# ── жизнь вместе с владельцем ───────────────────────────────────────────────


async def test_renewal_hook_extends_profiles_with_traffic_reset(db):
    _plan, owner_id, ids = await _family(db, 2)
    new_expire = now() + timedelta(days=60)
    await db.run(
        "UPDATE subscriptions SET expire_at = :e, updated_at = now() WHERE id = "
        "(SELECT current_subscription_id FROM users WHERE id = :u)",
        e=new_expire,
        u=owner_id,
    )
    async with db.session() as s:
        out = await family.after_purchase(s, db.panel, owner_id)
    assert sorted(out["synced"]) == sorted(ids)
    for p in await db.profiles(owner_id):
        assert abs(p.sub_expire_at - new_expire) < timedelta(seconds=1)
        panel_user = db.panel.store[p.sub_remna_id]
        assert abs(panel_user.expire_at - new_expire) < timedelta(seconds=1)
        assert ("reset", p.sub_remna_id) in db.panel.calls
    # По одному узкому PATCH на профиль — и ни одного лишнего.
    assert db.panel.count("update") == 2


async def test_change_to_regular_plan_suspends_all_and_back_resumes(db):
    family_plan, owner_id, ids = await _family(db, 2)
    regular = await db.plan(family_terms=None)
    snapshot_sql = (
        "UPDATE subscriptions SET plan_snapshot = jsonb_set(plan_snapshot, '{id}', to_jsonb(CAST(:p AS int))), "
        "updated_at = now() WHERE id = (SELECT current_subscription_id FROM users WHERE id = :u)"
    )
    await db.run(snapshot_sql, p=regular, u=owner_id)
    out = await db.reconcile(owner_id)
    assert sorted(out["suspended"]) == sorted(ids)
    for p in await db.profiles(owner_id):
        assert p.status == "suspended" and p.suspend_reason == "plan"
        assert db.panel.store[p.sub_remna_id].status.value == "DISABLED"

    await db.run(snapshot_sql, p=family_plan, u=owner_id)
    out = await db.reconcile(owner_id)
    assert sorted(out["resumed"]) == sorted(ids)
    for p in await db.profiles(owner_id):
        assert p.status == "active" and p.suspend_reason is None
        assert db.panel.store[p.sub_remna_id].status.value == "ACTIVE"


async def _hold_family_lock(db: Db, owner_id: int):
    """Чужая транзакция держит очередь семьи — как крон посреди медленной панели."""
    from sqlalchemy import text as sa_text

    session = db.session()
    await session.execute(
        sa_text("SELECT pg_advisory_xact_lock(:ns, :o)"),
        {"ns": family.FAMILY_LOCK_NS, "o": owner_id},
    )
    return session


async def _renew_owner(db: Db, owner_id: int, new_expire: datetime) -> None:
    """Оплата продления так, как её проводит база: сначала счёт, потом подписка."""
    await db.run(
        "INSERT INTO transactions (payment_id, user_id, status, is_test, purchase_type, "
        "gateway_type, pricing, currency, plan_snapshot) "
        "VALUES (gen_random_uuid(), :u, 'COMPLETED', false, 'RENEW', 'YOOMONEY', "
        "CAST('{}' AS jsonb), 'RUB', CAST('{\"id\": 7}' AS jsonb))",
        u=owner_id,
    )
    await db.run(
        "UPDATE subscriptions SET expire_at = :e, updated_at = clock_timestamp() WHERE id = "
        "(SELECT current_subscription_id FROM users WHERE id = :u)",
        e=new_expire,
        u=owner_id,
    )


async def test_payment_does_not_wait_for_the_family(db):
    """Крон сверяет семью (очередь занята) — хук оплаты уступает сразу, а продление
    с обнулением трафика доводит следующий проход крона: оплата позже сверки."""
    _plan, owner_id, _ids = await _family(db, 2)
    new_expire = now() + timedelta(days=60)
    await _renew_owner(db, owner_id, new_expire)
    holder = await _hold_family_lock(db, owner_id)
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        async with db.session() as s:
            out = await family.after_purchase(s, db.panel, owner_id)
    finally:
        await holder.rollback()
        await holder.close()
    assert out == {"busy": True}
    assert loop.time() - started < 2, "оплата ждала очередь семьи"
    assert db.panel.count("update") == 0

    async with db.session() as s:
        await family.reconcile_owner(s, db.panel, owner_id)
    for p in await db.profiles(owner_id):
        assert abs(p.sub_expire_at - new_expire) < timedelta(seconds=1)
        assert ("reset", p.sub_remna_id) in db.panel.calls, "крон не догнал сброс трафика"


async def test_payment_hook_has_a_deadline(db, monkeypatch):
    """Панель зависла — хук оплаты отпускает оплату по дедлайну, а не по таймауту базы."""
    _plan, owner_id, _ids = await _family(db, 1)
    await _renew_owner(db, owner_id, now() + timedelta(days=60))
    monkeypatch.setattr(family, "HOOK_DEADLINE_SECONDS", 0.5)
    db.panel.gate = asyncio.Event()  # никогда не откроется
    async with db.session() as s:
        out = await asyncio.wait_for(family.after_purchase(s, db.panel, owner_id), 5)
    assert out == {"timeout": True}


async def test_owner_row_is_free_while_the_family_talks_to_the_panel(db):
    """Пока сверка ждёт медленную панель, строка владельца свободна: покупка базы
    (CHANGE/NEW пишет в users после панели) не упирается в семью."""
    from sqlalchemy import text as sa_text

    _plan, owner_id, _ids = await _family(db, 2)
    await db.run("UPDATE family_plan_terms SET devices_per_profile = 3, updated_at = now()")
    db.panel.gate = asyncio.Event()
    db.panel.entered.clear()
    task = asyncio.create_task(db.reconcile(owner_id))
    await asyncio.wait_for(db.panel.entered.wait(), 5)
    try:
        async with db.session() as s:
            await s.execute(sa_text("SET LOCAL lock_timeout = '1s'"))
            await s.execute(
                sa_text("UPDATE users SET updated_at = now() WHERE id = :u"), {"u": owner_id}
            )
            await s.commit()
    finally:
        db.panel.gate.set()
        out = await asyncio.wait_for(task, 10)
    assert len(out["synced"]) == 2


async def test_each_profile_is_committed_right_after_the_panel(db):
    """Сбой базы на втором профиле не откатывает запись о первом: выключенный в
    панели профиль у нас тоже записан выключенным и включится вместе с семьёй."""
    _plan, owner_id, _ids = await _family(db, 2)
    real = family._store_status
    calls = {"n": 0}

    async def flaky_store(session, p, status):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("база моргнула")
        await real(session, p, status)

    await db.run("UPDATE users SET is_blocked = true, updated_at = now() WHERE id = :u", u=owner_id)
    family._store_status = flaky_store
    try:
        with pytest.raises(RuntimeError):
            await db.reconcile(owner_id)
    finally:
        family._store_status = real
    rows = await db.profiles(owner_id)
    first = next(p for p in rows if p.status == "suspended")
    assert first.suspend_reason == "owner_blocked" and first.sub_status == "DISABLED"

    # Владельца разблокировали — оба профиля снова работают, включая записанный первым.
    await db.run("UPDATE users SET is_blocked = false, updated_at = now() WHERE id = :u", u=owner_id)
    await db.reconcile(owner_id)
    assert {p.status for p in await db.profiles(owner_id)} == {"active"}
    assert all(u.status.value == "ACTIVE" for u in db.panel.store.values())


async def test_busy_family_answers_busy_instead_of_waiting(db, monkeypatch):
    """Вкладка человека не висит, пока крон в панели: «занято, повторите»."""
    _plan, owner_id, (pid,) = await _family(db, 1)
    monkeypatch.setattr(family, "LOCK_WAIT_MS", 300)
    holder = await _hold_family_lock(db, owner_id)
    try:
        assert (await db.create(owner_id, "Папа"))["result"] == "busy"
        async with db.session() as s:
            deleted = await family.delete_profile(s, db.panel, owner_id=owner_id, profile_id=pid, actor="t")
        async with db.session() as s:
            reset = await family.reset_profile_devices(s, db.panel, owner_id=owner_id, profile_id=pid, actor="t")
    finally:
        await holder.rollback()
        await holder.close()
    assert deleted == {"result": "busy"} and reset == {"result": "busy"}
    assert db.panel.count("create") == 1 and db.panel.count("delete") == 0


async def _one_profile(db: Db):
    _plan, owner_id, (pid,) = await _family(db, 1)
    p = (await db.profiles(owner_id))[0]
    return owner_id, pid, p


async def _touch_terms(db: Db, devices: int = 3) -> None:
    """Условия тарифа поменялись — сверке есть что отправить в панель."""
    await db.run(
        "UPDATE family_plan_terms SET devices_per_profile = :d, updated_at = now()", d=devices
    )


async def test_not_found_by_uuid_but_known_by_name_is_not_deleted(db):
    """Слой панели 3.x отвечает «не найден» на несопоставленный uuid. Профиль при этом
    жив — удалять его ссылку нельзя: сбой считается неудачей и повторится."""
    owner_id, pid, p = await _one_profile(db)
    db.panel.unmapped.add(p.sub_remna_id)
    await _touch_terms(db)
    out = await db.reconcile(owner_id)
    assert out["errors"] and not out["gone"] and not out["deleted"]
    assert db.panel.count("delete") == 0 and len(db.panel.store) == 1
    after = (await db.profiles(owner_id))[0]
    assert after.status == "active" and after.fail_count == 1


async def test_user_found_under_another_uuid_is_relinked(db):
    """Панель знает профиль под другим uuid — зеркало перепривязывается, и следующая
    сверка делает своё дело по новому uuid, ничего не удаляя."""
    owner_id, pid, p = await _one_profile(db)
    user = db.panel.store.pop(p.sub_remna_id)
    new_uuid = uuid_lib.uuid4()
    user.uuid = new_uuid
    db.panel.store[str(new_uuid)] = user
    await _touch_terms(db)
    await db.reconcile(owner_id)
    relinked = (await db.profiles(owner_id))[0]
    assert relinked.sub_remna_id == str(new_uuid) and relinked.status == "active"
    out = await db.reconcile(owner_id)
    assert out["synced"] == [pid]
    assert db.panel.store[str(new_uuid)].hwid_device_limit == 3
    assert db.panel.count("delete") == 0


async def test_confirmed_missing_is_suspended_and_deleted_only_after_grace(db):
    """Пользователя удалили в панели руками (вебхук не дошёл): профиль не удаляется
    сразу, а приостанавливается и удаляется через отсрочку."""
    owner_id, pid, p = await _one_profile(db)
    db.panel.store.clear()
    await _touch_terms(db)
    await db.reconcile(owner_id)
    after = (await db.profiles(owner_id))[0]
    assert after.status == "suspended" and after.suspend_reason == "panel_missing"
    suspended_at = after.suspended_at
    # Повторная проверка не сдвигает отсрочку.
    await db.run("UPDATE family_profiles SET last_reconciled_at = NULL")
    await db.reconcile(owner_id)
    assert (await db.profiles(owner_id))[0].suspended_at == suspended_at

    await db.run("UPDATE family_profiles SET suspended_at = now() - interval '31 days'")
    await db.reconcile(owner_id)
    assert await db.scalar("SELECT count(*) FROM family_profiles") == 0
    assert await db.scalar("SELECT count(*) FROM users WHERE id = :u", u=p.profile_user_id) == 0


async def test_deleted_by_webhook_but_alive_in_panel_is_kept(db):
    """Зеркало говорит «удалён» (вебхук), а панель пользователя знает — не удаляем."""
    owner_id, pid, p = await _one_profile(db)
    await db.run("UPDATE subscriptions SET status = 'DELETED' WHERE id = :s", s=p.sub_id)
    out = await db.reconcile(owner_id)
    assert out["errors"] and not out["gone"]
    assert await db.scalar("SELECT count(*) FROM family_profiles") == 1
    assert db.panel.count("delete") == 0


async def test_deleted_by_webhook_and_gone_from_panel_is_cleaned_up(db):
    owner_id, pid, p = await _one_profile(db)
    await db.run("UPDATE subscriptions SET status = 'DELETED' WHERE id = :s", s=p.sub_id)
    db.panel.store.clear()
    out = await db.reconcile(owner_id)
    assert out["gone"] == [pid]
    assert await db.scalar("SELECT count(*) FROM family_profiles") == 0


async def test_unfreeze_sets_the_term_before_enabling(db):
    _plan, owner_id, (pid,) = await _family(db, 1)
    await db.run(
        "INSERT INTO subscription_freezes (user_id, remna_uuid, frozen_at, remaining_seconds, active) "
        "VALUES (:u, gen_random_uuid()::text, now(), 86400, true)",
        u=owner_id,
    )
    out = await db.reconcile(owner_id)
    assert out["suspended"] == [pid]
    assert (await db.profiles(owner_id))[0].suspend_reason == "owner_frozen"

    # Разморозка: у владельца новый срок, пауза закрыта.
    new_expire = now() + timedelta(days=45)
    await db.run("UPDATE subscription_freezes SET active = false WHERE user_id = :u", u=owner_id)
    await db.run(
        "UPDATE subscriptions SET expire_at = :e WHERE id = "
        "(SELECT current_subscription_id FROM users WHERE id = :u)",
        e=new_expire,
        u=owner_id,
    )
    db.panel.calls.clear()
    out = await db.reconcile(owner_id)
    assert out["resumed"] == [pid]
    p = (await db.profiles(owner_id))[0]
    # Срок и включение — одним PATCH: окна «включён со старым сроком» нет вовсе.
    updates = [c for c in db.panel.calls if c[0] == "update" and c[1] == p.sub_remna_id]
    assert len(updates) == 1 and "status" in updates[0][2] and "expire_at" in updates[0][2]
    assert abs(updates[0][3] - new_expire) < timedelta(seconds=1)
    assert db.panel.count("enable") == 0


async def test_cron_pass_picks_up_a_changed_owner(db):
    tick = importlib.import_module("src.infrastructure.taskiq.tasks.family")
    _plan, owner_id, ids = await _family(db, 2)
    # Только что заведённые профили ближайший проход сверяет (пока они создавались,
    # владелец мог сменить тариф), следующий — уже ничего не трогает.
    for expected in (1, 0):
        async with db.session() as s:
            _users, subs = db.daos(s)
            summary = await tick.run_pass(s, db.panel, subscription_dao=subs, full=False)
        assert summary["owners"] == expected
    # Владельца заблокировали — ближайший проход приостанавливает семью.
    await db.run("UPDATE users SET is_blocked = true, updated_at = now() WHERE id = :u", u=owner_id)
    async with db.session() as s:
        _users, subs = db.daos(s)
        summary = await tick.run_pass(s, db.panel, subscription_dao=subs, full=False)
    assert summary["owners"] == 1
    assert {p.suspend_reason for p in await db.profiles(owner_id)} == {"owner_blocked"}


async def test_orphans_are_reported_once(db, monkeypatch):
    tick = importlib.import_module("src.infrastructure.taskiq.tasks.family")
    sent: list[str] = []

    async def fake_send(text):
        sent.append(text)

    monkeypatch.setattr(tick, "send_to_owner", fake_send)
    await _family(db, 1)
    await db.panel.users.create_user(
        SimpleNamespace(
            username="rs_fam_424242",
            expire_at=now(),
            traffic_limit_bytes=0,
            hwid_device_limit=1,
            traffic_limit_strategy=None,
            tag=None,
            active_internal_squads=[],
            external_squad_uuid=None,
            description="",
        )
    )
    for _ in range(2):
        async with db.session() as s:
            _users, subs = db.daos(s)
            await tick.run_pass(s, db.panel, subscription_dao=subs, full=True)
    assert len(sent) == 1 and "rs_fam_424242" in sent[0]


# ── удаление владельца ──────────────────────────────────────────────────────


async def test_purge_owner_removes_the_whole_family_in_panel(db):
    purge = importlib.import_module("src.infrastructure.services.overlay_user_purge")
    _plan, owner_id, _ids = await _family(db, 2)
    shadows = [p.profile_user_id for p in await db.profiles(owner_id)]
    uuids = {p.sub_remna_id for p in await db.profiles(owner_id)}

    async with db.session() as s:
        result = await purge.purge_user(s, RemnawaveFacade(db.panel), owner_id)
        await s.commit()
    assert result["family_profiles_removed"] == 2
    assert uuids <= {c[1] for c in db.panel.calls if c[0] == "delete"}
    assert db.panel.store == {}
    assert await db.scalar("SELECT count(*) FROM users WHERE id = ANY(:ids)", ids=shadows) == 0
    assert await db.scalar("SELECT count(*) FROM family_profiles") == 0


async def test_purge_of_a_paying_owner_leaves_no_names_of_the_family(db):
    """Владелец платил — его строку обезличивают, а не удаляют, и каскад не уносит
    журнал семьи и неудачные попытки. Имена близких — личные данные: их быть не должно."""
    purge = importlib.import_module("src.infrastructure.services.overlay_user_purge")
    _plan, owner_id, _ids = await _family(db, 1)
    db.panel.fail_create = BadRequestError("rejected")
    assert (await db.create(owner_id, "Бабушка"))["result"] == "failed"
    await _pay(db, owner_id, at_sql="now()")
    assert await db.scalar("SELECT count(*) FROM family_events WHERE owner_user_id = :u", u=owner_id) > 0

    async with db.session() as s:
        result = await purge.purge_user(s, RemnawaveFacade(db.panel), owner_id)
        await s.commit()
    assert result["mode"] == "anonymized"
    assert await db.scalar("SELECT count(*) FROM family_events WHERE owner_user_id = :u", u=owner_id) == 0
    assert await db.scalar("SELECT count(*) FROM family_profiles WHERE owner_user_id = :u", u=owner_id) == 0


async def test_purge_with_panel_down_changes_nothing(db):
    purge = importlib.import_module("src.infrastructure.services.overlay_user_purge")
    _plan, owner_id, _ids = await _family(db, 2)
    async with db.session() as s:
        with pytest.raises(purge.PanelUnavailable):
            await purge.purge_user(s, RemnawaveFacade(db.panel, fail=True), owner_id)
        await s.rollback()
    assert len(db.panel.store) == 2
    assert {p.status for p in await db.profiles(owner_id)} == {"active"}
    assert await db.scalar("SELECT count(*) FROM users WHERE name LIKE 'Семья #%'") == 2


# ── резерв ──────────────────────────────────────────────────────────────────


def _reserve_select_sql() -> str:
    """Выборка выдачи резерва — ровно тот текст, что в кроне, достаём из исходника."""
    reserve = importlib.import_module("src.infrastructure.taskiq.tasks.reserve")
    tree = ast.parse(Path(reserve.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith("SELECT u.id, s.user_remna_id") and "reserve_grants" in node.value:
                return node.value
    raise AssertionError("выборка резерва не найдена в tasks/reserve.py")


async def test_reserve_never_picks_a_family_profile(db):
    _plan, owner_id, _ids = await _family(db, 1)
    shadow = (await db.profiles(owner_id))[0].profile_user_id
    # Все истекли вчера; у владельца есть настоящая оплата, у профиля — нет и быть не может.
    await db.run("UPDATE subscriptions SET expire_at = now() - interval '1 day'")
    await db.run(
        "INSERT INTO transactions (payment_id, user_id, status, is_test, purchase_type, "
        "gateway_type, pricing, currency, plan_snapshot) VALUES (gen_random_uuid(), :u, "
        "'COMPLETED', false, 'RENEW', 'YOOMONEY', '{}'::jsonb, 'RUB', '{\"id\": 7}'::jsonb)",
        u=owner_id,
    )
    picked = {r[0] for r in await db.rows(_reserve_select_sql(), w=7)}
    assert owner_id in picked, "контроль: владелец с оплатой резерв получает"
    assert shadow not in picked


async def test_create_timeout_is_not_a_failure(db):
    """Панель не ответила на создание — это не «не создала»: строка остаётся «создаётся»
    и решается кроном после отсрочки, а не признаётся неудачной сразу."""
    plan_id = await db.plan()
    owner_id = await db.owner(plan_id)
    db.panel.fail_create = TimeoutError("read timeout")
    result = await db.create(owner_id, "Мама")
    assert result["result"] == "pending"
    assert await db.scalar("SELECT status FROM family_profiles") == "creating"


async def test_user_created_late_for_a_failed_attempt_is_removed_by_cron(db, monkeypatch):
    """Панель досоздала пользователя уже после того, как попытку признали неудачной:
    ссылку никто не видел — крон удаляет его сам и владельцу бота об этом не пишет."""
    tick = importlib.import_module("src.infrastructure.taskiq.tasks.family")
    sent: list[str] = []

    async def fake_send(text):
        sent.append(text)

    monkeypatch.setattr(tick, "send_to_owner", fake_send)
    plan_id = await db.plan()
    owner_id = await db.owner(plan_id)
    await db.create(owner_id, "Живой")
    db.panel.fail_create = BadRequestError("rejected")
    assert (await db.create(owner_id, "Мама"))["result"] == "failed"
    name = await db.scalar("SELECT panel_username FROM family_profiles WHERE status = 'failed'")
    db.panel.fail_create = None
    await db.panel.users.create_user(
        SimpleNamespace(
            username=name,
            expire_at=now(),
            traffic_limit_bytes=0,
            hwid_device_limit=1,
            traffic_limit_strategy=None,
            tag=None,
            active_internal_squads=[],
            external_squad_uuid=None,
            description="",
        )
    )
    async with db.session() as s:
        _users, subs = db.daos(s)
        await tick.run_pass(s, db.panel, subscription_dao=subs, full=True)
    assert db.panel.by_name(name) is None, "поздний пользователь неудачной попытки остался"
    assert db.panel.by_name("rs_fam_x") is None and len(db.panel.store) == 1
    assert sent == []


async def test_fresh_profile_waits_for_a_reconcile(db):
    """Отметки сверки у только что заведённого профиля нет — крон сверит его сразу."""
    _plan, owner_id, _ids = await _family(db, 1)
    assert (await db.profiles(owner_id))[0].last_reconciled_at is None


async def test_cron_waits_until_the_paid_period_is_issued(db):
    """Счёт уже проведён, а период ещё не выдан (крон попал между шагами базы) — трафик
    семьи не обнуляется со старым сроком; обнуляется, когда период выдан."""
    _plan, owner_id, (pid,) = await _family(db, 1)
    await db.run("UPDATE family_profiles SET last_reconciled_at = now()")
    await db.run(
        "INSERT INTO transactions (payment_id, user_id, status, is_test, purchase_type, "
        "gateway_type, pricing, currency, plan_snapshot, updated_at) "
        "VALUES (gen_random_uuid(), :u, 'COMPLETED', false, 'RENEW', 'YOOMONEY', "
        "CAST('{}' AS jsonb), 'RUB', CAST('{\"id\": 7}' AS jsonb), clock_timestamp())",
        u=owner_id,
    )
    await db.reconcile(owner_id)
    assert db.panel.count("reset") == 0, "трафик обнулён до выдачи оплаченного периода"
    new_expire = now() + timedelta(days=60)
    await db.run(
        "UPDATE subscriptions SET expire_at = :e, updated_at = clock_timestamp() WHERE id = "
        "(SELECT current_subscription_id FROM users WHERE id = :u)",
        e=new_expire,
        u=owner_id,
    )
    await db.reconcile(owner_id)
    assert db.panel.count("reset") == 1
    assert abs((await db.profiles(owner_id))[0].sub_expire_at - new_expire) < timedelta(seconds=1)


async def test_purchase_during_reserve_is_paid_time_for_the_family(db):
    """Короткий тариф, купленный во время резерва, — оплаченный срок, а не резерв:
    строка резерва ещё живёт, но семья получает купленное, а не приостановку."""
    _plan, owner_id, (pid,) = await _family(db, 1)
    await db.run(
        "INSERT INTO reserve_grants (user_id, remna_uuid, granted_at, reserve_expire_at, ended) "
        "VALUES (:u, 'x', now() - interval '1 day', now() + interval '7 days', false)",
        u=owner_id,
    )
    short = now() + timedelta(days=3)
    await db.run(
        "INSERT INTO transactions (payment_id, user_id, status, is_test, purchase_type, "
        "gateway_type, pricing, currency, plan_snapshot, updated_at) "
        "VALUES (gen_random_uuid(), :u, 'COMPLETED', false, 'CHANGE', 'YOOMONEY', "
        "CAST('{}' AS jsonb), 'RUB', CAST('{\"id\": 7}' AS jsonb), clock_timestamp())",
        u=owner_id,
    )
    await db.run(
        "UPDATE subscriptions SET expire_at = :e, updated_at = clock_timestamp() WHERE id = "
        "(SELECT current_subscription_id FROM users WHERE id = :u)",
        e=short,
        u=owner_id,
    )
    out = await db.reconcile(owner_id)
    assert out["synced"] == [pid] and not out["suspended"]
    after = (await db.profiles(owner_id))[0]
    assert after.status == "active" and abs(after.sub_expire_at - short) < timedelta(seconds=1)


async def test_owners_longest_unchecked_go_first(db):
    """Хвост больше лимита не должен крутить одних и тех же: первыми — давно не
    сверявшиеся и ещё ни разу не сверенные."""
    plan_id = await db.plan()
    first = await db.owner(plan_id)
    second = await db.owner(plan_id)
    assert (await db.create(first, "А"))["result"] == "created"
    assert (await db.create(second, "Б"))["result"] == "created"
    await db.run(
        "UPDATE family_profiles SET last_reconciled_at = now() - interval '1 hour' "
        "WHERE owner_user_id = :o",
        o=first,
    )
    async with db.session() as s:
        assert await family.owners_to_reconcile(s, full=True, limit=1) == [second]
    await db.run(
        "UPDATE family_profiles SET last_reconciled_at = now() WHERE owner_user_id = :o", o=second
    )
    async with db.session() as s:
        assert await family.owners_to_reconcile(s, full=True, limit=1) == [first]


async def test_delete_and_recreate_is_limited_per_paid_period(db):
    """«Удалить и завести заново» — свежий трафик и пустой список устройств. За период
    можно завести на один профиль больше, чем мест; дальше — после продления."""
    _plan, owner_id, (first, second) = await _family(db, 2, terms=(2, 2))

    async def remove(pid: int) -> None:
        async with db.session() as s:
            result = await family.delete_profile(s, db.panel, owner_id=owner_id, profile_id=pid, actor="t")
        assert result["result"] == "deleted"

    await remove(first)
    third = await db.create(owner_id, "Замена")
    assert third["result"] == "created", "одна замена за период разрешена"
    await remove(second)
    refused = await db.create(owner_id, "Ещё одна")
    assert refused == {"result": "not_available", "reason": "period_limit"}
    async with db.session() as s:
        view = await family.family_view(s, None, owner_id, with_panel=False)
    assert view["reason"] == "period_limit" and view["period_limit"] == 3
    assert view["created_in_period"] == 3

    # Продление начинает новый период — завести снова можно.
    await _renew_owner(db, owner_id, now() + timedelta(days=60))
    assert (await db.create(owner_id, "Ещё одна"))["result"] == "created"


# ── резерв владельца ────────────────────────────────────────────────────────


async def _history(db: Db, owner_id: int, *, bought_days_ago: int = 40) -> None:
    """Владелец давно купил период: строка подписки и оплата — в прошлом."""
    await db.run(
        "UPDATE subscriptions SET created_at = now() - interval '60 days' WHERE id = "
        "(SELECT current_subscription_id FROM users WHERE id = :u)",
        u=owner_id,
    )
    await _pay(db, owner_id, at_sql=f"now() - interval '{int(bought_days_ago)} days'")


async def _pay(db: Db, owner_id: int, *, at_sql: str, kind: str = "RENEW") -> None:
    await db.run(
        "INSERT INTO transactions (payment_id, user_id, status, is_test, purchase_type, "
        "gateway_type, pricing, currency, plan_snapshot, updated_at) "
        f"VALUES (gen_random_uuid(), :u, 'COMPLETED', false, '{kind}', 'YOOMONEY', "
        f"CAST('{{}}' AS jsonb), 'RUB', CAST('{{\"id\": 7}}' AS jsonb), {at_sql})",
        u=owner_id,
    )


async def _reserve(db: Db, owner_id: int, *, granted_sql: str, until_sql: str, ended: bool) -> None:
    await db.run(
        "INSERT INTO reserve_grants (user_id, remna_uuid, granted_at, reserve_expire_at, ended) "
        f"VALUES (:u, 'x', {granted_sql}, {until_sql}, :ended)",
        u=owner_id,
        ended=ended,
    )


async def _owner_expire(db: Db, owner_id: int, expire: datetime) -> None:
    await db.run(
        "UPDATE subscriptions SET expire_at = :e, updated_at = clock_timestamp() WHERE id = "
        "(SELECT current_subscription_id FROM users WHERE id = :u)",
        e=expire,
        u=owner_id,
    )


@pytest.mark.parametrize(
    "case",
    [
        # Пауза во время открытого резерва и её снятие: срок ушёл за конец окна.
        {"granted": "now() - interval '2 days'", "until": "now() + interval '5 days'", "ended": False, "days": 12},
        # Дни по промокоду или за приглашение поверх уже закрытого резерва.
        {"granted": "now() - interval '10 days'", "until": "now() - interval '3 days'", "ended": True, "days": 40},
    ],
)
async def test_time_grown_out_of_a_reserve_gives_the_family_nothing(db, case):
    """Срок владельца вырос из бесплатного резерва (пауза, дни по промокоду или за
    приглашение) — семья не получает ни полного набора серверов, ни трафика."""
    _plan, owner_id, (pid,) = await _family(db, 1)
    await _history(db, owner_id)
    await _reserve(db, owner_id, granted_sql=case["granted"], until_sql=case["until"], ended=case["ended"])
    await _owner_expire(db, owner_id, now() + timedelta(days=case["days"]))
    out = await db.reconcile(owner_id)
    assert out["suspended"] == [pid] and not out["synced"]
    after = (await db.profiles(owner_id))[0]
    assert after.suspend_reason == "owner_expired"
    assert db.panel.store[after.sub_remna_id].status.value == "DISABLED"
    async with db.session() as s:
        view = await family.family_view(s, None, owner_id, with_panel=False)
    assert view["reason"] == "reserve" and view["available"] is False


async def test_renewal_over_an_open_reserve_gives_only_the_paid_period(db):
    """Продление поверх открытого резерва: владелец получает период от конца резерва,
    семья — ровно купленный период от момента оплаты."""
    _plan, owner_id, (pid,) = await _family(db, 1)
    await _history(db, owner_id)
    await _reserve(
        db, owner_id, granted_sql="now() - interval '2 days'", until_sql="now() + interval '5 days'", ended=False
    )
    await _pay(db, owner_id, at_sql="now() - interval '1 hour'")
    until = await db.scalar("SELECT reserve_expire_at FROM reserve_grants WHERE user_id = :u", u=owner_id)
    paid_at = await db.scalar(
        "SELECT max(updated_at) FROM transactions WHERE user_id = :u", u=owner_id
    )
    await _owner_expire(db, owner_id, until + timedelta(days=30))
    async with db.session() as s:
        out = await family.after_purchase(s, db.panel, owner_id)
    assert out["synced"] == [pid]
    after = (await db.profiles(owner_id))[0]
    assert abs(after.sub_expire_at - (paid_at + timedelta(days=30))) < timedelta(seconds=1)


# ── включение и выключение как у панели 3.4.4 ───────────────────────────────


async def test_resume_of_a_profile_already_on_in_the_panel(db):
    """Выключение не дошло (панель осталась ACTIVE), владелец снова платит — профиль
    включается. Отдельное «включить» панель 3.4.4 отвергла бы (A030) навсегда."""
    from remnapy.enums.users import UserStatus

    _plan, owner_id, (pid,) = await _family(db, 1)
    p = (await db.profiles(owner_id))[0]
    await db.run(
        "UPDATE family_profiles SET status = 'suspended', suspend_reason = 'owner_blocked', "
        "suspended_at = now() WHERE id = :i",
        i=pid,
    )
    assert db.panel.store[p.sub_remna_id].status == UserStatus.ACTIVE
    out = await db.reconcile(owner_id)
    assert out["resumed"] == [pid] and not out["errors"]
    assert (await db.profiles(owner_id))[0].status == "active"
    assert db.panel.count("enable") == 0


async def test_suspend_of_a_profile_already_off_in_the_panel(db):
    """Профиль выключен в панели (руками или прошлым проходом), а у нас ещё «активен» —
    выключение проходит без ошибки «уже выключен» (A029)."""
    from remnapy.enums.users import UserStatus

    _plan, owner_id, (pid,) = await _family(db, 1)
    p = (await db.profiles(owner_id))[0]
    db.panel.store[p.sub_remna_id].status = UserStatus.DISABLED
    await db.run("UPDATE users SET is_blocked = true, updated_at = now() WHERE id = :u", u=owner_id)
    out = await db.reconcile(owner_id)
    assert out["suspended"] == [pid] and not out["errors"]
    assert db.panel.count("disable") == 0


async def test_traffic_is_reset_once_per_purchase(db):
    """Оплата — одно обнуление трафика, сколько бы проходов крона ни было. Сорвавшееся
    обнуление повторяется, удавшееся — нет."""
    _plan, owner_id, (pid,) = await _family(db, 1)
    await _renew_owner(db, owner_id, now() + timedelta(days=60))
    real = db.panel.users.reset_user_traffic
    calls = {"n": 0}

    async def flaky_reset(uuid):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("панель моргнула")
        return await real(uuid)

    db.panel.users.reset_user_traffic = flaky_reset
    for _ in range(4):
        await db.reconcile(owner_id)
    assert calls["n"] == 2, "обнуление трафика повторяется на каждом проходе"
    assert db.panel.count("reset") == 1


async def test_profile_name_never_reaches_html_of_admins_or_the_subscription_page(db):
    """Имя профиля пишет человек. В `users.name` теневого аккаунта (его база рендерит
    в уведомлениях админам как HTML) и в описание пользователя панели (плейсхолдер
    страницы подписки) оно не попадает — только номера."""
    plan_id = await db.plan()
    owner_id = await db.owner(plan_id)
    evil = '<a href="x">Ма&ма</a>'
    result = await db.create(owner_id, evil)
    assert result["result"] == "created"
    p = (await db.profiles(owner_id))[0]
    assert p.label == family.clean_label(evil), "метка профиля хранится как есть — для показа"
    name = await db.scalar("SELECT name FROM users WHERE id = :u", u=p.profile_user_id)
    description = db.panel.by_name(p.panel_username).description
    for text_ in (name, description):
        assert not set("<>&") & set(text_), text_
    assert name == f"Семья #{owner_id} · профиль {p.id}"


async def test_deleting_the_plan_does_not_take_the_family_mid_period(db):
    """Тариф удалили из каталога — оплатившие владельцы доживают срок с семьёй (как
    база оставляет им снимок тарифа), а условия видны в админке и снимаются там."""
    admin = importlib.import_module("src.web.endpoints.admin.family")
    plan_id, owner_id, (pid,) = await _family(db, 1)
    await db.run("DELETE FROM plans WHERE id = :p", p=plan_id)
    assert await db.scalar("SELECT count(*) FROM family_plan_terms WHERE plan_id = :p", p=plan_id) == 1
    await db.run("UPDATE family_profiles SET last_reconciled_at = NULL")
    out = await db.reconcile(owner_id)
    assert not out["suspended"] and (await db.profiles(owner_id))[0].status == "active"

    raw = admin.get_family_admin.__dishka_orig_func__
    async with db.session() as s:
        data = await raw(_admin=SimpleNamespace(role=5), session=s)
    deleted = [p for p in data["plans"] if p["id"] == plan_id]
    assert deleted and deleted[0]["deleted"] is True and deleted[0]["terms"] is not None

    # «Сделать обычным» для удалённого тарифа — и семья приостанавливается штатно.
    raw_delete = admin.delete_family_terms.__dishka_orig_func__
    async with db.session() as s:
        await raw_delete(plan_id=plan_id, _admin=SimpleNamespace(role=5), session=s)
    out = await db.reconcile(owner_id)
    assert out["suspended"] == [pid]


async def _gift(db: Db, owner_id: int, *, reward_type: str = "SUBSCRIPTION") -> None:
    """Владелец активировал подарок (или промокод) — как это записывает база."""
    await db.run(
        "WITH p AS (INSERT INTO promocodes (code, is_active, reward_type, availability, is_reusable) "
        f"VALUES (:c, true, '{reward_type}', 'ALL', false) RETURNING id) "
        "INSERT INTO promocode_activations (promocode_id, user_id, activated_at) "
        "SELECT id, :u, clock_timestamp() FROM p",
        c=uuid_lib.uuid4().hex[:10],
        u=owner_id,
    )


async def test_gift_of_the_same_plan_renews_the_family_with_fresh_traffic(db):
    """Подарок того же тарифа продлевает владельца и обнуляет ему трафик — семья
    получает и срок, и свежий трафик, а период замен начинается заново."""
    _plan, owner_id, (first, second) = await _family(db, 2, terms=(2, 2))
    async with db.session() as s:
        await family.delete_profile(s, db.panel, owner_id=owner_id, profile_id=first, actor="t")
    assert (await db.create(owner_id, "Замена"))["result"] == "created"
    async with db.session() as s:
        await family.delete_profile(s, db.panel, owner_id=owner_id, profile_id=second, actor="t")
    assert (await db.create(owner_id, "Ещё"))["reason"] == "period_limit"

    await _gift(db, owner_id)
    new_expire = now() + timedelta(days=60)
    await _owner_expire(db, owner_id, new_expire)
    out = await db.reconcile(owner_id)
    assert out["synced"]
    for p in await db.profiles(owner_id):
        assert abs(p.sub_expire_at - new_expire) < timedelta(seconds=1)
        assert ("reset", p.sub_remna_id) in db.panel.calls, "подарок не обнулил трафик семье"
    assert (await db.create(owner_id, "Ещё"))["result"] == "created", "период замен не начался заново"


async def test_bonus_days_extend_the_family_without_a_traffic_reset(db):
    """Промокод «+N дней» или дни за приглашение — сдвиг срока, а не новый период:
    база трафик владельцу не обнуляет, и семье — тоже."""
    _plan, owner_id, (pid,) = await _family(db, 1)
    await _gift(db, owner_id, reward_type="DURATION")
    new_expire = now() + timedelta(days=37)
    await _owner_expire(db, owner_id, new_expire)
    out = await db.reconcile(owner_id)
    assert out["synced"] == [pid]
    assert abs((await db.profiles(owner_id))[0].sub_expire_at - new_expire) < timedelta(seconds=1)
    assert db.panel.count("reset") == 0
