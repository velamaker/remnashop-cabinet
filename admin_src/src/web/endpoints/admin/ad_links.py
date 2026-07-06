from typing import Any, Optional

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.dao import AdLinkDao
from src.application.dto import AdLinkDto

from ._common import AdminUser

router = APIRouter(prefix="/ad-links", tags=["Admin - Ad Links"])


def _link_to_dict(link: Any) -> dict[str, Any]:
    return {
        "id": link.id,
        "name": link.name,
        "code": link.code,
        "is_active": link.is_active,
        "created_at": link.created_at.isoformat() if link.created_at else None,
    }


@router.get("")
@inject
async def list_ad_links(
    _admin: AdminUser,
    ad_link_dao: FromDishka[AdLinkDao],
) -> dict[str, Any]:
    links = await ad_link_dao.get_all()
    return {"items": [_link_to_dict(l) for l in links], "total": len(links)}


@router.get("/{link_id}/stats")
@inject
async def get_ad_link_stats(
    link_id: int,
    _admin: AdminUser,
    ad_link_dao: FromDishka[AdLinkDao],
) -> dict[str, Any]:
    link = await ad_link_dao.get_by_id(link_id)
    if not link:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Рекламная ссылка не найдена")
    stats = await ad_link_dao.get_stats(link_id)
    return {
        **_link_to_dict(link),
        "stats": {
            "registrations": stats.registrations,
            "trials": stats.trials,
            "buyers": stats.buyers,
            "trial_buyers": stats.trial_buyers,
            "revenue": stats.revenue,
            "reg_to_buy_rate": stats.reg_to_buy_rate,
            "trial_to_buy_rate": stats.trial_to_buy_rate,
        },
    }


class CreateAdLinkRequest(BaseModel):
    name: str
    code: str


class UpdateAdLinkRequest(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None


@router.post("", status_code=status.HTTP_201_CREATED)
@inject
async def create_ad_link(
    body: CreateAdLinkRequest,
    _admin: AdminUser,
    ad_link_dao: FromDishka[AdLinkDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    existing = await ad_link_dao.get_by_code(body.code)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Такой код уже существует")
    link = AdLinkDto(id=0, name=body.name, code=body.code, is_active=True)
    created = await ad_link_dao.create(link)
    await session.commit()
    return _link_to_dict(created)


@router.put("/{link_id}")
@inject
async def update_ad_link(
    link_id: int,
    body: UpdateAdLinkRequest,
    _admin: AdminUser,
    ad_link_dao: FromDishka[AdLinkDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    link = await ad_link_dao.get_by_id(link_id)
    if not link:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Рекламная ссылка не найдена")
    if body.name is not None:
        link.name = body.name
    if body.is_active is not None:
        link.is_active = body.is_active
    updated = await ad_link_dao.update(link)
    if not updated:
        raise HTTPException(status_code=500, detail="Не удалось обновить")
    await session.commit()
    return _link_to_dict(updated)


@router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def delete_ad_link(
    link_id: int,
    _admin: AdminUser,
    ad_link_dao: FromDishka[AdLinkDao],
    session: FromDishka[AsyncSession],
) -> None:
    link = await ad_link_dao.get_by_id(link_id)
    if not link:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Рекламная ссылка не найдена")
    await ad_link_dao.delete(link_id)
    await session.commit()
