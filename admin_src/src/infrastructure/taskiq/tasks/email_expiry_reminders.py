"""Email-напоминания об окончании подписки.

Шлёт письмо пользователям, у кого подписка скоро истекает, — НО только тем, у
кого НЕТ привязанного Telegram (email-only). У кого есть Telegram, тем напоминает
бот, и дубль не нужен.

Тайминг: за 3 дня и в день окончания (за ~4-5 часов). Задача крутится раз в час;
для каждой точки — окно в 1 час, поэтому каждому уходит ровно одно письмо на точку.

Авто-обнаруживается taskiq по globу tasks/*.py (см. docker-compose.yml).
"""

from datetime import datetime, timedelta, timezone

from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.email_sender import EmailSender
from src.infrastructure.database.models import Subscription, User
from src.infrastructure.services.overlay_renewal_discount import (
    ActiveGrant,
    active_grant_offers_by_user,
    email_discount_line,
)
from src.infrastructure.taskiq.broker import broker

# Точки напоминания: (часы до окончания, человеческая формулировка «когда»).
# Окно для каждой точки — 1 час (задача почасовая). Чтобы добавить «за 1 день» —
# впишите (24, "завтра").
REMINDERS: tuple[tuple[int, str], ...] = (
    (72, "через 3 дня"),
    (4, "сегодня, в течение нескольких часов"),
)


def _subject(hours: int) -> str:
    return "Подписка заканчивается сегодня" if hours < 24 else "Подписка скоро закончится"


def _brand() -> str:
    """Имя сервиса. Не «VPN»: письмо от Begemot VPN, подписанное «команда сервиса»,
    выглядит как рассылка неизвестно от кого."""
    try:
        from src.web.endpoints.public.appearance import resolve_brand_name

        return resolve_brand_name() or "VPN"
    except Exception:  # noqa: BLE001 — бренд не повод не отправить письмо
        return "VPN"


def _body(
    when: str,
    discount: ActiveGrant | None = None,
    sub_expire_at: datetime | None = None,
) -> str:
    # Подпись и ссылку на кабинет НЕ дублируем: их добавляет оформление письма
    # (шапка с логотипом, кнопка «Открыть кабинет», подвал с брендом).
    body = (
        "Здравствуйте!\n\n"
        f"Ваша подписка {_brand()} заканчивается {when}. "
        "Продлите её, чтобы не потерять доступ."
    )
    # Скидка на продление людям только с почтой сообщается ЭТИМ письмом: отдельного
    # письма о ней нет (см. services/overlay_renewal_discount.py, channels). Срок
    # скидки передаём обязательно: она может сгореть раньше подписки.
    if discount is not None and discount.percent:
        body += "\n\n" + email_discount_line(
            discount.percent,
            grant_expires_at=discount.expires_at,
            sub_expire_at=sub_expire_at,
        )
    return body


@broker.task(schedule=[{"cron": "0 * * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def send_email_expiry_reminders(
    session: FromDishka[AsyncSession],
    email_sender: FromDishka[EmailSender],
) -> None:
    # Спрашиваем отправитель, а не переменную окружения: почту включают ещё и в
    # админке, и у такой установки напоминания молчали бы при работающей почте.
    if not email_sender.is_enabled:
        logger.debug("Email не настроен — пропускаю напоминания об окончании")
        return
    await send_reminders(session, email_sender, datetime.now(timezone.utc))


async def send_reminders(session: AsyncSession, email_sender: EmailSender, now: datetime) -> int:
    """Один проход по точкам напоминаний. Отдельно от задачи — чтобы тест звал его без DI."""
    sent = 0

    for hours, when in REMINDERS:
        lo = now + timedelta(hours=hours)
        hi = lo + timedelta(hours=1)
        # Текущая подписка юзера (current_subscription_id), email-only, почта
        # подтверждена, expire_at попадает в часовое окно этой точки.
        stmt = (
            select(User.id, User.email, Subscription.expire_at)
            .join(Subscription, Subscription.id == User.current_subscription_id)
            .where(
                Subscription.expire_at >= lo,
                Subscription.expire_at < hi,
                User.telegram_id.is_(None),
                User.email.is_not(None),
                User.is_email_verified.is_(True),
                # Сидящих на резерве пропускаем: у них expire_at — это срок РЕЗЕРВА, а
                # не подписки. Подписка у такого человека уже кончилась, и «продлите,
                # осталось 3 дня» про бесплатную страховку только сбивает с толку —
                # 9 сентября письмо ушло тому, у кого подписка истекла ещё 22 августа.
                # Через text(): reserve_grants заведена overlay-миграцией, модели у неё
                # нет, а тянуть её сюда ради одного условия ни к чему.
                text(
                    "NOT EXISTS (SELECT 1 FROM reserve_grants r "
                    "WHERE r.user_id = users.id AND r.ended = false)"
                ),
            )
        )
        # Почта уникальна на человека; словарь сохраняет прежний дедуп по адресу.
        recipients = {
            r[1]: (r[0], r[2]) for r in (await session.execute(stmt)).all() if r[1]
        }
        # Строка про скидку — только в письме за 3 дня: в день окончания скидки уже
        # нет (она живёт не дольше подписки). Отдельный try: без строки письмо
        # всё равно обязано уйти.
        discounts: dict[int, ActiveGrant] = {}
        if hours >= 24 and recipients:
            try:
                discounts = await active_grant_offers_by_user(
                    session, [uid for uid, _ in recipients.values()], now
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Скидка на продление для писем не прочитана: {e}")
        for email, (user_id, expire_at) in recipients.items():
            try:
                await email_sender.send(
                    to=email,
                    subject=_subject(hours),
                    body=_body(when, discounts.get(user_id), expire_at),
                )
                sent += 1
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Письмо-напоминание на {email} не ушло: {e}")

    if sent:
        logger.info(f"Отправлено напоминаний об окончании подписки (email): {sent}")
    return sent
