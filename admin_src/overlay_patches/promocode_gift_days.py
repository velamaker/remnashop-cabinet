"""Подарок и промокод не сжигают оставшиеся дни подписки.

ЧТО БЫЛО. Награда типа SUBSCRIPTION звала обновление пользователя «по тарифу», а
эта ветка ставит срок «сейчас + длительность тарифа». У человека с действующей
подпиской остаток сгорал: реальный случай — подарок на 365 дней съел 295 уже
оплаченных.

ПРАВИЛО ВЛАДЕЛЬЦА. Тот же тариф — дни СКЛАДЫВАЮТСЯ. Другой тариф — замена, но остаток
текущей подписки больше не сгорает: он пересчитывается в дни тарифа-подарка по цене
дня, той же функцией, что при смене тарифа за деньги (overlay_plan_change). Подарок не
должен стоить получателю денег: раньше подарок более дешёвого тарифа уничтожал
оплаченное, то есть был буквально отрицательным.

Цена дня подарка — витринная цена его срока в валюте по умолчанию (точного срока нет —
самая высокая цена дня тарифа). Перенести нельзя (бессрочная, возврат, тарифа нет в
таблице цен, выключатель `assets/plan_change.json`) — «замена», как раньше: в боте
человека предупреждают и просят подтвердить дважды, веб такой подарок не активирует.

Запись журнала переноса пишется в ТОЙ ЖЕ сессии без commit: её закоммитит `_execute`
базы вместе с активацией, а упадёт активация — откатится вместе с ней. Чтение состояния
и запись — в SAVEPOINT: сбой нашей таблицы не имеет права сорвать активацию.

ПОЧЕМУ ЗАМЕНА МЕТОДА, А НЕ КОПИЯ ФАЙЛА. Раньше ради этой правки overlay держал
копию всего activate.py — 326 строк, из которых менялся один метод. Теперь
заменяется он и конструктор (ему нужна сессия): остальное приезжает из базы как есть.

ЧЕМ ЭТО ЗАЩИЩЕНО. Замена метода повторяет и те его строки, которых мы не трогали,
поэтому перед подменой сверяем исходник базы по хэшу: апстрим поправил что-то
внутри — правка не применяется и кричит, вместо того чтобы тихо затереть чужое.
"""

from __future__ import annotations

# Логгер и типы конструктора — на уровне МОДУЛЯ. Метод-замена объявлен здесь, значит
# и глобали ищет здесь (см. `NameError: PENDING_PROMO_KEY` 29.08). Типы `__init__`
# dishka разбирает через get_type_hints по глобалям ЭТОГО модуля. Тяжести тут нет:
# модуль грузится хуком, когда activate.py (и всё это) уже импортирован.
from datetime import timedelta
from typing import Any, Optional

from adaptix import Retort
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import EventPublisher
from src.application.common.dao import PromocodeDao, SubscriptionDao, UserDao
from src.application.common.remnawave import Remnawave
from src.application.common.uow import UnitOfWork
from src.application.dto import PlanSnapshotDto, PromocodeDto, SubscriptionDto, UserDto
from src.application.use_cases.promocode.queries.validate import ValidatePromocode
from src.core.enums import SubscriptionStatus
from src.core.utils.time import datetime_now

from . import PatchTargetChanged, expect_source

# sha256 методов базы v0.8.2 (с обрезанными отступами). Изменится — значит апстрим
# трогал метод, и правку надо переносить руками.
BASE_METHOD_SHA256 = "79975d2b00a34a95d6271b0654c950cf76095d9613ce43ec687e6d171ecaea49"
BASE_INIT_SHA256 = "6c05e0f8ea84051a4948b71af84cbe1ddf2ce3a5ed1766fee27eceb67c769052"


def _carry() -> Any:
    """Сервис переноса — лениво: пакет services тяжёлый (см. plan_change_carryover)."""
    from src.infrastructure.services import overlay_plan_change

    return overlay_plan_change


