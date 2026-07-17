"""Админ: скидка на первую покупку триальщикам за N дней до конца триала.

Хранится в assets/trial_discount.json (см. services/overlay_trial_discount.py).
Тумблер + % скидки + за сколько дней до конца триала + срок жизни промо.
Выдачу делает крон taskiq/tasks/trial_discount.py (ядро не трогаем — только
проставляем users.purchase_discount, база гасит её после первой покупки).
"""

from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from src.infrastructure.services.overlay_trial_discount import load_config, save_config

from ._common import AdminUser

router = APIRouter(prefix="/trial-discount", tags=["Admin - Trial Discount"])


class TrialDiscountUpdate(BaseModel):
    enabled: Optional[bool] = None
    percent: Optional[int] = None
    days_before: Optional[int] = None
    lifetime_hours: Optional[int] = None


@router.get("")
async def get_trial_discount(_admin: AdminUser) -> dict[str, Any]:
    return load_config()


@router.put("")
async def update_trial_discount(body: TrialDiscountUpdate, _admin: AdminUser) -> dict[str, Any]:
    current = load_config()
    for field in ("enabled", "percent", "days_before", "lifetime_hours"):
        val = getattr(body, field)
        if val is not None:
            current[field] = val
    return save_config(current)
