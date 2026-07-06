"""Админ: ступенчатый кэшбэк баллами покупателю — правится из кабинета.

Хранится в assets/cashback.json (см. services/overlay_cashback.py). Тумблер +
курс (1 балл = N ₽) + ступени [{min_days, percent}]. Начисление вешается на
успешную оплату через overlay AssignReferralRewards (ядро не трогаем). Только RUB.
"""

from typing import Any, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from src.infrastructure.services.overlay_cashback import load_config, save_config

from ._common import AdminUser

router = APIRouter(prefix="/cashback", tags=["Admin - Cashback"])


class CashbackTier(BaseModel):
    min_days: int
    percent: int


class CashbackUpdate(BaseModel):
    enabled: Optional[bool] = None
    point_value_rub: Optional[int] = None
    tiers: Optional[List[CashbackTier]] = None


@router.get("")
async def get_cashback(_admin: AdminUser) -> dict[str, Any]:
    return load_config()


@router.put("")
async def update_cashback(body: CashbackUpdate, _admin: AdminUser) -> dict[str, Any]:
    current = load_config()
    if body.enabled is not None:
        current["enabled"] = body.enabled
    if body.point_value_rub is not None:
        current["point_value_rub"] = body.point_value_rub
    if body.tiers is not None:
        current["tiers"] = [t.model_dump() for t in body.tiers]
    return save_config(current)
