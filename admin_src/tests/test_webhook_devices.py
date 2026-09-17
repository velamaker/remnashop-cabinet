"""Вебхуки устройств панели 3.x (overlay_patches/webhook_v3.py: apply_device_model и соседи).

ЧТО БЫЛО. Панель 3.4.4 шлёт `user_hwid_devices.added/deleted`, где устройство несёт
числовой `userId` и не несёт `userUuid`. Модель устройства в remnapy его требовала:
«1 validation error for HwidUserDeviceDto: userUuid Field required», бот отвечал 401,
панель трижды повторяла, а уведомление админам о новом/удалённом устройстве не
доходило ни разу.

ЧТО ЗАПИРАЕМ:
  * подписанное тело в форме панели 3.4.4 разбирается, `userId`/`requestIp` не мешают;
  * настоящий эндпоинт базы уводит событие в `handle_device_event`, подделка — 401;
  * настоящий обработчик базы публикует UserDeviceAddedEvent/DeletedEvent нашего
    пользователя (uuid восстановлен, у устройства проставлен владелец);
  * КОМУ уходит: событие устройства ловит только системный обработчик уведомлений,
    оно уходит ролям OWNER/DEV/ADMIN и не уходит человеку; тумблер его гасит;
  * крон new_device пишет только ролям USER — адресаты с базой не пересекаются,
    значит одного и того же человека одно устройство не будит дважды;
  * сверка «событие устройства осталось системным» валит правку, если база
    однажды начнёт писать человеку сама.

Все данные синтетические. Запуск — внутри образа бота, как остальные тесты (см. ci.yml).
"""

import hashlib
import hmac
import importlib
import inspect
import json
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

import overlay_patches

v3 = importlib.import_module("overlay_patches.webhook_v3")
controllers = importlib.import_module("remnapy.controllers.webhooks")
models = importlib.import_module("remnapy.models.webhook")
service_mod = importlib.import_module("src.application.services.remnawave")
endpoint_mod = importlib.import_module("src.web.endpoints.remnawave")
system_events = importlib.import_module("src.application.events.system")
base_events = importlib.import_module("src.application.events.base")
notification_mod = importlib.import_module("src.infrastructure.services.notification")
settings_dto = importlib.import_module("src.application.dto.settings")
enums = importlib.import_module("src.core.enums")

WebhookUtility = controllers.WebhookUtility
SECRET = "ci-test-webhook-secret"
PANEL_ID = 202
REMNA_UUID = UUID("00000000-0000-4000-8000-00000000d0d0")
STAMP = "2030-02-01T10:00:00.000Z"
HWID = "test-hwid-0001"


@pytest.fixture(autouse=True)
def patched() -> None:
    v3.apply_model()
    v3.apply_device_model()
    v3.apply_handlers()


@pytest.fixture
def identity(monkeypatch: pytest.MonkeyPatch) -> None:
    class Identity:
        async def to_uuid(self, panel_id: int) -> UUID:
            assert panel_id == PANEL_ID
            return REMNA_UUID

    monkeypatch.setattr(overlay_patches, "_identity_map", Identity())


def panel_user() -> dict:
    return {
        "id": PANEL_ID,
        "shortUuid": "shortuuid-dev",
        "username": "rs_test_device",
        "status": "ACTIVE",
        "trafficLimitBytes": 0,
        "trafficLimitStrategy": "NO_RESET",
        "expireAt": "2030-03-01T00:00:00.000Z",
        "telegramId": None,
        "email": None,
        "description": None,
        "tag": None,
        "hwidDeviceLimit": 3,
        "externalSquadUuid": None,
        "trojanPassword": "test",
        "vlessUuid": "00000000-0000-4000-8000-000000000002",
        "ssPassword": "test",
        "lastTriggeredThreshold": 0,
        "subRevokedAt": None,
        "lastTrafficResetAt": None,
        "createdAt": "2030-01-01T00:00:00.000Z",
        "updatedAt": "2030-01-01T00:00:00.000Z",
        "subscriptionUrl": "https://sub.example.test/shortuuid-dev",
        "activeInternalSquads": [],
        "userTraffic": {
            "usedTrafficBytes": 0,
            "lifetimeUsedTrafficBytes": 0,
            "onlineAt": None,
            "firstConnectedAt": None,
            "lastConnectedNodeUuid": None,
        },
    }


