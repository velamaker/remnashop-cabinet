"""Админ: настройки входа через Telegram (OIDC) — правятся из кабинета.

Хранится в assets/auth.json (см. auth_settings.py). Позволяет включить вход и
привязку через Telegram без переустановки: вписать Client ID / Secret из
@BotFather → Web Login прямо в админке. Секрет наружу не отдаём (только признак
«задан»); пустой секрет при сохранении = «не менять».
"""

import os
from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from src.infrastructure.services.auth_settings import (
    load_auth_settings,
    save_auth_settings,
    telegram_oidc_enabled,
)

from ._common import AdminUser

router = APIRouter(prefix="/auth-settings", tags=["Admin - Auth"])


class AuthSettingsUpdate(BaseModel):
    telegram_oidc_enabled: Optional[bool] = None
    telegram_oidc_client_id: Optional[str] = None
    telegram_oidc_client_secret: Optional[str] = None


def _redirect_uri() -> str:
    base = (os.environ.get("WEB_CABINET_URL") or "").strip().rstrip("/")
    return f"{base}/api/auth/telegram/oidc/callback" if base else ""


def _public_view() -> dict[str, Any]:
    s = load_auth_settings()
    return {
        "telegram_oidc_client_id": s["telegram_oidc_client_id"],
        # Секрет не раскрываем — только признак, что задан.
        "has_secret": bool(s["telegram_oidc_client_secret"]),
        # Сохранённый тумблер: None (авто) | true | false.
        "telegram_oidc_enabled_setting": s["telegram_oidc_enabled"],
        # Эффективно ли OIDC сейчас включён (тумблер + наличие кредов).
        "telegram_oidc_active": telegram_oidc_enabled(),
        # Готовый Redirect URI — добавить разово в @BotFather → Web Login.
        "redirect_uri": _redirect_uri(),
    }


@router.get("")
async def get_auth_settings(_admin: AdminUser) -> dict[str, Any]:
    return _public_view()


@router.put("")
async def update_auth_settings(body: AuthSettingsUpdate, _admin: AdminUser) -> dict[str, Any]:
    values = body.model_dump(exclude_none=True)
    # Пустой секрет в форме = «оставить как было» (не затирать сохранённый).
    if "telegram_oidc_client_secret" in values and values["telegram_oidc_client_secret"].strip() == "":
        del values["telegram_oidc_client_secret"]
    save_auth_settings(values)
    return _public_view()
