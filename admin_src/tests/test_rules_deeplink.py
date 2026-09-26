"""Deep link новичка при обязательных правилах (overlay_patches/rules_deeplink.py).

ЧТО ЗАПИРАЕМ. Подарок приходит ссылкой `?start=promo_<код>`, а получатель почти
всегда впервые в боте и правил не принимал. Мидлварь правил базы показывает правила
и гасит `/start`, а «Принять» открывает главное меню — код терялся. Теперь:

  * непринятые правила + `/start <аргумент>` — правила показаны, дальше апдейт НЕ
    пошёл (как в базе), аргумент лёг в Redis под telegram_id с TTL;
  * «Принять» — правила приняты (как в базе), а вместо главного меню синтетический
    `/start <аргумент>` проходит НАСТОЯЩИЙ роутер deep link базы: для promo_<код> —
    окно промокода с подставленным кодом;
  * однократно: второе «Принять» — главное меню; истёкший TTL — главное меню;
  * у принявшего правила (и когда правила не обязательны) deep link идёт как раньше,
    Redis не трогается;
  * ref_/ad_ не откладываются (база засчитывает их до правил), мусор — тоже;
  * аргумент, который роутер базы не узнал, и сбой Redis — главное меню, не ошибка.

КАК ГОНЯЕМ. Настоящая `RulesMiddleware.middleware_logic` (уже обёрнутая правкой),
настоящий обработчик «Принять» из роутера меню (подменённый правкой — берём его из
роутера, как aiogram) и настоящий роутер deep link базы `extra/goto.py` с его
фильтрами и обработчиками. Подделаны только контейнер (CheckRules/AcceptRules/
Notifier/Redis), менеджер диалогов и бот. Зависимости обработчиков deep link в
проде подставляет dishka (auto_inject на старте), здесь её нет — aiogram отдаёт их
по имени из данных апдейта, куда тест их и кладёт.

Сверку sha исходников базы и разрешимость имён закрывает CI-гейт «Правки overlay
применяются» (`failures()`/`pending()`); здесь — что правка встала и что делает.
"""

import importlib
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Optional

import pytest
from aiogram.methods import AnswerCallbackQuery, DeleteMessage
from aiogram.types import CallbackQuery, Chat, Message, User
from aiogram_dialog import StartMode
from redis.asyncio import Redis

import overlay_patches

rules_mw = importlib.import_module("src.telegram.middlewares.rules")
menu_handlers = importlib.import_module("src.telegram.routers.menu.handlers")
goto = importlib.import_module("src.telegram.routers.extra.goto")
rd = importlib.import_module("overlay_patches.rules_deeplink")

from src.application.common import Notifier  # noqa: E402
from src.application.use_cases.access.commands.validation import AcceptRules  # noqa: E402
from src.application.use_cases.access.queries.requirements import (  # noqa: E402
    CheckRules,
    CheckRulesResultDto,
)
from src.core.constants import CONTAINER_KEY, USER_KEY  # noqa: E402
from src.core.enums import PromocodeRewardType  # noqa: E402
from src.core.exceptions import PromocodeNotFoundError  # noqa: E402
from src.telegram.keyboards import CALLBACK_RULES_ACCEPT  # noqa: E402
from src.telegram.states import MainMenu, Subscription  # noqa: E402

TG = 700100200
GIFT = "GIFT-0123456789ABCDEF0123456789ABCDEF"
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
FROM = User(id=TG, is_bot=False, first_name="Получатель")
BOT_USER = User(id=1, is_bot=True, first_name="Бот")
CHAT = Chat(id=TG, type="private")
RULES_MSG_ID = 501


# ── подделки ────────────────────────────────────────────────────────────────


class FakeBot:
    """Бот, который помнит вызовы API и на всё отвечает True."""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def __call__(self, method: Any, request_timeout: Optional[int] = None) -> Any:
        self.calls.append(method)
        return True

    def called(self, kind: type) -> list[Any]:
        return [c for c in self.calls if isinstance(c, kind)]


