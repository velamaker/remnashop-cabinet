"""Deep link, пришедший до принятия правил, выполняется после «Принять».

ЧТО ЧИНИМ. Когда в настройках включено «обязательно принять правила», мидлварь
правил базы (`RulesMiddleware`) у человека, который их ещё не принял, показывает
правила и ГАСИТ апдейт — `/start promo_<код>` до обработчика deep link не доходит.
После «Принять» обработчик кнопки (`on_rules_accept`) открывает главное меню, и то,
ради чего человек пришёл, теряется. Сильнее всего это бьёт по подаркам: сертификат
ведёт в бота ссылкой `?start=promo_<код>`, а получатель подарка — почти всегда
человек, который в боте впервые и правил ещё не видел.

КАК УСТРОЕНО. Две точки, один модуль:

  * `apply_remember` оборачивает `RulesMiddleware.middleware_logic`. Мы не копируем
    её тело, а подсовываем ей следящий `handler`: если база отдала управление
    дальше — всё как было; если НЕ отдала (единственная такая ветка — «правила не
    приняты, показали правила»), а апдейт был `/start <аргумент>`, запоминаем
    аргумент в Redis на час под telegram_id человека.

  * `apply_resume` подменяет обработчик кнопки «Принять» в роутере меню. Он забирает
    отложенный аргумент (один раз — GETDEL) и прогоняет синтетическое сообщение
    `/start <аргумент>` через роутер deep link базы (`routers/extra/goto.py`) —
    теми же фильтрами и теми же обработчиками, что и настоящее. Для `promo_<код>` это
    окно промокода с подставленным кодом и двойным подтверждением. Отложенного нет,
    истёк или роутер его не узнал — отрабатывает исходный обработчик (главное меню).

ПОЧЕМУ REDIS, А НЕ БАЗА И НЕ ПАМЯТЬ ПРОЦЕССА. Это черновик намерения на пару минут,
в нашей базе ему не место. Память процесса не годится: апдейты может обработать
запасная копия бэкенда (см. web_replica.py), и «Принять» пришло бы не туда, где
лежит аргумент. Redis у бота общий для обеих копий, ключ живёт с TTL и снимается
при первом же «Принять».

ЧТО НЕ ЗАПОМИНАЕМ. `ref_…` и `ad_…` база разбирает раньше правил — в `UserMiddleware`,
при создании пользователя (приглашение и рекламная ссылка засчитываются сразу, до
правил), а сам `/start ref_…` у принявшего правила ведёт в главное меню. Повторять
их незачем. Остальное (plan_, invite, promo, promo_<код>, будущие deep link базы)
идёт одним путём.

ВНЕ ЗОНЫ. Обязательная подписка на канал (`ChannelMiddleware`) гасит апдейт так же,
её эта правка не трогает: deep link, остановленный каналом, не запоминается, а если
после «Принять» человека остановил канал, отложенный аргумент просто доживает до TTL
(подтверждение канала открывает главное меню, как в базе).
"""

from __future__ import annotations

# Импорты модульного уровня — так надо: подменённые функции ищут имена в НАШИХ
# глобалях (см. expect_names_resolve), а dishka при старте разбирает аннотации
# обработчика через get_type_hints — тоже по нашим глобалям.
import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Final, Optional

from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.dispatcher.event.handler import CallableObject, HandlerObject
from aiogram.types import CallbackQuery, Chat, Message, TelegramObject
from loguru import logger
from redis.asyncio import Redis

from src.core.constants import CONTAINER_KEY, USER_KEY
from src.core.enums import Deeplink

from . import PatchTargetChanged, expect_source

# sha256 исходников базы v0.8.2 (вместе с декораторами), которые мы оборачиваем.
# Обёртка опирается на смысл «handler не вызван ⇔ показали правила», а замена
# кнопки — на то, что исходный обработчик только открывает главное меню.
BASE_RULES_LOGIC_SHA256 = "6627ced9e19964d57e17367f5abb40d7a837b999e460a58cf4e905699b5150d8"
BASE_RULES_ACCEPT_SHA256 = "a0dae9575d03032e33843c53afdbdd7feb643443c57bdfd01a16ef30965cde3a"

# Час: человек читает правила по ссылке и возвращается нажать «Принять» — это
# минуты. Дольше держать намерение незачем: вернувшийся позже просто откроет ссылку
# ещё раз (правила уже приняты — сработает сразу).
PENDING_TTL: Final[int] = 60 * 60
KEY_PREFIX: Final[str] = "overlay:start_after_rules"

# Аргумент deep link, как его отдаёт Telegram: до 64 символов из A-Z a-z 0-9 _ -.
# Упоминание бота (`/start@bot …`) в личке не приходит — такое не запоминаем.
# Регистр команды не важен: база ловит /start с ignore_case=True.
_START_RE: Final = re.compile(r"^/start\s+([A-Za-z0-9_-]{1,64})\s*$", re.IGNORECASE)

