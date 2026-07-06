"""Админ: утренняя сводка владельцу в Telegram — правится из кабинета.

Хранится в assets/morning_summary.json (см. services/overlay_morning_summary.py).
Тумблер + час отправки (0-23, локальное время сервера) + окно «истекают в N дней».
Рассылку шлёт taskiq-крон tasks/morning_summary.py (владельцу).
"""

from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from src.infrastructure.services.overlay_morning_summary import load_config, save_config

from ._common import AdminUser

router = APIRouter(prefix="/morning-summary", tags=["Admin - Morning summary"])


class MorningSummaryUpdate(BaseModel):
    enabled: Optional[bool] = None
    hour: Optional[int] = None
    expiring_days: Optional[int] = None


@router.get("")
async def get_morning_summary(_admin: AdminUser) -> dict[str, Any]:
    return load_config()


@router.put("")
async def update_morning_summary(body: MorningSummaryUpdate, _admin: AdminUser) -> dict[str, Any]:
    current = load_config()
    current.update(body.model_dump(exclude_none=True))
    return save_config(current)
