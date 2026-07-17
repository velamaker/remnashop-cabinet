"""Уведомление «трафик заканчивается» (≥N% лимита) — конфиг (overlay RемнаShop).

Проактивно предупреждаем юзера, пока трафик не кончился. Только тарифы с лимитом.
Данные из Remnawave (bulk GET /api/users → usedTrafficBytes / trafficLimitBytes).
Канал — Telegram + Web Push. Конфиг assets/traffic_alert.json, правится в админке.
Дефолт ВЫКЛ. Крон — taskiq/tasks/traffic_alert.py.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
CONFIG_PATH = ASSETS_DIR / "traffic_alert.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,        # по умолчанию выключено
    "threshold_percent": 80, # при каком % израсходованного слать
}


def _clamp(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": bool(data.get("enabled", DEFAULT_CONFIG["enabled"])),
        "threshold_percent": _clamp(
            data.get("threshold_percent"), DEFAULT_CONFIG["threshold_percent"], 50, 99
        ),
    }


def load_config() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text("utf-8"))
    except FileNotFoundError:
        return dict(DEFAULT_CONFIG)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"traffic_alert: не удалось прочитать конфиг ({exc}) — дефолт")
        return dict(DEFAULT_CONFIG)
    if not isinstance(data, dict):
        return dict(DEFAULT_CONFIG)
    return _normalize(data)


def save_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize(config)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), "utf-8")
    return normalized
