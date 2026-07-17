"""Public: заморозка (пауза) подписки юзером.

Пауза: сохраняем остаток срока (remaining_seconds) и отключаем юзера в панели
(disable_user). Возобновление: expire = now + остаток, включаем (enable_user).
Дни на паузе не сгорают. Лимит длительности — max_days (крон авто-возобновляет).
Настраивается тумблером (assets/freeze.json). Крон — taskiq/tasks/freeze.py.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Remnawave
from src.infrastructure.services.overlay_freeze import load_config
from src.web.endpoints.public._common import CurrentUser

router = APIRouter(prefix="/subscription", tags=["Public - Freeze"])


async def _current_sub(session: AsyncSession, user_id: int):
    return (
        await session.execute(
            text(
                "SELECT s.user_remna_id, s.expire_at, s.status "
                "FROM users u JOIN subscriptions s ON u.current_subscription_id = s.id "
                "WHERE u.id = :uid"
            ),
            {"uid": user_id},
        )
    ).first()


@router.get("/freeze-status")
@inject
async def freeze_status(
    user: CurrentUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    cfg = load_config()
    fr = (
        await session.execute(
            text(
                "SELECT remaining_seconds FROM subscription_freezes "
                "WHERE user_id = :uid AND active = true"
            ),
            {"uid": user.id},
        )
    ).first()
    if fr:
        return {
            "enabled": cfg["enabled"],
            "frozen": True,
            "remaining_days": max(0, int(fr[0]) // 86400),
            "max_days": cfg["max_days"],
            "can_freeze": False,
        }
    sub = await _current_sub(session, user.id)
    now = datetime.now(timezone.utc)
    active = bool(sub and sub[0] and sub[1] and sub[1] > now and sub[2] == "ACTIVE")
    days_left = int((sub[1] - now).total_seconds() // 86400) if sub and sub[1] and sub[1] > now else 0
    return {
        "enabled": cfg["enabled"],
        "frozen": False,
        "can_freeze": bool(cfg["enabled"] and active),
        "max_days": cfg["max_days"],
        "days_left": max(0, days_left),
    }


@router.post("/freeze")
@inject
async def freeze(
    user: CurrentUser,
    session: FromDishka[AsyncSession],
    remnawave: FromDishka[Remnawave],
) -> dict[str, Any]:
    cfg = load_config()
    if not cfg["enabled"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Заморозка отключена")
    if (
        await session.execute(
            text("SELECT 1 FROM subscription_freezes WHERE user_id = :uid AND active = true"),
            {"uid": user.id},
        )
    ).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Подписка уже на паузе")

    sub = await _current_sub(session, user.id)
    if not sub or not sub[0] or not sub[1]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Нет активной подписки")
    remna_uuid, expire_at, st = sub
    now = datetime.now(timezone.utc)
    remaining = int((expire_at - now).total_seconds())
    if st != "ACTIVE" or remaining <= 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Подписка неактивна — заморозить нельзя")

    sdk = getattr(remnawave, "sdk", None)
    if sdk is None:
        raise HTTPException(status_code=500, detail="Панель недоступна")
    try:
        await sdk.users.disable_user(str(remna_uuid))
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="Не удалось заморозить подписку")

    await session.execute(
        text(
            "INSERT INTO subscription_freezes (user_id, remna_uuid, frozen_at, remaining_seconds, active) "
            "VALUES (:u, :ru, now(), :rs, true) "
            "ON CONFLICT (user_id) DO UPDATE SET remna_uuid = :ru, frozen_at = now(), "
            "remaining_seconds = :rs, active = true"
        ),
        {"u": user.id, "ru": str(remna_uuid), "rs": remaining},
    )
    await session.commit()
    return {"frozen": True, "remaining_days": max(0, remaining // 86400)}


@router.post("/unfreeze")
@inject
async def unfreeze(
    user: CurrentUser,
    session: FromDishka[AsyncSession],
    remnawave: FromDishka[Remnawave],
) -> dict[str, Any]:
    fr = (
        await session.execute(
            text(
                "SELECT remna_uuid, remaining_seconds FROM subscription_freezes "
                "WHERE user_id = :uid AND active = true"
            ),
            {"uid": user.id},
        )
    ).first()
    if not fr:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Подписка не на паузе")
    remna_uuid, remaining = fr

    sdk = getattr(remnawave, "sdk", None)
    if sdk is None:
        raise HTTPException(status_code=500, detail="Панель недоступна")

    from remnapy.models import UpdateUserRequestDto

    new_expire = datetime.now(timezone.utc) + timedelta(seconds=int(remaining))
    try:
        # Сначала будущий срок (пока disabled), затем включаем — без промежуточного EXPIRED.
        await sdk.users.update_user(UpdateUserRequestDto(uuid=str(remna_uuid), expire_at=new_expire))
        await sdk.users.enable_user(str(remna_uuid))
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="Не удалось возобновить подписку")

    await session.execute(
        text("UPDATE subscription_freezes SET active = false WHERE user_id = :uid"),
        {"uid": user.id},
    )
    await session.commit()
    return {"frozen": False, "expire_at": new_expire.isoformat()}
