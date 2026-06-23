"""
Лёгкий эндпоинт, отдающий роль текущего пользователя.

Базовый /auth/me (MeResponse) роль не возвращает, из-за чего кабинет не мог
отличить админа от обычного пользователя. Чтобы не перезаписывать (и не
«замораживать») весь auth.py, добавляем отдельный маленький роут.
"""

from fastapi import APIRouter

from src.web.endpoints.public._common import CurrentUser

router = APIRouter(prefix="/auth", tags=["Public - Auth"])


@router.get("/whoami")
async def whoami(user: CurrentUser) -> dict:
    role_value = getattr(user.role, "value", user.role)
    return {
        "role": role_value,
        # ADMIN(3)/DEV(4)/OWNER(5)/SYSTEM(6) — административные роли.
        "is_admin": bool(role_value is not None and role_value >= 3),
        # Нужно фронту, чтобы показать «задать пароль» (резервный вход по email).
        "has_password": bool(getattr(user, "password_hash", None)),
    }
