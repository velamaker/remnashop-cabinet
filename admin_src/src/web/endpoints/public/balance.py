from decimal import Decimal
from typing import Any

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, Query, status
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.dao import PaymentGatewayDao, SubscriptionDao, TransactionDao
from src.application.dto import PlanSnapshotDto, PriceDetailsDto
from src.application.services import PricingService
from src.application.use_cases.gateways.commands.payment import CreatePayment, CreatePaymentDto
from src.application.use_cases.plan.queries.match import MatchPlan, MatchPlanDto
from src.application.use_cases.subscription.commands.management import (
    AddSubscriptionDuration,
    AddSubscriptionDurationDto,
)
from src.application.use_cases.user.queries.plans import GetAvailablePlans
from src.core.enums import Currency, PaymentGatewayType, PlanType, PurchaseType, TransactionStatus
from src.infrastructure.services import overlay_topup
from src.web.endpoints.public._common import CurrentUser

router = APIRouter(prefix="/balance", tags=["Public - Balance"])

# 1 балл рефералки = 7 ₽ при оплате продления.
POINT_VALUE_RUB = Decimal(7)


def _fmt(value: Any) -> str:
    try:
        d = Decimal(str(value))
        if d == d.to_integral():
            return str(int(d))
        return format(d.normalize(), "f")
    except Exception:
        return str(value)


async def _get_balance(session: AsyncSession, user_id: int) -> Decimal:
    row = (
        await session.execute(
            text("SELECT cabinet_balance FROM users WHERE id = :id"), {"id": user_id}
        )
    ).scalar_one_or_none()
    return Decimal(str(row)) if row is not None else Decimal(0)


