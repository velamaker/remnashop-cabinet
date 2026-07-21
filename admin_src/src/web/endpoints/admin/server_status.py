"""Админ: настройка блока «Статус сервиса» в кабинете.

Хранится в assets/server_status.json (см. services/overlay_server_status.py):
тумблер показа + привязка по подписке + видимость для невошедших.
"""

from typing import Any, Optional

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter
from pydantic import BaseModel

from src.application.common import Remnawave
from src.infrastructure.services.overlay_server_status import load_config, save_config

from ._common import AdminUser

router = APIRouter(prefix="/server-status", tags=["Admin - Server status"])


class ServerStatusUpdate(BaseModel):
    enabled: Optional[bool] = None
    bind_to_subscription: Optional[bool] = None
    guest_visible: Optional[bool] = None
    visible_nodes: Optional[list[str]] = None  # UUID нод для показа ([] = все)


@router.get("")
async def get_server_status(_admin: AdminUser) -> dict[str, Any]:
    return load_config()


@router.get("/nodes")
@inject
async def list_nodes(_admin: AdminUser, remnawave: FromDishka[Remnawave]) -> dict[str, Any]:
    """Все ноды панели — для выбора видимых в статусе (uuid/имя/страна/онлайн)."""
    sdk = getattr(remnawave, "sdk", None)
    if sdk is None:
        return {"nodes": []}
    try:
        result = await sdk.nodes.get_all_nodes()
    except Exception:
        return {"nodes": []}
    raw = getattr(result, "root", result) or []
    nodes = []
    for n in raw:
        node_uuid = str(getattr(n, "uuid", "") or "")
        if not node_uuid:
            continue
        nodes.append(
            {
                "uuid": node_uuid,
                "name": getattr(n, "name", "") or "",
                "country_code": getattr(n, "country_code", "") or "",
                "online": bool(getattr(n, "is_connected", False)),
                "disabled": bool(getattr(n, "is_disabled", False)),
            }
        )
    return {"nodes": nodes}


@router.put("")
async def update_server_status(body: ServerStatusUpdate, _admin: AdminUser) -> dict[str, Any]:
    current = load_config()
    current.update(body.model_dump(exclude_none=True))
    return save_config(current)