# Разобраны базой до правил (UserMiddleware → GetOrCreateUser), повторять нечего.
_HANDLED_BEFORE_RULES: Final[tuple[str, ...]] = (
    Deeplink.REFERRAL.with_underscore,
    Deeplink.ADVERTISING.with_underscore,
)

# Роутер deep link базы — ставится в apply_resume (к этому моменту он уже загружен:
# `src.telegram.routers` импортирует `extra` раньше `menu`).
_deeplink_router: Any = None


def pending_key(telegram_id: int) -> str:
    return f"{KEY_PREFIX}:{telegram_id}"


def start_payload(event: TelegramObject) -> Optional[str]:
    """Аргумент `/start`, который стоит отложить до «Принять», или None."""
    if not isinstance(event, Message) or not event.text:
        return None
    match = _START_RE.match(event.text)
    if match is None:
        return None
    payload = match.group(1)
    if payload.startswith(_HANDLED_BEFORE_RULES):
        return None
    return payload


def _kind(payload: str) -> str:
    """Для лога — только вид ссылки: код подарка сам по себе предъявительский."""
    head, sep, _ = payload.partition("_")
    return f"{head}{sep}…" if sep else head


async def _redis(container: Any) -> Any:
    return await container.get(Redis)


async def remember_pending(container: Any, telegram_id: Optional[int], payload: str) -> bool:
    if container is None or telegram_id is None:
        return False
    try:
        redis = await _redis(container)
        await redis.set(pending_key(telegram_id), payload, ex=PENDING_TTL)
    except Exception as exc:  # noqa: BLE001 — правила уже показаны, это главное
        logger.warning(f"deep link до правил не сохранён для '{telegram_id}': {exc}")
        return False
    return True


async def take_pending(container: Any, telegram_id: Optional[int]) -> Optional[str]:
    """Забрать отложенный аргумент. Один раз: GETDEL снимает ключ тем же вызовом."""
    if container is None or telegram_id is None:
        return None
    try:
        redis = await _redis(container)
        value = await redis.getdel(pending_key(telegram_id))
    except Exception as exc:  # noqa: BLE001 — без аргумента откроется главное меню
        logger.warning(f"отложенный deep link не прочитан для '{telegram_id}': {exc}")
        return None
    if value is None:
        return None
    return value.decode() if isinstance(value, bytes) else str(value)


def synthetic_start(event: CallbackQuery, payload: str, bot: Any) -> Message:
    """`/start <аргумент>` от того же человека в тот же чат, что и нажатие «Принять».

    Номер сообщения — у сообщения с правилами: мидлварь мусора базы попробует
    удалить «сообщение пользователя», и пусть это будет то, что и так уходит.
    """
    source = event.message
    chat = getattr(source, "chat", None) or Chat(id=event.from_user.id, type="private")
    message = Message(
        message_id=getattr(source, "message_id", 0) or 0,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=event.from_user,
        text=f"/start {payload}",
    )
    return message.as_(bot) if bot is not None else message


async def replay_start(event: CallbackQuery, payload: str, data: dict[str, Any]) -> bool:
    """Прогнать отложенный deep link через роутер базы. True — его кто-то обработал."""
    router = _deeplink_router
    if router is None:
        return False
    message = synthetic_start(event, payload, data.get("bot"))
    result = await router.message.trigger(message, **data)
    return result is not UNHANDLED


# ── запомнить: мидлварь правил ───────────────────────────────────────────────


