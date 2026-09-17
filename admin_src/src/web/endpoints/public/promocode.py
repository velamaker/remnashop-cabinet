"""Активация промокода прямо в кабинете пользователя.

В боте промокод вводится в меню; в кабинете этого не было. Переиспользуем базовый
интерактор `ActivatePromocode` (required_permission = PUBLIC) — он сам валидирует
(лимиты/срок/аудитория/повтор), применяет награду (дни/трафик/устройства/подписка/
скидка) через Remnawave и коммитит свой UnitOfWork. Здесь только резолвим текущего
юзера, зовём интерактор и переводим доменные ошибки в понятные сообщения.

Награду возвращаем машинными полями (reward_type/reward) — текст успеха собирает и
локализует фронт (кабинет мультиязычный).

Подарок ДРУГОГО тарифа пересчитывает остаток текущей подписки в дни подарка по цене
дня. Где целиком перенести нельзя (бессрочная, возврат, дни без известной цены), здесь
подтверждения нет — поэтому отказ 409 с причиной, а не молчаливое сгорание дней
(overlay_plan_change.promo_web_refusal; в боте такой подарок подтверждают дважды).
"""

from typing import Any

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.dao import PromocodeDao, SubscriptionDao
from src.application.use_cases.promocode.commands.activate import (
    ActivatePromocode,
    ActivatePromocodeDto,
)
from src.core.exceptions import (
    PromocodeAlreadyActivatedError,
    PromocodeExpiredError,
    PromocodeNotAvailableError,
    PromocodeNotFoundError,
)
from src.core.utils.time import datetime_now
from src.infrastructure.services.overlay_plan_change import promo_web_refusal
from src.web.endpoints.public._common import CurrentUser

router = APIRouter(prefix="/promocode", tags=["Public - Promocode"])


class ActivateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64)


# PromocodeNotAvailableError объединяет несколько причин (сообщение — англ. из ядра).
# Мапим по подстроке в человекочитаемый RU-текст; фолбэк — общий.
def _availability_message(exc: PromocodeNotAvailableError) -> str:
    msg = str(exc).lower()
    if "limit reached" in msg:
        return "Лимит активаций промокода исчерпан"
    if "active subscription required" in msg:
        return "Для этого промокода нужна активная подписка"
    if "already unlimited" in msg:
        return "Этот ресурс у вас уже безлимитный"
    if "new users only" in msg:
        return "Промокод только для новых пользователей"
    if "existing users only" in msg:
        return "Промокод только для действующих пользователей"
    if "invited users only" in msg:
        return "Промокод только для приглашённых пользователей"
    return "Промокод недоступен для вашего аккаунта"


@router.post("/activate")
@inject
async def activate_promocode_endpoint(
    body: ActivateRequest,
    user: CurrentUser,
    activate_promocode: FromDishka[ActivatePromocode],
    session: FromDishka[AsyncSession],
    subscription_dao: FromDishka[SubscriptionDao],
    promocode_dao: FromDishka[PromocodeDao],
) -> dict[str, Any]:
    code = body.code.strip()
    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Введите промокод"
        )

    refusal = await promo_web_refusal(
        session, user=user, code=code, subscription_dao=subscription_dao,
        promocode_dao=promocode_dao, now=datetime_now(),
    )
    if refusal:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=refusal)

    try:
        promo = await activate_promocode(
            user, ActivatePromocodeDto(code=code, user=user)
        )
    except PromocodeNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Промокод не найден или неактивен",
        )
    except PromocodeExpiredError:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Срок действия промокода истёк",
        )
    except PromocodeAlreadyActivatedError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Вы уже активировали этот промокод",
        )
    except PromocodeNotAvailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=_availability_message(exc)
        )

    reward_type = (
        promo.reward_type.value
        if hasattr(promo.reward_type, "value")
        else str(promo.reward_type)
    )
    return {
        "success": True,
        "code": promo.code,
        "reward_type": reward_type,
        "reward": promo.reward,
    }
