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
from ._redact import is_readonly_admin

router = APIRouter(prefix="/abuse", tags=["Admin - Abuse"])


def _redact_clusters(result: dict[str, Any]) -> dict[str, Any]:
    """Маскировка для read-only админа: в аккаунтах зануляем id/telegram_id/username/
    email (имя оставляем), а ключ кластера — для сигналов, раскрывающих IP/email/
    внутренний id (сам ключ = IP или email или «referrer #id»). Согласовано с redact_user."""
    for c in result.get("clusters", []):
        if not isinstance(c, dict):
            continue
        if c.get("signal") in ("ip", "email", "referral"):
            c["key"] = None
        for a in c.get("accounts", []):
            if isinstance(a, dict):
                for f in ("id", "telegram_id", "username", "email"):
                    if f in a:
                        a[f] = None
    return result


@router.get("/trials")
@inject
async def trial_abuse(
    admin: AdminUser,
    session: FromDishka[AsyncSession],
    min_accounts: int = Query(2, ge=2, le=20),
    only_trial: bool = Query(True, description="показывать только группы, где ≥2 аккаунтов уже взяли триал"),
) -> dict[str, Any]:
    result = await detect_abuse_clusters(session, min_accounts=min_accounts, only_trial=only_trial)
    if is_readonly_admin(admin):
        result = _redact_clusters(result)
    return result
