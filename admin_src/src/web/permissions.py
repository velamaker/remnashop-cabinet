"""Гранулярные права админки (overlay).

Enum-роли (USER/PREVIEW/ADMIN/DEV/OWNER/SYSTEM) зашиты в базовый образ и не
расширяемы. Поэтому тонкие права хранятся отдельно — в таблице `admin_grants`
(создаётся идемпотентным DDL при старте, см. overlay_app._SUPPORT_TABLES_DDL).

Модель — «гибрид»:
  • full_access=true  → доступ ко всем разделам;
  • иначе доступ только к разделам из `sections` (ключи ниже);
  • can_write=false   → режим просмотра (мутации запрещены, как у PREVIEW);
  • expires_at        → после этого момента грант недействителен (падаем на enum).

Раздел определяется по первому сегменту пути под /api/v1/admin/ (напр.
/api/v1/admin/users/... → сегмент "users"). Каждый раздел владеет одним или
несколькими сегментами (см. SECTIONS[*]["prefixes"]).
"""

from datetime import datetime, timezone
from typing import Any, Optional

from src.core.enums import Role

# Разделы админки: ключ, человекочитаемая метка, какие path-сегменты покрывает.
# Порядок = порядок отображения в UI.
SECTIONS: list[dict[str, Any]] = [
    {"key": "dashboard", "label": "Обзор и статистика", "prefixes": ["statistics"]},
    {"key": "users", "label": "Пользователи", "prefixes": ["users"]},
    {"key": "subscriptions", "label": "Подписки", "prefixes": ["subscriptions"]},
    {"key": "transactions", "label": "Транзакции", "prefixes": ["transactions"]},
    {"key": "plans", "label": "Тарифы", "prefixes": ["plans"]},
    {"key": "promocodes", "label": "Промокоды", "prefixes": ["promocodes"]},
    {"key": "gateways", "label": "Платёжные шлюзы", "prefixes": ["gateways"]},
    {"key": "broadcasts", "label": "Рассылки", "prefixes": ["broadcasts"]},
    {"key": "ad_links", "label": "Рекламные ссылки", "prefixes": ["ad-links"]},
    {"key": "support", "label": "Поддержка", "prefixes": ["support"]},
    {"key": "remnawave", "label": "RemnaWave", "prefixes": ["remnawave"]},
    {
        "key": "content",
        "label": "Оформление и контент",
        "prefixes": ["appearance", "apps", "menu", "info"],
    },
    {
        "key": "settings",
        "label": "Настройки",
        "prefixes": ["settings", "email-settings", "email-template", "auth-settings"],
    },
    {"key": "audit", "label": "Журнал действий", "prefixes": ["audit"]},
    {"key": "updates", "label": "Обновления", "prefixes": ["updates"]},
]

ALL_SECTION_KEYS: list[str] = [s["key"] for s in SECTIONS]

# Обратный индекс: path-сегмент → ключ раздела.
_PREFIX_TO_SECTION: dict[str, str] = {
    p: s["key"] for s in SECTIONS for p in s["prefixes"]
}

# Пресеты ролей: заготовки наборов разделов. "admin" — полный доступ.
PRESETS: list[dict[str, Any]] = [
    {"key": "admin", "label": "Администратор", "full_access": True, "sections": []},
    {
        "key": "moderator",
        "label": "Модератор",
        "full_access": False,
        "sections": ["users", "subscriptions", "support", "remnawave"],
    },
    {
        "key": "marketer",
        "label": "Маркетолог",
        "full_access": False,
        "sections": ["dashboard", "broadcasts", "promocodes", "ad_links", "plans", "content"],
    },
    {
        "key": "support",
        "label": "Поддержка",
        "full_access": False,
        "sections": ["support", "subscriptions", "users"],
    },
]


def _grant_expired(expires_at: Any, now: Optional[datetime] = None) -> bool:
    if expires_at is None:
        return False
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at)
        except ValueError:
            return False
    now = now or datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return now >= expires_at