class FakeRedis:
    """SET с TTL и GETDEL — ровно то, чем пользуется правка. Часы двигает тест."""

    def __init__(self) -> None:
        self.now = 0.0
        self.store: dict[str, tuple[str, Optional[float]]] = {}
        self.sets: list[tuple[str, str, Optional[int]]] = []

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        self.sets.append((key, value, ex))
        self.store[key] = (value, self.now + ex if ex else None)
        return True

    async def getdel(self, key: str) -> Optional[str]:
        value = self.live(key)
        self.store.pop(key, None)
        return value

    async def get(self, key: str) -> Optional[str]:
        # Правка им не пользуется; есть, чтобы мутация «GETDEL → GET» (потеря
        # однократности) ловилась тестом, а не падала на AttributeError подделки.
        return self.live(key)

    def live(self, key: str) -> Optional[str]:
        item = self.store.get(key)
        if item is None or (item[1] is not None and self.now >= item[1]):
            return None
        return item[0]


class BrokenRedis:
    async def set(self, *args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("redis down")

    async def getdel(self, *args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("redis down")


class FakeCheckRules:
    def __init__(self, required: bool = True) -> None:
        self.required = required

    async def __call__(self, user: Any) -> CheckRulesResultDto:
        if not self.required:
            return CheckRulesResultDto(is_required=False, is_accepted=True)
        return CheckRulesResultDto(
            is_required=True, is_accepted=user.is_rules_accepted, rules_url="https://rules"
        )


class FakeAcceptRules:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, user: Any) -> None:
        self.calls += 1
        user.is_rules_accepted = True


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def notify_user(self, user: Any = None, payload: Any = None, i18n_key: str = "", **kw: Any) -> None:
        self.sent.append(payload.i18n_key if payload is not None else i18n_key)


class FakeContainer:
    def __init__(self, mapping: dict[Any, Any]) -> None:
        self.mapping = mapping

    async def get(self, cls: Any) -> Any:
        return self.mapping[cls]


class FakeState:
    """Состояние диалога человека. Открытие окна его МЕНЯЕТ — как в настоящем aiogram.

    По этому признаку правка отличает «окно открылось» от «обработчик нашёлся, но
    отказал»: при отказе состояние остаётся прежним, и человеку нужно показать
    главное меню, иначе он остаётся в пустом чате.
    """

    def __init__(self) -> None:
        self.value: Any = None

    async def get_state(self) -> Any:
        return self.value


class FakeBg:
    def __init__(self, log: list, state: "FakeState") -> None:
        self.log = log
        self.state = state

    async def start(self, state: Any, data: Any = None, mode: Any = None, show_mode: Any = None) -> None:
        self.log.append(("bg.start", state, data, mode))
        self.state.value = state


class FakeDialogManager:
    def __init__(self, state: "FakeState") -> None:
        self.log: list = []
        self.state = state

    def bg(self, user_id: Any = None, chat_id: Any = None, **kwargs: Any) -> FakeBg:
        assert user_id == TG and chat_id == TG
        return FakeBg(self.log, self.state)

    async def start(self, state: Any, data: Any = None, mode: Any = None, show_mode: Any = None) -> None:
        self.log.append(("start", state, data, mode))
        self.state.value = state

    def states(self, kind: str = "") -> list:
        return [entry[1] for entry in self.log if not kind or entry[0] == kind]


class FakeValidatePromocode:
    def __init__(self, known: tuple[str, ...] = (GIFT,)) -> None:
        self.known = known
        self.codes: list[str] = []

    async def __call__(self, actor: Any, dto: Any) -> Any:
        self.codes.append(dto.code)
        if dto.code not in self.known:
            raise PromocodeNotFoundError()
        return SimpleNamespace(code=dto.code, reward_type=PromocodeRewardType.SUBSCRIPTION, reward=30)


class FakeSubscriptionDao:
    async def get_current(self, user_id: Any) -> Any:
        return None


class FakePlanByCode:
    async def __call__(self, user: Any, code: str) -> Any:
        return SimpleNamespace(id=77) if code == "PLANCODE" else None


class World:
    """Один человек, один чат и всё, что обычно лежит в данных апдейта."""

    def __init__(self, *, accepted: bool = False, required: bool = True, redis: Any = None) -> None:
        self.user = SimpleNamespace(
            id=5, telegram_id=TG, log=f"[TG:{TG}]", is_rules_accepted=accepted, is_privileged=False
        )
        self.bot = FakeBot()
        self.redis = FakeRedis() if redis is None else redis
        self.accept = FakeAcceptRules()
        self.notifier = FakeNotifier()
        self.state = FakeState()
        self.dm = FakeDialogManager(self.state)
        self.validate = FakeValidatePromocode()
        self.container = FakeContainer(
            {
                CheckRules: FakeCheckRules(required),
                AcceptRules: self.accept,
                Notifier: self.notifier,
                Redis: self.redis,
            }
        )
        self.downstream: list[Any] = []

    def data(self) -> dict[str, Any]:
        return {
            "bot": self.bot,
            USER_KEY: self.user,
            CONTAINER_KEY: self.container,
            "dialog_manager": self.dm,
            "state": self.state,
            "event_from_user": FROM,
            "event_chat": CHAT,
            # В проде их подставит dishka из контейнера (см. docstring модуля).
            "validate_promocode": self.validate,
            "subscription_dao": FakeSubscriptionDao(),
            "notifier": self.notifier,
            "get_available_plan_by_code": FakePlanByCode(),
        }

    def message(self, text: str) -> Message:
        return Message(message_id=10, date=NOW, chat=CHAT, from_user=FROM, text=text).as_(self.bot)

    def accept_click(self) -> CallbackQuery:
        rules = Message(message_id=RULES_MSG_ID, date=NOW, chat=CHAT, from_user=BOT_USER, text="rules")
        return CallbackQuery(
            id="cb", from_user=FROM, chat_instance="ci", data=CALLBACK_RULES_ACCEPT,
            message=rules.as_(self.bot),
        ).as_(self.bot)

    async def send(self, event: Any) -> None:
        """Апдейт через мидлварь правил базы, а за ней — туда, куда его отправил бы aiogram."""

        async def downstream(inner_event: Any, inner_data: dict[str, Any]) -> Any:
            self.downstream.append(inner_event)
            if isinstance(inner_event, CallbackQuery):
                handler = accept_handler()
                passed, extra = await handler.check(inner_event, **inner_data)
                assert passed, "фильтр кнопки «Принять» перестал пропускать её колбэк"
                return await handler.call(inner_event, **{**inner_data, **extra})
            return await goto.router.message.trigger(inner_event, **inner_data)

        await rules_mw.RulesMiddleware().middleware_logic(downstream, event, self.data())


def accept_handler() -> Any:
    """Обработчик «Принять», как его держит роутер меню (после правки — наш)."""
    for handler in menu_handlers.router.callback_query.handlers:
        if getattr(handler.callback, "_overlay_wrapped", False):
            return handler
    raise AssertionError("обработчик «Принять» не заменён правкой")


def key() -> str:
    return rd.pending_key(TG)


# ── правка встала ───────────────────────────────────────────────────────────


def test_patches_applied_in_place():
    ours = {"deep link до правил: запомнить", "deep link до правил: выполнить после «Принять»"}
    assert not [f for f in overlay_patches.failures() if f[0] in ours]
    applied = overlay_patches.applied()
    assert all(any(entry.startswith(name + ": ") for entry in applied) for name in ours)
    assert getattr(rules_mw.RulesMiddleware.middleware_logic, "_overlay_wrapped", False)
    assert getattr(menu_handlers.on_rules_accept, "_overlay_wrapped", False)
    # В роутере наш обработчик на месте исходного, с тем же фильтром, и он один.
    wrapped = [h for h in menu_handlers.router.callback_query.handlers if getattr(h.callback, "_overlay_wrapped", False)]
    assert len(wrapped) == 1 and wrapped[0].varkw
    # Повторный apply ничего не ломает и не вешает вторую обёртку.
    assert rd.apply_remember() == "уже обёрнута"
    assert rd.apply_resume() == "уже заменён"


# ── главный сценарий: подарок новичку ───────────────────────────────────────


async def test_gift_link_before_rules_opens_promocode_after_accept():
    w = World()

    await w.send(w.message(f"/start promo_{GIFT}"))

    # Как в базе: правила показаны, дальше апдейт не пошёл.
    assert w.notifier.sent == ["ntf-requirement.rules-accept-required"]
    assert w.downstream == []
    # Аргумент отложен под этим человеком на час.
    assert w.redis.sets == [(key(), f"promo_{GIFT}", rd.PENDING_TTL)]
    assert rd.PENDING_TTL == 3600 and str(TG) in key()

    await w.send(w.accept_click())

    assert w.accept.calls == 1 and w.user.is_rules_accepted
    # Окно промокода с кодом — тем же путём, что у настоящего deep link.
    assert w.validate.codes == [GIFT]
    # Сначала базовое «Принять» (главное меню), следом окно промокода: отличить
    # «ссылка отказала» от «ссылка открыла окно» заранее нечем, а без меню человек
    # при отказе остался бы в пустом чате. Окно открывается с DELETE_AND_SEND и
    # заменяет меню, так что лишнего сообщения у человека не остаётся.
    assert w.dm.log == [
        ("start", MainMenu.MAIN, None, StartMode.RESET_STACK),
        (
            "bg.start",
            Subscription.PROMOCODE,
            {
                "prefill_dto": {"code": GIFT, "reward_type": "SUBSCRIPTION", "reward": 30},
                "prefill_replace": False,
            },
            StartMode.RESET_STACK,
        ),
    ]
    assert w.dm.states()[-1] == Subscription.PROMOCODE, "последним человек видит окно ссылки"
    # Аргумент снят, колбэк отвечен, сообщение с правилами убрано (как в базе).
    assert w.redis.live(key()) is None
    assert w.bot.called(AnswerCallbackQuery)
    assert [c.message_id for c in w.bot.called(DeleteMessage)] == [RULES_MSG_ID]


async def test_second_accept_opens_main_menu_once_consumed():
    w = World()
    await w.send(w.message(f"/start promo_{GIFT}"))
    await w.send(w.accept_click())
    w.dm.log.clear()

    await w.send(w.accept_click())

    assert w.dm.log == [("start", MainMenu.MAIN, None, StartMode.RESET_STACK)]
    assert w.validate.codes == [GIFT]  # второй раз код не проверялся


async def test_expired_pending_falls_back_to_main_menu():
    w = World()
    await w.send(w.message(f"/start promo_{GIFT}"))
    w.redis.now += rd.PENDING_TTL + 1

    await w.send(w.accept_click())

    assert w.dm.log == [("start", MainMenu.MAIN, None, StartMode.RESET_STACK)]
    assert w.validate.codes == []


async def test_latest_deeplink_wins():
    w = World()
    await w.send(w.message("/start promo_OLDCODE"))
    await w.send(w.message(f"/start promo_{GIFT}"))

    await w.send(w.accept_click())

    assert w.validate.codes == [GIFT]


# ── без побочек там, где правила не мешали ─────────────────────────────────


async def test_accepted_user_deeplink_works_as_before_and_redis_untouched():
    w = World(accepted=True)

    await w.send(w.message(f"/start promo_{GIFT}"))

    assert w.notifier.sent == []
    assert len(w.downstream) == 1
    assert w.dm.states("bg.start") == [Subscription.PROMOCODE]
    assert w.redis.sets == []


async def test_rules_not_required_nothing_deferred():
    w = World(required=False)

    await w.send(w.message(f"/start promo_{GIFT}"))

    assert w.dm.states("bg.start") == [Subscription.PROMOCODE]
    assert w.redis.sets == []


async def test_accept_without_pending_is_plain_main_menu():
    w = World()
    await w.send(w.message("/start"))  # правила показаны, откладывать нечего

    await w.send(w.accept_click())

    assert w.redis.sets == []
    assert w.dm.log == [("start", MainMenu.MAIN, None, StartMode.RESET_STACK)]


# ── что не откладываем ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "/start",
        "привет",
        "/start ref_AbC123",  # засчитан в UserMiddleware до правил
        "/start ad_spring",  # то же
        "/start promo_X extra",  # не deep link: Telegram шлёт один аргумент
        "/start " + "a" * 65,  # длиннее лимита Telegram
        "/start promo_<b>",  # чужие символы
        "/start@SomeBot promo_X",  # с упоминанием в личке не приходит
    ],
)
async def test_not_deferred(text):
    w = World()

    await w.send(w.message(text))

    assert w.notifier.sent == ["ntf-requirement.rules-accept-required"]
    assert w.redis.sets == []


