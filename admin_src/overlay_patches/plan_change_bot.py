"""Тексты смены тарифа в боте: сколько дней перенесётся — до оплаты и после.

ЧТО ДЕЛАЕМ. Окно подтверждения покупки писало «заменена … без пересчета оставшегося
срока». С переносом остатка (plan_change_carryover.py) это неправда. Переводы правятся
в памяти (translations_ru.py): вариант `[CHANGE]` выбирает текст по `$carry_state`
(CARRY | SMALL | LOST | CAPPED | NOPRICE | LIFETIME | NONE | OFF), а числа кладут сюда
обёртки геттеров:

  * `confirm_getter` — предпросмотр той же чистой функцией, что посчитает зачисление;
  * `success_payment_getter` — сколько дней реально добавлено (из журнала переноса):
    `carry_added` = YES/NO и `carry_days`. Строка итога печатается только при явном YES.

ПОЧЕМУ ОБОРАЧИВАЕМ, А НЕ КОПИРУЕМ. Геттеры базы отдают свои данные как есть; мы только
дописываем ключи. Сверять хэш нечего — копии нет. Любая ошибка расчёта → OFF («без
пересчёта»): лучше не пообещать перенос, чем пообещать лишнее. Переменные кладутся
ВСЕГДА, для любого типа покупки: Fluent на отсутствующей переменной пишет ошибку.

КУДА ВСТРАИВАЕМСЯ. `dialog.py` делает `from .getters import confirm_getter, …` и кладёт
функции в окна. Хук срабатывает сразу после загрузки `getters`, внутри того же
`from … import`, — имена подменяются раньше, чем их заберёт диалог.
"""

from __future__ import annotations

# Импорты модульного уровня: `@inject` разбирает аннотации через get_type_hints по
# ГЛОБАЛЯМ этого модуля (см. promocode_gift_confirm.py).
from typing import Any

from adaptix import Retort
from dishka import FromDishka
from dishka.integrations.aiogram_dialog import inject
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.dao import PaymentGatewayDao, SubscriptionDao
from src.application.dto import PlanDto, PriceDetailsDto
from src.core.utils.time import datetime_now

from . import PatchTargetChanged

TARGET_MODULE = "src.telegram.routers.subscription.getters"

# Базовые геттеры — кладёт apply(); функции ниже берут их отсюда (глобаль модуля).
_BASE: dict[str, Any] = {}

OFF = {"carry_state": "OFF", "carry_left": 0, "carry_bonus": 0, "carry_lost": 0}


def _carry() -> Any:
    """Сервис переноса — лениво (пакет services тяжёлый, см. plan_change_carryover)."""
    from src.infrastructure.services import overlay_plan_change

    return overlay_plan_change


def _state(name: str, result: Any = None) -> dict[str, Any]:
    if result is None:
        return {**OFF, "carry_state": name}
    return {
        "carry_state": name,
        "carry_left": int(result.remaining_days or 0),
        "carry_bonus": int(result.added_days),
        "carry_lost": int(result.lost_days),
    }


def vars_for_result(result: Any) -> dict[str, Any]:
    """Результат расчёта → переменные текста. Чистая функция."""
    mode = result.mode
    if mode == "lifetime":
        return _state("LIFETIME")
    if mode in ("none", "reserve"):
        return _state("NONE")
    if mode in ("refund", "failed"):
        return _state("OFF")
    if mode == "unpriced":
        return _state("NOPRICE", result)
    if result.capped or result.lost_reason == "cap":
        return _state("CAPPED", result)
    if result.lost_days > 0:
        return _state("LOST", result)
    if mode == "carry" and result.bonus_days == 0:
        return _state("SMALL", result)
    return _state("CARRY", result)