@router.get("")
@inject
async def get_balance(
    user: CurrentUser,
    transaction_dao: FromDishka[TransactionDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    transactions = await transaction_dao.get_by_user(user.id)
    completed = [t for t in transactions if t.status == TransactionStatus.COMPLETED]

    total_spent = sum(
        float(t.pricing.final_amount) for t in completed if not t.pricing.is_free
    )

    balance = await _get_balance(session, user.id)
    autopay = (
        await session.execute(
            text("SELECT autopay_enabled FROM users WHERE id = :id"), {"id": user.id}
        )
    ).scalar_one_or_none()

    return {
        "balance": float(balance),  # рублёвый кошелёк
        "points": user.points,  # баллы рефералки (отдельно)
        "total_spent": total_spent,
        "total_purchases": len(completed),
        "autopay_enabled": bool(autopay),
    }


class AutopayRequest(BaseModel):
    enabled: bool


@router.post("/autopay")
@inject
async def set_autopay(
    body: AutopayRequest,
    user: CurrentUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    await session.execute(
        text("UPDATE users SET autopay_enabled = :v WHERE id = :id"),
        {"v": body.enabled, "id": user.id},
    )
    await session.commit()
    return {"success": True, "autopay_enabled": body.enabled}


# ── Пополнение ₽-баланса через платёжные шлюзы (+бонус) ──


def _topup_gateways(active: list) -> list:
    """Активные, настроенные, рублёвые шлюзы (без Telegram Stars) — для пополнения."""
    return [
        g
        for g in active
        if g.type != PaymentGatewayType.TELEGRAM_STARS
        and g.currency == Currency.RUB
        and g.settings
        and g.settings.is_configured
    ]


@router.get("/topup/config")
@inject
async def topup_config(
    user: CurrentUser,
    payment_gateway_dao: FromDishka[PaymentGatewayDao],
) -> dict[str, Any]:
    """Конфиг пополнения для кабинета: тумблер/бонус/лимиты/пресеты + список шлюзов."""
    cfg = overlay_topup.load_config()
    gateways = [
        {
            "gateway_type": g.type.value,
            "name": (g.settings.display_name if g.settings and g.settings.display_name else g.type.value),
            "currency_symbol": g.currency.symbol,
        }
        for g in _topup_gateways(await payment_gateway_dao.get_active())
    ]
    return {
        "enabled": bool(cfg["enabled"]) and len(gateways) > 0,
        "bonus_percent": cfg["bonus_percent"],
        "min_amount": cfg["min_amount"],
        "max_amount": cfg["max_amount"],
        "presets": cfg["presets"],
        "gateways": gateways,
    }


class TopupRequest(BaseModel):
    amount: Decimal = Field(gt=0)
    gateway_type: PaymentGatewayType


@router.post("/topup")
@inject
async def create_topup(
    body: TopupRequest,
    user: CurrentUser,
    payment_gateway_dao: FromDishka[PaymentGatewayDao],
    create_payment: FromDishka[CreatePayment],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    cfg = overlay_topup.load_config()
    if not cfg["enabled"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Пополнение отключено")

    amount = overlay_topup.validate_amount(body.amount, cfg)
    if amount is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Сумма должна быть от {cfg['min_amount']} до {cfg['max_amount']} ₽",
        )

    gateway = await payment_gateway_dao.get_by_type(body.gateway_type)
    allowed = _topup_gateways(await payment_gateway_dao.get_active())
    if not gateway or gateway.type not in {g.type for g in allowed}:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Платёжный шлюз недоступен для пополнения",
        )

    bonus = overlay_topup.compute_bonus(amount, cfg)

    # Синтетический «тариф пополнения»: ProcessPayment (overlay) перехватит платёж
    # по строке в balance_topups и зачислит баланс, НЕ трогая подписку. Снимок нужен
    # лишь чтобы base CreatePayment собрал транзакцию/инвойс.
    plan_snapshot = PlanSnapshotDto(
        id=-2,
        name="Пополнение баланса",
        type=PlanType.UNLIMITED,
        traffic_limit=0,
        device_limit=0,
        duration=0,
        is_trial=False,
    )
    pricing = PriceDetailsDto(original_amount=amount, discount_percent=0, final_amount=amount)

    payment = await create_payment(
        user,
        CreatePaymentDto(
            plan_snapshot=plan_snapshot,
            pricing=pricing,
            purchase_type=PurchaseType.NEW,
            gateway_type=body.gateway_type,
        ),
    )

    # КРИТИЧНО: помечаем платёж как пополнение ДО отдачи URL. Если не записать —
    # на вебхуке платёж уйдёт как обычная покупка синтетического тарифа. Поэтому
    # при ошибке записи не отдаём URL (пользователь не оплатит).
    try:
        await overlay_topup.record_topup(
            session, payment_id=payment.id, user_id=user.id, amount=amount, bonus=bonus
        )
    except Exception as exc:  # noqa: BLE001
        logger.critical(f"topup: не удалось записать пополнение '{payment.id}': {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Не удалось создать пополнение, попробуйте ещё раз",
        ) from exc

    return {
        "payment_id": str(payment.id),
        "payment_url": payment.url,
        "amount": _fmt(amount),
        "bonus": _fmt(bonus),
        "total": _fmt(amount + bonus),
    }


@router.get("/transactions")
@inject
async def get_transactions(
    user: CurrentUser,
    transaction_dao: FromDishka[TransactionDao],
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    transactions = await transaction_dao.get_by_user(user.id)

    total = len(transactions)
    page = transactions[offset : offset + limit]

    items = []
    for t in page:
        items.append(
            {
                "payment_id": str(t.payment_id),
                "status": t.status.value if hasattr(t.status, "value") else str(t.status),
                "gateway_type": t.gateway_type.value if hasattr(t.gateway_type, "value") else str(t.gateway_type),
                "gateway_display_name": t.gateway_display_name,
                "purchase_type": t.purchase_type.value if hasattr(t.purchase_type, "value") else str(t.purchase_type),
                "plan_name": t.plan_snapshot.name if t.plan_snapshot else None,
                "original_amount": _fmt(t.pricing.original_amount),
                "discount_percent": t.pricing.discount_percent,
                "final_amount": _fmt(t.pricing.final_amount),
                "currency": t.currency.value if hasattr(t.currency, "value") else str(t.currency),
                "is_free": t.pricing.is_free,
                "is_test": t.is_test,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
        )

    return {"total": total, "limit": limit, "offset": offset, "items": items}


class SpendRenewalRequest(BaseModel):
    duration_days: int = Field(gt=0, le=3650)


@router.post("/spend-on-renewal")
@inject
async def spend_on_renewal(
    body: SpendRenewalRequest,
    user: CurrentUser,
    session: FromDishka[AsyncSession],
    subscription_dao: FromDishka[SubscriptionDao],
    pricing_service: FromDishka[PricingService],
    get_available_plans: FromDishka[GetAvailablePlans],
    match_plan: FromDishka[MatchPlan],
    add_duration: FromDishka[AddSubscriptionDuration],
) -> dict[str, Any]:
    """Продлить свою подписку, списав ₽ с баланса-кошелька (цена = цена тарифа).

    Цену считаем авторитетно тем же PricingService, что и оплата картой (со
    скидками юзера). Списываем баланс атомарно (race-safe), затем продлеваем;
    при ошибке продления — возвращаем деньги. `_execute` зовём напрямую в обход
    enum-прав: self-service над своей подпиской, доступ уже проверен CurrentUser.
    """
    days = body.duration_days

    current = await subscription_dao.get_current(user.id)
    if not current:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Нет активной подписки для продления. Сначала оформите тариф.",
        )

    plans = await get_available_plans.system(user)
    matched = await match_plan.system(
        MatchPlanDto(plan_snapshot=current.plan_snapshot, plans=plans)
    )
    if not matched:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Тариф для продления недоступен")

    duration = matched.get_duration(days)
    if not duration:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Срок тарифа не найден")

    pricing = pricing_service.calculate(user, duration.get_price(Currency.RUB), Currency.RUB)
    price = Decimal(str(pricing.final_amount))

    # Атомарное списание: спишется только если хватает (защита от гонок).
    new_balance = (
        await session.execute(
            text(
                "UPDATE users SET cabinet_balance = cabinet_balance - :amt "
                "WHERE id = :id AND cabinet_balance >= :amt RETURNING cabinet_balance"
            ),
            {"amt": price, "id": user.id},
        )
    ).scalar_one_or_none()
    if new_balance is None:
        balance = await _get_balance(session, user.id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Недостаточно средств на балансе: нужно {price} ₽, доступно {balance} ₽",
        )
    await session.commit()

    try:
        await add_duration._execute(user, AddSubscriptionDurationDto(user_id=user.id, days=days))
    except Exception as e:  # noqa: BLE001
        await session.execute(
            text("UPDATE users SET cabinet_balance = cabinet_balance + :amt WHERE id = :id"),
            {"amt": price, "id": user.id},
        )
        await session.commit()
        logger.warning(f"spend_on_renewal: продление user_id={user.id} упало ({e}), деньги возвращены")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Не удалось продлить подписку — деньги возвращены на баланс. Попробуйте позже.",
        )

    new_sub = await subscription_dao.get_current(user.id)
    return {
        "success": True,
        "days_added": days,
        "spent": float(price),
        "balance": float(Decimal(str(new_balance))),
        "expire_at": (new_sub.expire_at.isoformat() if new_sub and new_sub.expire_at else None),
    }


class ConvertPointsRequest(BaseModel):
    points: int = Field(gt=0)


@router.post("/convert-points")
@inject
async def convert_points(
    body: ConvertPointsRequest,
    user: CurrentUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    """Перевести баллы рефералки в рубли на баланс-кошелёк (1 балл = 7 ₽).

    Списание баллов АТОМАРНО и условно (UPDATE ... WHERE points >= :pts) — как у
    cabinet_balance. Иначе была гонка: проверка `pts > user.points` шла по DTO,
    а базовый ChangeUserPoints делал read-modify-write без блокировки → два
    параллельных запроса могли конвертировать больше баллов, чем есть, намайнив
    баланс. Списание баллов и зачисление ₽ — в ОДНОЙ транзакции: при сбое rollback
    возвращает и то, и другое (компенсирующий рефанд не нужен).
    """
    pts = body.points  # gt=0 из модели
    rub = Decimal(pts) * POINT_VALUE_RUB

    # Атомарно списываем баллы (спишутся только если хватает — защита от гонок).
    points_left = (
        await session.execute(
            text(
                "UPDATE users SET points = points - :p "
                "WHERE id = :id AND points >= :p RETURNING points"
            ),
            {"p": pts, "id": user.id},
        )
    ).scalar_one_or_none()
    if points_left is None:
        await session.rollback()
        have = (
            await session.execute(text("SELECT points FROM users WHERE id = :id"), {"id": user.id})
        ).scalar_one_or_none()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Недостаточно баллов: запрошено {pts}, доступно {int(have or 0)}",
        )

    # Зачисляем на баланс в ТОЙ ЖЕ транзакции; при ошибке rollback вернёт и баллы.
    try:
        new_balance = (
            await session.execute(
                text(
                    "UPDATE users SET cabinet_balance = cabinet_balance + :amt "
                    "WHERE id = :id RETURNING cabinet_balance"
                ),
                {"amt": rub, "id": user.id},
            )
        ).scalar_one()
        await session.commit()
    except Exception as e:  # noqa: BLE001
        await session.rollback()  # откатывает и списание баллов (одна транзакция)
        logger.warning(f"convert_points: зачисление user_id={user.id} упало ({e}), баллы возвращены")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Не удалось зачислить на баланс — баллы возвращены. Попробуйте позже.",
        )

    return {
        "success": True,
        "converted_points": pts,
        "credited_rub": float(rub),
        "balance": float(Decimal(str(new_balance))),
        "points": int(points_left or 0),
    }
