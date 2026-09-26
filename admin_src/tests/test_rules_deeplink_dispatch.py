"""Отложенный deep link через НАСТОЯЩИЙ диспетчер (overlay_patches/rules_deeplink.py).

ЗАЧЕМ ОТДЕЛЬНО ОТ test_rules_deeplink.py. Там правка гоняется по частям. Здесь —
то, что в проде происходит внутри одного апдейта и чего по частям не увидеть:
синтетический `/start` выполняется ПОСРЕДИ обработки колбэка «Принять», когда
aiogram-dialog уже держит замок стека этого чата, dishka уже открыла контейнер
запроса, а обработчики deep link обёрнуты auto_inject. Ошибись мы в этом — окно
промокода не придёт (замок не отпустится, зависимость не найдётся), а юнит-тесты
останутся зелёными.

ЧТО НАСТОЯЩЕЕ: Dispatcher aiogram, setup_dialogs (стек, замки, фоновые апдейты),
setup_dishka(auto_inject=True) + старт диспетчера, мидлвари правил и мусора базы,
роутеры базы `extra/goto.py` (deep link) и `menu/handlers.py` (кнопка «Принять»).
ЧТО ПОДДЕЛАНО: сессия бота (пишет вызовы API и отвечает как Telegram), сервисы в
контейнере dishka, пользователь (вместо мидлвари базы, которой нужна БД) и окна
диалогов — заглушки на тех же состояниях, что у базы, с текстом из start_data.

Роутеры базы — объекты модуля, прицепить их можно только к одному диспетчеру.
Поэтому после теста отцепляем их и возвращаем обработчикам исходные колбэки
(auto_inject dishka подменяет их на месте), чтобы соседние тесты видели всё как было.
"""

import asyncio
import importlib
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, AsyncIterator, Optional

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, DeleteMessage, GetMe, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from aiogram_dialog import Dialog, Window, setup_dialogs
from aiogram_dialog.widgets.text import Const, Format
from dishka import Provider, Scope, make_async_container
from dishka.integrations.aiogram import AiogramProvider, setup_dishka
from redis.asyncio import Redis

rules_mw = importlib.import_module("src.telegram.middlewares.rules")
garbage_mw = importlib.import_module("src.telegram.middlewares.garbage")
menu_handlers = importlib.import_module("src.telegram.routers.menu.handlers")
goto = importlib.import_module("src.telegram.routers.extra.goto")
rd = importlib.import_module("overlay_patches.rules_deeplink")

from src.application.common import Notifier  # noqa: E402
from src.application.common.dao import SubscriptionDao  # noqa: E402
from src.application.use_cases.access.commands.validation import AcceptRules  # noqa: E402
from src.application.use_cases.access.queries.requirements import (  # noqa: E402
    CheckRules,
    CheckRulesResultDto,
)
from src.application.use_cases.promocode.queries.validate import ValidatePromocode  # noqa: E402
from src.application.use_cases.user.queries.plans import GetAvailablePlanByCode  # noqa: E402
from src.core.constants import USER_KEY  # noqa: E402
from src.core.enums import PromocodeRewardType  # noqa: E402
from src.telegram.keyboards import CALLBACK_RULES_ACCEPT  # noqa: E402
from src.telegram.states import MainMenu, Subscription  # noqa: E402

TG = 700100300
GIFT = "GIFT-FEDCBA9876543210FEDCBA9876543210"
FROM = User(id=TG, is_bot=False, first_name="Получатель")
BOT_USER = User(id=42, is_bot=True, first_name="Бот", username="test_bot")
CHAT = Chat(id=TG, type="private")
RULES_MSG_ID = 900


class RecordingSession(BaseSession):
    """Вместо Telegram: запоминаем вызовы, на отправку отвечаем сообщением."""

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

    def texts(self) -> list[str]:
        return [m.text for m in self.requests if isinstance(m, SendMessage)]


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        self.store[key] = value
        return True

    async def getdel(self, key: str) -> Optional[str]:
        return self.store.pop(key, None)


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def notify_user(self, user: Any = None, payload: Any = None, i18n_key: str = "", **kw: Any) -> None:
        self.sent.append(payload.i18n_key if payload is not None else i18n_key)


class Services:
    def __init__(self, user: Any) -> None:
        self.user = user
        self.redis = FakeRedis()
        self.notifier = FakeNotifier()
        self.validated: list[str] = []

    async def check_rules(self, user: Any) -> CheckRulesResultDto:
        return CheckRulesResultDto(is_required=True, is_accepted=user.is_rules_accepted, rules_url="https://rules")

    async def accept_rules(self, user: Any) -> None:
        user.is_rules_accepted = True

    async def validate(self, actor: Any, dto: Any) -> Any:
        self.validated.append(dto.code)
        return SimpleNamespace(code=dto.code, reward_type=PromocodeRewardType.SUBSCRIPTION, reward=30)


def build_container(services: Services) -> Any:
    provider = Provider(scope=Scope.APP)
    bindings = {
        CheckRules: services.check_rules,
        AcceptRules: services.accept_rules,
        Notifier: services.notifier,
        Redis: services.redis,
        ValidatePromocode: services.validate,
        SubscriptionDao: SimpleNamespace(get_current=_none),
        GetAvailablePlanByCode: _none,
    }
    for kind, value in bindings.items():
        provider.provide(_const(value), provides=kind)
    return make_async_container(AiogramProvider(), provider)


async def _none(*args: Any, **kwargs: Any) -> None:
    return None


def _const(value: Any) -> Any:
    def factory() -> Any:
        return value

    return factory


