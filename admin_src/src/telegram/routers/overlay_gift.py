"""Подарить подписку прямо из бота: команда `/gift`.

Раньше подарок можно было купить только в кабинете и только с баланса. Здесь тот же
подарок доступен в боте, а оплатить можно двумя способами:

  • «С баланса» — списание с ₽-кошелька и код сразу (overlay_gift.create_gift_from_balance);
  • «Оплатить» — платёж через шлюз в обход баланса: создаём платёж штатным CreatePayment
    с синтетическим тарифом и помечаем его в gift_payments; код выпустит вебхук
    (overlay_gift.try_issue_gift), он же пришлёт его покупателю в этот же чат.

Роутер подключается в vendored dispatcher.py (defensive include: если файла нет,
бот стартует без него).
"""

from decimal import Decimal
from typing import Any, Optional

from adaptix import Retort
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dishka import FromDishka
from dishka.integrations.aiogram import inject
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.dao import PaymentGatewayDao
from src.application.dto import PlanSnapshotDto, PriceDetailsDto
from src.application.use_cases.gateways.commands.payment import CreatePayment, CreatePaymentDto
from src.application.use_cases.user.queries.plans import GetAvailablePlans
from src.core.constants import USER_KEY
from src.core.enums import Currency, PaymentGatewayType, PlanType, PurchaseType
from src.infrastructure.services import overlay_gift

router = Router(name="overlay_gift")

_PREFIX = "gift"


def _user(data: dict[str, Any]) -> Any:
    return data.get(USER_KEY)


def _esc(value: Any) -> str:
    return (
        str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if value is not None
        else ""
    )


def _rub_price(duration: Any) -> Optional[Decimal]:
    price = next((p.price for p in duration.prices if p.currency == Currency.RUB), None)
    return Decimal(str(price)) if price is not None else None


async def _find_plan(get_available_plans: GetAvailablePlans, user: Any, code: str) -> Optional[Any]:
    plans = await get_available_plans.system(user)
    return next((p for p in plans if p.public_code == code), None)


@router.message(Command("gift"))
@inject
async def cmd_gift(
    message: Message,
    get_available_plans: FromDishka[GetAvailablePlans],
    **data: Any,
) -> None:
    user = _user(data)
    if user is None:
        return
    plans = await get_available_plans.system(user)
    if not plans:
        await message.answer("Тарифы сейчас недоступны.")
        return
    rows = [
        [InlineKeyboardButton(text=p.name[:60], callback_data=f"{_PREFIX}:p:{p.public_code}")]
        for p in plans[:12]
    ]
    await message.answer(
        "🎁 <b>Подарить подписку</b>\n\nВыберите тариф — получите код, который "
        "получатель введёт в разделе «Промокод».",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith(f"{_PREFIX}:p:"))