def ActivatePromocode_init(
    self,
    uow: UnitOfWork,
    promocode_dao: PromocodeDao,
    user_dao: UserDao,
    subscription_dao: SubscriptionDao,
    remnawave: Remnawave,
    validate_promocode: ValidatePromocode,
    event_publisher: EventPublisher,
    retort: Retort,
    session: AsyncSession,
) -> None:
    self.uow = uow
    self.promocode_dao = promocode_dao
    self.user_dao = user_dao
    self.subscription_dao = subscription_dao
    self.remnawave = remnawave
    self.validate_promocode = validate_promocode
    self.event_publisher = event_publisher
    self.retort = retort
    # OVERLAY: сессия той же транзакции, что UoW, — перенос остатка и его журнал.
    self.session = session


async def plan_promo_carry(self: Any, user: Any, subscription: Any, plan: Any, now: Any) -> Optional[Any]:
    """Расчёт переноса для подарка другого тарифа. None — «замена», как раньше."""
    carry = _carry()
    session = getattr(self, "session", None)
    if session is None or not carry.load_config().get("enabled"):
        return None
    try:
        async with session.begin_nested():
            return await carry.promo_carry(
                session,
                user_id=user.id,
                subscription=subscription,
                plan_id=plan.id,
                duration=plan.duration,
                now=now,
            )
    except Exception:  # noqa: BLE001 — подарок важнее переноса: «замена», как раньше
        logger.exception(f"promo carry: расчёт переноса для user {user.id} не удался — замена")
        return None


async def record_promo_carry(
    self: Any, user: Any, subscription: Any, plan: Any, carried: Any, old_plan_id: Any, expire_before: Any
) -> None:
    carry = _carry()
    try:
        async with self.session.begin_nested():
            await carry.record_carryover(
                self.session,
                source="promocode",
                payment_id=None,
                user_id=user.id,
                old_subscription_id=subscription.id,
                subscription_id=subscription.id,
                old_plan_id=old_plan_id,
                new_plan_id=plan.id,
                new_duration=plan.duration,
                currency=carried.currency,
                result=carried.result,
                expire_before=expire_before,
                expire_after=subscription.expire_at,
            )
    except Exception:  # noqa: BLE001
        logger.exception(f"promo carry: журнал переноса для user {user.id} не записан")


async def close_promo_freeze(self: Any, user: Any) -> None:
    try:
        async with self.session.begin_nested():
            await _carry().close_freeze(self.session, user.id)
    except Exception:  # noqa: BLE001
        logger.exception(f"promo carry: паузу user {user.id} погасить не удалось")


