"""Настоящий диспетчер aiogram без Telegram — общая обвязка тестов роутеров бота.

ЗАЧЕМ. Часть ошибок в обработчиках живёт только внутри живого апдейта: aiogram
собирает `data` из workflow_data диспетчера и своих мидлварей, aiogram_dialog и
dishka дописывают туда своё, а обёртка `@inject` зовёт функцию с `**kwargs, **solved`.
Юнит-тест, который зовёт функцию напрямую, всего этого не проходит — и 25.09 так
пропустил TypeError на каждое нажатие кнопок «сигналов до ухода».

ЧТО НАСТОЯЩЕЕ: Dispatcher aiogram с тем же набором ключей workflow_data, что у базы
(читаем прямо из её `src/telegram/dispatcher.py`), setup_dialogs, setup_dishka
(auto_inject=True) и старт диспетчера так же, как его стартует база.
ЧТО ПОДДЕЛАНО: сессия бота (пишет вызовы API и отвечает как Telegram, в сеть не
ходит), хранилище FSM (память вместо Redis), сервисы в контейнере dishka и
пользователь (вместо мидлвари базы, которой нужна БД).
"""

from __future__ import annotations

import ast
import importlib
import inspect
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator, Iterable, Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import GetMe, SendMessage
from aiogram.types import CallbackQuery, Chat, InaccessibleMessage, Message, Update, User
from aiogram_dialog import setup_dialogs
from dishka import Provider, Scope, make_async_container
from dishka.integrations.aiogram import AiogramProvider, setup_dishka

from src.core.constants import USER_KEY

TG = 700100300
BOT_USER = User(id=42, is_bot=True, first_name="Бот", username="test_bot")
FROM = User(id=TG, is_bot=False, first_name="Человек")
CHAT = Chat(id=TG, type="private")


