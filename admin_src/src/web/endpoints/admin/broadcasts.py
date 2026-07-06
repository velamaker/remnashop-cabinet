from typing import Any
from uuid import UUID, uuid4

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import BroadcastDispatcher
from src.application.common.dao import BroadcastDao, UserDao
from src.application.common.uow import UnitOfWork
from src.application.dto import BroadcastDto, MessagePayloadDto
from src.core.enums import BroadcastAudience, BroadcastStatus

from ._common import AdminUser

router = APIRouter(prefix="/broadcasts", tags=["Admin - Broadcasts"])

from src.infrastructure.taskiq.tasks.broadcast_email import (
    EMAIL_SEGMENT_FROM,
    send_email_broadcast,
)

# Каналы формы кабинета → аудитория базового TG-пайплайна.
_TG_AUDIENCE: dict[str, BroadcastAudience] = {
    "TG_ALL": BroadcastAudience.ALL,
    "TG_SUBSCRIBED": BroadcastAudience.SUBSCRIBED,
    "TG_UNSUBSCRIBED": BroadcastAudience.UNSUBSCRIBED,
    "TG_TRIAL": BroadcastAudience.TRIAL,
    "TG_EXPIRED": BroadcastAudience.EXPIRED,
}
_EMAIL_CHANNELS = set(EMAIL_SEGMENT_FROM)  # EMAIL_ALL / _SUBSCRIBED / _TRIAL / _EXPIRING / _EXPIRED
_KNOWN_CHANNELS = set(_TG_AUDIENCE) | _EMAIL_CHANNELS


def _brand() -> str:
    try:
        from src.web.endpoints.public.appearance import resolve_brand_name

        return resolve_brand_name() or "VPN"
    except Exception:
        return "VPN"


async def _tg_count(user_dao: UserDao, audience: BroadcastAudience) -> int:
    if audience == BroadcastAudience.ALL:
        return await user_dao.count_active_non_blocked()
    if audience == BroadcastAudience.SUBSCRIBED:
        return await user_dao.count_with_active_subscription()
    if audience == BroadcastAudience.UNSUBSCRIBED:
        return await user_dao.count_without_subscription()
    if audience == BroadcastAudience.TRIAL:
        return await user_dao.count_with_trial_subscription()
    if audience == BroadcastAudience.EXPIRED:
        return await user_dao.count_with_expired_subscription()
    return 0


async def _email_count(session: AsyncSession, segment: str) -> int:
    frm = EMAIL_SEGMENT_FROM.get(segment)
    if not frm:
        return 0
    return int((await session.execute(text(f"SELECT count(*) {frm}"))).scalar_one())


def _broadcast_to_dict(b: Any) -> dict[str, Any]:
    return {
        "task_id": str(b.task_id),
        "status": b.status.value if hasattr(b.status, "value") else str(b.status),
        "audience": b.audience.value if hasattr(b.audience, "value") else str(b.audience),
        "total_count": b.total_count,
        "success_count": b.success_count,
        "failed_count": b.failed_count,
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }


def _email_row_to_dict(r: Any) -> dict[str, Any]:
    return {
        "task_id": f"email-{r.id}",
        "status": r.status,
        "audience": getattr(r, "segment", None) or "EMAIL_ALL",
        "total_count": r.total_count,
        "success_count": r.success_count,
        "failed_count": r.failed_count,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


class CreateBroadcastBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    channels: list[str] = Field(min_length=1)


@router.post("", status_code=status.HTTP_201_CREATED)
@inject
async def create_broadcast(
    body: CreateBroadcastBody,
    _admin: AdminUser,
    uow: FromDishka[UnitOfWork],
    broadcast_dao: FromDishka[BroadcastDao],
    dispatcher: FromDishka[BroadcastDispatcher],
    user_dao: FromDishka[UserDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    content = body.text.strip()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Текст рассылки пуст")

    channels = [c for c in dict.fromkeys(body.channels) if c in _KNOWN_CHANNELS]
    if not channels:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Не выбран ни один канал")

    telegram_tasks: list[str] = []
    for ch in channels:
        audience = _TG_AUDIENCE.get(ch)
        if audience is None:
            continue
        count = await _tg_count(user_dao, audience)
        payload = MessagePayloadDto(i18n_key="raw-message", i18n_kwargs={"content": content})
        broadcast = BroadcastDto(
            task_id=uuid4(),
            status=BroadcastStatus.PROCESSING,
            total_count=count,
            audience=audience,
            payload=payload,
        )
        async with uow:
            await broadcast_dao.create(broadcast)
            await uow.commit()
        await dispatcher.start(broadcast, None)
        telegram_tasks.append(str(broadcast.task_id))

    email_tasks: list[int] = []
    email_segments = [c for c in channels if c in _EMAIL_CHANNELS]
    if email_segments:
        subject = f"Сообщение от {_brand()}"
        for seg in email_segments:
            res = await session.execute(
                text(
                    "INSERT INTO email_broadcasts (subject, body, status, segment) "
                    "VALUES (:s, :b, 'PROCESSING', :seg) RETURNING id"
                ),
                {"s": subject, "b": content, "seg": seg},
            )
            email_id = res.scalar_one()
            await session.commit()
            await send_email_broadcast.kiq(email_id, subject, content, seg)  # type: ignore[call-overload]
            email_tasks.append(email_id)

    return {"telegram": telegram_tasks, "email": email_tasks}


@router.get("/audience-counts")
@inject
async def audience_counts(
    _admin: AdminUser,
    user_dao: FromDishka[UserDao],
    session: FromDishka[AsyncSession],
) -> dict[str, int]:
    counts: dict[str, int] = {
        "TG_ALL": await user_dao.count_active_non_blocked(),
        "TG_SUBSCRIBED": await user_dao.count_with_active_subscription(),
        "TG_UNSUBSCRIBED": await user_dao.count_without_subscription(),
        "TG_TRIAL": await user_dao.count_with_trial_subscription(),
        "TG_EXPIRED": await user_dao.count_with_expired_subscription(),
    }
    for seg in EMAIL_SEGMENT_FROM:
        counts[seg] = await _email_count(session, seg)
    return counts


@router.get("")
@inject
async def list_broadcasts(
    _admin: AdminUser,
    broadcast_dao: FromDishka[BroadcastDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    tg = [_broadcast_to_dict(b) for b in await broadcast_dao.get_all()]
    rows = (
        await session.execute(
            text(
                "SELECT id, status, total_count, success_count, failed_count, created_at, segment "
                "FROM email_broadcasts ORDER BY created_at DESC"
            )
        )
    ).all()
    items = tg + [_email_row_to_dict(r) for r in rows]
    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return {"items": items, "total": len(items)}


@router.get("/{task_id}")
@inject
async def get_broadcast(
    task_id: str,
    _admin: AdminUser,
    broadcast_dao: FromDishka[BroadcastDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    if task_id.startswith("email-"):
        try:
            eid = int(task_id[len("email-"):])
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Недопустимый task_id")
        row = (
            await session.execute(
                text(
                    "SELECT id, status, total_count, success_count, failed_count, created_at, segment "
                    "FROM email_broadcasts WHERE id = :id"
                ),
                {"id": eid},
            )
        ).first()
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Рассылка не найдена")
        return _email_row_to_dict(row)

    try:
        uid = UUID(task_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Недопустимый task_id")

    b = await broadcast_dao.get_by_task_id(uid)
    if not b:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Рассылка не найдена")
    return _broadcast_to_dict(b)
