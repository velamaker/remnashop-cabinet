from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.dao import PlanDao, SubscriptionDao, UserDao
from src.application.dto import PlanSnapshotDto, SubscriptionDto
from src.core.enums import SubscriptionStatus
from remnapy.enums.users import TrafficLimitStrategy

from ._common import AdminUser

router = APIRouter(prefix="/subscriptions", tags=["Admin - Subscriptions"])

UNLIMITED_YEAR = 2099


def _sub_to_dict(s: SubscriptionDto) -> dict[str, Any]:
    return {
        "id": s.id,
        "user_id": s.user_id,
        "status": s.current_status.value,
        "is_trial": s.is_trial,
        "plan_name": s.plan_snapshot.name if s.plan_snapshot else None,
        "expire_at": s.expire_at.isoformat() if s.expire_at else None,
        "traffic_limit": s.traffic_limit,
        "device_limit": s.device_limit,
        "url": s.url,
        "created_at": s.created_at.isoformat() if hasattr(s, "created_at") and s.created_at else None,
    }


# ─── Get user subscription ──────────────────────────────────────────────────

@router.get("/user/{user_id}")
@inject
async def get_user_subscription(
    user_id: int,
    _admin: AdminUser,
    subscription_dao: FromDishka[SubscriptionDao],
) -> dict[str, Any]:
    sub = await subscription_dao.get_current(user_id)
    all_subs = await subscription_dao.get_all_by_user(user_id)
    return {
        "current": _sub_to_dict(sub) if sub else None,
        "history": [_sub_to_dict(s) for s in all_subs[:20]],
    }


# ─── Extend subscription ─────────────────────────────────────────────────────

class ExtendRequest(BaseModel):
    days: int


@router.post("/user/{user_id}/extend")
@inject
async def extend_subscription(
    user_id: int,
    body: ExtendRequest,
    _admin: AdminUser,
    subscription_dao: FromDishka[SubscriptionDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    if body.days == 0 or abs(body.days) > 3650:
        raise HTTPException(status_code=400, detail="days must be between -3650 and 3650 (not 0)")

    sub = await subscription_dao.get_current(user_id)
    if not sub:
        raise HTTPException(status_code=404, detail="No active subscription found")

    now = datetime.now(timezone.utc)
    # Продление (+): считаем от текущего срока или от now, если уже истёк.
    # Убавление (−): считаем строго от текущего срока, не опускаем ниже now.
    if body.days >= 0:
        base = sub.expire_at if sub.expire_at > now else now
        sub.expire_at = base + timedelta(days=body.days)
    else:
        new_expire = sub.expire_at + timedelta(days=body.days)
        sub.expire_at = new_expire if new_expire > now else now

    updated = await subscription_dao.update(sub)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update subscription")
    await session.commit()
    return {"success": True, "subscription": _sub_to_dict(updated)}


# ─── Disable subscription ─────────────────────────────────────────────────────

@router.post("/user/{user_id}/disable")
@inject
async def disable_subscription(
    user_id: int,
    _admin: AdminUser,
    subscription_dao: FromDishka[SubscriptionDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    sub = await subscription_dao.get_current(user_id)
    if not sub:
        raise HTTPException(status_code=404, detail="No subscription found")

    updated = await subscription_dao.update_status(sub.id, SubscriptionStatus.DISABLED)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update subscription")
    await session.commit()
    return {"success": True, "subscription": _sub_to_dict(updated)}


# ─── Delete subscription ─────────────────────────────────────────────────────

@router.post("/user/{user_id}/delete")
@inject
async def delete_subscription(
    user_id: int,
    _admin: AdminUser,
    subscription_dao: FromDishka[SubscriptionDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    sub = await subscription_dao.get_current(user_id)
    if not sub:
        raise HTTPException(status_code=404, detail="No subscription found")

    updated = await subscription_dao.update_status(sub.id, SubscriptionStatus.DELETED)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to delete subscription")
    await session.commit()
    return {"success": True}


# ─── Grant subscription ───────────────────────────────────────────────────────

class GrantRequest(BaseModel):
    plan_id: int
    days: int
    is_trial: bool = False


@router.post("/user/{user_id}/grant")
@inject
async def grant_subscription(
    user_id: int,
    body: GrantRequest,
    _admin: AdminUser,
    user_dao: FromDishka[UserDao],
    plan_dao: FromDishka[PlanDao],
    subscription_dao: FromDishka[SubscriptionDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    if body.days <= 0 or body.days > 3650:
        raise HTTPException(status_code=400, detail="days must be between 1 and 3650")

    user = await user_dao.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    plan = await plan_dao.get_by_id(body.plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    snapshot = PlanSnapshotDto.from_plan(plan, duration=body.days)
    snapshot.is_trial = body.is_trial

    now = datetime.now(timezone.utc)

    # If user already has a subscription, extend it; otherwise create new
    existing = await subscription_dao.get_current(user_id)
    if existing and existing.is_active:
        base = existing.expire_at if existing.expire_at > now else now
        existing.expire_at = base + timedelta(days=body.days)
        existing.plan_snapshot = snapshot
        updated = await subscription_dao.update(existing)
        if not updated:
            raise HTTPException(status_code=500, detail="Failed to update subscription")
        await session.commit()
        return {"success": True, "subscription": _sub_to_dict(updated), "action": "extended"}

    # Create new subscription
    new_sub = SubscriptionDto(
        user_id=user_id,
        user_remna_id=user.remna_id if hasattr(user, "remna_id") else uuid4(),
        status=SubscriptionStatus.ACTIVE,
        is_trial=body.is_trial,
        traffic_limit=plan.traffic_limit,
        device_limit=plan.device_limit,
        traffic_limit_strategy=plan.traffic_limit_strategy or TrafficLimitStrategy.NO_RESET,
        expire_at=now + timedelta(days=body.days),
        url="",
        plan_snapshot=snapshot,
    )

    created = await subscription_dao.create(new_sub, user_id)
    if not created:
        raise HTTPException(status_code=500, detail="Failed to create subscription")
    await session.commit()
    return {"success": True, "subscription": _sub_to_dict(created), "action": "created"}


# ─── Reset trial ─────────────────────────────────────────────────────────────

@router.post("/user/{user_id}/reset-trial")
@inject
async def reset_trial(
    user_id: int,
    _admin: AdminUser,
    user_dao: FromDishka[UserDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    user = await user_dao.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_trial_available = True
    updated = await user_dao.update(user)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update user")
    await session.commit()
    return {"success": True, "is_trial_available": True}


# ─── Add points ──────────────────────────────────────────────────────────────

class AddPointsRequest(BaseModel):
    points: int


@router.post("/user/{user_id}/points")
@inject
async def add_points(
    user_id: int,
    body: AddPointsRequest,
    _admin: AdminUser,
    user_dao: FromDishka[UserDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    user = await user_dao.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.points = max(0, (user.points or 0) + body.points)
    updated = await user_dao.update(user)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update user")
    await session.commit()
    return {"success": True, "points": updated.points}
