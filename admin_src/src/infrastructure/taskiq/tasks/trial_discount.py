"""Скидка на первую покупку триальщикам за N дней до конца триала (overlay).

Крон (почасовой):
  • ВЫДАЧА: находит USER-ов на активном ТРИАЛЕ, у которых до конца осталось ≤ N дней
    и которым скидку ещё не выдавали, ставит им `users.purchase_discount = %`
    (одноразовая — база гасит её после первой покупки, см. purchase.py), пишет строку
    в trial_discounts (дедуп + срок + баннер в кабинете) и шлёт напоминание
    (Web Push + Telegram).
  • ПОГАШЕНИЕ: у кого промо истекло и НЕ использовано — снимает скидку (если она всё
    ещё равна выданной = ей не воспользовались) и удаляет строку.

Конфиг — assets/trial_discount.json (правится в админке). Дефолт ВЫКЛ. Ядро не трогаем.
Best-effort: ошибка по одному юзеру не роняет проход.
"""

from types import SimpleNamespace

from aiogram import Bot
from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import AppConfig
from src.infrastructure.services.overlay_push import notify_user_push
from src.infrastructure.services.overlay_trial_discount import load_config
from src.infrastructure.taskiq.broker import broker

_MSG = {
    "ru": (
        "🎁 Скидка {percent}% на первую покупку",
        "Ваш пробный период скоро заканчивается. Оформите подписку со скидкой "
        "{percent}% — предложение действует ограниченное время.",
    ),
    "en": (
        "🎁 {percent}% off your first purchase",
        "Your trial is ending soon. Subscribe with {percent}% off — limited-time offer.",
    ),
}


async def _expire_pass(session: AsyncSession) -> int:
    """Гасит истёкшие неиспользованные скидки.

    Строку НЕ удаляем, а помечаем used=true — она остаётся памятью «этому юзеру уже
    выдавали», чтобы грант-проход не выдал промо повторно (одна выдача на юзера).
    """
    expired = (
        await session.execute(
            text(
                "SELECT user_id, percent FROM trial_discounts "
                "WHERE used = false AND expires_at < now()"
            )
        )
    ).all()
    for uid, percent in expired:
        # Снимаем скидку только если она всё ещё равна выданной (юзер ей не
        # воспользовался и админ её не менял) — чтобы не затереть чужую скидку.
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
                "UPDATE trial_discounts SET used = true "
                "WHERE used = false AND expires_at < now()"
            )
        )
        await session.commit()
    return len(expired)


@broker.task(schedule=[{"cron": "7 * * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def run_trial_discount(
    session: FromDishka[AsyncSession],
    config: FromDishka[AppConfig],
) -> None:
    cfg = load_config()

    # Погашение просроченных делаем всегда (даже если фичу выключили — подчистить хвосты).
    await _expire_pass(session)

    if not cfg["enabled"]:
        return

    percent = cfg["percent"]
    days_before = cfg["days_before"]
    lifetime = cfg["lifetime_hours"]

    rows = (
        await session.execute(
            text(
                "SELECT u.id, lower(u.language::text), u.telegram_id "
                "FROM users u "
                "JOIN subscriptions s ON u.current_subscription_id = s.id "
                "LEFT JOIN trial_discounts td ON td.user_id = u.id "
                "WHERE u.role = 'USER' AND s.is_trial = true AND s.status = 'ACTIVE' "
                "AND s.expire_at >= now() "
                "AND s.expire_at < now() + make_interval(days => :n) "
                "AND td.user_id IS NULL"
            ),
            {"n": days_before},
        )
    ).all()

    if not rows:
        return

    bot: Bot | None = None
    try:
        bot = Bot(config.bot.token.get_secret_value())
    except Exception as e:  # noqa: BLE001 — без бота просто не шлём TG
        logger.warning(f"trial_discount: не смог создать Bot ({e}) — TG-напоминания пропущены")

    granted = 0
    for uid, lang, tg_id in rows:
        try:
            # Не понижаем уже имеющуюся бОльшую скидку.
            await session.execute(
                text(
                    "UPDATE users SET purchase_discount = GREATEST(purchase_discount, :p) "
                    "WHERE id = :u"
                ),
                {"p": percent, "u": uid},
            )
            await session.execute(
                text(
                    "INSERT INTO trial_discounts (user_id, percent, granted_at, expires_at, used) "
                    "VALUES (:u, :p, now(), now() + make_interval(hours => :h), false) "
                    "ON CONFLICT (user_id) DO NOTHING"
                ),
                {"u": uid, "p": percent, "h": lifetime},
            )
            await session.commit()
            granted += 1
        except Exception as e:  # noqa: BLE001
            await session.rollback()
            logger.warning(f"trial_discount: выдача user_id={uid} не удалась: {e}")
            continue

        # Уведомления best-effort — не влияют на выдачу.
        await notify_user_push(
            session,
            SimpleNamespace(id=uid, language=lang),
            _MSG,
            url="/billing",
            tag="trial-discount",
            percent=percent,
        )
        if bot is not None and tg_id:
            try:
                title, body = _MSG.get((lang or "ru")[:2], _MSG["ru"])
                await bot.send_message(
                    int(tg_id),
                    f"<b>{title.format(percent=percent)}</b>\n\n{body.format(percent=percent)}",
                )
            except Exception as e:  # noqa: BLE001 — заблокировал бота и т.п.
                logger.debug(f"trial_discount: TG user_id={uid} не доставлено: {e}")

    if bot is not None:
        try:
            await bot.session.close()
        except Exception:  # noqa: BLE001
            pass

    if granted:
        logger.info(f"trial_discount: выдана скидка {percent}% триальщикам — {granted} шт.")
