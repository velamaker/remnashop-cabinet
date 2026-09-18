"""Докупка «+1 устройства» поверх «Бедолаги» выключена явно.

ПОЧЕМУ ОТДЕЛЬНЫЙ ЗАМОК. У «Бедолаги» докупка устройств СВОЯ: своя цена, свой
минимум, своё списание с баланса, а `device_limit` в их ответах уже включает
докупленные места. Наш блок поверх них предложил бы вторую покупку того же самого
по нашей цене — и пошёл бы за ручкой, которой у них нет.

Кабинет читает «нет ключа» как «умеет», поэтому пропажа этого false молча включила
бы блок у всех кабинетов поверх «Бедолаги». Ручек `extra-device` адаптер не
обслуживает, но полагаться только на карту путей нельзя: она читается из файла и
может не прочитаться вовсе.

Запуск — внутри образа адаптера (см. другие тесты рядом).
"""

import importlib

compose = importlib.import_module("compose")


def test_extra_device_is_off_even_when_every_route_is_served():
    compose.set_route_probe(lambda method, path: True)
    try:
        known = compose.features()
        assert known is not None
        assert known["extra_device"] is False
        # Покупка при этом живёт — выключена только наша докупка.
        assert known["purchase"] is True
    finally:
        compose._serves = None


def test_extra_device_is_off_when_routes_are_missing():
    compose.set_route_probe(lambda method, path: "extra-device" not in path)
    try:
        assert compose.features()["extra_device"] is False
    finally:
        compose._serves = None
