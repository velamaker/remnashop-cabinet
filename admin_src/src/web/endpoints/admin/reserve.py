"""Админ: резервный доступ истёкшим подпискам (1 ГБ на N дней).

Хранится в assets/reserve.json (см. services/overlay_reserve.py). Тумблер + ГБ
резерва + окно (дней) + опц. отдельный сквад-резерв. Выдачу/окончание делает крон
taskiq/tasks/reserve.py (через Remnawave SDK; ядро не трогаем).
"""

from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from src.infrastructure.services.overlay_reserve import load_config, save_config

from ._common import AdminUser

router = APIRouter(prefix="/reserve", tags=["Admin - Reserve"])


class ReserveUpdate(BaseModel):
    enabled: Optional[bool] = None
    reserve_gb: Optional[int] = None
    window_days: Optional[int] = None
    squad_uuid: Optional[str] = None


@router.get("")
async def get_reserve(_admin: AdminUser) -> dict[str, Any]:
    return load_config()


@router.put("")
async def update_reserve(body: ReserveUpdate, _admin: AdminUser) -> dict[str, Any]:
    current = load_config()
    for field in ("enabled", "reserve_gb", "window_days", "squad_uuid"):
        val = getattr(body, field)
        if val is not None:
            current[field] = val
    return save_config(current)
