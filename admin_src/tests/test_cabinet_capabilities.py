"""Список возможностей бота для кабинета (`bot_capabilities` в публичном оформлении).

ЗАЧЕМ ЗАПИРАЕМ. Кабинет бывает новее бота («только кабинет» в update.sh, кабинет на
отдельном сервере) и прячет функции, токенов которых бот не прислал. Тихие поломки
здесь такие:
  * формат строк поменяли — update.sh, который читает файл из образа sed'ом, молча
    перестаёт видеть токены и пишет владельцу, что бот «не умеет» того, что умеет;
  * в публичное поле попал токен «только бот» — это отпечаток версии бота наружу;
  * ручка начала слать `features` — в кабинете это значит «чужой бэкенд решает сам»,
    и проверка возможностей НАШЕГО бота выключилась бы целиком;
  * ручка перестала ставить поле — кабинет у всех спрячет массовые дни и скидку.

Запуск — внутри образа бота, как остальные тесты рядом (см. ci.yml).
"""

import ast
import importlib
import inspect
import re
from pathlib import Path

import pytest

caps = importlib.import_module("src.web.cabinet_capabilities")

TOKEN = re.compile(r"^[a-z][a-z0-9_]*$")
# Ровно те выражения, которыми update.sh разбирает файл (см. bot_image_caps в update.sh).
SED_CABINET = re.compile(r'^    "([a-z0-9_]+)",$', re.M)
SED_BOT_ONLY = re.compile(r'^    "([a-z0-9_]+)": "([^"]*)",$', re.M)


def _raw() -> str:
    return Path(caps.__file__).read_text(encoding="utf-8")


def test_tokens_are_well_formed_and_unique():
    cabinet = list(caps.CABINET_CAPABILITIES)
    bot_only = list(caps.BOT_ONLY_CHANGES)
    assert cabinet, "пустой список спрятал бы в кабинете всё из манифеста"
    for token in cabinet + bot_only:
        assert TOKEN.match(token), token
    assert len(set(cabinet)) == len(cabinet)
    # Один токен в двух списках: update.sh назвал бы его и «скрыто в кабинете», и
    # «не установлено в боте», а кабинет увидел бы непубличный токен.
    assert not set(cabinet) & set(bot_only)
    for label in caps.BOT_ONLY_CHANGES.values():
        assert label.strip() and '"' not in label and "\n" not in label


def test_update_sh_parser_sees_every_cabinet_token():
    found = SED_CABINET.findall(_raw())
    assert found == list(caps.CABINET_CAPABILITIES)


def test_update_sh_parser_sees_every_bot_only_change():
    found = SED_BOT_ONLY.findall(_raw())
    assert dict(found) == caps.BOT_ONLY_CHANGES
    assert len(found) == len(caps.BOT_ONLY_CHANGES)


def test_public_list_is_cabinet_only():
    public = caps.public_capabilities()
    assert public == list(caps.CABINET_CAPABILITIES)
    assert not set(public) & set(caps.BOT_ONLY_CHANGES)
    # Копия, а не сам кортеж: ручка не должна уметь испортить модульный список.
    public.append("x")
    assert "x" not in caps.CABINET_CAPABILITIES


@pytest.fixture
def appearance_mod():
    importlib.import_module("src.web.endpoints.public")
    return importlib.import_module("src.web.endpoints.public.appearance")


def _handler_source(mod) -> str:
    """Текст самой ручки. Обёртка dishka (`@inject`) подменяет функцию и не хранит
    `__wrapped__`, поэтому inspect.getsource отдал бы код обёртки — берём из файла."""
    text = Path(mod.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_appearance":
            return ast.get_source_segment(text, node) or ""
    raise AssertionError("ручка get_appearance не найдена в модуле")


def _original_handler(fn):
    """Исходная функция из замыкания обёртки dishka (None, если устройство сменилось)."""
    for cell in getattr(fn, "__closure__", None) or ():
        value = cell.cell_contents
        if inspect.iscoroutinefunction(value) and value.__name__ == "get_appearance":
            return value
    return None


def test_appearance_sets_field_and_never_features(appearance_mod):
    src = _handler_source(appearance_mod)
    assert 'data["bot_capabilities"] = public_capabilities()' in src
    assert '"features"' not in src and "'features'" not in src
    assert "BOT_ONLY_CHANGES" not in src
    # Номер версии наружу не отдаём (по нему подбирают известные дыры).
    assert "VERSION" not in src


async def test_appearance_response_carries_capabilities(appearance_mod, monkeypatch, tmp_path):
    """Сквозь саму ручку: поле есть, `features` нет, бренд на месте."""
    handler = _original_handler(appearance_mod.get_appearance)
    assert handler is not None, "обёртка dishka изменилась — поправьте _original_handler"
    monkeypatch.setattr(appearance_mod, "BRANDING_PATH", tmp_path / "branding.json")
    # Без сети: имя бренда иначе пошло бы в Telegram getMe.
    monkeypatch.setattr(appearance_mod, "resolve_brand_name", lambda: "Brand")

    class _NoSettings:
        async def get(self):
            # maintenance_follow_bot по умолчанию выключен — до настроек бота не доходим.
            raise AssertionError("не должно вызываться")

    data = await handler(settings_dao=_NoSettings())
    assert data["bot_capabilities"] == list(caps.CABINET_CAPABILITIES)
    assert "features" not in data
    assert data["brand_name"] == "Brand"
