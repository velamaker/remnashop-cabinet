"""Убрать кнопки из сообщения, под которым нажали, — и у старого сообщения тоже.

ЗАЧЕМ ОТДЕЛЬНЫЙ МОДУЛЬ. Кнопки после ответа убирают несколько наших роутеров
(«сигналы до ухода», «не напоминать об оплате»), и у всех была одна и та же дыра.

ДЫРА. У нажатия под сообщением, которое бот уже не может прочитать (на деле — старше
примерно двух суток), Telegram присылает вместо сообщения «недоступное»
(`InaccessibleMessage`): только чат и номер, без текста и без методов правки.
`message.edit_reply_markup` у него нет вовсе. Прежний код падал на AttributeError и
либо глотал её — кнопки оставались живыми, человек, ответивший через неделю, видел
их и жал снова, — либо не глотал, и нажатие, уже записанное в базу, заканчивалось
ошибкой в обработчике.

ПОЧЕМУ ЧЕРЕЗ БОТА. Bot API не запрещает править разметку старых сообщений бота:
нужны только chat_id и message_id, а они у недоступного сообщения есть. Поэтому,
когда у сообщения нет своего метода правки, идём через `bot.edit_message_reply_markup`
по номеру.

ЛЮБАЯ НЕУДАЧА — ТОЛЬКО В ЛОГ. Ответ человека к этому моменту уже записан; снятие
кнопок — косметика, и ронять из-за неё обработку нельзя. Но сбой не API (а ошибка в
нашем коде) пишем предупреждением, а не на уровне debug: именно тихое проглатывание
AttributeError и спрятало эту дыру.
"""

from typing import Any

from aiogram.exceptions import TelegramAPIError
from loguru import logger


async def drop_buttons(callback: Any, *, tag: str) -> bool:
    """Убрать клавиатуру под сообщением нажатой кнопки. True — убрали.

    `tag` — префикс для лога, чтобы было видно, чей роутер не смог.
    """
    message = getattr(callback, "message", None)
    if message is None:
        return False
    try:
        edit = getattr(message, "edit_reply_markup", None)
        if edit is not None:
            # Обычное сообщение: текст оставляем — человек должен видеть, на что ответил.
            await edit(reply_markup=None)
            return True
        # Недоступное сообщение: своих методов правки нет, правим по номеру через бота.
        bot = getattr(callback, "bot", None)
        chat_id = getattr(getattr(message, "chat", None), "id", None)
        message_id = getattr(message, "message_id", None)
        if bot is None or chat_id is None or message_id is None:
            logger.debug(f"{tag}: кнопки не убрал — нет бота или номера сообщения")
            return False
        await bot.edit_message_reply_markup(
            chat_id=chat_id, message_id=message_id, reply_markup=None
        )
        return True
    except TelegramAPIError as exc:
        # «message is not modified», сообщение удалено человеком и т. п. — ожидаемо.
        logger.debug(f"{tag}: кнопки не убрал: {exc}")
    except Exception as exc:  # noqa: BLE001 — косметика не роняет уже записанный ответ
        logger.warning(f"{tag}: кнопки не убрал из-за ошибки в коде: {exc!r}")
    return False
