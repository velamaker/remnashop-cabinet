"""Команда `/gift` в меню бота (☰ у поля ввода).

ЧТО ДЕЛАЕМ. Бот выставляет Telegram'у список команд отдельно для каждого языка,
перебирая свой enum `Command`. Подарочные подписки — наша функция, в этом перечне
её нет, и добавить её туда значило бы держать копию `core/enums.py` целиком.

КУДА ВСТРАИВАЕМСЯ. В перечень не лезем и цикл по языкам не повторяем: на время
работы `setup_commands` подменяем у бота метод `set_my_commands`, дописывая свою
команду к тому списку, который база как раз собралась отправить. Язык берём из
аргумента того же вызова, поэтому подпись всегда совпадает с остальными командами.
После вызова метод возвращается на место — подмена нужна только на это время, а
бот живёт дальше и шлёт тем же методом другое.

Так мы не зависим ни от того, как база считает список языков, ни от того, из чего
она собирает команды, — только от того, что она их вообще отправляет.

Подписи держим здесь: ключа в переводах бота для нашей команды нет и быть не может.
Незнакомый язык получает английскую — это честнее пустой строки и надёжнее падения.

ПОВЕДЕНИЕ НЕ МЕНЯЕТСЯ. До этой правки тот же результат достигался копией файла
`infrastructure/services/command.py`; человек в боте видит ровно тот же пункт меню
с теми же подписями.
"""

from __future__ import annotations

from . import PatchTargetChanged

_DESCRIPTIONS: dict[str, str] = {
    "ru": "Подарить подписку",
    "en": "Gift a subscription",
    "tr": "Abonelik hediye et",
    "kk": "Жазылымды сыйға тарту",
    "uz": "Obunani sovg'a qilish",
    "be": "Падарыць падпіску",
    "es": "Regalar una suscripción",
}

_COMMAND = "gift"


def apply() -> str:
    from aiogram.types import BotCommand

    from src.infrastructure.services.command import CommandService

    original = getattr(CommandService, "setup_commands", None)
    if original is None:
        raise PatchTargetChanged(
            "у CommandService больше нет setup_commands — бот перестроил выставление "
            "команд, /gift пропадёт из меню"
        )
    if getattr(original, "_overlay_wrapped", False):
        return "уже добавлена"

    def _describe(language_code: str | None) -> str:
        return _DESCRIPTIONS.get(str(language_code or "en"), _DESCRIPTIONS["en"])

    async def setup_commands(self) -> None:
        bot = self.bot
        send = bot.set_my_commands

        async def set_my_commands(commands, **kwargs):
            items = list(commands)
            if not any(getattr(c, "command", None) == _COMMAND for c in items):
                items.append(
                    BotCommand(
                        command=_COMMAND,
                        description=_describe(kwargs.get("language_code")),
                    )
                )
            return await send(commands=items, **kwargs)

        bot.set_my_commands = set_my_commands
        try:
            return await original(self)
        finally:
            bot.set_my_commands = send

    setup_commands._overlay_wrapped = True  # type: ignore[attr-defined]
    CommandService.setup_commands = setup_commands  # type: ignore[method-assign]
    return f"/{_COMMAND} дописывается к списку команд ({len(_DESCRIPTIONS)} языков)"