def compute_access(
    role: int,
    grant: Optional[dict[str, Any]],
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Единая точка расчёта эффективных прав из enum-роли + строки admin_grants.

    Приоритет: OWNER+ → полный доступ всегда; иначе действующий грант; иначе
    legacy-enum (ADMIN+ = полный доступ, PREVIEW = только просмотр). Истёкший или
    отсутствующий грант → падаем на enum (для обычного USER это = нет доступа).

    Возвращает: allowed, full_access, can_write, sections(list), is_owner, expires_at, source.
    """
    is_owner = role >= Role.OWNER
    base = {
        "allowed": False,
        "full_access": False,
        "can_write": False,
        "sections": [],
        "is_owner": is_owner,
        "expires_at": None,
        "source": "none",
    }

    if is_owner:
        return {**base, "allowed": True, "full_access": True, "can_write": True, "source": "owner"}

    grant_valid = grant is not None and not _grant_expired(grant.get("expires_at"), now)
    if grant_valid:
        full = bool(grant.get("full_access"))
        secs = ALL_SECTION_KEYS if full else normalize_sections(grant.get("sections"))
        exp = grant.get("expires_at")
        return {
            "allowed": True,
            "full_access": full,
            "can_write": bool(grant.get("can_write", True)),
            "sections": list(secs),
            "is_owner": False,
            "expires_at": exp.isoformat() if hasattr(exp, "isoformat") else exp,
            "source": "grant",
        }

    # Legacy: полноправные админы по enum сохраняют полный доступ, PREVIEW — просмотр.
    if role >= Role.ADMIN:
        return {**base, "allowed": True, "full_access": True, "can_write": True, "source": "enum_admin"}
    if role >= Role.PREVIEW:
        return {**base, "allowed": True, "full_access": True, "can_write": False, "source": "enum_preview"}
    return base


def access_permits(access: dict[str, Any], path: str, method: str) -> Optional[str]:
    """Проверяет доступ к конкретному запросу. None = разрешено, иначе текст 403.

    Незнакомый admin-путь (section=None) требует полного доступа — безопасный дефолт.
    """
    if not access.get("allowed"):
        return "Admin access required"
    mutating = method not in ("GET", "HEAD", "OPTIONS")
    if mutating and not access.get("can_write"):
        return "Read-only admin: изменения недоступны"
    if access.get("full_access"):
        return None
    section = section_for_path(path)
    if section is None or section not in access.get("sections", []):
        return "Недостаточно прав для этого раздела"
    return None


def section_for_path(path: str) -> Optional[str]:
    """Ключ раздела для пути запроса. None — путь не под /admin/ или неизвестен."""
    marker = "/admin/"
    idx = path.find(marker)
    if idx == -1:
        return None
    rest = path[idx + len(marker):].lstrip("/")
    if not rest:
        return None
    segment = rest.split("/", 1)[0].split("?", 1)[0]
    return _PREFIX_TO_SECTION.get(segment)


def normalize_sections(sections: Any) -> list[str]:
    """Оставляет только валидные ключи разделов (защита от мусора из UI)."""
    if not isinstance(sections, (list, tuple)):
        return []
    valid = set(ALL_SECTION_KEYS)
    seen: list[str] = []
    for s in sections:
        if s in valid and s not in seen:
            seen.append(s)
    return seen


def sections_catalog() -> list[dict[str, str]]:
    """Каталог разделов для фронта (ключ+метка)."""
    return [{"key": s["key"], "label": s["label"]} for s in SECTIONS]


def presets_catalog() -> list[dict[str, Any]]:
    """Каталог пресетов для фронта."""
    return [
        {
            "key": p["key"],
            "label": p["label"],
            "full_access": p["full_access"],
            "sections": list(p["sections"]),
        }
        for p in PRESETS
    ]
