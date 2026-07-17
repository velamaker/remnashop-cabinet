"""Админ: win-back истёкших («вернись, вот скидка» через N дней после окончания).

Хранится в assets/winback.json (см. services/overlay_winback.py). Тумблер + % скидки +
через сколько дней после окончания + срок жизни промо. Выдачу делает крон
taskiq/tasks/winback.py (ставит users.purchase_discount, база гасит после покупки).
"""

from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from src.infrastructure.services.overlay_winback import load_config, save_config

from ._common import AdminUser

router = APIRouter(prefix="/winback", tags=["Admin - Winback"])


class WinbackUpdate(BaseModel):
    enabled: Optional[bool] = None
    percent: Optional[int] = None
    days_after: Optional[int] = None
    lifetime_hours: Optional[int] = None


@router.get("")
async def get_winback(_admin: AdminUser) -> dict[str, Any]:
    return load_config()


@router.put("")
async def update_winback(body: WinbackUpdate, _admin: AdminUser) -> dict[str, Any]:
    current = load_config()
    for field in ("enabled", "percent", "days_after", "lifetime_hours"):
        val = getattr(body, field)
        if val is not None:
            current[field] = val
    return save_config(current)
