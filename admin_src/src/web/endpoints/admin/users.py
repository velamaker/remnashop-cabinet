from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Remnawave
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
    session: FromDishka[AsyncSession],
    limit: int = Query(default=25, le=100),
    offset: int = Query(default=0, ge=0),
    search: Optional[str] = Query(default=None),
    blocked: Optional[bool] = Query(default=None),
    role: Optional[int] = Query(default=None),
    sort: str = Query(default="created_at"),
    order: str = Query(default="desc"),
) -> dict[str, Any]:
    # Гибкий фильтр+сортировка. last_login берём из login_events (LEFT JOIN),
    # чтобы можно было сортировать по дате последнего входа.
    where: list[str] = []
    params: dict[str, Any] = {}
    if search:
        where.append("(u.name ILIKE :s OR u.email ILIKE :s OR u.username ILIKE :s)")
        params["s"] = f"%{search.strip()}%"
    if blocked is not None:
        where.append("u.is_blocked = :bl")
        params["bl"] = blocked
    if role is not None:
        try:
            params["r"] = Role(role).name
            where.append("u.role = :r")
        except ValueError:
            pass
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    # Колонка сортировки — строго из белого списка (без SQL-инъекций).
    sort_col = {
        "created_at": "u.created_at",
        "last_login": "ll.last_login",
        "name": "lower(u.name)",
    }.get(sort, "u.created_at")
    direction = "ASC" if str(order).lower() == "asc" else "DESC"
    nulls = "NULLS LAST" if direction == "DESC" else "NULLS FIRST"

    total = (
        await session.execute(text(f"SELECT count(*) FROM users u {where_sql}"), params)
    ).scalar_one()

    rows = (
        await session.execute(
            text(
                f"""
                SELECT u.id AS id, ll.last_login AS last_login
                FROM users u
                LEFT JOIN (
                    SELECT user_id, max(created_at) AS last_login
                    FROM login_events GROUP BY user_id
                ) ll ON ll.user_id = u.id
                {where_sql}
                ORDER BY {sort_col} {direction} {nulls}, u.id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {**params, "limit": limit, "offset": offset},
        )
    ).all()

    ids = [r.id for r in rows]
    last_map = {r.id: r.last_login for r in rows}
    by_id: dict[int, UserDto] = {}
    if ids:
        for u in await user_dao.get_by_ids(ids):
            by_id[u.id] = u

    items = []
    for uid in ids:  # сохраняем порядок сортировки
        u = by_id.get(uid)
        if u is None:
            continue
        d = _user_to_dict(u)
        m = last_map.get(uid)
        d["last_login_at"] = m.isoformat() if m else None
        items.append(d)

    if is_readonly_admin(admin):
        items = [redact_user(it) for it in items]

    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.get("/{user_id}")
@inject
async def get_user(
    user_id: int,
    admin: AdminUser,
    user_dao: FromDishka[UserDao],
    subscription_dao: FromDishka[SubscriptionDao],
    transaction_dao: FromDishka[TransactionDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    user = await user_dao.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    current_sub = await subscription_dao.get_current(user_id)
    all_subs = await subscription_dao.get_all_by_user(user_id)
    login_stats = (
        await session.execute(
            text(
                "SELECT count(*) AS total, count(DISTINCT ip) AS ips, max(created_at) AS last "
                "FROM login_events WHERE user_id = :u"
            ),
            {"u": user_id},
        )
    ).first()
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
        "logins": {
            "total": login_stats.total if login_stats else 0,
            "distinct_ips": login_stats.ips if login_stats else 0,
            "last_login_at": login_stats.last.isoformat()
            if login_stats and login_stats.last
            else None,
        },
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


@router.get("/{user_id}/logins")
@inject
async def user_logins(
    user_id: int,
    admin: AdminUser,
    session: FromDishka[AsyncSession],
    limit: int = Query(default=50, le=200),
) -> dict[str, Any]:
    """История входов: всего, уникальных IP, последние события (время/IP/способ).

    Для read-only админа сами IP-адреса маскируются (видны только счётчики)."""
    readonly = is_readonly_admin(admin)
    summary = (
        await session.execute(
            text(
                "SELECT count(*) AS total, count(DISTINCT ip) AS ips, max(created_at) AS last "
                "FROM login_events WHERE user_id = :u"
            ),
            {"u": user_id},
        )
    ).first()
    rows = (
        await session.execute(
            text(
                "SELECT ip, user_agent, method, created_at FROM login_events "
                "WHERE user_id = :u ORDER BY created_at DESC LIMIT :l"
            ),
            {"u": user_id, "l": limit},
        )
    ).all()
    return {
        "total": summary.total if summary else 0,
        "distinct_ips": summary.ips if summary else 0,
        "last_login_at": summary.last.isoformat() if summary and summary.last else None,
        "items": [
            {
                "ip": None if readonly else r.ip,
                "user_agent": r.user_agent,
                "method": r.method,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


@router.get("/{user_id}/traffic-by-node")
@inject
async def user_traffic_by_node(
    user_id: int,
    _admin: AdminUser,
    subscription_dao: FromDishka[SubscriptionDao],
    remnawave: FromDishka[Remnawave],
    days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    """Расход трафика пользователя по нодам за последние N дней.

    Данные — живьём из API панели Remnawave (bandwidthstats), в БД не хранятся.
    Возвращаем список нод с трафиком (по убыванию) и суммарный объём."""
    empty = {"available": False, "days": days, "total": 0, "nodes": []}

    current = await subscription_dao.get_current(user_id)
    if not current or not getattr(current, "user_remna_id", None):
        return empty

    sdk = getattr(remnawave, "sdk", None)
    if sdk is None:
        return empty

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    try:
        result = await sdk.bandwidthstats.get_stats_user_usage(
            uuid=str(current.user_remna_id),
            top_nodes_limit=50,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"RemnaWave error: {e}")

    data = getattr(result, "root", result)
    top_nodes = getattr(data, "top_nodes", None) or []
    nodes = [
        {
            "name": n.name,
            "country_code": getattr(n, "country_code", "") or "",
            "total": int(getattr(n, "total", 0) or 0),
        }
        for n in top_nodes
    ]
    nodes.sort(key=lambda x: x["total"], reverse=True)

    return {
        "available": True,
        "days": days,
        "total": sum(n["total"] for n in nodes),
        "nodes": nodes,
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
