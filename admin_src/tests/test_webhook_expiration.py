"""Напоминания «подписка заканчивается» на панели 3.x (overlay_patches/webhook_expiration.py).

ЧТО ЗАПИРАЕМ. Панель 3.x шлёт одно `user.expiration` с `meta.expiration` в часах,
бот понимает только старые имена 2.x. Цена ошибки — либо снова тишина (как с 24.08),
либо «продлите, осталось 3 дня» не тем людям, либо поддельное событие, прошедшее
мимо подписи. Поэтому тесты держат смысл, а не строки:

  * часы панели ↔ имена событий базы ↔ строка для .env панели — одна и та же карта,
    и строка проходит правила, без которых панель не стартует;
  * перевод имени — только на подписанном теле: подпись не сошлась или meta
    подменено после подписи — события нет вовсе;
  * чужие события (даже с meta.expiration) и незнакомые часы не трогаются;
  * настоящий эндпоинт базы уводит переведённое событие в старую ветку, настоящий
    обработчик базы публикует сообщение с правильным днём;
  * кому не слать: не тот статус, резерв, конец резерва, пауза; кому слать;
  * дубль — одно сообщение; упавший обработчик не «съедает» повтор панели;
  * сбой фильтра не глушит напоминание и не отравляет транзакцию базы;
  * uuid дописывается при любом порядке обёрток с webhook_v3;
  * детектор выключенного env панели.

Сверку sha исходников базы и разрешимость имён закрывает CI-гейт «Правки overlay
применяются» (`failures()`/`pending()`).

Все данные синтетические. Запуск — внутри образа бота, как остальные тесты (см. ci.yml).
"""

import hashlib
import hmac
import importlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from fastapi import HTTPException
from loguru import logger
from pydantic import SecretStr

import overlay_patches

v3 = importlib.import_module("overlay_patches.webhook_v3")
exp = importlib.import_module("overlay_patches.webhook_expiration")
sdk_patch = importlib.import_module("overlay_patches.remnawave_sdk")
controllers = importlib.import_module("remnapy.controllers.webhooks")
service_mod = importlib.import_module("src.application.services.remnawave")
endpoint_mod = importlib.import_module("src.web.endpoints.remnawave")
user_events = importlib.import_module("src.application.events.user")

WebhookUtility = controllers.WebhookUtility
E = service_mod.RemnaUserEvent

SECRET = "ci-test-webhook-secret"
PANEL_ID = 101
REMNA_UUID = UUID("00000000-0000-4000-8000-00000000abcd")
EXPIRE = datetime(2030, 1, 13, 12, 0, 30, tzinfo=timezone.utc)
STAMP = "2030-01-10T12:00:00.123Z"


@pytest.fixture(autouse=True)
def patched() -> None:
    # В образе правки ставит хук на импорт; вне его — применяем сами, в том же
    # порядке, что и install(): модель, uuid, потом наши.
    v3.apply_model()
    v3.apply_handlers()
    exp.apply_parse()
    exp.apply_guard()
    exp.check_endpoint()


@pytest.fixture
def identity(monkeypatch: pytest.MonkeyPatch) -> None:
    class Identity:
        async def to_uuid(self, panel_id: int) -> UUID:
            assert panel_id == PANEL_ID
            return REMNA_UUID

    monkeypatch.setattr(overlay_patches, "_identity_map", Identity())


@pytest.fixture
def logs() -> Any:
    lines: list[tuple[str, str]] = []
    handler = logger.add(
        lambda m: lines.append((m.record["level"].name, m.record["message"])), level="DEBUG"
    )
    yield lines
    logger.remove(handler)


# ── тело вебхука, как его собирает панель ────────────────────────────────────


def panel_user(**over: Any) -> dict:
    """Пользователь панели 3.x: ключи GetFullUserResponseModel, uuid нет."""
    user = {
        "id": PANEL_ID,
        "shortUuid": "shortuuid-test",
        "username": "rs_test_user",
        "status": "ACTIVE",
        "trafficLimitBytes": 0,
        "trafficLimitStrategy": "NO_RESET",
        "expireAt": EXPIRE.isoformat().replace("+00:00", "Z"),
        "telegramId": None,
        "email": None,
        "description": None,
        "tag": None,
        "hwidDeviceLimit": None,
        "externalSquadUuid": None,
        "trojanPassword": "test",
        "vlessUuid": "00000000-0000-4000-8000-000000000001",
        "ssPassword": "test",
        "lastTriggeredThreshold": 0,
        "subRevokedAt": None,
        "lastTrafficResetAt": None,
        "createdAt": "2030-01-01T00:00:00.000Z",
        "updatedAt": "2030-01-01T00:00:00.000Z",
        "subscriptionUrl": "https://sub.example.test/shortuuid-test",
        "activeInternalSquads": [],
        "userTraffic": {
            "usedTrafficBytes": 0,
            "lifetimeUsedTrafficBytes": 0,
            "onlineAt": None,
            "firstConnectedAt": None,
            "lastConnectedNodeUuid": None,
        },
    }
    user.update(over)
    return user


