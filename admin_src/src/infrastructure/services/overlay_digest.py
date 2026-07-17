"""Месячный дайджест пользователю — конфиг (overlay RемнаShop).

Раз в месяц юзеру приходит сводка: сколько трафика использовал, любимый сервер.
Данные из Remnawave (bandwidthstats). Канал — Telegram + Web Push. Конфиг
assets/digest.json, правится в админке. Дефолт ВЫКЛ. Крон — taskiq/tasks/digest.py.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
CONFIG_PATH = ASSETS_DIR / "digest.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,      # по умолчанию выключено
    "day_of_month": 1,     # в какой день месяца слать (1..28)
    "hour": 10,            # в каком часу (UTC, 0..23)
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
        "day_of_month": _clamp(data.get("day_of_month"), DEFAULT_CONFIG["day_of_month"], 1, 28),
        "hour": _clamp(data.get("hour"), DEFAULT_CONFIG["hour"], 0, 23),
    }


def load_config() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text("utf-8"))
    except FileNotFoundError:
        return dict(DEFAULT_CONFIG)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"digest: не удалось прочитать конфиг ({exc}) — беру дефолт")
        return dict(DEFAULT_CONFIG)
    if not isinstance(data, dict):
        return dict(DEFAULT_CONFIG)
    return _normalize(data)


def save_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize(config)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), "utf-8")
    return normalized
