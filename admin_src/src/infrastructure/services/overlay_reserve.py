"""Резервный доступ истёкшим подпискам — конфиг (overlay RемнаShop).

Когда подписка заканчивается, вместо мгновенного отруба оставляем человеку дорогу
обратно к боту: N дней он активен с маленьким лимитом трафика (1 ГБ) НА СКВАД-РЕЗЕРВЕ
(squad_uuid) — сервере, который пускает только в Telegram. Так он доходит до бота и
продлевает подписку. Израсходовал лимит → LIMITED → «кончился трафик, продлите»; окно
вышло → срок истекает сам → «подписка закончилась» (customRemarks панели, уже по-русски).

squad_uuid ОБЯЗАТЕЛЕН при enabled: оставить человеку его прежние сквады означало бы
отдать полный сервис бесплатно, а не дать вход в Telegram. Какие именно серверы у
этого сквада и куда они пускают — задаётся в панели Remnawave; кабинет только сажает
на сквад и проверяет, что человек там оказался.

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
    "squad_uuid": "",     # ОБЯЗАТЕЛЕН при enabled: сквад с доступом только в Telegram
    # За сколько часов до конца резерва предупредить человека. 0 — не предупреждать.
    # Резерв — единственное, что у него осталось; кончится молча, и он просто
    # потеряет доступ, не поняв почему. Это ещё и последний момент, когда ему можно
    # предложить купить подписку, пока сервис у него работает.
    "warn_hours_before": 48,
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
        "warn_hours_before": _clamp(
            data.get("warn_hours_before"), DEFAULT_CONFIG["warn_hours_before"], 0, 720
        ),
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
