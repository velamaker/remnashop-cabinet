"""Чей адрес адаптер называет боту.

ЗАЧЕМ ЭТО ВАЖНО. Бот «Бедолага» с v4.11.0 считает по адресу посетителя лимиты
регистрации (час и сутки) и входа (10 попыток в минуту). Адаптер — единственный,
кого бот видит, поэтому настоящий адрес ему приходится пересылать самому. Отсюда
две противоположные опасности:

  * не переслать — и все лимиты бота лягут на ОДИН адрес, то есть на весь
    кабинет сразу: тридцать регистраций в сутки на всех и десять попыток входа
    в минуту на всех;
  * переслать подделку — и лимиты можно обойти в одну строку, а заодно
    заблокировать вход чужому адресу.

ЧТО БЫЛО. Брался ПЕРВЫЙ элемент `X-Forwarded-For`. Наш nginx собирает этот
заголовок как `$proxy_add_x_forwarded_for`, то есть дописывает свой адрес в
КОНЕЦ к тому, что прислал браузер — первый элемент был чужим текстом.

Запуск — внутри образа адаптера:

  docker build -t remnashop-adapter-ci adapter
  docker run --rm -v "$PWD/adapter/tests:/tmp/tests:ro" remnashop-adapter-ci \
    sh -c 'pip install -q --target /tmp/pylibs pytest &&
           PYTHONPATH=/tmp/pylibs python -m pytest /tmp/tests -q -p no:cacheprovider'
"""

import importlib
from types import SimpleNamespace

main = importlib.import_module("main")


def request(headers: dict, peer: str | None = "10.0.0.9"):
    """Запрос ровно в том объёме, в каком его читает _client_ip."""
    lowered = {k.lower(): v for k, v in headers.items()}
    return SimpleNamespace(
        headers=lowered,
        client=SimpleNamespace(host=peer) if peer else None,
    )


def test_real_ip_wins():
    """`X-Real-IP` ставит наш nginx из вычисленного $remote_addr — ему и верим."""
    assert main._client_ip(request({"X-Real-IP": "203.0.113.7"})) == "203.0.113.7"


def test_spoofed_chain_head_is_ignored():
    """Та самая дыра: браузер назвался чужим адресом.

    nginx дописал настоящий адрес в конец, поэтому подделка стоит в начале —
    и раньше именно её мы и пересылали боту.
    """
    headers = {"X-Forwarded-For": "1.2.3.4, 203.0.113.7"}
    assert main._client_ip(request(headers)) == "203.0.113.7"

    # И даже когда подделка притворяется целой цепочкой прокси.
    headers = {"X-Forwarded-For": "1.2.3.4, 5.6.7.8, 9.10.11.12, 203.0.113.7"}
    assert main._client_ip(request(headers)) == "203.0.113.7"


def test_real_ip_beats_forwarded_for():
    """Оба заголовка на месте — берём тот, который подделать нельзя."""
    headers = {"X-Forwarded-For": "1.2.3.4, 203.0.113.7", "X-Real-IP": "203.0.113.7"}
    assert main._client_ip(request(headers)) == "203.0.113.7"


def test_single_hop_chain():
    """Один прокси — в цепочке ровно его адрес."""
    assert main._client_ip(request({"X-Forwarded-For": "203.0.113.7"})) == "203.0.113.7"


def test_trailing_and_empty_parts_are_skipped():
    """Пустые элементы в цепочке не должны выдаваться за адрес."""
    headers = {"X-Forwarded-For": "1.2.3.4, 203.0.113.7, , "}
    assert main._client_ip(request(headers)) == "203.0.113.7"


def test_falls_back_to_connection_peer():
    """Заголовков нет вовсе — остаётся адрес соединения."""
    assert main._client_ip(request({}, peer="10.0.0.9")) == "10.0.0.9"
    assert main._client_ip(request({"X-Real-IP": "   "}, peer="10.0.0.9")) == "10.0.0.9"


def test_no_peer_gives_empty_and_not_a_crash():
    """Адрес неизвестен — пустая строка; _forwarded на неё не отправит заголовков."""
    assert main._client_ip(request({}, peer=None)) == ""
    assert main._forwarded(request({}, peer=None)) == {}


def test_forwarded_headers_carry_the_trusted_address():
    """Боту уходит именно доверенный адрес, а не голова цепочки."""
    headers = {"X-Forwarded-For": "1.2.3.4, 203.0.113.7", "X-Forwarded-Proto": "https"}
    sent = main._forwarded(request(headers))
    assert sent["x-forwarded-for"] == "203.0.113.7"
    assert sent["x-real-ip"] == "203.0.113.7"


def test_incoming_address_headers_never_pass_through():
    """Клиентские заголовки адреса до бота доходить не должны вообще.

    Иначе рядом с нашим значением поехал бы ещё и присланный браузером, и какой
    из них выберет бот — вопрос его настроек, а не наш.
    """
    for name in ("x-forwarded-for", "x-real-ip", "x-forwarded-proto"):
        assert name in main._HOP_BY_HOP
