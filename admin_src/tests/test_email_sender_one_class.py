"""Кнопка «Проверить» обязана проверять ту же почту, через которую уходят письма.

ЧТО СЛУЧИЛОСЬ (жалоба с установки из GitHub, 25.09.2026). Человек настроил SMTP,
тестовая отправка из админки прошла, а привязка резервной почты и подтверждение
текущей падали у Gmail с `534 5.7.9 Application-specific password required`.

Причина — два разных класса отправителя под одним именем. Наша правка
(overlay_patches/email_sender.py) подменяла `SmtpEmailSender` только в ПАКЕТЕ
`src.infrastructure.services` — оттуда его берёт контейнер, и настоящие письма
шли через наш класс с настройками из админки поверх `.env`. А кнопки «Проверить»
и оповещение о входе импортируют имя из МОДУЛЯ `...services.email_sender`, где
оставался исходный класс, читающий ТОЛЬКО `.env`. Итог: тест проверял один
пароль, письма слались с другим, и по тесту нельзя было понять ничего.

ЧТО ЗАПЕРТО ЗДЕСЬ:
  * под именем `SmtpEmailSender` везде — в пакете, в модуле и в админских
    ручках — лежит один и тот же наш класс;
  * созданный по имени отправитель читает настройки из админки, как и контейнер;
  * ошибка Gmail «нужен пароль приложения» объясняется словами, а исходный ответ
    сервера не теряется.
"""

import importlib
import json
import smtplib

import pytest

from src.core.config import AppConfig
from src.core.exceptions import EmailDeliveryError

settings_mod = importlib.import_module("src.infrastructure.services.email_settings")


def _overlay_class():
    patch = importlib.import_module("overlay_patches.email_sender")
    patch.apply()  # идемпотентно: второй вызов ничего не делает
    return patch.OverlaySmtpEmailSender


