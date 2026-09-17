"""Массовые «+N дней» и «Написать» поверх «Бедолаги» выключены — и остаются выключенными.

ЧТО ЗАПЕРТО. Кабинет читает «нет ключа» как «умеет». Пропади ключи `bulk_days` и
`bulk_message` из возможностей адаптера — чужой админ увидел бы в «Пользователях»
пункты «Добавить дни подписки…» и «Написать сообщение…», за которыми нет ручек.
Ключи привязаны к путям предпросмотра: пока адаптер их не отдаёт, пунктов нет.

Появится обработчик или проброс этих путей — тест на настоящей карте маршрутов
упадёт: выдавать дни и слать сообщения поверх их бота надо решать осознанно.

Запуск — внутри образа адаптера (см. другие тесты рядом).
"""

import importlib
import json
from pathlib import Path

compose = importlib.import_module("compose")
main = importlib.import_module("main")

PREVIEW_PATHS = {
    "bulk_days": "/api/admin/users/bulk/days/preview",
    "bulk_message": "/api/admin/users/bulk/message/preview",
}


def test_features_off_when_paths_are_not_served(monkeypatch):
    monkeypatch.setattr(compose, "_serves", lambda method, path: False)
    known = compose.features()
    assert known is not None
    assert known["bulk_days"] is False
    assert known["bulk_message"] is False


def test_features_follow_the_preview_paths(monkeypatch):
    served = {("GET", p) for p in PREVIEW_PATHS.values()}
    monkeypatch.setattr(compose, "_serves", lambda method, path: (method, path) in served)
    known = compose.features()
    assert known["bulk_days"] is True
    assert known["bulk_message"] is True


def test_real_route_map_does_not_serve_bulk_paths(monkeypatch):
    monkeypatch.setattr(compose, "_serves", main._serves)
    known = compose.features()
    assert known["bulk_days"] is False and known["bulk_message"] is False
    for path in PREVIEW_PATHS.values():
        assert compose._implemented("GET", path) is False
    # Карточка пользователя `/users/{id}` многосегментные пути не перехватывает.
    assert compose._implemented("GET", "/api/admin/users/bulk/jobs") is False
    routes = json.loads(Path(compose.__file__).with_name("route_map.json").read_text("utf-8"))
    for path in PREVIEW_PATHS.values():
        assert f"GET {path}" in routes["todo_admin"]
    assert not any("/users/bulk/" in p for p in routes["routes"])
