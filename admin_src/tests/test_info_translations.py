"""«Информация» на 12 языках: перевод поверх русского, фолбэк по полям.

ЗАЧЕМ. Кабинет говорит на 12 языках, а FAQ/Правила/Оферта — единственный
пользовательский текст, который иностранец читал по-русски. Это не строки
интерфейса, а контент владельца, поэтому переводы живут в том же файле рядом с
русским. Здесь заперто главное: перевод накрывает русский ПОЛЕ ЗА ПОЛЕМ (не
перевели «Оферту» — показываем русскую, а не пустоту), русская правка переводы не
стирает, а пустое поле снимает перевод обратно на русский.
"""

import json
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture()
def info(tmp_path, monkeypatch):
    """Модуль контента с assets в временном каталоге."""
    # appearance тянет конфигурацию приложения — подменяем на заглушку.
    fake = types.ModuleType("src.web.endpoints.public.appearance")
    fake.ASSETS_DIR = tmp_path  # type: ignore[attr-defined]
    fake.resolve_brand_name = lambda: "Бренд"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src.web.endpoints.public.appearance", fake)
    for name in list(sys.modules):
        if name.endswith("public.info_content"):
            del sys.modules[name]
    import importlib

    module = importlib.import_module("src.web.endpoints.public.info_content")
    module.INFO_PATH = tmp_path / "info_content.json"
    return module


def write(module, data: dict) -> None:
    Path(module.INFO_PATH).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_без_языка_всё_как_раньше(info):
    write(info, {"rules": "Русские правила"})
    assert info.effective_content()["rules"] == "Русские правила"
    assert info.effective_content("ru")["rules"] == "Русские правила"


def test_перевод_накрывает_русский(info):
    write(info, {"rules": "Русские правила", "i18n": {"en": {"rules": "English rules"}}})
    assert info.effective_content("en")["rules"] == "English rules"


def test_непереведённый_раздел_остаётся_русским(info):
    write(
        info,
        {
            "rules": "Русские правила",
            "offer": "Русская оферта",
            "i18n": {"en": {"rules": "English rules"}},
        },
    )
    content = info.effective_content("en")
    assert content["rules"] == "English rules"
    assert content["offer"] == "Русская оферта"  # фолбэк, а не пустота


def test_пустой_перевод_не_прячет_русский(info):
    write(info, {"rules": "Русские правила", "i18n": {"en": {"rules": "   "}}})
    assert info.effective_content("en")["rules"] == "Русские правила"


def test_faq_переводится_целиком_или_никак(info):
    write(
        info,
        {
            "faq": [{"q": "Вопрос", "a": "Ответ"}],
            "i18n": {"en": {"faq": [{"q": "Question", "a": "Answer"}]}},
        },
    )
    assert info.effective_content("en")["faq"] == [{"q": "Question", "a": "Answer"}]
    # Пустой список переводом не считается — иначе FAQ исчез бы вовсе.
    write(info, {"faq": [{"q": "Вопрос", "a": "Ответ"}], "i18n": {"en": {"faq": []}}})
    assert info.effective_content("en")["faq"] == [{"q": "Вопрос", "a": "Ответ"}]


def test_неизвестный_язык_отдаёт_русский(info):
    write(info, {"rules": "Русские правила", "i18n": {"en": {"rules": "English rules"}}})
    assert info.effective_content("fr")["rules"] == "Русские правила"
    assert info.effective_content("англ")["rules"] == "Русские правила"
    assert info.effective_content("")["rules"] == "Русские правила"


def test_код_языка_нормализуется(info):
    assert info.normalize_lang("EN") == "en"
    assert info.normalize_lang(" tr ") == "tr"
    assert info.normalize_lang("eng") is None
    assert info.normalize_lang(None) is None


def test_список_переведённых_языков(info):
    write(
        info,
        {
            "rules": "Русские правила",
            "i18n": {
                "en": {"rules": "English rules"},
                "tr": {"offer": "   "},  # пусто — язык не считается переведённым
                "ru": {"rules": "дубль"},  # русский в ветке переводов игнорируем
            },
        },
    )
    assert info.translated_langs() == ["en"]


def test_сохранённое_для_языка_без_фолбэка(info):
    write(info, {"rules": "Русские правила", "i18n": {"en": {"rules": "English rules"}}})
    assert info.stored_for_lang("en") == {"rules": "English rules"}
    assert info.stored_for_lang("tr") == {}
    # Для русского — корень файла без ветки переводов.
    assert "i18n" not in info.stored_for_lang("ru")
