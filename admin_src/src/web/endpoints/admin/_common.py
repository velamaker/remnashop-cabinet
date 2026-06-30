from typing import Annotated

import jwt
from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import Depends, HTTPException, Request, status

from src.application.common.dao import UserDao
from src.application.dto import UserDto
from src.core.config import AppConfig
from src.core.enums import Role
from src.web.endpoints.public._common import decode_access_token


@inject
async def _get_admin_user(
    request: Request,
    user_dao: FromDishka[UserDao] = None,  # type: ignore[assignment]
    config: FromDishka[AppConfig] = None,  # type: ignore[assignment]
) -> UserDto:
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        if config.jwt_secret is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="JWT secret not configured",
            )
        user_id = decode_access_token(token, config.jwt_secret.get_secret_value())
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        )
    user = await user_dao.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if user.is_blocked:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is blocked")
    # PREVIEW(2) — «админ только для просмотра»: видит всю админку, но ничего не
    # меняет. Полные админы — ADMIN(3) и выше. Всё, что ниже PREVIEW (обычный
    # USER) — в админку не пускаем вовсе.
    if user.role < Role.PREVIEW:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    # Read-only роль: разрешаем только безопасные методы; любой изменяющий запрос
    # (создать/обновить/удалить) отклоняем ещё до попадания в обработчик. Так
    # запрет действует на ВСЕ админ-эндпоинты сразу — единая точка контроля.
    if user.role < Role.ADMIN and request.method not in ("GET", "HEAD", "OPTIONS"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Read-only admin: изменения недоступны",
        )
    # Кто действует — для аудит-лога (читается мидлварью после ответа).
    request.state.audit_actor = (
        f"@{user.username}" if getattr(user, "username", None)
        else getattr(user, "email", None) or f"id:{user.id}"
    )
    return user


AdminUser = Annotated[UserDto, Depends(_get_admin_user)]
