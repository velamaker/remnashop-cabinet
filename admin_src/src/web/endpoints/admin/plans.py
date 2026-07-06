from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from remnapy.enums.users import TrafficLimitStrategy
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Remnawave
from src.application.common.dao import PlanDao
from src.application.dto import PlanDto, PlanDurationDto, PlanPriceDto
from src.core.enums import Currency, PlanAvailability, PlanType

from ._common import AdminUser

router = APIRouter(prefix="/plans", tags=["Admin - Plans"])


def _price_to_dict(p: PlanPriceDto) -> dict:
    return {"currency": p.currency.value if hasattr(p.currency, "value") else str(p.currency), "price": str(p.price)}


def _duration_to_dict(d: PlanDurationDto) -> dict:
    return {"days": d.days, "order_index": d.order_index, "prices": [_price_to_dict(p) for p in d.prices]}


def _plan_to_dict(plan: PlanDto) -> dict[str, Any]:
    return {
        "id": plan.id,
        "public_code": plan.public_code,
        "name": plan.name,
        "description": plan.description,
        "tag": plan.tag,
        "type": plan.type.value if hasattr(plan.type, "value") else str(plan.type),
        "availability": plan.availability.value if hasattr(plan.availability, "value") else str(plan.availability),
        "traffic_limit_strategy": plan.traffic_limit_strategy.value if hasattr(plan.traffic_limit_strategy, "value") else str(plan.traffic_limit_strategy),
        "traffic_limit": plan.traffic_limit,
        "device_limit": plan.device_limit,
        "allowed_telegram_ids": list(plan.allowed_telegram_ids or []),
        "allowed_emails": list(plan.allowed_emails or []),
        "internal_squads": [str(u) for u in (plan.internal_squads or [])],
        "external_squad": str(plan.external_squad) if plan.external_squad else None,
        "order_index": plan.order_index,
        "is_active": plan.is_active,
        "is_trial": plan.is_trial,
        "durations": [_duration_to_dict(d) for d in plan.durations],
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
    }


@router.get("")
@inject
async def list_plans(
    _admin: AdminUser,
    plan_dao: FromDishka[PlanDao],
) -> dict[str, Any]:
    plans = await plan_dao.get_all()
    return {"items": [_plan_to_dict(p) for p in plans], "total": len(plans)}


@router.get("/meta/squads")
@inject
async def list_squads(
    _admin: AdminUser,
    remnawave: FromDishka[Remnawave],
) -> dict[str, Any]:
    """Сквады из панели Remnawave для конфигуратора тарифа (как в боте)."""
    try:
        internal = await remnawave.get_internal_squads()
        external = await remnawave.get_external_squads()
        available = await remnawave.get_squads_available()
    except Exception:
        return {"internal": [], "external": [], "available": False}
    return {
        "internal": [{"uuid": str(s.uuid), "name": s.name} for s in internal],
        "external": [{"uuid": str(s.uuid), "name": s.name} for s in external],
        "available": bool(available),
    }


@router.get("/{plan_id}")
@inject
async def get_plan(
    plan_id: int,
    _admin: AdminUser,
    plan_dao: FromDishka[PlanDao],
) -> dict[str, Any]:
    plan = await plan_dao.get_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тариф не найден")
    return _plan_to_dict(plan)


class PlanPriceRequest(BaseModel):
    currency: str
    price: str


class PlanDurationRequest(BaseModel):
    days: int
    order_index: int = 0
    prices: list[PlanPriceRequest] = []


class CreatePlanRequest(BaseModel):
    name: str
    description: Optional[str] = None
    tag: Optional[str] = None
    public_code: Optional[str] = None
    type: str = "BOTH"
    availability: str = "ALL"
    traffic_limit_strategy: str = "NO_RESET"
    traffic_limit: int = 0
    device_limit: int = 1
    allowed_telegram_ids: list[int] = []
    allowed_emails: list[str] = []
    internal_squads: list[str] = []
    external_squad: Optional[str] = None
    order_index: int = 0
    is_active: bool = False
    is_trial: bool = False
    durations: list[PlanDurationRequest] = []


class UpdatePlanRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tag: Optional[str] = None
    public_code: Optional[str] = None
    type: Optional[str] = None
    availability: Optional[str] = None
    traffic_limit_strategy: Optional[str] = None
    traffic_limit: Optional[int] = None
    device_limit: Optional[int] = None
    allowed_telegram_ids: Optional[list[int]] = None
    allowed_emails: Optional[list[str]] = None
    internal_squads: Optional[list[str]] = None
    external_squad: Optional[str] = None
    # external_squad=None неоднозначен (не задан vs. сброс) — используем sentinel-флаг
    clear_external_squad: bool = False
    order_index: Optional[int] = None
    is_active: Optional[bool] = None
    is_trial: Optional[bool] = None
    durations: Optional[list[PlanDurationRequest]] = None


