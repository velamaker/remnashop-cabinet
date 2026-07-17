"""«Выйти со всех устройств» — инвалидация сессий по времени (overlay).

Токен с claim iat раньше пользовательского invalidated_at считается недействительным.
Проверку делает middleware (overlay_app). Чтобы не долбить БД на каждый запрос —
кэш всех инвалидаций в памяти с TTL; на logout-all кэш обновляется сразу.
"""

from __future__ import annotations

import time
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_CACHE: dict[int, float] = {}  # user_id → invalidated_at (unix ts)
_LAST_LOAD: float = 0.0
_TTL = 20.0  # сек между полными перезагрузками кэша


async def _reload(session: AsyncSession) -> None:
    global _CACHE, _LAST_LOAD
    rows = (
        await session.execute(text("SELECT user_id, invalidated_at FROM session_invalidations"))
    ).all()
    _CACHE = {int(uid): ts.timestamp() for uid, ts in rows}
    _LAST_LOAD = time.time()


async def token_invalidated(session: AsyncSession, user_id: int, iat: Optional[int]) -> bool:
    """True — токен недействителен (iat раньше invalidated_at)."""
    global _LAST_LOAD
    if iat is None:
        return False
    if time.time() - _LAST_LOAD > _TTL:
        try:
            await _reload(session)
        except Exception:  # noqa: BLE001 — не роняем запрос из-за кэша
            return False
    inv = _CACHE.get(int(user_id))
    return inv is not None and float(iat) < inv


async def invalidate_all(session: AsyncSession, user_id: int) -> None:
    """Помечает все текущие сессии юзера недействительными (logout-all)."""
    await session.execute(
        text(
            "INSERT INTO session_invalidations (user_id, invalidated_at) VALUES (:u, now()) "
            "ON CONFLICT (user_id) DO UPDATE SET invalidated_at = now()"
        ),
        {"u": user_id},
    )
    await session.commit()
    _CACHE[int(user_id)] = time.time()  # сразу в кэш
