# OVERLAY базового образа: файл вендорен целиком ради ДВОЙНОГО подтверждения при
# активации подарочного кода (награда SUBSCRIPTION). Первое нажатие показывает
# всплывающее окно с последствиями (сменится ли тариф, сложатся ли дни), второе —
# активирует. При обновлении базового образа перенести (искать «ПРАВКА OVERLAY»).
from datetime import timedelta
from typing import Any, cast

from aiogram.types import CallbackQuery, Message
from aiogram_dialog import DialogManager, ShowMode, StartMode
from aiogram_dialog.widgets.input import MessageInput
from aiogram_dialog.widgets.kbd import Button
from dishka import FromDishka
from dishka.integrations.aiogram_dialog import inject
from loguru import logger

from src.application.common import EventPublisher, Notifier
from src.application.common.dao import PromocodeDao, SubscriptionDao
from src.application.dto import TelegramUserDto
from src.application.events import ErrorEvent
from src.application.use_cases.promocode.commands.activate import (
    ActivatePromocode,
    ActivatePromocodeDto,
)
from src.application.use_cases.promocode.queries.validate import (
    ValidatePromocode,
    ValidatePromocodeDto,
)
from src.core.config import AppConfig
from src.core.constants import USER_KEY
from src.core.enums import PromocodeRewardType
from src.core.exceptions import (
    PromocodeAlreadyActivatedError,
    PromocodeExpiredError,
    PromocodeNotAvailableError,
    PromocodeNotFoundError,
)
from src.telegram.states import MainMenu
from src.telegram.utils import is_double_click

PENDING_PROMO_KEY = "pending_promo_code"
PENDING_PROMO_DTO_KEY = "pending_promo_dto"
PENDING_PROMO_REPLACE_KEY = "pending_promo_replace"
PROMO_CONFIRM_STAGE_KEY = "promo_confirm_stage"


def _fmt_date(value: Any) -> str:
    try:
        return value.strftime("%d.%m.%Y")
    except Exception:  # noqa: BLE001
        return "—"


def _gift_warning(current: Any, plan: Any) -> str:
    """Текст всплывающего предупреждения перед активацией подарка.

    Собираем на месте (без ftl): в alert Telegram помещается ~200 символов, а текст
    зависит от данных — тот же тариф или другой, сложатся дни или сгорят.
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
    return (
        f"ВНИМАНИЕ: тариф сменится «{cur_name}» → «{plan_name}». "
        f"Остаток текущей подписки (до {_fmt_date(expire)}) НЕ переносится, "
        f"срок станет {days} дн. Нажмите ещё раз, если согласны."
    )


@inject
async def on_promocode_input(
    message: Message,
    widget: MessageInput,
    dialog_manager: DialogManager,
    validate_promocode: FromDishka[ValidatePromocode],
    subscription_dao: FromDishka[SubscriptionDao],
    notifier: FromDishka[Notifier],
) -> None:
    dialog_manager.show_mode = ShowMode.EDIT
    user: TelegramUserDto = dialog_manager.middleware_data[USER_KEY]
    code = (message.text or "").strip().upper()

    if not code:
        return

    try:
        promo = await validate_promocode(user, ValidatePromocodeDto(code=code, user=user))
    except PromocodeNotFoundError:
        await notifier.notify_user(user, i18n_key="ntf-promocode.not-found")
        return
    except PromocodeAlreadyActivatedError:
        await notifier.notify_user(user, i18n_key="ntf-promocode.already-activated")
        return
    except PromocodeExpiredError:
        await notifier.notify_user(user, i18n_key="ntf-promocode.expired")
        return
    except PromocodeNotAvailableError:
        await notifier.notify_user(user, i18n_key="ntf-promocode.not-available")
        return

    will_replace = False
    if promo.reward_type == PromocodeRewardType.SUBSCRIPTION:
        current = await subscription_dao.get_current(user.id)
        will_replace = current is not None

    logger.info(f"{user.log} Promocode '{code}' validated, pending confirmation")

    dialog_manager.dialog_data[PENDING_PROMO_KEY] = promo.code
    dialog_manager.dialog_data[PENDING_PROMO_DTO_KEY] = {
        "code": promo.code,
        "reward_type": promo.reward_type.value,
        "reward": promo.reward,
    }
    dialog_manager.dialog_data[PENDING_PROMO_REPLACE_KEY] = will_replace


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
) -> None:
    if is_double_click(dialog_manager, key="promo_confirm"):
        return

    user: TelegramUserDto = dialog_manager.middleware_data[USER_KEY]
    code = dialog_manager.dialog_data.get(PENDING_PROMO_KEY)

    if not code:
        return

    # ── ПРАВКА OVERLAY: второе подтверждение для подарков ────────────────────
    # Подарок меняет подписку и может обнулить остаток дней (если тариф другой),
    # поэтому первое нажатие только показывает последствия, второе — активирует.
    dto = cast(dict[str, Any], dialog_manager.dialog_data.get(PENDING_PROMO_DTO_KEY) or {})
    is_gift = dto.get("reward_type") == PromocodeRewardType.SUBSCRIPTION.value
    if is_gift and not dialog_manager.dialog_data.get(PROMO_CONFIRM_STAGE_KEY):
        try:
            promo = await promocode_dao.get_by_code(code)
            plan = getattr(promo, "plan_snapshot", None)
            if isinstance(plan, dict):  # снимок хранится json-ом
                plan = type("PlanView", (), plan)
            current = await subscription_dao.get_current(user.id)
            warning = _gift_warning(current, plan)
        except Exception as exc:  # noqa: BLE001 — предупреждение не должно ломать активацию
            logger.warning(f"{user.log} не смог собрать предупреждение по подарку: {exc}")
            warning = "Подарок изменит вашу подписку. Нажмите ещё раз для подтверждения."
        dialog_manager.dialog_data[PROMO_CONFIRM_STAGE_KEY] = 1
        await callback.answer(warning[:200], show_alert=True)
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


async def getter_promocode(dialog_manager: DialogManager, **kwargs: Any) -> dict[str, Any]:
    if dialog_manager.start_data and not dialog_manager.dialog_data.get(PENDING_PROMO_KEY):
        start_data = cast(dict[str, Any], dialog_manager.start_data)
        prefill_dto = start_data.get("prefill_dto")
        if prefill_dto:
            dialog_manager.dialog_data[PENDING_PROMO_KEY] = prefill_dto["code"]
            dialog_manager.dialog_data[PENDING_PROMO_DTO_KEY] = prefill_dto
            dialog_manager.dialog_data[PENDING_PROMO_REPLACE_KEY] = start_data.get(
                "prefill_replace", False
            )

    promo_data: dict[str, Any] = cast(
        dict[str, Any], dialog_manager.dialog_data.get(PENDING_PROMO_DTO_KEY, {})
    )
    reward_type = promo_data.get("reward_type", "")
    return {
        "has_promo": bool(promo_data),
        "promo_code": promo_data.get("code", dialog_manager.dialog_data.get(PENDING_PROMO_KEY, "")),
        "promo_reward_type": reward_type,
        "promo_reward": promo_data.get("reward") or 0,
        "show_reset_warning": reward_type
        in {PromocodeRewardType.TRAFFIC.value, PromocodeRewardType.DEVICES.value},
        "will_replace_subscription": bool(
            dialog_manager.dialog_data.get(PENDING_PROMO_REPLACE_KEY)
        ),
    }
