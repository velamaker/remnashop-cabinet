"""Админ: пополнение ₽-баланса через шлюзы + бонус — правится из кабинета.

Хранится в assets/topup.json (см. services/overlay_topup.py). Тумблер + бонус% +
лимиты min/max + пресеты сумм. Зачисление на вебхуке через overlay ProcessPayment.
"""

from typing import Any, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from src.infrastructure.services.overlay_topup import load_config, save_config

from ._common import AdminUser

router = APIRouter(prefix="/topup", tags=["Admin - Topup"])


class TopupUpdate(BaseModel):
    enabled: Optional[bool] = None
    bonus_percent: Optional[int] = None
    min_amount: Optional[int] = None
    max_amount: Optional[int] = None
    presets: Optional[List[int]] = None


@router.get("")
async def get_topup(_admin: AdminUser) -> dict[str, Any]:
    return load_config()


@router.put("")
async def update_topup(body: TopupUpdate, _admin: AdminUser) -> dict[str, Any]:
    current = load_config()
    values = body.model_dump(exclude_none=True)
    current.update(values)
    return save_config(current)
