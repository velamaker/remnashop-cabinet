"""Авто-подтяжка ссылок установки приложений из upstream `app-config.json`.

Проблема: ссылки на скачивание клиентов (особенно iOS App Store — напр. Happ)
меняются, когда приложение переиздают в сторе. Каталог приложений во фронте
(cabinet/src/data/apps.ts) захардкожен и устаревает.

Решение: периодически тянуть Remnawave `app-config.json` (его поддерживает upstream
и обновляет при апдейте образа subscription-page), извлекать актуальные ссылки
установки по id приложения + платформе и складывать в assets/app_links.json.
Public `/apps` отдаёт эти оверрайды, фронт применяет их поверх встроенных ссылок.

Источник (URL) настраивается админом в apps.json (`links_source_url`) — например
`https://<sub-домен>/assets/app-config.json`. Сервис — leaf-модуль (только httpx/
stdlib), без импортов app-слоя, чтобы безопасно импортироваться из public/apps.py.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
LINKS_PATH = ASSETS_DIR / "app_links.json"

# Ключи платформ в app-config.json → платформы кабинета (data/apps.ts).
# linux / appleTV в кабинете нет — пропускаем.
_PLATFORM_MAP = {
    "ios": "ios",
    "android": "android",
    "windows": "windows",
    "macos": "macos",
    "androidtv": "androidtv",
}


def _first_button_link(app: dict[str, Any]) -> Optional[str]:
    """Первая http(s)-кнопка installationStep — основная ссылка установки.

    Для iOS-приложений upstream первой ставит нужный RU App Store (напр. Happ),
    поэтому берём именно первую подходящую."""
    step = app.get("installationStep")
    if not isinstance(step, dict):
        return None
    for btn in step.get("buttons") or []:
        if not isinstance(btn, dict):
            continue
        link = str(btn.get("buttonLink") or "").strip()
        if link.startswith(("http://", "https://")):
            return link[:500]
    return None


def parse_app_config(raw: Any) -> dict[str, dict[str, str]]:
    """app-config.json → {app_id(lower): {platform: install_url}}."""
    out: dict[str, dict[str, str]] = {}
    platforms = (raw or {}).get("platforms") if isinstance(raw, dict) else None
    if not isinstance(platforms, dict):
        return out
    for plat_key, apps in platforms.items():
        plat = _PLATFORM_MAP.get(str(plat_key).lower())
        if not plat or not isinstance(apps, list):
            continue
        for app in apps:
            if not isinstance(app, dict):
                continue
            aid = str(app.get("id") or "").strip().lower()
            if not aid:
                continue
            link = _first_button_link(app)
            if link:
                out.setdefault(aid, {})[plat] = link
    return out


async def fetch_and_store(url: str) -> dict[str, Any]:
    """Скачать app-config.json, распарсить, записать assets/app_links.json.

    Возвращает статус {ok, count, updated_at, apps} или {ok:false, error}."""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "Некорректный URL источника (нужен http/https)", "count": 0}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            raw = resp.json()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Не удалось загрузить источник: {exc}", "count": 0}

    links = parse_app_config(raw)
    if not links:
        return {"ok": False, "error": "В источнике не найдено ссылок приложений", "count": 0}

    updated_at = datetime.now(timezone.utc).isoformat()
    payload = {"updated_at": updated_at, "source_url": url, "links": links}
    try:
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        with LINKS_PATH.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Не удалось сохранить: {exc}", "count": len(links)}

    return {
        "ok": True,
        "count": len(links),
        "updated_at": updated_at,
        "apps": sorted(links.keys()),
    }


def load_links() -> dict[str, Any]:
    """Прочитать assets/app_links.json (оверрайды ссылок). Безопасно → дефолт."""
    try:
        if LINKS_PATH.exists():
            with LINKS_PATH.open(encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and isinstance(data.get("links"), dict):
                return {
                    "links": data["links"],
                    "updated_at": data.get("updated_at"),
                    "source_url": data.get("source_url"),
                }
    except Exception:
        pass
    return {"links": {}, "updated_at": None, "source_url": None}
