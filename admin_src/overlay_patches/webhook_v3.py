"""Вебхуки панели 3.x: восстановить uuid, которого в них больше нет.

ЧТО ЛОМАЕТСЯ. Панель шлёт боту события — «пользователь создан», «изменён»,
«добавлено устройство», «истёк срок». В 3.x у пользователя больше нет `uuid`, и
в теле события приходит только числовой `id`. Модель события в SDK требует `uuid`,
поэтому КАЖДОЕ событие отваливалось на разборе с «Webhook validation failed», а
бот отвечал панели 401. Наружу это выглядит как «уведомления перестали приходить»
и «подписки не синхронизируются» — без единой подсказки, что дело в вебхуках.

Слой совместимости 3.x закрывал ИСХОДЯЩИЕ вызовы к панели; это входящая сторона,
и её он не касается.

ПОЧЕМУ ДВЕ ПРАВКИ, А НЕ ОДНА. Тело события подписано, и подпись считается по
исходным байтам — дописать `uuid` до проверки нельзя, подпись сразу перестанет
сходиться. Значит:

  1. модель события перестаёт ТРЕБОВАТЬ `uuid` (иначе разбор не дойдёт до нас);
  2. обработчик бота дописывает `uuid` уже в разобранном объекте — по числовому
     `id`, через ту же карту идентичности, что и весь слой 3.x.

Так подпись проверяется по неизменному телу, а весь код бота дальше работает с
привычным `uuid` и находит нашего пользователя в базе.

НА 2.x НИЧЕГО НЕ МЕНЯЕТСЯ. Там `uuid` приходит в теле, и обработчик просто не
находит, что дописывать. Карта в этом случае отсутствует — правка молча пропускает.

УСТРОЙСТВА. В событии `user_hwid_devices.added/deleted` два объекта: пользователь
и устройство. Пользователя закрывали правки выше, а устройство панель 3.x шлёт с
числовым `userId` вместо `userUuid` (так же, как её API устройств с 2.8), и модель
устройства в SDK падала на «userUuid Field required». Бот отвечал 401, панель трижды
повторяла, и уведомление админам «#UserDeviceAddedEvent» не доходило ни разу.
Лечится тем же приёмом: `userUuid` у устройства необязателен, а после разбора в
него пишется uuid владельца — того самого пользователя из события, которого мы
только что восстановили.

КОМУ УХОДИТ УВЕДОМЛЕНИЕ ОБ УСТРОЙСТВЕ И ПОЧЕМУ БЕЗ ДУБЛЕЙ. База делает из события
системное уведомление — владельцу и админам (тумблер «USER_DEVICES_UPDATED» в
настройках уведомлений бота), самому человеку не пишет. Человеку пишет наш крон
`new_device` («к вашей подписке подключилось новое устройство») — и только тем, у
кого роль USER. Адресаты не пересекаются, поэтому ничего не гасим. Держится это на
двух вещах, и обе заперты: сверка обработчика базы и проверка, что событие
устройства осталось системным (`check_device_notify`), плюс тест на выборку крона.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from loguru import logger

from . import PatchTargetChanged, expect_source, identity_map

# Методы сервиса, которым приходит объект пользователя из события.
_HANDLERS = ("handle_user_event", "handle_device_event")

# Поля устройства, которые читает обработчик базы, — без них уведомление не собрать.
_DEVICE_FIELDS = ("hwid", "platform", "device_model", "os_version", "user_agent")

# sha256 обработчика событий устройств базы v0.8.2. Мы его не заменяем, но решение
# «дублей с кроном new_device нет» верно, пока он шлёт только системные события.
SHA_DEVICE_HANDLER = "3bd18cdfa24d5071e9da54f9a15a6a8dff64e8286ebc4c24304453bf5fe68975"


def apply_model() -> str:
    """Разрешить событию приходить без `uuid` — иначе разбор упадёт до обработчика."""
    from remnapy.models import webhook as wh

    base = getattr(wh, "BaseUserDto", None)
    if base is None:
        raise PatchTargetChanged(
            "в remnapy.models.webhook нет BaseUserDto — модель события изменилась, "
            "вебхуки панели 3.x перестанут разбираться"
        )

    if "uuid" not in base.model_fields:
        return "поля uuid в модели уже нет — править нечего"
    if "id" not in base.model_fields:
        raise PatchTargetChanged(
            "в событии нет числового id — восстанавливать uuid будет нечем"
        )
    if not base.model_fields["uuid"].is_required():
        return "уже необязательное"

    # Правим КАЖДЫЙ класс отдельно. Pydantic при создании наследника делает свою
    # копию описания полей, поэтому смены в базовом классе наследникам не видно:
    # `UserDto` продолжал бы требовать uuid, хотя `BaseUserDto` уже нет.
    targets = [base] + [
        cls
        for name in dir(wh)
        if isinstance(cls := getattr(wh, name), type)
        and issubclass(cls, base)
        and cls is not base
    ]
    for cls in targets:
        field = cls.model_fields.get("uuid")
        if field is None or not field.is_required():
            continue
        field.annotation = Optional[UUID]
        field.default = None
        cls.model_rebuild(force=True)

    still = [c.__name__ for c in targets if c.model_fields["uuid"].is_required()]
    if still:
        raise PatchTargetChanged(f"uuid остался обязательным в моделях: {still}")

    return f"uuid в событии стал необязательным (моделей: {len(targets)})"


def apply_device_model() -> str:
    """Разрешить устройству приходить без `userUuid` — в 3.x у него только `userId`."""
    from remnapy.models import webhook as wh

    model = getattr(wh, "HwidUserDeviceDto", None)
    if model is None:
        raise PatchTargetChanged(
            "в remnapy.models.webhook нет HwidUserDeviceDto — модель события устройства "
            "изменилась, вебхуки устройств панели 3.x перестанут разбираться"
        )
    missing = [name for name in _DEVICE_FIELDS if name not in model.model_fields]
    if missing:
        raise PatchTargetChanged(
            f"в модели устройства нет полей {missing} — обработчик базы их читает"
        )

    field = model.model_fields.get("user_uuid")
    if field is None:
        return "поля userUuid в модели устройства уже нет — править нечего"
    if not field.is_required():
        return "уже необязательное"

    # Тот же приём, что для пользователя выше: меняем описание поля и пересобираем
    # валидатор. Наследников у модели нет, а WebhookPayloadDto получает уже готовый
    # объект события и заново устройство не проверяет.
    field.annotation = Optional[UUID]
    field.default = None
    model.model_rebuild(force=True)

    if model.model_fields["user_uuid"].is_required():
        raise PatchTargetChanged("userUuid остался обязательным в модели устройства")
    return "userUuid в событии устройства стал необязательным"


def check_device_notify() -> str:
    """Событие устройства из вебхука — уведомление админам, а не человеку.

    Ничего не меняет, только сверяет. Человеку о новом устройстве пишет крон
    `new_device`; если база однажды начнёт писать ему сама, он получит одно и то
    же дважды. Узнать об этом надо на гейте, а не от владельца.
    """
    import src.application.services.remnawave as target
    from src.application.events.base import SystemEvent
    from src.application.events.base import UserEvent as PersonalEvent

    expect_source(
        target,
        "RemnaWebhookService.handle_device_event",
        SHA_DEVICE_HANDLER,
        "обработчик вебхуков устройств",
    )
    for name in ("UserDeviceAddedEvent", "UserDeviceDeletedEvent"):
        event = getattr(target, name, None)
        if event is None:
            raise PatchTargetChanged(f"обработчик устройств больше не публикует {name}")
        if not issubclass(event, SystemEvent) or issubclass(event, PersonalEvent):
            raise PatchTargetChanged(
                f"{name} больше не системное событие: база шлёт его человеку, и оно "
                f"задвоится с уведомлением крона new_device — разведите их"
            )
    return "устройства: база пишет админам, крон new_device — человеку, дублей нет"


def apply_handlers() -> str:
    """Дописать `uuid` в разобранном событии — по числовому id через карту."""
    from src.application.services.remnawave import RemnaWebhookService

    # Флаг и на классе, а не только на функции: поверх нас может встать другая
    # обёртка (фильтр напоминаний, webhook_expiration.py), и по флагу на функции
    # повторный вызов нас уже не узнал бы — обернул бы обработчик второй раз.
    if RemnaWebhookService.__dict__.get("_overlay_uuid_restored", False):
        return "uuid восстанавливается в событиях: уже было"

    patched = []
    for name in _HANDLERS:
        original = getattr(RemnaWebhookService, name, None)
        if original is None:
            raise PatchTargetChanged(
                f"у RemnaWebhookService нет {name} — база перестроила обработку "
                f"вебхуков, события панели 3.x останутся без uuid"
            )
        if getattr(original, "_overlay_wrapped", False):
            continue

        def make(orig, method_name):
            async def wrapper(self, event, remna_user, *args, **kwargs):
                if getattr(remna_user, "uuid", None) is None:
                    await _fill_uuid(remna_user, method_name)
                # Устройство (handle_device_event) пришло без userUuid — его владелец
                # и есть пользователь события, uuid которого только что восстановлен.
                for device in args:
                    if hasattr(device, "user_uuid") and device.user_uuid is None:
                        device.user_uuid = getattr(remna_user, "uuid", None)
                return await orig(self, event, remna_user, *args, **kwargs)

            wrapper._overlay_wrapped = True  # type: ignore[attr-defined]
            return wrapper

        setattr(RemnaWebhookService, name, make(original, name))
        patched.append(name)

    RemnaWebhookService._overlay_uuid_restored = True
    return f"uuid восстанавливается в событиях: {', '.join(patched) or 'уже было'}"


async def _fill_uuid(remna_user, method_name: str) -> None:
    identity = identity_map()
    panel_id = getattr(remna_user, "id", None)

    if identity is None or panel_id is None:
        # На 2.x карты нет, но там и uuid приходит сам; сюда попадаем, только если
        # панель 3.x, а слой не поднялся — молчать нельзя, иначе событие уйдёт в
        # обработчик с пустым uuid и «пользователь не найден» без объяснения.
        logger.error(
            f"Overlay: событие {method_name} пришло без uuid, восстановить нечем "
            f"(id={panel_id}, карта={'есть' if identity else 'нет'})"
        )
        return

    try:
        remna_user.uuid = await identity.to_uuid(int(panel_id))
    except Exception as exc:  # noqa: BLE001 — событие важнее, чем наша уверенность
        logger.error(f"Overlay: не восстановил uuid для id={panel_id}: {exc}")
