"""Месячный дайджест пользователю — конфиг (overlay RемнаShop).

Раз в месяц юзеру приходит сводка: сколько трафика использовал, любимый сервер.
Данные из Remnawave (bandwidthstats). Канал — Telegram + Web Push, а тем, у кого
нет ни того ни другого, — письмо на подтверждённую почту (отдельный тумблер
`email_enabled`, см. overlay_digest_email.py). Конфиг assets/digest.json, правится
в админке. Дефолт ВЫКЛ. Крон — taskiq/tasks/digest.py.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from loguru import logger

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
CONFIG_PATH = ASSETS_DIR / "digest.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,      # по умолчанию выключено
    "day_of_month": 1,     # в какой день месяца слать (1..28)
    "hour": 10,            # в каком часу (UTC, 0..23)
    # Сводка письмом. Отдельный тумблер и тоже ВЫКЛ: письмо живому человеку не
    # отзовёшь, поэтому включается только осознанно, из карточки «Сводка письмом».
    "email_enabled": False,
    # Адрес отправителя сводки. Пусто — основной адрес почты. Для Brevo обязан
    # отличаться от основного (см. overlay_digest_email.email_blockers).
    "email_from": "",
}

# Тот же шаблон, что у тестового письма в разделе «Почта» (TestEmailRequest).
EMAIL_FROM_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email_from(value: Any) -> str:
    """Адрес отправителя сводки: нижний регистр без пробелов или '' для мусора.

    Мусор молча становится пустым, а не ошибкой: файл могли поправить руками или
    восстановить из бэкапа, и кривое значение не должно ронять чтение конфига.
    Отказ «адрес неверный» даёт ручка сохранения — до того, как сюда дойдёт.
    """
    if not isinstance(value, str):
        return ""
    v = value.strip().lower()
    if not v or len(v) > 255 or not EMAIL_FROM_RE.match(v):
        return ""
    return v


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
        "email_enabled": bool(data.get("email_enabled", DEFAULT_CONFIG["email_enabled"])),
        "email_from": normalize_email_from(data.get("email_from")),
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
