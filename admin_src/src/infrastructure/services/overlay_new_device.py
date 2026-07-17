"""Уведомление «новое устройство подключилось» — конфиг (overlay RемнаShop).

Периодически снимаем HWID-устройства с панели и, если у юзера появился НОВЫЙ девайс
(которого не было в нашей базе known_devices), шлём ему уведомление (Telegram + Web
Push) — доверие + антишеринг. Первый снимок юзера = baseline (без уведомлений).
Конфиг assets/new_device.json, правится в админке. Дефолт ВЫКЛ.
Крон — taskiq/tasks/new_device.py.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
CONFIG_PATH = ASSETS_DIR / "new_device.json"

DEFAULT_CONFIG: dict[str, Any] = {"enabled": False}


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    return {"enabled": bool(data.get("enabled", False))}


def load_config() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text("utf-8"))
    except FileNotFoundError:
        return dict(DEFAULT_CONFIG)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"new_device: не удалось прочитать конфиг ({exc}) — дефолт")
        return dict(DEFAULT_CONFIG)
    if not isinstance(data, dict):
        return dict(DEFAULT_CONFIG)
    return _normalize(data)


def save_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize(config)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), "utf-8")
    return normalized
