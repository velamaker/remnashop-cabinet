"""Оплата звёздами через НАСТОЯЩИЙ диспетчер и настоящие мидлвари базы
(overlay_patches/stars_payment.py).

ЧТО ЛОМАЛОСЬ. Сервисное сообщение `successful_payment` идёт в базе общим путём
любого сообщения — через проверки доступа, троттлинга, правил и канала. Не принял
правила, вышел из канала, бот на обслуживании, нажал что-то за полсекунду до
оплаты — проверка гасила сообщение, обработчик оплаты не выполнялся: звёзды
списаны, подписки нет, и Telegram этого сообщения больше не пришлёт.

ЧТО НАСТОЯЩЕЕ: Dispatcher aiogram, `setup_middlewares` базы целиком (те же классы,
в том же порядке, с той же правкой, что в проде), setup_dishka(auto_inject=True),
роутер оплаты базы `routers/extra/payment.py` с его фильтрами.
ЧТО ПОДДЕЛАНО: сессия бота (пишет вызовы API), сервисы в контейнере dishka —
выдача пользователя, проверки, уведомитель и сам ProcessPayment.
"""

import importlib
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from aiogram import F, Router
from aiogram.methods import AnswerPreCheckoutQuery
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery, SuccessfulPayment, Update

from _aiogram_harness import (  # noqa: E402 — соседний модуль тестов
    CHAT,
    FROM,
    TG,
    real_dispatcher,
    vendor_workflow_keys,
)

import overlay_patches  # noqa: E402

sp = importlib.import_module("overlay_patches.stars_payment")
mws = importlib.import_module("src.telegram.middlewares")
access_mw = importlib.import_module("src.telegram.middlewares.access")
user_mw = importlib.import_module("src.telegram.middlewares.user")
throttling_mw = importlib.import_module("src.telegram.middlewares.throttling")
rules_mw = importlib.import_module("src.telegram.middlewares.rules")
channel_mw = importlib.import_module("src.telegram.middlewares.channel")
payment = importlib.import_module("src.telegram.routers.extra.payment")

from src.application.dto import TelegramUserDto  # noqa: E402
from src.application.use_cases.access.queries.requirements import (  # noqa: E402
    CheckChannelSubscriptionResultDto,
    CheckRulesResultDto,
)
from src.core.enums import PaymentGatewayType, TransactionStatus  # noqa: E402

PAYMENT_ID = UUID("5b0c8a52-0d0e-4a57-9a3f-2f9c5e1d7a01")
RULES_PROMPT = "ntf-requirement.rules-accept-required"
THROTTLED = "ntf-common.throttling"


class World:
    """Что видит бот: человек, настройки проверок и записи о том, что произошло."""

    def __init__(self) -> None:
        self.user = TelegramUserDto(id=7, telegram_id=TG, name="Плательщик")
        self.access_allowed = True
        self.rules_required = False
        self.subscribed = True
        self.notified: list[str] = []
        self.processed: list[Any] = []
        self.probe: list[str] = []

    # UserMiddleware базы: человек уже есть в базе
    async def get_or_create(self, dto: Any) -> TelegramUserDto:
        return self.user

    async def update_profile(self, dto: Any) -> TelegramUserDto:
        return dto.user

    async def track(self, user_id: int) -> None:
        return None

    # ворота
    async def check_access(self, dto: Any) -> bool:
        return self.access_allowed

    async def check_rules(self, user: Any) -> CheckRulesResultDto:
        return CheckRulesResultDto(
            is_required=self.rules_required,
            is_accepted=user.is_rules_accepted,
            rules_url="https://rules.example",
        )

    async def accept_rules(self, user: Any) -> None:
        user.is_rules_accepted = True

    async def check_channel(self, user: Any) -> CheckChannelSubscriptionResultDto:
        return CheckChannelSubscriptionResultDto(
            is_subscribed=self.subscribed, channel_url="https://t.me/channel"
        )

    async def notify_user(self, user: Any = None, payload: Any = None, i18n_key: Any = None) -> None:
        self.notified.append(payload.i18n_key if payload is not None else i18n_key)

    # оплата
    async def process(self, dto: Any) -> None:
        self.processed.append(dto)


def _bindings(world: World) -> dict[type, Any]:
    # Типы берём из тех модулей, где их ищет код базы: правки overlay могут подменять
    # классы в модулях use case, а dishka сопоставляет зависимость по объекту типа.
    return {
        user_mw.GetOrCreateUser: SimpleNamespace(system=world.get_or_create),
        user_mw.UpdateUserProfile: SimpleNamespace(system=world.update_profile),
        user_mw.TrackUserActivity: SimpleNamespace(system=world.track),
        access_mw.CheckAccess: SimpleNamespace(system=world.check_access),
        rules_mw.CheckRules: world.check_rules,
        rules_mw.AcceptRules: world.accept_rules,
        channel_mw.CheckChannelSubscription: world.check_channel,
        rules_mw.Notifier: SimpleNamespace(notify_user=world.notify_user),
        payment.ProcessPayment: SimpleNamespace(system=world.process),
    }


@pytest.fixture
async def world():
    state = World()
    probe = Router(name="probe_ordinary_messages")

    @probe.message(F.text)
    async def on_text(message: Message) -> None:
        state.probe.append(message.text)

    workflow = {key: object() for key in vendor_workflow_keys()}
    async with real_dispatcher(
        [payment.router, probe], workflow=workflow, bindings=_bindings(state)
    ) as w:
        # Мидлвари базы целиком — ровно тот список и порядок, что в проде.
        mws.setup_middlewares(w.dp)
        w.state = state
        yield w


_update_id = 5000


