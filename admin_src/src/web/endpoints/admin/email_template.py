"""Админ: редактирование текста письма с кодом подтверждения + тест-отправка."""

from typing import Any, Optional

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from src.core.config import AppConfig
from src.core.constants import EMAIL_VERIFICATION_SUBJECT
from src.infrastructure.services.email_sender import SmtpEmailSender
from src.infrastructure.services.email_template_config import (
    load_email_template,
    save_email_template,
)

from ._common import AdminUser

router = APIRouter(prefix="/email-template", tags=["Admin - Email"])


class EmailTemplateUpdate(BaseModel):
    subject: Optional[str] = None
    heading: Optional[str] = None
    intro: Optional[str] = None
    expire_note: Optional[str] = None
    ignore_note: Optional[str] = None


class TestEmailRequest(BaseModel):
    to: str = Field(max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@router.get("")
async def get_email_template(_admin: AdminUser) -> dict[str, Any]:
    return load_email_template()


@router.put("")
async def update_email_template(body: EmailTemplateUpdate, _admin: AdminUser) -> dict[str, Any]:
    return save_email_template(body.model_dump(exclude_none=True))


@router.post("/test")
@inject
async def send_test_email(
    body: TestEmailRequest,
    _admin: AdminUser,
    config: FromDishka[AppConfig],
) -> dict[str, Any]:
    sender = SmtpEmailSender(config)
    if not sender.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email не настроен (EMAIL_ENABLED / Brevo или SMTP).",
        )
    # subject=EMAIL_VERIFICATION_SUBJECT → sender отрендерит как письмо с кодом;
    # тело содержит тестовый код 123456 и срок 15 минут (их вытащит парсер).
    try:
        await sender.send(
            to=body.to,
            subject=EMAIL_VERIFICATION_SUBJECT,
            body="Your verification code is 123456. Code is valid for 15 minutes.",
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Не удалось отправить тестовое письмо: {exc}",
        )
    return {"success": True, "to": body.to}
