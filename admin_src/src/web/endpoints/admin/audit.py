"""Аудит-лог админ-действий: кто/когда менял что (изменяющие админ-запросы).

Записи кладёт мидлварь (см. overlay_app), здесь — только чтение для админки.
"""

from datetime import date, timedelta
from typing import Any, Optional

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ._common import AdminUser

router = APIRouter(prefix="/audit", tags=["Admin - Audit"])


@router.get("")
@inject
async def list_audit(
    _admin: AdminUser,
    session: FromDishka[AsyncSession],
    limit: int = 100,
    offset: int = 0,
    actor: Optional[str] = Query(None, description="фильтр по действующему (ILIKE)"),
    method: Optional[str] = Query(None, description="GET/POST/PUT/DELETE/PATCH"),
    path: Optional[str] = Query(None, description="фильтр по пути (ILIKE)"),
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD включительно"),
) -> dict[str, Any]:
    limit = max(1, min(limit, 500))
    offset = max(0, offset)

    where: list[str] = []
    params: dict[str, Any] = {"l": limit, "o": offset}
    if actor:
        where.append("actor ILIKE :actor")
        params["actor"] = f"%{actor.strip()}%"
    if method:
        where.append("method = :method")
        params["method"] = method.strip().upper()
    if path:
        where.append("path ILIKE :path")
        params["path"] = f"%{path.strip()}%"
    if date_from:
        try:
            params["df"] = date.fromisoformat(date_from)
            where.append("created_at >= :df")
        except ValueError:
            pass
    if date_to:
        try:
            params["dt"] = date.fromisoformat(date_to) + timedelta(days=1)
            where.append("created_at < :dt")
        except ValueError:
            pass
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    rows = (
        await session.execute(
            text(
                "SELECT id, actor, method, path, status, created_at "
                f"FROM admin_audit_log{where_sql} ORDER BY created_at DESC "
                "LIMIT :l OFFSET :o"
            ),
            params,
        )
    ).mappings().all()
    return {
        "items": [
            {
                "id": r["id"],
                "actor": r["actor"],
                "method": r["method"],
                "path": r["path"],
                "status": r["status"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]
    }
