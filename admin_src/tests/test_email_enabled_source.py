"""Почта, включённая В АДМИНКЕ, должна работать везде, а не только в письме с кодом.

ЧТО СЛУЧИЛОСЬ. Настройки почты живут в двух местах: `.env` — как значение по
умолчанию, и `assets/email.json` — то, что админ сохранил в «Настройках → Почта»
(оно главнее). Отправитель это знает. А три фоновых места спрашивали НАПРЯМУЮ
переменную окружения EMAIL_ENABLED — и у установки, где почту включили в админке,
вели себя так, будто почты нет:

  * рассылка по почте падала «Ошибкой» мгновенно (ноль писем, ни слова о причине);
  * напоминания «подписка заканчивается» молча не уходили;
  * письмо со скидкой на продление — тоже.

Четвёртое место ломалось иначе: `is_enabled` — СВОЙСТВО, а оповещение о новом
входе звало его как функцию (`sender.is_enabled()`), получало
«'bool' object is not callable», ошибка гасилась в `except`, и письмо о входе не
уходило НИКОГДА — ни при какой настройке.

ЧТО ЗАПЕРТО ЗДЕСЬ: правило «можно ли слать» одно на всех, никто не читает
EMAIL_ENABLED в обход него, и никто не зовёт свойство как функцию.
"""

import importlib
import re
from pathlib import Path

import pytest

settings_mod = importlib.import_module("src.infrastructure.services.email_settings")

# `src` — пакет без __init__.py, его __file__ пуст: берём корень от модуля настроек.
SRC = Path(settings_mod.__file__).parents[2]


def _settings(**over):
    base = {
        "enabled": True,
        "provider": "custom",
        "host": "smtp.example.test",
        "port": 587,
        "username": "u",
        "password": "p",
        "from_email": "noreply@example.test",
        "from_name": "X",
        "brevo_api_key": "",
    }
    base.update(over)
    return base


class TestПравилоОтправки:
    def test_полный_smtp_отправлять_можно(self):
        assert settings_mod.settings_allow_sending(_settings()) is True

    def test_выключено_админом_нельзя(self):
        assert settings_mod.settings_allow_sending(_settings(enabled=False)) is False

    def test_без_адреса_отправителя_нельзя(self):
        # Письмо без From не уйдёт ни через SMTP, ни через Brevo.
        assert settings_mod.settings_allow_sending(_settings(from_email="")) is False

    def test_brevo_хватает_ключа_и_адреса(self):
        s = _settings(provider="brevo", brevo_api_key="key", host="", username="", password="")
        assert settings_mod.settings_allow_sending(s) is True

    def test_brevo_выбран_но_ключа_нет_нельзя(self):
        s = _settings(provider="brevo", brevo_api_key="", host="", username="", password="")
        assert settings_mod.settings_allow_sending(s) is False

    def test_неполный_smtp_нельзя(self):
        assert settings_mod.settings_allow_sending(_settings(password="")) is False


class TestНиктоНеСпрашиваетОкружениеВОбход:
    """EMAIL_ENABLED имеет право читать только общий модуль настроек.

    Проверяем исходники целиком: именно так этот баг и появился — в отдельных
    задачах завели по своему маленькому `_email_enabled()`.
    """

    def test_ни_одна_задача_не_читает_email_enabled_сама(self):
        allowed = {
            SRC / "infrastructure" / "services" / "email_settings.py",
            # Исторический запасной путь: если настройки не прочитались, решаем по .env.
            SRC / "infrastructure" / "services" / "overlay_renewal_discount.py",
        }
        offenders = []
        for path in SRC.rglob("*.py"):
            if path in allowed or "__pycache__" in str(path):
                continue
            text = path.read_text(encoding="utf-8")
            if "EMAIL_ENABLED" in text:
                offenders.append(str(path.relative_to(SRC)))
        assert not offenders, (
            "эти файлы решают про почту в обход общих настроек "
            f"(почта из админки для них не существует): {offenders}"
        )


class TestСвойствоНеЗовутКакФункцию:
    """`is_enabled` — свойство. `sender.is_enabled()` роняет отправку на ровном месте.

    Ищем вызов БЕЗ аргументов: у настроек уведомлений базового образа есть свой
    честный метод `is_enabled(тип)`, и он к почте отношения не имеет.
    """

    def test_нигде_нет_вызова_is_enabled_как_функции(self):
        pattern = re.compile(r"\.is_enabled\s*\(\s*\)")
        offenders = []
        for path in SRC.rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            if pattern.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(SRC)))
        assert not offenders, f"свойство зовут как функцию: {offenders}"


class TestОтправительЗовётОбщееПравило:
    """Правда одна: у отправителя не своя копия условия, а вызов общего правила."""

    def test_патч_отправителя_использует_общее_правило(self):
        patch = SRC.parent / "overlay_patches" / "email_sender.py"
        if not patch.exists():  # pragma: no cover — в образе правки лежат рядом
            pytest.skip("overlay_patches рядом нет")
        text = patch.read_text(encoding="utf-8")
        assert "settings_allow_sending" in text
