"""Загрузка/сохранение гранта прав из таблицы admin_grants (overlay)."""

from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def load_grant(session: AsyncSession, user_id: int) -> Optional[dict[str, Any]]:
    """Строка admin_grants как dict, или None если гранта нет."""
    row = (
        await session.execute(
            text(
                "SELECT user_id, full_access, can_write, sections, expires_at, "
                "granted_by, updated_at FROM admin_grants WHERE user_id = :u"
            ),
            {"u": user_id},
        )
    ).first()
    if row is None:
        return None
    return {
        "user_id": row.user_id,
        "full_access": row.full_access,
        "can_write": row.can_write,
        "sections": row.sections or [],
        "expires_at": row.expires_at,
        "granted_by": row.granted_by,
        "updated_at": row.updated_at,
    }


async def upsert_grant(
    session: AsyncSession,
    user_id: int,
    *,
    full_access: bool,
    can_write: bool,
    sections: list[str],
    expires_at: Any,
    granted_by: Optional[str],
) -> None:
    """Создать/обновить грант. sections — уже нормализованный список ключей."""
    import json

    await session.execute(
        text(
            """
            INSERT INTO admin_grants
                (user_id, full_access, can_write, sections, expires_at, granted_by, updated_at)
            VALUES
                (:u, :fa, :cw, CAST(:secs AS jsonb), :exp, :by, now())
            ON CONFLICT (user_id) DO UPDATE SET
                full_access = EXCLUDED.full_access,
                can_write   = EXCLUDED.can_write,
                sections    = EXCLUDED.sections,
                expires_at  = EXCLUDED.expires_at,
                granted_by  = EXCLUDED.granted_by,
                updated_at  = now()
            """
        ),
        {
            "u": user_id,
            "fa": full_access,
            "cw": can_write,
            "secs": json.dumps(sections),
            "exp": expires_at,
            "by": granted_by,
        },
    )


async def delete_grant(session: AsyncSession, user_id: int) -> None:
    await session.execute(
        text("DELETE FROM admin_grants WHERE user_id = :u"), {"u": user_id}
    )
