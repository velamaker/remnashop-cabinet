"""Управление гранулярными правами админов (таблица admin_grants).

Только OWNER может смотреть/менять гранты. Раздел пути `/grants` не привязан к
секции (section_for_path=None) → в точке контроля требует полного доступа; сверх
этого тут явная проверка OWNER, чтобы full_access-админ (не владелец) не мог
раздавать права и эскалироваться.
"""

from datetime import datetime, timezone
from typing import Any, Optional

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.dao import UserDao
from src.core.enums import Role
from src.web.permissions import (
    compute_access,
    normalize_sections,
    presets_catalog,
    sections_catalog,
)
from src.web.permissions_dao import delete_grant, load_grant, upsert_grant

from ._common import AdminUser

router = APIRouter(prefix="/grants", tags=["Admin - Grants"])


def _require_owner(admin) -> None:
    if admin.role < Role.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Только владелец может управлять правами",
        )


@router.get("/catalog")
@inject
async def get_catalog(_admin: AdminUser) -> dict[str, Any]:
    """Справочник разделов и пресетов для редактора прав."""
    return {"sections": sections_catalog(), "presets": presets_catalog()}


def _grant_public(grant: Optional[dict[str, Any]], role: int) -> dict[str, Any]:
    access = compute_access(role, grant)
    has_grant = grant is not None
    exp = grant.get("expires_at") if grant else None
    return {
        "has_grant": has_grant,
        "full_access": bool(grant["full_access"]) if has_grant else False,
        "can_write": bool(grant["can_write"]) if has_grant else True,
        "sections": list(grant["sections"]) if has_grant else [],
        "expires_at": exp.isoformat() if hasattr(exp, "isoformat") else exp,
        "granted_by": grant.get("granted_by") if has_grant else None,
        # Что реально действует сейчас (с учётом enum и срока).
        "effective": {
            "allowed": access["allowed"],
            "full_access": access["full_access"],
            "can_write": access["can_write"],
            "sections": access["sections"],
            "source": access["source"],
        },
    }


@router.get("/{user_id}")
@inject
async def get_user_grant(
    user_id: int,
    admin: AdminUser,
    user_dao: FromDishka[UserDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    _require_owner(admin)
    user = await user_dao.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
    role_value = getattr(user.role, "value", user.role)
    grant = await load_grant(session, user_id)
    return {"user_id": user_id, "role": role_value, **_grant_public(grant, role_value)}


class GrantRequest(BaseModel):
    full_access: bool = False
    can_write: bool = True
    sections: list[str] = []
    expires_at: Optional[str] = None  # ISO-8601 или null (бессрочно)


@router.put("/{user_id}")
@inject
async def set_user_grant(
    user_id: int,
    body: GrantRequest,
    admin: AdminUser,
    user_dao: FromDishka[UserDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    _require_owner(admin)
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Нельзя менять свои права"
        )
    user = await user_dao.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
    role_value = getattr(user.role, "value", user.role)
    if role_value >= Role.OWNER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="У владельца и так полный доступ",
        )

    # Разбор срока действия.
    exp: Optional[datetime] = None
    if body.expires_at:
        try:
            exp = datetime.fromisoformat(body.expires_at.replace("Z", "+00:00"))
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Неверный формат срока (ожидается ISO-8601)",
            )

    sections = [] if body.full_access else normalize_sections(body.sections)
    granted_by = (
        f"@{admin.username}" if getattr(admin, "username", None)
        else getattr(admin, "email", None) or f"id:{admin.id}"
    )

    await upsert_grant(
        session,
        user_id,
        full_access=body.full_access,
        can_write=body.can_write,
        sections=sections,
        expires_at=exp,
        granted_by=granted_by,
    )
    await session.commit()

    grant = await load_grant(session, user_id)
    return {"success": True, "user_id": user_id, "role": role_value, **_grant_public(grant, role_value)}


@router.delete("/{user_id}")
@inject
async def remove_user_grant(
    user_id: int,
    admin: AdminUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    _require_owner(admin)
    await delete_grant(session, user_id)
    await session.commit()
    return {"success": True, "user_id": user_id}
