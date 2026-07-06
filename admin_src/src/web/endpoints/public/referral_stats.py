"""Заработок пользователя по реферальной программе — для дашборда в кабинете.

Базовый `/referral/program` (в base-образе) отдаёт код/кол-во приглашённых/уровни,
но НЕ сумму заработка. Достраиваем её overlay-ручкой поверх таблицы referral_rewards
(её пишет базовый AssignReferralRewards): `amount` там — величина награды в единицах
типа (для POINTS/PERCENT это РУБЛИ платёж×%, для EXTRA_DAYS — дни). Суммируем только
уже выданные (is_issued=true). Тип награды фронт уже знает из /referral/program и сам
форматирует (₽ / дни / ≈баллы).
"""

from typing import Any

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.web.endpoints.public._common import CurrentUser

router = APIRouter(prefix="/referral", tags=["Public - Referral"])


@router.get("/earnings")
@inject
async def get_referral_earnings(
    user: CurrentUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                "SELECT COALESCE(SUM(amount), 0) AS earned, COUNT(*) AS rewards "
                "FROM referral_rewards WHERE user_id = :uid AND is_issued = true"
            ),
            {"uid": user.id},
        )
    ).one()
    return {"earned": int(row.earned or 0), "rewards_count": int(row.rewards or 0)}
