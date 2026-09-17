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
# Функции, уже проверенные на разрешимость имён. Одна и та же наша функция видна
# из нескольких модулей базы (класс, импортированный в чужую шапку, лежит и там),
# и без этого одна поломка попадала бы в отчёт от имени чужой правки.
_names_checked: set[int] = set()


class PatchTargetChanged(RuntimeError):
    """Цель правки выглядит не так, как мы ожидали, — апстрим её изменил."""


# Карта «uuid ↔ числовой id», созданная слоем 3.x. Кладётся сюда провайдером SDK и
# нужна другим правкам: вебхуки панели 3.x приходят БЕЗ uuid, и восстановить его
# может только эта карта. На 2.x остаётся None — там uuid приходит сам.
_identity_map: Any = None


def set_identity_map(value: Any) -> None:
    global _identity_map
    _identity_map = value


def identity_map() -> Any:
    return _identity_map


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


def _overlay_functions(obj: Any, seen: set[int]) -> list[Any]:
    """Наши функции, до которых можно дойти от подставленного объекта.

    Замена редко лежит в базе голой: обработчики бота обёрнуты `@inject` из dishka,
    и в модуль попадает ЕЁ функция, а наша спрятана в замыкании обёртки. Поэтому
    идём не только по самому объекту, но и по ячейкам его `__closure__`.
    Свои узнаём по модулю, в котором функция объявлена, — это и есть тот модуль,
    в чьих глобалях она будет искать имена.
    """
    import types

    if id(obj) in seen:
        return []
    seen.add(id(obj))

    found: list[Any] = []
    if isinstance(obj, (staticmethod, classmethod)):
        return _overlay_functions(obj.__func__, seen)
    if not isinstance(obj, types.FunctionType):
        return []

    if str(obj.__globals__.get("__name__", "")).startswith(f"{__package__}."):
        found.append(obj)

    for cell in obj.__closure__ or ():
        try:
            value = cell.cell_contents
        except ValueError:  # noqa: PERF203 — пустая ячейка, идти некуда
            continue
        found.extend(_overlay_functions(value, seen))
    return found


def _unresolved_globals(func: Any) -> list[str]:
    """Имена, которые функция возьмёт из глобалей, но взять их будет неоткуда.

    Смотрим не текст, а байткод: `LOAD_GLOBAL` — это ровно то, что интерпретатор
    пойдёт искать в `__globals__` и builtins, без ложных срабатываний на атрибуты
    (`x.foo`) и без ложных обвинений замыканию (те идут через `LOAD_DEREF`).
    Вложенные функции лежат в `co_consts` — обходим и их.
    """
    import builtins
    import dis
    import types

    names: set[str] = set()

    def walk(code: Any) -> None:
        for instruction in dis.get_instructions(code):
            if instruction.opname == "LOAD_GLOBAL":
                names.add(str(instruction.argval))
        for const in code.co_consts:
            if isinstance(const, types.CodeType):
                walk(const)

    walk(func.__code__)
    return sorted(n for n in names if n not in func.__globals__ and not hasattr(builtins, n))


