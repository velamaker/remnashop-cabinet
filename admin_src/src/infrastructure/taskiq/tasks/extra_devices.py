"""Жизнь докупленных мест под устройства: крон каждые 15 минут (overlay).

ТРИ ДЕЛА ЗА ПРОХОД:
  1. Деньги. Заказ `pending`, по которому счёт уже COMPLETED (вебхук не дошёл или
     упал между зачислением и применением), — зачислить на баланс и применить.
     Заказ `credited` — применить; висит дольше двух часов (панель молчит) —
     отклонить, деньги остаются на балансе. Неоплаченные `pending` НЕ закрываем
     никогда: отменённый счёт оживает опоздавшей оплатой, и закрытый заказ потерял
     бы эти деньги.
  2. Слоты. Срок вышел — лимит вниз (и, если владелец включил, отключение устройств,
     подключённых после покупки места); лимит сбросили продлением — вернуть; тариф
     или строка подписки сменились — слот сгорает.
  3. Хвосты отключения устройств: `removal_done = false` дольше суток — повтор,
     дальше алерт владельцу.

Крон работает ДАЖЕ при выключенной докупке: выключатель гасит продажи, а уже
оплаченное обязано дожить свой срок и кончиться вовремя.

Задача обнаруживается taskiq по глобу tasks/*.py — регистрировать её негде.
"""

from types import SimpleNamespace
from typing import Any, Optional

from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Notifier, Remnawave
from src.application.common.dao import UserDao
from src.application.dto import MessagePayloadDto
from src.infrastructure.services import overlay_extra_device as extra
from src.infrastructure.services.overlay_push import notify_user_push
from src.infrastructure.taskiq.broker import broker

# Сколько заказов и людей берём за проход. Крон каждые 15 минут, хвост уедет
# следующим проходом — лучше, чем час висеть на панели в одной транзакции.
MAX_ORDERS_PER_RUN = 50
MAX_USERS_PER_RUN = 500
# Заказ `pending` считаем «вебхук не дошёл» не раньше двух минут: прямо сейчас его,
# возможно, обрабатывает веб-процесс.
PENDING_GRACE_MINUTES = 2

# Заказы, которые надо довести: оплаченные (счёт COMPLETED), но ещё не применённые.
OPEN_ORDERS_SQL = (
    "SELECT o.payment_id, o.status, o.created_at "
    "FROM extra_device_orders o JOIN transactions t ON t.payment_id = o.payment_id "
    "WHERE o.status IN ('pending', 'credited') AND t.status::text = 'COMPLETED' "
    "AND o.created_at < now() - make_interval(mins => :grace) "
    "ORDER BY o.created_at LIMIT :lim"
)

ACTIVE_SLOT_USERS_SQL = (
    "SELECT DISTINCT ON (user_id) user_id FROM extra_device_slots "
    "WHERE status = 'active' ORDER BY user_id, ends_at LIMIT :lim"
)

STUCK_REMOVALS_SQL = (
    "SELECT id, user_id, ended_at FROM extra_device_slots "
    "WHERE status = 'ended' AND removal_done = false ORDER BY ended_at LIMIT :lim"
)

_PUSH_ENDED = {
    "ru": ("Докупленное устройство закончилось", "Теперь можно подключить {limit} устр."),
    "en": ("Your extra device has expired", "You can now connect {limit} devices."),
}
_PUSH_REMIND = {
    "ru": ("Докупленное устройство скоро отключится", "Работает до {date} — продлите в «Устройствах»."),
    "en": ("Your extra device expires soon", "It works until {date} — extend it on Devices."),
}