def event_body(event: str = "user.expiration", meta: Any = None, data: Any = None, **extra: Any) -> dict:
    body = {
        "scope": "user",
        "event": event,
        "timestamp": STAMP,
        "data": panel_user() if data is None else data,
    }
    if meta is not None:
        body["meta"] = meta
    body.update(extra)
    return body


def signed(payload: dict, secret: str = SECRET) -> tuple[str, dict]:
    raw = json.dumps(payload, separators=(",", ":"))
    signature = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return raw, {"X-Remnawave-Signature": signature, "X-Remnawave-Timestamp": STAMP}


def parse(raw: str, headers: dict) -> Any:
    return WebhookUtility.parse_webhook(
        body=raw, headers=headers, webhook_secret=SECRET, validate=True
    )


# ── карта часов ──────────────────────────────────────────────────────────────


def test_offsets_match_base_event_names():
    assert exp.LEGACY_EVENT_BY_OFFSET == {
        -72: E.EXPIRES_IN_72_HOURS,
        -48: E.EXPIRES_IN_48_HOURS,
        -24: E.EXPIRES_IN_24_HOURS,
        24: E.EXPIRED_24_HOURS_AGO,
    }
    assert exp.BEFORE == {E.EXPIRES_IN_72_HOURS, E.EXPIRES_IN_48_HOURS, E.EXPIRES_IN_24_HOURS}
    assert exp.AFTER == {E.EXPIRED_24_HOURS_AGO}


def test_env_hint_passes_panel_validation():
    # Правила superRefine панели 3.4.4: ошибка в массиве = панель не стартует вовсе.
    assert "EXPIRATION_NOTIFICATIONS_ENABLED=true" in exp.PANEL_ENV_HINT
    raw = exp.PANEL_ENV_HINT.split("EXPIRATION_NOTIFICATIONS=")[1]
    assert raw == "[-72,-48,-24,24]" and " " not in raw
    hours = json.loads(raw)
    assert hours == sorted(set(hours))  # строго по возрастанию, без дублей
    assert all(isinstance(h, int) and h != 0 and -744 <= h <= 744 for h in hours)
    assert sum(h < 0 for h in hours) <= 5 and sum(h > 0 for h in hours) <= 5
    assert set(hours) == set(exp.LEGACY_EVENT_BY_OFFSET)


# ── перевод в разборе ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "hours, name",
    [
        (-72, "user.expires_in_72_hours"),
        (-48, "user.expires_in_48_hours"),
        (-24, "user.expires_in_24_hours"),
        (24, "user.expired_24_hours_ago"),
    ],
)
def test_signed_expiration_translated(hours: int, name: str):
    payload = parse(*signed(event_body(meta={"expiration": hours})))
    assert payload.event == name
    assert payload.data.id == PANEL_ID and payload.data.uuid is None
    assert payload.data.expire_at == EXPIRE


def test_translation_never_before_signature():
    raw, headers = signed(event_body(meta={"expiration": -72}))

    # Чужой секрет — события нет.
    assert WebhookUtility.parse_webhook(raw, headers, "other-secret") is None

    # Подпись от −72, а meta подменено на −24 уже после подписи — события нет.
    forged = raw.replace('"expiration":-72', '"expiration":-24')
    assert forged != raw
    assert parse(forged, headers) is None

    # Без заголовков подписи разбор не начинается вовсе.
    with pytest.raises(ValueError):
        WebhookUtility.parse_webhook(raw, {}, SECRET)


