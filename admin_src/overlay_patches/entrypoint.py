"""Точка входа: приложение кабинета вместо базового, без правки скрипта запуска.

ЧТО БЫЛО. Скрипт `docker-entrypoint.sh` в образе бота заканчивается строкой
`exec uvicorn src.__main__:application …`, и путь там зашит намертво. Чтобы
поднялось наше приложение, сборка переписывала эту строку через `sed` — то есть
меняла файл бота.

ЧТО СТАЛО. Скрипт остаётся нетронутым, а `src.__main__:application` сама начинает
возвращать наше приложение: базовую фабрику откладываем в сторону и подставляем на
её место свою обёртку. Uvicorn просит `application` — получает кабинет со всеми
нашими роутами; сам скрипт при этом побайтово совпадает с апстримом.

ПОЧЕМУ ЭТО НЕ ЗАЦИКЛИВАЕТСЯ. Наша обёртка внутри зовёт базовую фабрику — ту самую,
которую только что подменили. Поэтому оригинал сохраняется в модуле под отдельным
именем, а `src/overlay_app.py` берёт для вызова именно его. Правка выполняется
внутри загрузки `src.__main__`, до того как кто-либо успеет прочитать оттуда имя
`application`, — значит подменённое имя видят все, а оригинал не теряется.

ПОЧЕМУ ОБЁРТКА ЛЕНИВАЯ. `src.overlay_app` импортируется не здесь, а в момент
вызова: на этапе загрузки `src.__main__` половина приложения ещё не собрана, и
тянуть её раньше времени значит поменять порядок инициализации у бота.
"""

from __future__ import annotations

from . import PatchTargetChanged

# Под этим именем оригинальная фабрика остаётся доступной; его читает overlay_app.
STASH = "_overlay_base_application"


def apply() -> str:
    import src.__main__ as entry

    original = getattr(entry, "application", None)
    if original is None or not callable(original):
        raise PatchTargetChanged(
            "в src/__main__.py больше нет фабрики application — база сменила точку "
            "входа, uvicorn поднимет не наше приложение"
        )
    if getattr(original, "_overlay_wrapped", False):
        return "уже подставлена"

    setattr(entry, STASH, original)

    def application():
        # Импорт внутри вызова: см. docstring про порядок инициализации.
        from src.overlay_app import application as build_cabinet

        return build_cabinet()

    application._overlay_wrapped = True  # type: ignore[attr-defined]
    entry.application = application
    return "src.__main__:application отдаёт кабинет (скрипт запуска не тронут)"
