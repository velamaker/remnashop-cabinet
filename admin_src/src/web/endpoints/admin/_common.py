from typing import Annotated

import jwt
from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.dao import UserDao
from src.application.dto import UserDto
from src.core.config import AppConfig
from src.web.endpoints.public._common import decode_access_token
from src.web.permissions import access_permits, compute_access
from src.web.permissions_dao import load_grant

from ._redact import set_request_readonly


@inject
async def _get_admin_user(
    request: Request,
    user_dao: FromDishka[UserDao] = None,  # type: ignore[assignment]
    config: FromDishka[AppConfig] = None,  # type: ignore[assignment]
    session: FromDishka[AsyncSession] = None,  # type: ignore[assignment]
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

    # Единая точка контроля. Права считаем из enum-роли + гранта (таблица
    # admin_grants). Приоритет: OWNER+ = полный доступ; иначе действующий грант
    # (набор разделов + can_write + срок); иначе legacy-enum (ADMIN+ = полный,
    # PREVIEW = только просмотр). См. src/web/permissions.py.
    grant = None
    try:
        grant = await load_grant(session, user.id)
    except Exception:
        grant = None  # нет таблицы/ошибка → падаем на enum, админку не роняем
    access = compute_access(user.role, grant)

    denial = access_permits(access, request.url.path, request.method)
    if denial is not None:
        code = (
            status.HTTP_403_FORBIDDEN
            if access.get("allowed")
            else status.HTTP_403_FORBIDDEN
        )
        raise HTTPException(status_code=code, detail=denial)

    # Эффективные права — на request.state, чтобы эндпоинты могли брать готовое
    # (без повторной загрузки гранта). Плюс решение read-only в contextvar для
    # маскировки данных (_redact) — read-only может быть и от гранта, не только PREVIEW.
    request.state.admin_access = access
    set_request_readonly(not access.get("can_write"))
    # Кто действует — для аудит-лога (читается мидлварью после ответа).
    request.state.audit_actor = (
        f"@{user.username}" if getattr(user, "username", None)
        else getattr(user, "email", None) or f"id:{user.id}"
    )
    return user


AdminUser = Annotated[UserDto, Depends(_get_admin_user)]
