"""Свои разделы бота: приватный раздел абьюза и подарки.

ЧТО ДЕЛАЕМ. Подключаем два наших роутера к диспетчеру бота.

ПОЧЕМУ ИМЕННО ПЕРЕД БАЗОВЫМИ. Порядок регистрации у aiogram — это порядок обхода.
Окно главного меню (aiogram-dialog) ловит и сообщения (через MessageInput —
«умный поиск» отвечал «пользователь не найден» на команду `/gift`), и колбэки без
intent-id (перерисовывал меню вместо нашего шага). Наши роутеры фильтруют строго
свои апдейты — команду и префикс колбэка, — поэтому базовые сценарии не задеты,
но встать они обязаны раньше.

ПОЧЕМУ ПАТЧИМ `setup_routers`, А НЕ `setup_dispatcher`. Базовый `setup_dispatcher`
делает три шага подряд: middlewares → фильтры → роутеры. Нам нужно встрять РОВНО
между вторым и третьим, а обёртка вокруг всей функции такой возможности не даёт —
только «до всего» или «после всего». Зато имя `setup_routers` лежит в модуле
диспетчера отдельно и ищется в момент вызова, так что подмена этого имени
вклинивает нас точно в нужную точку, не переписывая саму функцию.

ОТСУТСТВИЕ РОУТЕРА — НЕ ОШИБКА. Раздел абьюза лежит вне публичной сборки
(в .gitignore), поэтому его может не быть вовсе; пропускаем и пишем в лог.
А вот сам факт, что патчить нечего (в базе не стало `setup_routers`), — ошибка:
значит бот перестроил подключение роутеров и наши разделы просто исчезли бы.
"""

from __future__ import annotations

from loguru import logger

from . import PatchTargetChanged

# Наши роутеры и почему каждый может отсутствовать.
_ROUTERS = (
    ("src.telegram.routers.overlay_abuse", "раздел абьюза (только владельцу)"),
    ("src.telegram.routers.overlay_gift", "подарочные подписки"),
)


def _include_ours(dispatcher) -> None:
    for module_path, what in _ROUTERS:
        try:
            module = __import__(module_path, fromlist=["router"])
            dispatcher.include_router(module.router)
        except Exception as exc:  # noqa: BLE001 — раздела может не быть в этой сборке
            logger.info(f"Overlay: раздел «{what}» не подключён: {exc}")
        else:
            logger.info(f"Overlay: раздел «{what}» подключён")


def apply() -> str:
    import src.telegram.dispatcher as dispatcher_module

    original = getattr(dispatcher_module, "setup_routers", None)
    if original is None or not callable(original):
        raise PatchTargetChanged(
            "в src.telegram.dispatcher больше нет setup_routers — бот перестроил "
            "подключение роутеров, наши разделы не встанут"
        )
    if getattr(original, "_overlay_wrapped", False):
        return "уже подключено"

    def setup_routers(dispatcher) -> None:
        # Наши — первыми, иначе окно главного меню съест их апдейты (см. docstring).
        _include_ours(dispatcher)
        original(dispatcher)

    setup_routers._overlay_wrapped = True  # type: ignore[attr-defined]
    dispatcher_module.setup_routers = setup_routers
    return f"наши разделы встают перед базовыми ({len(_ROUTERS)} шт.)"
