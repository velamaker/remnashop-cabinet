"""Обязательная верификация email перед триалом/покупкой — конфиг (overlay).

Гейт `_assert_web_purchase_email_verified` (public/subscription.py) блокирует
email-зарегистрированных юзеров без подтверждённого email на триал/покупку/продление
(Telegram/OAuth не трогает). Раньше был захардкожен «всегда вкл» — теперь тумблер.
Дефолт ВКЛ (сохраняем прежнее поведение; установщик может выключить).
Конфиг assets/email_gate.json, правится в админке.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
CONFIG_PATH = ASSETS_DIR / "email_gate.json"

DEFAULT_CONFIG: dict[str, Any] = {"enabled": True}  # по умолчанию ВКЛ (как было)


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    return {"enabled": bool(data.get("enabled", True))}


def load_config() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text("utf-8"))
    except FileNotFoundError:
        return dict(DEFAULT_CONFIG)
    except Exception as exc:  # noqa: BLE001 — битый конфиг не должен ронять покупку
        logger.warning(f"email_gate: не удалось прочитать конфиг ({exc}) — беру дефолт (вкл)")
        return dict(DEFAULT_CONFIG)
    if not isinstance(data, dict):
        return dict(DEFAULT_CONFIG)
    return _normalize(data)


def is_enabled() -> bool:
    return load_config()["enabled"]


def save_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize(config)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), "utf-8")
    return normalized
