"""Управление привязанной почтой из кабинета: удаление email.

Смена почты делается существующим флоу верификации (request-verification на
новый адрес → confirm), отдельный эндпоинт не нужен. Здесь — только удаление.

Удалять email разрешено ТОЛЬКО если у пользователя есть Telegram (другой способ
входа), иначе он заблокирует себе доступ. Очищаем email, флаг подтверждения и
пароль (он был нужен только для входа по email).
"""

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from src.application.common.dao import UserDao
from src.application.common.uow import UnitOfWork

from ._common import CurrentUser

router = APIRouter(prefix="/auth", tags=["Public - Auth"])


class DeleteEmailResponse(BaseModel):
    success: bool


@router.delete("/email", response_model=DeleteEmailResponse)
@inject
async def delete_email(
    user: CurrentUser,
    user_dao: FromDishka[UserDao],
    uow: FromDishka[UnitOfWork],
) -> DeleteEmailResponse:
    if not user.telegram_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email — единственный способ входа. Сначала привяжите Telegram, "
            "затем можно удалить почту.",
        )
    if not user.email:
        return DeleteEmailResponse(success=True)

    user.email = None
    user.is_email_verified = False
    user.password_hash = None  # пароль использовался только для входа по email

    async with uow:
        updated = await user_dao.update(user)
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Пользователь не найден",
            )
        await uow.commit()

    return DeleteEmailResponse(success=True)
