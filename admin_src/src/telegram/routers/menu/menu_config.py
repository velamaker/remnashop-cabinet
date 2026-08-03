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
import re as _re
from urllib.parse import urlsplit as _urlsplit
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
    # OVERLAY: своя мини-аппа рядом с кабинетом. Кабинет ставят и поверх уже
    # работающих мини-приложений (Maposia, Orion и т.п.), и терять их кнопку
    # из-за нашей установки человек не должен. Ссылка задаётся отдельно
    # (custom_url ниже) или берётся из BOT_MINI_APP, если там URL.
    # По умолчанию ВЫКЛЮЧЕНА: у большинства своей мини-аппы нет.
    "custom_miniapp": False,
    # OVERLAY: кнопка «Подарить подписку» (открывает выбор тарифа, см. overlay_gift).
    # Не кнопка доступа — в DEFAULT_ORDER намеренно НЕ добавлена: её место в меню
    # фиксировано, настраиваются только тумблер, текст и цвет.
    "gift": True,
}

# Порядок кнопок в меню по умолчанию (как было зашито в dialog.py).
DEFAULT_ORDER: list[str] = [
    "cabinet_miniapp",
    "cabinet_url",
    "connect_miniapp",
    "connect_url",
    "remna_sub",
    "custom_miniapp",
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
    "custom_miniapp": "🚀 Мини-приложение",
    "nav_devices": "📱 Устройства",
    "nav_subscription": "🪪 Подписка",
    "nav_invite": "🎁 Пригласить",
    "nav_support": "💬 Поддержка",
    "nav_dashboard": "⚙️ Панель управления",
    "gift": "🎁 Подарить подписку",
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


# Адрес своей мини-аппы проверяем СТРОГО, а не по началу строки. Причина
# серьёзная: кнопку с адресом, который Telegram не примет, он отвергает вместе
# со ВСЕМ сообщением — падает не одна кнопка, а всё главное меню бота. Опечатка
# администратора не должна оставлять людей без меню.
_URL_BAD_CHARS = _re.compile(r"[\s<>\"']")


def validate_custom_url(value: Any) -> str:
    """Приводит адрес к рабочему виду. Пустая строка = «убрать ссылку».

    Бросает ValueError с человеческой причиной — админка показывает её как есть,
    вместо молчаливого «сохранено», после которого поле оказывалось пустым.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("Адрес должен быть строкой")
    url = value.strip()
    if not url:
        return ""
    if _URL_BAD_CHARS.search(url):
        raise ValueError("В адресе есть пробел или перенос строки — Telegram такую ссылку не примет")
    if len(url) > 512:
        raise ValueError("Адрес слишком длинный")
    try:
        parts = _urlsplit(url)
    except ValueError:
        raise ValueError("Не удалось разобрать адрес") from None
    scheme = (parts.scheme or "").lower()
    if scheme != "https":
        raise ValueError("Только https:// — по другим схемам Telegram мини-приложение не откроет")
    host = (parts.hostname or "").lower()
    if not host or "." not in host or host.startswith(".") or host.endswith("."):
        raise ValueError("В адресе нет домена")
    if parts.username or parts.password:
        raise ValueError("Логин и пароль в адресе недопустимы")
    # Схему и хост нормализуем: Telegram примет и HTTPS://, но хранить лучше одинаково.
    rest = url.split("://", 1)[1]
    tail = rest[len(parts.netloc):] if rest.lower().startswith(parts.netloc.lower()) else ""
    port = f":{parts.port}" if parts.port else ""
    return f"https://{host}{port}{tail}"


def _normalize_custom_url(value: Any) -> str:
    """То же самое при ЧТЕНИИ файла: битое значение (правили руками) не должно
    доехать до кнопки — иначе меню перестанет отправляться целиком."""
    try:
        return validate_custom_url(value)
    except ValueError:
        return ""


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
                if "custom_url" in stored:
                    data["custom_url"] = _normalize_custom_url(stored["custom_url"])
    except Exception:
        # Битый файл не должен ронять меню — отдаём defaults/env.
        pass
    data["order"] = _normalize_order(order)
    data.setdefault("texts", {})
    data.setdefault("colors", {})
    # Ключа в файле НЕТ (ни разу не сохраняли) — берём из BOT_MINI_APP, если там
    # адрес, а не true/false: у кого мини-аппа уже настроена в боте, кнопка
    # появится без правок. Пустая строка в файле — это осознанное «убрать»,
    # её окружением не перебиваем.
    if "custom_url" not in data:
        data["custom_url"] = _normalize_custom_url(os.environ.get("BOT_MINI_APP"))
    return data


def _stored_raw() -> dict[str, Any]:
    """Файл как есть — чтобы отличить «ключа не было» от «значение пустое»."""
    try:
        with MENU_PATH.open(encoding="utf-8") as fh:
            stored = json.load(fh)
        return stored if isinstance(stored, dict) else {}
    except Exception:
        return {}


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
    if values.get("custom_url") is not None:
        data["custom_url"] = validate_custom_url(values["custom_url"])
    elif "custom_url" not in _stored_raw():
        # Админ ссылку не задавал — значение в data пришло из BOT_MINI_APP.
        # Записывать его в файл нельзя: тогда смена .env перестанет работать.
        data.pop("custom_url", None)
    MENU_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MENU_PATH.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    return data
