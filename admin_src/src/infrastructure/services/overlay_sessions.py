"""Отзыв сессий (overlay): «выйти со всех устройств» и «выйти здесь».

Два разных отзыва:
  • logout-all — токен с claim iat раньше пользовательского invalidated_at недействителен;
  • обычный «Выйти» — недействителен один конкретный токен (revoked_tokens).
Проверку делает middleware (overlay_app). Чтобы не долбить БД на каждый запрос — кэш
в памяти с TTL; тот процесс, который выполнил отзыв, обновляет свой кэш сразу, соседняя
копия бэкенда (локальный HA) увидит отзыв в пределах TTL.
"""

from __future__ import annotations

import hashlib
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
            # FAIL-CLOSED по отзыву: НЕ возвращаем False (это трактовало бы отозванный
            # токен как валидный при сбое БД). Используем последний известный кэш — уже
            # известные инвалидации продолжают действовать. Обновим при следующем запросе.
            pass
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


# --- отзыв одного токена («Выйти» на этом устройстве) ---------------------------

_REVOKED: dict[str, float] = {}  # sha256(token) → exp (unix ts)
_REVOKED_LOADED: float = 0.0


def token_fingerprint(token: str) -> str:
    """Хранить сам токен нельзя — из базы он утёк бы как готовый ключ доступа."""
    return hashlib.sha256(token.encode()).hexdigest()


async def revoke_token(session: AsyncSession, token: str, expires_at: Optional[float]) -> None:
    """Гасит один access-токен до конца его срока (обычный выход).

    `expires_at` — claim exp; без него держим час (дольше наш access не живёт).
    """
    exp = float(expires_at) if expires_at else time.time() + 3600
    fingerprint = token_fingerprint(token)
    await session.execute(
        text(
            "INSERT INTO revoked_tokens (token_hash, expires_at) "
            "VALUES (:h, to_timestamp(:e)) ON CONFLICT (token_hash) DO NOTHING"
        ),
        {"h": fingerprint, "e": exp},
    )
    # Протухшие записи убираем здесь же: отдельного планировщика ради этого не нужно.
    await session.execute(text("DELETE FROM revoked_tokens WHERE expires_at < now()"))
    await session.commit()
    _REVOKED[fingerprint] = exp  # сразу в кэш


async def token_revoked(session: AsyncSession, token: str) -> bool:
    """True — этот токен погашен выходом."""
    global _REVOKED, _REVOKED_LOADED
    if time.time() - _REVOKED_LOADED > _TTL:
        try:
            rows = (
                await session.execute(
                    text("SELECT token_hash, expires_at FROM revoked_tokens WHERE expires_at > now()")
                )
            ).all()
            _REVOKED = {str(h): ts.timestamp() for h, ts in rows}
            _REVOKED_LOADED = time.time()
        except Exception:  # noqa: BLE001 — как и выше, fail-closed: держим прежний кэш
            pass
    exp = _REVOKED.get(token_fingerprint(token))
    return exp is not None and exp > time.time()
