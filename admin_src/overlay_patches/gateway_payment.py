"""Пополнение ₽-баланса через шлюз и подарок, оплаченный шлюзом.

ЧТО ДЕЛАЕМ. Успешный платёж у базы означает ровно одно — выдать подписку. У нас
через тот же шлюз проходят ещё два случая: пополнение рублёвого баланса кабинета
и оплата подарка другому человеку. Оба опознаются по нашей собственной отметке,
привязанной к идентификатору платежа, и обрабатываются ДО обычной ветки: иначе
человек, пополнивший баланс, получил бы вместо денег подписку.

ПОЧЕМУ ЗАМЕНА ДВУХ МЕТОДОВ, А НЕ КОПИЯ ФАЙЛА. Раньше overlay держал копию всего
payment.py (608 строк). Меняются в нём ровно два места: конструктор, которому
нужна сессия базы для наших отметок, и обработчик успешной оплаты. Остальное —
проверка подписи, идемпотентность, возвраты, события — приезжает из базы как есть.

ТРЕТЬЯ ПРАВКА — ОПОЗДАВШИЙ ПЛАТЁЖ. Счёт живёт 30 минут (крон гасит PENDING), а
ссылка ЮMoney платится вечно. Заплатил через час — вебхук приходит на CANCELED,
переход в COMPLETED разрешён только из PENDING/FAILED, и деньги молча пропадают:
шлюзу отвечено 200, человеку не сказано ничего, владельцу тоже. Реальный случай
29.08: 929 ₽ пришли на кошелёк, бот их не увидел (транзакция ee0e36b6…).
Здесь мы НЕ переписываем `_execute`, а оборачиваем: перед вызовом базы поднимаем
отменённый счёт обратно в PENDING, и дальше отрабатывает её собственная,
нетронутая логика. Копировать в денежном пути нечего — ровно то, чему научил
`NameError` в подтверждении подарка.

ЧЕТВЁРТАЯ — ОТЧЁТ О ПЕРЕНОСЕ ОСТАТКА. Смену тарифа с переносом выполняет правка
покупки (plan_change_carryover.py), а владельцу о ней говорит этот обработчик: после
успешной выдачи CHANGE — уведомление с числами, сбой расчёта — «остаток не пересчитан,
добавьте вручную», правка покупки не встала в ЭТОМ процессе (непересобранный воркер) —
алерт ровно в момент вреда. Возврат (REFUNDED) по платежу, из которого дни ушли в
перенос, — алерт с подпиской и днями: база подписку при возврате не отзывает.
Всё это best-effort в `try`: уведомление не имеет права сорвать оплаченную выдачу.

ЭТО ДЕНЕЖНЫЙ ПУТЬ, поэтому сверка исходника обязательна: апстрим правит что-то
внутри `_handle_success` — правка не применяется и кричит, а не подменяет молча
изменившуюся логику зачисления.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from uuid import UUID

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

# не требуется — overlay_topup не тянет application-слой на верхнем уровне.
from src.infrastructure.services.overlay_gift import try_issue_gift
from src.infrastructure.services.overlay_topup import try_credit_topup
# Сервис переноса остатка: только stdlib/sqlalchemy/loguru, как и два выше.
from src.infrastructure.services import overlay_plan_change as carry

from src.application.common import (
    EventPublisher,
    Interactor,
    Notifier,
    Redirect,
    TranslatorHub,
)
from src.application.common.dao import (
    PaymentGatewayDao,
    ReferralDao,
    SubscriptionDao,
    TransactionDao,
    UserDao,
)
from src.application.common.policy import Permission
from src.application.common.uow import UnitOfWork
from src.application.dto import (
    MessagePayloadDto,
    PaymentResultDto,
    PlanSnapshotDto,
    PriceDetailsDto,
    TransactionDto,
    UserDto,
)
from src.application.dto.payment_gateway import (
    CryptomusGatewaySettingsDto,
    CryptoPayGatewaySettingsDto,
    FreeKassaGatewaySettingsDto,
    HeleketGatewaySettingsDto,
    MulenPayGatewaySettingsDto,
    PayMasterGatewaySettingsDto,
    PaymentGatewayDto,
    PlategaGatewaySettingsDto,
    RoboKassaGatewaySettingsDto,
    TelegramStarsGatewaySettingsDto,
    UrlPayGatewaySettingsDto,
    ValutixGatewaySettingsDto,
    WataGatewaySettingsDto,
    YooKassaGatewaySettingsDto,
    YooMoneyGatewaySettingsDto,
)
from src.application.events import UserPurchaseEvent
from src.application.use_cases.gateways.queries.providers import GetPaymentGatewayInstance
from src.application.use_cases.referral.commands.rewards import (
    AssignReferralRewards,
    AssignReferralRewardsDto,
)
from src.application.use_cases.subscription.commands.purchase import (
    PurchaseSubscription,
    PurchaseSubscriptionDto,
)
from src.core.enums import (
    Currency,
    PaymentGatewayType,
    PurchaseType,
    Role,
    SystemNotificationType,
    TransactionStatus,
)
from src.core.exceptions import PurchaseError
from src.core.utils.i18n_helpers import (
    i18n_format_days,
    i18n_format_device_limit,
    i18n_format_traffic_limit,
)

from . import PatchTargetChanged, expect_source
from .plan_change_carryover import outcome_for

# sha256 методов базы v0.8.2.
BASE_METHODS = {
    "ProcessPayment.__init__": "2053f2c9cb98f384efb6968fa059ee966c4010317c0279477d8e2008fc84820f",
    "ProcessPayment._handle_success": "acf75f43ae87e34039923870a8895897da0ab2884ce22a6da3502980a21b52b5",
    # Оборачиваем, а не заменяем, но сверяем всё равно: наша обёртка опирается на
    # то, ЧТО база делает со статусами (переход разрешён из PENDING/FAILED).
    # Апстрим поменяет набор — поднимать счёт в PENDING станет бессмысленно.
    "ProcessPayment._execute": "a7edbdbbbff93e2c58eee96289a296b5940d1994450004fa8cc3ca4164f1c1e0",
}


async def _notify_admins_raw(self, content: str) -> None:
    await self.notifier.notify_admins(
        MessagePayloadDto(
            i18n_key="raw-message",
            i18n_kwargs={"content": content},
            # Без этого сообщение самоуничтожится через 5 секунд (дефолт payload).
            delete_after=None,
        )
    )


def _days_left(subscription) -> "int | None":
    expire = getattr(subscription, "expire_at", None)
    if expire is None or getattr(subscription, "is_unlimited", False):
        return None
    from src.core.utils.time import datetime_now

    return max(0, int((expire - datetime_now()).total_seconds()) // carry.DAY)


async def _after_change(self, user: UserDto, transaction: TransactionDto, before) -> None:
    """Сказать владельцу, чем кончилась смена тарифа. Только после выдачи, только в try."""
    config = carry.load_config()
    if not config.get("enabled") or before is None or getattr(before, "is_trial", False):
        return

    outcome = outcome_for(self.purchase_subscription, transaction.payment_id)
    if outcome is None:
        # Выключатель включён, CHANGE не-триала, а наша ветка не работала: правка покупки
        # в этом процессе не встала (или ей не дали сессию) — база сожгла остаток.
        logger.error(
            f"carry: перенос не применился в процессе {carry.process_name()} "
            f"(счёт '{transaction.payment_id}', user {user.log})"
        )
        await _notify_admins_raw(self, carry.admin_not_applied_text(user.log, _days_left(before)))
        return
    if outcome.get("skipped") or not outcome.get("applied"):
        return

    result = outcome["result"]
    if outcome.get("load_error"):
        await _notify_admins_raw(
            self, carry.admin_failed_text(user.log, outcome["load_error"], result.remaining_days)
        )
        return
    if outcome.get("record_error"):
        await _notify_admins_raw(
            self,
            carry.admin_unrecorded_text(user.log, result, outcome["record_error"], transaction.payment_id),
        )
        return
    if result.mode != "refund" and not config.get("notify_admins"):
        return
    old_plan = getattr(before, "plan_snapshot", None)
    await _notify_admins_raw(
        self,
        carry.admin_carry_text(
            user.log,
            outcome.get("old_plan_name") or getattr(old_plan, "name", "—"),
            transaction.plan_snapshot.name,
            transaction.plan_snapshot.duration,
            result,
            transaction.payment_id,
        ),
    )


async def _status_before_refund(self, payment_id) -> "str | None":
    session = getattr(self, "session", None)
    if session is None:
        return None
    try:
        return await carry.transaction_status(session, payment_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"carry: статус счёта '{payment_id}' до возврата не прочитан: {exc}")
        return None


async def _alert_refunded_carry(self, data, before: "str | None") -> None:
    """Возврат по платежу, чьи дни ушли в перенос: база подписку не отзывает — владелец решает.

    Алертим, только если переход РЕАЛЬНО случился (был COMPLETED, стал REFUNDED): база
    при несовпавшем переходе молча выходит, и повтор вебхука не должен слать второй алерт.
    """
    if before != TransactionStatus.COMPLETED.value:
        return
    session = self.session
    after = await carry.transaction_status(session, data.payment_id)
    if after != TransactionStatus.REFUNDED.value:
        return
    carries = await carry.carries_by_source_payment(session, data.payment_id)
    if not carries:
        return
    await _notify_admins_raw(self, carry.admin_refund_text(data.payment_id, carries))


def apply() -> str:
    import src.application.use_cases.gateways.commands.payment as target

    if getattr(target.ProcessPayment.__init__, "_overlay_wrapped", False):
        return "уже заменены"

    for qualname, sha in BASE_METHODS.items():
        expect_source(target, qualname, sha, qualname)

    def ProcessPayment_init(
        self,
        uow: UnitOfWork,
        user_dao: UserDao,
        transaction_dao: TransactionDao,
        subscription_dao: SubscriptionDao,
        referral_dao: ReferralDao,
        event_publisher: EventPublisher,
        notifier: Notifier,
        redirect: Redirect,
        assign_referral_rewards: AssignReferralRewards,
        purchase_subscription: PurchaseSubscription,
        session: AsyncSession,
    ) -> None:
        self.uow = uow
        self.user_dao = user_dao
        self.transaction_dao = transaction_dao
        self.subscription_dao = subscription_dao
        self.referral_dao = referral_dao
        self.event_publisher = event_publisher
        self.notifier = notifier
        self.redirect = redirect
        self.assign_referral_rewards = assign_referral_rewards
        self.purchase_subscription = purchase_subscription
        # OVERLAY: сессия для ветки пополнения баланса (try_credit_topup).
        self.session = session


    async def ProcessPayment_handle_success(self, user: UserDto, transaction: TransactionDto) -> None:
        if transaction.is_test:
            await self.notifier.notify_user(user, i18n_key="ntf-gateway.test-payment-confirmed")
            return

        # OVERLAY (вариант B): пополнение ₽-баланса через шлюз. Если этот платёж —
        # пополнение (есть строка в balance_topups), зачисляем баланс+бонус и
        # ВЫХОДИМ до подписки/события покупки/рефералки/кэшбэка. Ядро в остальном
        # нетронуто. Идемпотентно (флаг credited).
        credited = await try_credit_topup(self.session, transaction.payment_id)
        if credited is not None:
            bonus = credited["bonus"]
            bonus_part = f" (+{bonus} ₽ бонус)" if bonus > 0 else ""
            await self.notifier.notify_user(
                user,
                payload=MessagePayloadDto(
                    i18n_key="raw-message",
                    i18n_kwargs={
                        "content": (
                            f"✅ Баланс пополнен на {credited['total']} ₽{bonus_part}."
                        )
                    },
                ),
            )
            logger.info(
                f"Balance topup credited '{credited['total']}' for user {user.log}, "
                f"transaction '{transaction.payment_id}'"
            )
            return

        # OVERLAY: подарок, оплаченный через шлюз. Если платёж помечен в
        # gift_payments — выпускаем код и ВЫХОДИМ: подписку покупателю не выдаём,
        # событие покупки/рефералку/кэшбэк не трогаем. Идемпотентно (флаг issued).
        gift = await try_issue_gift(self.session, transaction.payment_id)
        if gift is not None:
            # Убираем сообщение бота с кнопкой «Перейти к оплате»: счёт уже оплачен,
            # а кнопка на нём остаётся рабочей на вид и путает покупателя.
            if gift.get("chat_id") and gift.get("message_id"):
                try:
                    await self.notifier.delete_notification(
                        chat_id=int(gift["chat_id"]), message_id=int(gift["message_id"])
                    )
                except Exception as exc:  # noqa: BLE001 — не мешаем выдаче кода
                    logger.warning(f"Gift: не удалил сообщение с оплатой: {exc}")
            await self.notifier.notify_user(
                user,
                payload=MessagePayloadDto(
                    i18n_key="raw-message",
                    i18n_kwargs={
                        "content": (
                            f"🎁 Подарок оплачен: {gift['plan_name']} на {gift['duration_days']} дн.\n"
                            f"Код для получателя:\n<code>{gift['code']}</code>\n\n"
                            "Он вводит его в разделе «Промокод»."
                        )
                    },
                    # ОБЯЗАТЕЛЬНО: по умолчанию у payload delete_after=5 — сообщение с
                    # кодом самоуничтожалось через 5 секунд, и код терялся навсегда.
                    delete_after=None,
                ),
            )
            logger.info(
                f"Gift issued '{gift['code']}' for user {user.log}, "
                f"transaction '{transaction.payment_id}'"
            )
            return

        subscription = await self.subscription_dao.get_current(user.id)
        old_plan = subscription.plan_snapshot if subscription else None

        event = UserPurchaseEvent(
            user_id=user.id,
            telegram_id=user.telegram_id,
            name=user.name,
            email=user.email,
            username=user.username,
            #
            purchase_type=transaction.purchase_type,
            is_trial_plan=transaction.plan_snapshot.is_trial,
            payment_id=transaction.payment_id,
            gateway_type=transaction.gateway_type,
            final_amount=transaction.pricing.final_amount,
            discount_percent=transaction.pricing.discount_percent,
            original_amount=transaction.pricing.original_amount,
            currency=transaction.currency.symbol,
            #
            plan_name=(transaction.plan_snapshot.name, {}),
            plan_type=transaction.plan_snapshot.type,
            plan_traffic_limit=i18n_format_traffic_limit(transaction.plan_snapshot.traffic_limit),
            plan_device_limit=i18n_format_device_limit(transaction.plan_snapshot.device_limit),
            plan_duration=i18n_format_days(transaction.plan_snapshot.duration),
            #
            previous_plan_name=(old_plan.name, {}) if old_plan else "N/A",
            previous_plan_type={
                "key": "plan-type",
                "plan_type": old_plan.type if old_plan else "N/A",
            },
            previous_plan_traffic_limit=i18n_format_traffic_limit(old_plan.traffic_limit)
            if old_plan
            else "N/A",
            previous_plan_device_limit=i18n_format_device_limit(old_plan.device_limit)
            if old_plan
            else "N/A",
            previous_plan_duration=i18n_format_days(old_plan.duration) if old_plan else "N/A",
        )

        try:
            await self.purchase_subscription.system(
                PurchaseSubscriptionDto(user, transaction, subscription)
            )
        except Exception as e:
            logger.exception(
                f"Failed to process purchase for user '{user.remna_name}', "
                f"transaction '{transaction.payment_id}'"
            )
            async with self.uow:  # fresh UoW, no nesting
                await self.transaction_dao.update_status(
                    transaction.payment_id, TransactionStatus.FAILED
                )
                await self.uow.commit()
            await self.notifier.notify_system(
                MessagePayloadDto(
                    i18n_key="event-payment.purchase-failed",
                    i18n_kwargs={
                        "payment_id": str(transaction.payment_id),
                        "gateway_type": transaction.gateway_type,
                        "final_amount": transaction.pricing.final_amount,
                        "original_amount": transaction.pricing.original_amount,
                        "discount_percent": transaction.pricing.discount_percent,
                        "currency": transaction.currency.symbol,
                        "telegram_id": user.telegram_id or 0,
                        "username": user.username or 0,
                        "name": user.name,
                        "email": user.email,
                    },
                ),
                roles=[Role.OWNER, Role.DEV],
                notification_type=SystemNotificationType.SYSTEM,
            )
            if user.telegram_id is not None:
                await self.redirect.to_failed_payment(user.telegram_id)
            raise PurchaseError(e)

        # OVERLAY: отчёт о переносе остатка при смене тарифа (best-effort, после выдачи).
        if transaction.purchase_type == PurchaseType.CHANGE:
            try:
                await _after_change(self, user, transaction, subscription)
            except Exception:  # noqa: BLE001 — уведомление не срывает выдачу
                logger.exception(
                    f"carry: отчёт о смене тарифа не отправлен (счёт '{transaction.payment_id}')"
                )

        await self.event_publisher.publish(event)

        if not transaction.pricing.is_free:
            # The purchase is already COMPLETED and committed. Referral rewards are
            # best-effort: their failure must not break the successful purchase nor
            # leave the transaction in a non-terminal state for retry. Isolate it.
            try:
                await self.assign_referral_rewards.system(
                    AssignReferralRewardsDto(user, transaction)
                )
            except Exception:
                logger.exception(
                    f"Referral reward assignment failed for user '{user.remna_name}', "
                    f"transaction '{transaction.payment_id}' — purchase succeeded"
                )
                await self.notifier.notify_admins(
                    MessagePayloadDto(
                        i18n_key="event-payment.referral-failed",
                        i18n_kwargs={
                            "payment_id": str(transaction.payment_id),
                            "gateway_type": transaction.gateway_type,
                            "final_amount": transaction.pricing.final_amount,
                            "original_amount": transaction.pricing.original_amount,
                            "discount_percent": transaction.pricing.discount_percent,
                            "currency": transaction.currency.symbol,
                            "telegram_id": user.telegram_id or 0,
                            "username": user.username or 0,
                            "name": user.name,
                            "email": user.email,
                        },
                    )
                )

        if user.telegram_id is not None:
            await self.redirect.to_success_payment(user.telegram_id, transaction.purchase_type)

    base_execute = target.ProcessPayment._execute

    async def ProcessPayment_execute(self, actor: UserDto, data) -> None:
        """Поднять отменённый счёт, если по нему ВСЁ-ТАКИ заплатили.

        Идемпотентно по построению: `UPDATE … WHERE status='CANCELED'` совпадает
        не более одного раза, а уже проведённый счёт в выборку не попадает вовсе.
        Дальше зовём базу как есть — она сама сделает свой обычный переход
        PENDING → COMPLETED и выдаст подписку.
        """
        if data.new_transaction_status == TransactionStatus.COMPLETED:
            try:
                await _revive_canceled(self, data)
            except Exception:  # noqa: BLE001 — спасение не имеет права ломать обычный путь
                logger.exception(
                    f"Опоздавший платёж: не смог поднять счёт '{data.payment_id}'"
                )
        refund = data.new_transaction_status == TransactionStatus.REFUNDED
        status_before = await _status_before_refund(self, data.payment_id) if refund else None
        await base_execute(self, actor, data)
        if refund:
            try:
                await _alert_refunded_carry(self, data, status_before)
            except Exception:  # noqa: BLE001 — алерт не ломает обработку возврата
                logger.exception(f"carry: алерт возврата по '{data.payment_id}' не отправлен")

    async def _revive_canceled(self, data) -> None:
        async with self.uow:
            transaction = await self.transaction_dao.get_by_payment_id(data.payment_id)
            # Нет счёта или не тот шлюз — молчим: база это проверит сама и напишет.
            if transaction is None or transaction.gateway_type != data.gateway_type:
                return
            if transaction.status != TransactionStatus.CANCELED:
                return

            revived = await self.transaction_dao.transition_status(
                data.payment_id,
                TransactionStatus.PENDING,
                (TransactionStatus.CANCELED,),
            )
            if not revived:  # кто-то успел раньше — значит уже спасён
                return
            await self.uow.commit()

        user = await self.user_dao.get_by_id(transaction.user_id)
        logger.warning(
            f"Опоздавший платёж спасён: счёт '{data.payment_id}' был отменён "
            f"({transaction.created_at}), но по нему заплатили — поднят в PENDING"
        )
        await self.notifier.notify_admins(
            MessagePayloadDto(
                i18n_key="raw-message",
                i18n_kwargs={
                    "content": (
                        "💸 <b>Опоздавший платёж принят</b>\n"
                        f"Счёт <code>{data.payment_id}</code> был автоматически отменён "
                        "(старше 30 минут), но человек всё равно оплатил по старой ссылке.\n"
                        f"Шлюз: {transaction.gateway_type}\n"
                        f"Сумма: {transaction.pricing.final_amount} "
                        f"{transaction.currency.symbol}\n"
                        f"Покупатель: {user.log if user else transaction.user_id}\n\n"
                        "Подписка выдана автоматически — проверьте, что она встала верно."
                    )
                },
                # Без этого сообщение самоуничтожится через 5 секунд (дефолт payload).
                delete_after=None,
            )
        )

    ProcessPayment_init._overlay_wrapped = True  # type: ignore[attr-defined]
    target.ProcessPayment.__init__ = ProcessPayment_init
    target.ProcessPayment._handle_success = ProcessPayment_handle_success
    target.ProcessPayment._execute = ProcessPayment_execute
    return (
        "пополнение баланса, подарок через шлюз, спасение опоздавших платежей "
        "и отчёт о переносе остатка"
    )