@pytest.mark.parametrize(
    "payload, event",
    [
        (event_body("user.expired", data=panel_user(status="EXPIRED")), "user.expired"),
        (event_body("user.modified", meta={"expiration": -24}), "user.modified"),
        (
            event_body("user.not_connected", meta={"notConnectedAfterHours": 24, "expiration": -24}),
            "user.not_connected",
        ),
        (
            event_body("service.panel_started", data={}, meta={"expiration": -24}, scope="service"),
            "service.panel_started",
        ),
    ],
)
def test_other_events_untouched(payload: dict, event: str, logs: Any):
    assert parse(*signed(payload)).event == event
    assert not [m for _, m in logs if "user.expiration" in m]


@pytest.mark.parametrize(
    "meta",
    [
        {"expiration": -12},
        {"expiration": 48},
        {"expiration": None},
        {"expiration": "-72"},
        {"expiration": True},
        {"expiration": -24.0},
        None,
        "not-a-dict",
        [],
    ],
)
def test_unknown_offset_stays(meta: Any, logs: Any):
    payload = parse(*signed(event_body(meta=meta)))
    assert payload.event == "user.expiration"
    warnings = [m for level, m in logs if level == "WARNING" and "user.expiration" in m]
    assert warnings and "EXPIRATION_NOTIFICATIONS=[-72,-48,-24,24]" in warnings[0]


# ── настоящий эндпоинт базы ──────────────────────────────────────────────────


class FakeRequest:
    def __init__(self, raw: str, headers: dict) -> None:
        self._raw = raw.encode()
        self.headers = headers

    async def body(self) -> bytes:
        return self._raw


class RecordingService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def handle_user_event(self, event: str, user: Any) -> None:
        self.calls.append((event, user))


CONFIG = SimpleNamespace(
    remnawave=SimpleNamespace(webhook_secret=SecretStr(SECRET)),
    build=SimpleNamespace(data={}),
)


async def test_base_endpoint_routes_to_legacy_branch():
    service = RecordingService()
    raw, headers = signed(event_body(meta={"expiration": -24}))

    response = await endpoint_mod._process_remnawave_webhook(
        request=FakeRequest(raw, headers),
        config=CONFIG,
        remna_webhook_service=service,
        event_publisher=None,
    )

    assert response.status_code == 200
    assert [event for event, _ in service.calls] == ["user.expires_in_24_hours"]

    bad = dict(headers, **{"X-Remnawave-Signature": "0" * 64})
    with pytest.raises(HTTPException) as err:
        await endpoint_mod._process_remnawave_webhook(
            request=FakeRequest(raw, bad),
            config=CONFIG,
            remna_webhook_service=service,
            event_publisher=None,
        )
    assert err.value.status_code == 401
    assert len(service.calls) == 1


# ── настоящий обработчик базы ────────────────────────────────────────────────


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.calls: list[tuple] = []

    async def set(self, key: str, value: str, ex: Any = None, nx: bool = False) -> Any:
        self.calls.append(("set", key, ex, nx))
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key: str) -> None:
        self.calls.append(("delete", key))
        self.store.pop(key, None)


class Savepoint:
    def __init__(self, session: "FakeSession") -> None:
        self.session = session

    async def __aenter__(self) -> "Savepoint":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        self.session.savepoints.append("rollback" if exc_type else "release")
        return False


class FakeSession:
    def __init__(self, fail: Exception | None = None, **flags: bool) -> None:
        self.row = SimpleNamespace(
            on_reserve=flags.get("on_reserve", False),
            reserve_ran_out=flags.get("reserve_ran_out", False),
            frozen=flags.get("frozen", False),
        )
        self.fail = fail
        self.executed: list[tuple[str, dict]] = []
        self.savepoints: list[str] = []

    def begin_nested(self) -> Savepoint:
        return Savepoint(self)

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.executed.append((str(statement), dict(params or {})))
        if self.fail:
            raise self.fail
        return SimpleNamespace(one=lambda: self.row)


class FakeUserDao:
    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.user = SimpleNamespace(id=7, telegram_id=None, remna_name="rs_test_user")
        self.lookups: list[Any] = []

    async def get_by_remna_uuid(self, remna_uuid: Any) -> Any:
        self.lookups.append(remna_uuid)
        return self.user


class FakeSubscriptionDao:
    def __init__(self, is_trial: bool) -> None:
        self.current = SimpleNamespace(expire_at=EXPIRE, is_trial=is_trial)

    async def get_current(self, user_id: int) -> Any:
        return self.current


class Bus:
    def __init__(self, fail_times: int = 0) -> None:
        self.events: list[Any] = []
        self.fail_times = fail_times

    async def publish(self, event: Any) -> None:
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("шина недоступна")
        self.events.append(event)


