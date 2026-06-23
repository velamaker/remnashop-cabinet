from typing import Any

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.dao import PaymentGatewayDao

from ._common import AdminUser

router = APIRouter(prefix="/gateways", tags=["Admin - Gateways"])


def _gateway_to_dict(g: Any) -> dict[str, Any]:
    gtype = g.type.value if hasattr(g.type, "value") else str(g.type)
    currency = g.currency.value if hasattr(g.currency, "value") else str(g.currency)
    is_configured = g.settings.is_configured if g.settings else False
    return {
        "id": g.id,
        "type": gtype,
        "currency": currency,
        "is_active": g.is_active,
        "is_configured": is_configured,
        "order_index": g.order_index,
        "display_name": g.settings.display_name if g.settings else None,
    }


@router.get("")
@inject
async def list_gateways(
    _admin: AdminUser,
    gateway_dao: FromDishka[PaymentGatewayDao],
) -> dict[str, Any]:
    gateways = await gateway_dao.get_all(only_active=False, sorted=True)
    return {"items": [_gateway_to_dict(g) for g in gateways], "total": len(gateways)}


class ToggleGatewayRequest(BaseModel):
    is_active: bool


@router.put("/{gateway_id}/toggle")
@inject
async def toggle_gateway(
    gateway_id: int,
    body: ToggleGatewayRequest,
    _admin: AdminUser,
    gateway_dao: FromDishka[PaymentGatewayDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    gateway = await gateway_dao.get_by_id(gateway_id)
    if not gateway:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gateway not found")

    if body.is_active and gateway.settings and not gateway.settings.is_configured:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Gateway is not configured. Set up credentials first.",
        )

    await gateway_dao.set_active_status(gateway.type, body.is_active)
    await session.commit()
    return {"id": gateway_id, "is_active": body.is_active}
