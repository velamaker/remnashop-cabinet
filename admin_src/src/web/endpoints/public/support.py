"""
Поддержка через сайт: тикеты пользователя и переписка.
Пользователь видит только свои тикеты; админ — все (см. admin/support.py).
"""

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ._common import CurrentUser

router = APIRouter(prefix="/support", tags=["Public - Support"])


class CreateTicketRequest(BaseModel):
    subject: str = Field(min_length=2, max_length=200)
    message: str = Field(min_length=1, max_length=4000)


class MessageRequest(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


def _msg(row) -> dict:
    return {
        "id": row.id,
        "sender": row.sender,
        "body": row.body,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _ticket(row) -> dict:
    return {
        "id": row.id,
        "subject": row.subject,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("/tickets")
@inject
async def list_my_tickets(
    user: CurrentUser,
    session: FromDishka[AsyncSession],
) -> dict:
    rows = (
        await session.execute(
            text(
                """
                SELECT t.id, t.subject, t.status, t.created_at, t.updated_at,
                       (SELECT COUNT(*) FROM support_messages m WHERE m.ticket_id = t.id) AS messages_count
                FROM support_tickets t
                WHERE t.user_id = :uid
                ORDER BY t.updated_at DESC
                """
            ),
            {"uid": user.id},
        )
    ).all()
    return {
        "items": [{**_ticket(r), "messages_count": r.messages_count} for r in rows]
    }


@router.post("/tickets", status_code=status.HTTP_201_CREATED)
@inject
async def create_ticket(
    body: CreateTicketRequest,
    user: CurrentUser,
    session: FromDishka[AsyncSession],
) -> dict:
    ticket_id = (
        await session.execute(
            text(
                """
                INSERT INTO support_tickets (user_id, subject, status)
                VALUES (:uid, :subject, 'open')
                RETURNING id
                """
            ),
            {"uid": user.id, "subject": body.subject.strip()},
        )
    ).scalar_one()
    await session.execute(
        text(
            """
            INSERT INTO support_messages (ticket_id, sender, body)
            VALUES (:tid, 'user', :body)
            """
        ),
        {"tid": ticket_id, "body": body.message.strip()},
    )
    await session.commit()
    return {"id": ticket_id}


@router.get("/tickets/{ticket_id}")
@inject
async def get_ticket(
    ticket_id: int,
    user: CurrentUser,
    session: FromDishka[AsyncSession],
) -> dict:
    t = (
        await session.execute(
            text(
                "SELECT id, subject, status, created_at, updated_at "
                "FROM support_tickets WHERE id = :id AND user_id = :uid"
            ),
            {"id": ticket_id, "uid": user.id},
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
    return {**_ticket(t), "messages": [_msg(m) for m in msgs]}


@router.post("/tickets/{ticket_id}/messages")
@inject
async def add_message(
    ticket_id: int,
    body: MessageRequest,
    user: CurrentUser,
    session: FromDishka[AsyncSession],
) -> dict:
    owned = (
        await session.execute(
            text("SELECT status FROM support_tickets WHERE id = :id AND user_id = :uid"),
            {"id": ticket_id, "uid": user.id},
        )
    ).first()
    if not owned:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тикет не найден")

    await session.execute(
        text(
            "INSERT INTO support_messages (ticket_id, sender, body) VALUES (:tid, 'user', :body)"
        ),
        {"tid": ticket_id, "body": body.body.strip()},
    )
    await session.execute(
        text("UPDATE support_tickets SET status = 'open', updated_at = now() WHERE id = :id"),
        {"id": ticket_id},
    )
    await session.commit()
    return {"success": True}


@router.post("/tickets/{ticket_id}/close")
@inject
async def close_ticket(
    ticket_id: int,
    user: CurrentUser,
    session: FromDishka[AsyncSession],
) -> dict:
    res = await session.execute(
        text(
            "UPDATE support_tickets SET status = 'closed', updated_at = now() "
            "WHERE id = :id AND user_id = :uid"
        ),
        {"id": ticket_id, "uid": user.id},
    )
    if res.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тикет не найден")
    await session.commit()
    return {"success": True}