def apply_remember() -> str:
    import src.telegram.middlewares.rules as target

    cls = getattr(target, "RulesMiddleware", None)
    original = getattr(cls, "middleware_logic", None) if cls is not None else None
    if original is None:
        raise PatchTargetChanged(
            "в src.telegram.middlewares.rules больше нет RulesMiddleware.middleware_logic — "
            "база перестроила проверку правил, deep link новичка снова потеряется"
        )
    if getattr(original, "_overlay_wrapped", False):
        return "уже обёрнута"

    expect_source(
        target, "RulesMiddleware.middleware_logic", BASE_RULES_LOGIC_SHA256,
        "RulesMiddleware.middleware_logic",
    )

    async def middleware_logic(
        self: Any,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        passed = False

        async def watched(inner_event: TelegramObject, inner_data: dict[str, Any]) -> Any:
            nonlocal passed
            passed = True
            return await handler(inner_event, inner_data)

        result = await original(self, watched, event, data)

        # База не пустила апдейт дальше — значит показала правила (другой такой ветки
        # в сверенном исходнике нет). Если это был deep link — откладываем его.
        if not passed:
            payload = start_payload(event)
            if payload is not None:
                user = data.get(USER_KEY)
                telegram_id = getattr(user, "telegram_id", None)
                if await remember_pending(data.get(CONTAINER_KEY), telegram_id, payload):
                    logger.info(
                        f"{getattr(user, 'log', telegram_id)} deep link '{_kind(payload)}' "
                        f"отложен до принятия правил"
                    )
        return result

    middleware_logic._overlay_wrapped = True  # type: ignore[attr-defined]
    cls.middleware_logic = middleware_logic
    return f"deep link до принятия правил запоминается на {PENDING_TTL // 60} мин"


# ── выполнить: кнопка «Принять» ──────────────────────────────────────────────


def apply_resume() -> str:
    global _deeplink_router

    import src.telegram.routers.menu.handlers as target
    import src.telegram.routers.extra.goto as goto

    original = getattr(target, "on_rules_accept", None)
    router = getattr(target, "router", None)
    if original is None or router is None:
        raise PatchTargetChanged(
            "в src.telegram.routers.menu.handlers больше нет on_rules_accept/router — "
            "база перестроила кнопку «Принять», отложенный deep link не выполнится"
        )
    if getattr(original, "_overlay_wrapped", False):
        return "уже заменён"

    expect_source(target, "on_rules_accept", BASE_RULES_ACCEPT_SHA256, "on_rules_accept")

    deeplink_router = getattr(goto, "router", None)
    if deeplink_router is None or not deeplink_router.message.handlers:
        raise PatchTargetChanged(
            "в src.telegram.routers.extra.goto больше нет роутера deep link с обработчиками "
            "сообщений — отложенному /start некуда идти"
        )

    handlers = router.callback_query.handlers
    index = next((i for i, h in enumerate(handlers) if h.callback is original), None)
    if index is None:
        raise PatchTargetChanged(
            "on_rules_accept не найден среди обработчиков колбэков роутера меню — "
            "база регистрирует кнопку «Принять» иначе"
        )

    registered = handlers[index]
    fallback = CallableObject(callback=original)

    # **data, а не именованные параметры: aiogram отдаёт такому обработчику ВСЕ данные
    # апдейта, а синтетическому /start нужны именно все (бот, контейнер dishka,
    # стек диалогов, пользователь) — ровно как настоящему сообщению от этого человека.
    async def on_rules_accept(callback: CallbackQuery, **data: Any) -> Any:
        user = data.get(USER_KEY)
        payload = await take_pending(data.get(CONTAINER_KEY), getattr(user, "telegram_id", None))

        # СНАЧАЛА базовое поведение (принять правила и открыть главное меню), и лишь
        # ПОТОМ отложенная ссылка. Порядок не косметика: обработчик ссылки может
        # найтись и ОТКАЗАТЬ — подарок уже активирован, промокод истёк, тариф
        # недоступен. Отказ база присылает уведомлением, а оно самоудаляется; если
        # бы меню не открылось, новичок остался бы в пустом чате (сообщение с
        # правилами к этому моменту стёрто). Отличить отказ от успеха заранее
        # нечем: окно открывается фоном, а подменить менеджер диалогов не выйдет —
        # диспетчер подставляет свой. При успехе меню не мешает: окно ссылки
        # открывается с DELETE_AND_SEND и заменяет его.
        result = await fallback.call(callback, **data)
        if payload is None:
            return result

        handled = await replay_start(callback, payload, data)
        try:
            # Кнопку нужно «отпустить», иначе у человека крутится часик. База это
            # делает не всегда, а после нашего проигрывания ссылки — тем более.
            # Повторный ответ Telegram не принимает, поэтому молча пропускаем.
            await callback.answer()
        except Exception:  # noqa: BLE001
            pass

        if handled:
            logger.info(
                f"{getattr(user, 'log', '')} после принятия правил выполнен отложенный "
                f"deep link '{_kind(payload)}'"
            )
        else:
            logger.info(
                f"{getattr(user, 'log', '')} отложенный deep link '{_kind(payload)}' никто не "
                f"обработал — остаётся главное меню"
            )
        return result

    on_rules_accept._overlay_wrapped = True  # type: ignore[attr-defined]

    # Роутер держит СВОЙ объект обработчика — замена имени в модуле его не касается.
    # Ставим новый на то же место (тот же фильтр, те же флаги), как это делает сама
    # dishka при auto_inject; имя в модуле тоже меняем, чтобы сторож имён видел нашу
    # функцию и чтобы повторный apply узнал, что замена уже стоит.
    handlers[index] = HandlerObject(
        callback=on_rules_accept, filters=registered.filters, flags=registered.flags
    )
    target.on_rules_accept = on_rules_accept
    _deeplink_router = deeplink_router
    return "после «Принять» выполняется отложенный deep link вместо главного меню"
