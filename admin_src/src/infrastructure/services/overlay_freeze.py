"""Заморозка (пауза) подписки — конфиг (overlay RемнаShop).

Юзер может поставить подписку на паузу: дни не сгорают, доступ отключается. При
возобновлении expire сдвигается вперёд на сохранённый остаток. Лимит max_days —
максимальная длительность одной паузы (дальше крон авто-возобновляет). Дефолт ВЫКЛ.
Конфиг assets/freeze.json, правится в админке. Крон — taskiq/tasks/freeze.py.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
CONFIG_PATH = ASSETS_DIR / "freeze.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,  # по умолчанию выключено
    "max_days": 30,    # макс. длительность одной паузы (дней), потом авто-возобновление
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
        "max_days": _clamp(data.get("max_days"), DEFAULT_CONFIG["max_days"], 1, 365),
    }


def load_config() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text("utf-8"))
    except FileNotFoundError:
        return dict(DEFAULT_CONFIG)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"freeze: не удалось прочитать конфиг ({exc}) — беру дефолт")
        return dict(DEFAULT_CONFIG)
    if not isinstance(data, dict):
        return dict(DEFAULT_CONFIG)
    return _normalize(data)


def save_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize(config)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), "utf-8")
    return normalized
