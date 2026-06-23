"""Поддержка для админа: все тикеты пользователей и ответы."""

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ._common import AdminUser

router = APIRouter(prefix="/support", tags=["Admin - Support"])


class ReplyRequest(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class StatusRequest(BaseModel):
    status: str = Field(pattern="^(open|answered|closed)$")


@router.get("/tickets")
@inject
async def list_all_tickets(
    _admin: AdminUser,
    session: FromDishka[AsyncSession],
    status: str | None = None,
) -> dict:
    where = "WHERE t.status = :status" if status else ""
    rows = (
        await session.execute(
            text(
                f"""
                SELECT t.id, t.subject, t.status, t.created_at, t.updated_at,
                       u.id AS user_id, u.name AS user_name, u.email AS user_email,
                       u.telegram_id AS user_telegram_id,
                       (SELECT COUNT(*) FROM support_messages m WHERE m.ticket_id = t.id) AS messages_count
                FROM support_tickets t
                JOIN users u ON u.id = t.user_id
                {where}
                ORDER BY
                    CASE t.status WHEN 'open' THEN 0 WHEN 'answered' THEN 1 ELSE 2 END,
                    t.updated_at DESC
                """
            ),
            {"status": status} if status else {},
        )
    ).all()
    return {
        "items": [
            {
                "id": r.id,
                "subject": r.subject,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                "messages_count": r.messages_count,
                "user": {
                    "id": r.user_id,
                    "name": r.user_name,
                    "email": r.user_email,
                    "telegram_id": r.user_telegram_id,
                },
            }
            for r in rows
        ]
    }


@router.get("/tickets/{ticket_id}")
@inject
async def get_ticket(
    ticket_id: int,
    _admin: AdminUser,
    session: FromDishka[AsyncSession],
) -> dict:
    t = (
        await session.execute(
            text(
                """
                SELECT t.id, t.subject, t.status, t.created_at, t.updated_at,
                       u.id AS user_id, u.name AS user_name, u.email AS user_email,
                       u.telegram_id AS user_telegram_id
                FROM support_tickets t
                JOIN users u ON u.id = t.user_id
                WHERE t.id = :id
                """
            ),
            {"id": ticket_id},
        )
    ).first()
    if not t:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тикет не найден")

    msgs = (
        await session.execute(
            text(
                "SELECT id, sender, body, created_at FROM support_messages "
                "WHERE ticket_id = :id ORDER BY created_at ASC"
            ),
            {"id": ticket_id},
        )
    ).all()
    return {
        "id": t.id,
        "subject": t.subject,
        "status": t.status,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        "user": {
            "id": t.user_id,
            "name": t.user_name,
            "email": t.user_email,
            "telegram_id": t.user_telegram_id,
        },
        "messages": [
            {
                "id": m.id,
                "sender": m.sender,
                "body": m.body,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in msgs
        ],
    }


@router.post("/tickets/{ticket_id}/messages")
@inject
async def admin_reply(
    ticket_id: int,
    body: ReplyRequest,
    _admin: AdminUser,
    session: FromDishka[AsyncSession],
) -> dict:
    exists = (
        await session.execute(
            text("SELECT 1 FROM support_tickets WHERE id = :id"), {"id": ticket_id}
        )
    ).first()
    if not exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тикет не найден")

    await session.execute(
        text(
            "INSERT INTO support_messages (ticket_id, sender, body) VALUES (:tid, 'admin', :body)"
        ),
        {"tid": ticket_id, "body": body.body.strip()},
    )
    await session.execute(
        text("UPDATE support_tickets SET status = 'answered', updated_at = now() WHERE id = :id"),
        {"id": ticket_id},
    )
    await session.commit()
    return {"success": True}


@router.post("/tickets/{ticket_id}/status")
@inject
async def set_status(
    ticket_id: int,
    body: StatusRequest,
    _admin: AdminUser,
    session: FromDishka[AsyncSession],
) -> dict:
    res = await session.execute(
        text("UPDATE support_tickets SET status = :s, updated_at = now() WHERE id = :id"),
        {"s": body.status, "id": ticket_id},
    )
    if res.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тикет не найден")
    await session.commit()
    return {"success": True, "status": body.status}
