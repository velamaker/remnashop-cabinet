"""«Нужно больше устройств?» поверх «Бедолаги» выключен явно.

ПОЧЕМУ ОТДЕЛЬНЫЙ ЗАМОК. Возможность `device_upsell` не привязана к пути: витрину
(`/subscription/offers`) и устройства адаптер обслуживает, поэтому по наличию
ручек блок посчитался бы доступным. А строить его поверх их бота нельзя: у них
своя докупка устройств, лимит в ответах уже с докупленными, смена тарифа
устроена иначе — кабинет предложил бы не тот тариф и не ту цену.

Кабинет читает «нет ключа» как «умеет», так что пропажа этого false молча
включила бы блок и запросы к витрине с Главной у всех кабинетов поверх них.

Запуск — внутри образа адаптера (см. другие тесты рядом).
"""

import importlib

compose = importlib.import_module("compose")


def test_device_upsell_is_off_even_when_every_route_is_served():
    compose.set_route_probe(lambda method, path: True)
    try:
        known = compose.features()
        assert known is not None
        assert known["device_upsell"] is False
        # Сам раздел покупки при этом жив — выключен только блок.
        assert known["purchase"] is True
    finally:
        compose._serves = None


def test_unknown_route_map_still_means_show_everything():
    """Карта не прочиталась — features() None, как и раньше (кабинет не схлопывается).

    Блок тогда тоже не появится: без флага `plan_change_keeps_days` в витрине
    кабинет его не рисует — это заперто тестами кабинета.
    """
    compose._serves = None
    assert compose.features() is None