def _purchase_type_name(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


async def carry_vars(
    *,
    session: Any,
    subscription_dao: Any,
    payment_gateway_dao: Any,
    retort: Any,
    dialog_manager: Any,
    user: Any,
) -> dict[str, Any]:
    """Переменные окна подтверждения. Никогда не бросает: сбой → OFF."""
    carry = _carry()
    try:
        if not carry.overlay_active():
            return dict(OFF)
        data = dialog_manager.dialog_data
        if _purchase_type_name(data.get("purchase_type")) != "CHANGE":
            return _state("NONE")
        current = await subscription_dao.get_current(user.id)
        if current is None or current.is_trial:
            return _state("NONE")

        plan = retort.load(data[PlanDto.__name__], PlanDto)
        days = int(data["selected_duration"])
        gateway = await payment_gateway_dao.get_by_type(data["selected_payment_method"])
        pricing = retort.load(data["final_pricing"], PriceDetailsDto)
        currency = str(getattr(gateway.currency, "value", gateway.currency))
        now = datetime_now()

        state = await carry.load_carry_state(
            session,
            user_id=user.id,
            subscription=carry.sub_row_from_dto(current),
            now=now,
            exclude_payment_id=None,
            extra_plan_ids=(plan.id,),
        )
        new_list = carry.new_day_amount(pricing, plan.id, days, currency, state.prices)
        result = carry.compute_carryover(
            state,
            new_plan_id=plan.id,
            new_duration=days,
            new_list_amount=new_list,
            currency=currency,
            now=now,
        )
        return vars_for_result(result)
    except Exception as exc:  # noqa: BLE001 — окно подтверждения важнее предпросмотра
        logger.warning(f"carry: предпросмотр переноса в боте не удался ({exc}) — текст «без пересчёта»")
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return dict(OFF)


async def carry_days_after(*, session: Any, subscription_dao: Any, dialog_manager: Any, user: Any) -> int:
    """Сколько дней перенос добавил к оплаченному сроку (итог покупки). Сбой → 0."""
    try:
        start = dialog_manager.start_data or {}
        if _purchase_type_name(start.get("purchase_type")) != "CHANGE":
            return 0
        current = await subscription_dao.get_current(user.id)
        if current is None or getattr(current, "id", None) is None:
            return 0
        record = await _carry().carry_for_subscription(session, current.id)
        return int(record["added_days"]) if record else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"carry: итог переноса в боте не прочитан ({exc})")
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0


@inject
async def confirm_getter(
    session: FromDishka[AsyncSession],
    subscription_dao: FromDishka[SubscriptionDao],
    payment_gateway_dao: FromDishka[PaymentGatewayDao],
    retort: FromDishka[Retort],
    **kwargs: Any,
) -> dict[str, Any]:
    data = await _BASE["confirm_getter"](**kwargs)
    data.update(
        await carry_vars(
            session=session,
            subscription_dao=subscription_dao,
            payment_gateway_dao=payment_gateway_dao,
            retort=retort,
            dialog_manager=kwargs.get("dialog_manager"),
            user=kwargs.get("user"),
        )
    )
    return data


@inject
async def success_payment_getter(
    session: FromDishka[AsyncSession],
    subscription_dao: FromDishka[SubscriptionDao],
    **kwargs: Any,
) -> dict[str, Any]:
    data = await _BASE["success_payment_getter"](**kwargs)
    days = await carry_days_after(
        session=session,
        subscription_dao=subscription_dao,
        dialog_manager=kwargs.get("dialog_manager"),
        user=kwargs.get("user"),
    )
    data["carry_days"] = days
    data["carry_added"] = "YES" if days > 0 else "NO"
    return data


def apply() -> str:
    import src.telegram.routers.subscription.getters as target

    for name in ("confirm_getter", "success_payment_getter"):
        if getattr(target, name, None) is None:
            raise PatchTargetChanged(
                f"в subscription/getters.py больше нет {name} — тексты переноса остатка "
                "в боте пропадут (останется «без пересчёта»)"
            )
    if getattr(target.confirm_getter, "_overlay_wrapped", False):
        return "уже обёрнуты"

    _BASE["confirm_getter"] = target.confirm_getter
    _BASE["success_payment_getter"] = target.success_payment_getter

    confirm_getter._overlay_wrapped = True  # type: ignore[attr-defined]
    success_payment_getter._overlay_wrapped = True  # type: ignore[attr-defined]
    target.confirm_getter = confirm_getter
    target.success_payment_getter = success_payment_getter
    return "окно подтверждения и итог покупки знают о переносе остатка"
