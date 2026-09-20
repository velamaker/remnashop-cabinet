"""Сторож класса ошибок «потерянные глобали» — теперь и для обычных модулей.

ЗАЧЕМ. У overlay-правок такой сторож уже есть (`expect_names_resolve`): функция,
объявленная в нашем модуле, ищет имена ТОЖЕ в нашем модуле, и перенос тела без
шапки даёт NameError под живым человеком. Выпуск 1.5.0 наступил на те же грабли
ДВАЖДЫ в обычных модулях, которые overlay не проверяет:

  * `renew_current_from_balance` после выноса расчёта продолжал читать `pricing` и
    `matched` — каждое автосписание снимало деньги, падало на NameError и
    возвращало их, так и не продлив подписку;
  * `_drill_check` в мониторинге бэкапов звал `json`, которого в модуле не было, —
    учение по бэкапу навсегда стало бы «итог нечитаем».

Ни один из случаев не ловится ни `py_compile`, ни юнит-тестом, который выполняет
СРЕЗ исходника со своим словарём globals (там недостающее имя подсовывает сам
тест). Ловит только взгляд на байткод настоящего импортированного модуля.

Проверяем модули, где цена ошибки — деньги или молчащая сигнализация.
"""

import builtins
import dis
import importlib
import types

import pytest

MODULES = [
    "src.infrastructure.services.overlay_balance",
    "src.infrastructure.services.overlay_payment_reminder",
    "src.infrastructure.services.overlay_extra_device",
    "src.infrastructure.services.overlay_extra_traffic",
    "src.infrastructure.taskiq.tasks.autopay",
    "src.infrastructure.taskiq.tasks.autopay_warning",
    "src.infrastructure.taskiq.tasks.backup_monitor",
    "src.infrastructure.taskiq.tasks.payment_reminder",
    "src.infrastructure.taskiq.tasks.extra_devices",
    "src.infrastructure.taskiq.tasks.extra_traffic",
    "src.infrastructure.services.overlay_user_purge",
    "src.web.endpoints.admin.users",
    "src.web.endpoints.admin.gateways",
    "src.web.endpoints.public.account",
    "src.web.endpoints.public.balance",
    "src.web.endpoints.public.info_content",
    "src.web.endpoints.admin.info",
]


def _globals_of(code: types.CodeType) -> set[str]:
    """Имена, которые код возьмёт из глобалей (включая вложенные функции)."""
    names: set[str] = set()

    def walk(target: types.CodeType) -> None:
        for instruction in dis.get_instructions(target):
            if instruction.opname == "LOAD_GLOBAL":
                names.add(str(instruction.argval))
        for const in target.co_consts:
            if isinstance(const, types.CodeType):
                walk(const)

    walk(code)
    return names


def _functions(obj: object, seen: set[int]) -> list:
    """Функции модуля: верхнего уровня и методы классов, без повторов."""
    out = []
    for name in dir(obj):
        if name.startswith("__"):
            continue
        try:
            value = getattr(obj, name)
        except Exception:  # noqa: BLE001 — свойства модулей бывают ленивыми
            continue
        target = getattr(value, "__wrapped__", value)
        if isinstance(target, types.FunctionType):
            if id(target) not in seen:
                seen.add(id(target))
                out.append(target)
        elif isinstance(target, type):
            for attr in vars(target).values():
                attr = getattr(attr, "__wrapped__", attr)
                if isinstance(attr, types.FunctionType) and id(attr) not in seen:
                    seen.add(id(attr))
                    out.append(attr)
    return out


@pytest.mark.parametrize("module_name", MODULES)
def test_все_имена_модуля_находятся(module_name: str):
    module = importlib.import_module(module_name)
    seen: set[int] = set()
    broken: dict[str, list[str]] = {}
    for func in _functions(module, seen):
        # Функция ищет глобали в СВОЁМ модуле — берём именно его словарь.
        scope = getattr(func, "__globals__", {})
        missing = sorted(
            name
            for name in _globals_of(func.__code__)
            if name not in scope and not hasattr(builtins, name)
        )
        if missing:
            broken[f"{func.__module__}.{func.__qualname__}"] = missing
    assert not broken, f"имена не находятся: {broken}"
