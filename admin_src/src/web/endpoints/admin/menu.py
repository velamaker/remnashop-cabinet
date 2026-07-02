"""Админ: состав кнопок главного меню бота (редактируется из кабинета).

Хранилище и дефолты — в telegram/routers/menu/menu_config.py (тот же файл читает
геттер меню). Меню применяет изменения сразу, без перезапуска бота.
"""

from typing import Any, Optional

from aiogram.enums import ButtonStyle
from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.dao import SettingsDao
from src.telegram.routers.menu.menu_config import load_menu_config, save_menu_config

from ._common import AdminUser

router = APIRouter(prefix="/menu", tags=["Admin - Menu"])

# Доступные цвета кнопок бота (авторская задумка: MenuButtonDto.color).
BUTTON_COLORS = [c.value for c in ButtonStyle]  # ['danger','success','primary']


class MenuConfigUpdate(BaseModel):
    cabinet_miniapp: Optional[bool] = None
    cabinet_url: Optional[bool] = None
    connect_miniapp: Optional[bool] = None
    connect_url: Optional[bool] = None
    remna_sub: Optional[bool] = None
    # Порядок кнопок (список ключей). Нормализуется в menu_config (чужие ключи и
    # дубли отсекаются, недостающие добиваются в дефолтном порядке).
    order: Optional[list[str]] = None


@router.get("")
async def get_menu(_admin: AdminUser) -> dict[str, Any]:
    return load_menu_config()


@router.put("")
async def update_menu(body: MenuConfigUpdate, _admin: AdminUser) -> dict[str, Any]:
    return save_menu_config(body.model_dump(exclude_none=True))


# ── Цвета кнопок бота (settings.menu.buttons[].color) — авторская фича ──────────

def _bot_button_public(b: Any) -> dict[str, Any]:
    return {
        "index": b.index,
        "text": b.text,
        "type": b.type.value if hasattr(b.type, "value") else str(b.type),
        "is_active": bool(b.is_active),
        "color": b.color.value if getattr(b, "color", None) is not None else None,
    }


@router.get("/buttons")
@inject
async def get_menu_buttons(
    _admin: AdminUser,
    settings_dao: FromDishka[SettingsDao],
) -> dict[str, Any]:
    s = await settings_dao.get()
    return {
        "buttons": [_bot_button_public(b) for b in s.menu.buttons],
        "colors": BUTTON_COLORS,
    }


class ButtonColorsUpdate(BaseModel):
    # index кнопки → цвет ('primary'/'success'/'danger') или null (дефолт темы).
    colors: dict[int, Optional[str]]


@router.put("/buttons")
@inject
async def set_menu_button_colors(
    body: ButtonColorsUpdate,
    _admin: AdminUser,
    settings_dao: FromDishka[SettingsDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    s = await settings_dao.get()
    for b in s.menu.buttons:
        if b.index in body.colors:
            val = body.colors[b.index]
            if val is None or val == "":
                b.color = None
            else:
                try:
                    b.color = ButtonStyle(val)
                except ValueError:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Недопустимый цвет: {val}",
                    )
    updated = await settings_dao.update(s)
    if not updated:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Update failed")
    await session.commit()
    return {
        "buttons": [_bot_button_public(b) for b in updated.menu.buttons],
        "colors": BUTTON_COLORS,
    }
