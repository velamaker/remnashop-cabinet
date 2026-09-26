"""Сторож: зависимость dishka не называется так же, как ключ данных апдейта aiogram.

ЧТО СЛУЧИЛОСЬ. Дважды одна и та же ошибка уронила кнопку у ВСЕХ:
  * 18.09 — окно «Устройства»: геттер просил у dishka `config` (tests/test_menu_getter_kwargs.py);
  * 25.09 — кнопки «сигналов до ухода» ✅/❌/🔕: обработчик просил `config` (tests/test_churn_signals_dispatch.py).

МЕХАНИКА. Функция принимает `**data` (или `**kwargs`) и параметр `x: FromDishka[...]`.
aiogram отдаёт такому обработчику ВСЕ данные апдейта, aiogram_dialog — геттеру окна
все `middleware_data`, а обёртка `@inject` зовёт функцию с `**kwargs, **solved`. Если
имя зависимости совпало с ключом данных, аргумент приходит дважды — TypeError на
каждом вызове. Ключ `config` там всегда: диспетчер базы создан как
`Dispatcher(storage=…, config=config)`.

Без `**kwargs` этой беды нет: aiogram отдаёт обработчику только те ключи, что есть
в его сигнатуре, а `@inject` зависимости из сигнатуры убирает. Поэтому сторож смотрит
именно функции с `**kwargs`.

ПОЧЕМУ СТАТИЧЕСКИ И ПО ВСЕМУ admin_src. Ошибка живёт только в момент живого апдейта:
модуль импортируется, правки применяются, юнит-тест функции проходит. Ловит её либо
прогон через настоящий диспетчер (он есть не у каждого обработчика), либо взгляд на
сигнатуры — это дёшево и покрывает всё сразу, включая функции, объявленные внутри
правок overlay_patches.

ОТКУДА СПИСОК КЛЮЧЕЙ. Не только из памяти: к базовому списку добавляются ключи
workflow_data, которые база передаёт в Dispatcher (читаем её dispatcher.py), ключи,
которые пишут её мидлвари (читаем src/telegram/middlewares), и всё, что реально
кладут в data живые aiogram + aiogram_dialog + dishka (прогоняем апдейт через
настоящий диспетчер). Обновится база или aiogram — список обновится сам.
"""

import ast
import asyncio
import functools
from pathlib import Path
from typing import Iterator, NamedTuple

import pytest

from _aiogram_harness import (  # noqa: E402 — соседний модуль тестов
    observed_data_keys,
    vendor_middleware_keys,
    vendor_workflow_keys,
)

# Что заведомо лежит в data апдейта. Часть ключей появляется не в каждом апдейте
# (event_thread_id — только в темах, bots/dispatcher — только при polling), поэтому
# живой прогон их может не показать, а сторож знать обязан.
KNOWN_KEYS = {
    # aiogram
    "bot", "bots", "dispatcher", "event_update", "event_router", "event_from_user",
    "event_chat", "event_thread_id", "event_business_connection_id", "event_context",
    "fsm_storage", "state", "raw_state", "handler",
    # aiogram_dialog
    "dialog_manager", "dialog_bg_factory", "aiogd_context", "aiogd_event_context",
    "aiogd_original_callback_data", "aiogd_stack", "aiogd_storage_proxy",
    # dishka
    "dishka_container",
    # база: workflow_data диспетчера и UserMiddleware; middleware_data — её ключ
    # для данных мидлварей в окнах
    "config", "user", "middleware_data",
}

MARKERS = {"FromDishka", "FromComponent"}


def _source_root() -> Path | None:
    """Наш код отдельно от вендорного: рядом с тестами или смонтированный в /tmp/admin_src."""
    here = Path(__file__).resolve().parents[1]
    for candidate in (here, Path("/tmp/admin_src")):
        if (candidate / "src").is_dir() and (candidate / "overlay_patches").is_dir():
            return candidate
    return None


ROOT = _source_root()


@functools.lru_cache(maxsize=1)
def reserved_keys() -> frozenset[str]:
    observed = asyncio.run(observed_data_keys())
    return frozenset(KNOWN_KEYS | vendor_workflow_keys() | vendor_middleware_keys() | observed)


class AtRisk(NamedTuple):
    where: str
    name: str
    injected: tuple[str, ...]


def _marker_names(tree: ast.Module) -> set[str]:
    """Как в модуле зовут метки dishka — с учётом `from dishka import FromDishka as FD`."""
    names = set(MARKERS)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("dishka"):
            names.update(a.asname for a in node.names if a.name in MARKERS and a.asname)
    return names


def _is_injected(annotation: ast.AST | None, markers: set[str]) -> bool:
    if annotation is None:
        return False
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        # Аннотация строкой («FromDishka[X]») — разбираем как выражение.
        try:
            annotation = ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            return False
    for node in ast.walk(annotation):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _is_injected(node, markers):
                return True
        name = getattr(node, "id", None) if isinstance(node, ast.Name) else (
            node.attr if isinstance(node, ast.Attribute) else None
        )
        if name in markers:
            return True
    return False


