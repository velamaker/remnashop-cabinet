"""Правки поведения бота, которые НЕ трогают его исходники.

ЗАЧЕМ ЭТОТ ПАКЕТ
----------------
Overlay кладётся поверх базового образа строкой `COPY admin_src/src/ …/src/`.
Файл, путь которого совпал с базовым, не дополняет его, а ЗАМЕЩАЕТ целиком —
и в день нового релиза базы такая копия молча откатывает всё, что авторы бота
поправили в этом файле. Ради правки в одну строку держать копию чужого файла на
сотню строк — плохая сделка: рискуем сотней строк, чтобы изменить одну.

Здесь та же правка делается в рантайме: находим объект бота и меняем ровно то,
что нужно. Исходник остаётся нетронутым, новый релиз базы приезжает целиком.

ПОЧЕМУ ЭТО НЕ «ХУЖЕ, ЧЕМ КОПИЯ»
-------------------------------
У копии и у правки одна и та же уязвимость — апстрим меняет то место, за которое
мы держимся. Разница в том, КАК мы об этом узнаём. Копия узнать не даёт вовсе:
она просто перестаёт содержать чужие изменения. Правка обязана сначала УЗНАТЬ
цель и сверить, что она той формы, которую мы ожидали, — не совпало, значит
кричим. Поэтому каждая правка ниже проверяет исходное значение перед заменой.

ГДЕ ЗАПУСКАЕТСЯ
---------------
Процессов четыре и точки входа у них разные: бот и веб идут через
`src.overlay_app`, а taskiq-воркер и планировщик импортируют модули базы напрямую
и про overlay не знают вовсе. Общего места в коде приложения нет, поэтому
подключаемся уровнем ниже — `sitecustomize.py` в site-packages venv, который
интерпретатор импортирует сам, до любого кода приложения (см. scripts/sitecustomize.py).

Интерпретатор мы НЕ роняем даже при неудаче: этим же питоном работают alembic,
taskiq и pip, и падение на старте превратило бы понятную проблему в необъяснимую.
Вместо этого пишем в stderr так, чтобы нельзя было не заметить, и оставляем след
в `failures()` — по нему проверка перед деплоем (`check-update.sh`) валит прогон.
"""

from __future__ import annotations

import hashlib
import pathlib
import sys
import textwrap
from typing import Any, Callable

# Что не применилось: (имя правки, причина). Пусто — всё в порядке.
_failures: list[tuple[str, str]] = []
_applied: list[str] = []
_done = False


class PatchTargetChanged(RuntimeError):
    """Цель правки выглядит не так, как мы ожидали, — апстрим её изменил."""


def failures() -> list[tuple[str, str]]:
    return list(_failures)


def applied() -> list[str]:
    return list(_applied)


