"""Email-рассылка из кабинета.

Шлёт письмо email-only пользователям (нет привязанного Telegram, почта
подтверждена, аккаунт не заблокирован) — опционально суженным по статусу
подписки сегментом. У кого есть Telegram, тем шлёт бот (TG-аудитории), дублей нет.

Прогресс пишется в overlay-таблицу email_broadcasts (см. overlay_app._SUPPORT_TABLES_DDL).
Запись создаёт эндпоинт POST /admin/broadcasts, сюда прилетает её id и сегмент.

Авто-обнаруживается taskiq по глобу tasks/*.py (см. docker-compose.yml).
"""

import asyncio
import os

from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.email_sender import EmailSender
from src.infrastructure.taskiq.broker import broker

# email-only, подтверждённая почта, не заблокирован.
_BASE = (
    "u.telegram_id IS NULL AND u.email IS NOT NULL "
    "AND u.is_email_verified = true AND u.is_blocked = false"
)
_JOIN = "FROM users u JOIN subscriptions s ON u.current_subscription_id = s.id"

# Сегмент → FROM/WHERE (без SELECT). Статусы совпадают с TG-аудиториями базы:
# с подпиской = ACTIVE, пробный = is_trial, истекла = EXPIRED, заканчивается =
# активна и истекает в ближайшие 7 дней.
EMAIL_SEGMENT_FROM: dict[str, str] = {
    "EMAIL_ALL": f"FROM users u WHERE {_BASE}",
    "EMAIL_SUBSCRIBED": f"{_JOIN} WHERE {_BASE} AND s.status = 'ACTIVE' AND s.is_trial = false",
    "EMAIL_TRIAL": f"{_JOIN} WHERE {_BASE} AND s.is_trial = true",
    "EMAIL_EXPIRING": (
        f"{_JOIN} WHERE {_BASE} AND s.status = 'ACTIVE' "
        "AND s.expire_at >= now() AND s.expire_at < now() + interval '7 days'"
    ),
    "EMAIL_EXPIRED": f"{_JOIN} WHERE {_BASE} AND s.status = 'EXPIRED'",
}


def _email_enabled() -> bool:
    return (os.environ.get("EMAIL_ENABLED") or "").strip().lower() == "true"


@broker.task
@inject(patch_module=True)
async def send_email_broadcast(
    broadcast_id: int,
    subject: str,
    body: str,
    segment: str = "EMAIL_ALL",
    session: FromDishka[AsyncSession] = None,  # type: ignore[assignment]
    email_sender: FromDishka[EmailSender] = None,  # type: ignore[assignment]
) -> None:
    async def _update(**cols: object) -> None:
        assignments = ", ".join(f"{k} = :{k}" for k in cols)
        params = {**cols, "id": broadcast_id}
        await session.execute(
            text(f"UPDATE email_broadcasts SET {assignments} WHERE id = :id"), params
        )
        await session.commit()

    if not _email_enabled():
        logger.warning(f"Email отключён — email-рассылка #{broadcast_id} помечена ERROR")
        await _update(status="ERROR")
        return

    frm = EMAIL_SEGMENT_FROM.get(segment, EMAIL_SEGMENT_FROM["EMAIL_ALL"])
    rows = (await session.execute(text(f"SELECT u.email {frm}"))).all()
    emails = sorted({r[0] for r in rows if r[0]})
    total = len(emails)
    await _update(total_count=total)

    ok = 0
    fail = 0
    for i, email in enumerate(emails, 1):
        try:
            await email_sender.send(to=email, subject=subject, body=body)
            ok += 1
        except Exception as e:  # noqa: BLE001
            fail += 1
            logger.warning(f"Email-рассылка #{broadcast_id}: письмо на {email} не ушло: {e}")
        # Не молотим SMTP без пауз + периодически сохраняем прогресс.
        await asyncio.sleep(0.05)
        if i % 20 == 0:
            await _update(success_count=ok, failed_count=fail)

    await _update(status="COMPLETED", total_count=total, success_count=ok, failed_count=fail)
    logger.info(
        f"Email-рассылка #{broadcast_id} ({segment}) завершена: ok={ok} fail={fail} total={total}"
    )
