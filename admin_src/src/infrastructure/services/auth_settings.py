"""Настройки входа через Telegram (OIDC), редактируемые в рантайме из админки.

Хранятся в assets/auth.json (том переживает пересоздание контейнера) и читаются
при каждом запросе — поэтому включение/смена кредов из админки применяется сразу,
без рестарта и без пересборки образа.

Если файла нет или поле пустое — берётся значение из .env
(`TELEGRAM_OIDC_CLIENT_ID`, `TELEGRAM_OIDC_CLIENT_SECRET`). Так старые установки,
где креды лежат в .env, продолжают работать как раньше.

Тумблер `telegram_oidc_enabled`:
  • None  — авто-режим (как раньше): включено, если заданы оба крединала;
  • True  — включено (но только если креды реально заданы);
  • False — явно выключено, даже если креды есть (off-switch для владельца).
"""

import json
import os
from pathlib import Path
from typing import Any

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
AUTH_SETTINGS_PATH = ASSETS_DIR / "auth.json"

# Текстовые поля, которые админка может сохранять.
FIELDS = ("telegram_oidc_client_id", "telegram_oidc_client_secret")


def _load_json() -> dict[str, Any]:
    try:
        if AUTH_SETTINGS_PATH.exists():
            with AUTH_SETTINGS_PATH.open(encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
    except Exception:
        # Битый файл не должен ронять кабинет — отдаём дефолты из .env.
        pass
    return {}


def load_auth_settings() -> dict[str, Any]:
    """Эффективные настройки: .env как дефолт, поверх — сохранённое из админки."""
    eff: dict[str, Any] = {
        "telegram_oidc_client_id": (os.environ.get("TELEGRAM_OIDC_CLIENT_ID") or "").strip(),
        "telegram_oidc_client_secret": (os.environ.get("TELEGRAM_OIDC_CLIENT_SECRET") or "").strip(),
        "telegram_oidc_enabled": None,  # None => авто-режим
    }
    stored = _load_json()
    for key in FIELDS:
        val = stored.get(key)
        if val is not None and str(val).strip() != "":
            eff[key] = str(val).strip()
    if isinstance(stored.get("telegram_oidc_enabled"), bool):
        eff["telegram_oidc_enabled"] = stored["telegram_oidc_enabled"]
    return eff


def telegram_oidc_client_id() -> str:
    return load_auth_settings()["telegram_oidc_client_id"]


def telegram_oidc_client_secret() -> str:
    return load_auth_settings()["telegram_oidc_client_secret"]


def telegram_oidc_enabled() -> bool:
    """Эффективный флаг: учитывает явный тумблер И наличие кредов."""
    s = load_auth_settings()
    has_creds = bool(s["telegram_oidc_client_id"] and s["telegram_oidc_client_secret"])
    toggle = s["telegram_oidc_enabled"]
    if toggle is None:
        return has_creds  # авто-режим — как было до тумблера
    return bool(toggle) and has_creds


def save_auth_settings(values: dict[str, Any]) -> dict[str, Any]:
    """Сохраняет присланные поля поверх уже сохранённых (None — не трогаем)."""
    data = _load_json()
    for key in FIELDS:
        if key in values and values[key] is not None:
            data[key] = str(values[key]).strip()
    if "telegram_oidc_enabled" in values and values["telegram_oidc_enabled"] is not None:
        data["telegram_oidc_enabled"] = bool(values["telegram_oidc_enabled"])
    AUTH_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUTH_SETTINGS_PATH.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    return data
