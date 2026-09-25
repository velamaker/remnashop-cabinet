"""Настройки отправки почты, редактируемые в рантайме (из админки).

Хранятся в assets/email.json (том переживает пересоздание контейнера) и читаются
ПРИ КАЖДОЙ отправке — поэтому смена в админке применяется сразу, без рестарта.

Если файла нет или поле пустое — берётся значение из .env (`EMAIL_*`,
`EMAIL_BREVO_API_KEY`). Так старые установки продолжают работать как раньше.

Провайдер задаётся пресетом (gmail/yandex/mailru) — host/port/TLS подставляются
автоматически; «custom» — host/port/TLS заполняются вручную; «brevo» — отправка
через HTTP API Brevo (нужен только api-ключ и адрес отправителя).
"""

import json
import os
from pathlib import Path
from typing import Any, Optional

from src.core.config import AppConfig

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
EMAIL_SETTINGS_PATH = ASSETS_DIR / "email.json"

# Пресеты SMTP популярных провайдеров: host/port/TLS известны заранее.
# Пароль — это пароль приложения (app password), а не основной пароль аккаунта.
PRESETS: dict[str, dict[str, Any]] = {
    "gmail":  {"host": "smtp.gmail.com", "port": 587, "use_tls": True,  "use_ssl": False},
    "yandex": {"host": "smtp.yandex.ru", "port": 465, "use_tls": False, "use_ssl": True},
    "mailru": {"host": "smtp.mail.ru",   "port": 465, "use_tls": False, "use_ssl": True},
}

# Поля, которые админка может сохранять.
FIELDS = (
    "provider", "host", "port", "use_tls", "use_ssl",
    "username", "password", "from_email", "from_name", "brevo_api_key",
)


def _load_json() -> dict[str, Any]:
    try:
        if EMAIL_SETTINGS_PATH.exists():
            with EMAIL_SETTINGS_PATH.open(encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def load_email_settings(config: AppConfig) -> dict[str, Any]:
    """Эффективные настройки: .env как дефолт, поверх — сохранённое из admin."""
    email = config.email
    eff: dict[str, Any] = {
        "enabled": bool(email.enabled),
        "provider": "custom",
        "host": (email.host or ""),
        "port": int(email.port or 587),
        "use_tls": bool(email.use_tls),
        "use_ssl": bool(email.use_ssl),
        "username": email.username.get_secret_value() or "",
        "password": email.password.get_secret_value() or "",
        "from_email": (email.from_email or "").strip(),
        "from_name": (email.from_name or "").strip(),
        "brevo_api_key": (os.environ.get("EMAIL_BREVO_API_KEY") or "").strip(),
    }

    # Дефолт провайдера из .env: задан Brevo-ключ → "brevo", иначе "custom".
    # (Сохранённый в админке provider ниже это переопределит.)
    if eff["brevo_api_key"]:
        eff["provider"] = "brevo"

    stored = _load_json()
    for key in FIELDS:
        if key in stored and stored[key] is not None and stored[key] != "":
            eff[key] = stored[key]
    if "enabled" in stored and isinstance(stored["enabled"], bool):
        eff["enabled"] = stored["enabled"]

    # Пресет провайдера принудительно задаёт host/port/TLS (админ их не вводит).
    preset = PRESETS.get(str(eff.get("provider") or "").lower())
    if preset:
        eff.update(preset)

    eff["port"] = int(eff["port"])
    return eff


def save_email_settings(values: dict[str, Any]) -> dict[str, Any]:
    """Сохраняет присланные поля поверх уже сохранённых (None — не трогаем)."""
    data = _load_json()
    for key in FIELDS:
        if key in values and values[key] is not None:
            data[key] = values[key]
    if "enabled" in values and values["enabled"] is not None:
        data["enabled"] = bool(values["enabled"])
    EMAIL_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with EMAIL_SETTINGS_PATH.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    return data

def settings_allow_sending(settings: dict[str, Any]) -> bool:
    """Можно ли этими настройками реально отправить письмо.

    ОДНО ПРАВИЛО НА ВСЕХ. Раньше половина кода спрашивала переменную окружения
    EMAIL_ENABLED напрямую, и почта, включённая В АДМИНКЕ, для них не
    существовала: письма с кодом уходили, а рассылка по почте падала «Ошибкой»
    сразу, напоминания об окончании молчали, и скидка на продление тоже.
    Теперь правило живёт здесь, а `SmtpEmailSender.is_enabled` только зовёт его.
    """
    if not settings.get("enabled") or not settings.get("from_email"):
        return False
    # Brevo требует только API-ключ и адрес отправителя.
    if str(settings.get("provider") or "").lower() == "brevo" and settings.get("brevo_api_key"):
        return True
    # Иначе нужен полноценный SMTP-конфиг.
    return bool(settings.get("host") and settings.get("username") and settings.get("password"))


def email_enabled_now(config: Optional[AppConfig] = None) -> bool:
    """Включена ли почта ПРЯМО СЕЙЧАС — для мест, где отправителя под рукой нет."""
    cfg = config or AppConfig.get()
    return settings_allow_sending(load_email_settings(cfg))
