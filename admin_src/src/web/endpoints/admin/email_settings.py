"""Админ: настройки отправки почты (SMTP-провайдер / Brevo) — правятся из кабинета.

Хранится в assets/email.json (см. email_settings.py), читается при каждой отправке.
Пароль и Brevo-ключ наружу не отдаём (только признак «задан»); при сохранении
пустое значение этих полей трактуем как «не менять».
"""

from typing import Any, Optional

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from src.core.config import AppConfig
from src.core.constants import EMAIL_VERIFICATION_SUBJECT
from src.infrastructure.services.email_sender import SmtpEmailSender
from src.infrastructure.services.email_settings import (
    PRESETS,
    explain_send_error,
    load_email_settings,
    save_email_settings,
)

from ._common import AdminUser

router = APIRouter(prefix="/email-settings", tags=["Admin - Email"])


class EmailSettingsUpdate(BaseModel):
    enabled: Optional[bool] = None
    provider: Optional[str] = None  # gmail | yandex | mailru | brevo | custom
    host: Optional[str] = None
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    use_tls: Optional[bool] = None
    use_ssl: Optional[bool] = None
    username: Optional[str] = None
    password: Optional[str] = None
    from_email: Optional[str] = None
    from_name: Optional[str] = None
    brevo_api_key: Optional[str] = None


class TestEmailRequest(BaseModel):
    to: str = Field(max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _public_view(config: AppConfig) -> dict[str, Any]:
    s = load_email_settings(config)
    sender = SmtpEmailSender(config)
    return {
        "enabled": s["enabled"],
        "provider": s["provider"],
        "host": s["host"],
        "port": s["port"],
        "use_tls": s["use_tls"],
        "use_ssl": s["use_ssl"],
        "username": s["username"],
        "from_email": s["from_email"],
        "from_name": s["from_name"],
        # Секреты не раскрываем — только признак, что заданы.
        "has_password": bool(s["password"]),
        "has_brevo_key": bool(s["brevo_api_key"]),
        "is_enabled": sender.is_enabled,
        "presets": PRESETS,
    }


@router.get("")
@inject
async def get_email_settings(_admin: AdminUser, config: FromDishka[AppConfig]) -> dict[str, Any]:
    return _public_view(config)


@router.put("")
@inject
async def update_email_settings(
    body: EmailSettingsUpdate,
    _admin: AdminUser,
    config: FromDishka[AppConfig],
) -> dict[str, Any]:
    if body.provider is not None and body.provider not in (*PRESETS, "brevo", "custom"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Неизвестный провайдер",
        )

    values = body.model_dump(exclude_none=True)
    # Пустой пароль / brevo-ключ в форме = «оставить как было» (не затирать секрет).
    for secret in ("password", "brevo_api_key"):
        if secret in values and values[secret].strip() == "":
            del values[secret]
    save_email_settings(values)
    return _public_view(config)


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
            detail="Почта не настроена (провайдер/логин/пароль или Brevo-ключ).",
        )
    try:
        await sender.send(
            to=body.to,
            subject=EMAIL_VERIFICATION_SUBJECT,
            body="Your verification code is 123456. Code is valid for 15 minutes.",
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Не удалось отправить тестовое письмо: {explain_send_error(exc, load_email_settings(config).get('provider'))}",
        )
    return {"success": True, "to": body.to}
