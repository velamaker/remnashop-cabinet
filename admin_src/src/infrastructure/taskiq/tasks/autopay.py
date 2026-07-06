"""Autopay: автопродление подписки с ₽-баланса.

За N дней до конца подписки (env AUTOPAY_DAYS_BEFORE, дефолт 3) у пользователей с
включённым autopay и достаточным балансом продлевает текущий тариф, списывая ₽.
Логика продления — в services/overlay_balance.renew_current_from_balance (та же,
что «оплата с баланса»: транзакция + базовый ProcessPayment + реферальные).

Дедуп естественный: после продления expire_at уходит за окно N дней. Cron почасовой.
Авто-обнаруживается taskiq по глобу tasks/*.py.
"""

import os

from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.dao import (
    PaymentGatewayDao,
    SubscriptionDao,
    TransactionDao,
    UserDao,
)
from src.application.common.uow import UnitOfWork
from src.application.services import PricingService
from src.application.use_cases.gateways.commands.payment import ProcessPayment
from src.application.use_cases.plan.queries.match import MatchPlan
from src.application.use_cases.user.queries.plans import GetAvailablePlans
from src.infrastructure.services.overlay_balance import renew_current_from_balance
from src.infrastructure.services.overlay_push import notify_user_push
from src.infrastructure.taskiq.broker import broker


def _autopay_enabled() -> bool:
    return (os.environ.get("AUTOPAY_ENABLED") or "true").strip().lower() == "true"


def _days_before() -> int:
    try:
        return max(1, int(os.environ.get("AUTOPAY_DAYS_BEFORE") or "3"))
    except ValueError:
        return 3


@broker.task(schedule=[{"cron": "0 * * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def run_autopay(
    session: FromDishka[AsyncSession],
    user_dao: FromDishka[UserDao],
    uow: FromDishka[UnitOfWork],
    subscription_dao: FromDishka[SubscriptionDao],
    payment_gateway_dao: FromDishka[PaymentGatewayDao],
    pricing_service: FromDishka[PricingService],
    get_available_plans: FromDishka[GetAvailablePlans],
    match_plan: FromDishka[MatchPlan],
    transaction_dao: FromDishka[TransactionDao],
    process_payment: FromDishka[ProcessPayment],
) -> None:
    if not _autopay_enabled():
        return

    n = _days_before()
    rows = (
        await session.execute(
            text(
                "SELECT u.id FROM users u "
                "JOIN subscriptions s ON u.current_subscription_id = s.id "
                "WHERE u.autopay_enabled = true AND s.status = 'ACTIVE' "
                "AND s.expire_at >= now() AND s.expire_at < now() + make_interval(days => :n) "
                "AND u.cabinet_balance > 0"
            ),
            {"n": n},
        )
    ).all()
    user_ids = [r[0] for r in rows]
    if not user_ids:
        return

    renewed = 0
    for uid in user_ids:
        user = await user_dao.get_by_id(uid)
        if not user:
            continue
        try:
            result = await renew_current_from_balance(
                user,
                session=session,
                uow=uow,
                subscription_dao=subscription_dao,
                payment_gateway_dao=payment_gateway_dao,
                pricing_service=pricing_service,
                get_available_plans=get_available_plans,
                match_plan=match_plan,
                transaction_dao=transaction_dao,
                process_payment=process_payment,
            )
            if result is not None:
                renewed += 1
                # Web-push об автосписании (PWA/iOS) вдобавок к TG-подтверждению
                # покупки от базового ProcessPayment. best-effort, коммитит сам.
                await notify_user_push(
                    session,
                    user,
                    {
                        "ru": ("🔄 Подписка продлена автоматически", "Списано с баланса. Остаток: {balance} ₽."),
                        "en": ("🔄 Subscription auto-renewed", "Charged from your balance. Remaining: {balance} ₽."),
                    },
                    url="/",
                    tag="autopay",
                    balance=result,
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"autopay: продление user_id={uid} не удалось: {e}")

    if renewed:
        logger.info(f"Autopay: автопродлено подписок: {renewed} из {len(user_ids)} кандидатов")