async def _tell_user(
    session: AsyncSession,
    notifier: Notifier,
    user: Any,
    content: str,
    *,
    push: Optional[dict] = None,
    **fmt: Any,
) -> None:
    """Сообщение человеку — best-effort и ТОЛЬКО после commit денежной части."""
    try:
        await notifier.notify_user(
            user,
            payload=MessagePayloadDto(
                i18n_key="raw-message",
                i18n_kwargs={"content": content},
                # Без этого сообщение самоуничтожится через 5 секунд (дефолт payload).
                delete_after=None,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"extra_device: сообщение user_id={getattr(user, 'id', '?')} не ушло: {exc}")
    if push is not None:
        await notify_user_push(
            session,
            SimpleNamespace(id=user.id, language=getattr(user, "language", None)),
            push,
            url="/devices",
            tag="extra-device",
            **fmt,
        )


async def _tell_admins(notifier: Notifier, content: str) -> None:
    try:
        await notifier.notify_admins(
            MessagePayloadDto(i18n_key="raw-message", i18n_kwargs={"content": content}, delete_after=None)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"extra_device: алерт владельцу не ушёл: {exc}")


async def _settle_orders(
    session: AsyncSession,
    *,
    sdk: Any,
    config: dict,
    user_dao: UserDao,
    notifier: Notifier,
    now: Any,
) -> int:
    rows = (
        await session.execute(
            text(OPEN_ORDERS_SQL), {"grace": PENDING_GRACE_MINUTES, "lim": MAX_ORDERS_PER_RUN}
        )
    ).all()
    await session.rollback()
    done = 0
    for payment_id, _status, _created in rows:
        try:
            # Порядок замков всегда «строка заказа → строка users»: обратный порядок
            # столкнул бы крон с вебхуком на одном человеке взаимоблокировкой.
            order = await extra.order_for_update(session, payment_id)
            if order is None or order["status"] not in ("pending", "credited"):
                await session.rollback()
                continue
            if order["status"] == "pending":
                await extra.credit_order(session, order)
                order["status"] = "credited"
                order = await extra.order_for_update(session, payment_id) or order
            age = (now - order["created_at"]).total_seconds() / 3600
            if age > extra.CREDIT_RETRY_HOURS and order["attempts"] > 0:
                await session.rollback()
                await extra.reject_order(session, order["id"], "panel_timeout")
                await _report_rejected(session, user_dao, notifier, config, order, "panel_timeout")
                continue
            result = await extra.apply_order(session, sdk=sdk, config=config, order=order, now=now)
            done += 1
            await _report_order(session, user_dao, notifier, config, order, result)
        except Exception as exc:  # noqa: BLE001 — один заказ не мешает остальным
            await session.rollback()
            logger.warning(f"extra_device: заказ '{payment_id}' не применён: {exc}")
            try:
                order = await extra.order_for_update(session, payment_id)
                await session.rollback()
                if order is not None:
                    await extra.note_attempt(session, order["id"], f"{type(exc).__name__}: {exc}")
            except Exception:  # noqa: BLE001
                await session.rollback()
    return done


async def _report_order(
    session: AsyncSession,
    user_dao: UserDao,
    notifier: Notifier,
    config: dict,
    order: dict,
    result: dict,
) -> None:
    user = await user_dao.get_by_id(order["user_id"])
    if user is None:
        return
    if result.get("result") == "applied":
        if config.get("notify_users"):
            await _tell_user(
                session,
                notifier,
                user,
                extra.user_text("applied", limit=result["device_limit"], until=result["until"]),
            )
        if config.get("notify_admins"):
            await _tell_admins(
                notifier,
                extra.admin_text(
                    "bought",
                    user=user.log,
                    kind=order["kind"],
                    until=order["period_end"],
                    amount=order["amount"],
                    source="gateway",
                ),
            )
        return
    await _report_rejected(session, user_dao, notifier, config, order, result.get("reason") or "unknown", user)


async def _report_rejected(
    session: AsyncSession,
    user_dao: UserDao,
    notifier: Notifier,
    config: dict,
    order: dict,
    reason: str,
    user: Any = None,
) -> None:
    user = user or await user_dao.get_by_id(order["user_id"])
    if user is None:
        return
    if config.get("notify_users"):
        key = "balance_spent" if reason == "balance_spent" else "not_applied"
        await _tell_user(
            session, notifier, user, extra.user_text(key, amount=order["amount"], reason=reason)
        )
    if config.get("notify_admins"):
        await _tell_admins(
            notifier,
            extra.admin_text(
                "rejected",
                user=user.log,
                amount=order["amount"],
                payment_id=order["payment_id"],
                reason=reason,
            ),
        )


async def _live_slots(
    session: AsyncSession,
    *,
    sdk: Any,
    remnawave: Remnawave,
    config: dict,
    user_dao: UserDao,
    notifier: Notifier,
    now: Any,
) -> int:
    user_ids = [
        int(r[0])
        for r in (await session.execute(text(ACTIVE_SLOT_USERS_SQL), {"lim": MAX_USERS_PER_RUN})).all()
    ]
    await session.rollback()
    touched = 0
    for user_id in user_ids:
        try:
            out = await extra.reconcile_user(
                session,
                sdk=sdk,
                remnawave=remnawave,
                user_id=user_id,
                config=config,
                now=now,
            )
        except Exception as exc:  # noqa: BLE001 — один человек не мешает остальным
            await session.rollback()
            logger.warning(f"extra_device: сверка user_id={user_id} не прошла: {exc}")
            continue
        if out.get("error"):
            await _alert_fail(session, notifier, user_dao, user_id, out["error"])
            continue
        if not (out.get("ended") or out.get("burned") or out.get("reminded")):
            continue
        touched += 1
        await _report_reconcile(session, user_dao, notifier, config, user_id, out)
    return touched


async def _alert_fail(
    session: AsyncSession, notifier: Notifier, user_dao: UserDao, user_id: int, error: str
) -> None:
    """Алерт только после трёх неудач подряд: панель моргает чаще, чем ломается."""
    row = (
        await session.execute(
            text(
                "SELECT id, fail_count FROM extra_device_slots "
                "WHERE user_id = :uid AND status = 'active' ORDER BY fail_count DESC LIMIT 1"
            ),
            {"uid": user_id},
        )
    ).first()
    await session.rollback()
    if row is None or int(row[1] or 0) != extra.FAIL_ALERT_AT:
        return
    user = await user_dao.get_by_id(user_id)
    await _tell_admins(
        notifier,
        extra.admin_text(
            "cron_failed",
            user=user.log if user else f"user_id={user_id}",
            slot_id=int(row[0]),
            what="limit",
            error=error,
        ),
    )


async def _report_reconcile(
    session: AsyncSession,
    user_dao: UserDao,
    notifier: Notifier,
    config: dict,
    user_id: int,
    out: dict,
) -> None:
    user = await user_dao.get_by_id(user_id)
    if user is None:
        return
    for _slot_id, reason in out.get("burned") or []:
        if reason == "plan_replaced" and config.get("notify_admins"):
            await _tell_admins(
                notifier,
                extra.admin_text(
                    "plan_replaced", user=user.log, slot_id=_slot_id, until=out.get("expire_at")
                ),
            )
    expire_at = out.get("expire_at")
    alive = expire_at is not None and expire_at > extra.now_utc()
    if out.get("ended") and config.get("notify_users") and alive:
        # Истёкшей подписке сообщать не о чем: лимит и подписка кончились вместе.
        removed = [d.get("name") for d in (out.get("removed") or [])]
        await _tell_user(
            session,
            notifier,
            user,
            extra.user_text("ended", limit=out["limit"], removed=removed),
            push=_PUSH_ENDED,
            limit=out["limit"],
        )
    for claim in out.get("reminded") or []:
        if not config.get("notify_users"):
            break
        await _tell_user(
            session,
            notifier,
            user,
            extra.user_text(
                "reminder",
                ends_at=claim["ends_at"],
                until=expire_at,
                limit=out.get("plan_device_limit"),
            ),
            push=_PUSH_REMIND,
            date=claim["ends_at"].strftime("%d.%m.%Y"),
        )


async def _retry_removals(
    session: AsyncSession, *, remnawave: Remnawave, notifier: Notifier, user_dao: UserDao, now: Any
) -> None:
    rows = (await session.execute(text(STUCK_REMOVALS_SQL), {"lim": MAX_ORDERS_PER_RUN})).all()
    await session.rollback()
    for slot_id, user_id, ended_at in rows:
        old = ended_at is not None and (now - ended_at).total_seconds() > 24 * 3600
        try:
            st = await extra.lock_state(session, int(user_id))
            await session.rollback()
            if st.sub_id is None or not st.remna_uuid:
                raise RuntimeError("подписки или пользователя в панели нет")
            await extra._remove_excess(
                session,
                remnawave=remnawave,
                st=st,
                limit=st.device_limit,
                since=None,
                slot_ids=[int(slot_id)],
            )
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            if not old:
                continue
            user = await user_dao.get_by_id(int(user_id))
            await _tell_admins(
                notifier,
                extra.admin_text(
                    "cron_failed",
                    user=user.log if user else f"user_id={user_id}",
                    slot_id=int(slot_id),
                    what="devices",
                    error=str(exc),
                ),
            )
            await session.execute(
                text("UPDATE extra_device_slots SET removal_done = true WHERE id = :id"),
                {"id": int(slot_id)},
            )
            await session.commit()


@broker.task(schedule=[{"cron": "*/15 * * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def run_extra_devices_tick(
    session: FromDishka[AsyncSession],
    remnawave: FromDishka[Remnawave],
    user_dao: FromDishka[UserDao],
    notifier: FromDishka[Notifier],
) -> None:
    config = extra.load_config()
    sdk = getattr(remnawave, "sdk", None)
    if sdk is None:
        logger.warning("extra_device: панель недоступна — проход пропущен")
        return
    now = extra.now_utc()

    settled = await _settle_orders(
        session, sdk=sdk, config=config, user_dao=user_dao, notifier=notifier, now=now
    )
    touched = await _live_slots(
        session,
        sdk=sdk,
        remnawave=remnawave,
        config=config,
        user_dao=user_dao,
        notifier=notifier,
        now=now,
    )
    if config.get("remove_excess_devices"):
        await _retry_removals(
            session, remnawave=remnawave, notifier=notifier, user_dao=user_dao, now=now
        )
    if settled or touched:
        logger.info(f"extra_device: заказов доведено {settled}, людей сведено {touched}")
