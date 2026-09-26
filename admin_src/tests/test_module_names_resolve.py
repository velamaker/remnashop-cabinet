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
    "src.infrastructure.services.overlay_churn_signals",
    "src.infrastructure.taskiq.tasks.churn_signals",
    "src.telegram.routers.overlay_churn_signals",
    "src.web.endpoints.admin.churn_signals",
    "src.infrastructure.services.overlay_device_full",
    "src.infrastructure.taskiq.tasks.device_full",
    "src.telegram.routers.overlay_device_full",
    "src.web.endpoints.admin.device_full",
    "src.infrastructure.taskiq.tasks.extra_devices",
    "src.infrastructure.taskiq.tasks.extra_traffic",
    "src.infrastructure.services.overlay_user_purge",
    "src.web.endpoints.admin.users",
    "src.web.endpoints.admin.gateways",
    "src.web.endpoints.public.account",
    "src.web.endpoints.public.balance",
    "src.web.endpoints.public.info_content",
    "src.web.endpoints.admin.info",
    # Проверка оплаты ЮKassa: функции уровня модуля правка не показывает
    # expect_names_resolve (он видит только подставленное в класс).
    "overlay_patches.yookassa_api_status",
    # Приём оплаты звёздами: проверка «платёжный ли апдейт» — функция уровня модуля.
    "overlay_patches.stars_payment",
    # Тревога о падении кронов: сорвись она на NameError — молчали бы все кроны сразу.
    "src.infrastructure.services.overlay_cron_guard",
    # Семейные профили: сервис стоит в денежном пути (хук оплаты) и в удалении людей,
    # крон приостанавливает и продлевает чужие подписки, ручки и бот создают профили.
    "src.infrastructure.services.overlay_family",
    "src.infrastructure.taskiq.tasks.family",
    "src.web.endpoints.public.family",
    "src.web.endpoints.admin.family",
    "src.telegram.routers.overlay_family",
    "overlay_patches.family_sync_guard",
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


def _unwrap(value: object) -> object:
    """Добраться до ТЕЛА функции сквозь обёртки.

    Крон — это `@broker.task` → `@inject` → `@cron_guard` → тело. Задача taskiq — не
    функция вовсе (тело у неё в `original_func`), обёртка dishka хранит исходную в
    `__dishka_orig_func__` (а `__wrapped__` не ставит), cron_guard — в `__wrapped__`.
    Без разворота сторож смотрел на код обёрток (с чужими глобалями) и тела кронов и
    ручек под dishka не проверял вовсе.
    """
    try:
        target = getattr(value, "original_func", None) or value
        target = getattr(target, "__dishka_orig_func__", None) or target
        for _ in range(20):  # цепочка обёрток конечна; мок отдал бы её бесконечной
            inner = getattr(target, "__wrapped__", None)
            if inner is None or inner is target:
                break
            target = inner
    except Exception:  # noqa: BLE001 — ленивые атрибуты модулей бывают капризны
        return value
    return target


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
        target = _unwrap(value)
        if isinstance(target, types.FunctionType):
            if id(target) not in seen:
                seen.add(id(target))
                out.append(target)
        elif isinstance(target, type):
            for attr in vars(target).values():
                attr = _unwrap(attr)
                if isinstance(attr, types.FunctionType) and id(attr) not in seen:
                    seen.add(id(attr))
                    out.append(attr)
    return out


def _broken_names(module: object) -> dict[str, list[str]]:
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
    return broken


@pytest.mark.parametrize("module_name", MODULES)
def test_все_имена_модуля_находятся(module_name: str):
    module = importlib.import_module(module_name)
    broken = _broken_names(module)
    assert not broken, f"имена не находятся: {broken}"


# Крон в полной обвязке, как в tasks/*.py, с именем, которого в модуле нет.
_SELFTEST_SRC = '''
from dishka.integrations.taskiq import FromDishka, inject
from src.infrastructure.services.overlay_cron_guard import cron_guard


@broker.task(schedule=[{"cron": "*/5 * * * *"}], retry_on_error=False)
@inject(patch_module=True)
@cron_guard("names_selftest", "Самопроверка сторожа имён")
async def run_selftest() -> None:
    return pricing_lost_in_refactor  # имени в модуле нет — так и уехал NameError 1.5.0


class Handlers:
    @staticmethod
    @cron_guard("names_selftest_method", "Самопроверка: метод")
    async def method() -> None:
        return matched_lost_in_refactor
'''


def test_сторож_видит_тело_крона_под_обёртками(monkeypatch):
    """Мутационная самопроверка: сторож, который не разворачивает обёртки, прошёл бы
    мимо тела крона — а ради кронов и денежных путей он и заведён."""
    import sys

    from taskiq import InMemoryBroker

    module = types.ModuleType("rs_names_selftest")
    # taskiq ищет модуль задачи в sys.modules — кладём на время теста.
    monkeypatch.setitem(sys.modules, "rs_names_selftest", module)
    # Свой брокер: в настоящий самопроверочная задача попасть не должна.
    module.broker = InMemoryBroker()  # type: ignore[attr-defined]
    exec(compile(_SELFTEST_SRC, "rs_names_selftest", "exec"), module.__dict__)  # noqa: S102
    assert type(module.run_selftest).__name__ == "AsyncTaskiqDecoratedTask", "обвязка не та, что у кронов"

    broken = _broken_names(module)
    assert broken.get("rs_names_selftest.run_selftest") == ["pricing_lost_in_refactor"], broken
    assert broken.get("rs_names_selftest.Handlers.method") == ["matched_lost_in_refactor"], broken

    # И на настоящем кроне: проверяется его тело, а не обёртка dishka или сторожа.
    real = importlib.import_module("src.infrastructure.taskiq.tasks.device_full")
    bodies = {f.__qualname__: f.__code__.co_filename for f in _functions(real, set())}
    assert bodies.get("run_device_full", "").endswith("tasks/device_full.py"), bodies.get("run_device_full")
