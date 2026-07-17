"""Админ: обязательная верификация email перед триалом/покупкой (тумблер).

Хранится в assets/email_gate.json (см. services/overlay_email_gate.py). Гейт в
public/subscription.py (`_assert_web_purchase_email_verified`) читает этот тумблер.
Дефолт ВКЛ. Telegram/OAuth-юзеров гейт не касается.
"""

from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from src.infrastructure.services.overlay_email_gate import load_config, save_config

from ._common import AdminUser

router = APIRouter(prefix="/email-gate", tags=["Admin - Email Gate"])


class EmailGateUpdate(BaseModel):
    enabled: Optional[bool] = None


@router.get("")
async def get_email_gate(_admin: AdminUser) -> dict[str, Any]:
    return load_config()


@router.put("")
async def update_email_gate(body: EmailGateUpdate, _admin: AdminUser) -> dict[str, Any]:
    current = load_config()
    if body.enabled is not None:
        current["enabled"] = body.enabled
    return save_config(current)
