from typing import Any, Optional

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.dao import SubscriptionDao, TransactionDao, UserDao
from src.application.dto import UserDto
from src.core.enums import Role

from ._common import AdminUser
from ._redact import is_readonly_admin, redact_user

router = APIRouter(prefix="/users", tags=["Admin - Users"])


def _user_to_dict(user: UserDto) -> dict[str, Any]:
    return {
        "id": user.id,
        "telegram_id": user.telegram_id,
        "auth_type": user.auth_type,
        "email": user.email,
        "is_email_verified": user.is_email_verified,
        "name": user.name,
        "username": user.username,
        "role": user.role,
        "language": user.language,
        "is_blocked": user.is_blocked,
        "is_bot_blocked": user.is_bot_blocked,
        "is_trial_available": user.is_trial_available,
        "personal_discount": user.personal_discount,
        "purchase_discount": user.purchase_discount,
        "points": user.points,
        "referral_code": user.referral_code,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@router.get("")
@inject
async def list_users(
    admin: AdminUser,
    user_dao: FromDishka[UserDao],
    limit: int = Query(default=25, le=100),
    offset: int = Query(default=0, ge=0),
    search: Optional[str] = Query(default=None),
    blocked: Optional[bool] = Query(default=None),
) -> dict[str, Any]:
    if search:
        users = await user_dao.get_by_partial_name(search)
        total = len(users)
        users = users[offset : offset + limit]
    elif blocked is True:
        users = await user_dao.get_blocked_users()
        total = len(users)
        users = users[offset : offset + limit]
    else:
        total = await user_dao.count()
        users = await user_dao.get_all(limit=limit, offset=offset)

    items = [_user_to_dict(u) for u in users]
    if is_readonly_admin(admin):
        items = [redact_user(it) for it in items]

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
    }


@router.get("/{user_id}")
@inject
async def get_user(
    user_id: int,
    admin: AdminUser,
    user_dao: FromDishka[UserDao],
    subscription_dao: FromDishka[SubscriptionDao],
    transaction_dao: FromDishka[TransactionDao],
) -> dict[str, Any]:
    user = await user_dao.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    current_sub = await subscription_dao.get_current(user_id)
    all_subs = await subscription_dao.get_all_by_user(user_id)
    # Транзакции в карточке — платёжные детали; для read-only их не отдаём.
    readonly = is_readonly_admin(admin)
    transactions = [] if readonly else await transaction_dao.get_by_user(user_id)

    user_dict = _user_to_dict(user)
    if readonly:
        user_dict = redact_user(user_dict)

    return {
        "user": user_dict,
        "current_subscription": {
            "status": current_sub.current_status.value,
            "is_trial": current_sub.is_trial,
            "plan_name": current_sub.plan_snapshot.name if current_sub.plan_snapshot else None,
            "expire_at": current_sub.expire_at.isoformat() if current_sub.expire_at else None,
            "traffic_limit": current_sub.traffic_limit,
            "device_limit": current_sub.device_limit,
        }
        if current_sub
        else None,
        "subscriptions_count": len(all_subs),
        "transactions": [
            {
                "payment_id": str(t.payment_id),
                "status": t.status,
                "gateway_type": t.gateway_type,
                "purchase_type": t.purchase_type,
                "final_amount": str(t.final_amount) if hasattr(t, "final_amount") else None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in transactions[:20]
        ],
    }


class ToggleBlockRequest(BaseModel):
    is_blocked: bool


@router.put("/{user_id}/block")
@inject
async def toggle_block_user(
    user_id: int,
    body: ToggleBlockRequest,
    admin: AdminUser,
    user_dao: FromDishka[UserDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    user = await user_dao.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot block yourself")
    if user.role >= Role.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Cannot block owner"
        )

    user.is_blocked = body.is_blocked
    updated = await user_dao.update(user)
    if not updated:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Update failed")
    await session.commit()
    return {"success": True, "is_blocked": updated.is_blocked}


class ChangeRoleRequest(BaseModel):
    role: int


@router.put("/{user_id}/role")
@inject
async def change_user_role(
    user_id: int,
    body: ChangeRoleRequest,
    admin: AdminUser,
    user_dao: FromDishka[UserDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    if admin.role < Role.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only owner can change roles",
        )
    user = await user_dao.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot change own role")

    try:
        new_role = Role(body.role)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role value")

    user.role = new_role
    updated = await user_dao.update(user)
    if not updated:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Update failed")
    await session.commit()
    return {"success": True, "role": updated.role}


class SetDiscountRequest(BaseModel):
    personal_discount: int
    purchase_discount: int


@router.put("/{user_id}/discount")
@inject
async def set_user_discount(
    user_id: int,
    body: SetDiscountRequest,
    admin: AdminUser,
    user_dao: FromDishka[UserDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    user = await user_dao.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.personal_discount = max(0, min(100, body.personal_discount))
    user.purchase_discount = max(0, min(100, body.purchase_discount))
    updated = await user_dao.update(user)
    if not updated:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Update failed")
    await session.commit()
    return {
        "success": True,
        "personal_discount": updated.personal_discount,
        "purchase_discount": updated.purchase_discount,
    }
