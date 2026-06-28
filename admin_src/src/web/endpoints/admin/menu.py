"""Админ: состав кнопок главного меню бота (редактируется из кабинета).

Хранилище и дефолты — в telegram/routers/menu/menu_config.py (тот же файл читает
геттер меню). Меню применяет изменения сразу, без перезапуска бота.
"""

from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from src.telegram.routers.menu.menu_config import load_menu_config, save_menu_config

from ._common import AdminUser

router = APIRouter(prefix="/menu", tags=["Admin - Menu"])


class MenuConfigUpdate(BaseModel):
    cabinet_miniapp: Optional[bool] = None
    cabinet_url: Optional[bool] = None
    connect_miniapp: Optional[bool] = None
    connect_url: Optional[bool] = None
    remna_sub: Optional[bool] = None


@router.get("")
async def get_menu(_admin: AdminUser) -> dict[str, Any]:
    return load_menu_config()


@router.put("")
async def update_menu(body: MenuConfigUpdate, _admin: AdminUser) -> dict[str, Any]:
    return save_menu_config(body.model_dump(exclude_none=True))