@inject
async def on_plan(
    callback: CallbackQuery,
    get_available_plans: FromDishka[GetAvailablePlans],
    **data: Any,
) -> None:
    user = _user(data)
    code = (callback.data or "").split(":")[2]
    plan = await _find_plan(get_available_plans, user, code)
    if not plan:
        await callback.answer("Тариф не найден", show_alert=True)
        return
    rows = []
    for d in plan.durations:
        price = _rub_price(d)
        if price is None:
            continue
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{d.days} дн. — {price:.0f} ₽",
                    callback_data=f"{_PREFIX}:d:{code}:{d.days}",
                )
            ]
        )
    if not rows:
        await callback.answer("У тарифа нет рублёвых цен", show_alert=True)
        return
    await callback.message.edit_text(
        f"🎁 <b>{_esc(plan.name)}</b>\n\nВыберите срок подарка:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith(f"{_PREFIX}:d:"))
@inject
async def on_duration(
    callback: CallbackQuery,
    get_available_plans: FromDishka[GetAvailablePlans],
    payment_gateway_dao: FromDishka[PaymentGatewayDao],
    **data: Any,
) -> None:
    user = _user(data)
    _, _, code, days_raw = (callback.data or "").split(":")
    days = int(days_raw)
    plan = await _find_plan(get_available_plans, user, code)
    duration = plan.get_duration(days) if plan else None
    price = _rub_price(duration) if duration else None
    if price is None:
        await callback.answer("Срок недоступен", show_alert=True)
        return

    rows = [[InlineKeyboardButton(text=f"💼 С баланса ({price:.0f} ₽)", callback_data=f"{_PREFIX}:b:{code}:{days}")]]
    for gw in await payment_gateway_dao.get_active():
        if gw.currency != Currency.RUB or gw.type == PaymentGatewayType.TELEGRAM_STARS:
            continue
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"💳 Оплатить · {gw.display_name or gw.type.value}"[:60],
                    callback_data=f"{_PREFIX}:g:{code}:{days}:{gw.type.value}",
                )
            ]
        )
    await callback.message.edit_text(
        f"🎁 <b>{_esc(plan.name)}</b> · {days} дн. — <b>{price:.0f} ₽</b>\n\nКак оплатить?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith(f"{_PREFIX}:b:"))
@inject
async def on_pay_balance(
    callback: CallbackQuery,
    get_available_plans: FromDishka[GetAvailablePlans],
    session: FromDishka[AsyncSession],
    retort: FromDishka[Retort],
    **data: Any,
) -> None:
    user = _user(data)
    _, _, code, days_raw = (callback.data or "").split(":")
    days = int(days_raw)
    plan = await _find_plan(get_available_plans, user, code)
    duration = plan.get_duration(days) if plan else None
    price = _rub_price(duration) if duration else None
    if price is None:
        await callback.answer("Срок недоступен", show_alert=True)
        return

    snapshot = retort.dump(PlanSnapshotDto.from_plan(plan, days))
    try:
        gift_code = await overlay_gift.create_gift_from_balance(
            session, user_id=user.id, plan_snapshot=snapshot, price=price
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"gift(bot): покупка с баланса упала для user_id={user.id}: {exc}")
        await callback.answer("Не удалось купить подарок, средства не списаны", show_alert=True)
        return
    if gift_code is None:
        await callback.answer(f"Недостаточно средств: нужно {price:.0f} ₽", show_alert=True)
        return

    await callback.message.edit_text(
        f"🎁 Подарок готов: <b>{_esc(plan.name)}</b> на {days} дн.\n\n"
        f"Код для получателя:\n<code>{gift_code}</code>\n\n"
        "Он вводит его в разделе «Промокод».",
    )
    await callback.answer("Куплено")


@router.callback_query(F.data.startswith(f"{_PREFIX}:g:"))
@inject
async def on_pay_gateway(
    callback: CallbackQuery,
    get_available_plans: FromDishka[GetAvailablePlans],
    payment_gateway_dao: FromDishka[PaymentGatewayDao],
    create_payment: FromDishka[CreatePayment],
    session: FromDishka[AsyncSession],
    retort: FromDishka[Retort],
    **data: Any,
) -> None:
    user = _user(data)
    _, _, code, days_raw, gw_raw = (callback.data or "").split(":")
    days = int(days_raw)
    plan = await _find_plan(get_available_plans, user, code)
    duration = plan.get_duration(days) if plan else None
    price = _rub_price(duration) if duration else None
    if price is None:
        await callback.answer("Срок недоступен", show_alert=True)
        return

    gateway = await payment_gateway_dao.get_by_type(PaymentGatewayType(gw_raw))
    if not gateway or not gateway.is_active or gateway.currency != Currency.RUB:
        await callback.answer("Шлюз недоступен", show_alert=True)
        return

    invoice_plan = PlanSnapshotDto(
        id=overlay_gift.GIFT_PLAN_ID,
        name=f"Подарок: {plan.name} ({days} дн.)"[:120],
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
            pricing=PriceDetailsDto(original_amount=price, discount_percent=0, final_amount=price),
            purchase_type=PurchaseType.NEW,
            gateway_type=gateway.type,
        ),
    )
    # КРИТИЧНО: пометить платёж подарком ДО показа ссылки — иначе вебхук проведёт
    # его как обычную покупку синтетического тарифа.
    try:
        await overlay_gift.record_gift_payment(
            session,
            payment_id=payment.id,
            user_id=user.id,
            plan_snapshot=retort.dump(PlanSnapshotDto.from_plan(plan, days)),
            duration_days=days,
            amount=price,
        )
    except Exception as exc:  # noqa: BLE001
        logger.critical(f"gift(bot): не смог пометить платёж '{payment.id}' подарком: {exc}")
        await callback.answer("Не удалось создать оплату, попробуйте ещё раз", show_alert=True)
        return

    await callback.message.edit_text(
        f"🎁 <b>{_esc(plan.name)}</b> · {days} дн. — <b>{price:.0f} ₽</b>\n\n"
        "Оплатите по ссылке — код подарка придёт сюда же сразу после оплаты.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="💳 Перейти к оплате", url=payment.url)]]
        ),
    )
    await callback.answer()
