"""Спорный платёж: решение прямо из уведомления, а не походом в админку.

ЗАЧЕМ. Сверка суммы (`overlay_patches/yoomoney_amount_check.py`) не проводит
платёж, если списано меньше счёта, если перевод «с протекцией» или не принят, — и
пишет владельцу: «Решите вручную: вернуть деньги или выдать подписку». Дальше
уведомление заканчивается, и владелец идёт искать этот счёт в админке руками.
Уведомление, которое ТРЕБУЕТ действия, обязано это действие и предлагать.

ЧТО ДЕЛАЕТ КНОПКА «ВЫДАТЬ». Ровно то же, что сделал бы вебхук, если бы сверка его
пропустила: зовёт штатный `ProcessPayment` с тем же `payment_id` и статусом
COMPLETED. Своей выдачи здесь НЕТ и быть не должно — иначе появился бы второй путь
выдачи подписки, который пришлось бы чинить отдельно от первого.

ПОЧЕМУ БЕЗОПАСНО НАЖАТЬ ДВАЖДЫ. Перевод счёта в COMPLETED у базы идёт через
`transition_status` с набором допустимых исходных статусов. Уже проведённый счёт
второй раз не переводится — база сама откажет, и человек увидит «счёт уже
проведён», а не вторую подписку.

КТО МОЖЕТ НАЖАТЬ. Уведомление уходит владельцу, но кнопка проверяет роль
отдельно: сообщение можно переслать, а callback прилетает от того, кто нажал.
"""

from typing import Any, Optional
from uuid import UUID

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from dishka import FromDishka
from dishka.integrations.aiogram import inject
from loguru import logger

from src.application.common.dao import TransactionDao
from src.application.use_cases.gateways.commands.payment import (
    ProcessPayment,
    ProcessPaymentDto,
)
from src.core.constants import USER_KEY
from src.core.enums import PaymentGatewayType, Role, TransactionStatus

router = Router(name="overlay_payment_review")

PREFIX = "payrev"
ALLOWED_ROLES = (Role.OWNER, Role.DEV, Role.ADMIN)


def keyboard(payment_id: Any, gateway: str) -> InlineKeyboardMarkup:
    """Кнопки под уведомлением о спорном платеже.

    `gateway` едет в callback, потому что `ProcessPayment` его требует, а второй
    раз читать базу ради значения, которое у нас уже есть, незачем. Длина
    callback-данных ограничена 64 байтами: «payrev:grant:<uuid>:YOOMONEY» — 58.
    """
    tail = f"{payment_id}:{gateway}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Выдать подписку", callback_data=f"{PREFIX}:grant:{tail}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗂 Отметить разобранным", callback_data=f"{PREFIX}:done:{payment_id}"
                )
            ],
        ]
    )


def _actor_role(data: dict[str, Any]) -> Optional[Role]:
    user = data.get(USER_KEY)
    return getattr(user, "role", None)


async def _finish(callback: CallbackQuery, note: str) -> None:
    """Дописать итог в само уведомление и убрать кнопки.

    Именно ДОПИСАТЬ, а не заменить: исходные суммы должны остаться на виду —
    через месяц по этому сообщению и будут разбираться, что произошло.

    Неудача правки сообщения ничего не отменяет: действие уже выполнено, и ронять
    обработку из-за косметики нельзя. Самая частая причина — сообщение слишком
    старое, чтобы его править.
    """
    message = callback.message
    if message is None:
        return
    try:
        text = getattr(message, "html_text", None) or ""
        await message.edit_text(f"{text}\n\n{note}", reply_markup=None)
    except (TelegramBadRequest, TypeError) as exc:
        logger.warning(f"payment_review: не смог поправить сообщение: {exc}")


@router.callback_query(F.data.startswith(f"{PREFIX}:done:"))
async def on_done(callback: CallbackQuery, **data: Any) -> None:
    if _actor_role(data) not in ALLOWED_ROLES:
        await callback.answer("Недоступно", show_alert=True)
        return
    await callback.answer("Отмечено")
    await _finish(callback, "🗂 <i>Отмечено как разобранное.</i>")


