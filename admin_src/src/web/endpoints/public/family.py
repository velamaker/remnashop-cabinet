"""Public: семейные профили владельца подписки (overlay).

Четыре ручки: что есть и что можно (GET /family), завести профиль, отвязать его
устройства и удалить его. Всё важное — в services/overlay_family.py, здесь только
разбор запроса и ответ. Правила те же, что у соседней докупки устройства:
  * бизнес-отказы — 200 с полем `result`, а не HTTP-ошибка: `ApiError.detail`
    кабинета — строка, и коду причины неоткуда взять перевод;
  * `request_id` кабинета — ключ идемпотентности: двойной клик «Добавить» даёт
    один профиль, чужой ключ — 409 без подробностей;
  * профиль чужой семьи — 404, как несуществующий: номер профиля не должен
    рассказывать, есть ли такой у кого-то ещё.
CSRF закрывает общая мидлварь (overlay_app): мутации с нашей кукой без своего
Origin/Referer не проходят.
"""

from __future__ import annotations

import uuid as uuid_lib
from datetime import datetime
from typing import Any, Optional

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Remnawave
from src.application.common.dao import SettingsDao, SubscriptionDao, UserDao
from src.infrastructure.services import overlay_family as family
from src.web.endpoints.public._common import CurrentUser

router = APIRouter(prefix="/family", tags=["Public - Family"])


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if isinstance(value, datetime) else None


def _link(url: Optional[str]) -> Optional[str]:
    """Ссылка профиля — через крипто-алиас, если владелец его включил (как своя)."""
    if not url:
        return None
    try:
        from src.web.endpoints.public.sub_alias import maybe_alias_url

        return maybe_alias_url(url)
    except Exception:  # noqa: BLE001 — алиас не сработал: отдаём настоящую ссылку
        return url


async def _reset_rules(settings_dao: SettingsDao) -> tuple[bool, int]:
    """Сброс устройств профиля — по тем же правилам бота, что и у владельца."""
    try:
        settings = await settings_dao.get()
        rule = settings.extra.device_all_reset
        return bool(rule.enabled), int(rule.cooldown_hours or 0)
    except Exception:  # noqa: BLE001 — настройки не прочитались: как у базы по умолчанию
        return True, 0


def _payload(view: dict[str, Any], reset_enabled: bool, cooldown_hours: int) -> dict[str, Any]:
    profiles = []
    for p in view["profiles"]:
        profiles.append(
            {
                "id": p["id"],
                "label": p["label"],
                "status": p["status"],
                "suspend_reason": p["suspend_reason"],
                "expired": p["expired"],
                "expire_at": _iso(p["expire_at"]),
                "url": _link(p["url"]) if p["status"] == "active" else None,
                "device_limit": p["device_limit"],
                "devices": p["devices"],
                "traffic_limit_bytes": p["traffic_limit_bytes"],
                "traffic_used_bytes": p["traffic_used_bytes"],
                "created_at": _iso(p["created_at"]),
                "device_reset_at": _iso(p["device_reset_at"]),
            }
        )
    return {
        "enabled": view["enabled"],
        "available": view["available"],
        "reason": view["reason"],
        "plan_name": view["plan_name"],
        "terms": view["terms"],
        "used": view["used"],
        "profiles": profiles,
        "reset_devices": {"enabled": reset_enabled, "cooldown_hours": cooldown_hours},
    }


@router.get("")
@inject
async def get_family(
    user: CurrentUser,
    session: FromDishka[AsyncSession],
    remnawave: FromDishka[Remnawave],
    settings_dao: FromDishka[SettingsDao],
    light: bool = False,
) -> dict[str, Any]:
    """Семья владельца. `light=1` — без походов в панель (устройства и расход пусты):
    так кабинет решает, показывать ли пункт меню, не дёргая панель на каждом заходе."""
    config = family.load_config()
    view = await family.family_view(
        session, getattr(remnawave, "sdk", None), user.id, config=config, with_panel=not light
    )
    # Выключено и своих профилей нет — не рассказываем ничего: пункта «Семья» у
    # человека быть не должно. Профили уже есть — показываем их и при выключенной
    # функции: свою ссылку и кнопку «Удалить» человек видеть обязан.
    if not config.get("enabled") and not view["profiles"]:
        return {"enabled": False, "available": False, "profiles": []}
    reset_enabled, cooldown = await _reset_rules(settings_dao)
    return _payload(view, reset_enabled, cooldown)


class CreateProfileRequest(BaseModel):
    request_id: uuid_lib.UUID
    label: str = Field(min_length=1, max_length=200)


@router.post("/profiles")
@inject
async def create_family_profile(
    body: CreateProfileRequest,
    user: CurrentUser,
    session: FromDishka[AsyncSession],
    remnawave: FromDishka[Remnawave],
    user_dao: FromDishka[UserDao],
    subscription_dao: FromDishka[SubscriptionDao],
) -> dict[str, Any]:
    sdk = getattr(remnawave, "sdk", None)
    if sdk is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Сервер подписок недоступен, попробуйте позже",
        )
    try:
        result = await family.create_profile(
            session,
            sdk,
            owner_id=user.id,
            label=body.label,
            request_id=body.request_id,
            user_dao=user_dao,
            subscription_dao=subscription_dao,
            actor="cabinet",
        )
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        logger.exception(f"family: профиль не создан user_id={user.id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Не удалось создать профиль. Попробуйте ещё раз.",
        ) from exc
    if result.get("result") == "conflict":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Повторите попытку")
    if result.get("result") == "failed":
        # Панель отказала — теневой аккаунт удалён, строка помечена. Новая попытка —
        # с новым request_id: этот ключ уже означает «не вышло».
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Сервер подписок не создал профиль. Попробуйте позже.",
        )
    return result


async def _owned(session: AsyncSession, owner_id: int, profile_id: int) -> None:
    """Профиль чужой семьи — 404, как несуществующий."""
    p = await family.load_profile(session, profile_id)
    await session.rollback()
    if p is None or p.owner_user_id != owner_id or p.status == "failed":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Профиль не найден")


@router.post("/profiles/{profile_id}/reset-devices")
@inject
async def reset_family_profile_devices(
    profile_id: int,
    user: CurrentUser,
    session: FromDishka[AsyncSession],
    remnawave: FromDishka[Remnawave],
    settings_dao: FromDishka[SettingsDao],
) -> dict[str, Any]:
    await _owned(session, user.id, profile_id)
    reset_enabled, cooldown = await _reset_rules(settings_dao)
    result = await family.reset_profile_devices(
        session,
        getattr(remnawave, "sdk", None),
        owner_id=user.id,
        profile_id=profile_id,
        actor="cabinet",
        reset_enabled=reset_enabled,
        cooldown_hours=cooldown,
    )
    if result.get("result") == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Профиль не найден")
    if result.get("result") == "panel_error":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Не удалось сбросить устройства. Попробуйте позже.",
        )
    if "available_at" in result:
        result["available_at"] = _iso(result["available_at"])
    return result


@router.delete("/profiles/{profile_id}")
@inject
async def delete_family_profile(
    profile_id: int,
    user: CurrentUser,
    session: FromDishka[AsyncSession],
    remnawave: FromDishka[Remnawave],
) -> dict[str, Any]:
    await _owned(session, user.id, profile_id)
    result = await family.delete_profile(
        session,
        getattr(remnawave, "sdk", None),
        owner_id=user.id,
        profile_id=profile_id,
        actor="cabinet",
    )
    if result.get("result") == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Профиль не найден")
    # «pending» — ссылка уже помечена к удалению, панель не ответила: крон повторит.
    return result
