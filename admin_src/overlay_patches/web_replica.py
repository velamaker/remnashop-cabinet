"""Вторая копия бэкенда не должна вести себя как второй бот.

ЗАЧЕМ. Кабинет можно поднять в двух экземплярах: основной процесс, которому Caddy
отдаёт вебхук телеграма, и запасной — он обслуживает только веб и подхватывает
кабинет, если основной упал. Оба поднимают ОДНО И ТО ЖЕ приложение, поэтому оба
выполняют и то, что положено делать ровно один раз на бота.

ЧТО ИЗ ЭТОГО ВЫХОДИТ:

  • владельцу приходит ПО ДВА уведомления «бот запущен» и «бот остановлен» —
    именно с этого симптома всё и нашлось;

  • при остановке запасной копии стирается меню команд бота. `delete_commands`
    ничем не защищён и проходится по всем языкам — то есть перезапуск ЗАПАСНОГО
    процесса гасит меню у живого бота, и вернётся оно только когда перезапустится
    основной. Это опаснее дублей, но снаружи выглядит просто «у бота пропали
    команды», и связать это с запасной копией без подсказки невозможно;

  • управление вебхуком тоже выполняется дважды. Сейчас обе ручки — установка и
    удаление — упираются в выключенный `BOT_RESET_WEBHOOK` и ничего не делают,
    но стоит его включить, и остановка запасной копии снимет вебхук у бота.

КАК ВКЛЮЧИТЬ. Запасному сервису в compose добавляется `OVERLAY_WEB_REPLICA=1`.
Признак осознанный, а не угаданный: «второй ты или первый» из самого процесса не
видно, а ошибиться тут — значит либо получить те же дубли, либо оставить бота без
меню и вебхука вообще. Переменной нет — правка спит и ничего не меняет.

ЧТО НЕ ТРОГАЕМ. Всё остальное запасная копия делает как обычно: обслуживает веб,
шлёт пользовательские уведомления, ходит в панель. Гасим ровно то, что обязано
случиться один раз на бота, а не один раз на процесс.
"""

from __future__ import annotations

import os

from loguru import logger

from . import PatchTargetChanged

_ENV = "OVERLAY_WEB_REPLICA"

# События жизненного цикла БОТА. Их шлёт тот, кто ботом и владеет.
_LIFECYCLE_EVENTS = ("BotStartupEvent", "BotShutdownEvent")


def is_replica() -> bool:
    return str(os.environ.get(_ENV, "")).strip().lower() in ("1", "true", "yes", "on")


def apply() -> str:
    if not is_replica():
        return f"обычный процесс ({_ENV} не задан) — ничего не меняем"

    from src.infrastructure.services.event_bus import EventBusImpl

    original = getattr(EventBusImpl, "publish", None)
    if original is None:
        raise PatchTargetChanged(
            "у EventBusImpl больше нет publish — база перестроила шину событий, "
            "дубли уведомлений о старте и остановке вернутся"
        )
    if getattr(original, "_overlay_wrapped", False):
        return "уже включено"

    async def publish(self, event) -> None:
        if type(event).__name__ in _LIFECYCLE_EVENTS:
            logger.info(
                f"Overlay: событие {type(event).__name__} не публикуем — это запасная "
                f"копия бэкенда, о старте и остановке бота сообщает основная"
            )
            return
        return await original(self, event)

    publish._overlay_wrapped = True  # type: ignore[attr-defined]
    EventBusImpl.publish = publish  # type: ignore[method-assign]
    return "запасная копия: без дублей о старте и остановке бота"


def apply_commands() -> str:
    """Не трогать меню команд бота из запасной копии.

    Здесь важнее не дублирование, а удаление: `delete_commands` стирает команды у
    ЖИВОГО бота при остановке запасного процесса.
    """
    if not is_replica():
        return f"обычный процесс ({_ENV} не задан) — ничего не меняем"

    from src.infrastructure.services.command import CommandService

    for name in ("setup_commands", "delete_commands"):
        original = getattr(CommandService, name, None)
        if original is None:
            raise PatchTargetChanged(
                f"у CommandService больше нет {name} — база перестроила работу с меню "
                f"команд, запасная копия может стереть его у живого бота"
            )
        if getattr(original, "_overlay_replica", False):
            continue

        async def noop(self, _name=name) -> None:
            logger.info(
                f"Overlay: {_name} пропущен — это запасная копия бэкенда, "
                f"меню команд ведёт основная"
            )

        noop._overlay_replica = True  # type: ignore[attr-defined]
        setattr(CommandService, name, noop)

    return "запасная копия: меню команд не трогает"


def apply_webhook() -> str:
    """Не трогать вебхук бота из запасной копии.

    Сейчас обе ручки упираются в выключенный BOT_RESET_WEBHOOK и молчат, но это
    защита по случайности, а не по замыслу: включат переменную — и остановка
    запасной копии снимет вебхук у бота.
    """
    if not is_replica():
        return f"обычный процесс ({_ENV} не задан) — ничего не меняем"

    from src.infrastructure.services.webhook import WebhookService

    setup = getattr(WebhookService, "setup_webhook", None)
    delete = getattr(WebhookService, "delete_webhook", None)
    if setup is None or delete is None:
        raise PatchTargetChanged(
            "у WebhookService больше нет setup_webhook/delete_webhook — база "
            "перестроила работу с вебхуком"
        )
    if getattr(setup, "_overlay_replica", False):
        return "уже включено"

    async def setup_webhook(self, *args, **kwargs):
        # Возвращаем то же, что вернула бы база при совпадении хэша: текущее
        # состояние вебхука. Вызывающий проверяет его на ошибку и идёт дальше.
        logger.info("Overlay: вебхук не переустанавливаем — это запасная копия бэкенда")
        return await self.bot.get_webhook_info()

    async def delete_webhook(self) -> None:
        logger.info("Overlay: вебхук не удаляем — это запасная копия бэкенда")

    setup_webhook._overlay_replica = True  # type: ignore[attr-defined]
    WebhookService.setup_webhook = setup_webhook  # type: ignore[method-assign]
    WebhookService.delete_webhook = delete_webhook  # type: ignore[method-assign]
    return "запасная копия: вебхук не трогает"
