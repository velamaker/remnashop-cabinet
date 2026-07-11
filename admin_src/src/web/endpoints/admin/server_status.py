"""Админ: настройка блока «Статус сервиса» в кабинете.

Хранится в assets/server_status.json (см. services/overlay_server_status.py):
тумблер показа + привязка по подписке + видимость для невошедших.
"""

from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from src.infrastructure.services.overlay_server_status import load_config, save_config

from ._common import AdminUser

router = APIRouter(prefix="/server-status", tags=["Admin - Server status"])


class ServerStatusUpdate(BaseModel):
    enabled: Optional[bool] = None
    bind_to_subscription: Optional[bool] = None
    guest_visible: Optional[bool] = None


@router.get("")
async def get_server_status(_admin: AdminUser) -> dict[str, Any]:
    return load_config()


@router.put("")
async def update_server_status(body: ServerStatusUpdate, _admin: AdminUser) -> dict[str, Any]:
    current = load_config()
    current.update(body.model_dump(exclude_none=True))
    return save_config(current)
