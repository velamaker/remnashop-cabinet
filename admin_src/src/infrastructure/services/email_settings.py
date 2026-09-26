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
import smtplib
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


def explain_send_error(exc: BaseException, provider: Optional[str] = None) -> str:
    """Почему письмо не ушло — словами для админа, с ответом сервера в хвосте.

    ЗАЧЕМ. Отправитель заворачивает ошибку SMTP в общее «Failed to send email»:
    человеку в кабинете подробности ни к чему. Но на кнопке «Проверить» админу
    нужна именно настоящая причина, а она лежит в `__cause__`. Раньше кнопка
    показывала сырой ответ вида `(534, b'5.7.9 Application-specific password
    required ...')` — по нему нельзя догадаться, что Gmail просто хочет пароль
    приложения вместо обычного.
    """
    # Идём по цепочке «из-за чего» до САМОГО дна, но останавливаемся на первом
    # звене, которое уже всё объясняет. Без этого таймаут httpx уходил глубже
    # нужного: ConnectTimeout → httpcore.ConnectTimeout → TimeoutError →
    # asyncio.CancelledError. Корнем оказывалась отмена задачи — не таймаут и не
    # ошибка связи, подсказки не было вовсе, а админ видел внутренний текст anyio.
    # Туда же не спускаемся через звенья, которые не Exception (CancelledError —
    # BaseException): объяснять админу устройство асинхронной отмены незачем.
    root: BaseException = exc
    seen = 0
    while seen < 6:
        if isinstance(root, (TimeoutError, ConnectionError, smtplib.SMTPException)):
            break
        nxt = root.__cause__ or root.__context__
        if nxt is None or not isinstance(nxt, Exception):
            break
        root, seen = nxt, seen + 1

    raw = str(root).strip() or type(root).__name__
    low = raw.lower()

    # Код ответа SMTP берём из самого исключения, а не ищем цифры в тексте: «535»
    # встречается и в номере порта, и в идентификаторе очереди почтовика.
    smtp_code = getattr(root, "smtp_code", None)

    hint = ""
    # Именно гугловский отказ, а не любой код 5.7.9: по RFC 4954 он общий
    # («механизм проверки слишком слабый»), и совет про пароль приложения к нему
    # не подходит.
    if "application-specific password" in low or "invalidsecondfactor" in low:
        hint = (
            "Gmail не принимает обычный пароль от почты: нужен ПАРОЛЬ ПРИЛОЖЕНИЯ. "
            "Создайте его в Аккаунте Google → Безопасность → Пароли приложений "
            "(нужна включённая двухэтапная проверка) и вставьте в поле «Пароль»"
        )
    elif smtp_code == 535 or "badcredentials" in low \
            or "username and password not accepted" in low \
            or "authentication failed" in low or "authentication credentials invalid" in low:
        hint = "Логин или пароль не подошли — проверьте их (у Gmail и Яндекса нужен пароль приложения)"
    elif "timed out" in low or "connection refused" in low or "connection reset" in low \
            or isinstance(root, (TimeoutError, ConnectionError)):
        if str(provider or "").lower() == "brevo":
            # Уже Brevo — советовать «выберите Brevo» значит увести не туда.
            hint = (
                "Brevo не отвечает — проверьте, что сервер выходит в интернет по HTTPS "
                "(api.brevo.com) и что ключ API действителен"
            )
        else:
            hint = (
                "Почтовый сервер не отвечает на этом порту — часто хостер закрывает "
                "исходящую почту. Выберите провайдера Brevo: он шлёт через HTTPS"
            )

    return f"{hint} (ответ сервера: {raw})" if hint else raw
