"""Конфиг утренней сводки владельцу — правится из админки (overlay).

Сама рассылка живёт в taskiq/tasks/morning_summary.py (почасовой крон, внутри
проверяет час). Раньше настройка была ТОЛЬКО через env; теперь тумблер/час/окно
хранятся в assets/morning_summary.json и правятся в кабинете (Настройки).

Обратная совместимость: если файла ещё нет, дефолты берутся из прежних env
(MORNING_SUMMARY_ENABLED/HOUR/EXPIRING_DAYS) — существующие установки не ломаются,
пока владелец не сохранит настройку из админки (тогда приоритет у json).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
CONFIG_PATH = ASSETS_DIR / "morning_summary.json"


def _env_enabled() -> bool:
    return (os.environ.get("MORNING_SUMMARY_ENABLED") or "true").strip().lower() in (
        "1", "true", "yes", "on", "да",
    )


def _env_hour() -> int:
    try:
        return min(23, max(0, int(os.environ.get("MORNING_SUMMARY_HOUR") or "9")))
    except ValueError:
        return 9


def _env_expiring_days() -> int:
    try:
        return max(1, int(os.environ.get("MORNING_SUMMARY_EXPIRING_DAYS") or "3"))
    except ValueError:
        return 3


def _default_config() -> dict[str, Any]:
    return {
        "enabled": _env_enabled(),
        "hour": _env_hour(),
        "expiring_days": _env_expiring_days(),
    }


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    defaults = _default_config()
    try:
        hour = int(data.get("hour", defaults["hour"]))
    except (TypeError, ValueError):
        hour = defaults["hour"]
    hour = min(23, max(0, hour))
    try:
        days = int(data.get("expiring_days", defaults["expiring_days"]))
    except (TypeError, ValueError):
        days = defaults["expiring_days"]
    days = max(1, days)
    return {
        "enabled": bool(data.get("enabled", defaults["enabled"])),
        "hour": hour,
        "expiring_days": days,
    }


def load_config() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text("utf-8"))
    except FileNotFoundError:
        return _default_config()
    except Exception as exc:  # noqa: BLE001 — битый конфиг не должен ронять крон
        logger.warning(f"morning_summary: не удалось прочитать конфиг ({exc}) — беру дефолт")
        return _default_config()
    if not isinstance(data, dict):
        return _default_config()
    return _normalize(data)


def save_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize(config)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), "utf-8")
    return normalized
