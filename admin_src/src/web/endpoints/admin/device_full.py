"""Админ: сообщение «все места для устройств заняты» — тумблер и итог прохода (overlay).

Живёт карточкой на странице «Докупка устройств»: это одна тема — люди упираются в
лимит устройств, а место можно докупить или освободить. Сводка берётся из файла
состояния крона: сколько людей заполнены сейчас, скольким написали в последнем
проходе и почему остальным нет. Правка настроек — не-GET, PREVIEW-админу её отдаёт
403 общим механизмом (admin/_common.py).
"""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from src.infrastructure.services import overlay_device_full as df

from ._common import AdminUser

router = APIRouter(prefix="/device-full", tags=["Admin - Device full"])


class DeviceFullConfigRequest(BaseModel):
    enabled: bool = False
    cooldown_days: int = 7


def _summary() -> dict[str, Any]:
    state = df.load_state()
    return {
        "baselined": state["baselined"],
        "full_now": len(state["full"]),
        "last_run": state["last_run"],
    }


@router.get("")
async def get_device_full(_admin: AdminUser) -> dict[str, Any]:
    return {"config": df.load_config(), **_summary()}


@router.put("")
async def put_device_full(body: DeviceFullConfigRequest, _admin: AdminUser) -> dict[str, Any]:
    return {"config": df.save_config(body.model_dump()), **_summary()}