def test_start_payload_parsing():
    w = World()
    assert rd.start_payload(w.message(f"/start promo_{GIFT}")) == f"promo_{GIFT}"
    assert rd.start_payload(w.message("/START invite")) == "invite"  # база: ignore_case=True
    assert rd.start_payload(w.message("/start promo")) == "promo"
    assert rd.start_payload(w.accept_click()) is None


# ── другие deep link базы тем же путём ─────────────────────────────────────


async def test_invite_and_plan_links_replayed_by_base_router():
    w = World()
    await w.send(w.message("/start invite"))
    await w.send(w.accept_click())
    # Главное меню базы, поверх него — окно приглашения (см. соседний тест о порядке).
    assert w.dm.log == [
        ("start", MainMenu.MAIN, None, StartMode.RESET_STACK),
        ("start", MainMenu.INVITE, None, StartMode.RESET_STACK),
    ]

    w2 = World()
    await w2.send(w2.message("/start plan_PLANCODE"))
    await w2.send(w2.accept_click())
    assert w2.dm.log == [
        ("start", MainMenu.MAIN, None, StartMode.RESET_STACK),
        ("bg.start", Subscription.PLAN, {"plan_id": 77}, StartMode.RESET_STACK),
    ]


async def test_unknown_payload_falls_back_to_main_menu():
    w = World()
    await w.send(w.message("/start somethingelse"))
    assert w.redis.live(key()) == "somethingelse"

    await w.send(w.accept_click())

    assert w.dm.log == [("start", MainMenu.MAIN, None, StartMode.RESET_STACK)]
    assert w.redis.live(key()) is None


