"""Промо-баннер в кабинете — конфиг (overlay RемнаShop).

Админ задаёт баннер (заголовок/текст/кнопка/цвет/аудитория/период), кабинет
показывает его подходящим юзерам. Чистый маркетинг — цену НЕ трогает (скидки —
через промокоды/скидку триальщикам). Конфиг assets/promo_banner.json, правится в
админке (admin/promo_banner.py). Дефолт ВЫКЛ.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
CONFIG_PATH = ASSETS_DIR / "promo_banner.json"

# audience: all | no_sub | has_sub | trial | expiring
_AUDIENCES = {"all", "no_sub", "has_sub", "trial", "expiring"}
# color: accent | red | green | amber
_COLORS = {"accent", "red", "green", "amber"}


def _safe_cta_url(url: str) -> str:
    """cta_url для <a href>: только http(s) или относительный /path (не //). Иначе '' —
    чтобы javascript:/data: не дал stored-XSS у всех юзеров, видящих баннер."""
    u = (url or "").strip()
    if not u:
        return ""
    if u.startswith("/") and not u.startswith("//"):
        return u
    return u if u.lower().startswith(("http://", "https://")) else ""

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "title": "",
    "text": "",
    "cta_text": "",
    "cta_url": "",
    "color": "accent",
    "audience": "all",
    "dismissible": True,
    "starts_at": "",  # ISO-строка или пусто (без ограничения снизу)
    "ends_at": "",     # ISO-строка или пусто (без ограничения сверху)
}


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    color = str(data.get("color", "accent")).strip().lower()
    audience = str(data.get("audience", "all")).strip().lower()
    return {
        "enabled": bool(data.get("enabled", False)),
        "title": str(data.get("title", "") or "")[:120],
        "text": str(data.get("text", "") or "")[:500],
        "cta_text": str(data.get("cta_text", "") or "")[:40],
        # cta_url рендерится в <a href> у всех юзеров → пускаем только http(s) или
        # относительный /path (не //). javascript:/data: → пусто (защита от stored-XSS;
        # фронт safeExternalUrl тоже режет, это defense-in-depth на источнике).
        "cta_url": _safe_cta_url(str(data.get("cta_url", "") or "")[:300]),
        "color": color if color in _COLORS else "accent",
        "audience": audience if audience in _AUDIENCES else "all",
        "dismissible": bool(data.get("dismissible", True)),
        "starts_at": str(data.get("starts_at", "") or "")[:40],
        "ends_at": str(data.get("ends_at", "") or "")[:40],
    }


def load_config() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text("utf-8"))
    except FileNotFoundError:
        return dict(DEFAULT_CONFIG)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"promo_banner: не удалось прочитать конфиг ({exc}) — дефолт")
        return dict(DEFAULT_CONFIG)
    if not isinstance(data, dict):
        return dict(DEFAULT_CONFIG)
    return _normalize(data)


def save_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize(config)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), "utf-8")
    return normalized
