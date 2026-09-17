"""Второе подтверждение, когда промокод — это подарочная подписка.

ЧТО ДЕЛАЕМ. Промокод с наградой SUBSCRIPTION у человека с ДРУГИМ действующим
тарифом не продлевает подписку, а заменяет её тариф. Остаток при этом пересчитывается
в дни подарка по цене дня (promocode_gift_days.py), а где это невозможно — сгорает.
Одного нажатия для такого мало — показываем, что именно произойдёт (сколько дней
добавится или что остаток не переносится), и просим подтвердить повторно.

КУДА ВСТРАИВАЕМСЯ. Обработчик кнопки подставляется в окно диалога по имени —
`dialog.py` делает `from .promocode_handlers import on_promocode_confirm` и кладёт
объект функции в кнопку. Значит подменить имя нужно ДО того, как выполнится эта
строка. Наш хук срабатывает сразу после загрузки самого `promocode_handlers`,
то есть внутри этого же `from … import`, ещё до привязки имён, — успеваем.

ПОЧЕМУ НЕ КОПИЯ ФАЙЛА. Раньше ради этой правки overlay держал копию всего файла;
теперь из базы заменяется один обработчик, а `on_promocode_input`, геттер и
остальное приезжают из базы как есть.
"""

from __future__ import annotations

from . import PatchTargetChanged, expect_source

# Импорты модульного уровня — так надо. Декоратор `@inject` из dishka разбирает
# аннотации обработчика через get_type_hints, а тот ищет имена в ГЛОБАЛЯХ модуля,
# где функция объявлена. Держать их внутри apply() значит получить NameError на
# ровном месте. Тяжесть импортов здесь не страшна: сам этот модуль подгружается
# лениво — только когда бот дошёл до промокодов.
from datetime import timedelta
from typing import Any, Optional, cast
from aiogram.types import CallbackQuery, Message
from aiogram_dialog import DialogManager, ShowMode, StartMode
from aiogram_dialog.widgets.kbd import Button
from dishka import FromDishka
from dishka.integrations.aiogram_dialog import inject
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
import src.telegram.routers.subscription.promocode_handlers as target
from src.application.common import EventPublisher, Notifier
from src.application.common.dao import PromocodeDao, SubscriptionDao
from src.application.dto import TelegramUserDto
from src.application.events import ErrorEvent
from src.core.config import AppConfig
from src.core.constants import USER_KEY
from src.core.enums import PromocodeRewardType
from src.telegram.states import MainMenu
from src.telegram.utils import is_double_click
from src.core.utils.time import datetime_now
from src.application.use_cases.promocode.commands.activate import (
    ActivatePromocode,
    ActivatePromocodeDto,
)
# Исключения промокода — тоже на уровне модуля. Они стоят в `except`, то есть
# вычисляются, только когда активация И ВПРАВДУ упала: без импорта законная
# «промокод просрочен» превратилась бы во второй NameError поверх первого.
from src.core.exceptions import (
    PromocodeAlreadyActivatedError,
    PromocodeExpiredError,
    PromocodeNotAvailableError,
    PromocodeNotFoundError,
)

# Отметка «предупреждение уже показано». Ключ НАШ собственный: база про второе
# подтверждение не знает, а её PENDING_PROMO_REPLACE_KEY занят — он управляет
# текстом окна. Храним в нём сам код, а не флаг: иначе человек, отменивший один
# подарок и введший другой, активировал бы второй с первого нажатия.
PROMO_CONFIRM_STAGE_KEY = "overlay_gift_confirm_stage"


# sha256 обработчика on_promocode_confirm в базе v0.8.2 (вместе с декоратором).
BASE_HANDLER_SHA256 = "97d0c1b63a00fbec4c1f71b2377c03cf035d0448fef99764f2d1b539a0590386"

# Имя тарифа в тексте всплывающего окна — не длиннее: в alert Telegram ~200 символов.
PLAN_NAME_MAX = 24


def _carry() -> Any:
    """Сервис переноса — лениво (пакет services тяжёлый, см. plan_change_carryover)."""
    from src.infrastructure.services import overlay_plan_change

    return overlay_plan_change


def _fmt_date(value: Any) -> str:
    try:
        return value.strftime("%d.%m.%Y")
    except Exception:  # noqa: BLE001
        return "—"


