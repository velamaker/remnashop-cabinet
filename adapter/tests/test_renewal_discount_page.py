"""«Скидка до окончания подписки» поверх «Бедолаги» не строится — и не ломает админку.

ЧТО ЗАПЕРТО. Страница объявлена в карте страниц (кабинет знает её раздел), но её
ручки адаптер не отдаёт: значит, её нет в `admin_pages()` → нет в whoami.pages →
пункт меню скрыт, а по прямой ссылке кабинет покажет «страница недоступна».
Раздел «Настройки» из-за неё не закрывается: в ADMIN_SECTION_REQUIREMENTS её нет.

Появится вдруг обработчик с таким путём (или проброс в карте маршрутов) — тест
упадёт: скидку через `purchase_discount` и итоги по нашим транзакциям на их базе
строить нельзя, это надо решать осознанно.

Запуск — внутри образа адаптера (см. другие тесты рядом).
"""

import importlib
import json
from pathlib import Path

compose = importlib.import_module("compose")
main = importlib.import_module("main")

PAGE = "/admin/renewal-discount"
ADMIN_PATHS = [
    ("GET", "/api/admin/renewal-discount"),
    ("PUT", "/api/admin/renewal-discount"),
    ("GET", "/api/admin/renewal-discount/preview"),
    ("GET", "/api/admin/renewal-discount/stats"),
    ("POST", "/api/admin/renewal-discount/test-send"),
    ("POST", "/api/admin/renewal-discount/revoke-active"),
]


def test_page_is_declared_but_not_served():
    assert compose.ADMIN_PAGE_REQUIREMENTS[PAGE] == [("GET", "/api/admin/renewal-discount")]
    assert compose.ADMIN_PAGE_SECTION[PAGE] == "settings"
    assert not any(
        "renewal-discount" in path
        for needs in compose.ADMIN_SECTION_REQUIREMENTS.values()
        for _method, path in needs
    )
    # Настоящая карта маршрутов, а не «ничего не знаем»: иначе False был бы даром.
    previous = compose._serves
    compose.set_route_probe(main._serves)
    try:
        assert PAGE not in compose.admin_pages()
        for method, path in ADMIN_PATHS:
            assert compose._implemented(method, path) is False, (method, path)
        assert compose._implemented("GET", "/api/renewal-discount") is False
    finally:
        compose._serves = previous


def test_route_map_marks_paths_as_todo():
    """Карта маршрутов перечисляет пути как непереводимые — адаптер ответит 501."""
    routes = json.loads(Path(compose.__file__).with_name("route_map.json").read_text("utf-8"))
    assert "GET /api/renewal-discount" in routes["todo"]
    for method, path in ADMIN_PATHS:
        assert f"{method} {path}" in routes["todo_admin"]
    assert not any("renewal-discount" in p for p in routes["routes"])
