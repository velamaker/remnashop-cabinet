"""Win-back истёкших: «вернись, вот скидка» через N дней после конца подписки (overlay).

Через `days_after` дней ПОСЛЕ окончания подписки юзеру выдаётся одноразовая скидка
на возврат (`users.purchase_discount` — база гасит её после покупки) + напоминание в
Telegram/Web Push. Конфиг assets/winback.json, правится в админке. Дефолт ВЫКЛ.
Родственно скидке триальщикам (см. overlay_trial_discount) — та же механика скидки.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
CONFIG_PATH = ASSETS_DIR / "winback.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,       # по умолчанию выключено
    "percent": 20,          # % скидки на возврат
    "days_after": 3,        # через сколько дней после окончания слать
    "lifetime_hours": 168,  # сколько живёт промо (дефолт 7 дней)
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
        "days_after": _clamp(data.get("days_after"), DEFAULT_CONFIG["days_after"], 1, 90),
        "lifetime_hours": _clamp(data.get("lifetime_hours"), DEFAULT_CONFIG["lifetime_hours"], 1, 1440),
    }


def load_config() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text("utf-8"))
    except FileNotFoundError:
        return dict(DEFAULT_CONFIG)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"winback: не удалось прочитать конфиг ({exc}) — беру дефолт")
        return dict(DEFAULT_CONFIG)
    if not isinstance(data, dict):
        return dict(DEFAULT_CONFIG)
    return _normalize(data)


def save_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize(config)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), "utf-8")
    return normalized
