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
from fastapi import APIRouter

router = APIRouter(prefix="/appearance", tags=["Public - Appearance"])

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
BRANDING_PATH = ASSETS_DIR / "branding.json"

FALLBACK_BRAND = "RemnaShop"

# brand_name == None → подхватывается автоматически (см. resolve_brand_name).
# accent / background == None → кабинет использует цвета темы по умолчанию.
DEFAULTS: dict[str, Any] = {
    "brand_name": None,
    "accent": None,
    "background": None,
}

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
async def get_appearance() -> dict[str, Any]:
    """Для кабинета: brand_name всегда конкретный (авто-резолв)."""
    data = load_branding()
    if not data.get("brand_name"):
        data["brand_name"] = resolve_brand_name()
    data["support_username"] = _support_username()
    return data
