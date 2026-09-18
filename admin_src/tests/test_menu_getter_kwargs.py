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


def _overlay_getters():
    from overlay_patches import menu_dialog  # в образе правки лежат пакетом overlay_patches

    for name, obj in vars(menu_dialog).items():
        if name.endswith("_getter_overlay") and callable(obj):
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


@pytest.mark.parametrize("name", ["devices_getter_overlay"])
def test_known_overlay_getters_are_still_there(name):
    """Сторож бесполезен, если обёртку переименуют и он перестанет её видеть."""
    assert name in dict(_overlay_getters())
