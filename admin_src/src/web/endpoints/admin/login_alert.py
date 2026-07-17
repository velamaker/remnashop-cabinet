"""Админ: алерт пользователю о новом входе в кабинет (новый IP/устройство).

Хранится в assets/login_alert.json (см. services/overlay_login_alert.py). Только тумблер.
Детект и рассылка (Push/TG/Email) — в login-tracking middleware overlay_app при входе.
"""

from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from src.infrastructure.services.overlay_login_alert import load_config, save_config

from ._common import AdminUser

router = APIRouter(prefix="/login-alert", tags=["Admin - Login Alert"])


class LoginAlertUpdate(BaseModel):
    enabled: Optional[bool] = None


@router.get("")
async def get_login_alert(_admin: AdminUser) -> dict[str, Any]:
    return load_config()


@router.put("")
async def update_login_alert(body: LoginAlertUpdate, _admin: AdminUser) -> dict[str, Any]:
    current = load_config()
    if body.enabled is not None:
        current["enabled"] = body.enabled
    return save_config(current)
