"""Смена срока подписки с синхронизацией панели — один путь на всю админку (overlay).

Кнопка «Продлить» в карточке и массовое «Добавить N дней» обязаны менять срок
ОДИНАКОВО: считать новый срок по одним правилам и писать его в панель и в нашу базу
в одном и том же порядке. Когда путей было бы два, первая же правка одного из них
(скажем, «не опускать ниже now») молча разъехалась бы со вторым, и массовая выдача
давала бы людям не тот срок, что кнопка.

Порядок записи: сначала панель, потом наша база, коммит делает вызывающий. Упала
панель — локально ничего не меняется и не коммитится; упала база после панели —
вызывающий видит исключение, а массовая задача сверит срок с панелью при повторе.
"""

from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Optional

from src.application.dto import SubscriptionDto


def compute_new_expire(current: datetime, days: int, now: datetime) -> datetime:
    """Новый срок после «+/− days».

    Продление (+) считается от текущего срока или от now, если подписка уже истекла:
    иначе истёкшему «+3 дня» дали бы срок, который тоже уже в прошлом.
    Убавление (−) считается строго от текущего срока и не опускает ниже now.
    """
    if days >= 0:
        base = current if current > now else now
        return base + timedelta(days=days)
    new_expire = current + timedelta(days=days)
    return new_expire if new_expire > now else now


async def push_subscription_expire(
    *,
    user: Any,
    sub: SubscriptionDto,
    target: datetime,
    remnawave: Any,
    subscription_dao: Any,
    guard: Optional[Callable[[Awaitable[Any]], Awaitable[Any]]] = None,
) -> Optional[SubscriptionDto]:
    """Записать срок `target` в панель, затем в нашу базу. Коммит — за вызывающим.

    `user` — чей Telegram и почту увидит панель: `update_user` шлёт в неё ПОЛНОЕ
    состояние, поэтому массовая задача передаёт сюда несохраняемую копию с данными
    панели там, где у нас пусто (см. overlay_bulk.protected_user).

    `guard` оборачивает только вызов панели: карточка превращает его сбой в 502
    «Ошибка синхронизации с Remnawave», а сбой нашей базы остаётся обычной ошибкой,
    как было до выноса этой функции.
    """
    sub.expire_at = target
    call = remnawave.update_user(user=user, uuid=sub.user_remna_id, subscription=sub)
    await (guard(call) if guard is not None else call)
    return await subscription_dao.update(sub)
