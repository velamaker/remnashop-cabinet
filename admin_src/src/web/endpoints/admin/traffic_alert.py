"""Админ: уведомление «трафик заканчивается» (≥N% лимита).

Хранится в assets/traffic_alert.json (см. services/overlay_traffic_alert.py). Тумблер +
порог %. Рассылку делает крон taskiq/tasks/traffic_alert.py (данные из Remnawave).
"""

from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from src.infrastructure.services.overlay_traffic_alert import load_config, save_config

from ._common import AdminUser

router = APIRouter(prefix="/traffic-alert", tags=["Admin - Traffic Alert"])


class TrafficAlertUpdate(BaseModel):
    enabled: Optional[bool] = None
    threshold_percent: Optional[int] = None


@router.get("")
async def get_traffic_alert(_admin: AdminUser) -> dict[str, Any]:
    return load_config()


@router.put("")
async def update_traffic_alert(body: TrafficAlertUpdate, _admin: AdminUser) -> dict[str, Any]:
    current = load_config()
    for field in ("enabled", "threshold_percent"):
        val = getattr(body, field)
        if val is not None:
            current[field] = val
    return save_config(current)
