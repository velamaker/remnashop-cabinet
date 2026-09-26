"""Оплата звёздами доходит до обработчика, даже если проверки бота её бы остановили.

ЧТО ЧИНИМ. Когда человек платит звёздами, Telegram сначала спрашивает бота
«принять?» (`pre_checkout_query`), а после списания присылает сервисное сообщение
`successful_payment`. Подписку выдаёт ТОЛЬКО обработчик этого сообщения
(`on_successful_payment` в `routers/extra/payment.py`): он зовёт ProcessPayment.
А сообщение в базе идёт общим путём любого сообщения — через внешние мидлвари
диспетчера, и четыре из них умеют апдейт погасить, не отдав дальше:

  * `AccessMiddleware` — человек заблокирован, бот на обслуживании;
  * `ThrottlingMiddleware` — от человека было что-то в последние полсекунды;
  * `RulesMiddleware` — правила обязательны, а он их не принял (или их включили
    уже после того, как он открыл счёт);
  * `ChannelMiddleware` — подписка на канал обязательна, а он вышел из канала.

Погашенный `successful_payment` не повторяется: звёзды списаны, подписки нет, а в
логе — «throttled» или показанные правила, по которым оплату не найти.

КАК ЧИНИМ. Эти четыре — ворота, а не поставщики данных: пропуская апдейт, они
ничего не дописывают в `data`, что понадобилось бы обработчику оплаты (ему нужен
пользователь — его кладёт `UserMiddleware`, её мы НЕ трогаем). Поэтому на время
платёжного апдейта ворота просто открываем: их `__call__` отдаёт такой апдейт
дальше, не выполняя проверку. Остальное — как было: пользователь, контейнер dishka,
мидлварь мусора, обработка ошибок. Любой другой апдейт проходит проверки целиком.

Подменяем `__call__`, а не `middleware_logic`: это вход, по которому aiogram зовёт
мидлварь, — он не зависит от того, как база устроит проверку внутри. Логику правил
ещё и оборачивает rules_deeplink.py, и два слоя над одним методом друг другу бы
мешали, а над разными — нет.

`pre_checkout_query` сегодня не гасит никто (эти четыре на него не подписаны), но
ответить на него нужно за десять секунд, иначе Telegram отменит оплату. Ворота
открываем и для него — на случай, если база подпишет на него проверку.

ЧТО СВЕРЯЕМ. Состав и порядок мидлварей (`setup_middlewares`): появится новая
проверка — правка не встанет, пока человек не решит, может ли та погасить оплату.
И сами обработчики оплаты (`check_handlers`): начни они брать из `data` то, что
кладут ворота, открытые ворота сломали бы оплату — это тоже надо увидеть до деплоя.

БЕЗОПАСНОСТЬ. `successful_payment` и `pre_checkout_query` порождает сам Telegram,
подделать их через вебхук нельзя (у него секретный токен). Заблокированный человек
или заплативший во время обслуживания получает через открытые ворота только одно —
засчитанную оплату, за которую звёзды уже списаны; вернуть их владелец может сам.
"""

from __future__ import annotations

# Импорты модульного уровня — так надо: подменённые функции ищут имена в НАШИХ
# глобалях (см. expect_names_resolve).
from typing import Any, Awaitable, Callable, Final

from aiogram import BaseMiddleware
from aiogram.types import Message, PreCheckoutQuery, TelegramObject

from . import PatchTargetChanged, expect_source

# sha256 исходников базы v0.8.2 (вместе с декораторами).
BASE_SETUP_MIDDLEWARES_SHA256 = "2e4b8c7d0005a4f76b5dc0dda1d65226a2977ca4d8b521f6e93e604a44887903"
BASE_HANDLERS_SHA256: Final[dict[str, str]] = {
    "on_successful_payment": "f9074b93a03e04aea580bb359f1832a52a44178bc2499e539275e0e7f1875cd9",
    "on_pre_checkout": "0fa15f564d8dca3e475daf45f902960087b2c46d62c78d64238e4dfa72f56866",
}

# Ворота, которые умеют погасить апдейт. UserMiddleware сюда не входит намеренно:
# она кладёт в data пользователя, без которого обработчик оплаты не работает.
GATES: Final[tuple[str, ...]] = (
    "AccessMiddleware",
    "ThrottlingMiddleware",
    "RulesMiddleware",
    "ChannelMiddleware",
)

_MARK: Final[str] = "_overlay_payment_passthrough"


def is_payment_update(event: TelegramObject) -> bool:
    """Апдейт, на котором держится уже начатая оплата звёздами."""
    if isinstance(event, PreCheckoutQuery):
        return True
    return isinstance(event, Message) and event.successful_payment is not None


def _passthrough(original: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    async def __call__(
        self: Any,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if is_payment_update(event):
            return await handler(event, data)
        return await original(self, handler, event, data)

    setattr(__call__, _MARK, True)
    # Исходный вход — чтобы тест мог показать, что без правки оплата теряется.
    __call__._overlay_original = original  # type: ignore[attr-defined]
    return __call__


def apply_bypass() -> str:
    import src.telegram.middlewares as target

    if getattr(target, "setup_middlewares", None) is None:
        raise PatchTargetChanged(
            "в src.telegram.middlewares больше нет setup_middlewares — база перестроила "
            "подключение проверок, оплата звёздами снова может потеряться"
        )
    expect_source(
        target, "setup_middlewares", BASE_SETUP_MIDDLEWARES_SHA256,
        "состав мидлварей бота (оплата звёздами)",
    )

    classes = []
    for name in GATES:
        cls = getattr(target, name, None)
        if not isinstance(cls, type) or not issubclass(cls, BaseMiddleware):
            raise PatchTargetChanged(
                f"в src.telegram.middlewares нет класса {name} — база перестроила проверки, "
                "оплата звёздами снова может потеряться"
            )
        classes.append(cls)

    done = 0
    for cls in classes:
        # Смотрим в __dict__ класса, а не через getattr: унаследованный от базового
        # класса вход ещё не наш, даже если соседний класс уже обёрнут.
        own = cls.__dict__.get("__call__")
        if own is not None and getattr(own, _MARK, False):
            continue
        cls.__call__ = _passthrough(cls.__call__)
        done += 1

    if not done:
        return "уже стоит"
    return (
        "successful_payment и pre_checkout_query проходят мимо проверок доступа, "
        "троттлинга, правил и канала"
    )


def check_handlers() -> str:
    """Обработчики оплаты не меняем, но открытые ворота опираются на то, что им нужно."""
    import src.telegram.routers.extra.payment as target

    for qualname, sha in BASE_HANDLERS_SHA256.items():
        expect_source(target, qualname, sha, f"обработчик оплаты звёздами: {qualname}")

    router = getattr(target, "router", None)
    if router is None:
        raise PatchTargetChanged(
            "в src.telegram.routers.extra.payment больше нет router — база перенесла приём "
            "оплаты звёздами"
        )
    registered = {
        "on_successful_payment": router.message.handlers,
        "on_pre_checkout": router.pre_checkout_query.handlers,
    }
    for qualname, handlers in registered.items():
        callback = getattr(target, qualname, None)
        if not any(h.callback is callback for h in handlers):
            raise PatchTargetChanged(
                f"{qualname} не зарегистрирован в роутере оплаты так, как ожидалось — "
                "база иначе принимает оплату звёздами"
            )
    return "обработчики оплаты звёздами те же: им нужен только пользователь из UserMiddleware"
