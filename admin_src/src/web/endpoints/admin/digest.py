"""Админ: месячный дайджест пользователю (трафик за месяц, любимый сервер).

Хранится в assets/digest.json (см. services/overlay_digest.py). Тумблер + день месяца +
час. Рассылку делает крон taskiq/tasks/digest.py (данные из Remnawave bandwidthstats).
"""

from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from src.infrastructure.services.overlay_digest import load_config, save_config

from ._common import AdminUser

router = APIRouter(prefix="/digest", tags=["Admin - Digest"])


class DigestUpdate(BaseModel):
    enabled: Optional[bool] = None
    day_of_month: Optional[int] = None
    hour: Optional[int] = None


@router.get("")
async def get_digest(_admin: AdminUser) -> dict[str, Any]:
    return load_config()


@router.put("")
async def update_digest(body: DigestUpdate, _admin: AdminUser) -> dict[str, Any]:
    current = load_config()
    for field in ("enabled", "day_of_month", "hour"):
        val = getattr(body, field)
        if val is not None:
            current[field] = val
    return save_config(current)
