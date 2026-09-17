"""Смена тарифа не сжигает остаток: перенос по цене дня при зачислении.

ЧТО БЫЛО. Ветка «CHANGE или триал» в `PurchaseSubscription._execute` зовёт
`update_user(plan=…)`, а `RemnawaveImpl._build_update_request` по тарифу ставит
`expire_at = сейчас + длительность`. Оплаченный остаток текущей подписки сгорал —
человек, перешедший на тариф побольше за неделю после продления, терял месяц.

ЧТО ДЕЛАЕМ. ОБОРАЧИВАЕМ `_execute`, а не копируем: наша ветка — только CHANGE для
подписки, которая не триал, при включённом выключателе. Всё прочее (NEW, RENEW,
триал, выключено) уходит в нетронутую базу. В нашей ветке — тот же порядок, что у
базы, плюс перенос, в одной транзакции:

  замок users + свежее чтение строки → запись журнала по счёту уже есть? выход →
  состояние (SAVEPOINT) → расчёт → старая строка DELETED → панель ОДНИМ вызовом
  `update_user(subscription=черновик)` со сроком «сейчас + длительность + бонус» →
  новая строка со сроком ИЗ ОТВЕТА панели → гашение паузы (SAVEPOINT) → журнал
  (SAVEPOINT) → гашение скидки → commit.

Панель пишется тем же путём, что админское «Продлить» (`update_user(subscription=…)`):
поля запроса совпадают с веткой plan, кроме срока, — окна «срок без бонуса» нет.

ОТКАЗЫ. Упала панель — откат, счёт FAILED (база), журнала нет, повтор посчитает
заново из нетронутой базы. Не загрузилось состояние — выдача по правилам базы (бонус 0),
итог с ошибкой ложится в `_overlay_carry_outcome`, и обработчик оплаты шлёт владельцу
«остаток не пересчитан, добавьте вручную». Не записался журнал (таблицы ещё нет) —
выдача не срывается: человек заплатил.

ЭТО ДЕНЕЖНЫЙ ПУТЬ: сверка исходника трёх методов обязательна. Апстрим начнёт
переносить остаток сам — наша правка перенесла бы его второй раз; хэш `_execute`
этого не пустит, а сигнальный тест test_offers_plan_change_terms.py упадёт.
"""

from __future__ import annotations

# Импорты модульного уровня — урок NameError (overlay-nameerror-class): функции ниже
# объявлены ЗДЕСЬ и ищут глобали здесь, шапка базового модуля им не видна.
from datetime import timedelta  # noqa: F401 — держим рядом с базой: тело может понадобиться
from typing import Any, Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Remnawave
from src.application.common.dao import SubscriptionDao, UserDao
from src.application.common.uow import UnitOfWork
from src.application.dto import SubscriptionDto, UserDto
from src.core.enums import PurchaseType, SubscriptionStatus
from src.core.utils.converters import days_to_datetime
from src.core.utils.time import datetime_now

from . import PatchTargetChanged, expect_source


def _carry() -> Any:
    """Сервис переноса — лениво, в момент вызова.

    Модуль-цель импортируется посреди `src.application.use_cases.subscription`, а
    пакет `src.infrastructure.services` на импорте тянет бота, уведомления и панель —
    импорт его отсюда в момент хука рисковал бы циклом. Локальное имя внутри функции
    к классу ошибки NameError не относится: оно не глобаль.
    """
    from src.infrastructure.services import overlay_plan_change

    return overlay_plan_change


TARGET_MODULE = "src.application.use_cases.subscription.commands.purchase"

# sha256 методов базы v0.8.2.
BASE_METHODS = {
    "PurchaseSubscription.__init__": "f5cc7415075e36de2d65ef050c8823a6f68f8041a70b9646d8026403df2eac35",
    # Оборачиваем, но сверяем: мы опираемся на то, ЧТО база делает в ветке CHANGE.
    "PurchaseSubscription._execute": "e6b339b9614ac5ea56c4650792249f5658c1004c877a052c305ec986d3e17cc4",
    # Новую строку строим базовым методом — её форма часть контракта.
    "PurchaseSubscription._build_subscription_dto": "8e5eb889bed73e4bc1bd185e36c873e09b98d98a2e0be9d35b439b3b9b986317",
}

OUTCOME_ATTR = "_overlay_carry_outcome"


