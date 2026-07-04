from datetime import date as _date, timedelta
from typing import Any, Optional

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.dao import UserDao

from ._common import AdminUser
from ._redact import is_readonly_admin, redact_transaction

router = APIRouter(prefix="/transactions", tags=["Admin - Transactions"])


@router.get("")
@inject
async def list_transactions(
    admin: AdminUser,
    user_dao: FromDishka[UserDao],
    session: FromDishka[AsyncSession],
    limit: int = Query(default=25, le=100),
    offset: int = Query(default=0, ge=0),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    gateway: Optional[str] = Query(default=None),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    # Фильтры: статус, шлюз, период (date_from..date_to включительно) + пагинация.
    where: list[str] = []
    params: dict[str, Any] = {}
    if status_filter:
        where.append("upper(status::text) = :st")
        params["st"] = status_filter.upper()
    if gateway:
        where.append("upper(gateway_type::text) = :gw")
        params["gw"] = gateway.upper()
    if date_from:
        try:
            params["df"] = _date.fromisoformat(date_from)
            where.append("created_at >= :df")
        except ValueError:
            pass
    if date_to:
        try:
            # включительно: < (дата_до + 1 день)
            params["dt"] = _date.fromisoformat(date_to) + timedelta(days=1)
            where.append("created_at < :dt")
        except ValueError:
            pass
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    total = (
        await session.execute(text(f"SELECT count(*) FROM transactions {where_sql}"), params)
    ).scalar_one()
    rows = (
        await session.execute(
            text(
                f"""
                SELECT payment_id, status, is_test, purchase_type, gateway_type,
                       created_at, updated_at, user_id,
                       pricing->>'final_amount' AS final_amount,
                       currency::text AS currency,
                       plan_snapshot->>'name' AS plan_name,
                       plan_snapshot->>'duration' AS plan_duration
                FROM transactions {where_sql}
                ORDER BY created_at DESC NULLS LAST
                LIMIT :limit OFFSET :offset
                """
            ),
            {**params, "limit": limit, "offset": offset},
        )
    ).all()

    user_ids = list({r.user_id for r in rows if r.user_id is not None})
    users_map = {u.id: u for u in (await user_dao.get_by_ids(user_ids) if user_ids else [])}

    items = []
    for r in rows:
        u = users_map.get(r.user_id)
        items.append(
            {
                "payment_id": str(r.payment_id),
                "user_id": r.user_id,
                "user_name": u.name if u else None,
                "user_email": u.email if u else None,
                "status": r.status,
                "gateway_type": r.gateway_type,
                "purchase_type": r.purchase_type,
                "is_test": r.is_test,
                "amount": r.final_amount,
                "currency": r.currency,
                "plan_name": r.plan_name,
                "plan_duration": int(r.plan_duration) if r.plan_duration else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
        )

    if is_readonly_admin(admin):
        items = [redact_transaction(it) for it in items]

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
    }