def _parse_uuids(raw: list[str]) -> list[UUID]:
    try:
        return [UUID(str(x)) for x in raw]
    except (ValueError, AttributeError, TypeError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Некорректный UUID сквада: {e}")


def _parse_uuid(raw: Optional[str]) -> Optional[UUID]:
    if not raw:
        return None
    try:
        return UUID(str(raw))
    except (ValueError, AttributeError, TypeError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Некорректный UUID сквада: {e}")


def _build_durations(raw: list[PlanDurationRequest]) -> list[PlanDurationDto]:
    result = []
    for d in raw:
        prices = []
        for p in d.prices:
            try:
                cur = Currency(p.currency.upper())
            except ValueError:
                continue
            prices.append(PlanPriceDto(id=0, currency=cur, price=Decimal(p.price)))
        result.append(PlanDurationDto(id=0, days=d.days, order_index=d.order_index, prices=prices))
    return result


@router.post("", status_code=status.HTTP_201_CREATED)
@inject
async def create_plan(
    body: CreatePlanRequest,
    _admin: AdminUser,
    plan_dao: FromDishka[PlanDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    try:
        plan_type = PlanType(body.type.upper())
        plan_avail = PlanAvailability(body.availability.upper())
        tls = TrafficLimitStrategy(body.traffic_limit_strategy.upper())
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    plan = PlanDto(
        id=0,
        name=body.name,
        description=body.description,
        tag=body.tag,
        public_code=body.public_code,
        type=plan_type,
        availability=plan_avail,
        traffic_limit_strategy=tls,
        traffic_limit=body.traffic_limit,
        device_limit=body.device_limit,
        allowed_telegram_ids=body.allowed_telegram_ids,
        allowed_emails=body.allowed_emails,
        internal_squads=_parse_uuids(body.internal_squads),
        external_squad=_parse_uuid(body.external_squad),
        order_index=body.order_index,
        is_active=body.is_active,
        is_trial=body.is_trial,
        durations=_build_durations(body.durations),
    )
    created = await plan_dao.create(plan)
    await session.commit()
    return _plan_to_dict(created)


@router.put("/{plan_id}")
@inject
async def update_plan(
    plan_id: int,
    body: UpdatePlanRequest,
    _admin: AdminUser,
    plan_dao: FromDishka[PlanDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    plan = await plan_dao.get_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тариф не найден")

    if body.name is not None:
        plan.name = body.name
    if body.description is not None:
        plan.description = body.description
    if body.tag is not None:
        plan.tag = body.tag
    if body.public_code is not None:
        plan.public_code = body.public_code
    if body.type is not None:
        plan.type = PlanType(body.type.upper())
    if body.availability is not None:
        plan.availability = PlanAvailability(body.availability.upper())
    if body.traffic_limit_strategy is not None:
        plan.traffic_limit_strategy = TrafficLimitStrategy(body.traffic_limit_strategy.upper())
    if body.traffic_limit is not None:
        plan.traffic_limit = body.traffic_limit
    if body.device_limit is not None:
        plan.device_limit = body.device_limit
    if body.allowed_telegram_ids is not None:
        plan.allowed_telegram_ids = body.allowed_telegram_ids
    if body.allowed_emails is not None:
        plan.allowed_emails = body.allowed_emails
    if body.internal_squads is not None:
        plan.internal_squads = _parse_uuids(body.internal_squads)
    if body.clear_external_squad:
        plan.external_squad = None
    elif body.external_squad is not None:
        plan.external_squad = _parse_uuid(body.external_squad)
    if body.order_index is not None:
        plan.order_index = body.order_index
    if body.is_active is not None:
        plan.is_active = body.is_active
    if body.is_trial is not None:
        plan.is_trial = body.is_trial
    if body.durations is not None:
        plan.durations = _build_durations(body.durations)

    updated = await plan_dao.update(plan)
    if not updated:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Не удалось обновить")
    await session.commit()
    return _plan_to_dict(updated)


@router.put("/{plan_id}/toggle")
@inject
async def toggle_plan(
    plan_id: int,
    _admin: AdminUser,
    plan_dao: FromDishka[PlanDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    plan = await plan_dao.get_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тариф не найден")
    updated = await plan_dao.update_status(plan_id, not plan.is_active)
    if not updated:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Не удалось обновить")
    await session.commit()
    return {"id": plan_id, "is_active": updated.is_active}


@router.delete("/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def delete_plan(
    plan_id: int,
    _admin: AdminUser,
    plan_dao: FromDishka[PlanDao],
    session: FromDishka[AsyncSession],
) -> None:
    plan = await plan_dao.get_by_id(plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тариф не найден")
    await plan_dao.delete(plan_id)
    await session.commit()