class SyncUser:
    async def system(self, dto: Any) -> None:
        return None


def service(session: FakeSession | None = None, is_trial: bool = False, bus: Bus | None = None) -> Any:
    svc = object.__new__(service_mod.RemnaWebhookService)
    svc.config = None
    svc.uow = None
    svc.user_dao = FakeUserDao(session or FakeSession())
    svc.subscription_dao = FakeSubscriptionDao(is_trial)
    svc.event_bus = bus or Bus()
    svc.redis = FakeRedis()
    svc.bot_service = None
    svc.sync_user = SyncUser()
    return svc


async def deliver(svc: Any, hours: int, **user: Any) -> None:
    payload = parse(*signed(event_body(meta={"expiration": hours}, data=panel_user(**user))))
    await svc.handle_user_event(payload.event, payload.data)


@pytest.mark.parametrize(
    "hours, status, day",
    [(-72, "ACTIVE", 3), (-48, "ACTIVE", 2), (-24, "ACTIVE", 1), (24, "EXPIRED", 1)],
)
async def test_chain_publishes_day(hours: int, status: str, day: int, identity: None):
    svc = service()
    await deliver(svc, hours, status=status)

    assert len(svc.event_bus.events) == 1
    event = svc.event_bus.events[0]
    wanted = user_events.SubscriptionExpiredAgoEvent if hours > 0 else user_events.SubscriptionExpiresEvent
    assert type(event) is wanted
    assert event.day == day and event.user is svc.user_dao.user and event.is_trial is False
    # Пользователь найден по восстановленному uuid, а не по пустому.
    assert set(svc.user_dao.lookups) == {REMNA_UUID}


async def test_trial_keeps_trial_flag(identity: None):
    # Решение владельца: пробник в последний день получает и напоминание, и скидку.
    svc = service(is_trial=True)
    await deliver(svc, -24)
    assert [e.is_trial for e in svc.event_bus.events] == [True]


@pytest.mark.parametrize(
    "hours, status, flags",
    [
        (-24, "DISABLED", {}),
        (-72, "EXPIRED", {}),
        (24, "ACTIVE", {}),
        (24, "LIMITED", {}),
        (-24, "ACTIVE", {"on_reserve": True}),
        (-72, "LIMITED", {"on_reserve": True}),
        (24, "EXPIRED", {"reserve_ran_out": True}),
        (-48, "ACTIVE", {"frozen": True}),
        (24, "EXPIRED", {"frozen": True}),
    ],
)
async def test_guard_skips(hours: int, status: str, flags: dict, identity: None, logs: Any):
    session = FakeSession(**flags)
    svc = service(session)
    await deliver(svc, hours, status=status)

    assert svc.event_bus.events == []
    assert svc.redis.store == {}  # пропуск не ставит метку «отправлено»
    assert [m for _, m in logs if "не шлём" in m]
    if not flags:
        assert session.executed == []  # статус отсекается без похода в базу


@pytest.mark.parametrize(
    "hours, status, flags",
    [
        (-48, "LIMITED", {}),
        (24, "EXPIRED", {}),
        # Старый резерв, кончившийся не сейчас, напоминанию о подписке не помеха.
        (-24, "ACTIVE", {"reserve_ran_out": True}),
        (24, "EXPIRED", {"on_reserve": True}),
    ],
)
async def test_guard_sends(hours: int, status: str, flags: dict, identity: None):
    session = FakeSession(**flags)
    svc = service(session)
    await deliver(svc, hours, status=status)

    assert len(svc.event_bus.events) == 1
    sql, params = session.executed[0]
    assert params == {"u": 7, "exp": EXPIRE}
    assert session.savepoints == ["release"]


async def test_duplicate_delivery_sent_once(identity: None, logs: Any):
    svc = service()
    await deliver(svc, -48)
    await deliver(svc, -48)

    assert len(svc.event_bus.events) == 1
    sets = [c for c in svc.redis.calls if c[0] == "set"]
    assert len(sets) == 2
    key = f"overlay:expiry_reminder:7:user.expires_in_48_hours:{int(EXPIRE.timestamp())}"
    assert all(c[1] == key and c[2] == exp.DEDUPE_TTL and c[3] is True for c in sets)
    assert [m for _, m in logs if "уже отправляли" in m]

    # Другой интервал — другое напоминание, не дубль.
    await deliver(svc, -24)
    assert [e.day for e in svc.event_bus.events] == [2, 1]


