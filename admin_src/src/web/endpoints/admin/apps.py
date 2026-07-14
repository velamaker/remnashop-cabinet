"""Админ: выбор приложений для подключения (приоритетное + включённые).

Каталог приложений — во фронте (cabinet/src/data/apps.ts). Тут сохраняем выбор
администратора в assets/apps.json (см. public/apps.py).
"""

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from src.infrastructure.services.overlay_app_links import fetch_and_store, load_links
from src.web.endpoints.public.apps import (
    load_apps_config,
    sanitize_custom_apps,
    save_apps_config,
)

from ._common import AdminUser

router = APIRouter(prefix="/apps", tags=["Admin - Apps"])


class AppsConfigUpdate(BaseModel):
    priority: Optional[str] = None      # id приоритетного приложения ("" → сброс)
    enabled: Optional[list[str]] = None  # список id (None — не менять)
    custom: Optional[list[dict[str, Any]]] = None  # свои приложения (None — не менять)
    links_source_url: Optional[str] = None  # URL источника авто-подтяжки ("" → сброс)


class RefreshLinksBody(BaseModel):
    source_url: Optional[str] = None  # если задан — сохранить и обновить с него


def _with_links(data: dict[str, Any]) -> dict[str, Any]:
    """Дополнить конфиг статусом авто-подтяжки (дата/оверрайды) для админки."""
    links = load_links()
    data["link_overrides"] = links["links"]
    data["links_updated_at"] = links["updated_at"]
    return data


@router.get("")
async def get_apps(_admin: AdminUser) -> dict[str, Any]:
    return _with_links(load_apps_config())


@router.put("")
async def update_apps(body: AppsConfigUpdate, _admin: AdminUser) -> dict[str, Any]:
    data = load_apps_config()

    if body.enabled is not None:
        # ограничиваем разумным числом id, нормализуем в строки
        data["enabled"] = [str(x) for x in body.enabled][:100]
    if body.priority is not None:
        data["priority"] = body.priority.strip() or None
    if body.custom is not None:
        data["custom"] = sanitize_custom_apps(body.custom)
    if body.links_source_url is not None:
        data["links_source_url"] = body.links_source_url.strip() or None

    try:
        save_apps_config(data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Не удалось сохранить список приложений: {exc}",
        )

    return _with_links(data)


@router.post("/refresh-links")
async def refresh_links(body: RefreshLinksBody, _admin: AdminUser) -> dict[str, Any]:
    """Скачать актуальные ссылки установки из источника (app-config.json) сейчас.

    Если передан новый source_url — сохраняем его в конфиг. Иначе берём из конфига."""
    data = load_apps_config()
    new_url = (body.source_url or "").strip() if body.source_url is not None else None
    url = new_url or (data.get("links_source_url") or "")
    if not url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Не задан URL источника ссылок",
        )
    if new_url and new_url != (data.get("links_source_url") or ""):
        data["links_source_url"] = new_url
        try:
            save_apps_config(data)
        except Exception:  # noqa: BLE001
            pass  # не критично — обновление ссылок важнее сохранения URL

    result = await fetch_and_store(url)
    if not result.get("ok"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=result.get("error") or "Не удалось обновить ссылки",
        )
    return result
