"""Клавиатура напоминания о незавершённой оплате (overlay).

Отдельным модулем, а не внутри крона: кнопки нужны и обработчику нажатия, и тестам,
а тянуть ради них весь taskiq незачем.

ДВЕ КНОПКИ:
  • «Открыть оплату» — ссылка в КАБИНЕТ (не на старый счёт: почему — в миграции 0013).
    Если адреса кабинета нет, кнопки нет вовсе: пустой url Telegram не принимает и
    отверг бы сообщение целиком.
  • «Не напоминать» — единственный способ сказать «не пиши»: в базе такого поля не
    было (is_blocked — бан магазина, is_bot_blocked — уже случившаяся блокировка).
    Нажатие пишет строку в notification_optouts, и напоминаний больше не будет.
"""

from typing import Any, Optional

# Префикс callback-данных нашей кнопки. Своё пространство имён, чтобы не пересечься
# с базовыми обработчиками (правило «не перехватывать чужие ручки»).
OPTOUT_CALLBACK = "rs_pay_reminder_off"

TEXTS = {
    "ru": {"open": "💳 Открыть оплату", "off": "Не напоминать"},
    "en": {"open": "💳 Open checkout", "off": "Don't remind me"},
}


def texts_for(lang: Optional[str]) -> dict[str, str]:
    return TEXTS["en"] if (lang or "ru").lower().startswith("en") else TEXTS["ru"]


def reminder_keyboard(url: str, lang: Optional[str] = None) -> Any:
    """Разметка сообщения. Без адреса кабинета остаётся только «Не напоминать»."""
    from aiogram.types import InlineKeyboardButton
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    words = texts_for(lang)
    builder = InlineKeyboardBuilder()
    if url:
        builder.row(InlineKeyboardButton(text=words["open"], url=url))
    builder.row(InlineKeyboardButton(text=words["off"], callback_data=OPTOUT_CALLBACK))
    return builder.as_markup()
