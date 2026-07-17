"""Public: активная скидка на первую покупку для текущего юзера (для баннера-таймера).

Скидку выдаёт крон taskiq/tasks/trial_discount.py триальщикам за N дней до конца
триала. Здесь кабинет спрашивает: есть ли у меня сейчас действующая скидка и до
какого времени — чтобы показать баннер с обратным отсчётом и кнопкой «оплатить».
"""

from typing import Any

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.web.endpoints.public._common import CurrentUser

router = APIRouter(prefix="/trial-discount", tags=["Public - Trial Discount"])


@router.get("")
@inject
async def get_my_trial_discount(
    user: CurrentUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    # Скидка активна, если промо не истекло, не помечено использованным и всё ещё
    # реально висит на юзере (purchase_discount не обнулился первой покупкой).
    row = (
        await session.execute(
            text(
                "SELECT td.percent, td.expires_at "
                "FROM trial_discounts td JOIN users u ON u.id = td.user_id "
                "WHERE td.user_id = :uid AND td.used = false "
                "AND td.expires_at > now() AND u.purchase_discount >= td.percent"
            ),
            {"uid": user.id},
        )
    ).first()
    if not row:
        return {"active": False}
    percent, expires_at = row
    return {
        "active": True,
        "percent": percent,
        "expires_at": expires_at.isoformat() if expires_at else None,
    }
