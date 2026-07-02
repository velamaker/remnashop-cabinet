"""Маскировка чувствительных данных для роли «админ только для просмотра» (PREVIEW).

PREVIEW видит все разделы и страницы (меню не урезаем), но важные данные приходят
затёртыми — и в интерфейсе, и при прямом обращении к API (у viewer'а живая сессия,
поэтому прятать только в UI недостаточно). Запись блокируется отдельно в _common.py.

Что прячем:
  • Пользователи — все идентификаторы (внутренний id, telegram_id, @username,
    email, реф.код); видно только отображаемое имя (name).
  • Транзакции — кто платил (user_id, email) и payment_id; имя пользователя видно.
  • Ноды/хосты/inbounds RemnaWave — адреса, порты, конфиги, провайдер, железо;
    остаются имя/метка и статус онлайн/нагрузка (для мониторинга).
"""

import contextvars
from typing import Any

from src.application.dto import UserDto
from src.core.enums import Role

# Решение «только просмотр» на текущий запрос. Ставится в _get_admin_user из
# эффективных прав (can_write): read-only может задаваться и грантом, не только
# enum-ролью PREVIEW. Контекстно-локально — у каждого запроса своё значение.
_readonly_ctx: contextvars.ContextVar = contextvars.ContextVar("admin_readonly", default=None)


def set_request_readonly(value: bool) -> None:
    _readonly_ctx.set(value)


def is_readonly_admin(admin: UserDto) -> bool:
    """True для режима «только просмотр».

    Если для запроса вычислены эффективные права (грант или enum) — берём их;
    иначе фолбэк на enum-роль (PREVIEW и ниже ADMIN)."""
    v = _readonly_ctx.get()
    if v is not None:
        return bool(v)
    return admin.role < Role.ADMIN


def redact_user(d: dict[str, Any]) -> dict[str, Any]:
    # username = телеграм @хэндл (тоже идентификатор) — прячем; имя (name) остаётся.
    out = dict(d)
    for k in ("id", "telegram_id", "username", "email", "referral_code"):
        if k in out:
            out[k] = None
    return out


def redact_transaction(d: dict[str, Any]) -> dict[str, Any]:
    out = dict(d)
    for k in ("user_id", "user_email", "payment_id"):
        if k in out:
            out[k] = None
    return out


# Поля ноды, безопасные для просмотра (мониторинг). uuid оставляем — он нужен фронту
# как ключ/для рендера и сам по себе не раскрывает инфраструктуру; адрес/порт/конфиг
# и железо ниже затираются.
_NODE_KEEP = {
    "uuid", "name", "country_code", "is_connected", "is_connecting", "is_disabled",
    "is_traffic_tracking_active", "users_online", "node_version", "xray_version",
    "xray_uptime", "traffic_used_bytes", "traffic_limit_bytes", "view_position",
    "last_status_change", "last_status_message", "created_at", "updated_at",
}

_HOST_KEEP = {"uuid", "remark", "is_disabled", "is_hidden", "view_position", "tag"}

_INBOUND_KEEP = {"uuid", "tag", "type", "network", "security"}


def _keep_only(d: Any, keep: set[str]) -> Any:
    if not isinstance(d, dict):
        return d
    return {k: (v if k in keep else None) for k, v in d.items()}


def redact_node(d: Any) -> Any:
    return _keep_only(d, _NODE_KEEP)


def redact_host(d: Any) -> Any:
    out = _keep_only(d, _HOST_KEEP)
    # inbound оставляем только меткой tag — без адресов/конфигов.
    if isinstance(d, dict) and isinstance(d.get("inbound"), dict):
        out["inbound"] = {"tag": d["inbound"].get("tag")}
    return out


def redact_inbound(d: Any) -> Any:
    return _keep_only(d, _INBOUND_KEEP)
