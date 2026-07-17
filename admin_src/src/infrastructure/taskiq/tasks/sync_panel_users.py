"""Авто-синхрон пользователей из панели Remnawave в кабинет/бота.

Проблема: юзеры, созданные ПРЯМО в панели (не через бота), не появляются в
кабинете — их надо подтягивать «Импортом» вручную. Здесь то же самое, но по
расписанию: раз в 30 минут вызываем базовый use-case SyncAllUsersFromPanel
(создаёт в БД бота недостающих панельных юзеров, линкует по remna_uuid).

Redis-лок SyncPanelRunningKey тот же, что у ручного импорта, — не запускаемся
параллельно с ним. Auto-discover taskiq по глобу tasks/*.py.

Выключатель: env AUTO_SYNC_PANEL_USERS (по умолчанию ON).
"""

import os

from adaptix import Retort
from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger
from redis.asyncio import Redis

from src.application.use_cases.remnawave.commands.synchronization import (
    SyncAllUsersFromPanel,
)
from src.infrastructure.redis.keys import SyncPanelRunningKey
from src.infrastructure.taskiq.broker import broker


def _enabled() -> bool:
    return (os.environ.get("AUTO_SYNC_PANEL_USERS") or "true").strip().lower() in (
        "1", "true", "yes", "on", "да",
    )


@broker.task(schedule=[{"cron": "*/30 * * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def auto_sync_panel_users(
    sync_all_users: FromDishka[SyncAllUsersFromPanel],
    redis: FromDishka[Redis],
    retort: FromDishka[Retort],
) -> None:
    if not _enabled():
        return
    key = retort.dump(SyncPanelRunningKey())
    if await redis.get(key):
        return  # уже идёт (ручной импорт или прошлый прогон) — не дублируем
    await redis.set(key, value=1, ex=600)
    try:
        res = await sync_all_users.system()
        added = res.get("added_users") if isinstance(res, dict) else None
        if added:
            logger.info(f"auto-sync panel: добавлено {added} юзеров из панели ({res})")
    except Exception as e:  # noqa: BLE001 — авто-синхрон не должен ронять воркер
        logger.warning(f"auto-sync panel: ошибка: {e}")
    finally:
        await redis.delete(key)