def expect_names_resolve(target_module: str, what: str) -> None:
    """Убедиться, что подставленный код сможет найти все свои имена.

    ЗАЧЕМ ОТДЕЛЬНО ОТ expect_source. Та сверяет ЧУЖОЙ код — тот, который мы
    собираемся заменить. А эта — НАШ. Разница стоила денег: 29.08 подтверждение
    подарочного промокода падало на `NameError: PENDING_PROMO_KEY`, потому что
    функция была объявлена здесь, а имя жило в шапке базового модуля. Сверка
    исходника прошла, правка «применилась», и сломалось только под живым
    оплатившим человеком — на нажатии кнопки, куда overlay уже не смотрит.

    Ловится это тем, что функция, объявленная в нашем модуле, ищет глобали ТОЖЕ
    в нашем модуле: переносить тело из базы можно, а её шапку — нельзя забывать.
    """
    import sys

    module = sys.modules.get(target_module)
    if module is None:  # хук сработал на импорте — модуль обязан быть, но не падаем
        return

    seen: set[int] = set()
    broken: list[str] = []

    def check(holder: str, obj: Any) -> None:
        for func in _overlay_functions(obj, seen):
            if id(func) in _names_checked:
                continue
            _names_checked.add(id(func))
            missing = _unresolved_globals(func)
            if missing:
                # Виновата не та правка, при которой мы наткнулись, а тот файл, где
                # функция ОБЪЯВЛЕНА: искать имена она будет именно в его глобалях.
                where = str(func.__globals__.get("__name__", "?")).rpartition(".")[2]
                broken.append(f"{where}.{holder}{func.__name__}: {', '.join(missing)}")

    for attr, value in list(vars(module).items()):
        check("", value)
        if isinstance(value, type):
            for method in list(vars(value).values()):
                check(f"{attr}.", method)

    if broken:
        raise PatchTargetChanged(
            f"{what}: подставленный код не найдёт свои имена — "
            + "; ".join(broken)
            + ". Имя взято из шапки базового модуля, а функция объявлена у нас: "
            "перенесите импорт/константу в наш модуль или прочитайте её с цели."
        )


