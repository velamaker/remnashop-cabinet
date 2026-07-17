"""Public: центр уведомлений пользователя — лента + отметка прочитанного.

Записи кладёт services/overlay_push.py::_record_user_notification при каждой
отправке push/событийного уведомления юзеру (чтобы push не терялся «в небытие»
на мобиле). Здесь — чтение и отметка прочитанным для кабинета.
"""

from typing import Any, Optional

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.web.endpoints.public._common import CurrentUser

router = APIRouter(prefix="/notifications", tags=["Public - Notifications"])


@router.get("")
@inject
async def list_notifications(
    user: CurrentUser,
    session: FromDishka[AsyncSession],
    limit: int = 50,
) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    rows = (
        await session.execute(
            text(
                "SELECT id, title, body, url, is_read, created_at FROM user_notifications "
                "WHERE user_id = :u ORDER BY created_at DESC LIMIT :l"
            ),
            {"u": user.id, "l": limit},
        )
    ).mappings().all()
    unread = (
        await session.execute(
            text("SELECT count(*) FROM user_notifications WHERE user_id = :u AND is_read = false"),
            {"u": user.id},
        )
    ).scalar_one()
    return {
        "unread": int(unread or 0),
        "items": [
            {
                "id": r["id"],
                "title": r["title"],
                "body": r["body"],
                "url": r["url"],
                "is_read": r["is_read"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ],
    }


@router.get("/unread-count")
@inject
async def unread_count(
    user: CurrentUser,
    session: FromDishka[AsyncSession],
) -> dict[str, int]:
    unread = (
        await session.execute(
            text("SELECT count(*) FROM user_notifications WHERE user_id = :u AND is_read = false"),
            {"u": user.id},
        )
    ).scalar_one()
    return {"unread": int(unread or 0)}


class ReadRequest(BaseModel):
    id: Optional[int] = None  # None → отметить все прочитанными


@router.post("/read")
@inject
async def mark_read(
    body: ReadRequest,
    user: CurrentUser,
    session: FromDishka[AsyncSession],
) -> dict[str, bool]:
    if body.id is not None:
        await session.execute(
            text("UPDATE user_notifications SET is_read = true WHERE user_id = :u AND id = :id"),
            {"u": user.id, "id": body.id},
        )
    else:
        await session.execute(
            text("UPDATE user_notifications SET is_read = true WHERE user_id = :u AND is_read = false"),
            {"u": user.id},
        )
    await session.commit()
    return {"ok": True}


@router.delete("")
@inject
async def clear_notifications(
    user: CurrentUser,
    session: FromDishka[AsyncSession],
) -> dict[str, bool]:
    await session.execute(
        text("DELETE FROM user_notifications WHERE user_id = :u"),
        {"u": user.id},
    )
    await session.commit()
    return {"ok": True}