def PurchaseSubscription_init(
    self,
    uow: UnitOfWork,
    user_dao: UserDao,
    subscription_dao: SubscriptionDao,
    remnawave: Remnawave,
    session: AsyncSession,
) -> None:
    self.uow = uow
    self.user_dao = user_dao
    self.subscription_dao = subscription_dao
    self.remnawave = remnawave
    # OVERLAY: сессия той же транзакции, что и UoW, — замок, журнал, пауза.
    self.session = session


def _wants_carry(self: Any, data: Any) -> bool:
    carry = _carry()
    transaction = getattr(data, "transaction", None)
    subscription = getattr(data, "subscription", None)
    if transaction is None or subscription is None or getattr(self, "session", None) is None:
        return False
    if transaction.purchase_type != PurchaseType.CHANGE or subscription.is_trial:
        return False
    if not getattr(data, "user", None) or not getattr(transaction, "plan_snapshot", None):
        return False
    return bool(carry.load_config().get("enabled"))


def _currency_code(transaction: Any) -> str:
    currency = getattr(transaction, "currency", None)
    return str(getattr(currency, "value", currency) or "")


def _draft(current: Any, plan: Any, expire_at: Any) -> SubscriptionDto:
    """Черновик подписки нового тарифа — то, что уходит в панель одним вызовом."""
    return SubscriptionDto(
        user_remna_id=current.user_remna_id,
        status=SubscriptionStatus.ACTIVE,
        is_trial=False,
        traffic_limit=plan.traffic_limit,
        device_limit=plan.device_limit,
        traffic_limit_strategy=plan.traffic_limit_strategy,
        tag=plan.tag,
        internal_squads=plan.internal_squads,
        external_squad=plan.external_squad,
        expire_at=expire_at,
        url=current.url,
        plan_snapshot=plan,
    )