def _run(name: str, fn: Callable[[], str], target_module: str = "") -> None:
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
        # Правка встала — теперь проверяем НАШ код, а не чужой (см. док-строку
        # expect_names_resolve). Отдельным try: сорвавшаяся проверка обязана
        # оказаться в _failures как отказ, а не утонуть в already-applied.
        if target_module:
            try:
                expect_names_resolve(target_module, name)
            except Exception as exc:  # noqa: BLE001
                _failures.append((name, f"{type(exc).__name__}: {exc}"))
                print(
                    f"\n!!! OVERLAY: правка «{name}» ПРИМЕНИЛАСЬ, НО СЛОМАНА: "
                    f"{type(exc).__name__}: {exc}\n"
                    f"!!! Она упадёт под живым пользователем. Разберитесь до деплоя.\n",
                    file=sys.stderr,
                    flush=True,
                )
                try:
                    from loguru import logger

                    logger.error(f"Overlay: правка «{name}» применилась, но сломана — {exc}")
                except Exception:  # noqa: BLE001
                    pass
                return

        _applied.append(f"{name}: {detail}")
        # Пишем в лог КАЖДОЕ применение, а не только отказы. Правка меняет поведение
        # бота молча, и «сработала ли она в этом процессе» иначе не проверить ничем:
        # процессов четыре, каждый импортирует своё, и правка, не нужная одному,
        # обязана сработать в другом. Один INFO на правку за жизнь процесса.
        try:
            from loguru import logger

            logger.info(f"Overlay: правка «{name}» применена — {detail}")
        except Exception:  # noqa: BLE001 — логгер не обязан быть готов
            pass


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
        # Первой: подменяет фабрику в src.__main__, чтобы скрипт запуска бота
        # остался нетронутым. Всё, что читает оттуда имя application, идёт позже.
        ("точка входа", "src.__main__", "entrypoint"),
        ("потолок версии панели", "src.core.constants", "panel_version"),
        ("русские переводы", "src.infrastructure.services.i18n", "translations_ru"),
        ("свои разделы бота", "src.telegram.dispatcher", "bot_routers"),
        (
            "команда /gift в меню",
            "src.infrastructure.services.command",
            "bot_commands",
        ),
        ("SDK панели по её версии", "src.infrastructure.di.providers", "remnawave_sdk"),
        (
            "подарок не сжигает дни",
            "src.application.use_cases.promocode.commands.activate",
            "promocode_gift_days",
        ),
        (
            "пополнение и подарок через шлюз",
            "src.application.use_cases.gateways.commands.payment",
            "gateway_payment",
        ),
        (
            "перенос остатка при смене тарифа",
            "src.application.use_cases.subscription.commands.purchase",
            "plan_change_carryover",
        ),
        (
            "тексты смены тарифа в боте",
            "src.telegram.routers.subscription.getters",
            "plan_change_bot",
        ),
        (
            "письма в оформлении кабинета",
            "src.infrastructure.services",
            "email_sender",
        ),
        (
            "публичные ручки подписки",
            "src.web.endpoints.public.subscription",
            "public_subscription",
        ),
        (
            "главное меню бота",
            "src.telegram.routers.menu.dialog",
            "menu_dialog",
        ),
        (
            "уведомления в стиле кабинета",
            "src.infrastructure.services.notification",
            "notifications",
        ),
        (
            "баллы рефералки и кэшбэк",
            "src.application.use_cases.referral.commands.rewards",
            "referral_rewards",
        ),
        (
            "сверка суммы платежа ЮMoney",
            "src.infrastructure.payment_gateways.yoomoney",
            "yoomoney_amount_check",
        ),
        (
            "рефералка без почтового гейта",
            "src.web.endpoints.public.referral",
            "public_referral_gate",
        ),
        (
            "второе подтверждение подарка",
            "src.telegram.routers.subscription.promocode_handlers",
            "promocode_gift_confirm",
        ),
        (
            "статистика без пробных оплат",
            "src.infrastructure.database.dao.transaction",
            "stats_exclude_test",
        ),
        (
            "рассылка «истекают скоро»",
            "src.application.use_cases.broadcast.queries.audience",
            "broadcast_expiring",
        ),
    )

    # Правки запасной копии бэкенда: у них по три разные цели, поэтому идут
    # отдельным списком с явным именем функции.
    replica = (
        ("запасная копия: без дублей о боте", "src.infrastructure.services.event_bus", "apply"),
        ("запасная копия: меню команд", "src.infrastructure.services.command", "apply_commands"),
        ("запасная копия: вебхук", "src.infrastructure.services.webhook", "apply_webhook"),
    )

    # Вебхуки панели: модели событий (пользователь, устройство), восстановление uuid
    # и сверка адресатов уведомлений об устройствах — разные модули, поэтому
    # отдельным списком с явным именем функции.
    webhook = (
        ("вебхуки: модель события", "remnapy.models.webhook", "apply_model"),
        ("вебхуки: восстановление uuid", "src.application.services.remnawave", "apply_handlers"),
        ("вебхуки: модель устройства", "remnapy.models.webhook", "apply_device_model"),
        ("вебхуки: устройства — админам, не человеку", "src.application.services.remnawave", "check_device_notify"),
    )

    for name, target, patch_module in plan:
        on_import(
            target,
            lambda n=name, m=patch_module, t=target: _run(
                n, lambda: import_module(f".{m}", __package__).apply(), t
            ),
        )

    for name, target, func in webhook:
        on_import(
            target,
            lambda n=name, f=func, t=target: _run(
                n, lambda: getattr(import_module(".webhook_v3", __package__), f)(), t
            ),
        )

    # Напоминания об окончании подписки на панели 3.x: перевод события в разборе,
    # фильтр получателей в обработчике и сверка эндпоинта между ними — три модуля,
    # поэтому своим списком. Идёт ПОСЛЕ `webhook`: фильтр оборачивает обработчик
    # поверх восстановления uuid (но и в обратном порядке uuid он дописывает сам).
    expiration = (
        ("напоминания: user.expiration панели 3.x", "remnapy.controllers.webhooks", "apply_parse"),
        ("напоминания: кому не слать", "src.application.services.remnawave", "apply_guard"),
        ("напоминания: эндпоинт вебхука не менялся", "src.web.endpoints.remnawave", "check_endpoint"),
    )

    for name, target, func in expiration:
        on_import(
            target,
            lambda n=name, f=func, t=target: _run(
                n, lambda: getattr(import_module(".webhook_expiration", __package__), f)(), t
            ),
        )

    for name, target, func in replica:
        on_import(
            target,
            lambda n=name, f=func, t=target: _run(
                n, lambda: getattr(import_module(".web_replica", __package__), f)(), t
            ),
        )


def pending() -> list[str]:
    """Модули, чьи правки ещё не сработали (их пока не импортировали)."""
    from ._hooks import pending as _pending

    return _pending()
