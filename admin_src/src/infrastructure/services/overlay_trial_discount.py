"""Скидка на ПЕРВУЮ покупку триальщикам — конфиг (overlay RемнаShop).

За N дней до конца пробного периода юзеру выдаётся одноразовая скидка на первую
оплату (`users.purchase_discount` — база сама гасит её после покупки, см.
purchase.py) + баннер-таймер в кабинете + напоминание в Telegram/Web Push.
Конфиг — assets/trial_discount.json, правится из админки (admin/trial_discount.py).
Выдачу/погашение делает крон taskiq/tasks/trial_discount.py.

Дефолт — ВЫКЛ (не всем установщикам нужно).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
CONFIG_PATH = ASSETS_DIR / "trial_discount.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,      # по умолчанию выключено
    "percent": 15,         # % скидки на первую покупку
    "days_before": 1,      # за сколько дней до конца триала выдавать
    "lifetime_hours": 72,  # сколько живёт промо, если не воспользовались
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
        "percent": _clamp(data.get("percent"), DEFAULT_CONFIG["percent"], 1, 100),
        "days_before": _clamp(data.get("days_before"), DEFAULT_CONFIG["days_before"], 1, 30),
        "lifetime_hours": _clamp(data.get("lifetime_hours"), DEFAULT_CONFIG["lifetime_hours"], 1, 720),
    }


def load_config() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text("utf-8"))
    except FileNotFoundError:
        return dict(DEFAULT_CONFIG)
    except Exception as exc:  # noqa: BLE001 — битый конфиг не должен ронять крон
        logger.warning(f"trial_discount: не удалось прочитать конфиг ({exc}) — беру дефолт")
        return dict(DEFAULT_CONFIG)
    if not isinstance(data, dict):
        return dict(DEFAULT_CONFIG)
    return _normalize(data)


def save_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize(config)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), "utf-8")
    return normalized
