"""Отказы на входе и регистрации: человек должен читать причину, а не JSON.

ЗАЧЕМ. Кабинет печатает поле `detail` как есть и умеет только строку. А бот
«Бедолага» с v4.11.0 отвечает на регистрации ОБЪЕКТОМ: согласие с документами —
428 с `code: legal_consent_required`, приглашение — 403 с
`registration_invite_required`. Раньше тело уходило наружу нетронутым, и вместо
причины человек видел фигурные скобки — на самой первой странице, до которой он
дошёл.

Отдельно про 429: их новый дроссель регистрации и входа отдаёт голое
`Too many requests` и заголовок `Retry-After`. Заголовок обязан доехать: без него
«слишком много попыток» не отличить от поломки, и человек жмёт кнопку ещё раз.
"""

import importlib
import json

import httpx

main = importlib.import_module("main")


def resp(status: int, body=None, headers=None) -> httpx.Response:
    return httpx.Response(status, json=body, headers=headers or {})


def detail_of(out) -> str:
    return json.loads(bytes(out.body).decode())["detail"]


def test_legal_consent_is_explained():
    """428 с их кодом — в понятную фразу, а не в сырой объект."""
    out = main._auth_error(resp(428, {"detail": {
        "code": "legal_consent_required",
        "message": "Consent to the legal documents is required to create an account",
        "documents": [], "missing": ["offer"], "prechecked": False,
    }}))
    assert out.status_code == 428
    assert detail_of(out) == (
        "Чтобы создать аккаунт, примите оферту и политику конфиденциальности"
    )


def test_invite_only_is_explained():
    out = main._auth_error(resp(403, {"detail": {
        "code": "registration_invite_required", "message": "Invite required",
    }}))
    assert "по приглашению" in detail_of(out)


def test_unknown_code_falls_back_to_their_message():
    """Незнакомый код — печатаем их фразу, а не «что-то пошло не так».

    Английский текст хуже русского, но он хотя бы что-то объясняет.
    """
    out = main._auth_error(resp(400, {"detail": {
        "code": "some_new_thing", "message": "Account is locked",
    }}))
    assert detail_of(out) == "Account is locked"


def test_throttle_says_how_long_to_wait():
    """Главное в 429 — не сам отказ, а срок."""
    out = main._auth_error(resp(429, {"detail": "Too many requests"},
                                {"Retry-After": "60"}))
    assert detail_of(out) == "Слишком много попыток. Повторите через 60 с."
    assert out.headers["retry-after"] == "60"

    out = main._auth_error(resp(429, {"detail": "Too many requests"},
                                {"Retry-After": "3600"}))
    assert "60 мин." in detail_of(out)


def test_throttle_without_retry_after_still_reads():
    out = main._auth_error(resp(429, {"detail": "Too many requests"}))
    assert detail_of(out) == "Слишком много попыток. Попробуйте позже."


def test_pydantic_validation_list_is_joined():
    out = main._auth_error(resp(422, {"detail": [
        {"msg": "value is not a valid email address"},
        {"msg": "password too short"},
    ]}))
    assert detail_of(out) == "value is not a valid email address; password too short"


def test_common_english_reasons_are_translated():
    """Форма входа — единственная страница, куда доходит человек со стороны.

    «Invalid email or password» посреди русского экрана читается как поломка
    сайта, а не как опечатка в пароле.
    """
    out = main._auth_error(resp(401, {"detail": "Invalid email or password"}))
    assert detail_of(out) == "Неверная почта или пароль"
    assert out.status_code == 401

    out = main._auth_error(resp(403, {"detail": "Please verify your email first"}))
    assert detail_of(out).startswith("Сначала подтвердите почту")


def test_translation_is_case_and_space_tolerant():
    """Их фразы приходят строкой; регистр и пробелы не должны решать судьбу текста."""
    out = main._auth_error(resp(401, {"detail": "  INVALID EMAIL OR PASSWORD  "}))
    assert detail_of(out) == "Неверная почта или пароль"


def test_unknown_english_reason_is_left_alone():
    """Незнакомую фразу не пересказываем: английский текст честнее выдумки."""
    out = main._auth_error(resp(400, {"detail": "Invalid token payload"}))
    assert detail_of(out) == "Invalid token payload"


def test_russian_detail_survives():
    out = main._auth_error(resp(401, {"detail": "Неверная почта или пароль"}))
    assert detail_of(out) == "Неверная почта или пароль"


def test_unreadable_body_does_not_produce_empty_reason():
    """Тело не JSON или без detail — всё равно говорим человеку что-то внятное."""
    assert detail_of(main._auth_error(httpx.Response(500, content=b"<html>"))) == (
        "Не удалось выполнить запрос"
    )
    assert detail_of(main._auth_error(resp(400, {"error": "oops"}))) == (
        "Не удалось выполнить запрос"
    )


def test_status_is_never_rewritten():
    """Код ответа — часть договора с кабинетом: 401 гасит сессию, 429 — нет."""
    for status in (400, 401, 403, 409, 422, 428, 429, 500):
        assert main._auth_error(resp(status, {"detail": "x"})).status_code == status