def _short(name: str) -> str:
    return name if len(name) <= PLAN_NAME_MAX else name[: PLAN_NAME_MAX - 1] + "…"


def gift_warning(current: Any, plan: Any, carried: Optional[Any] = None) -> str:
    """Текст всплывающего предупреждения перед активацией подарка.

    Собираем на месте (без ftl): в alert Telegram помещается ~200 символов, а текст
    зависит от данных — тот же тариф или другой, сколько дней добавит перенос остатка.
    `carried` — расчёт переноса (overlay_plan_change.promo_carry) или None.
    """
    plan_name = getattr(plan, "name", "") or "подписка"
    days = getattr(plan, "duration", 0) or 0
    if current is None:
        return f"Будет активирован тариф «{plan_name}» на {days} дн. Нажмите ещё раз для подтверждения."

    cur_plan = getattr(current, "plan_snapshot", None)
    cur_name = getattr(cur_plan, "name", "") or "текущий тариф"
    cur_id = getattr(cur_plan, "id", None)
    expire = getattr(current, "expire_at", None)
    same = cur_id is not None and cur_id == getattr(plan, "id", None)

    if same:
        new_date = _fmt_date(expire + timedelta(days=days)) if expire else "—"
        return (
            f"Тариф тот же — {days} дн. добавятся к текущему сроку, "
            f"подписка станет активна до {new_date}. Нажмите ещё раз для подтверждения."
        )
    result = getattr(carried, "result", None)
    if result is not None and result.mode == "carry":
        head = f"Тариф сменится «{_short(cur_name)}» → «{_short(plan_name)}». "
        if result.lost_days > 0:
            return (
                head + f"К {days} дн. добавится {result.added_days} дн., ещё {result.lost_days} дн. "
                "перенести нельзя. Нажмите ещё раз, если согласны."
            )
        return (
            head + f"Остаток пересчитаем по цене дня: к {days} дн. добавится {result.added_days} дн. "
            "Нажмите ещё раз."
        )
    return (
        f"ВНИМАНИЕ: тариф сменится «{_short(cur_name)}» → «{_short(plan_name)}». "
        f"Остаток текущей подписки (до {_fmt_date(expire)}) НЕ переносится, "
        f"срок станет {days} дн. Нажмите ещё раз, если согласны."
    )


async def carry_preview(session: Any, user: Any, current: Any, plan: Any) -> Optional[Any]:
    """Предпросмотр переноса для окна. Выключено, правка не встала или сбой — None."""
    carry = _carry()
    try:
        if not carry.promo_active():
            return None
        plan_id = getattr(plan, "id", None)
        duration = getattr(plan, "duration", None)
        if plan_id is None or duration is None:
            return None
        return await carry.promo_carry(
            session, user_id=user.id, subscription=current, plan_id=int(plan_id),
            duration=int(duration), now=datetime_now(),
        )
    except Exception as exc:  # noqa: BLE001 — предупреждение важнее предпросмотра
        logger.warning(f"{user.log} предпросмотр переноса по подарку не удался: {exc}")
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


