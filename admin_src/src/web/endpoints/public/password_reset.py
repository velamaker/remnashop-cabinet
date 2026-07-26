"""Сброс пароля по email для НЕвошедших пользователей («Забыл пароль»).

Базовый код умеет менять пароль только залогиненному (/auth/change-password) и
подтверждать email только залогиненному — для сброса «снаружи» эндпоинта нет.
Здесь добавляем два публичных шага, переиспользуя ту же механику кодов, что и
верификация email: код хранится НА юзере (`email_verification_code_hash` +
`email_verification_expires_at`, хэш через crypt_key), письмо шлёт EmailSender.

  POST /auth/password/reset/request  {email}                 → шлём код (если есть
       аккаунт с подтверждённым email). Ответ ВСЕГДА success — не раскрываем,
       зарегистрирован ли email (защита от перебора). Рейт-лимит — на nginx.
  POST /auth/password/reset/confirm  {email, code, password} → проверяем код и
       срок, ставим новый пароль, сбрасываем код.

Сброс доступен только для аккаунтов с ПОДТВЕРЖДЁННЫМ email (у Telegram-юзеров без
почты сбрасывать нечего — они и так входят через Telegram).
"""

import hmac
from datetime import timedelta

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.dao import UserDao
from src.application.common.dao.auth import AuthSessionDao
from src.application.common.email_sender import EmailSender
from src.application.common.password_hasher import PasswordHasher
from src.application.common.uow import UnitOfWork
from src.core.config import AppConfig
from src.infrastructure.services.overlay_sessions import invalidate_all

router = APIRouter(prefix="/auth/password/reset", tags=["Public - Auth"])


class ResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class ResetConfirm(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    code: str = Field(min_length=4, max_length=8)
    password: str = Field(min_length=8, max_length=256)


class ResetResponse(BaseModel):
    success: bool


@router.post("/request", response_model=ResetResponse)
@inject
async def reset_request(
    body: ResetRequest,
    user_dao: FromDishka[UserDao],
    config: FromDishka[AppConfig],
    email_sender: FromDishka[EmailSender],
    uow: FromDishka[UnitOfWork],
) -> ResetResponse:
    # Базовые внутренности импортируем лениво (устойчивость overlay к смене базы).
    from src.application.use_cases.auth._codes import (
        generate_email_verification_code,
        hash_email_verification_code,
    )
    from src.core.constants import (
        EMAIL_VERIFICATION_BODY_TEMPLATE,
        EMAIL_VERIFICATION_SUBJECT,
    )
    from src.core.utils.time import datetime_now

    if not email_sender.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Сброс по email недоступен — почта не настроена.",
        )

    email = body.email.strip().lower()
    user = await user_dao.get_by_email(email)
    # Не раскрываем существование аккаунта: при любом исходе отвечаем success.
    # Сброс возможен только для подтверждённого email.
    if not user or not user.email or not user.is_email_verified:
        return ResetResponse(success=True)

    ttl = config.email.verification_code_ttl_minutes
    code = generate_email_verification_code()

    # Сначала отправляем письмо; код/срок сохраняем только при успехе доставки.
    await email_sender.send(
        to=user.email,
        subject=EMAIL_VERIFICATION_SUBJECT,
        body=EMAIL_VERIFICATION_BODY_TEMPLATE.format(code=code, minutes=ttl),
    )

    user.email_verification_code_hash = hash_email_verification_code(
        code, config.crypt_key.get_secret_value()
    )
    user.email_verification_expires_at = datetime_now() + timedelta(minutes=ttl)

    async with uow:
        await user_dao.update(user)
        await uow.commit()

    return ResetResponse(success=True)


@router.post("/confirm", response_model=ResetResponse)
@inject
async def reset_confirm(
    body: ResetConfirm,
    user_dao: FromDishka[UserDao],
    config: FromDishka[AppConfig],
    password_hasher: FromDishka[PasswordHasher],
    uow: FromDishka[UnitOfWork],
    session: FromDishka[AsyncSession],
    auth_session: FromDishka[AuthSessionDao],
) -> ResetResponse:
    from src.application.use_cases.auth._codes import hash_email_verification_code
    from src.core.utils.time import datetime_now

    bad = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный email или код"
    )

    email = body.email.strip().lower()
    user = await user_dao.get_by_email(email)
    if (
        not user
        or not user.email_verification_code_hash
        or not user.email_verification_expires_at
    ):
        raise bad
    if user.email_verification_expires_at < datetime_now():
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Код истёк — запросите новый",
        )

    incoming = hash_email_verification_code(body.code, config.crypt_key.get_secret_value())
    if not hmac.compare_digest(incoming, user.email_verification_code_hash):
        raise bad

    user.password_hash = password_hasher.hash(body.password)
    user.email_verification_code_hash = None
    user.email_verification_expires_at = None

    async with uow:
        updated = await user_dao.update(user)
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Не удалось сохранить новый пароль",
            )
        await uow.commit()

    # Сброс пароля = компрометация → отзываем ВСЕ существующие сессии пользователя:
    #   • access-токены (по времени, проверяет middleware overlay_app);
    #   • серверные refresh-токены (Redis) — иначе старая сессия выпустит новый access.
    # Иначе тот, кто держал сессию до сброса, сохранил бы доступ к аккаунту.
    try:
        await invalidate_all(session, user.id)
        await auth_session.revoke_all_user_tokens(user.id)
    except Exception:  # noqa: BLE001 — пароль уже сменён; отзыв не должен ронять ответ
        pass

    return ResetResponse(success=True)