async def test_failure_releases_claim(identity: None):
    svc = service(bus=Bus(fail_times=1))

    with pytest.raises(RuntimeError):
        await deliver(svc, -24)
    assert svc.redis.store == {}
    assert svc.event_bus.events == []

    # Панель повторит после 503 — повтор обязан дойти.
    await deliver(svc, -24)
    assert len(svc.event_bus.events) == 1


async def test_guard_fail_open_on_sql_error(identity: None, logs: Any):
    session = FakeSession(fail=RuntimeError('relation "reserve_grants" does not exist'))
    svc = service(session)
    await deliver(svc, -24)

    assert len(svc.event_bus.events) == 1
    assert session.savepoints == ["rollback"]
    assert [m for level, m in logs if level == "ERROR" and "резерв/пауза" in m]


async def test_dedupe_unavailable_still_sends(identity: None, logs: Any):
    class DownRedis(FakeRedis):
        async def set(self, *args: Any, **kwargs: Any) -> Any:
            raise ConnectionError("redis down")

    svc = service()
    svc.redis = DownRedis()
    await deliver(svc, -72)

    assert len(svc.event_bus.events) == 1
    assert [m for level, m in logs if level == "WARNING" and "дублей" in m]


async def test_other_user_events_no_queries(identity: None):
    session = FakeSession()
    svc = service(session)
    for event, status in (("user.traffic_reset", "ACTIVE"), ("user.expired", "EXPIRED")):
        user = panel_user(status=status, expireAt="2020-01-01T00:00:00.000Z")
        payload = parse(*signed(event_body(event, data=user)))
        await svc.handle_user_event(payload.event, payload.data)

    assert session.executed == [] and session.savepoints == []
    assert svc.redis.calls == []


@pytest.mark.parametrize("order", ["webhook_v3 first", "guard first"])
async def test_uuid_filled_regardless_of_order(order: str, identity: None, monkeypatch: pytest.MonkeyPatch):
    seen: list[Any] = []

    class Stand:
        async def handle_user_event(self, event: str, remna_user: Any) -> None:
            seen.append(remna_user.uuid)

        async def handle_device_event(self, event: str, remna_user: Any, device: Any) -> None:
            return None

    monkeypatch.setattr(service_mod, "RemnaWebhookService", Stand)
    steps = [v3.apply_handlers, exp.apply_guard]
    for step in steps if order == "webhook_v3 first" else reversed(steps):
        step()
    assert exp.apply_guard() == "уже применено"

    svc = Stand()
    svc.user_dao = FakeUserDao(FakeSession())
    svc.redis = FakeRedis()
    payload = parse(*signed(event_body(meta={"expiration": -24})))
    await svc.handle_user_event(payload.event, payload.data)

    assert seen == [REMNA_UUID]
    assert svc.user_dao.lookups == [REMNA_UUID]


def test_apply_idempotent_distinct_flag():
    cls = service_mod.RemnaWebhookService
    handler = cls.handle_user_event
    parser = WebhookUtility.__dict__["parse_webhook"]

    assert exp.apply_guard() == "уже применено"
    assert exp.apply_parse() == "уже применено"
    # И webhook_v3 не оборачивает второй раз, хотя снаружи теперь не его обёртка.
    assert v3.apply_handlers().endswith("уже было")
    assert cls.handle_user_event is handler
    assert WebhookUtility.__dict__["parse_webhook"] is parser
    assert cls.__dict__["_overlay_expiry_guard"] is True
    # Флаг webhook_v3 не наш: с ним webhook_v3 принял бы нашу обёртку за свою.
    assert not getattr(handler, "_overlay_wrapped", False)


def test_registered_in_install():
    applied = " | ".join(overlay_patches.applied())
    failed = {name for name, _ in overlay_patches.failures()}
    for name in (
        "напоминания: user.expiration панели 3.x",
        "напоминания: кому не слать",
        "напоминания: эндпоинт вебхука не менялся",
    ):
        assert name in applied
        assert name not in failed


def test_guard_sql_fragments():
    sql = " ".join(str(exp.GUARD_SQL).split())
    for fragment in (
        "FROM reserve_grants r WHERE r.user_id = :u AND r.ended = false",
        "r.reserve_expire_at BETWEEN CAST(:exp AS timestamptz) - interval '2 hours' "
        "AND CAST(:exp AS timestamptz) + interval '2 hours'",
        "FROM subscription_freezes f WHERE f.user_id = :u AND f.active = true",
        "AS on_reserve",
        "AS reserve_ran_out",
        "AS frozen",
    ):
        assert fragment in sql


