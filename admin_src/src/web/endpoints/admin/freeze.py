"""Админ: заморозка (пауза) подписки — тумблер + макс. длительность.

Хранится в assets/freeze.json (см. services/overlay_freeze.py). Юзер сам ставит
подписку на паузу в кабинете (public/freeze.py). Крон авто-возобновляет по max_days.
"""

from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from src.infrastructure.services.overlay_freeze import load_config, save_config

from ._common import AdminUser

router = APIRouter(prefix="/freeze", tags=["Admin - Freeze"])


class FreezeUpdate(BaseModel):
    enabled: Optional[bool] = None
    max_days: Optional[int] = None


@router.get("")
async def get_freeze(_admin: AdminUser) -> dict[str, Any]:
    return load_config()


@router.put("")
async def update_freeze(body: FreezeUpdate, _admin: AdminUser) -> dict[str, Any]:
    current = load_config()
    for field in ("enabled", "max_days"):
        val = getattr(body, field)
        if val is not None:
            current[field] = val
    return save_config(current)
