"""Настройка блока «Статус сервиса» в кабинете (overlay).

Три флага, правятся из админки (assets/server_status.json):
  • enabled — показывать блок статуса серверов вообще;
  • bind_to_subscription — вошедший видит ТОЛЬКО серверы своих сквадов
    (по активной подписке), а не все ноды панели;
  • guest_visible — показывать ли блок невошедшим / на публичной /status.

Приватность (важно): публичный /status НЕ отдаёт host (адрес) ноды — иначе
адрес/через DNS и IP утекал бы любому без авторизации. host (нужен для
клиентского замера пинга) отдаётся ТОЛЬКО владельцу — на авторизованном
/subscription/servers, где он и так подключается к этим нодам.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
CONFIG_PATH = ASSETS_DIR / "server_status.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "bind_to_subscription": True,
    "guest_visible": True,
    # Какие ноды показывать в статусе. Пустой список = ВСЕ. Иначе — только эти
    # UUID нод панели (админ выбирает в кабинете).
    "visible_nodes": [],
}


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    raw_nodes = data.get("visible_nodes") or []
    if isinstance(raw_nodes, (list, tuple)):
        visible = [str(x).strip() for x in raw_nodes if str(x).strip()]
    else:
        visible = []
    return {
        "enabled": bool(data.get("enabled", True)),
        "bind_to_subscription": bool(data.get("bind_to_subscription", True)),
        "guest_visible": bool(data.get("guest_visible", True)),
        "visible_nodes": visible,
    }


def visible_node_ids(cfg: dict[str, Any]) -> set[str]:
    """Множество UUID нод для показа. Пустое = ограничения нет (показываем все)."""
    return {str(x) for x in (cfg.get("visible_nodes") or [])}


def load_config() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text("utf-8"))
    except FileNotFoundError:
        return dict(DEFAULT_CONFIG)
    except Exception as exc:  # noqa: BLE001 — битый конфиг не должен ронять статус
        logger.warning(f"server_status: не удалось прочитать конфиг ({exc}) — беру дефолт")
        return dict(DEFAULT_CONFIG)
    if not isinstance(data, dict):
        return dict(DEFAULT_CONFIG)
    return _normalize(data)


def save_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize(config)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), "utf-8")
    return normalized
