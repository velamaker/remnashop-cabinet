"""Каждая админская ручка принадлежит разделу прав — иначе меню и 403 расходятся.

ЗАЧЕМ. Список пунктов меню строит кабинет (у каждого пункта свой раздел), а доступ
проверяет бот по первому сегменту пути (`permissions.section_for_path`). Ручка,
сегмента которой нет ни в одном разделе, требует ПОЛНОГО доступа. Итог — худший
вид отказа: делегированный админ с разделом «Настройки» видит пункт «Напоминание об
оплате», открывает его и получает 403, хотя соседние страницы раздела у него
работают. Так и было: `payment-reminder` в раздел не добавили, когда делали
страницу.

Тест смотрит на настоящий роутер админки, а не на список, переписанный руками:
новая страница без раздела упадёт здесь сразу.
"""

import importlib

import pytest

permissions = importlib.import_module("src.web.permissions")

# Сегменты, которые НАМЕРЕННО без раздела: доступны только владельцу/полному доступу.
OWNER_ONLY = {
    "grants",  # управление правами админов — только OWNER (см. endpoints/admin/grants.py)
}


def _paths(routes) -> list[str]:
    """Пути ручек. FastAPI 0.140 кладёт подключённый роутер обёрткой `_IncludedRouter`
    (пути — у `original_router`), старые версии — плоским списком с полным путём."""
    found: list[str] = []
    for route in routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            found.extend(_paths(original.routes))
        elif getattr(route, "path", None):
            found.append(route.path)
    return found


def _admin_segments() -> set[str]:
    admin = importlib.import_module("src.web.endpoints.admin")
    segments: set[str] = set()
    for path in _paths(admin.router.routes):
        rest = path.split("/admin/", 1)[1] if "/admin/" in path else path
        segment = rest.lstrip("/").split("/", 1)[0].split("?", 1)[0]
        if segment:
            segments.add(segment)
    return segments


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/admin/payment-reminder",
        "/api/v1/admin/churn-signals",
    ],
)
def test_settings_pages_belong_to_settings(path):
    assert permissions.section_for_path(path) == "settings"


def test_payment_reminder_opens_for_an_admin_with_only_settings():
    """Ровно тот человек, у которого был 403: грант только на «Настройки»."""
    access = permissions.compute_access(
        permissions.Role.USER, {"full_access": False, "sections": ["settings"], "can_write": True}
    )
    assert permissions.access_permits(access, "/api/v1/admin/payment-reminder", "GET") is None
    assert permissions.access_permits(access, "/api/v1/admin/payment-reminder", "PUT") is None


def test_every_admin_route_belongs_to_a_section():
    segments = _admin_segments()
    # Самопроверка теста: роутер действительно прочитан, а не пуст.
    assert {"users", "payment-reminder", "churn-signals"} <= segments
    orphans = sorted(
        seg
        for seg in segments - OWNER_ONLY
        if permissions.section_for_path(f"/api/v1/admin/{seg}") is None
    )
    assert orphans == [], f"ручки без раздела — делегированный админ получит 403: {orphans}"