def device_body(event: str = "user_hwid_devices.added") -> dict:
    # Ключи — ровно BaseUserHwidDevicesResponseModel панели 3.4.4: userId вместо userUuid.
    return {
        "scope": "user_hwid_devices",
        "event": event,
        "timestamp": STAMP,
        "data": {
            "hwidUserDevice": {
                "hwid": HWID,
                "userId": PANEL_ID,
                "platform": "iOS",
                "osVersion": "18.0",
                "deviceModel": "iPhone",
                "userAgent": "TestClient/1.0",
                "requestIp": "192.0.2.10",
                "createdAt": STAMP,
                "updatedAt": STAMP,
            },
            "user": panel_user(),
        },
    }


def signed(payload: dict) -> tuple[str, dict]:
    raw = json.dumps(payload, separators=(",", ":"))
    signature = hmac.new(SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return raw, {"X-Remnawave-Signature": signature, "X-Remnawave-Timestamp": STAMP}


def parse(raw: str, headers: dict) -> Any:
    return WebhookUtility.parse_webhook(body=raw, headers=headers, webhook_secret=SECRET, validate=True)


# ── разбор ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("event", ["user_hwid_devices.added", "user_hwid_devices.deleted"])
def test_panel_3x_device_payload_parses(event: str):
    payload = parse(*signed(device_body(event)))

    assert payload.event == event
    assert isinstance(payload.data, models.UserHwidDeviceEventDto)
    device, user = payload.data.hwid_user_device, payload.data.user
    assert isinstance(device, models.HwidUserDeviceDto)
    assert (device.hwid, device.platform, device.os_version, device.device_model, device.user_agent) == (
        HWID,
        "iOS",
        "18.0",
        "iPhone",
        "TestClient/1.0",
    )
    assert device.user_uuid is None
    assert user.id == PANEL_ID and user.uuid is None


def test_device_model_still_validates_the_rest():
    # Сделали необязательным ровно userUuid, а не всё подряд: без hwid разбор падает.
    body = device_body()
    del body["data"]["hwidUserDevice"]["hwid"]
    with pytest.raises(Exception, match="hwid"):
        parse(*signed(body))


def test_forged_device_event_rejected():
    raw, headers = signed(device_body())
    assert parse(raw.replace(HWID, "other-hwid"), headers) is None


def test_apply_device_model_idempotent():
    assert v3.apply_device_model() == "уже необязательное"
    assert not models.HwidUserDeviceDto.model_fields["user_uuid"].is_required()
    assert models.HwidUserDeviceDto.model_fields["hwid"].is_required()


# ── эндпоинт и обработчик базы ───────────────────────────────────────────────


class FakeRequest:
    def __init__(self, raw: str, headers: dict) -> None:
        self._raw = raw.encode()
        self.headers = headers

    async def body(self) -> bytes:
        return self._raw


CONFIG = SimpleNamespace(
    remnawave=SimpleNamespace(webhook_secret=SecretStr(SECRET)),
    build=SimpleNamespace(data={}),
)


async def test_base_endpoint_routes_device_event():
    calls: list[tuple] = []

    class Service:
        async def handle_device_event(self, event: str, user: Any, device: Any) -> None:
            calls.append((event, user.id, device.hwid))

    raw, headers = signed(device_body())
    response = await endpoint_mod._process_remnawave_webhook(
        request=FakeRequest(raw, headers), config=CONFIG, remna_webhook_service=Service(), event_publisher=None
    )
    assert response.status_code == 200
    assert calls == [("user_hwid_devices.added", PANEL_ID, HWID)]

    bad = dict(headers, **{"X-Remnawave-Signature": "0" * 64})
    with pytest.raises(HTTPException) as err:
        await endpoint_mod._process_remnawave_webhook(
            request=FakeRequest(raw, bad), config=CONFIG, remna_webhook_service=Service(), event_publisher=None
        )
    assert err.value.status_code == 401
    assert len(calls) == 1


class Bus:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)


class UserDao:
    def __init__(self) -> None:
        self.lookups: list[Any] = []
        self.user = SimpleNamespace(id=9, telegram_id=None, username=None, name="Test", email=None)

    async def get_by_remna_uuid(self, remna_uuid: Any) -> Any:
        self.lookups.append(remna_uuid)
        return self.user if remna_uuid == REMNA_UUID else None