def scan_source(text: str, where: str) -> Iterator[AtRisk]:
    """Функции с `**kwargs` и хотя бы одной зависимостью dishka — вложенные тоже."""
    tree = ast.parse(text)
    markers = _marker_names(tree)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = node.args
        if args.kwarg is None:
            continue
        injected = tuple(
            a.arg
            for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)
            if _is_injected(a.annotation, markers)
        )
        if injected:
            yield AtRisk(f"{where}:{node.lineno}", node.name, injected)


@functools.lru_cache(maxsize=1)
def scan_admin_src() -> tuple[AtRisk, ...]:
    found: list[AtRisk] = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if "__pycache__" in rel.parts or rel.parts[:1] == ("tests",):
            continue
        # Нечитаемый файл не пропускаем: сторож, который молча не смотрит файл, хуже,
        # чем упавший сторож.
        found.extend(scan_source(path.read_text(encoding="utf-8"), str(rel)))
    return tuple(found)


needs_source = pytest.mark.skipif(
    ROOT is None, reason="исходник admin_src не смонтирован (в CI: -v admin_src:/tmp/admin_src)"
)


@needs_source
def test_no_injected_name_shadows_update_data():
    reserved = reserved_keys()
    clashes = [
        f"{item.where} {item.name}(): {sorted(set(item.injected) & reserved)}"
        for item in scan_admin_src()
        if set(item.injected) & reserved
    ]
    assert not clashes, (
        "эти зависимости dishka придут дважды — и от dishka, и из данных апдейта "
        "(**kwargs), каждый вызов упадёт «got multiple values for keyword argument». "
        "Переименуйте параметр (db_session, app_config, _extra_…): " + "; ".join(clashes)
    )


@needs_source
@pytest.mark.parametrize(
    "name",
    [
        "on_signal_button",       # сигналы до ухода — случай 25.09
        "on_optout",              # «не напоминать» об оплате
        "on_grant",               # спорный платёж
        "cmd_gift",               # подарки
        "menu_getter",            # главное окно бота (правка)
        "devices_getter_overlay", # окно «Устройства» — случай 18.09
        "confirm_getter",         # перенос остатка (правка), объявлен в overlay_patches
    ],
)
def test_guard_still_sees_known_handlers(name):
    """Сузился отбор — сторож молча перестал бы что-то проверять."""
    assert name in {item.name for item in scan_admin_src()}


def test_reserved_keys_come_from_the_real_stack():
    """Динамические источники живы: иначе сторож тихо свёлся бы к списку из памяти."""
    assert "config" in vendor_workflow_keys()
    assert "user" in vendor_middleware_keys()
    observed = asyncio.run(observed_data_keys())
    assert {"config", "user", "bot", "dialog_manager", "dishka_container", "event_from_user"} <= observed
    assert observed <= reserved_keys()


BUG_25_09 = '''
from dishka import FromDishka
async def on_signal_button(callback, session: FromDishka[AsyncSession],
                           config: FromDishka[AppConfig], **data): ...
'''

VARIANTS = {
    "строковая аннотация": '''
async def h(cb, config: "FromDishka[AppConfig]", **data): ...
''',
    "псевдоним импорта": '''
from dishka import FromDishka as FD
async def h(cb, bot: FD[Bot], **kw): ...
''',
    "Annotated с FromComponent": '''
from typing import Annotated
from dishka import FromComponent
async def h(cb, state: Annotated[Storage, FromComponent("x")], **kw): ...
''',
    "вложенная функция правки": '''
def apply():
    @inject
    async def getter(user: FromDishka[UserDao], **kwargs): ...
''',
    "keyword-only параметр": '''
async def h(cb, *, handler: FromDishka[Handler], **data): ...
''',
}


def test_scanner_catches_the_25_09_shape():
    items = list(scan_source(BUG_25_09, "x.py"))
    assert [(i.name, set(i.injected)) for i in items] == [("on_signal_button", {"session", "config"})]


@pytest.mark.parametrize("label", list(VARIANTS))
def test_scanner_catches_variants(label):
    items = list(scan_source(VARIANTS[label], "x.py"))
    assert items and set(items[0].injected) & KNOWN_KEYS, label


def test_scanner_ignores_what_cannot_clash():
    """Без **kwargs aiogram отдаёт только ключи сигнатуры — одноимённость не страшна;
    обычный (не dishka) параметр `config` тоже честно берётся из данных."""
    source = '''
from dishka import FromDishka
async def a(cb, config: FromDishka[AppConfig]): ...
async def b(cb, config: AppConfig, **data): ...
'''
    assert list(scan_source(source, "x.py")) == []
