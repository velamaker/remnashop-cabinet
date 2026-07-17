"""Админ: импорт/экспорт настроек инсталляции (бэкап конфигурации одним файлом).

Экспорт — бандл конфиг-JSON'ов из assets (брендинг/приложения/меню/почта/auth/все
тумблеры фич/инфо). Импорт — восстанавливает их (whitelist, чужие файлы не пишем).
ТОЛЬКО ВЛАДЕЛЕЦ (бандл содержит секреты email/auth). Рантайм-стейт (*_state.json,
node_health) и приватный push-ключ (push_vapid.json) в бэкап НЕ входят.
"""

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.core.enums import Role
from src.web.endpoints.public.appearance import ASSETS_DIR

from ._common import AdminUser

router = APIRouter(prefix="/settings-io", tags=["Admin - Settings I/O"])

# Конфиг-файлы для бэкапа/восстановления (whitelist). ИСКЛЮЧЕНЫ: *_state.json
# (рантайм), node_health.json (мониторинг), push_vapid.json (приватный ключ push —
# на каждой инсталляции свой, переносить нельзя).
CONFIG_FILES = [
    "branding.json", "apps.json", "app_links.json", "menu.json",
    "email.json", "auth.json", "cashback.json", "topup.json",
    "morning_summary.json", "info_content.json", "server_status.json",
    "digest.json", "email_gate.json", "freeze.json", "new_device.json",
    "promo_banner.json", "reserve.json", "traffic_alert.json",
    "trial_discount.json", "winback.json",
]


def _require_owner(admin: Any) -> None:
    if admin.role < Role.OWNER:
        raise HTTPException(status_code=403, detail="Доступно только владельцу")


@router.get("/export")
async def export_settings(admin: AdminUser) -> dict[str, Any]:
    _require_owner(admin)
    assets: dict[str, Any] = {}
    for name in CONFIG_FILES:
        p = ASSETS_DIR / name
        if p.exists():
            try:
                assets[name] = json.loads(p.read_text("utf-8"))
            except Exception:  # noqa: BLE001 — битый файл пропускаем
                continue
    return {
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "assets": assets,
    }


class ImportBody(BaseModel):
    version: int | None = None
    assets: dict[str, Any]


@router.post("/import")
async def import_settings(admin: AdminUser, body: ImportBody) -> dict[str, Any]:
    _require_owner(admin)
    restored: list[str] = []
    skipped: list[str] = []
    for name, content in (body.assets or {}).items():
        if name not in CONFIG_FILES:
            skipped.append(name)  # whitelist — произвольные файлы не пишем
            continue
        try:
            (ASSETS_DIR / name).write_text(
                json.dumps(content, ensure_ascii=False, indent=2), "utf-8"
            )
            restored.append(name)
        except Exception:  # noqa: BLE001
            skipped.append(name)
    return {"restored": restored, "skipped": skipped, "count": len(restored)}
