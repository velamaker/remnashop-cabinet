from datetime import datetime
from typing import Any, Optional

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.dao import PromocodeDao
from src.application.dto import PromocodeDto
from src.core.enums import PromocodeAvailability, PromocodeRewardType

from ._common import AdminUser

router = APIRouter(prefix="/promocodes", tags=["Admin - Promocodes"])


def _promo_to_dict(p: PromocodeDto) -> dict[str, Any]:
    return {
        "id": p.id,
        "code": p.code,
        "is_active": p.is_active,
        "reward_type": p.reward_type,
        "reward": p.reward,
        "availability": p.availability,
        "is_reusable": p.is_reusable,
        "max_activations": p.max_activations,
        "expires_at": p.expires_at.isoformat() if p.expires_at else None,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


@router.get("")
@inject
async def list_promocodes(
    _admin: AdminUser,
    promocode_dao: FromDishka[PromocodeDao],
    limit: int = Query(default=25, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    total = await promocode_dao.get_count()
    promos = await promocode_dao.get_list(limit=limit, offset=offset)
    items = []
    for p in promos:
        activations = await promocode_dao.get_activations_count(p.id)
        d = _promo_to_dict(p)
        d["total_activations"] = activations
        items.append(d)

    return {"total": total, "limit": limit, "offset": offset, "items": items}


class CreatePromocodeRequest(BaseModel):
    code: str
    reward_type: str
    reward: Optional[int] = None
    availability: str = "ALL"
    is_reusable: bool = False
    max_activations: Optional[int] = None
    expires_at: Optional[datetime] = None


@router.post("", status_code=status.HTTP_201_CREATED)
@inject
async def create_promocode(
    body: CreatePromocodeRequest,
    _admin: AdminUser,
    promocode_dao: FromDishka[PromocodeDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    existing = await promocode_dao.get_by_code(body.code.upper())
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Promocode with this code already exists"
        )

    try:
        reward_type = PromocodeRewardType(body.reward_type.upper())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid reward_type: {body.reward_type}"
        )

    try:
        availability = PromocodeAvailability(body.availability.upper())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid availability: {body.availability}"
        )

    promo = PromocodeDto(
        id=0,
        code=body.code.upper(),
        is_active=True,
        reward_type=reward_type,
        reward=body.reward,
        availability=availability,
        is_reusable=body.is_reusable,
        max_activations=body.max_activations,
        expires_at=body.expires_at,
    )

    created = await promocode_dao.create(promo)
    await session.commit()
    return _promo_to_dict(created)


@router.get("/{promocode_id}/stats")
@inject
async def get_promocode_stats(
    promocode_id: int,
    _admin: AdminUser,
    promocode_dao: FromDishka[PromocodeDao],
) -> dict[str, Any]:
    promo = await promocode_dao.get_by_id(promocode_id)
    if not promo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promocode not found")

    stats = await promocode_dao.get_detail_statistics(promocode_id)
    if not stats:
        return {**_promo_to_dict(promo), "stats": None}

    return {
        **_promo_to_dict(promo),
        "stats": {
            "total_activations": stats.total_activations,
            "activations_today": stats.activations_today,
            "activations_week": stats.activations_week,
            "activations_month": stats.activations_month,
        },
    }


@router.delete("/{promocode_id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def delete_promocode(
    promocode_id: int,
    _admin: AdminUser,
    promocode_dao: FromDishka[PromocodeDao],
    session: FromDishka[AsyncSession],
) -> None:
    promo = await promocode_dao.get_by_id(promocode_id)
    if not promo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promocode not found")
    await promocode_dao.delete(promocode_id)
    await session.commit()


class TogglePromocodeRequest(BaseModel):
    is_active: bool


@router.put("/{promocode_id}/toggle")
@inject
async def toggle_promocode(
    promocode_id: int,
    body: TogglePromocodeRequest,
    _admin: AdminUser,
    promocode_dao: FromDishka[PromocodeDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    promo = await promocode_dao.get_by_id(promocode_id)
    if not promo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promocode not found")
    promo.is_active = body.is_active
    updated = await promocode_dao.update(promo)
    if not updated:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Update failed")
    await session.commit()
    return _promo_to_dict(updated)
