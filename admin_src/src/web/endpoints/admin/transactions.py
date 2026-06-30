from typing import Any, Optional

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, Query

from src.application.common.dao import TransactionDao, UserDao
from src.core.enums import TransactionStatus

from ._common import AdminUser
from ._redact import is_readonly_admin, redact_transaction

router = APIRouter(prefix="/transactions", tags=["Admin - Transactions"])


@router.get("")
@inject
async def list_transactions(
    admin: AdminUser,
    transaction_dao: FromDishka[TransactionDao],
    user_dao: FromDishka[UserDao],
    limit: int = Query(default=25, le=100),
    offset: int = Query(default=0, ge=0),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    gateway: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    total = await transaction_dao.count_total()

    if status_filter:
        try:
            tx_status = TransactionStatus(status_filter.upper())
            transactions = await transaction_dao.get_by_status(tx_status)
        except ValueError:
            transactions = await transaction_dao.get_all(limit=limit, offset=offset)
            total = len(transactions)
    else:
        transactions = await transaction_dao.get_all(limit=limit, offset=offset)

    if gateway:
        transactions = [t for t in transactions if str(t.gateway_type).upper() == gateway.upper()]

    user_ids = list({t.user_id for t in transactions})
    users_list = await user_dao.get_by_ids(user_ids) if user_ids else []
    users_map = {u.id: u for u in users_list}

    items = []
    for t in transactions:
        u = users_map.get(t.user_id)
        items.append(
            {
                "payment_id": str(t.payment_id),
                "user_id": t.user_id,
                "user_name": u.name if u else None,
                "user_email": u.email if u else None,
                "status": t.status,
                "gateway_type": t.gateway_type,
                "purchase_type": t.purchase_type,
                "is_test": t.is_test,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
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
