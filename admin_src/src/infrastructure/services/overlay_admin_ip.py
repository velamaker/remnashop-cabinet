"""Ограничение доступа в админку по IP — конфиг (overlay RемнаShop).

Если включено и список непустой — админ-запросы (/api/v1/admin/*) разрешены только
с перечисленных IP. Fail-safe: пустой список = как выключено (чтобы не залочиться).
⚠️ Под нашим VPN IP админа = IP ноды (см. [[login-ip-is-tunnel-exit]]) — учитывать.
Конфиг assets/admin_ip.json, правится в админке (owner-only). Дефолт ВЫКЛ.
Восстановление при локауте: отредактировать assets/admin_ip.json на сервере.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
CONFIG_PATH = ASSETS_DIR / "admin_ip.json"

DEFAULT_CONFIG: dict[str, Any] = {"enabled": False, "allowed_ips": []}


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get("allowed_ips") or []
    ips = []
    if isinstance(raw, list):
        for x in raw:
            s = str(x).strip()
            if s and s not in ips:
                ips.append(s[:64])
    return {"enabled": bool(data.get("enabled", False)), "allowed_ips": ips}


def load_config() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text("utf-8"))
    except FileNotFoundError:
        return dict(DEFAULT_CONFIG)
    except Exception as exc:  # noqa: BLE001 — битый конфиг не должен ронять админку
        logger.warning(f"admin_ip: не удалось прочитать конфиг ({exc}) — беру дефолт (выкл)")
        return dict(DEFAULT_CONFIG)
    if not isinstance(data, dict):
        return dict(DEFAULT_CONFIG)
    return _normalize(data)


def is_ip_allowed(client_ip: str) -> bool:
    """True — доступ разрешён (выключено / список пуст / IP в списке)."""
    cfg = load_config()
    if not cfg["enabled"] or not cfg["allowed_ips"]:
        return True  # fail-safe: не залочиться
    return client_ip in cfg["allowed_ips"]


def save_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize(config)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), "utf-8")
    return normalized