class TestОдинКлассВезде:
    def test_пакет_и_модуль_отдают_наш_класс(self):
        ours = _overlay_class()
        pkg = importlib.import_module("src.infrastructure.services")
        mod = importlib.import_module("src.infrastructure.services.email_sender")
        assert pkg.SmtpEmailSender is ours, "контейнер получил бы не наш отправитель"
        assert mod.SmtpEmailSender is ours, (
            "по имени из модуля создаётся исходный класс — кнопка «Проверить» "
            "проверит .env, а письма уйдут с настройками из админки"
        )

    @pytest.mark.parametrize(
        "module_name",
        [
            "src.web.endpoints.admin.email_settings",
            "src.web.endpoints.admin.email_template",
        ],
    )
    def test_админские_проверки_держат_наш_класс(self, module_name):
        ours = _overlay_class()
        module = importlib.import_module(module_name)
        assert module.SmtpEmailSender is ours, (
            f"{module_name} проверяет почту не тем отправителем, которым она шлётся"
        )

    def test_созданный_по_имени_читает_настройки_из_админки(self, tmp_path, monkeypatch):
        _overlay_class()
        stored = tmp_path / "email.json"
        stored.write_text(
            json.dumps(
                {
                    "enabled": True,
                    "provider": "custom",
                    "host": "smtp.admin-page.test",
                    "username": "admin@example.test",
                    "password": "PASSWORD_FROM_ADMIN_PAGE",
                    "from_email": "admin@example.test",
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(settings_mod, "EMAIL_SETTINGS_PATH", stored)

        mod = importlib.import_module("src.infrastructure.services.email_sender")
        sender = mod.SmtpEmailSender(AppConfig.get())
        effective = sender._settings()
        assert effective["password"] == "PASSWORD_FROM_ADMIN_PAGE"
        assert effective["host"] == "smtp.admin-page.test"


def _wrapped(inner: BaseException) -> EmailDeliveryError:
    """Как отправитель отдаёт ошибку наружу: общая фраза, причина — в __cause__."""
    try:
        try:
            raise inner
        except BaseException as e:  # noqa: BLE001
            raise EmailDeliveryError("Failed to send email. Please try again later.") from e
    except EmailDeliveryError as outer:
        return outer


class TestОшибкаСловамиАдмина:
    def test_gmail_хочет_пароль_приложения(self):
        err = smtplib.SMTPAuthenticationError(
            534,
            b"5.7.9 Application-specific password required. For more information, go to\n"
            b"5.7.9 https://support.google.com/mail/?p=InvalidSecondFactor - gsmtp",
        )
        text = settings_mod.explain_send_error(_wrapped(err))
        assert "ПАРОЛЬ ПРИЛОЖЕНИЯ" in text
        assert "5.7.9" in text, "исходный ответ сервера обязан остаться"

    def test_неверный_логин_или_пароль(self):
        err = smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")
        assert "Логин или пароль не подошли" in settings_mod.explain_send_error(_wrapped(err))

    def test_порт_закрыт_хостером(self):
        text = settings_mod.explain_send_error(_wrapped(TimeoutError("timed out")))
        assert "Brevo" in text

    def test_незнакомая_ошибка_отдаётся_как_есть(self):
        text = settings_mod.explain_send_error(_wrapped(RuntimeError("что-то новое")))
        assert text == "что-то новое"

    def test_причина_не_теряется_за_общей_фразой(self):
        # Главное: «Failed to send email» — не причина. Админ должен увидеть ответ сервера.
        text = settings_mod.explain_send_error(_wrapped(RuntimeError("сервер сказал нет")))
        assert "Failed to send email" not in text


class TestСоветЗнаетПровайдера:
    """Таймаут на Brevo не лечится советом «выберите Brevo» — админ уже там."""

    def test_уже_brevo_не_советуем_brevo(self):
        text = settings_mod.explain_send_error(_wrapped(TimeoutError("timed out")), "brevo")
        assert "Выберите провайдера Brevo" not in text
        assert "api.brevo.com" in text

    def test_smtp_советуем_brevo(self):
        text = settings_mod.explain_send_error(_wrapped(TimeoutError("timed out")), "gmail")
        assert "Выберите провайдера Brevo" in text


class TestПричинаИзНастоящейЦепочкиHttpx:
    """Таймаут Brevo объясняется словами — на ЖИВОЙ цепочке исключений httpx.

    Прежний тест строил упрощённую цепочку и проходил, а в жизни подсказки не было:
    httpx на таймауте выстраивает ConnectTimeout → httpcore.ConnectTimeout →
    TimeoutError → asyncio.CancelledError, и обход «до самого дна» доходил до отмены
    задачи. Она не таймаут и не ошибка связи, поэтому админ видел внутренний текст
    anyio без единого совета.
    """

    @staticmethod
    def _httpx_timeout_chain() -> BaseException:
        import asyncio

        import httpcore
        import httpx

        try:
            try:
                try:
                    try:
                        raise asyncio.CancelledError(
                            "Cancelled by cancel scope 7f3; reason: deadline exceeded"
                        )
                    except BaseException as cancelled:
                        raise TimeoutError from cancelled
                except BaseException as timeout:
                    raise httpcore.ConnectTimeout from timeout
            except BaseException as core:
                raise httpx.ConnectTimeout("timed out") from core
        except httpx.ConnectTimeout as outer:
            return outer

    def test_таймаут_brevo_объясняется(self):
        wrapped = _wrapped(self._httpx_timeout_chain())
        text = settings_mod.explain_send_error(wrapped, "brevo")
        assert "api.brevo.com" in text
        assert "Cancelled by cancel scope" not in text, "админу показали внутренности anyio"

    def test_таймаут_smtp_советует_brevo(self):
        text = settings_mod.explain_send_error(self._httpx_timeout_chain(), "gmail")
        assert "Выберите провайдера Brevo" in text


class TestПодсказкиНеПоСлучайнымЦифрам:
    def test_общий_код_5_7_9_не_выдаём_за_пароль_приложения(self):
        # RFC 4954: 5.7.9 — «механизм проверки слишком слабый», к паролю приложения
        # отношения не имеет. Раньше любой такой ответ получал гугловский совет.
        err = smtplib.SMTPAuthenticationError(534, b"5.7.9 Authentication mechanism is too weak")
        text = settings_mod.explain_send_error(_wrapped(err))
        assert "ПАРОЛЬ ПРИЛОЖЕНИЯ" not in text
        assert "5.7.9" in text

    def test_гугловский_отказ_по_прежнему_объясняется(self):
        err = smtplib.SMTPAuthenticationError(
            534, b"5.7.9 Application-specific password required. https://support.google.com/mail/?p=InvalidSecondFactor"
        )
        assert "ПАРОЛЬ ПРИЛОЖЕНИЯ" in settings_mod.explain_send_error(_wrapped(err))

    def test_цифры_535_в_тексте_не_значат_отказ_входа(self):
        # «535» бывает номером порта и идентификатором очереди в ответе почтовика.
        err = smtplib.SMTPDataError(451, b"4.3.0 queue 535 temporarily unavailable, try later")
        text = settings_mod.explain_send_error(_wrapped(err))
        assert "Логин или пароль не подошли" not in text

    def test_настоящий_535_объясняется(self):
        err = smtplib.SMTPAuthenticationError(535, b"5.7.8 Error: authentication failed")
        assert "Логин или пароль не подошли" in settings_mod.explain_send_error(_wrapped(err))
