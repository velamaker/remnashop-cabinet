"""Public: активный промо-баннер для текущего юзера (кабинет показывает его).

Учитывает тумблер, окно показа (starts_at/ends_at) и аудиторию (все / без подписки /
с подпиской / триал / истекающие). Настраивается в админке (admin/promo_banner.py).
"""

from datetime import datetime, timezone
from typing import Any

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.services.overlay_promo_banner import load_config
from src.web.endpoints.public._common import CurrentUser

router = APIRouter(prefix="/promo-banner", tags=["Public - Promo Banner"])

_INACTIVE: dict[str, Any] = {"active": False}


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


async def _audience_matches(audience: str, session: AsyncSession, user_id: int) -> bool:
    if audience == "all":
        return True
    row = (
        await session.execute(
            text(
                "SELECT s.status, s.is_trial, s.expire_at "
                "FROM users u LEFT JOIN subscriptions s ON u.current_subscription_id = s.id "
                "WHERE u.id = :uid"
            ),
            {"uid": user_id},
        )
    ).first()
    status = row[0] if row else None
    is_trial = row[1] if row else None
    expire_at = row[2] if row else None
    active = status == "ACTIVE" and expire_at is not None and expire_at > datetime.now(timezone.utc)
    if audience == "no_sub":
        return not active
    if audience == "has_sub":
        return active
    if audience == "trial":
        return bool(active and is_trial)
    if audience == "expiring":
        if not active:
            return False
        days_left = (expire_at - datetime.now(timezone.utc)).days
        return days_left <= 3
    return False


@router.get("")
@inject
async def get_promo_banner(
    user: CurrentUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    cfg = load_config()
    if not cfg["enabled"] or not (cfg["title"] or cfg["text"]):
        return _INACTIVE

    now = datetime.now(timezone.utc)
    starts = _parse_iso(cfg["starts_at"])
    ends = _parse_iso(cfg["ends_at"])
    if starts and now < starts:
        return _INACTIVE
    if ends and now > ends:
        return _INACTIVE

    if not await _audience_matches(cfg["audience"], session, user.id):
        return _INACTIVE

    return {
        "active": True,
        "title": cfg["title"],
        "text": cfg["text"],
        "cta_text": cfg["cta_text"],
        "cta_url": cfg["cta_url"],
        "color": cfg["color"],
        "dismissible": cfg["dismissible"],
        # версия для дедупа «скрыто» на клиенте — по контенту
        "version": str(abs(hash((cfg["title"], cfg["text"], cfg["ends_at"]))) % (10**8)),
    }