# ── детектор выключенного env панели ─────────────────────────────────────────


class FakeClient:
    def __init__(self, notifications: Any = None, fail: Exception | None = None) -> None:
        self.notifications = notifications
        self.fail = fail
        self.paths: list[str] = []

    async def get(self, path: str, timeout: Any = None) -> Any:
        self.paths.append(path)
        if self.fail:
            raise self.fail
        body = {"response": {"notifications": self.notifications}}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)


def loud(logs: Any) -> list[tuple[str, str]]:
    return [(level, m) for level, m in logs if level in ("WARNING", "ERROR")]


async def test_detector_disabled_is_error(logs: Any):
    client = FakeClient({"webhook": True, "expirationNotifications": None})
    await sdk_patch._check_expiration_notifications(client)

    assert client.paths == ["/system/configuration"]
    [(level, message)] = loud(logs)
    assert level == "ERROR"
    assert "EXPIRATION_NOTIFICATIONS_ENABLED=true" in message
    assert "EXPIRATION_NOTIFICATIONS=[-72,-48,-24,24]" in message


async def test_detector_enabled_is_quiet(logs: Any):
    await sdk_patch._check_expiration_notifications(
        FakeClient({"expirationNotifications": [-72, -48, -24, 24]})
    )
    assert loud(logs) == []


async def test_detector_unknown_hours_warn(logs: Any):
    await sdk_patch._check_expiration_notifications(
        FakeClient({"expirationNotifications": [-72, -12, 24, 72]})
    )
    [(level, message)] = loud(logs)
    assert level == "WARNING" and "[-12, 72]" in message


@pytest.mark.parametrize(
    "client",
    [FakeClient(fail=RuntimeError("panel down")), FakeClient({"webhook": True}), FakeClient("oops")],
)
async def test_detector_unreadable_is_debug_only(client: FakeClient, logs: Any):
    await sdk_patch._check_expiration_notifications(client)
    assert loud(logs) == []


@pytest.mark.parametrize("version, checked", [("3.4.4", True), ("2.8.1", False)])
async def test_provider_runs_detector_only_on_3x(
    version: str, checked: bool, monkeypatch: pytest.MonkeyPatch, logs: Any
):
    """Детектор встроен в настоящий провайдер SDK и не ломает его сборку."""
    import asyncio

    import httpx
    from dishka import Provider, Scope, make_async_container, provide
    from remnapy import RemnawaveSDK
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.core.config import AppConfig

    paths: list[str] = []

    def panel(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/system/metadata"):
            return httpx.Response(200, json={"response": {"version": version}})
        return httpx.Response(200, json={"response": {"notifications": {"expirationNotifications": None}}})

    class MockedClient(httpx.AsyncClient):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(transport=httpx.MockTransport(panel), **kwargs)

    remnawave = SimpleNamespace(
        token=SecretStr("t"),
        caddy_token=SecretStr(""),
        cf_client_id=SecretStr(""),
        cf_client_secret=SecretStr(""),
        is_external=False,
        url=SecretStr("http://panel.test"),
        cookies={},
    )

    class Context(Provider):
        scope = Scope.APP

        @provide
        def config(self) -> AppConfig:
            return SimpleNamespace(remnawave=remnawave)  # type: ignore[return-value]

        @provide
        def sessions(self) -> async_sessionmaker[AsyncSession]:
            return None  # type: ignore[return-value]

    monkeypatch.setattr(sdk_patch, "AsyncClient", MockedClient)
    # Провайдер кладёт карту 3.x в глобаль overlay — после теста вернуть как было.
    monkeypatch.setattr(overlay_patches, "_identity_map", None)

    container = make_async_container(Context(), sdk_patch.RemnawaveProvider())
    try:
        sdk = await container.get(RemnawaveSDK)
        await asyncio.gather(*list(sdk_patch._background))
        assert (type(sdk).__name__ == "RemnawaveSDKv3") is checked
    finally:
        await container.close()

    assert ("/api/system/configuration" in paths) is checked
    errors = [m for level, m in logs if level == "ERROR" and "ВЫКЛЮЧЕНЫ" in m]
    assert bool(errors) is checked