def stub_dialogs() -> list[Dialog]:
    """Окна на тех же состояниях, что у базы: текст говорит, куда попал человек."""
    return [
        Dialog(
            Window(Format("PROMO {start_data[prefill_dto][code]}"), state=Subscription.PROMOCODE),
            Window(Const("PLAN"), state=Subscription.PLAN),
        ),
        Dialog(
            Window(Const("MAIN MENU"), state=MainMenu.MAIN),
            Window(Const("INVITE"), state=MainMenu.INVITE),
        ),
    ]


VENDOR_ROUTERS = (goto.router, menu_handlers.router)


@pytest.fixture
async def world():
    # Снимок обработчиков базы: auto_inject dishka подменит их колбэки на месте.
    saved = [
        (h, h.callback, h.params, h.varkw, h.awaitable)
        for r in VENDOR_ROUTERS
        for observer in r.observers.values()
        for h in observer.handlers
    ]
    user = SimpleNamespace(
        id=7, telegram_id=TG, log=f"[TG:{TG}]", is_rules_accepted=False, is_privileged=False,
        language="ru",
    )
    services = Services(user)
    session = RecordingSession()
    bot = Bot(token="42:TEST", session=session)
    dp = Dispatcher()
    setup_dialogs(dp)

    async def put_user(handler: Any, event: Any, data: dict[str, Any]) -> Any:
        data[USER_KEY] = user  # вместо UserMiddleware базы (ей нужна БД)
        return await handler(event, data)

    dp.message.outer_middleware(put_user)
    dp.callback_query.outer_middleware(put_user)
    rules_mw.RulesMiddleware().setup_outer(dp)
    garbage_mw.GarbageMiddleware().setup_inner(dp)
    dp.include_routers(*VENDOR_ROUTERS, *stub_dialogs())
    container = build_container(services)
    setup_dishka(container, dp, auto_inject=True)
    await dp.emit_startup(bot=bot)

    try:
        yield SimpleNamespace(dp=dp, bot=bot, session=session, services=services, user=user)
    finally:
        await container.close()
        for handler, callback, params, varkw, awaitable in saved:
            handler.callback, handler.params, handler.varkw, handler.awaitable = callback, params, varkw, awaitable
        for r in VENDOR_ROUTERS:
            r._parent_router = None  # noqa: SLF001 — отцепить от тестового диспетчера
        dp.sub_routers.clear()


_update_id = 0


async def feed(w: Any, **event: Any) -> None:
    global _update_id
    _update_id += 1
    await w.dp.feed_update(w.bot, Update(update_id=_update_id, **event))
    # Фоновые апдейты диалогов (bg().start) идут отдельной задачей после этого апдейта.
    for _ in range(50):
        await asyncio.sleep(0.01)


async def send_start(w: Any, text: str) -> None:
    await feed(w, message=Message(message_id=10 + _update_id, date=datetime.now(timezone.utc), chat=CHAT, from_user=FROM, text=text))


async def click_accept(w: Any) -> None:
    rules = Message(message_id=RULES_MSG_ID, date=datetime.now(timezone.utc), chat=CHAT, from_user=BOT_USER, text="rules")
    await feed(w, callback_query=CallbackQuery(id=f"cb{_update_id}", from_user=FROM, chat_instance="ci", data=CALLBACK_RULES_ACCEPT, message=rules))


async def test_gift_link_through_real_dispatcher(world):
    w = world
    # Обработчики deep link в этом диспетчере — как в проде, обёрнуты dishka.
    assert all(getattr(h.callback, "__dishka_injected__", False) for h in goto.router.message.handlers)

    await send_start(w, f"/start promo_{GIFT}")

    assert w.services.notifier.sent == ["ntf-requirement.rules-accept-required"]
    assert w.session.texts() == []
    assert w.services.redis.store == {rd.pending_key(TG): f"promo_{GIFT}"}

    await click_accept(w)

    assert w.user.is_rules_accepted
    assert w.services.validated == [GIFT]
    # Сначала базовое «Принять» открывает главное меню, затем отложенная ссылка
    # открывает окно промокода. Порядок нарочный: заранее отличить «ссылка
    # отказала» от «ссылка открыла окно» нечем (окно открывается фоном), а без
    # меню человек при отказе остался бы в пустом чате. Лишним сообщение не
    # остаётся: окно ссылки открывается с DELETE_AND_SEND и удаляет меню — это
    # проверяется ниже по DeleteMessage.
    assert w.session.texts() == ["MAIN MENU", f"PROMO {GIFT}"]
    assert any(isinstance(m, AnswerCallbackQuery) for m in w.session.requests)
    deleted = [m.message_id for m in w.session.requests if isinstance(m, DeleteMessage)]
    assert RULES_MSG_ID in deleted, "сообщение с правилами должно быть убрано"
    # Меню тоже убирается: окно ссылки открывается с DELETE_AND_SEND, поэтому
    # удалений больше одного — человек видит одно сообщение, а не меню плюс окно.
    assert len(deleted) >= 2, f"окно ссылки не убрало меню: удалений {deleted}"
    assert w.services.redis.store == {}

    # Второе «Принять» (старое сообщение с правилами): отложенного больше нет —
    # остаётся главное меню.
    await click_accept(w)
    assert w.session.texts()[-1] == "MAIN MENU"
    assert w.services.validated == [GIFT]


async def test_accepted_user_link_and_plain_accept_unchanged(world):
    w = world
    w.user.is_rules_accepted = True

    await send_start(w, f"/start promo_{GIFT}")

    assert w.services.notifier.sent == []
    assert w.session.texts() == [f"PROMO {GIFT}"]
    assert w.services.redis.store == {}

    await click_accept(w)
    assert w.session.texts()[-1] == "MAIN MENU"