async def test_used_gift_after_accept_explains_and_still_opens_menu():
    """Код уже недействителен: объясняем это И открываем меню.

    Обработчик deep link нашёлся, но отказал — окно не открылось. Сам отказ база
    присылает уведомлением, а оно самоудаляется; сообщение с правилами к этому
    моменту уже стёрто. Без меню новичок остался бы в пустом чате, с одним
    выходом — набрать /start руками.
    """
    w = World()
    w.validate.known = ()
    await w.send(w.message(f"/start promo_{GIFT}"))

    await w.send(w.accept_click())

    assert w.notifier.sent[-1] == "ntf-promocode.not-found"
    assert w.dm.log == [("start", MainMenu.MAIN, None, StartMode.RESET_STACK)]


# ── сбои хранилища не ломают правила ───────────────────────────────────────


async def test_redis_down_rules_still_shown_and_accept_opens_menu():
    w = World(redis=BrokenRedis())

    await w.send(w.message(f"/start promo_{GIFT}"))
    assert w.notifier.sent == ["ntf-requirement.rules-accept-required"]

    await w.send(w.accept_click())
    assert w.accept.calls == 1
    assert w.dm.log == [("start", MainMenu.MAIN, None, StartMode.RESET_STACK)]


def test_synthetic_start_is_from_same_person_and_chat():
    w = World()
    message = rd.synthetic_start(w.accept_click(), f"promo_{GIFT}", w.bot)
    assert message.text == f"/start promo_{GIFT}"
    assert message.from_user.id == TG and message.chat.id == TG and message.chat.type == "private"
    assert message.message_id == RULES_MSG_ID
