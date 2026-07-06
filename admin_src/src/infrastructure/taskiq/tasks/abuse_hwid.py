"""Периодический снимок HWID-устройств пользователей (overlay).

В нашей БД устройств нет — Remnawave отдаёт их только per-user (нет «все разом»).
Раз в 6 часов обходим активных USER-ов (у кого есть текущая подписка = uuid) и
складываем их HWID в overlay-таблицу hwid_devices. По этим данным детект абьюза
(admin/abuse.py) ловит сигнал «один HWID → разные аккаунты».

Авто-обнаруживается taskiq по globу tasks/*.py (см. docker-compose.yml). Задача
best-effort: ошибка по одному юзеру не роняет весь проход.
"""

import asyncio

from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Remnawave
from src.infrastructure.taskiq.broker import broker

# Пауза между вызовами SDK, чтобы не долбить панель (≈224 юзера → ~единицы минут).
_SLEEP_BETWEEN = 0.1


@broker.task(schedule=[{"cron": "17 */6 * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def snapshot_hwid_devices(
    session: FromDishka[AsyncSession],
    remnawave: FromDishka[Remnawave],
) -> None:
    sdk = getattr(remnawave, "sdk", None)
    if sdk is None:
        logger.warning("abuse_hwid: Remnawave SDK недоступен — снимок пропущен")
        return

    # Активные обычные юзеры: id + uuid текущей подписки.
    rows = (
        await session.execute(
            text(
                "SELECT u.id, s.user_remna_id "
                "FROM users u "
                "JOIN subscriptions s ON s.id = u.current_subscription_id "
                "WHERE u.role = 'USER' AND s.user_remna_id IS NOT NULL"
            )
        )
    ).all()

    scanned = 0
    devices_total = 0
    errors = 0
    for user_id, uuid in rows:
        try:
            resp = await sdk.hwid.get_hwid_user(str(uuid))
        except Exception as e:
            errors += 1
            logger.debug(f"abuse_hwid: не удалось получить устройства user={user_id}: {e}")
            await asyncio.sleep(_SLEEP_BETWEEN)
            continue

        hwids = {
            d.hwid.strip()
            for d in (getattr(resp, "devices", None) or [])
            if getattr(d, "hwid", None) and d.hwid.strip()
        }
        # Полностью пересобираем строки юзера: снимок = текущее состояние панели.
        await session.execute(
            text("DELETE FROM hwid_devices WHERE user_id = :u"), {"u": user_id}
        )
        for hwid in hwids:
            await session.execute(
                text(
                    "INSERT INTO hwid_devices (user_id, hwid, updated_at) "
                    "VALUES (:u, :h, now()) ON CONFLICT DO NOTHING"
                ),
                {"u": user_id, "h": hwid[:256]},
            )
        await session.commit()
        scanned += 1
        devices_total += len(hwids)
        await asyncio.sleep(_SLEEP_BETWEEN)

    logger.info(
        f"abuse_hwid: снимок готов — юзеров {scanned}/{len(rows)}, "
        f"устройств {devices_total}, ошибок {errors}"
    )
