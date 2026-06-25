"""Email-напоминания об окончании подписки.

Шлёт письмо пользователям, у кого подписка скоро истекает, — НО только тем, у
кого НЕТ привязанного Telegram (email-only). У кого есть Telegram, тем напоминает
бот, и дубль не нужен.

Тайминг: за 3 дня и в день окончания (за ~4-5 часов). Задача крутится раз в час;
для каждой точки — окно в 1 час, поэтому каждому уходит ровно одно письмо на точку.

Авто-обнаруживается taskiq по globу tasks/*.py (см. docker-compose.yml).
"""

import os
from datetime import datetime, timedelta, timezone

from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.email_sender import EmailSender
from src.infrastructure.database.models import Subscription, User
from src.infrastructure.taskiq.broker import broker

# Точки напоминания: (часы до окончания, человеческая формулировка «когда»).
# Окно для каждой точки — 1 час (задача почасовая). Чтобы добавить «за 1 день» —
# впишите (24, "завтра").
REMINDERS: tuple[tuple[int, str], ...] = (
    (72, "через 3 дня"),
    (4, "сегодня, в течение нескольких часов"),
)


def _email_enabled() -> bool:
    return (os.environ.get("EMAIL_ENABLED") or "").strip().lower() == "true"


def _subject(hours: int) -> str:
    return "Подписка заканчивается сегодня" if hours < 24 else "Подписка скоро закончится"


def _body(when: str) -> str:
    return (
        "Здравствуйте!\n\n"
        f"Ваша подписка на VPN заканчивается {when}. "
        "Чтобы не потерять доступ, продлите её в личном кабинете.\n\n"
        "С уважением, команда сервиса."
    )


@broker.task(schedule=[{"cron": "0 * * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def send_email_expiry_reminders(
    session: FromDishka[AsyncSession],
    email_sender: FromDishka[EmailSender],
) -> None:
    if not _email_enabled():
        logger.debug("Email отключён — пропускаю напоминания об окончании")
        return

    now = datetime.now(timezone.utc)
    sent = 0

    for hours, when in REMINDERS:
        lo = now + timedelta(hours=hours)
        hi = lo + timedelta(hours=1)
        # Текущая подписка юзера (current_subscription_id), email-only, почта
        # подтверждена, expire_at попадает в часовое окно этой точки.
        stmt = (
            select(User.email)
            .join(Subscription, Subscription.id == User.current_subscription_id)
            .where(
                Subscription.expire_at >= lo,
                Subscription.expire_at < hi,
                User.telegram_id.is_(None),
                User.email.is_not(None),
                User.is_email_verified.is_(True),
            )
        )
        emails = {r[0] for r in (await session.execute(stmt)).all() if r[0]}
        for email in emails:
            try:
                await email_sender.send(
                    to=email, subject=_subject(hours), body=_body(when)
                )
                sent += 1
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Письмо-напоминание на {email} не ушло: {e}")

    if sent:
        logger.info(f"Отправлено напоминаний об окончании подписки (email): {sent}")
