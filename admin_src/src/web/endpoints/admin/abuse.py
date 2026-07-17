"""Детект абьюза триала (overlay, web-админка).

Тонкая обёртка над общим движком `overlay_abuse_engine.detect_abuse_clusters`
(его же переиспользует раздел абьюза в Telegram-боте, чтобы не дублировать SQL).

Ищем группы аккаунтов с совпадающими идентифицирующими сигналами (IP / HWID /
email / само-реферал), которые успели воспользоваться пробником. Никаких
автодействий: только показываем группы, действия — вручную (см. users.py block/trial).
"""

from typing import Any

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.services.overlay_abuse_engine import detect_abuse_clusters

from ._common import AdminUser

router = APIRouter(prefix="/abuse", tags=["Admin - Abuse"])


@router.get("/trials")
@inject
async def trial_abuse(
    _admin: AdminUser,
    session: FromDishka[AsyncSession],
    min_accounts: int = Query(2, ge=2, le=20),
    only_trial: bool = Query(True, description="показывать только группы, где ≥2 аккаунтов уже взяли триал"),
) -> dict[str, Any]:
    return await detect_abuse_clusters(session, min_accounts=min_accounts, only_trial=only_trial)