def apply() -> str:
    original = getattr(target, "on_promocode_confirm", None)
    if original is None:
        raise PatchTargetChanged(
            "в promocode_handlers больше нет on_promocode_confirm — база перестроила "
            "подтверждение промокода, второе подтверждение для подарка пропадёт"
        )
    if getattr(original, "_overlay_wrapped", False):
        return "уже заменён"

    expect_source(target, "on_promocode_confirm", BASE_HANDLER_SHA256, "on_promocode_confirm")

    # Ключи, под которыми базовый обработчик кладёт промокод в диалог. Берём их У
    # БАЗЫ, а не повторяем строками: наш обработчик читает ЧУЖОЕ хранилище, и
    # разъехавшийся ключ дал бы молчаливое «нажал — ничего не произошло».
    # Через ЛОКАЛЬНЫЕ имена — так они попадают в замыкание и разрешаются; глобали
    # базового модуля нашей функции недоступны (она объявлена здесь, не там).
    try:
        pending_key: str = target.PENDING_PROMO_KEY
        pending_dto_key: str = target.PENDING_PROMO_DTO_KEY
    except AttributeError as exc:
        raise PatchTargetChanged(
            "в promocode_handlers больше нет PENDING_PROMO_KEY/PENDING_PROMO_DTO_KEY — "
            "база сменила ключи диалога, второе подтверждение читало бы пустоту"
        ) from exc

    @inject
    async def on_promocode_confirm(
        callback: CallbackQuery,
        widget: Button,
        dialog_manager: DialogManager,
        activate_promocode: FromDishka[ActivatePromocode],
        notifier: FromDishka[Notifier],
        event_publisher: FromDishka[EventPublisher],
        config: FromDishka[AppConfig],
        promocode_dao: FromDishka[PromocodeDao],
        subscription_dao: FromDishka[SubscriptionDao],
        session: FromDishka[AsyncSession],
    ) -> None:
        user: TelegramUserDto = dialog_manager.middleware_data[USER_KEY]
        code = dialog_manager.dialog_data.get(pending_key)

        if not code:
            return

        # ── ПРАВКА OVERLAY: второе подтверждение для подарков ────────────────────
        # Подарок меняет подписку и может обнулить остаток дней (если тариф другой),
        # поэтому первое нажатие только показывает последствия, второе — активирует.
        #
        # Защита от двойного клика стоит НИЖЕ этой ветки, а не выше, как в базе.
        # У базы нажатие одно, и первое же оно и последнее; у нас их два подряд, а
        # is_double_click глотает всё, что пришло в течение 10 секунд после
        # предыдущего. Стой она выше — подтверждающее нажатие уходило бы в пустоту,
        # и человек ждал бы десять секунд, не понимая, почему кнопка мёртвая.
        dto = cast(dict[str, Any], dialog_manager.dialog_data.get(pending_dto_key) or {})
        is_gift = dto.get("reward_type") == PromocodeRewardType.SUBSCRIPTION.value
        if is_gift and dialog_manager.dialog_data.get(PROMO_CONFIRM_STAGE_KEY) != code:
            try:
                promo = await promocode_dao.get_by_code(code)
                plan = getattr(promo, "plan_snapshot", None)
                if isinstance(plan, dict):  # снимок хранится json-ом
                    plan = type("PlanView", (), plan)
                current = await subscription_dao.get_current(user.id)
                carried = await carry_preview(session, user, current, plan)
                warning = gift_warning(current, plan, carried)
            except Exception as exc:  # noqa: BLE001 — предупреждение не должно ломать активацию
                logger.warning(f"{user.log} не смог собрать предупреждение по подарку: {exc}")
                warning = "Подарок изменит вашу подписку. Нажмите ещё раз для подтверждения."
            dialog_manager.dialog_data[PROMO_CONFIRM_STAGE_KEY] = code
            await callback.answer(warning[:200], show_alert=True)
            return

        if is_double_click(dialog_manager, key="promo_confirm"):
            return

        try:
            promo = await activate_promocode(user, ActivatePromocodeDto(code=code, user=user))
        except PromocodeAlreadyActivatedError:
            await notifier.notify_user(user, i18n_key="ntf-promocode.already-activated")
            return
        except PromocodeExpiredError:
            await notifier.notify_user(user, i18n_key="ntf-promocode.expired")
            return
        except PromocodeNotFoundError:
            await notifier.notify_user(user, i18n_key="ntf-promocode.not-found")
            return
        except PromocodeNotAvailableError:
            await notifier.notify_user(user, i18n_key="ntf-promocode.not-available")
            return
        except Exception as exc:
            logger.exception(f"{user.log} Promocode '{code}' activation failed unexpectedly")
            await notifier.notify_user(user, i18n_key="ntf-promocode.activation-failed")
            await event_publisher.publish(
                ErrorEvent(
                    **config.build.data,
                    telegram_id=user.telegram_id,
                    username=user.username,
                    name=user.name,
                    exception=exc,
                )
            )
            return

        dialog_manager.dialog_data.pop(PROMO_CONFIRM_STAGE_KEY, None)
        logger.info(f"{user.log} Activated promocode '{promo.code}'")
        await notifier.notify_user(user, i18n_key="ntf-promocode.activated")
        await dialog_manager.start(MainMenu.MAIN, mode=StartMode.RESET_STACK)

    on_promocode_confirm._overlay_wrapped = True  # type: ignore[attr-defined]
    target.on_promocode_confirm = on_promocode_confirm
    return "подарочный промокод требует второго подтверждения"
