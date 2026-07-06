"""Импорт/синхронизация пользователей в кабинете — как в боте («Импорт пользователей»).

3 режима (переиспользуют базовые таски/юзкейсы):
  • sync-panel — подтянуть юзеров из панели Remnawave в бота (sync_all_users_from_panel_task);
  • sync-bot   — отправить юзеров бота в панель (sync_all_users_from_bot_task);
  • xui        — импорт из файла БД x-ui/3x-ui (ExportUsersFromXui → import_exported_users_task).
Redis-ключи (SyncPanel/SyncBot/ImportRunningKey) защищают от параллельных запусков.
"""

import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from adaptix import Retort
from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from redis.asyncio import Redis

from src.application.common import Remnawave
from src.application.use_cases.importer.queries.xui import ExportUsersFromXui
from src.infrastructure.redis.keys import (
    ImportRunningKey,
    SyncBotRunningKey,
    SyncPanelRunningKey,
)
from src.infrastructure.taskiq.tasks.importer import (
    import_exported_users_task,
    sync_all_users_from_bot_task,
    sync_all_users_from_panel_task,
)

from ._common import AdminUser

router = APIRouter(prefix="/import", tags=["Admin - Import"])


@router.get("/status")
@inject
async def import_status(
    _admin: AdminUser,
    redis: FromDishka[Redis],
    retort: FromDishka[Retort],
) -> dict[str, bool]:
    return {
        "panel": bool(await redis.get(retort.dump(SyncPanelRunningKey()))),
        "bot": bool(await redis.get(retort.dump(SyncBotRunningKey()))),
        "xui": bool(await redis.get(retort.dump(ImportRunningKey()))),
    }


@router.get("/squads")
@inject
async def import_squads(
    _admin: AdminUser,
    remnawave: FromDishka[Remnawave],
) -> dict[str, Any]:
    sdk = getattr(remnawave, "sdk", None)
    if sdk is None:
        return {"squads": []}
    try:
        res = await sdk.internal_squads.get_internal_squads()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"RemnaWave error: {e}")
    squads = getattr(res, "internal_squads", []) or []
    return {"squads": [{"uuid": str(s.uuid), "name": getattr(s, "name", "") or ""} for s in squads]}


async def _run_sync(redis: Redis, key: str, task) -> int:
    if await redis.get(key):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Синхронизация уже идёт")
    await redis.set(key, value=1, ex=600)
    try:
        kicked = await task.kiq()  # type: ignore[call-overload]
        result = await kicked.wait_result()
        count = result.return_value
    finally:
        await redis.delete(key)
    return int(count or 0)


@router.post("/sync-panel")
@inject
async def sync_from_panel(
    _admin: AdminUser,
    redis: FromDishka[Redis],
    retort: FromDishka[Retort],
) -> dict[str, Any]:
    synced = await _run_sync(redis, retort.dump(SyncPanelRunningKey()), sync_all_users_from_panel_task)
    return {"success": True, "synced": synced}


@router.post("/sync-bot")
@inject
async def sync_from_bot(
    _admin: AdminUser,
    redis: FromDishka[Redis],
    retort: FromDishka[Retort],
) -> dict[str, Any]:
    synced = await _run_sync(redis, retort.dump(SyncBotRunningKey()), sync_all_users_from_bot_task)
    return {"success": True, "synced": synced}


@router.post("/xui")
@inject
async def import_from_xui(
    admin: AdminUser,
    redis: FromDishka[Redis],
    retort: FromDishka[Retort],
    export_users: FromDishka[ExportUsersFromXui],
    file: UploadFile = File(...),
    squads: str = Form(...),
) -> dict[str, Any]:
    key = retort.dump(ImportRunningKey())
    if await redis.get(key):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Импорт уже идёт")

    squad_uuids = []
    for s in squads.split(","):
        s = s.strip()
        if s:
            try:
                squad_uuids.append(UUID(s))
            except ValueError:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректный сквад")
    if not squad_uuids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Выберите хотя бы один сквад")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
        tmp.write(await file.read())
        path = Path(tmp.name)

    try:
        users = await export_users._execute(admin, path)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Не удалось разобрать файл: {e}")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    if not users:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="В файле не найдено пользователей")

    # Импорт идёт в фоне (taskiq); ставим running-ключ, фронт опрашивает /status.
    await redis.set(key, value=1, ex=3600)
    await import_exported_users_task.kiq(users, squad_uuids)  # type: ignore[call-overload]

    return {"success": True, "found": len(users), "started": True}
