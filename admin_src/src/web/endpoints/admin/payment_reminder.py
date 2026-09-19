"""Админ: напоминание о незавершённой оплате — настройки и сводка (overlay).

Страница «Напоминание об оплате»: тумблер (ВЫКЛ по умолчанию), через сколько минут
писать, до какого возраста счёта это ещё уместно, частота на человека.

ЧЕГО ЗДЕСЬ НЕТ И ПОЧЕМУ. Ни ссылки на счёт, ни «повторить отправку»: напоминание
ведёт человека в кабинет начать оплату заново, а досылки нет намеренно — сообщение
«оплата не завершилась» через два часа читается как спам, а не как помощь.

СВОДКА показывает не только отправленное, но и КОГО НЕ ТРОГАЛИ и почему: «уже
заплатил», «ушёл в другой шлюз», «отказался», «кулдаун». Это главный инструмент
доверия к фиче: по нему видно, что она молчит там, где должна молчать.

Правки настроек — не-GET, то есть PREVIEW-админу их отдаёт 403 общим механизмом.
Коммит сессии ручной: overlay-ручки живут вне UoW базы.
"""

from typing import Any

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.services import overlay_payment_reminder as reminder

from ._common import AdminUser

router = APIRouter(prefix="/payment-reminder", tags=["Admin - Payment reminder"])


class PaymentReminderConfigRequest(BaseModel):
    enabled: bool = False
    delay_minutes: int = 10
    max_age_minutes: int = 45
    cooldown_hours: int = 24
    max_per_30d: int = 3
    notify_admins: bool = False


# Сколько брошенных счетов есть прямо сейчас — чтобы владелец видел масштаб до
# включения. Только клиенты: персонал и проверочные платежи из денег исключены везде.
BACKLOG_SQL = """
SELECT count(*) AS invoices,
       count(DISTINCT t.user_id) AS people,
       coalesce(sum((t.pricing->>'final_amount')::numeric), 0) AS amount
  FROM transactions t
  JOIN users u ON u.id = t.user_id
 WHERE t.created_at > now() - interval '30 days'
   AND t.status::text IN ('PENDING', 'CANCELED')
   AND coalesce(t.is_test, false) = false
   AND coalesce(u.role::text, 'USER') = 'USER'
   AND (t.plan_snapshot->>'id')::int > 0
   AND NOT EXISTS (SELECT 1 FROM transactions p
                    WHERE p.user_id = t.user_id AND p.status::text = 'COMPLETED'
                      AND p.updated_at >= t.created_at)
"""


@router.get("")
@inject
async def get_payment_reminder_config(
    _admin: AdminUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    config = reminder.load_config()
    summary: list[dict[str, Any]] = []
    conversion: dict[str, Any] = {}
    backlog: dict[str, Any] = {}
    try:
        summary = [
            {"status": r[0], "detail": r[1], "count": int(r[2] or 0)}
            for r in (await session.execute(text(reminder.SUMMARY_SQL), {"hours": 720})).all()
        ]
        row = (await session.execute(text(reminder.CONVERSION_SQL), {"days": 30})).first()
        conversion = {
            "sent_30d": int(row[0] or 0) if row else 0,
            "paid_after_30d": int(row[1] or 0) if row else 0,
        }
        back = (await session.execute(text(BACKLOG_SQL))).first()
        backlog = {
            "invoices_30d": int(back[0] or 0) if back else 0,
            "people_30d": int(back[1] or 0) if back else 0,
            "amount_30d": float(back[2] or 0) if back else 0.0,
        }
    except Exception:  # noqa: BLE001 — таблиц ещё нет: страница открывается пустой
        await session.rollback()
    await session.commit()
    return {
        "config": config,
        "effective_enabled": reminder.effective_enabled(config),
        "summary": summary,
        "conversion": conversion,
        "backlog": backlog,
    }


@router.put("")
@inject
async def put_payment_reminder_config(
    body: PaymentReminderConfigRequest,
    _admin: AdminUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    config = reminder.save_config(body.model_dump())
    # Ручной commit — правило overlay-ручек без исключений (память admin-endpoints-commit).
    await session.commit()
    return {"config": config, "effective_enabled": reminder.effective_enabled(config)}
