"""Предупреждение «на балансе не хватит на автопродление» (overlay).

ЗАЧЕМ. Автопродление списывает с рублёвого баланса за N дней до конца подписки
(tasks/autopay.py, N = AUTOPAY_DAYS_BEFORE). Денег не хватило — крон молча ничего не
делает: выборка требует `cabinet_balance > 0`, а само списание отваливается на
`WHERE cabinet_balance >= :amt`. Человек при этом уверен, что подписка продлится сама:
тумблер включён. Замер на бою: баланс больше нуля у троих из 1124, то есть молчание
здесь — правило, а не редкий случай.

Поэтому за СУТКИ до попытки списания уходит ОДНО сообщение: сколько на балансе, сколько
нужно, до какого числа пополнить — и кнопка пополнения ровно на недостающую сумму.

ЧЕГО ЗДЕСЬ НЕТ И ПОЧЕМУ:
  * досылок и повторов: не пополнил после одного напоминания — второе читается как
    выпрашивание денег, а подписка и так продлевается вручную в два касания;
  * предупреждения тем, у кого денег ХВАТАЕТ: им списание пройдёт, и сообщение было бы
    ложной тревогой;
  * своего расчёта цены: сумму берём из `renewal_quote` — той же функции, которой
    считает само списание, иначе назвали бы человеку не ту сумму.

Повтор не даёт таблица `autopay_warnings` (миграция 0014): ключ — подписка, а `sent_for`
хранит срок, к которому относилось предупреждение. Продлился — срок уехал вперёд, и на
новый период человек снова имеет право на предупреждение.

Задача обнаруживается taskiq по глобу tasks/*.py — регистрировать её негде.
"""

import datetime as dt
import os
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Notifier
from src.application.common.dao import PaymentGatewayDao, SubscriptionDao, UserDao
from src.application.dto import MessagePayloadDto
from src.application.services import PricingService
from src.application.use_cases.plan.queries.match import MatchPlan
from src.application.use_cases.user.queries.plans import GetAvailablePlans
from src.infrastructure.services.overlay_balance import renewal_quote
from src.infrastructure.services.overlay_push import notify_user_push
from src.infrastructure.taskiq.broker import broker

#: Сколько человек разбираем за проход. Крон почасовой, хвост уедет следующим.
MAX_PER_RUN = 200

#: За сколько часов до попытки списания предупреждаем. Сутки — чтобы человек успел
#: дойти до оплаты, но не забыл, о чём речь.
WARN_AHEAD_HOURS = 24

# Кандидаты: автопродление включено, подписка активна и её срок попадает в окно
# «списание завтра». Баланс НЕ фильтруем в SQL: цену знает только расчёт тарифа,
# и «мало денег» определяется сравнением с ней, а не с нулём.
CANDIDATES_SQL = """
SELECT u.id AS user_id, s.id AS subscription_id, s.expire_at, u.cabinet_balance
  FROM users u
  JOIN subscriptions s ON u.current_subscription_id = s.id
 WHERE u.autopay_enabled = true
   AND s.status::text = 'ACTIVE'
   AND coalesce(u.is_blocked, false) = false
   AND s.expire_at >= now() + make_interval(days => :days)
   AND s.expire_at < now() + make_interval(days => :days, hours => :hours)
   AND NOT EXISTS (
         SELECT 1 FROM autopay_warnings w
          WHERE w.subscription_id = s.id AND w.sent_for = s.expire_at
       )
   AND NOT EXISTS (
         SELECT 1 FROM notification_optouts o
          WHERE o.user_id = u.id AND o.kind = 'autopay_warning'
       )
 ORDER BY s.expire_at
 LIMIT :limit
"""

MARK_SQL = """
INSERT INTO autopay_warnings (subscription_id, user_id, sent_for, short_by)
VALUES (:subscription_id, :user_id, :sent_for, :short_by)
ON CONFLICT (subscription_id) DO UPDATE
   SET sent_for = EXCLUDED.sent_for,
       short_by = EXCLUDED.short_by,
       sent_at  = now()
"""


def _enabled() -> bool:
    return (os.environ.get("AUTOPAY_ENABLED") or "true").strip().lower() == "true"


def _days_before() -> int:
    try:
        return max(1, int(os.environ.get("AUTOPAY_DAYS_BEFORE") or "3"))
    except ValueError:
        return 3


def _cabinet_url() -> str:
    return (os.environ.get("WEB_CABINET_URL") or "").strip().rstrip("/")


