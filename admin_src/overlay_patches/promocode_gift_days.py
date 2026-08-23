"""Подарок и промокод не сжигают оставшиеся дни подписки.

ЧТО БЫЛО. Награда типа SUBSCRIPTION звала обновление пользователя «по тарифу», а
эта ветка ставит срок «сейчас + длительность тарифа». У человека с действующей
подпиской остаток сгорал: реальный случай — подарок на 365 дней съел 295 уже
оплаченных.

ПРАВИЛО ВЛАДЕЛЬЦА. Дни СКЛАДЫВАЮТСЯ, только если тариф тот же самый. Другой тариф —
это замена, срок считается с нуля (в боте человека предупреждают и просят
подтвердить дважды).

ПОЧЕМУ ЗАМЕНА МЕТОДА, А НЕ КОПИЯ ФАЙЛА. Раньше ради этой правки overlay держал
копию всего activate.py — 326 строк, из которых менялся один метод. Теперь
заменяется только он: остальные девять методов приезжают из базы как есть.

ЧЕМ ЭТО ЗАЩИЩЕНО. Замена метода повторяет и те его строки, которых мы не трогали,
поэтому перед подменой сверяем исходник базы по хэшу: апстрим поправил что-то
внутри этого метода — правка не применяется и кричит, вместо того чтобы тихо
затереть чужое изменение.
"""

from __future__ import annotations

from . import PatchTargetChanged, expect_source

# sha256 метода `_apply_subscription` в базе v0.8.2 (с обрезанными отступами).
# Изменится — значит апстрим трогал этот метод, и правку надо переносить руками.
BASE_METHOD_SHA256 = "79975d2b00a34a95d6271b0654c950cf76095d9613ce43ec687e6d171ecaea49"


def apply() -> str:
    # Импорты внутри функции, а не в шапке модуля: правки применяются из
    # sitecustomize, то есть до готовности приложения, и тянуть тяжёлые модули
    # бота на старте интерпретатора нельзя.
    from datetime import timedelta  # noqa: F401 — используется в теле метода
    from typing import Optional  # noqa: F401

    from src.application.dto import (  # noqa: F401
        PlanSnapshotDto,
        PromocodeDto,
        SubscriptionDto,
        UserDto,
    )
    from src.application.use_cases.promocode.commands.activate import (
        ActivatePromocode,
        _PendingReward,  # noqa: F401 — тип возврата метода
    )
    from src.core.enums import SubscriptionStatus  # noqa: F401
    from src.core.utils.time import datetime_now  # noqa: F401

    original = getattr(ActivatePromocode, "_apply_subscription", None)
    if original is None:
        raise PatchTargetChanged(
            "у ActivatePromocode больше нет _apply_subscription — база перестроила "
            "выдачу подписки по промокоду, дни получателя снова начнут сгорать"
        )
    if getattr(original, "_overlay_wrapped", False):
        return "уже заменён"

    expect_source(original, BASE_METHOD_SHA256, "ActivatePromocode._apply_subscription")

    async def _apply_subscription(
        self,
        actor: UserDto,
        user: UserDto,
        promo: PromocodeDto,
        subscription: Optional[SubscriptionDto],
    ) -> _PendingReward:
        if not promo.plan_snapshot:
            return _PendingReward()
        plan = self.retort.load(promo.plan_snapshot, PlanSnapshotDto)
        if subscription:
            # ── ПРАВКА OVERLAY ────────────────────────────────────────────────
            # Базовый код звал update_user(..., plan=plan), а ветка «по тарифу» в
            # _build_update_request ставит expire_at = days_to_datetime(plan.duration),
            # то есть СЕЙЧАС + длительность: у получателя с активной подпиской
            # оставшиеся дни сгорали (реальный случай — подарок на 365 дн. съел 295
            # оплаченных).
            # Правило владельца: дни складываются ТОЛЬКО если тариф тот же самый.
            # Другой тариф — замена, как раньше (пользователя предупреждаем в боте
            # и просим подтвердить дважды).
            current_plan = subscription.plan_snapshot
            current_id = getattr(current_plan, "id", None) if current_plan else None
            same_plan = current_id is not None and current_id == plan.id

            subscription.traffic_limit = plan.traffic_limit
            subscription.device_limit = plan.device_limit
            subscription.traffic_limit_strategy = plan.traffic_limit_strategy
            subscription.tag = plan.tag
            subscription.internal_squads = plan.internal_squads
            subscription.external_squad = plan.external_squad

            if same_plan:
                base = subscription.expire_at
                now = datetime_now()
                if base is None or base < now:
                    base = now
                subscription.expire_at = base + timedelta(days=plan.duration)
                updated = await self.remnawave.update_user(
                    user=user,
                    uuid=subscription.user_remna_id,
                    subscription=subscription,
                    reset_traffic=True,
                )
            else:
                # Тариф другой — панель ставит срок «с нуля» (сейчас + длительность).
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
            logger.info(
                f"{actor.log} SUBSCRIPTION reward: тариф {'тот же' if same_plan else 'другой'}, "
                f"срок до {subscription.expire_at} (+{plan.duration} дн.)"
            )
            logger.info(f"{actor.log} SUBSCRIPTION reward applied")
            return _PendingReward(subscription_update=subscription)
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
        return _PendingReward(subscription_create=new_sub)

    _apply_subscription._overlay_wrapped = True  # type: ignore[attr-defined]
    ActivatePromocode._apply_subscription = _apply_subscription  # type: ignore[method-assign]
    return "дни складываются при том же тарифе"
