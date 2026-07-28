"""Public: подарить подписку — юзер платит с баланса, получает код-подарок.

Юзер выбирает тариф+длительность → списываем с его ₽-баланса цену тарифа → создаём
ОДНОРАЗОВЫЙ промокод типа SUBSCRIPTION (тот же механизм, что у админ-промокодов) и
отдаём код. Получатель активирует код обычным «ввести промокод» → получает подписку.
При ошибке создания — рефанд. Реюз: race-safe списание баланса + PlanSnapshotDto.
"""

import secrets
import uuid
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

from src.application.common.dao import PaymentGatewayDao, PromocodeDao, TransactionDao
from src.application.common.uow import UnitOfWork
from src.application.dto import (
    PlanSnapshotDto,
    PriceDetailsDto,
    PromocodeDto,
    TransactionDto,
)
from src.application.use_cases.gateways.commands.payment import CreatePayment, CreatePaymentDto
from src.application.use_cases.user.queries.plans import GetAvailablePlans
from src.infrastructure.services import overlay_gift
from src.core.enums import (
    Currency,
    PaymentGatewayType,
    PlanType,
    PromocodeAvailability,
    PromocodeRewardType,
    PurchaseType,
    TransactionStatus,
)
from src.web.endpoints.public._common import CurrentUser

router = APIRouter(prefix="/gift", tags=["Public - Gift"])


class GiftCreate(BaseModel):
    plan_code: str
    duration_days: int = Field(gt=0, le=3650)
    # Если задан — оплата через шлюз (в обход баланса); иначе списываем с баланса.
    gateway_type: PaymentGatewayType | None = None