class RecordingSession(BaseSession):
    """Вместо Telegram: запоминаем вызовы API, на отправку отвечаем сообщением."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[Any] = []
        self._next_id = 1000

    async def make_request(self, bot: Bot, method: Any, timeout: Optional[int] = None) -> Any:
        self.requests.append(method)
        if isinstance(method, SendMessage):
            self._next_id += 1
            return Message(
                message_id=self._next_id, date=datetime.now(timezone.utc),
                chat=Chat(id=int(method.chat_id), type="private"), from_user=BOT_USER,
                text=method.text,
            )
        if isinstance(method, GetMe):
            return BOT_USER
        return True

    async def stream_content(self, *args: Any, **kwargs: Any) -> AsyncIterator[bytes]:
        yield b""

    async def close(self) -> None:
        return None

    def of(self, kind: type) -> list[Any]:
        return [m for m in self.requests if isinstance(m, kind)]


def const_provider(bindings: dict[type, Any]) -> Provider:
    """Провайдер dishka: каждому типу — готовый объект (заглушка сервиса)."""
    provider = Provider(scope=Scope.APP)
    for kind, value in bindings.items():
        provider.provide(_const(value), provides=kind)
    return provider


def _const(value: Any) -> Any:
    def factory() -> Any:
        return value

    return factory


# ── какие ключи база кладёт в data ──────────────────────────────────────────


def vendor_workflow_keys() -> set[str]:
    """Ключи workflow_data диспетчера базы — всё, что она передаёт в `Dispatcher(...)`
    сверх его собственных параметров (сейчас это `config`).

    Читаем исходник базы, а не переписываем список руками: добавит база в диспетчер
    ещё что-нибудь (`redis=…`) — тесты и сторож узнают об этом сами.
    """
    module = importlib.import_module("src.telegram.dispatcher")
    tree = ast.parse(inspect.getsource(module))
    own = set(inspect.signature(Dispatcher.__init__).parameters) - {"self", "kwargs"}
    keys: set[str] = set()
    calls = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name != "Dispatcher":
            continue
        calls += 1
        for keyword in node.keywords:
            if keyword.arg is None:
                raise AssertionError(
                    "база передаёт в Dispatcher(**…) — ключи workflow_data статически не "
                    "прочитать, обновите vendor_workflow_keys"
                )
            if keyword.arg not in own:
                keys.add(keyword.arg)
    if not calls:
        raise AssertionError(
            "в src.telegram.dispatcher нет вызова Dispatcher(...) — база перестроила "
            "создание диспетчера, обновите vendor_workflow_keys"
        )
    return keys


def vendor_middleware_keys() -> set[str]:
    """Что мидлвари базы дописывают в data: `data[USER_KEY] = user` и подобное.

    Неразборчивую запись не пропускаем молча, а валим: сторож, который не знает
    ключа, пропустит ровно ту ошибку, ради которой он написан.
    """
    package = importlib.import_module("src.telegram.middlewares")
    keys: set[str] = set()
    for path in sorted(Path(package.__path__[0]).glob("*.py")):
        module = importlib.import_module(f"src.telegram.middlewares.{path.stem}")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            for key_node in _data_writes(node):
                keys.add(_resolve_key(key_node, module, path, node))
    return keys


def _data_writes(node: ast.AST) -> list[ast.AST]:
    targets: list[ast.AST] = []
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        targets = [node.target]
    found = [
        t.slice for t in targets
        if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and t.value.id == "data"
    ]
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "data"
    ):
        if node.func.attr == "setdefault" and node.args:
            found.append(node.args[0])
        elif node.func.attr == "update":
            for keyword in node.keywords:
                found.append(ast.Constant(keyword.arg) if keyword.arg else keyword.value)
            for arg in node.args:
                if isinstance(arg, ast.Dict):
                    found.extend(k for k in arg.keys if k is not None)
                else:
                    found.append(arg)
    return found


def _resolve_key(key: ast.AST, module: Any, path: Path, node: ast.AST) -> str:
    if isinstance(key, ast.Constant) and isinstance(key.value, str):
        return key.value
    if isinstance(key, ast.Name) and isinstance(getattr(module, key.id, None), str):
        return getattr(module, key.id)
    raise AssertionError(
        f"{path.name}:{getattr(node, 'lineno', '?')}: мидлварь базы пишет в data ключ, "
        f"который не прочитать статически ({ast.unparse(key)}) — обновите vendor_middleware_keys"
    )


@asynccontextmanager
async def real_dispatcher(
    routers: Iterable[Router],
    *,
    workflow: dict[str, Any],
    user: Any = None,
    bindings: Optional[dict[type, Any]] = None,
) -> AsyncIterator[SimpleNamespace]:
    """Диспетчер как у базы, с нашими роутерами; после выхода — всё как было.

    Роутеры — объекты модулей, прицепить их можно только к одному диспетчеру, а
    auto_inject dishka подменяет колбэки обработчиков на месте. Поэтому на выходе
    отцепляем роутеры и возвращаем обработчикам исходные колбэки — соседние тесты
    видят модули нетронутыми.
    """
    routers = list(routers)
    saved = [
        (h, h.callback, h.params, h.varkw, h.awaitable)
        for r in routers
        for observer in r.observers.values()
        for h in observer.handlers
    ]
    session = RecordingSession()
    bot = Bot(token="42:TEST", session=session)
    dp = Dispatcher(storage=MemoryStorage(), **workflow)
    setup_dialogs(dp)

    if user is not None:
        async def put_user(handler: Any, event: Any, data: dict[str, Any]) -> Any:
            data[USER_KEY] = user  # вместо UserMiddleware базы (ей нужна БД)
            return await handler(event, data)

        dp.message.outer_middleware(put_user)
        dp.callback_query.outer_middleware(put_user)

    dp.include_routers(*routers)
    container = make_async_container(AiogramProvider(), const_provider(bindings or {}))
    setup_dishka(container, dp, auto_inject=True)
    # Как база (web/endpoints/telegram.py): старт с workflow_data — тогда же
    # auto_inject оборачивает обработчики, не обёрнутые вручную.
    await dp.emit_startup(bot=bot, **dp.workflow_data)
    try:
        yield SimpleNamespace(dp=dp, bot=bot, session=session)
    finally:
        await container.close()
        for handler, callback, params, varkw, awaitable in saved:
            handler.callback, handler.params, handler.varkw, handler.awaitable = (
                callback, params, varkw, awaitable,
            )
        for r in routers:
            r._parent_router = None  # noqa: SLF001 — отцепить от тестового диспетчера
        dp.sub_routers.clear()


_update_id = 0


def bot_message(message_id: int = 900, text: str = "вопрос") -> Message:
    return Message(
        message_id=message_id, date=datetime.now(timezone.utc), chat=CHAT,
        from_user=BOT_USER, text=text,
    )


def old_message(message_id: int = 900) -> InaccessibleMessage:
    """Так Telegram присылает сообщение, которое бот уже не может прочитать."""
    return InaccessibleMessage(chat=CHAT, message_id=message_id)


async def press(w: Any, data: str, *, message: Any = None) -> Any:
    """Нажать инлайн-кнопку с callback_data `data` под сообщением `message`."""
    global _update_id
    _update_id += 1
    update = Update(
        update_id=_update_id,
        callback_query=CallbackQuery(
            id=f"cb{_update_id}", from_user=FROM, chat_instance="ci", data=data,
            message=message if message is not None else bot_message(),
        ),
    )
    return await w.dp.feed_update(w.bot, update)


async def observed_data_keys() -> set[str]:
    """Ключи, которые НАСТОЯЩИЕ aiogram + aiogram_dialog + dishka кладут в data
    обработчика с `**data` — при тех же ключах workflow_data, что у базы.

    Список aiogram меняется от версии к версии (event_router, aiogd_* появились не
    сразу), поэтому сторож берёт его у живого диспетчера, а не только из памяти.
    """
    seen: set[str] = set()
    probe = Router(name="probe_data_keys")

    @probe.callback_query(F.data == "probe")
    async def on_probe(callback: CallbackQuery, **data: Any) -> None:
        seen.update(data)

    @probe.message()
    async def on_message(message: Message, **data: Any) -> None:
        seen.update(data)

    workflow = {key: object() for key in vendor_workflow_keys()}
    async with real_dispatcher([probe], workflow=workflow, user=SimpleNamespace(id=1)) as w:
        await press(w, "probe")
        global _update_id
        _update_id += 1
        await w.dp.feed_update(
            w.bot,
            Update(
                update_id=_update_id,
                message=Message(
                    message_id=11, date=datetime.now(timezone.utc), chat=CHAT,
                    from_user=FROM, text="привет",
                ),
            ),
        )
    return seen
