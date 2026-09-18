"""Обёртки геттеров окон бота не должны спорить именами с aiogram_dialog.

ЧТО СЛУЧИЛОСЬ 18.09. Обёртка окна «Устройства» просила у dishka `config` и `session`
одноимёнными параметрами. Но aiogram_dialog зовёт геттер окна со СВОИМИ kwargs, и
`config` там уже есть: вызов падал с «got multiple values for keyword argument
'config'», и окно «Устройства» не открывалось НИ У КОГО — а открывают его как раз
тогда, когда что-то не подключается.

Почему это не поймали обычные тесты: сам модуль импортируется и правка применяется,
ошибка живёт только в момент живого вызова из диалога. Поэтому сторож проверяет
СИГНАТУРЫ: имя параметра под FromDishka не должно совпадать с тем, что кладёт в
kwargs сам диалог.
"""

import inspect

import pytest

# Что aiogram_dialog (и наши middleware) кладут в kwargs геттера окна. `config`
# подтверждён боевым падением; остальные — обычный набор окна.
DIALOG_KWARGS = {"dialog_manager", "config", "user", "event_from_user", "i18n", "middleware_data"}


def _menu_dialog():
    """Модуль правки через ЕЁ ЦЕЛЬ: прямой импорт даёт кольцо и «правка не применилась»."""
    import importlib
    import sys

    importlib.import_module("src.telegram.routers.menu.dialog")
    return sys.modules["overlay_patches.menu_dialog"]


def _overlay_getters():  # noqa: C901
    """ВСЕ геттеры, которые реально привязаны к окнам через `getter=`.

    Раньше сторож смотрел только имена `*_getter_overlay` — и не видел `menu_getter`,
    обёртку ГЛАВНОГО окна бота. То есть ровно то окно, которое открывают чаще всех,
    оставалось без проверки. Берём объекты из самих окон: как бы функцию ни назвали,
    привязка к окну её выдаст.

    Отдельно возвращаем и то, что объявлено у нас по имени: окно может собираться
    не из модульной переменной, и терять такую обёртку тоже нельзя.
    """
    menu_dialog = _menu_dialog()
    seen: set[int] = set()
    for value in vars(menu_dialog).values():
        if type(value).__name__ != "Window":
            continue
        # aiogram_dialog оборачивает геттер окна (PreviewAwareGetter / StaticGetter),
        # поэтому идём и по самому объекту, и по его атрибутам: как бы обёртка ни
        # называлась, функция лежит в одном из них.
        candidates = [getattr(value, "getter", None)]
        candidates.extend(vars(candidates[0] or object()).values())
        for candidate in candidates:
            func = getattr(candidate, "__func__", candidate)
            if not callable(func) or id(func) in seen:
                continue
            if getattr(func, "__module__", "") != menu_dialog.__name__:
                # Чужие геттеры базы (devices_getter, invite_getter) не наши — их
                # сигнатуру задаёт апстрим, и спорить с ним здесь не о чем.
                continue
            seen.add(id(func))
            yield getattr(func, "__name__", "?"), func
    for name, obj in vars(menu_dialog).items():
        if name.endswith("_getter_overlay") and callable(obj) and id(obj) not in seen:
            seen.add(id(obj))
            yield name, obj


def _injected_names(func):
    """Имена параметров, которые подставляет dishka.

    Опознаём по МЕТКЕ в аннотации, а не по её тексту: `FromDishka[X]` — это
    `Annotated[X, FromComponent(...)]`, и подстроки «FromDishka» в ней нет. Первый
    вариант этого сторожа искал именно подстроку и мутацию не поймал.
    """
    import typing

    target = getattr(func, "__dishka_orig_func__", func)
    try:
        hints = typing.get_type_hints(target, include_extras=True)
    except Exception:  # noqa: BLE001 — не разрешились имена: лучше проверить грубо, чем молча пропустить
        hints = {}
    names = []
    for param in inspect.signature(target).parameters.values():
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            continue
        hint = hints.get(param.name, param.annotation)
        # Класс метки называется `_FromComponent` (с подчёркиванием) — сравнение по
        # точному имени «FromComponent» мутацию не ловило.
        marked = any(
            "FromComponent" in type(meta).__name__ for meta in typing.get_args(hint)[1:]
        )
        if marked:
            names.append(param.name)
    return names


def test_overlay_getters_do_not_shadow_dialog_kwargs():
    found = list(_overlay_getters())
    assert found, "обёрток геттеров не найдено — сторож потерял цель"
    for name, func in found:
        clash = set(_injected_names(func)) & DIALOG_KWARGS
        assert not clash, (
            f"{name}: параметры {sorted(clash)} придёт и от dishka, и от aiogram_dialog — "
            f"окно упадёт «got multiple values for keyword argument». Переименуйте их."
        )


@pytest.mark.parametrize("name", ["devices_getter_overlay", "menu_getter"])
def test_known_overlay_getters_are_still_there(name):
    """Сторож бесполезен, если обёртку переименуют и он перестанет её видеть.

    `menu_getter` — обёртка ГЛАВНОГО окна: до расширения сторожа она в его поле
    зрения не попадала вовсе, хотя тоже просит у dishka сессию и панель.
    """
    assert name in dict(_overlay_getters())


def test_guard_sees_more_than_one_getter():
    """Сузился отбор — сторож молча перестал бы что-то проверять."""
    assert len(dict(_overlay_getters())) >= 2
