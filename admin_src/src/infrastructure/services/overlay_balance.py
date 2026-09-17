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


async def was_subscription_granted(
    subscription_dao: SubscriptionDao, user_id: int, expire_before: object
) -> "bool | None":
    """Сдвинулся ли срок подписки. True/False, None — определить не удалось.

    Судим по СРОКУ, а не по статусу счёта: базовый ProcessPayment переводит счёт в
    COMPLETED ДО выдачи, и «счёт проведён» ещё не значит «подписка есть». Сдвинутый
    вперёд срок — единственный признак, который нельзя истолковать двояко.

    Вынесено отдельно, потому что от этого ответа зависит, вернуть человеку деньги
    или нет; такое обязано быть проверяемым.
    """
    try:
        after = await subscription_dao.get_current(user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"renew_current_from_balance: не смог сверить срок: {exc}")
        return None

    after_expire = getattr(after, "expire_at", None)
    if after_expire is None or expire_before is None:
        return None
    return bool(after_expire > expire_before)


async def was_change_granted(
    subscription_dao: SubscriptionDao, user_id: int, before: object
) -> "bool | None":
    """Выдана ли покупка с баланса (NEW / RENEW / CHANGE). None — определить не удалось.

    ПОЧЕМУ НЕ ПО СРОКУ, как `was_subscription_granted`. Смена тарифа с переносом
    остатка создаёт НОВУЮ строку подписки, и её срок бывает РАНЬШЕ старого: полгода
    дешёвого тарифа превращаются в два месяца дорогого. Сравнение сроков сказало бы
    «не выдано» — и деньги вернулись бы поверх выданной смены с бонусом. Поэтому
    главный признак — сменилась ли строка; срок вперёд — признак продления.

    `before` — текущая подписка ДО списания (SubscriptionDto или None).
    """
    try:
        after = await subscription_dao.get_current(user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"pay_with_balance: не смог сверить подписку после оплаты: {exc}")
        return None

    if after is None:
        # Подписки не было и нет — выдачи точно не было. Была, а теперь нет — странно.
        return False if before is None else None
    if before is None:
        return True
    before_id = getattr(before, "id", None)
    after_id = getattr(after, "id", None)
    if before_id is not None and after_id is not None and after_id != before_id:
        return True
    before_expire = getattr(before, "expire_at", None)
    after_expire = getattr(after, "expire_at", None)
    if before_expire is None or after_expire is None:
        return None
    return bool(after_expire > before_expire)


async def was_balance_purchase_granted(
    *,
    error: BaseException,
    session: AsyncSession,
    payment_id: object,
    subscription_dao: SubscriptionDao,
    user_id: int,
    before: object,
) -> "bool | None":
    """Выдана ли покупка с баланса, которая закончилась исключением. None — не знаем.

    Порядок условий денежный:
      1) `PurchaseError` — выдача упала, база перевела счёт в FAILED: НЕ выдано;
      2) счёт не COMPLETED (не создан, PENDING, FAILED): НЕ выдано;
      3) и только потом — изменилась ли подписка (`was_change_granted`).
    Одна проверка строки подписки врёт при гонке: соседняя покупка могла сменить
    строку, пока наша упала, — и деньги не вернулись бы за то, чего человек не получил.
    """
    from src.core.exceptions import PurchaseError

    if isinstance(error, PurchaseError):
        return False
    if payment_id is None:
        return False
    try:
        row = (
            await session.execute(
                text("SELECT status::text FROM transactions WHERE payment_id = CAST(:pid AS uuid)"),
                {"pid": str(payment_id)},
            )
        ).first()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"pay_with_balance: статус счёта не прочитан: {exc}")
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None
    if row is None or str(row[0]) != TransactionStatus.COMPLETED.value:
        return False
    return await was_change_granted(subscription_dao, user_id, before)


async def _alert_admins_balance(message: str, title: str = "⚠️ Продление с баланса") -> None:
    """Сказать владельцу. Никогда не мешает основному пути."""
    try:
        from src.infrastructure.services.overlay_push import push_admins_standalone

        await push_admins_standalone({"title": title, "body": message})
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"renew_current_from_balance: не предупредил владельца: {exc}")


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

    # Срок ДО списания: по нему потом узнаем, выдалась подписка или нет.
    expire_before = getattr(current, "expire_at", None)

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
        # ВОЗВРАЩАТЬ ДЕНЬГИ МОЖНО, ТОЛЬКО ЕСЛИ ПОДПИСКА НЕ ВЫДАНА.
        #
        # Раньше возврат был безусловным, а `try` накрывает не только оплату, но и
        # последний шаг успешного пути — живой вызов Telegram
        # (`redirect.to_success_payment`). Человек заблокировал бота → исключение
        # прилетает ПОСЛЕ того, как подписка уже закоммичена и в Remnawave, и в БД,
        # и деньги возвращались поверх выданной услуги. Автоплатёж гасит это одним
        # warning, так что каждый его прогон по такому юзеру = бесплатное продление.
        #
        # Судим по СРОКУ ПОДПИСКИ, а не по статусу счёта: базовый ProcessPayment
        # переводит счёт в COMPLETED ДО выдачи, и «счёт проведён» ещё не значит
        # «подписка есть».
        granted = await was_subscription_granted(subscription_dao, user.id, expire_before)

        if granted:
            logger.error(
                f"renew_current_from_balance: user_id={user.id} — подписка ВЫДАНА, "
                f"но шаг после выдачи упал ({e}). Деньги НЕ возвращаем: "
                "это было бы бесплатное продление."
            )
            await _alert_admins_balance(
                f"Продление с баланса: подписка выдана, но последний шаг упал "
                f"(user_id={user.id}, {price} ₽). Деньги не возвращены — проверьте, "
                f"дошло ли уведомление."
            )
            return Decimal(str(new_balance))

        await session.execute(
            text("UPDATE users SET cabinet_balance = cabinet_balance + :amt WHERE id = :id"),
            {"amt": price, "id": user.id},
        )
        await session.commit()
        if granted is None:
            # Определить не смогли. Возвращаем деньги (как раньше), но громко:
            # человек мог остаться и с деньгами, и с подпиской.
            logger.error(
                f"renew_current_from_balance: user_id={user.id} упало ({e}), деньги "
                "возвращены, но выдачу подтвердить НЕ удалось — проверьте вручную"
            )
            await _alert_admins_balance(
                f"Продление с баланса упало (user_id={user.id}, {price} ₽), деньги "
                "вернули, но выдалась ли подписка — неизвестно. Нужна проверка."
            )
        else:
            logger.warning(
                f"renew_current_from_balance: user_id={user.id} упало ({e}), деньги возвращены"
            )
        raise

    logger.info(f"Autopay: продлил подписку user_id={user.id} за {price} ₽ ({days} дн.)")
    return Decimal(str(new_balance))
