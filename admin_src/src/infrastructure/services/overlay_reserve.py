"""Резервный доступ истёкшим подпискам — конфиг (overlay RемнаShop).

Когда подписка заканчивается, вместо мгновенного отруба даём «спасательный круг»:
держим юзера активным N дней с маленьким лимитом трафика (1 ГБ) на его же скваде
(или на отдельном сквад-резерве, если задан squad_uuid). Пока есть 1 ГБ — работает
VPN; израсходовал → LIMITED → русская надпись «кончился трафик, продлите»; окно
вышло → крон окончательно истекает подписку → «подписка закончилась» (customRemarks
панели, уже по-русски). Так у человека есть время и стимул продлить.

Крон — taskiq/tasks/reserve.py. Конфиг — assets/reserve.json (правится в админке).
Дефолт ВЫКЛ (не всем установщикам нужно раздавать бесплатный трафик).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
CONFIG_PATH = ASSETS_DIR / "reserve.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,     # по умолчанию выключено
    "reserve_gb": 1,      # сколько ГБ резервного трафика
    "window_days": 7,     # сколько дней держать резервный доступ
    "squad_uuid": "",     # опц.: отдельный сквад-резерв (пусто = оставить текущий сквад)
}


def _clamp(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    squad = str(data.get("squad_uuid", "") or "").strip()
    return {
        "enabled": bool(data.get("enabled", DEFAULT_CONFIG["enabled"])),
        "reserve_gb": _clamp(data.get("reserve_gb"), DEFAULT_CONFIG["reserve_gb"], 1, 100),
        "window_days": _clamp(data.get("window_days"), DEFAULT_CONFIG["window_days"], 1, 60),
        "squad_uuid": squad,
    }


def load_config() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text("utf-8"))
    except FileNotFoundError:
        return dict(DEFAULT_CONFIG)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"reserve: не удалось прочитать конфиг ({exc}) — беру дефолт")
        return dict(DEFAULT_CONFIG)
    if not isinstance(data, dict):
        return dict(DEFAULT_CONFIG)
    return _normalize(data)


def save_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize(config)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), "utf-8")
    return normalized
