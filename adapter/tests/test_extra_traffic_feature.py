"""Докупка трафика поверх «Бедолаги» выключена явно.

ПОЧЕМУ ОТДЕЛЬНЫЙ ЗАМОК. Лимит трафика у «Бедолаги» живёт по своим правилам: свои
сбросы, свой учёт, свой смысл у числа в ответах. Наших таблиц `extra_traffic_*` там
нет вовсе, и ручки `/subscription/extra-traffic` адаптер не обслуживает.

Кабинет читает «нет ключа» как «умеет», поэтому пропажа этого false молча включила
бы карточку докупки у всех кабинетов поверх «Бедолаги» — с ценой, датой обновления
трафика и кнопкой, которая упирается в 404 уже после подтверждения суммы. Полагаться
только на карту путей нельзя: она читается из файла и может не прочитаться вовсе.

Запуск — внутри образа адаптера (см. другие тесты рядом).
"""

import importlib

compose = importlib.import_module("compose")


def test_extra_traffic_is_off_even_when_every_route_is_served():
    compose.set_route_probe(lambda method, path: True)
    try:
        known = compose.features()
        assert known is not None
        assert known["extra_traffic"] is False
        # Покупка и витрина при этом живут — выключена только наша докупка.
        assert known["purchase"] is True
        assert known["subscription"] is True
    finally:
        compose._serves = None


def test_extra_traffic_is_off_when_routes_are_missing():
    compose.set_route_probe(lambda method, path: "extra-traffic" not in path)
    try:
        assert compose.features()["extra_traffic"] is False
    finally:
        compose._serves = None


def test_other_sections_are_untouched_by_the_new_key():
    """Новый ключ не должен погасить ничего из того, что работало вчера."""
    compose.set_route_probe(lambda method, path: True)
    try:
        known = compose.features()
        # Данные панели принадлежат не боту — их гасить нельзя ни при каких ключах.
        for not_bot_owned in compose.NOT_BOT_OWNED:
            assert not_bot_owned not in known, (
                f"{not_bot_owned} — данные панели или фишка кабинета, в списке их быть не должно"
            )
        # Соседние наши докупки не перепутаны.
        assert known["extra_device"] is False
        assert known["device_upsell"] is False
    finally:
        compose._serves = None
