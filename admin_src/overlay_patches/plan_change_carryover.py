"""Смена тарифа не сжигает остаток: перенос по цене дня при зачислении.

ЧТО БЫЛО. Ветка «CHANGE или триал» в `PurchaseSubscription._execute` зовёт
`update_user(plan=…)`, а `RemnawaveImpl._build_update_request` по тарифу ставит
`expire_at = сейчас + длительность`. Оплаченный остаток текущей подписки сгорал —
человек, перешедший на тариф побольше за неделю после продления, терял месяц.

ЧТО ДЕЛАЕМ. ОБОРАЧИВАЕМ `_execute`, а не копируем: наша ветка — только CHANGE для
подписки, которая не триал, при включённом выключателе. Всё прочее (NEW, триал,
выключено) уходит в нетронутую базу.

ДОКУПЛЕННЫЕ УСТРОЙСТВА. Их ёмкость при смене тарифа сгорает — лимит ставит новый
тариф, — но неиспользованная стоимость не пропадает: она приходит сюда слоями
`extras` и считается той же чистой функцией, что и дни. Слоты гасим в ТОЙ ЖЕ
транзакции, что и выдачу: перенос, записанный без гашения, посчитал бы их второй раз
при следующей смене.

RENEW ЧУЖОГО ТАРИФА. Счёт на продление создан, пока человек был на тарифе A, а оплачен
после смены на B с переносом. База такой счёт не сверяет: `max(срок, now) + срок A` и
снимок тарифа A поверх срока, набранного переносом в дни B, — дешёвые дни превращаются
в дорогие, и цикл повторяем. Поэтому RENEW под замком сверяется с тарифом строки: тот же
тариф — база, другой — наша ветка, как смена с пересчётом по стоимости.

В нашей ветке — тот же порядок, что у базы, плюс перенос, в одной транзакции:

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
from dataclasses import replace
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


def _wants_carry(self: Any, data: Any) -> Optional[str]:
    """Какая ветка нужна: "change" — перенос, "renew" — сверка RENEW под замком, None — база."""
    carry = _carry()
    transaction = getattr(data, "transaction", None)
    subscription = getattr(data, "subscription", None)
    if transaction is None or subscription is None or getattr(self, "session", None) is None:
        return None
    if subscription.is_trial:
        return None
    if not getattr(data, "user", None) or not getattr(transaction, "plan_snapshot", None):
        return None
    if transaction.purchase_type not in (PurchaseType.CHANGE, PurchaseType.RENEW):
        return None
    if carry.load_config().get("enabled") is not True:
        return None
    return "change" if transaction.purchase_type == PurchaseType.CHANGE else "renew"


async def renew_with_plan_check(self: Any, actor: UserDto, data: Any, base_execute: Any) -> None:
    """RENEW под замком: тот же тариф, что у строки, — база; другой — смена с пересчётом."""
    carry = _carry()
    user = data.user
    plan = data.transaction.plan_snapshot
    async with self.uow:
        row = await carry.lock_current_subscription(self.session, user.id)
        other_plan = (
            row is not None
            and not row.is_trial
            and not row.is_unlimited
            and str(row.status).upper() != "DELETED"
            and row.plan_id is not None
            and int(row.plan_id) != int(plan.id)
        )
        if not other_plan:
            if row is not None and row.id != getattr(data.subscription, "id", None):
                fresh = await self.subscription_dao.get_current(user.id)
                if fresh is not None and fresh.id == row.id:
                    data = replace(data, subscription=fresh)
            setattr(self, OUTCOME_ATTR, None)
            # Замок держим до коммита базы: соседняя смена тарифа подождёт это продление.
            await base_execute(self, actor, data)
            return
    logger.warning(
        f"{actor.log} carry: RENEW счёта '{data.transaction.payment_id}' на тариф {plan.id}, "
        f"а строка уже на тарифе {row.plan_id} — пересчитываю как смену тарифа по стоимости"
    )
    await change_with_carryover(self, actor, data, base_execute, rerouted_renew=True)


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


async def change_with_carryover(
    self: Any, actor: UserDto, data: Any, base_execute: Any, *, rerouted_renew: bool = False
) -> None:
    carry = _carry()
    user = data.user
    transaction = data.transaction
    plan = transaction.plan_snapshot
    currency = _currency_code(transaction)
    now = datetime_now()
    outcome: dict[str, Any] = {
        "payment_id": transaction.payment_id,
        "applied": False,
        "rerouted_renew": rerouted_renew,
    }
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

        # 2. Идемпотентность: перенос по этому счёту уже был. Проверка — в SAVEPOINT:
        # сбой (например, таблицы журнала ещё нет) не имеет права сорвать ОПЛАЧЕННУЮ
        # смену. Не проверили — считаем «переноса не было» (CAS базы и так пускает
        # выдачу по счёту один раз) и говорим владельцу.
        already = False
        try:
            async with self.session.begin_nested():
                already = await carry.carry_already_applied(self.session, transaction.payment_id)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"{actor.log} carry: проверка журнала по счёту не удалась ({exc}) — продолжаю")
            outcome["idempotency_error"] = f"{type(exc).__name__}: {exc}"
        if already:
            logger.warning(
                f"{actor.log} carry: по счёту '{transaction.payment_id}' перенос уже записан — выхожу"
            )
            outcome["skipped"] = True
            await self.session.rollback()
            return

        # 3. Состояние и расчёт. Сбой загрузки или расчёта не срывает оплаченную выдачу.
        state = None
        try:
            async with self.session.begin_nested():
                extras = await _device_extras(self.session, user.id, row.id, now)
                state = await carry.load_carry_state(
                    self.session,
                    user_id=user.id,
                    subscription=row,
                    now=now,
                    exclude_payment_id=transaction.payment_id,
                    extra_plan_ids=(plan.id,),
                    extras=extras,
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"{actor.log} carry: состояние не загрузилось — выдаю без переноса")
            outcome["load_error"] = f"{type(exc).__name__}: {exc}"

        result = None
        if state is not None:
            try:
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
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"{actor.log} carry: расчёт переноса упал — выдаю без переноса")
                outcome["load_error"] = f"{type(exc).__name__}: {exc}"
        if result is None:
            frozen, reserve = await _pause_and_reserve(carry, self.session, user.id, state)
            result = carry.failed_result(
                row.expire_at, now, frozen_seconds=frozen, reserve_expire_at=reserve
            )

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

        # 8. Докупленные устройства старой строки сгорают: лимит уже ставит новый тариф.
        # В ТОЙ ЖЕ транзакции — иначе перенос записан, а слоты живы, и следующая смена
        # посчитала бы их стоимость второй раз. SAVEPOINT: таблиц может не быть.
        try:
            async with self.session.begin_nested():
                burned = await _burn_device_slots(self.session, row.id, result)
                if burned:
                    logger.info(
                        f"{actor.log} extra_device: сгорело мест при смене тарифа: {burned}"
                    )
        except Exception as exc:  # noqa: BLE001 — выдача уже оплачена
            logger.exception(f"{actor.log} extra_device: слоты не погашены ({exc})")
            outcome["device_burn_error"] = f"{type(exc).__name__}: {exc}"

        # 9. Скидка на покупку погашается, как у базы (на этом держатся кроны скидок).
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


def _extra() -> Any:
    """Сервис докупки — лениво, по той же причине, что и сервис переноса (см. _carry)."""
    from src.infrastructure.services import overlay_extra_device

    return overlay_extra_device


# Точка отсчёта «уже прожито» для докупленных мест: на паузе срок стоит, и считать
# израсходованной надо часть до момента паузы, а не до «сейчас».
FROZEN_AT_SQL = "SELECT frozen_at FROM subscription_freezes WHERE user_id = :uid AND active = true"


async def _device_extras(session: Any, user_id: int, subscription_id: int, now: Any) -> tuple:
    """Стоимость действующих докупленных мест → слои `extras` расчёта переноса.

    Ошибку не глотаем: вызывающий SAVEPOINT уже ловит сбой загрузки состояния и
    выдаёт без переноса, сказав об этом владельцу.
    """
    extra = _extra()
    carry = _carry()
    orders = await extra.load_carry_orders(session, subscription_id)
    if not orders:
        return ()
    frozen = (await session.execute(carry.text(FROZEN_AT_SQL), {"uid": user_id})).scalar()
    ref = frozen or now
    return tuple(
        carry.ParallelLayer(
            amount=amount,
            currency=currency,
            total_seconds=total,
            remaining_seconds=remaining,
            ref="device",
        )
        for amount, currency, total, remaining in extra.carry_layers(orders, ref)
    )


async def _burn_device_slots(session: Any, old_subscription_id: int, result: Any) -> int:
    """Погасить слоты старой строки, записав, сколько ₽ ушло в дни."""
    extra = _extra()
    carried = None
    breakdown = getattr(result, "breakdown", ()) or ()
    moved = [b for b in breakdown if b.get("kind") == "device" and b.get("converted")]
    if moved:
        from decimal import Decimal

        carried = sum((Decimal(str(b.get("amount") or 0)) for b in moved), Decimal(0))
    return await extra.burn_for_change(session, old_subscription_id, carried)


async def _pause_and_reserve(carry: Any, session: Any, user_id: int, state: Any) -> tuple[Any, Any]:
    """Пауза и резерв для остатка в алерте о сбое. Берём из состояния или читаем отдельно."""
    if state is not None:
        return state.frozen_seconds, state.reserve_expire_at
    frozen = reserve = None
    try:
        async with session.begin_nested():
            row = (await session.execute(carry.text(carry.FREEZE_SQL), {"uid": user_id})).first()
            frozen = int(row[0]) if row and row[0] is not None else None
            reserve = (await session.execute(carry.text(carry.RESERVE_SQL), {"uid": user_id})).scalar()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"carry: пауза и резерв для алерта не прочитаны ({exc})")
    return frozen, reserve


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
        branch = _wants_carry(self, data)
        if branch == "change":
            await change_with_carryover(self, actor, data, base_execute)
        elif branch == "renew":
            await renew_with_plan_check(self, actor, data, base_execute)
        else:
            setattr(self, OUTCOME_ATTR, None)
            await base_execute(self, actor, data)

    PurchaseSubscription_execute._overlay_wrapped = True  # type: ignore[attr-defined]
    PurchaseSubscription_init._overlay_wrapped = True  # type: ignore[attr-defined]
    cls.__init__ = PurchaseSubscription_init
    cls._execute = PurchaseSubscription_execute
    return "смена тарифа пересчитывает остаток по цене дня (CHANGE не-триала, RENEW чужого тарифа)"


def outcome_for(purchase_subscription: Any, payment_id: Any) -> Optional[dict]:
    """Итог нашей ветки для этого счёта (или None, если шла база)."""
    outcome = getattr(purchase_subscription, OUTCOME_ATTR, None)
    if not isinstance(outcome, dict) or outcome.get("payment_id") != payment_id:
        return None
    return outcome
