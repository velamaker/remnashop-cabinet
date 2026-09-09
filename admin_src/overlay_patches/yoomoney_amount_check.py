"""Оплата на сумму меньше счёта больше не проводит счёт целиком.

ЧТО БЫЛО. `YoomoneyGateway.handle_webhook` проверяет подпись, берёт из вебхука
`label` и ВОЗВРАЩАЕТ статус COMPLETED, не глядя на сумму. Сколько человек
заплатил — известно (`withdraw_amount` — списано с плательщика), но это поле
используется только при расчёте HMAC и дальше выбрасывается. Ниже по течению
сверки тоже нет: `ProcessPayment` читает `transaction.pricing.final_amount`
исключительно для событий и уведомлений.

Итог: платёж на любую сумму по существующему `label` проводил счёт полностью.
ЮMoney — основной шлюз (65 успешных платежей из 89), так что цена вопроса не
теоретическая.

ЧЕГО ЕЩЁ НЕ ПРОВЕРЯЛОСЬ. `codepro` — перевод «с протекцией»: деньги можно
отозвать, пока получатель их не принял. `unaccepted` — перевод не принят вовсе.
И то, и другое приходило как обычная успешная оплата.

ПОЧЕМУ СРАВНИВАЕМ ИМЕННО `withdraw_amount`. В вебхуке две суммы: `amount` —
сколько ДОШЛО до кошелька (за вычетом комиссии), `withdraw_amount` — сколько
СПИСАНО с плательщика. Сверять надо со второй: комиссия всегда делает первую
меньше счёта, и проверка по ней отвергала бы каждый честный платёж. Проверено на
боевых данных: счёт 929 → `withdraw_amount` 929.00, `amount` 901.13; счёт 449 →
`withdraw_amount` 449.00, `amount` 435.53.

ПОЧЕМУ `<`, А НЕ `!=`. Переплата — не повод отказывать человеку в подписке:
деньги пришли, услугу выдаём, расхождение видит владелец в уведомлении.

ПОЧЕМУ ОБЁРТКА, А НЕ КОПИЯ. Внутри базового метода живёт проверка подписи —
самое security-критичное место шлюза. Переносить её к себе нельзя ни в каком
виде: разойдётся с апстримом молча. Зовём базу как есть и лишь смотрим на её
ответ. Тело вебхука к этому моменту уже прочитано, но Starlette кэширует его в
`request._body`, поэтому второе чтение бесплатно и безопасно.

ОТКАЗ НЕ МОЛЧАЛИВЫЙ. Возвращаем None — базовый эндпоинт тогда не ставит задачу
на выдачу и отвечает шлюзу как обычно, — и отдельно пишем владельцу в Telegram,
кто и сколько недоплатил. Деньги, которые мы не провели, обязаны быть видны.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from loguru import logger

from . import expect_source

# sha256 методов базы v0.8.2. Нам важен не только сам handle_webhook, но и
# _verify_webhook: если апстрим тронет проверку подписи, надо посмотреть глазами.
BASE_METHODS = {
    "YoomoneyGateway.handle_webhook": (
        "84b8c0a307bd13ac975438e8473becb3b88875675a0706015a5c731eaa5bd159"
    ),
    "YoomoneyGateway._verify_webhook": (
        "504f67d0e58a094b473f0e81a41bb852044bbc15ea70f5f1abbadf02e1349c49"
    ),
}


def _to_decimal(value: Any) -> Optional[Decimal]:
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError, AttributeError):
        return None


async def _invoice_amount(payment_id: Any) -> Optional[Decimal]:
    """Сумма счёта из нашей базы. None — счёта нет или сумма нечитаема."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from src.core.config import AppConfig

    engine = create_async_engine(AppConfig.get().database.dsn)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT pricing->>'final_amount' AS amount, user_id "
                        "FROM transactions WHERE payment_id = :pid"
                    ),
                    {"pid": str(payment_id)},
                )
            ).first()
    finally:
        await engine.dispose()

    if row is None:
        return None
    return _to_decimal(row.amount)


def verdict(
    data: dict, paid: Optional[Decimal], invoice: Optional[Decimal]
) -> tuple[bool, Optional[str]]:
    """Проводить ли платёж и что сказать владельцу.

    Вынесено из обёртки НАРОЧНО: обёртка сидит в замыкании apply() и позвать её в
    тесте нельзя, а решение о деньгах обязано быть проверяемым. Чистая функция —
    ни базы, ни бота, ни сети.

    Возвращает (проводить, текст владельцу|None).
    """
    for flag, why in (("codepro", "перевод с протекцией"), ("unaccepted", "перевод не принят")):
        if str(data.get(flag, "")).strip().lower() == "true":
            return False, (
                f"⚠️ Платёж не проведён: {why}.\n"
                f"Списано: {data.get('withdraw_amount')}\n\n"
                "Деньги пока не ваши — подписка не выдана."
            )

    # Не смогли прочитать одну из сумм — НЕ мешаем оплате. Отказ по незнанию хуже
    # пропуска: подпись уже проверена базой, а человек заплатил.
    if paid is None or invoice is None:
        return True, None

    if paid < invoice:
        return False, (
            f"⚠️ Недоплата — подписка НЕ выдана.\n"
            f"Списано: {paid} ₽, по счёту: {invoice} ₽\n\n"
            "Решите вручную: вернуть деньги или выдать подписку."
        )

    if paid > invoice:
        return True, (
            f"ℹ️ Переплата — подписка выдана.\n"
            f"Списано: {paid} ₽, по счёту: {invoice} ₽"
        )

    return True, None


def apply() -> str:
    import src.infrastructure.payment_gateways.yoomoney as target

    original = target.YoomoneyGateway.handle_webhook
    if getattr(original, "_overlay_wrapped", False):
        return "уже обёрнут"

    for qualname, sha in BASE_METHODS.items():
        expect_source(target, qualname, sha, qualname)

    async def _alert_owner(self, text_message: str) -> None:
        """Сказать владельцу. Никогда не мешает основному пути."""
        try:
            owner_id = self.config.bot.owner_id
            if owner_id:
                await self.bot.send_message(int(owner_id), text_message)
        except Exception as exc:  # noqa: BLE001 — не уведомили, но и не сломали
            logger.warning(f"ЮMoney: не смог предупредить владельца: {exc}")

    async def handle_webhook(self, request):
        result = await original(self, request)
        if result is None:
            return None

        payment_id, status = result

        # Данные вебхука читаем повторно: тело закэшировано Starlette.
        try:
            data = await self._get_webhook_data(request)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"ЮMoney: не смог перечитать вебхук для сверки суммы: {exc}")
            return result

        paid = _to_decimal(data.get("withdraw_amount"))
        invoice = await _invoice_amount(payment_id)
        allow, message = verdict(data, paid, invoice)

        if message:
            await _alert_owner(self, f"{message}\n\nСчёт: {payment_id}")

        if not allow:
            logger.warning(
                f"ЮMoney: платёж по счёту '{payment_id}' НЕ проведён "
                f"(списано={paid}, счёт={invoice})"
            )
            return None

        if paid is None or invoice is None:
            logger.warning(f"ЮMoney: сверка суммы пропущена для '{payment_id}'")

        return payment_id, status

    handle_webhook._overlay_wrapped = True  # type: ignore[attr-defined]
    target.YoomoneyGateway.handle_webhook = handle_webhook
    return "сумма платежа сверяется со счётом"
