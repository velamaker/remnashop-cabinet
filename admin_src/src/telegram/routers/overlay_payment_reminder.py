"""Кнопка «Не напоминать» под напоминанием о незавершённой оплате (overlay).

ЗАЧЕМ ОТДЕЛЬНЫЙ РОУТЕР. До этой фичи у человека не было НИ ОДНОГО способа сказать
«не пиши мне»: `is_blocked` — это бан со стороны магазина, `is_bot_blocked` — уже
случившаяся блокировка бота. Рассылать напоминания, не дав отказаться, значит
подталкивать людей блокировать бота целиком — и терять вместе с напоминаниями всё
остальное, включая уведомления об окончании подписки.

ЧТО ДЕЛАЕТ КНОПКА. Пишет строку в `notification_optouts` (вид — только напоминания
об оплате) и убирает кнопки из сообщения. Отказ бессрочный; вернуть его можно из
админки, сняв строку. Повторное нажатие безопасно: запись идёт ON CONFLICT DO NOTHING.

ПОЧЕМУ ФИЛЬТР СТРОГО ПО СВОЕМУ ПРЕФИКСУ. Наши роутеры подключаются ПЕРЕД базовыми
(bot_routers.py), и широкий фильтр перехватил бы чужие колбэки — правило «не
перехватывать чужие ручки» появилось ровно после такого случая.
"""

from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from dishka import FromDishka
from dishka.integrations.aiogram import inject
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import USER_KEY
from src.infrastructure.services.overlay_payment_reminder import OPTOUT_KIND, OPTOUT_SQL
from src.infrastructure.services.overlay_payment_reminder_kb import OPTOUT_CALLBACK, texts_for

router = Router(name="overlay_payment_reminder")

DONE_RU = "Больше не напомню об оплате."
DONE_EN = "No more payment reminders."


@router.callback_query(F.data == OPTOUT_CALLBACK)
@inject
async def on_optout(callback: CallbackQuery, session: FromDishka[AsyncSession], **data: Any) -> None:
    user = data.get(USER_KEY)
    user_id = getattr(user, "id", None)
    lang = getattr(user, "language", None)
    words = texts_for(str(lang) if lang else None)
    done = DONE_EN if words is texts_for("en") else DONE_RU
    if user_id is None:
        # Неизвестный отправитель: молча закрываем «часики», писать в базу нечего.
        await callback.answer()
        return
    try:
        await session.execute(text(OPTOUT_SQL), {"user_id": int(user_id), "kind": OPTOUT_KIND})
        await session.commit()
    except Exception as exc:  # noqa: BLE001 — отказ не должен падать человеку в лицо
        await session.rollback()
        logger.warning(f"payment_reminder: отказ user_id={user_id} не записан: {exc}")
        await callback.answer("Не получилось, попробуйте ещё раз", show_alert=True)
        return

    await callback.answer(done)
    message = callback.message
    if message is None:
        return
    try:
        # Кнопки убираем, текст оставляем: человек должен видеть, на что ответил.
        await message.edit_reply_markup(reply_markup=None)
    except (TelegramBadRequest, TypeError) as exc:
        logger.debug(f"payment_reminder: кнопки не убрал: {exc}")
