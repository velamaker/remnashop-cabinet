"""Админ: промо-баннер в кабинете (заголовок/текст/кнопка/цвет/аудитория/период).

Хранится в assets/promo_banner.json (см. services/overlay_promo_banner.py). Кабинет
показывает баннер подходящим юзерам через public /promo-banner. Цену не трогает.
"""

from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from src.infrastructure.services.overlay_promo_banner import load_config, save_config

from ._common import AdminUser

router = APIRouter(prefix="/promo-banner", tags=["Admin - Promo Banner"])


class PromoBannerUpdate(BaseModel):
    enabled: Optional[bool] = None
    title: Optional[str] = None
    text: Optional[str] = None
    cta_text: Optional[str] = None
    cta_url: Optional[str] = None
    color: Optional[str] = None
    audience: Optional[str] = None
    dismissible: Optional[bool] = None
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None


@router.get("")
async def get_promo_banner(_admin: AdminUser) -> dict[str, Any]:
    return load_config()


@router.put("")
async def update_promo_banner(body: PromoBannerUpdate, _admin: AdminUser) -> dict[str, Any]:
    current = load_config()
    for field in (
        "enabled", "title", "text", "cta_text", "cta_url",
        "color", "audience", "dismissible", "starts_at", "ends_at",
    ):
        val = getattr(body, field)
        if val is not None:
            current[field] = val
    return save_config(current)