def apply() -> str:
    from src.application.use_cases.promocode.commands.activate import (
        ActivatePromocode,
        _PendingReward,
    )

    original = getattr(ActivatePromocode, "_apply_subscription", None)
    if original is None:
        raise PatchTargetChanged(
            "у ActivatePromocode больше нет _apply_subscription — база перестроила "
            "выдачу подписки по промокоду, дни получателя снова начнут сгорать"
        )
    if getattr(original, "_overlay_wrapped", False):
        return "уже заменён"

    import src.application.use_cases.promocode.commands.activate as target

    expect_source(
        target,
        "ActivatePromocode._apply_subscription",
        BASE_METHOD_SHA256,
        "ActivatePromocode._apply_subscription",
    )
    expect_source(target, "ActivatePromocode.__init__", BASE_INIT_SHA256, "ActivatePromocode.__init__")

    # Имя из шапки базы — локальной ссылкой, чтобы попасть в замыкание (глобали базового
    # модуля нашей функции не видны).
    pending_reward = _PendingReward

    async def _apply_subscription(
        self,
        actor: UserDto,
        user: UserDto,
        promo: PromocodeDto,
        subscription: Optional[SubscriptionDto],
    ):
        if not promo.plan_snapshot:
            return pending_reward()
        plan = self.retort.load(promo.plan_snapshot, PlanSnapshotDto)
        if subscription:
            # ── ПРАВКА OVERLAY ────────────────────────────────────────────────
            # Базовый код звал update_user(..., plan=plan), а ветка «по тарифу» в
            # _build_update_request ставит expire_at = days_to_datetime(plan.duration),
            # то есть СЕЙЧАС + длительность: у получателя с активной подпиской
            # оставшиеся дни сгорали (реальный случай — подарок на 365 дн. съел 295
            # оплаченных).
            # Правило владельца: тот же тариф — дни складываются; другой — остаток
            # пересчитывается по цене дня, а где нельзя — замена, как раньше.
            current_plan = subscription.plan_snapshot
            current_id = getattr(current_plan, "id", None) if current_plan else None
            same_plan = current_id is not None and current_id == plan.id
            now = datetime_now()
            expire_before = subscription.expire_at

            carried = None if same_plan else await plan_promo_carry(self, user, subscription, plan, now)
            carry_mode = carried.result.mode if carried is not None else None

            subscription.traffic_limit = plan.traffic_limit
            subscription.device_limit = plan.device_limit
            subscription.traffic_limit_strategy = plan.traffic_limit_strategy
            subscription.tag = plan.tag
            subscription.internal_squads = plan.internal_squads
            subscription.external_squad = plan.external_squad

            if same_plan:
                base = subscription.expire_at
                if base is None or base < now:
                    base = now
                subscription.expire_at = base + timedelta(days=plan.duration)
                updated = await self.remnawave.update_user(
                    user=user,
                    uuid=subscription.user_remna_id,
                    subscription=subscription,
                    reset_traffic=True,
                )
            elif carry_mode == "carry":
                # Другой тариф, остаток переводится в дни подарка: срок сразу с бонусом.
                if carried.state.frozen_seconds is not None:
                    await close_promo_freeze(self, user)
                    subscription.status = SubscriptionStatus.ACTIVE
                subscription.expire_at = _carry().target_expire(now, plan.duration, carried.result)
                updated = await self.remnawave.update_user(
                    user=user,
                    uuid=subscription.user_remna_id,
                    subscription=subscription,
                    reset_traffic=True,
                )
                subscription.expire_at = updated.expire_at
            else:
                # Перенести нельзя (или выключено) — панель ставит срок «с нуля».
                updated = await self.remnawave.update_user(
                    user=user,
                    uuid=subscription.user_remna_id,
                    plan=plan,
                    reset_traffic=True,
                )
                subscription.expire_at = updated.expire_at

            subscription.status = SubscriptionStatus(updated.status)
            subscription.url = updated.subscription_url
            subscription.plan_snapshot = plan
            if carried is not None:
                # Отсечка на КАЖДЫЙ подарок другого тарифа (и с бонусом 0): старые оплаты
                # этой строки уже пересчитаны или сгорели — второй раз их не считаем.
                await record_promo_carry(self, user, subscription, plan, carried, current_id, expire_before)
            logger.info(
                f"{actor.log} SUBSCRIPTION reward: тариф {'тот же' if same_plan else 'другой'}"
                f"{f', перенос {carry_mode} +{carried.result.added_days} дн.' if carried is not None else ''}, "
                f"срок до {subscription.expire_at} (+{plan.duration} дн.)"
            )
            logger.info(f"{actor.log} SUBSCRIPTION reward applied")
            return pending_reward(subscription_update=subscription)
        created = await self.remnawave.create_user(user=user, plan=plan)
        new_sub = SubscriptionDto(
            user_remna_id=created.uuid,
            status=SubscriptionStatus(created.status),
            traffic_limit=plan.traffic_limit,
            device_limit=plan.device_limit,
            traffic_limit_strategy=plan.traffic_limit_strategy,
            tag=plan.tag,
            internal_squads=plan.internal_squads,
            external_squad=plan.external_squad,
            expire_at=created.expire_at,
            url=created.subscription_url,
            plan_snapshot=plan,
        )
        logger.info(f"{actor.log} SUBSCRIPTION reward applied")
        return pending_reward(subscription_create=new_sub)

    _apply_subscription._overlay_wrapped = True  # type: ignore[attr-defined]
    ActivatePromocode_init._overlay_wrapped = True  # type: ignore[attr-defined]
    ActivatePromocode.__init__ = ActivatePromocode_init  # type: ignore[method-assign]
    ActivatePromocode._apply_subscription = _apply_subscription  # type: ignore[method-assign]
    return "дни складываются при том же тарифе, другой тариф пересчитывает остаток по цене дня"
