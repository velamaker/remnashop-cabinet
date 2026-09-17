"""Public: действующая скидка на продление у текущего пользователя.

Скидку выдаёт крон taskiq/tasks/renewal_discount.py за N дней до конца платной
подписки. Кабинет спрашивает, есть ли она сейчас и до какого времени, и
показывает её ВНУТРИ плашки продления на Главной и отдельным блоком на оплате.

Активна — если выдача открыта, срок не вышел и скидка всё ещё висит на человеке:
база гасит `purchase_discount` покупкой раньше, чем крон пометит выдачу `used`,
и без этого условия плашка звала бы продлить со скидкой, которой уже нет.
"""

from typing import Any

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.web.endpoints.public._common import CurrentUser

router = APIRouter(prefix="/renewal-discount", tags=["Public - Renewal Discount"])


@router.get("")
@inject
async def get_my_renewal_discount(
    user: CurrentUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    try:
        row = (
            await session.execute(
                text(
                    "SELECT g.percent, g.expires_at "
                    "FROM renewal_discount_grants g JOIN users u ON u.id = g.user_id "
                    "WHERE g.user_id = :uid AND g.status = 'active' "
                    "AND g.expires_at > now() AND u.purchase_discount >= g.percent "
                    "ORDER BY g.expires_at DESC LIMIT 1"
                ),
                {"uid": user.id},
            )
        ).first()
    except Exception as exc:  # noqa: BLE001 — плашка необязательна: нет таблицы → нет скидки
        logger.debug(f"renewal_discount: статус для кабинета не прочитан: {exc}")
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {"active": False}
    if not row:
        return {"active": False}
    percent, expires_at = row
    return {
        "active": True,
        "percent": percent,
        "expires_at": expires_at.isoformat() if expires_at else None,
    }
