"""
Установка ПЕРВОГО пароля для пользователей без него (например, авторизовавшихся
через Telegram). Нужно для резервного доступа по email на случай блокировки
Telegram: TG-юзер добавляет и подтверждает email, затем задаёт пароль — после
чего может входить связкой email + пароль (LoginEmailUser auth_type не проверяет).

Менять существующий пароль здесь нельзя — для этого есть /auth/change-password,
требующий текущий пароль. Эндпоинт срабатывает только когда password_hash пуст.
"""

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from src.application.common.dao import UserDao
from src.application.common.password_hasher import PasswordHasher
from src.application.common.uow import UnitOfWork

from ._common import CurrentUser

router = APIRouter(prefix="/auth", tags=["Public - Auth"])


class SetPasswordRequest(BaseModel):
    password: str = Field(min_length=8, max_length=256)


class SetPasswordResponse(BaseModel):
    success: bool
    has_password: bool


@router.post("/password/set", response_model=SetPasswordResponse)
@inject
async def set_initial_password(
    body: SetPasswordRequest,
    user: CurrentUser,
    password_hasher: FromDishka[PasswordHasher],
    user_dao: FromDishka[UserDao],
    uow: FromDishka[UnitOfWork],
) -> SetPasswordResponse:
    if user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Пароль уже установлен. Используйте смену пароля.",
        )

    user.password_hash = password_hasher.hash(body.password)

    async with uow:
        updated = await user_dao.update(user)
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Пользователь не найден",
            )
        await uow.commit()

    return SetPasswordResponse(success=True, has_password=True)
