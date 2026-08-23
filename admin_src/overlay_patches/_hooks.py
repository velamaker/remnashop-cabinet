"""Применение правки в момент, когда бот импортирует нужный ему модуль.

ЗАЧЕМ ОТКЛАДЫВАТЬ. Правки подключаются из `sitecustomize`, то есть на старте
интерпретатора. Если применять их сразу, каждая обязана тут же импортировать свой
модуль бота — а импорт половины из них тянет за собой конфигурацию приложения.
Тем же интерпретатором работают `pip` при сборке образа и `alembic` при миграциях,
где переменных окружения нет вовсе: правки начали бы падать и заваливать вывод
ошибками там, где приложения нет и не должно быть.

КАК РАБОТАЕТ. Ставим в `sys.meta_path` искатель, который ничего не ищет сам:
он лишь перехватывает загрузку тех модулей, на которые кто-то подписался, и
вызывает подписку СРАЗУ ПОСЛЕ того, как модуль выполнился. Модуль не импортируют —
правка и не срабатывает; импортируют — срабатывает ровно вовремя.

ПОЧЕМУ «СРАЗУ ПОСЛЕ» ВАЖНО. Часть правок обязана успеть до того, как значение
разойдётся по чужим модулям: `src/lifespan.py` берёт потолок версии панели по
имени в момент СВОЕГО импорта, и правка, опоздавшая на один импорт, уже ни на что
не влияет. Хук же выполняется внутри загрузки модуля-цели, раньше любого, кто
станет из него что-то читать.

Модуль, уже загруженный к моменту подписки, обрабатываем сразу — иначе правка
молча не применилась бы.
"""

from __future__ import annotations

import importlib.util
import sys
from importlib.abc import MetaPathFinder
from typing import Callable

# module_name → что сделать после его загрузки
_subscribers: dict[str, list[Callable[[], None]]] = {}
# защита от рекурсии: find_spec ниже сам зовёт importlib.util.find_spec
_resolving: set[str] = set()
_installed = False


class _PatchOnImport(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):  # noqa: ANN001, ANN201
        callbacks = _subscribers.get(fullname)
        if not callbacks or fullname in _resolving:
            return None

        _resolving.add(fullname)
        try:
            spec = importlib.util.find_spec(fullname)
        except (ImportError, AttributeError, ValueError):
            return None
        finally:
            _resolving.discard(fullname)

        if spec is None or spec.loader is None:
            return None

        loader = spec.loader
        original_exec = loader.exec_module

        def exec_module(module):  # noqa: ANN001, ANN202
            original_exec(module)
            for callback in _subscribers.pop(fullname, []):
                callback()

        loader.exec_module = exec_module  # type: ignore[method-assign]
        return spec


def on_import(module_name: str, callback: Callable[[], None]) -> None:
    """Выполнить `callback` сразу после загрузки модуля (или сейчас, если он уже есть)."""
    global _installed

    if module_name in sys.modules:
        callback()
        return

    _subscribers.setdefault(module_name, []).append(callback)

    if not _installed:
        # Первым в очереди: иначе штатные искатели вернут спецификацию раньше нас
        # и перехватывать будет уже нечего.
        sys.meta_path.insert(0, _PatchOnImport())
        _installed = True


def pending() -> list[str]:
    """Модули, чьи правки ещё не сработали, — их просто не импортировали."""
    return sorted(_subscribers)
