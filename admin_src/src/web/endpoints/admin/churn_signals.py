"""Админ: «сигналы до ухода» — настройки и сводка (overlay).

Страница «Сигналы до ухода»: два тумблера (ОБА выключены по умолчанию) и их
параметры — через сколько после первого подключения спрашивать «Всё работает?»,
сколько дней простоя считать поводом написать, как часто и сколько должно
оставаться до конца подписки.

СВОДКА отвечает на вопрос, ради которого фича сделана: сколько людей ответили и
какая доля из них сказала «не работает». Рядом — кто именно (id, чтобы открыть
карточку), сколько «давно не был» вернулись после сообщения и итог последнего
прохода. Итог прохода важен сам по себе: если панель молчала, крон никому не писал,
и владелец должен видеть это здесь, а не гадать, почему фича «ничего не делает».

Итог прохода живёт, пока сигналы включены. Выключены оба — крон не ходит, и старый
итог (с красным «панель молчала» недельной давности) читался бы как текущий: ручка
его не отдаёт, а сохранение «всё выключено» стирает файл, чтобы после включения до
первого нового прохода не всплыл прошлый.

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

from src.infrastructure.services import overlay_churn_signals as signals

from ._common import AdminUser

router = APIRouter(prefix="/churn-signals", tags=["Admin - Churn signals"])

STATS_DAYS = 30
BROKEN_LIMIT = 10


class ChurnSignalsConfigRequest(BaseModel):
    check_enabled: bool = False
    check_delay_hours: int = 24
    idle_enabled: bool = False
    idle_days: int = 7
    idle_cooldown_days: int = 30
    idle_min_days_left: int = 3


async def _stats(session: AsyncSession) -> dict[str, Any]:
    row = (await session.execute(text(signals.STATS_SQL), {"days": STATS_DAYS})).first()
    values = [int(v or 0) for v in (row or [0] * 8)]
    (check_sent, answered, works, broken, check_failed, idle_sent, returned, idle_failed) = values
    return {
        "days": STATS_DAYS,
        "check_sent": check_sent,
        "check_answered": answered,
        "check_works": works,
        "check_broken": broken,
        "check_failed": check_failed,
        # Доля считается от ОТВЕТИВШИХ: молчание — не «работает» и не «не работает».
        "check_answered_percent": signals.share_percent(answered, check_sent),
        "check_broken_percent": signals.share_percent(broken, answered),
        "idle_sent": idle_sent,
        "idle_returned": returned,
        "idle_failed": idle_failed,
        "idle_returned_percent": signals.share_percent(returned, idle_sent),
    }


@router.get("")
@inject
async def get_churn_signals(
    _admin: AdminUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    config = signals.load_config()
    stats: dict[str, Any] = {}
    broken: list[dict[str, Any]] = []
    optouts: dict[str, int] = {}
    try:
        stats = await _stats(session)
        broken = [
            {
                "user_id": int(r[0]),
                "answered_at": r[1].isoformat() if r[1] is not None else None,
            }
            for r in (
                await session.execute(text(signals.BROKEN_SQL), {"limit": BROKEN_LIMIT})
            ).all()
        ]
        optouts = {
            str(r[0]): int(r[1] or 0)
            for r in (
                await session.execute(
                    text(signals.OPTOUTS_SQL),
                    {"optout_check": signals.OPTOUT_CHECK, "optout_idle": signals.OPTOUT_IDLE},
                )
            ).all()
        }
    except Exception:  # noqa: BLE001 — таблицы ещё нет: страница открывается пустой
        await session.rollback()
    await session.commit()
    return {
        "config": config,
        "check_window_hours": signals.CHECK_WINDOW_HOURS,
        "idle_window_days": signals.IDLE_WINDOW_DAYS,
        "stats": stats,
        "broken": broken,
        "optouts": {
            "check": optouts.get(signals.OPTOUT_CHECK, 0),
            "idle": optouts.get(signals.OPTOUT_IDLE, 0),
        },
        "last_run": signals.load_last_run() if signals.any_enabled(config) else None,
    }


@router.put("")
@inject
async def put_churn_signals(
    body: ChurnSignalsConfigRequest,
    _admin: AdminUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    config = signals.save_config(body.model_dump())
    if not signals.any_enabled(config):
        signals.clear_last_run()
    # Ручной commit — правило overlay-ручек без исключений (память admin-endpoints-commit).
    await session.commit()
    return {"config": config}
