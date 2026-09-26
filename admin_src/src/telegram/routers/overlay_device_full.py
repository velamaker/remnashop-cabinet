"""Кнопка «Не присылать такое» под сообщением «все места для устройств заняты» (overlay).

Пишет строку в `notification_optouts` (вид `device_full` — только это сообщение, другие
уведомления не трогает) и убирает кнопки. Повторное нажатие безопасно: запись идёт
ON CONFLICT DO NOTHING. Фильтр строго по своему префиксу: наши роутеры стоят перед
базовыми, и широкий фильтр перехватил бы чужие колбэки.
"""

from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery
from dishka import FromDishka
from dishka.integrations.aiogram import inject
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.constants import USER_KEY
from src.infrastructure.services.overlay_device_full import (
    OPTOUT_CALLBACK,
    OPTOUT_KIND,
    OPTOUT_SQL,
    texts_for,
)
from src.telegram.overlay_markup import drop_buttons

router = Router(name="overlay_device_full")


@router.callback_query(F.data == OPTOUT_CALLBACK)
@inject
async def on_device_full_optout(
    callback: CallbackQuery, db_session: FromDishka[AsyncSession], **data: Any
) -> None:
    user = data.get(USER_KEY)
    user_id = getattr(user, "id", None)
    lang = getattr(user, "language", None)
    words = texts_for(str(lang) if lang else None)
    if user_id is None:
        await callback.answer()
        return
    try:
        await db_session.execute(text(OPTOUT_SQL), {"user_id": int(user_id), "kind": OPTOUT_KIND})
        await db_session.commit()
    except Exception as exc:  # noqa: BLE001 — отказ не должен падать человеку в лицо
        await db_session.rollback()
        logger.warning(f"device_full: отказ user_id={user_id} не записан: {exc}")
        await callback.answer("Не получилось, попробуйте ещё раз", show_alert=True)
        return

    await callback.answer(words["done"])
    await drop_buttons(callback, tag="device_full")
