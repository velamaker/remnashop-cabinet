"""Админ: уведомление «новое устройство подключилось».

Хранится в assets/new_device.json (см. services/overlay_new_device.py). Тумблер.
Детект/рассылку делает крон taskiq/tasks/new_device.py (снимок HWID из Remnawave).
"""

from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from src.infrastructure.services.overlay_new_device import load_config, save_config

from ._common import AdminUser

router = APIRouter(prefix="/new-device", tags=["Admin - New Device"])


class NewDeviceUpdate(BaseModel):
    enabled: Optional[bool] = None


@router.get("")
async def get_new_device(_admin: AdminUser) -> dict[str, Any]:
    return load_config()


@router.put("")
async def update_new_device(body: NewDeviceUpdate, _admin: AdminUser) -> dict[str, Any]:
    current = load_config()
    if body.enabled is not None:
        current["enabled"] = body.enabled
    return save_config(current)