async def change_with_carryover(self: Any, actor: UserDto, data: Any, base_execute: Any) -> None:
    carry = _carry()
    user = data.user
    transaction = data.transaction
    plan = transaction.plan_snapshot
    currency = _currency_code(transaction)
    now = datetime_now()
    outcome: dict[str, Any] = {"payment_id": transaction.payment_id, "applied": False}
    setattr(self, OUTCOME_ATTR, outcome)

    logger.info(
        f"{actor.log} Purchase subscription started: 'CHANGE' with carry-over "
        f"for user '{user.remna_name}'"
    )

    async with self.uow:
        # 1. Замок и свежая строка одним запросом (см. lock_current_subscription).
        row = await carry.lock_current_subscription(self.session, user.id)
        if row is None:
            raise ValueError(f"No subscription found for change for user '{user.remna_name}'")
        if row.is_trial:
            # Строка стала триалом между чтением и замком — это ветка базы, не наша.
            await self.session.rollback()
            setattr(self, OUTCOME_ATTR, None)
            await base_execute(self, actor, data)
            return
        current = data.subscription
        if row.id != getattr(current, "id", None):
            logger.warning(
                f"{actor.log} carry: под замком другая строка подписки "
                f"({getattr(current, 'id', None)} → {row.id}), считаю от свежей"
            )
            fresh = await self.subscription_dao.get_current(user.id)
            if fresh is not None and fresh.id == row.id:
                current = fresh

        # 2. Идемпотентность: перенос по этому счёту уже был.
        if await carry.carry_already_applied(self.session, transaction.payment_id):
            logger.warning(
                f"{actor.log} carry: по счёту '{transaction.payment_id}' перенос уже записан — выхожу"
            )
            outcome["skipped"] = True
            await self.session.rollback()
            return

        # 3. Состояние и расчёт. Сбой загрузки не срывает оплаченную выдачу.
        state = None
        try:
            async with self.session.begin_nested():
                state = await carry.load_carry_state(
                    self.session,
                    user_id=user.id,
                    subscription=row,
                    now=now,
                    exclude_payment_id=transaction.payment_id,
                    extra_plan_ids=(plan.id,),
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"{actor.log} carry: состояние не загрузилось — выдаю без переноса")
            outcome["load_error"] = f"{type(exc).__name__}: {exc}"

        if state is not None:
            new_list = carry.new_day_amount(
                transaction.pricing, plan.id, plan.duration, currency, state.prices
            )
            result = carry.compute_carryover(
                state,
                new_plan_id=plan.id,
                new_duration=plan.duration,
                new_list_amount=new_list,
                currency=currency,
                now=now,
            )
        else:
            result = carry.failed_result(row.expire_at, now)

        # 4. Старая строка — DELETED (как база).
        await self.subscription_dao.update_status(
            subscription_id=row.id,
            status=SubscriptionStatus.DELETED,
        )

        # 5. Панель одним вызовом: срок сразу с бонусом.
        if plan.duration == 0:
            expire_target = days_to_datetime(0)
        else:
            expire_target = carry.target_expire(now, plan.duration, result)
        updated_user = await self.remnawave.update_user(
            user=user,
            uuid=current.user_remna_id,
            subscription=_draft(current, plan, expire_target),
            reset_traffic=True,
        )

        new_sub = self._build_subscription_dto(updated_user, plan)
        created = await self.subscription_dao.create(subscription=new_sub, user_id=user.id)
        new_id = getattr(created, "id", None)

        # 6. Пауза: иначе крон или «возобновить» поставят now + старый остаток поверх.
        if state is None or state.frozen_seconds is not None:
            try:
                async with self.session.begin_nested():
                    await carry.close_freeze(self.session, user.id)
            except Exception:  # noqa: BLE001
                logger.exception(f"{actor.log} carry: паузу погасить не удалось")
                outcome["freeze_error"] = True

        # 7. Журнал: отсечка, аудит, идемпотентность. Выдачу не срывает.
        try:
            async with self.session.begin_nested():
                await carry.record_carryover(
                    self.session,
                    source="purchase",
                    payment_id=transaction.payment_id,
                    user_id=user.id,
                    old_subscription_id=row.id,
                    subscription_id=int(new_id) if new_id is not None else row.id,
                    old_plan_id=row.plan_id,
                    new_plan_id=plan.id,
                    new_duration=plan.duration,
                    currency=currency,
                    result=result,
                    expire_before=row.expire_at,
                    expire_after=getattr(updated_user, "expire_at", None) or expire_target,
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"{actor.log} carry: запись журнала не удалась — выдача продолжается")
            outcome["record_error"] = f"{type(exc).__name__}: {exc}"

        # 8. Скидка на покупку погашается, как у базы (на этом держатся кроны скидок).
        if user.purchase_discount:
            user.purchase_discount = 0
            await self.user_dao.update(user)
        await self.uow.commit()

    outcome.update(
        applied=True,
        result=result,
        old_plan_id=row.plan_id,
        old_plan_name=getattr(getattr(current, "plan_snapshot", None), "name", None),
        subscription_id=new_id,
        expire_before=row.expire_at,
        expire_after=getattr(updated_user, "expire_at", None) or expire_target,
    )
    logger.info(
        f"{actor.log} Changed subscription with carry-over for user '{user.id}': "
        f"mode={result.mode} left={result.remaining_days} bonus=+{result.added_days} "
        f"lost={result.lost_days}"
    )


def apply() -> str:
    import src.application.use_cases.subscription.commands.purchase as target

    cls = getattr(target, "PurchaseSubscription", None)
    if cls is None:
        raise PatchTargetChanged(
            "в purchase.py больше нет PurchaseSubscription — перенос остатка при смене тарифа пропадёт"
        )
    if getattr(cls._execute, "_overlay_wrapped", False):
        return "уже обёрнута"

    for qualname, sha in BASE_METHODS.items():
        expect_source(target, qualname, sha, qualname)

    base_execute = cls._execute

    async def PurchaseSubscription_execute(self, actor: UserDto, data: Any) -> None:
        if not _wants_carry(self, data):
            setattr(self, OUTCOME_ATTR, None)
            await base_execute(self, actor, data)
            return
        await change_with_carryover(self, actor, data, base_execute)

    PurchaseSubscription_execute._overlay_wrapped = True  # type: ignore[attr-defined]
    PurchaseSubscription_init._overlay_wrapped = True  # type: ignore[attr-defined]
    cls.__init__ = PurchaseSubscription_init
    cls._execute = PurchaseSubscription_execute
    return "смена тарифа пересчитывает остаток по цене дня (CHANGE не-триала)"


def outcome_for(purchase_subscription: Any, payment_id: Any) -> Optional[dict]:
    """Итог нашей ветки для этого счёта (или None, если шла база)."""
    outcome = getattr(purchase_subscription, OUTCOME_ATTR, None)
    if not isinstance(outcome, dict) or outcome.get("payment_id") != payment_id:
        return None
    return outcome
