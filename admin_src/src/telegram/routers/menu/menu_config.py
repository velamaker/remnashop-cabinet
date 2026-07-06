"""Конфиг состава и ПОРЯДКА кнопок главного меню (редактируется из админки).

Хранится в assets/menu.json (том переживает пересоздание контейнера). Читается
геттером меню НА КАЖДЫЙ рендер, поэтому изменения из админки применяются сразу,
без перезапуска бота.

Приоритет значений: значения из menu.json → переменные окружения BOT_MENU_*
(обратная совместимость) → дефолты ниже. Порядок (`order`) — только в menu.json;
если его нет, берётся DEFAULT_ORDER.
"""

import json
import os
from pathlib import Path
from typing import Any

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
MENU_PATH = ASSETS_DIR / "menu.json"

# Ключи = идентификаторы кнопок. Дефолт: кабинет (Mini App) + кабинет (браузер)
# + сабка Remnawave. Кнопки «Подключиться» (→ /devices) по умолчанию выключены.
MENU_DEFAULTS: dict[str, bool] = {
    "cabinet_miniapp": True,
    "cabinet_url": True,
    "connect_miniapp": False,
    "connect_url": False,
    "remna_sub": True,
}

# Порядок кнопок в меню по умолчанию (как было зашито в dialog.py).
DEFAULT_ORDER: list[str] = [
    "cabinet_miniapp",
    "cabinet_url",
    "connect_miniapp",
    "connect_url",
    "remna_sub",
]

# Базовые кнопки навигации бота: ключ конфига → i18n-ключ дефолтной подписи.
# Их состав фиксирован (это стандартная навигация), настраиваются только текст и цвет.
NAV_KEYS: dict[str, str] = {
    "nav_devices": "btn-menu.devices",
    "nav_subscription": "btn-menu.subscription",
    "nav_invite": "btn-menu.invite",
    "nav_support": "btn-menu.support",
    "nav_dashboard": "btn-menu.dashboard",
}

# Все ключи, для которых можно задать кастомный текст/цвет.
_CUSTOMIZABLE = set(MENU_DEFAULTS) | set(NAV_KEYS)

# Подписи по умолчанию (для превью в админке — «что покажется, если оставить
# пустым» — и чтобы кнопка «добавить эмодзи» дописывала эмодзи к реальному
# дефолтному тексту, а не стирала его). Должны совпадать с фактическими
# текстами: dialog.py._ACCESS_DEFS (cabinet_url/remna_sub — статика) и
# assets/translations/ru/custom.ftl (btn-menu.* — i18n-ключи в NAV_KEYS и
# web-cabinet/connect/connect-reserve).
DEFAULT_TEXTS: dict[str, str] = {
    "cabinet_miniapp": "👤 Личный кабинет",
    "cabinet_url": "🌐 Кабинет в браузере",
    "connect_miniapp": "⚡ Подключиться",
    "connect_url": "🔁 Подключиться (резерв)",
    "remna_sub": "📲 Подписка (резерв)",
    "nav_devices": "📱 Устройства",
    "nav_subscription": "🪪 Подписка",
    "nav_invite": "🎁 Пригласить",
    "nav_support": "💬 Поддержка",
    "nav_dashboard": "⚙️ Панель управления",
}

# Цвета кнопок (Telegram/aiogram ButtonStyle). Пусто/None = дефолт кнопки.
VALID_COLORS: set[str] = {"primary", "success", "danger"}
# Ограничение длины подписи кнопки (эмодзи считаются символами).
BTN_TEXT_MAX = 64

# OVERLAY (RемнаShop): премиум-эмодзи в тексте кнопки задаётся тегом
# <tg-emoji emoji-id="123">⭐</tg-emoji> (бот парсит его в icon_custom_emoji_id).
# Лимит 64 считаем по ЧИСТОМУ тексту (тег заменяется своим fallback при отправке),
# иначе тег «съедал» бы бюджет и обрезался. Влезает чистый — сохраняем сырой
# (с тегом) целиком; иначе — старое поведение (обрезка сырого).
# ВНИМАНИЕ: файл перекрывает базовый — при обновлении базы сверять с оригиналом.
import re as _re

_TG_EMOJI_RE = _re.compile(r'<tg-emoji emoji-id="\d+">([^<]*)</tg-emoji>')


def _clean_len(s: str) -> int:
    return len(list(_TG_EMOJI_RE.sub(r"\1", s)))


def _normalize_texts(texts: Any) -> dict[str, str]:
    """Только известные ключи, непустые строки, лимит BTN_TEXT_MAX по чистому тексту."""
    out: dict[str, str] = {}
    if isinstance(texts, dict):
        for k, v in texts.items():
            if k in _CUSTOMIZABLE and isinstance(v, str):
                s = v.strip()
                if s:
                    out[k] = s if _clean_len(s) <= BTN_TEXT_MAX else "".join(list(s)[:BTN_TEXT_MAX])
    return out


def _normalize_colors(colors: Any) -> dict[str, str]:
    """Только известные ключи и допустимые цвета; пустое/невалидное отбрасываем."""
    out: dict[str, str] = {}
    if isinstance(colors, dict):
        for k, v in colors.items():
            if k in _CUSTOMIZABLE and isinstance(v, str) and v in VALID_COLORS:
                out[k] = v
    return out


def _normalize_order(order: Any) -> list[str]:
    """Только известные ключи, без дублей; недостающие добиваем в дефолтном порядке."""
    result: list[str] = []
    if isinstance(order, list):
        for k in order:
            if k in MENU_DEFAULTS and k not in result:
                result.append(k)
    for k in DEFAULT_ORDER:
        if k not in result:
            result.append(k)
    return result


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or not v.strip():
        return default
    return v.strip().lower() in ("1", "true", "yes", "on", "да")


def load_menu_config() -> dict[str, Any]:
    """Текущий состав+порядок кнопок: defaults → env (BOT_MENU_*) → menu.json."""
    data: dict[str, Any] = {
        k: _env_bool("BOT_MENU_" + k.upper(), v) for k, v in MENU_DEFAULTS.items()
    }
    order: Any = DEFAULT_ORDER
    try:
        if MENU_PATH.exists():
            with MENU_PATH.open(encoding="utf-8") as fh:
                stored = json.load(fh)
            if isinstance(stored, dict):
                for k in MENU_DEFAULTS:
                    if k in stored:
                        data[k] = bool(stored[k])
                if "order" in stored:
                    order = stored["order"]
                data["texts"] = _normalize_texts(stored.get("texts"))
                data["colors"] = _normalize_colors(stored.get("colors"))
    except Exception:
        # Битый файл не должен ронять меню — отдаём defaults/env.
        pass
    data["order"] = _normalize_order(order)
    data.setdefault("texts", {})
    data.setdefault("colors", {})
    return data


def save_menu_config(values: dict[str, Any]) -> dict[str, Any]:
    data = load_menu_config()
    for k in MENU_DEFAULTS:
        if values.get(k) is not None:
            data[k] = bool(values[k])
    if values.get("order") is not None:
        data["order"] = _normalize_order(values["order"])
    if values.get("texts") is not None:
        data["texts"] = _normalize_texts(values["texts"])
    if values.get("colors") is not None:
        data["colors"] = _normalize_colors(values["colors"])
    MENU_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MENU_PATH.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    return data
