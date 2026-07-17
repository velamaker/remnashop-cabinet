"""Публичная конфигурация приложений для подключения.

Сам каталог приложений (deep-link'и, ссылки установки) живёт во фронте кабинета
(cabinet/src/data/apps.ts). Здесь хранится только ВЫБОР администратора:
  • priority — id приоритетного приложения (показывается первым, «Рекомендуем»);
  • enabled  — список id приложений, которые показывать (null = показывать все).

Хранится в assets/apps.json (том переживает пересоздание контейнера).
"""

import json
import os
import re
import secrets
from pathlib import Path
from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/apps", tags=["Public - Apps"])

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
APPS_PATH = ASSETS_DIR / "apps.json"

_PLATFORMS = {"ios", "android", "windows", "macos", "androidtv"}


def sanitize_custom_apps(raw: Any) -> list[dict[str, Any]]:
    """Свои приложения админа → безопасный вид. deep_link — шаблон с {sub}."""
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for item in raw[:50]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:60]
        deep = str(item.get("deep_link") or "").strip()[:300]
        if not name or not deep:
            continue
        plats = [p for p in (item.get("platforms") or []) if p in _PLATFORMS]
        if not plats:
            plats = sorted(_PLATFORMS)
        cid = str(item.get("id") or "").strip()[:40]
        if not cid:
            slug = re.sub(r"[^a-z0-9]+", "", name.lower())[:20]
            cid = f"custom_{slug or secrets.token_hex(4)}"
        out.append({
            "id": cid,
            "name": name,
            "desc": str(item.get("desc") or "").strip()[:120],
            "platforms": plats,
            "deep_link": deep,
            "install_url": (str(item.get("install_url") or "").strip()[:300] or None),
        })
    return out


def sanitize_manual_links(raw: Any) -> dict[str, dict[str, str]]:
    """Ручные оверрайды ссылок админа → {app_id(lower): {platform: http-url}}.

    Побеждают резолверы/upstream. Нужны, когда приложение вернулось в стор под
    новой ссылкой и авто-резолвер ещё не подхватил (или для замены на что угодно)."""
    out: dict[str, dict[str, str]] = {}
    if not isinstance(raw, dict):
        return out
    for aid, plats in list(raw.items())[:100]:
        if not isinstance(plats, dict):
            continue
        app_id = str(aid).strip().lower()[:40]
        if not app_id:
            continue
        clean: dict[str, str] = {}
        for plat, url in plats.items():
            if plat not in _PLATFORMS:
                continue
            u = str(url or "").strip()[:500]
            if u.startswith(("http://", "https://")):
                clean[plat] = u
        if clean:
            out[app_id] = clean
    return out


def load_apps_config() -> dict[str, Any]:
    """priority: str|None, enabled: list[str]|None (None = все), custom: list,
    links_source_url: str|None, manual_links: {app:{platform:url}} (ручные оверрайды)."""
    data: dict[str, Any] = {
        "priority": None,
        "enabled": None,
        "custom": [],
        "links_source_url": None,
        "manual_links": {},
    }
    try:
        if APPS_PATH.exists():
            with APPS_PATH.open(encoding="utf-8") as fh:
                stored = json.load(fh)
            if isinstance(stored, dict):
                pr = stored.get("priority")
                if pr is None or isinstance(pr, str):
                    data["priority"] = pr
                en = stored.get("enabled")
                if isinstance(en, list):
                    data["enabled"] = [str(x) for x in en]
                data["custom"] = sanitize_custom_apps(stored.get("custom"))
                lsu = stored.get("links_source_url")
                if lsu is None or isinstance(lsu, str):
                    data["links_source_url"] = lsu
                data["manual_links"] = sanitize_manual_links(stored.get("manual_links"))
    except Exception:
        # Битый файл не должен ронять кабинет — отдаём дефолт (все приложения).
        pass
    return data


def save_apps_config(data: dict[str, Any]) -> None:
    """Записать apps.json (только известные поля выбора админа)."""
    payload = {
        "priority": data.get("priority"),
        "enabled": data.get("enabled"),
        "custom": data.get("custom") or [],
        "links_source_url": data.get("links_source_url"),
        "manual_links": data.get("manual_links") or {},
    }
    APPS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with APPS_PATH.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def apply_manual_links(
    links: dict[str, dict[str, str]],
    meta: dict[str, dict[str, Any]],
    manual: dict[str, dict[str, str]],
) -> None:
    """Наложить ручные ссылки поверх резолвер-ссылок (in-place). Ручная всегда
    главнее и считается рабочей → degraded=false, source=manual."""
    for app_id, plats in (manual or {}).items():
        for plat, url in plats.items():
            links.setdefault(app_id, {})[plat] = url
            meta.setdefault(app_id, {})[plat] = {"source": "manual", "version": None, "degraded": False}


@router.get("")
async def get_apps_config() -> dict[str, Any]:
    # Оверрайды ссылок установки (авто-подтяжка из upstream app-config.json).
    # Фронт применяет их поверх встроенного каталога (data/apps.ts) по id+платформе.
    from src.infrastructure.services.overlay_app_links import load_links

    cfg = load_apps_config()
    links = load_links()
    overrides = links["links"]
    meta = links.get("meta") or {}
    apply_manual_links(overrides, meta, cfg.get("manual_links") or {})
    cfg["link_overrides"] = overrides
    cfg["link_meta"] = meta
    cfg["links_updated_at"] = links["updated_at"]
    return cfg