def topup_link(short_by: Decimal) -> str:
    """Ссылка на пополнение РОВНО на недостающую сумму (округляем вверх до рубля)."""
    base = _cabinet_url()
    amount = int(short_by) + (1 if short_by % 1 else 0)
    return f"{base}/balance?topup={max(amount, 1)}" if base else ""


def message_text(
    *, price: Decimal, balance: Decimal, short_by: Decimal, date: str, link: str, lang: str = "ru"
) -> str:
    """Текст предупреждения. Деньги называем все три: есть, нужно, не хватает."""
    if lang == "en":
        body = (
            "⚠️ Auto-renewal won't go through\n\n"
            f"We'll charge {price} ₽ on {date}, but your balance is {balance} ₽ — "
            f"{short_by} ₽ short.\n\n"
            "Top up before that date and the subscription renews by itself."
        )
        return f"{body}\n\n{link}" if link else body
    body = (
        "⚠️ Автопродление не пройдёт\n\n"
        f"{date} спишем {price} ₽, а на балансе {balance} ₽ — не хватает {short_by} ₽.\n\n"
        "Пополните до этой даты, и подписка продлится сама."
    )
    return f"{body}\n\n{link}" if link else body


async def _tell(notifier: Notifier, user: Any, content: str) -> None:
    """Сообщение человеку. Никогда не роняет проход."""
    try:
        await notifier.notify_user(
            user,
            payload=MessagePayloadDto(
                i18n_key="raw-message",
                i18n_kwargs={"content": content},
                # Без этого сообщение самоуничтожится через 5 секунд (дефолт payload).
                delete_after=None,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"autopay-warning: сообщение user_id={getattr(user, 'id', '?')} не ушло: {exc}")


@broker.task(schedule=[{"cron": "23 * * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def run_autopay_warning(
    session: FromDishka[AsyncSession],
    user_dao: FromDishka[UserDao],
    notifier: FromDishka[Notifier],
    subscription_dao: FromDishka[SubscriptionDao],
    payment_gateway_dao: FromDishka[PaymentGatewayDao],
    pricing_service: FromDishka[PricingService],
    get_available_plans: FromDishka[GetAvailablePlans],
    match_plan: FromDishka[MatchPlan],
) -> None:
    if not _enabled():
        return

    days = _days_before()
    rows = (
        await session.execute(
            text(CANDIDATES_SQL),
            {"days": days, "hours": WARN_AHEAD_HOURS, "limit": MAX_PER_RUN},
        )
    ).all()
    if not rows:
        return

    warned = 0
    for row in rows:
        user = await user_dao.get_by_id(row.user_id)
        if not user:
            continue
        try:
            quote = await renewal_quote(
                user,
                subscription_dao=subscription_dao,
                payment_gateway_dao=payment_gateway_dao,
                pricing_service=pricing_service,
                get_available_plans=get_available_plans,
                match_plan=match_plan,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"autopay-warning: цена для user_id={row.user_id} не посчиталась: {exc}")
            continue
        if quote is None:
            continue  # тариф снят с продажи или нет ₽-шлюза — списания и так не будет

        balance = Decimal(str(row.cabinet_balance or 0))
        if balance >= quote.price:
            continue  # денег хватает — предупреждать не о чем

        short_by = quote.price - balance
        # Дата попытки списания = срок минус N дней, в том же виде, что и в кабинете.
        charge_at = row.expire_at - dt.timedelta(days=days)
        date = charge_at.strftime("%d.%m.%Y")
        lang = (getattr(user, "language", None) or "ru").lower()
        link = topup_link(short_by)
        await _tell(
            notifier,
            user,
            message_text(
                price=quote.price,
                balance=balance,
                short_by=short_by,
                date=date,
                link=link,
                lang="en" if lang.startswith("en") else "ru",
            ),
        )
        await notify_user_push(
            session,
            SimpleNamespace(id=user.id, language=getattr(user, "language", None)),
            {
                "ru": ("⚠️ Не хватит на автопродление", "Нужно {price} ₽, на балансе {balance} ₽."),
                "en": ("⚠️ Not enough for auto-renewal", "Need {price} ₽, balance is {balance} ₽."),
            },
            url="/balance",
            tag="autopay-warning",
            price=quote.price,
            balance=balance,
        )
        await session.execute(
            text(MARK_SQL),
            {
                "subscription_id": row.subscription_id,
                "user_id": row.user_id,
                "sent_for": row.expire_at,
                "short_by": short_by,
            },
        )
        await session.commit()
        warned += 1

    if warned:
        logger.info(f"Autopay-warning: предупреждено людей: {warned} из {len(rows)} кандидатов")


__all__ = ["run_autopay_warning", "message_text", "topup_link", "MAX_PER_RUN", "WARN_AHEAD_HOURS"]
