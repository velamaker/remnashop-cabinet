"""Публичное оформление кабинета (название бренда, цвета).

Название по умолчанию подхватывается автоматически из конфигурации:
явно заданное в админке → переменная BRAND_NAME → имя Telegram-бота (getMe)
→ "RemnaShop". Email не используется (его настраивают не все).

Цвета/название хранятся в JSON-файле в каталоге assets (том переживает
пересоздание контейнера). Не зависит от доменной модели бота — безопасно
для overlay поверх базового образа.
"""

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from src.application.common.dao import SettingsDao

router = APIRouter(prefix="/appearance", tags=["Public - Appearance"])

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
BRANDING_PATH = ASSETS_DIR / "branding.json"

FALLBACK_BRAND = "RemnaShop"

# Расширения логотипа → media-type. SVG, загруженный через <img>, скрипты не
# исполняет — безопасно отдавать как картинку.
LOGO_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".gif": "image/gif",
}

# brand_name == None → подхватывается автоматически (см. resolve_brand_name).
# accent / background == None → кабинет использует цвета темы по умолчанию.
# logo_file == None → логотип не загружен (показывается дефолтная иконка).
DEFAULTS: dict[str, Any] = {
    "brand_name": None,
    "accent": None,
    "background": None,        # legacy: общий фон (фолбэк, если нет раздельных)
    "background_dark": None,   # фон для тёмной темы
    "background_light": None,  # фон для светлой темы
    "logo_file": None,
    # Показывать в кабинете прямую ссылку подписки и QR (тумблер админки).
    "sub_link_enabled": True,
    # Тех-работы: свой тумблер + опция «следовать за режимом доступа бота»
    # (когда у бота AccessMode.RESTRICTED «Запрещён для всех» — кабинет тоже закрыт).
    "maintenance_enabled": False,
    "maintenance_follow_bot": False,
    "maintenance_message": "",
    # Что именно ограничивать в тех-работах (гранулярно). По умолчанию — всё.
    "maintenance_block_login": True,         # вход в кабинет (полное закрытие для не-админов)
    "maintenance_block_registration": True,  # новые регистрации
    "maintenance_block_payments": True,      # оплата/пополнение
    # Доступные языки кабинета: None = все; иначе список кодов (ru всегда включён).
    "enabled_languages": None,
}


def logo_path(logo_file: Optional[str]) -> Optional[Path]:
    """Безопасный путь к файлу логотипа внутри ASSETS_DIR (или None)."""
    if not logo_file:
        return None
    # Только basename — защита от path traversal (logo_file пишет админ, но всё же).
    path = ASSETS_DIR / Path(logo_file).name
    return path if path.exists() else None


def logo_url(logo_file: Optional[str]) -> Optional[str]:
    """Публичный URL логотипа с cache-busting по mtime, либо None."""
    path = logo_path(logo_file)
    if path is None:
        return None
    return f"/api/appearance/logo?v={int(path.stat().st_mtime)}"

# Кэш имени бота, чтобы не дёргать getMe на каждый запрос.
_bot_name_cache: Optional[str] = None
_bot_name_last_try: float = 0.0


def _bot_brand_name() -> Optional[str]:
    """Имя бота из Telegram getMe (с кэшем). None — если недоступно."""
    global _bot_name_cache, _bot_name_last_try
    if _bot_name_cache:
        return _bot_name_cache
    # Не чаще раза в 60с при неудачах, чтобы не блокировать запросы.
    if time.monotonic() - _bot_name_last_try < 60:
        return None
    _bot_name_last_try = time.monotonic()
    token = (os.environ.get("BOT_TOKEN") or "").strip()
    if not token:
        return None
    try:
        resp = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=4)
        data = resp.json()
        if data.get("ok"):
            name = (data["result"].get("first_name") or "").strip()
            if name:
                _bot_name_cache = name
                return name
    except Exception:
        pass
    return None


def _support_username() -> Optional[str]:
    """Username поддержки из конфигурации бота (BOT_SUPPORT_USERNAME), без @."""
    u = (os.environ.get("BOT_SUPPORT_USERNAME") or "").strip().lstrip("@")
    return u or None


def resolve_brand_name() -> str:
    env = (os.environ.get("BRAND_NAME") or "").strip()
    if env:
        return env
    bot = _bot_brand_name()
    if bot:
        return bot
    return FALLBACK_BRAND


def load_branding() -> dict[str, Any]:
    """Сырые сохранённые значения (brand_name может быть None = авто)."""
    data = dict(DEFAULTS)
    try:
        if BRANDING_PATH.exists():
            with BRANDING_PATH.open(encoding="utf-8") as fh:
                stored = json.load(fh)
            if isinstance(stored, dict):
                for key in DEFAULTS:
                    if key in stored:
                        data[key] = stored[key]
    except Exception:
        # Битый файл не должен ронять кабинет — отдаём дефолты.
        pass
    return data


@router.get("")
@inject
async def get_appearance(settings_dao: FromDishka[SettingsDao]) -> dict[str, Any]:
    """Для кабинета: brand_name всегда конкретный (авто-резолв)."""
    data = load_branding()
    if not data.get("brand_name"):
        data["brand_name"] = resolve_brand_name()
    data["support_username"] = _support_username()
    # logo_file — внутреннее имя файла; наружу отдаём готовый logo_url.
    data["logo_url"] = logo_url(data.pop("logo_file", None))

    # Тех-работы: свой тумблер ИЛИ (следовать за ботом И у бота «Запрещён для всех»).
    # Публичный эндпоинт не должен падать — чтение режима бота в try.
    maintenance = bool(data.get("maintenance_enabled"))
    if not maintenance and data.get("maintenance_follow_bot"):
        try:
            s = await settings_dao.get()
            mode = getattr(s.access.mode, "value", s.access.mode)
            maintenance = str(mode).upper() == "RESTRICTED"
        except Exception:
            maintenance = False
    data["maintenance"] = maintenance
    # Вход через Telegram по OIDC доступен, если заданы client_id/secret и тумблер
    # не выключен. Креды/тумблер берутся из assets/auth.json (правятся в админке),
    # с фолбэком на .env (TELEGRAM_OIDC_CLIENT_ID/SECRET) — см. auth_settings.
    from src.infrastructure.services.auth_settings import telegram_oidc_enabled

    data["telegram_oidc_enabled"] = telegram_oidc_enabled()
    return data


@router.get("/logo")
async def get_logo() -> FileResponse:
    """Отдаёт загруженный логотип кабинета (публично, для тега <img>)."""
    path = logo_path(load_branding().get("logo_file"))
    if path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Логотип не задан")
    media = LOGO_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media)
