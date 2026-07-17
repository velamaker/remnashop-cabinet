"""Win-back истёкших: «вернись, вот скидка» через N дней после окончания (overlay).

Крон (почасовой):
  • ВЫДАЧА: находит USER, у кого подписка истекла ~days_after дней назад (окно поимки)
    и кому win-back ещё не выдавали, ставит одноразовую `users.purchase_discount = %`
    (база гасит её после покупки), пишет строку в winback_grants (дедуп: один win-back
    на юзера) и шлёт напоминание (Web Push + Telegram) «вернись, скидка N%».
  • ПОГАШЕНИЕ: истёкшие неиспользованные промо помечает used=true и снимает скидку
    (если она == выданной).

Конфиг assets/winback.json (админка). Дефолт ВЫКЛ. Ядро не трогаем. Механика скидки —
как у скидки триальщикам (см. trial_discount.py). Best-effort.
"""

from types import SimpleNamespace

from aiogram import Bot
from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import AppConfig
from src.infrastructure.services.overlay_push import notify_user_push
from src.infrastructure.services.overlay_winback import load_config
from src.infrastructure.taskiq.broker import broker

# Окно поимки: юзеров, истёкших от days_after до days_after+CATCH дней назад.
_CATCH_DAYS = 14

_MSG = {
    "ru": (
        "💜 Возвращайтесь — скидка {percent}%",
        "Мы соскучились! Оформите подписку со скидкой {percent}% — "
        "предложение действует ограниченное время.",
    ),
    "en": (
        "💜 Come back — {percent}% off",
        "We miss you! Resubscribe with {percent}% off — limited-time offer.",
    ),
}


async def _expire_pass(session: AsyncSession) -> int:
    expired = (
        await session.execute(
            text(
                "SELECT user_id, percent FROM winback_grants "
                "WHERE used = false AND expires_at < now()"
            )
        )
    ).all()
    for uid, percent in expired:
        await session.execute(
            text(
                "UPDATE users SET purchase_discount = 0 "
                "WHERE id = :u AND purchase_discount = :p"
            ),
            {"u": uid, "p": percent},
        )
    if expired:
        await session.execute(
            text(
                "UPDATE winback_grants SET used = true "
                "WHERE used = false AND expires_at < now()"
            )
        )
        await session.commit()
    return len(expired)


@broker.task(schedule=[{"cron": "37 * * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def run_winback(
    session: FromDishka[AsyncSession],
    config: FromDishka[AppConfig],
) -> None:
    await _expire_pass(session)

    cfg = load_config()
    if not cfg["enabled"]:
        return

    percent = cfg["percent"]
    days_after = cfg["days_after"]
    lifetime = cfg["lifetime_hours"]

    rows = (
        await session.execute(
            text(
                "SELECT u.id, lower(u.language::text), u.telegram_id "
                "FROM users u "
                "JOIN subscriptions s ON u.current_subscription_id = s.id "
                "LEFT JOIN winback_grants w ON w.user_id = u.id "
                "WHERE u.role = 'USER' AND u.is_blocked = false "
                "AND s.expire_at < now() - make_interval(days => :d) "
                "AND s.expire_at > now() - make_interval(days => :d + :catch) "
                "AND w.user_id IS NULL"
            ),
            {"d": days_after, "catch": _CATCH_DAYS},
        )
    ).all()
    if not rows:
        return

    bot: Bot | None = None
    try:
        bot = Bot(config.bot.token.get_secret_value())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"winback: не смог создать Bot ({e}) — TG-напоминания пропущены")

    granted = 0
    for uid, lang, tg_id in rows:
        try:
            await session.execute(
                text(
                    "UPDATE users SET purchase_discount = GREATEST(purchase_discount, :p) "
                    "WHERE id = :u"
                ),
                {"p": percent, "u": uid},
            )
            await session.execute(
                text(
                    "INSERT INTO winback_grants (user_id, percent, granted_at, expires_at, used) "
                    "VALUES (:u, :p, now(), now() + make_interval(hours => :h), false) "
                    "ON CONFLICT (user_id) DO NOTHING"
                ),
                {"u": uid, "p": percent, "h": lifetime},
            )
            await session.commit()
            granted += 1
        except Exception as e:  # noqa: BLE001
            await session.rollback()
            logger.warning(f"winback: выдача user_id={uid} не удалась: {e}")
            continue

        await notify_user_push(
            session,
            SimpleNamespace(id=uid, language=lang),
            _MSG,
            url="/billing",
            tag="winback",
            percent=percent,
        )
        if bot is not None and tg_id:
            try:
                title, body = _MSG.get((lang or "ru")[:2], _MSG["ru"])
                await bot.send_message(
                    int(tg_id),
                    f"<b>{title.format(percent=percent)}</b>\n\n{body.format(percent=percent)}",
                )
            except Exception as e:  # noqa: BLE001
                logger.debug(f"winback: TG user_id={uid} не доставлено: {e}")

    if bot is not None:
        try:
            await bot.session.close()
        except Exception:  # noqa: BLE001
            pass

    if granted:
        logger.info(f"winback: выдана скидка {percent}% истёкшим — {granted} шт.")