@pytest.mark.parametrize(
    "event, published",
    [
        ("user_hwid_devices.added", "UserDeviceAddedEvent"),
        ("user_hwid_devices.deleted", "UserDeviceDeletedEvent"),
    ],
)
async def test_chain_publishes_device_event(event: str, published: str, identity: None):
    svc = object.__new__(service_mod.RemnaWebhookService)
    svc.user_dao = UserDao()
    svc.event_bus = Bus()

    payload = parse(*signed(device_body(event)))
    device = payload.data.hwid_user_device
    await svc.handle_device_event(payload.event, payload.data.user, device)

    assert svc.user_dao.lookups == [REMNA_UUID]
    assert device.user_uuid == REMNA_UUID  # владелец устройства — пользователь события
    [sent] = svc.event_bus.events
    assert type(sent).__name__ == published
    assert (sent.user_id, sent.hwid, sent.platform, sent.device_model, sent.os_version, sent.user_agent) == (
        9,
        HWID,
        "iOS",
        "iPhone",
        "18.0",
        "TestClient/1.0",
    )


# ── кому уходит уведомление ──────────────────────────────────────────────────


def device_event() -> Any:
    return system_events.UserDeviceAddedEvent(
        user_id=9,
        telegram_id=None,
        username=None,
        name="Test",
        email=None,
        hwid=HWID,
        platform="iOS",
        device_model="iPhone",
        os_version="18.0",
        user_agent="TestClient/1.0",
    )


def test_device_event_reaches_only_system_listener():
    # Шина базы раздаёт событие всем слушателям, чей тип подходит по isinstance.
    event = device_event()
    listeners = sorted(
        name
        for name, member in vars(notification_mod.NotificationService).items()
        if any(isinstance(event, t) for t in getattr(member, "_event_types", ()))
    )
    assert listeners == ["on_system_event"]
    assert not isinstance(event, base_events.UserEvent)


class Worker:
    def __init__(self) -> None:
        self.tasks: list[Any] = []

    async def enqueue(self, task: Any) -> None:
        self.tasks.append(task)


def notifier(enabled: bool) -> Any:
    notifications = settings_dto.NotificationsSettingsDto()
    kind = enums.SystemNotificationType.USER_DEVICES_UPDATED
    if not enabled:
        notifications.toggle(kind)
    assert notifications.is_enabled(kind) is enabled

    class Settings:
        async def get(self) -> Any:
            return SimpleNamespace(notifications=notifications)

    service = object.__new__(notification_mod.NotificationService)
    service.settings_dao = Settings()
    service.worker = Worker()
    service.personal: list[Any] = []

    async def notify_user(user: Any, payload: Any = None, i18n_key: Any = None) -> None:
        service.personal.append(user)

    service.notify_user = notify_user
    return service


async def test_device_notification_goes_to_admins_only():
    service = notifier(enabled=True)
    await service.on_system_event(device_event())

    [task] = service.worker.tasks
    Role = enums.Role
    assert task.roles == [Role.OWNER, Role.DEV, Role.ADMIN]
    assert task.payload.i18n_key == "event-user.device-added"
    assert service.personal == []


async def test_device_notification_toggle_still_works():
    service = notifier(enabled=False)
    await service.on_system_event(device_event())
    assert service.worker.tasks == [] and service.personal == []


def test_new_device_cron_writes_only_to_users():
    # Крон пишет самому человеку — и только роли USER. Админ, которому база шлёт
    # «#UserDeviceAddedEvent», своё же устройство от крона не получит.
    module = importlib.import_module("src.infrastructure.taskiq.tasks.new_device")
    source = " ".join(inspect.getsource(module).split())
    assert "WHERE u.role = 'USER' AND s.user_remna_id IS NOT NULL" in source
    assert "bot.send_message(int(tg_id)" in source
    for admin_path in ("notify_admins", "notify_system", "UserDeviceAddedEvent", "event_bus"):
        assert admin_path not in source


# ── сверка на гейте ──────────────────────────────────────────────────────────


def test_check_device_notify_passes_on_current_base():
    assert "дублей нет" in v3.check_device_notify()


def test_check_device_notify_refuses_personal_event(monkeypatch: pytest.MonkeyPatch):
    class Personal(base_events.UserEvent):
        pass

    monkeypatch.setattr(service_mod, "UserDeviceAddedEvent", Personal)
    with pytest.raises(overlay_patches.PatchTargetChanged, match="задвоится"):
        v3.check_device_notify()


def test_registered_in_install():
    applied = " | ".join(overlay_patches.applied())
    failed = {name for name, _ in overlay_patches.failures()}
    for name in ("вебхуки: модель устройства", "вебхуки: устройства — админам, не человеку"):
        assert name in applied
        assert name not in failed