def expect_source(module: Any, qualname: str, sha256_hex: str, what: str) -> None:
    """Убедиться, что код бота, который мы собираемся заменить, тот самый.

    Замена метода повторяет и те строки, которых мы не меняли, — ровно как копия
    файла, только объёмом в один метод. Значит остаётся та же опасность: апстрим
    поправит что-то ВНУТРИ, а наша версия это затрёт. Отличие в том, что здесь мы
    это ловим — и отказываемся подменять молча.

    Читаем ИСХОДНЫЙ ФАЙЛ модуля и достаём функцию разбором, а не `inspect` по
    объекту. Причина конкретная: обработчики бота обёрнуты декоратором `@inject`
    из dishka, который подменяет функцию своей и НЕ проставляет `__wrapped__`, —
    `inspect.getsource` в таком случае показывает код самой dishka, а не бота,
    и сверять было бы нечего.

    Декораторы включаем в текст: они часть контракта (сменится `@inject` на что-то
    другое — нам это тоже важно знать). Отступы снимаем, чтобы метод внутри класса
    сверялся по содержимому, а не по месту в файле.
    """
    import ast

    path = getattr(module, "__file__", None)
    if not path:
        raise PatchTargetChanged(f"{what}: у модуля нет файла, сверить исходник нечем")

    try:
        source = pathlib.Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise PatchTargetChanged(f"{what}: не удалось прочитать {path} ({exc})") from exc

    lines = source.splitlines(keepends=True)
    want = qualname.split(".")
    found: str | None = None

    def walk(nodes: list, prefix: list[str]) -> None:
        nonlocal found
        for node in nodes:
            if isinstance(node, ast.ClassDef):
                walk(node.body, prefix + [node.name])
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if prefix + [node.name] == want:
                    first = min([d.lineno for d in node.decorator_list] + [node.lineno])
                    found = textwrap.dedent("".join(lines[first - 1 : node.end_lineno]))

    walk(ast.parse(source).body, [])

    if found is None:
        raise PatchTargetChanged(
            f"{what}: в {path} больше нет {qualname} — база перестроила этот код"
        )

    actual = hashlib.sha256(found.encode()).hexdigest()
    if actual != sha256_hex:
        raise PatchTargetChanged(
            f"{what}: апстрим ИЗМЕНИЛ этот код. Ожидался {sha256_hex[:12]}…, "
            f"сейчас {actual[:12]}…. Наша версия затёрла бы их правки — сверьте "
            f"изменения, перенесите в нашу и обновите хэш на {actual}"
        )


def _run(name: str, fn: Callable[[], str]) -> None:
    try:
        detail = fn()
    except Exception as exc:  # noqa: BLE001 — любая причина одинаково важна
        _failures.append((name, f"{type(exc).__name__}: {exc}"))
        print(
            f"\n!!! OVERLAY: правка «{name}» НЕ ПРИМЕНИЛАСЬ: {type(exc).__name__}: {exc}\n"
            f"!!! Бот работает на исходном поведении базы. Разберитесь до деплоя.\n",
            file=sys.stderr,
            flush=True,
        )
    else:
        _applied.append(f"{name}: {detail}")


def install() -> None:
    """Подписать все правки на импорт их модулей. Повторный вызов ничего не делает.

    Сами правки здесь НЕ выполняются: каждая ждёт, пока бот импортирует её модуль
    (см. _hooks.py). Поэтому список ниже — это не «что сделано», а «что случится,
    когда дойдёт дело»; фактически применённое смотрите в applied().
    """
    global _done
    if _done:
        return
    _done = True

    from importlib import import_module

    from ._hooks import on_import

    # (что правим, модуль бота, наш модуль с правкой)
    #
    # Свои модули тоже импортируем ЛЕНИВО, уже внутри хука: половина из них тянет
    # конфигурацию приложения на уровне импорта, а мы находимся на старте
    # интерпретатора, где её может не быть вовсе (сборка образа, alembic).
    plan = (
        ("потолок версии панели", "src.core.constants", "panel_version"),
        ("свои разделы бота", "src.telegram.dispatcher", "bot_routers"),
        ("SDK панели по её версии", "src.infrastructure.di.providers", "remnawave_sdk"),
        (
            "подарок не сжигает дни",
            "src.application.use_cases.promocode.commands.activate",
            "promocode_gift_days",
        ),
        (
            "баллы рефералки и кэшбэк",
            "src.application.use_cases.referral.commands.rewards",
            "referral_rewards",
        ),
        (
            "второе подтверждение подарка",
            "src.telegram.routers.subscription.promocode_handlers",
            "promocode_gift_confirm",
        ),
    )

    for name, target, patch_module in plan:
        on_import(
            target,
            lambda n=name, m=patch_module: _run(
                n, lambda: import_module(f".{m}", __package__).apply()
            ),
        )


def pending() -> list[str]:
    """Модули, чьи правки ещё не сработали (их пока не импортировали)."""
    from ._hooks import pending as _pending

    return _pending()