async def grant_outcome(
    payment_id: UUID,
    gateway: Any,
    process_payment: Any,
    transaction_dao: Any,
) -> tuple[bool, str]:
    """Провести счёт и честно сказать, что вышло: (выдано, текст человеку).

    Вынесено из обработчика НАРОЧНО. Обработчик обвязан `@inject` и телеграмными
    типами, и проверить в нём самое важное — что кнопка действительно выдаёт
    подписку и не врёт об успехе — нельзя. Первая версия этого кода не выдавала
    ничего ни разу, и тесты, проверявшие только длину callback-данных, этого не
    заметили.
    """
    try:
        # ПОЧЕМУ `.system`, А НЕ `(actor, …)`. У ProcessPayment
        # `required_permission = None`, и база на это отвечает отказом ЛЮБОМУ, кроме
        # системного актора (`Interactor._check_permissions`). Вызов от имени
        # нажавшего падал бы с PermissionDeniedError всегда. Штатный вебхук зовёт
        # этот сценарий ровно так же (`web/endpoints/public/subscription.py`).
        await process_payment.system(
            ProcessPaymentDto(
                payment_id=payment_id,
                new_transaction_status=TransactionStatus.COMPLETED,
                gateway_type=gateway,
            )
        )
    except Exception as exc:  # noqa: BLE001 — причину показываем, кнопку не теряем
        logger.warning(f"payment_review: не выдал по счёту {payment_id}: {exc}")
        return False, f"Не удалось выдать: {exc}"

    # ПРОВЕРЯЕМ РЕЗУЛЬТАТ, А НЕ ОТСУТСТВИЕ ИСКЛЮЧЕНИЯ.
    # `ProcessPayment._execute` на неподходящем статусе счёта пишет warning в лог и
    # ПРОСТО ВЫХОДИТ — без ошибки. Доверять «не упало» здесь нельзя: самый частый
    # исход (счёт уже проведён, счёт отменён) выглядел бы как успех, и владелец
    # закрыл бы вопрос, которого никто не решил.
    try:
        fresh = await transaction_dao.get_by_payment_id(payment_id)
        status = getattr(fresh, "status", None)
    except Exception as exc:  # noqa: BLE001 — не смогли проверить, так и скажем
        logger.warning(f"payment_review: не смог перечитать счёт {payment_id}: {exc}")
        return False, "Выполнено, но проверить результат не удалось — посмотрите счёт в админке."

    if status == TransactionStatus.COMPLETED:
        return True, "Подписка выдана"
    if status is None:
        return False, "Счёт не найден — проверьте в админке."
    return False, (
        f"Подписка НЕ выдана: счёт в состоянии «{status}». "
        f"Проведённый или отменённый счёт заново не проводится."
    )


@router.callback_query(F.data.startswith(f"{PREFIX}:grant:"))
@inject
async def on_grant(
    callback: CallbackQuery,
    process_payment: FromDishka[ProcessPayment],
    transaction_dao: FromDishka[TransactionDao],
    **data: Any,
) -> None:
    if _actor_role(data) not in ALLOWED_ROLES:
        await callback.answer("Недоступно", show_alert=True)
        return

    parts = (callback.data or "").split(":")
    if len(parts) < 4:
        await callback.answer("Не разобрать платёж", show_alert=True)
        return
    try:
        payment_id = UUID(parts[2])
    except ValueError:
        await callback.answer("Не разобрать платёж", show_alert=True)
        return
    try:
        gateway = PaymentGatewayType(parts[3])
    except ValueError:
        await callback.answer("Неизвестный шлюз", show_alert=True)
        return

    granted, text = await grant_outcome(payment_id, gateway, process_payment, transaction_dao)
    await callback.answer(text, show_alert=not granted)
    if granted:
        await _finish(callback, "✅ <i>Подписка выдана вручную из этого уведомления.</i>")
