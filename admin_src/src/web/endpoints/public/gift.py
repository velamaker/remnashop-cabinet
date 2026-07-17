"""Public: подарить подписку — юзер платит с баланса, получает код-подарок.

Юзер выбирает тариф+длительность → списываем с его ₽-баланса цену тарифа → создаём
ОДНОРАЗОВЫЙ промокод типа SUBSCRIPTION (тот же механизм, что у админ-промокодов) и
отдаём код. Получатель активирует код обычным «ввести промокод» → получает подписку.
При ошибке создания — рефанд. Реюз: race-safe списание баланса + PlanSnapshotDto.
"""

import secrets
from decimal import Decimal
from typing import Any

from adaptix import Retort
from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.dao import PromocodeDao
from src.application.dto import PlanSnapshotDto, PromocodeDto
from src.application.use_cases.user.queries.plans import GetAvailablePlans
from src.core.enums import Currency, PromocodeAvailability, PromocodeRewardType
from src.web.endpoints.public._common import CurrentUser

router = APIRouter(prefix="/gift", tags=["Public - Gift"])


class GiftCreate(BaseModel):
    plan_code: str
    duration_days: int = Field(gt=0, le=3650)


@router.post("/create")
@inject
async def create_gift(
    body: GiftCreate,
    user: CurrentUser,
    session: FromDishka[AsyncSession],
    promocode_dao: FromDishka[PromocodeDao],
    get_available_plans: FromDishka[GetAvailablePlans],
    retort: FromDishka[Retort],
) -> dict[str, Any]:
    from src.web.endpoints.public.subscription import _get_available_plan_by_code

    plan = await _get_available_plan_by_code(user, body.plan_code, get_available_plans)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тариф не найден")
    duration = plan.get_duration(body.duration_days)
    if not duration:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="У тарифа нет такой длительности")

    rub = next((p.price for p in duration.prices if p.currency == Currency.RUB), None)
    if rub is None or Decimal(str(rub)) <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Тариф недоступен в рублях")
    price = Decimal(str(rub))

    # Атомарное списание с баланса дарителя (race-safe).
    row = (
        await session.execute(
            text(
                "UPDATE users SET cabinet_balance = cabinet_balance - :amt "
                "WHERE id = :id AND cabinet_balance >= :amt RETURNING cabinet_balance"
            ),
            {"amt": price, "id": user.id},
        )
    ).first()
    if not row:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Недостаточно средств на балансе")

    code = "GIFT-" + secrets.token_hex(4).upper()
    try:
        snapshot = PlanSnapshotDto.from_plan(plan, body.duration_days)
        plan_snapshot = retort.dump(snapshot)
        promo = PromocodeDto(
            id=0,
            code=code,
            is_active=True,
            reward_type=PromocodeRewardType.SUBSCRIPTION,
            reward=None,
            plan_snapshot=plan_snapshot,
            availability=PromocodeAvailability.ALL,
            is_reusable=False,
            max_activations=1,
            expires_at=None,
        )
        await promocode_dao.create(promo)
        await session.commit()
    except Exception as e:  # noqa: BLE001 — рефанд при любой ошибке
        await session.execute(
            text("UPDATE users SET cabinet_balance = cabinet_balance + :amt WHERE id = :id"),
            {"amt": price, "id": user.id},
        )
        await session.commit()
        logger.warning(f"gift: создание подарка user_id={user.id} упало ({e}), средства возвращены")
        raise HTTPException(status_code=502, detail="Не удалось создать подарок, средства возвращены")

    return {
        "code": code,
        "plan_name": plan.name,
        "duration_days": body.duration_days,
        "price": str(price),
    }
