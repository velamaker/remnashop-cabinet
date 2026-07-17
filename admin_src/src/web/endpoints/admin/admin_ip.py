"""Админ: ограничение доступа в админку по IP (owner-only).

Хранится в assets/admin_ip.json (см. services/overlay_admin_ip.py). Проверку делает
`_get_admin_user` (_common.py). GET отдаёт текущий IP запрашивающего — чтобы владелец
добавил его и не залочился. Fail-safe: пустой список = как выключено.
"""

from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from src.core.enums import Role
from src.infrastructure.services.overlay_admin_ip import load_config, save_config

from ._common import AdminUser

router = APIRouter(prefix="/admin-ip", tags=["Admin - IP restriction"])


def _require_owner(admin: Any) -> None:
    if admin.role < Role.OWNER:
        raise HTTPException(status_code=403, detail="Доступно только владельцу")


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    return (xff.split(",")[0].strip() if xff else "") or (
        request.client.host if request.client else ""
    )


class AdminIpUpdate(BaseModel):
    enabled: Optional[bool] = None
    allowed_ips: Optional[List[str]] = None


@router.get("")
async def get_admin_ip(admin: AdminUser, request: Request) -> dict[str, Any]:
    _require_owner(admin)
    return {**load_config(), "your_ip": _client_ip(request)}


@router.put("")
async def update_admin_ip(body: AdminIpUpdate, admin: AdminUser) -> dict[str, Any]:
    _require_owner(admin)
    current = load_config()
    if body.enabled is not None:
        current["enabled"] = body.enabled
    if body.allowed_ips is not None:
        current["allowed_ips"] = body.allowed_ips
    return save_config(current)
