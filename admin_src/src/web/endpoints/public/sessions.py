"""Public: активные сессии/входы юзера + «выйти со всех устройств».

Список — из login_events (пишет middleware при каждом входе). «Выйти со всех» —
помечает все токены до текущего момента недействительными (session_invalidations),
проверяет middleware overlay_app. Текущую куку тоже чистим → юзер входит заново.
"""

from typing import Any

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.services.overlay_sessions import invalidate_all
from src.web.endpoints.public._common import CurrentUser

router = APIRouter(prefix="/sessions", tags=["Public - Sessions"])


@router.get("")
@inject
async def list_sessions(
    user: CurrentUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    # Скрываем входы старше последнего «Выйти со всех» — их токены уже
    # недействительны (см. overlay_sessions.token_invalidated), это не активные
    # сессии. Дедуп по устройству делает фронт (нормализованный UA + ip).
    rows = (
        await session.execute(
            text(
                "SELECT le.ip, le.user_agent, le.method, le.created_at "
                "FROM login_events le "
                "LEFT JOIN session_invalidations si ON si.user_id = le.user_id "
                "WHERE le.user_id = :uid "
                "  AND (si.invalidated_at IS NULL OR le.created_at >= si.invalidated_at) "
                "ORDER BY le.created_at DESC LIMIT 50"
            ),
            {"uid": user.id},
        )
    ).mappings().all()
    return {
        "items": [
            {
                "ip": r["ip"],
                "user_agent": r["user_agent"],
                "method": r["method"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]
    }


@router.post("/logout-all")
@inject
async def logout_all(
    user: CurrentUser,
    session: FromDishka[AsyncSession],
    response: Response,
) -> dict[str, Any]:
    await invalidate_all(session, user.id)
    # Текущую сессию тоже завершаем — юзер войдёт заново.
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    return {"ok": True}
