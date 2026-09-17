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
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from loguru import logger

from . import PatchTargetChanged, identity_map

# Методы сервиса, которым приходит объект пользователя из события.
_HANDLERS = ("handle_user_event", "handle_device_event")


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