async def _feed(w: Any, **event: Any) -> None:
    global _update_id
    _update_id += 1
    await w.dp.feed_update(w.bot, Update(update_id=_update_id, **event))


async def send_text(w: Any, text: str) -> None:
    await _feed(
        w,
        message=Message(
            message_id=_update_id, date=datetime.now(timezone.utc), chat=CHAT,
            from_user=FROM, text=text,
        ),
    )


async def pay(w: Any) -> None:
    await _feed(
        w,
        message=Message(
            message_id=_update_id, date=datetime.now(timezone.utc), chat=CHAT, from_user=FROM,
            successful_payment=SuccessfulPayment(
                currency="XTR",
                total_amount=150,
                invoice_payload=str(PAYMENT_ID),
                telegram_payment_charge_id="stxCHARGE",
                provider_payment_charge_id="",
            ),
        ),
    )


async def pre_checkout(w: Any) -> None:
    await _feed(
        w,
        pre_checkout_query=PreCheckoutQuery(
            id="pcq-1", from_user=FROM, currency="XTR", total_amount=150,
            invoice_payload=str(PAYMENT_ID),
        ),
    )


def _assert_paid_once(w: Any) -> None:
    assert len(w.state.processed) == 1, "обработчик оплаты не выполнился — звёзды потеряны"
    dto = w.state.processed[0]
    assert dto.payment_id == PAYMENT_ID
    assert dto.new_transaction_status == TransactionStatus.COMPLETED
    assert dto.gateway_type == PaymentGatewayType.TELEGRAM_STARS


# ── оплата доходит ───────────────────────────────────────────────────────────


async def test_payment_from_user_who_did_not_accept_rules(world):
    w = world
    w.state.rules_required = True

    await pay(w)

    _assert_paid_once(w)
    assert RULES_PROMPT not in w.state.notified, "вместо зачисления показали правила"

    # Обычное сообщение того же человека по-прежнему упирается в правила.
    await send_text(w, "привет")
    assert w.state.probe == []
    assert w.state.notified == [RULES_PROMPT]


async def test_payment_right_after_another_message_is_not_throttled(world):
    w = world

    await send_text(w, "первое")
    await pay(w)  # в те же полсекунды, что и первое сообщение

    _assert_paid_once(w)
    assert THROTTLED not in w.state.notified

    # Троттлинг жив: второе обычное сообщение в те же полсекунды гасится.
    await send_text(w, "второе")
    assert w.state.probe == ["первое"]
    assert w.state.notified == [THROTTLED]


async def test_payment_during_maintenance_and_outside_channel(world):
    w = world
    w.state.access_allowed = False
    w.state.subscribed = False

    await pay(w)
    _assert_paid_once(w)

    await send_text(w, "привет")
    assert w.state.probe == [], "обслуживание и канал перестали работать для обычных сообщений"


async def test_pre_checkout_is_answered_for_user_behind_every_gate(world):
    w = world
    w.state.access_allowed = False
    w.state.rules_required = True
    w.state.subscribed = False

    await pre_checkout(w)

    answers = w.bot.session.of(AnswerPreCheckoutQuery)
    assert [(a.pre_checkout_query_id, a.ok) for a in answers] == [("pcq-1", True)]


async def test_without_patch_payment_is_lost(world, monkeypatch: pytest.MonkeyPatch):
    """Доказательство, что тест выше проверяет правку, а не удачное стечение."""
    w = world
    for name in sp.GATES:
        cls = getattr(mws, name)
        monkeypatch.setattr(cls, "__call__", cls.__dict__["__call__"]._overlay_original)
    w.state.rules_required = True

    await pay(w)

    assert w.state.processed == []
    assert w.state.notified == [RULES_PROMPT]


# ── устройство правки ────────────────────────────────────────────────────────


def test_is_payment_update():
    now = datetime.now(timezone.utc)
    paid = Message(
        message_id=1, date=now, chat=CHAT, from_user=FROM,
        successful_payment=SuccessfulPayment(
            currency="XTR", total_amount=1, invoice_payload="x",
            telegram_payment_charge_id="c", provider_payment_charge_id="",
        ),
    )
    plain = Message(message_id=2, date=now, chat=CHAT, from_user=FROM, text="/start")
    query = PreCheckoutQuery(
        id="q", from_user=FROM, currency="XTR", total_amount=1, invoice_payload="x"
    )
    callback = CallbackQuery(id="c", from_user=FROM, chat_instance="ci", data="pay")
    assert sp.is_payment_update(paid)
    assert sp.is_payment_update(query)
    assert not sp.is_payment_update(plain)
    assert not sp.is_payment_update(callback)


def test_only_gates_are_opened():
    for name in sp.GATES:
        own = getattr(mws, name).__dict__.get("__call__")
        assert own is not None and getattr(own, sp._MARK, False), name
    # Пользователя кладёт UserMiddleware — без неё обработчику оплаты не с чем работать.
    assert not getattr(mws.UserMiddleware.__call__, sp._MARK, False)


def test_registered_in_install():
    applied = " | ".join(overlay_patches.applied())
    failed = {name for name, _ in overlay_patches.failures()}
    for name in ("оплата звёздами мимо проверок бота", "оплата звёздами: обработчики не менялись"):
        assert name in applied
        assert name not in failed


def test_changed_middleware_list_is_refused(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sp, "BASE_SETUP_MIDDLEWARES_SHA256", "0" * 64)
    with pytest.raises(overlay_patches.PatchTargetChanged, match="ИЗМЕНИЛ"):
        sp.apply_bypass()


def test_changed_payment_handler_is_refused(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(sp.BASE_HANDLERS_SHA256, "on_successful_payment", "0" * 64)
    with pytest.raises(overlay_patches.PatchTargetChanged, match="ИЗМЕНИЛ"):
        sp.check_handlers()
