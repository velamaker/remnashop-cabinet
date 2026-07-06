"""Продление текущей подписки за ₽-баланс (общая логика для autopay-cron и др.).

Повторяет суть `pay-with-balance`, но для ТЕКУЩЕГО тарифа на его же срок:
считает цену PricingService (RUB, со скидками), атомарно списывает cabinet_balance,
создаёт завершённую транзакцию (без обращения к шлюзу) и отдаёт базовому
ProcessPayment (он продлевает подписку + начисляет реферальные). При ошибке —
возвращает деньги.
"""

from decimal import Decimal
from typing import Optional
from uuid import uuid4

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.dao import (
    PaymentGatewayDao,
    SubscriptionDao,
    TransactionDao,
)
from src.application.common.uow import UnitOfWork
from src.application.dto import PlanSnapshotDto, TransactionDto, UserDto
from src.application.services import PricingService
from src.application.use_cases.gateways.commands.payment import (
    ProcessPayment,
    ProcessPaymentDto,
)
from src.application.use_cases.plan.queries.match import MatchPlan, MatchPlanDto
from src.application.use_cases.user.queries.plans import GetAvailablePlans
from src.core.enums import Currency, PurchaseType, TransactionStatus


async def _first_rub_gateway(payment_gateway_dao: PaymentGatewayDao):
    for gw in await payment_gateway_dao.get_active():
        if gw.currency == Currency.RUB:
            return gw
    return None


async def renew_current_from_balance(
    user: UserDto,
    *,
    session: AsyncSession,
    uow: UnitOfWork,
    subscription_dao: SubscriptionDao,
    payment_gateway_dao: PaymentGatewayDao,
    pricing_service: PricingService,
    get_available_plans: GetAvailablePlans,
    match_plan: MatchPlan,
    transaction_dao: TransactionDao,
    process_payment: ProcessPayment,
    source: str = "Баланс (авто)",
) -> Optional[Decimal]:
    """Продлить текущий тариф юзера за ₽-баланс. Возвращает новый баланс или None
    (если продлевать нечего/тариф недоступен/не хватает средств/нет RUB-шлюза)."""
    current = await subscription_dao.get_current(user.id)
    if not current:
        return None

    plans = await get_available_plans.system(user)
    matched = await match_plan.system(
        MatchPlanDto(plan_snapshot=current.plan_snapshot, plans=plans)
    )
    if not matched:
        return None

    days = current.plan_snapshot.duration
    duration = matched.get_duration(days)
    if not duration:
        return None

    gateway = await _first_rub_gateway(payment_gateway_dao)
    if not gateway:
        logger.warning("autopay: нет активного RUB-шлюза — пропускаю")
        return None

    pricing = pricing_service.calculate(user, duration.get_price(Currency.RUB), Currency.RUB)
    price = Decimal(str(pricing.final_amount))

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
        return None  # не хватает средств
    await session.commit()

    try:
        transaction = TransactionDto(
            payment_id=uuid4(),
            user_id=user.id,
            status=TransactionStatus.PENDING,
            purchase_type=PurchaseType.RENEW,
            gateway_type=gateway.type,
            gateway_display_name=source,
            pricing=pricing,
            currency=Currency.RUB,
            plan_snapshot=PlanSnapshotDto.from_plan(matched, days),
        )
        async with uow:
            await transaction_dao.create(transaction)
            await uow.commit()

        await process_payment.system(
            ProcessPaymentDto(
                payment_id=transaction.payment_id,
                new_transaction_status=TransactionStatus.COMPLETED,
                gateway_type=gateway.type,
            ),
        )
    except Exception as e:  # noqa: BLE001
        await session.execute(
            text("UPDATE users SET cabinet_balance = cabinet_balance + :amt WHERE id = :id"),
            {"amt": price, "id": user.id},
        )
        await session.commit()
        logger.warning(f"renew_current_from_balance: user_id={user.id} упало ({e}), деньги возвращены")
        raise

    logger.info(f"Autopay: продлил подписку user_id={user.id} за {price} ₽ ({days} дн.)")
    return Decimal(str(new_balance))
