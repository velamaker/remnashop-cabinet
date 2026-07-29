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
from dishka.integrations.aiogram_dialog import inject as dialog_inject
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


# Человеческие названия шлюзов: display_name админ заполняет не всегда, а сырой
# «YOOMONEY» на кнопке выглядит как техническая ошибка.
_GATEWAY_LABELS: dict[str, str] = {
    "YOOKASSA": "ЮKassa",
    "YOOMONEY": "ЮMoney",
    "CRYPTOMUS": "Cryptomus",
    "HELEKET": "Heleket",
    "CRYPTOPAY": "CryptoPay",
    "FREEKASSA": "FreeKassa",
    "PAYMASTER": "PayMaster",
}


def _gateway_name(gateway: Any) -> str:
    """Подпись шлюза: имя из настроек → известное название → тип как есть."""
    settings = getattr(gateway, "settings", None)
    custom = getattr(settings, "display_name", None)
    if custom:
        return custom
    raw = gateway.type.value
    return _GATEWAY_LABELS.get(raw.upper(), raw)


def _rub_price(duration: Any) -> Optional[Decimal]:
    price = next((p.price for p in duration.prices if p.currency == Currency.RUB), None)
    return Decimal(str(price)) if price is not None else None


async def _find_plan(get_available_plans: GetAvailablePlans, user: Any, code: str) -> Optional[Any]:
    plans = await get_available_plans.system(user)
    return next((p for p in plans if p.public_code == code), None)


GIFT_INTRO = (
    "🎁 <b>Подарить подписку</b>\n\nВыберите тариф — получите код, который "
    "получатель введёт в разделе «Промокод»."
)


def _plans_keyboard(plans: list[Any]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=p.name[:60], callback_data=f"{_PREFIX}:p:{p.public_code}")]
            for p in plans[:12]
        ]
    )


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
    await message.answer(GIFT_INTRO, reply_markup=_plans_keyboard(plans))


# Точка входа из ГЛАВНОГО МЕНЮ бота (кнопка «Подарить подписку», см. menu/dialog.py).
# Шлём отдельным сообщением, а не окном диалога: дальше работают обычные callback'и
# этого роутера, и окно меню остаётся на месте.
@dialog_inject
async def open_gift_from_menu(
    callback: CallbackQuery,
    widget: Any,
    dialog_manager: Any,
    get_available_plans: FromDishka[GetAvailablePlans],
) -> None:
    user = dialog_manager.middleware_data.get(USER_KEY)
    if user is None or callback.message is None:
        await callback.answer()
        return
    plans = await get_available_plans.system(user)
    if not plans:
        await callback.answer("Тарифы сейчас недоступны", show_alert=True)
        return
    await callback.message.answer(GIFT_INTRO, reply_markup=_plans_keyboard(plans))
    await callback.answer()


@router.callback_query(F.data == f"{_PREFIX}:back")
@inject
async def on_back_to_plans(
    callback: CallbackQuery,
    get_available_plans: FromDishka[GetAvailablePlans],
    **data: Any,
) -> None:
    """Возврат к списку тарифов: правим то же сообщение, лишних не плодим."""
    user = _user(data)
    plans = await get_available_plans.system(user) if user else []
    if not plans:
        await callback.answer("Тарифы сейчас недоступны", show_alert=True)
        return
    await callback.message.edit_text(GIFT_INTRO, reply_markup=_plans_keyboard(plans))
    await callback.answer()


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
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"{_PREFIX}:back")])
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
                    # Имя шлюза настраивается админом и лежит в settings, у самого
                    # PaymentGatewayDto поля display_name нет.
                    text=f"💳 Оплатить · {_gateway_name(gw)}"[:60],
                    callback_data=f"{_PREFIX}:g:{code}:{days}:{gw.type.value}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"{_PREFIX}:p:{code}")])
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
            # Это сообщение станет «Перейти к оплате» — после оплаты вебхук его удалит,
            # иначе у покупателя висит кнопка на уже оплаченный счёт.
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
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
