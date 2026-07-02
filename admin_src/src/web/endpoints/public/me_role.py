"""
Лёгкий эндпоинт, отдающий роль и эффективные права текущего пользователя.

Базовый /auth/me (MeResponse) роль не возвращает, из-за чего кабинет не мог
отличить админа от обычного пользователя. Чтобы не перезаписывать (и не
«замораживать») весь auth.py, добавляем отдельный маленький роут. Здесь же
считаем гранулярные права (разделы/полный доступ/запись) — фронт по ним прячет
недоступные пункты меню.
"""

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession

from src.web.endpoints.public._common import CurrentUser
from src.web.permissions import compute_access
from src.web.permissions_dao import load_grant

router = APIRouter(prefix="/auth", tags=["Public - Auth"])


@router.get("/whoami")
@inject
async def whoami(
    user: CurrentUser,
    session: FromDishka[AsyncSession],
) -> dict:
    role_value = getattr(user.role, "value", user.role)

    grant = None
    try:
        grant = await load_grant(session, user.id)
    except Exception:
        grant = None
    access = compute_access(role_value or 0, grant)

    return {
        "role": role_value,
        # Полноценный админ = есть доступ И право менять (грант с can_write или enum ADMIN+).
        "is_admin": bool(access["allowed"] and access["can_write"]),
        # «Только просмотр»: доступ есть, но менять нельзя (PREVIEW или грант read-only).
        "is_readonly_admin": bool(access["allowed"] and not access["can_write"]),
        # Доступ к админ-разделу вообще (полный или read-only).
        "can_access_admin": bool(access["allowed"]),
        # OWNER(5)/SYSTEM(6) — только они меняют роли/гранты пользователей.
        "is_owner": bool(access["is_owner"]),
        # Гранулярные права для скрытия пунктов меню на фронте.
        "full_access": bool(access["full_access"]),
        "can_write": bool(access["can_write"]),
        "sections": list(access["sections"]),
        "grant_expires_at": access["expires_at"],
        # Нужно фронту, чтобы показать «задать пароль» (резервный вход по email).
        "has_password": bool(getattr(user, "password_hash", None)),
    }
