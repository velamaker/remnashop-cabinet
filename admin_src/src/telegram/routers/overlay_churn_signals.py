"""Кнопки под «сигналами до ухода»: ответ на «Всё работает?» и «Не присылать такое».

ОТВЕТ И ЕСТЬ СМЫСЛ ВОПРОСА. «✅ Всё работает» и «❌ Не работает» пишутся в строку
сообщения (`churn_signals.answer`) — по ним владелец видит в админке, какая доля
только что подключившихся на деле не может пользоваться. Колбэк несёт id строки, а
владельца строки проверяет сам UPDATE: чужой id не меняет чужой ответ.

«НЕ РАБОТАЕТ» — НЕ «НАПИШИТЕ НАМ». Человек получает отдельное сообщение с кнопкой
самопроверки в кабинете (/support: там мастер, который находит частые причины и
собирает паспорт обращения) и кнопкой поддержки. Отдельным сообщением, а не правкой
вопроса: вопрос мог уйти rich-сообщением, а править его текст Bot API может не дать.
Кнопки под вопросом убираем — ответ принят, повторно нажимать незачем.

«НЕ ПРИСЫЛАТЬ ТАКОЕ» пишет свой вид в notification_optouts; крон молчит, если есть
любой из двух видов. Повторное нажатие безопасно: ON CONFLICT DO NOTHING.

ФИЛЬТР — строго свой префикс `rs_sig:`: наши роутеры встают ПЕРЕД базовыми
(bot_routers.py), и широкий фильтр перехватил бы чужие колбэки.
"""

from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery
from dishka import FromDishka
from dishka.integrations.aiogram import inject
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import AppConfig
from src.core.constants import USER_KEY
from src.infrastructure.services import overlay_churn_signals as signals
from src.telegram.overlay_markup import drop_buttons

router = Router(name="overlay_churn_signals")


async def _drop_buttons(callback: CallbackQuery) -> None:
    # Текст оставляем: человек должен видеть, на что ответил. Под вопросом недельной
    # давности Telegram присылает «недоступное» сообщение без методов правки — общий
    # помощник снимает кнопки и с него (через бота по номеру), а не глотает
    # AttributeError, оставляя их живыми.
    await drop_buttons(callback, tag="churn_signals")


async def handle(
    callback: CallbackQuery,
    session: AsyncSession,
    config: Any,
    user: Any,
) -> str:
    """Разбор нажатия. Возвращает, что сделали, — ради тестов и лога."""
    parsed = signals.parse_callback(callback.data)
    user_id = getattr(user, "id", None)
    lang = getattr(user, "language", None)
    lang = str(lang) if lang else None
    words = signals.words_for(lang)
    if parsed is None or user_id is None:
        # Не наше или неизвестный отправитель: гасим «часики», в базу не пишем.
        await callback.answer()
        return "ignored"

    action, arg = parsed
    try:
        if action == "off":
            await session.execute(
                text(signals.OPTOUT_SQL),
                {"user_id": int(user_id), "kind": signals.OPTOUT_BY_KIND[arg]},
            )
            await session.commit()
            await callback.answer(words["off_done"])
            await _drop_buttons(callback)
            return "opted_out"

        row = (
            await session.execute(
                text(signals.ANSWER_SQL),
                {"answer": action, "id": int(arg), "user_id": int(user_id)},
            )
        ).first()
        await session.commit()
    except Exception as exc:  # noqa: BLE001 — ответ не должен падать человеку в лицо
        await session.rollback()
        logger.warning(f"churn_signals: нажатие user_id={user_id} не записано: {exc}")
        await callback.answer(words["retry"], show_alert=True)
        return "error"

    if row is None:
        # Строка не его или её уже нет (человека удалили и завели заново).
        await callback.answer(words["stale"])
        await _drop_buttons(callback)
        return "stale"

    if action == "works":
        await callback.answer(words["thanks"])
        await _drop_buttons(callback)
        return "works"

    await callback.answer(words["sorry"])
    await _drop_buttons(callback)
    cabinet_url = getattr(config, "web_cabinet_url", "") or ""
    markup = signals.broken_keyboard(cabinet_url, signals.support_link(config), lang)
    await _send_help(callback, signals.broken_message(lang), markup)
    return "broken"


async def _send_help(callback: CallbackQuery, text_html: str, markup: Any) -> None:
    """Подсказка «не работает» — в личку человеку, а не ответом на вопрос.

    Через бота и id нажавшего, потому что вопрос мог устареть: у сообщений старше
    двух суток Telegram отдаёт в колбэке «недоступное сообщение», на которое не
    ответить, — а человек, нажавший «не работает» через неделю, нуждается в ссылке
    не меньше. Ответ к этому моменту уже записан, поэтому сбой здесь только в лог.
    """
    bot = getattr(callback, "bot", None)
    chat_id = getattr(getattr(callback, "from_user", None), "id", None)
    try:
        if bot is not None and chat_id is not None:
            await bot.send_message(chat_id=chat_id, text=text_html, reply_markup=markup)
            return
        message = callback.message
        if message is not None and hasattr(message, "answer"):
            await message.answer(text_html, reply_markup=markup)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"churn_signals: подсказку «не работает» не отправил: {exc}")


@router.callback_query(F.data.startswith(signals.CALLBACK_PREFIX))
@inject
async def on_signal_button(
    callback: CallbackQuery,
    # Имена НАРОЧНО не `session`/`config`. Диспетчер базы создан как
    # `Dispatcher(storage=…, config=config)`, поэтому `config` лежит в данных
    # КАЖДОГО апдейта. Обработчику с `**data` aiogram отдаёт их все, а обёртка dishka
    # зовёт его с `**kwargs, **solved` — одноимённый параметр приходил дважды, и
    # каждое нажатие ✅/❌/🔕 падало TypeError: ответ не записывался. Правило для всех
    # обработчиков сторожит tests/test_dishka_handler_kwargs.py.
    db_session: FromDishka[AsyncSession],
    app_config: FromDishka[AppConfig],
    **data: Any,
) -> None:
    await handle(callback, db_session, app_config, data.get(USER_KEY))
