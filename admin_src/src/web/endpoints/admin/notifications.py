"""История уведомлений админам: лента web-push, которые уходили админам.

Записи кладёт services/overlay_push.py::push_admins_standalone при каждой
отправке админам (даже если ни одного устройства не подписано). Здесь — чтение
для админки и очистка. Раздел прав — «settings» (см. permissions.py).
"""

from typing import Any

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.services.overlay_push import load_notif_settings, save_notif_settings

from ._common import AdminUser

router = APIRouter(prefix="/notifications", tags=["Admin - Notifications"])


class NotifSettingsUpdate(BaseModel):
    admin_push_enabled: bool


@router.get("/settings")
async def get_notif_settings(_admin: AdminUser) -> dict[str, Any]:
    """Тумблер дублирования админ-уведомлений в web-push (на телефон)."""
    return load_notif_settings()


@router.put("/settings")
async def update_notif_settings(body: NotifSettingsUpdate, _admin: AdminUser) -> dict[str, Any]:
    save_notif_settings(body.admin_push_enabled)
    return load_notif_settings()


@router.get("")
@inject
async def list_notifications(
    _admin: AdminUser,
    session: FromDishka[AsyncSession],
    limit: int = 100,
) -> dict[str, Any]:
    limit = max(1, min(limit, 500))
    rows = (
        await session.execute(
            text(
                "SELECT id, title, body, url, created_at "
                "FROM admin_notifications ORDER BY created_at DESC LIMIT :l"
            ),
            {"l": limit},
        )
    ).mappings().all()
    return {
        "items": [
            {
                "id": r["id"],
                "title": r["title"],
                "body": r["body"],
                "url": r["url"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]
    }


@router.delete("")
@inject
async def clear_notifications(
    _admin: AdminUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    # Мутация — overlay-эндпоинт коммитит сессию вручную (см. память проекта).
    await session.execute(text("DELETE FROM admin_notifications"))
    await session.commit()
    return {"ok": True}