@router.post("/create")
@inject
async def create_gift(
    body: GiftCreate,
    user: CurrentUser,
    session: FromDishka[AsyncSession],
    promocode_dao: FromDishka[PromocodeDao],
    get_available_plans: FromDishka[GetAvailablePlans],
    retort: FromDishka[Retort],
    transaction_dao: FromDishka[TransactionDao],
    payment_gateway_dao: FromDishka[PaymentGatewayDao],
    create_payment: FromDishka[CreatePayment],
    uow: FromDishka[UnitOfWork],
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

    snapshot = PlanSnapshotDto.from_plan(plan, body.duration_days)

    # ── Оплата через шлюз (в обход баланса) ──────────────────────────────────
    # Повторяем безопасную схему пополнения баланса: платёж создаётся штатным
    # CreatePayment с СИНТЕТИЧЕСКИМ тарифом, а строка в gift_payments заставит
    # overlay-обработчик вебхука выпустить код вместо выдачи подписки покупателю.
    if body.gateway_type is not None:
        gw = await payment_gateway_dao.get_by_type(body.gateway_type)
        if not gw or not gw.is_active:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Платёжный шлюз недоступен")
        if gw.currency != Currency.RUB:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Подарок оплачивается только в рублях",
            )
        invoice_plan = PlanSnapshotDto(
            id=overlay_gift.GIFT_PLAN_ID,
            name=f"Подарок: {plan.name} ({body.duration_days} дн.)"[:120],
            type=PlanType.UNLIMITED,
            traffic_limit=0,
            device_limit=0,
            duration=0,
            is_trial=False,
        )
        payment = await create_payment(
            user,
            CreatePaymentDto(
                plan_snapshot=invoice_plan,
                pricing=PriceDetailsDto(
                    original_amount=price, discount_percent=0, final_amount=price
                ),
                purchase_type=PurchaseType.NEW,
                gateway_type=body.gateway_type,
            ),
        )
        # КРИТИЧНО: пометить платёж как подарок ДО отдачи URL. Без метки вебхук
        # проведёт его как обычную покупку синтетического тарифа.
        try:
            await overlay_gift.record_gift_payment(
                session,
                payment_id=payment.id,
                user_id=user.id,
                plan_snapshot=retort.dump(snapshot),
                duration_days=body.duration_days,
                amount=price,
            )
        except Exception as exc:  # noqa: BLE001
            logger.critical(f"gift: не смог пометить платёж '{payment.id}' как подарок: {exc}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Не удалось создать оплату подарка, попробуйте ещё раз",
            ) from exc
        return {
            "paid_by": "gateway",
            "payment_id": str(payment.id),
            "payment_url": payment.url,
            "plan_name": plan.name,
            "duration_days": body.duration_days,
            "price": str(price),
        }

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

    # 128-бит энтропии: гифт-код = одноразовый ваучер на подписку (реальные деньги),
    # а /promocode/activate брутфорсится. token_hex(4)=32 бита перебирался за часы;
    # token_hex(16)=128 бит неперечислим. Колонка code — varchar без лимита длины.
    code = "GIFT-" + secrets.token_hex(16).upper()
    try:
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
    except Exception as e:  # noqa: BLE001 — при любой ошибке откатываем всю транзакцию
        # Списание и создание промокода — В ОДНОЙ незакоммиченной транзакции (единственный
        # commit выше). Поэтому корректно и достаточно rollback: он отменяет списание.
        # Ручной +price был опасен — при сбое самого commit (обрыв/timeout) списание уже
        # откачено сервером, а +price в НОВОЙ транзакции создавал деньги из воздуха.
        await session.rollback()
        logger.warning(f"gift: создание подарка user_id={user.id} упало ({e}), списание отменено")
        raise HTTPException(status_code=502, detail="Не удалось создать подарок, средства возвращены")

    payment_uuid = uuid.uuid4()

    # История подарков (та же таблица, что и у шлюзовых) — чтобы код можно было
    # посмотреть позже: в ответе API он приходит один раз. Best-effort по той же
    # причине, что и транзакция ниже: подарок уже выдан.
    try:
        await overlay_gift.record_issued_gift(
            session,
            payment_id=payment_uuid,
            user_id=user.id,
            plan_snapshot=plan_snapshot,
            duration_days=body.duration_days,
            amount=price,
            code=code,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"gift: подарок {code} выдан, но в истории подарков не записан ({e})")

    # Запись в историю баланса. Раньше подарок списывал деньги молча: в
    # transactions ничего не появлялось, и в кабинете трату было не найти.
    # Пишем ПОСЛЕ выдачи кода и best-effort: подарок уже действителен, и ронять
    # его из-за проблем с журналом нельзя.
    # Шлюза у балансовой покупки нет, но колонка gateway_type в transactions
    # обязательная — берём любой активный рублёвый (как это делает оплата с баланса).
    gateway = None
    try:
        for g in await payment_gateway_dao.get_active():
            if g.currency == Currency.RUB:
                gateway = g
                break
    except Exception:  # noqa: BLE001
        gateway = None
    try:
        transaction = TransactionDto(
            payment_id=payment_uuid,
            user_id=user.id,
            status=TransactionStatus.COMPLETED,
            purchase_type=PurchaseType.NEW,
            gateway_type=getattr(gateway, "type", None) or PaymentGatewayType.YOOMONEY,
            gateway_display_name="Баланс · подарок",
            pricing=PriceDetailsDto(
                original_amount=price,
                discount_percent=0,
                final_amount=price,
            ),
            currency=Currency.RUB,
            plan_snapshot=snapshot,
        )
        async with uow:
            await transaction_dao.create(transaction)
            await uow.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"gift: подарок {code} выдан, но запись в историю не создана ({e})")

    return {
        "paid_by": "balance",
        "code": code,
        "plan_name": plan.name,
        "duration_days": body.duration_days,
        "price": str(price),
    }


@router.get("/my")
@inject
async def my_gifts(
    user: CurrentUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    """Подарки, купленные ЭТИМ пользователем (свои коды + ожидающие оплаты).

    Нужен кабинету после оплаты через шлюз: код выпускается на вебхуке, когда юзер
    ещё на странице банка, и в ответе /gift/create его нет. Плюс страховка, если код
    потеряли: раньше он приходил только сообщением в Telegram.
    """
    return {"items": await overlay_gift.list_user_gifts(session, user_id=user.id)}
