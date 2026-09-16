"""Месячный дайджест пользователю: трафик за месяц + любимый сервер (overlay).

Крон почасовой, но реально работает раз в месяц: в настроенный день месяца и час
обходит активных USER с подпиской и (Telegram или push) и шлёт сводку за 30 дней —
сколько ГБ использовал и любимый сервер (данные из Remnawave bandwidthstats). Дедуп
по месяцу (assets/digest_state.json), чтобы не слать дважды.

Конфиг assets/digest.json (админка). Дефолт ВЫКЛ. Юзеров без трафика пропускаем.
Best-effort: ошибка по одному не роняет проход; пауза между вызовами SDK.

Письмом (тумблер `email_enabled`, тоже ВЫКЛ) — тем, у кого нет ни Telegram, ни
push, но есть подтверждённая почта. Тот же проход и те же цифры; вся механика
писем и их защиты — services/overlay_digest_email.py.
"""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from aiogram import Bot
from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Remnawave
from src.application.common.email_sender import EmailSender
from src.core.config import AppConfig
from src.infrastructure.services.overlay_digest import load_config
from src.infrastructure.services.overlay_digest_email import (
    SHORT_FAV as _FAV,
    SHORT_MESSAGES as _MSG,
    TG_AUDIENCE_SQL,
    DbLedger,
    fetch_usage,
    run_email_pass,
    select_email_audience_safe,
    sender_settings,
)
from src.infrastructure.services.overlay_push import _fill, notify_user_push
from src.infrastructure.taskiq.broker import broker

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
STATE_PATH = ASSETS_DIR / "digest_state.json"
_GB = 1024 ** 3
_SLEEP = 0.1


def _sent_month() -> str:
    try:
        return json.loads(STATE_PATH.read_text("utf-8")).get("month", "")
    except Exception:  # noqa: BLE001
        return ""


def _save_month(month: str) -> None:
    try:
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps({"month": month}), "utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"digest: не сохранил стейт: {e}")


@broker.task(schedule=[{"cron": "0 * * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def run_digest(
    session: FromDishka[AsyncSession],
    remnawave: FromDishka[Remnawave],
    config: FromDishka[AppConfig],
    email_sender: FromDishka[EmailSender],
) -> None:
    cfg = load_config()
    if not cfg["enabled"]:
        return

    now = datetime.now(timezone.utc)
    if now.day != cfg["day_of_month"] or now.hour != cfg["hour"]:
        return
    month_key = now.strftime("%Y-%m")
    if _sent_month() == month_key:
        return  # уже слали в этом месяце

    sdk = getattr(remnawave, "sdk", None)
    if sdk is None:
        return

    # Активные USER с подпиской и хотя бы одним каналом (Telegram или push).
    rows = (await session.execute(text(TG_AUDIENCE_SQL))).all()
    # Письмом — те, у кого нет ни одного из этих каналов. Выбираем сразу, ДО первой
    # отправки: условия двух выборок взаимоисключающие только на один и тот же
    # момент. Выбери письма после Telegram-части (а она идёт минутами) — человек,
    # отвязавший за это время Telegram, получил бы сводку дважды.
    email_rows = await select_email_audience_safe(session) if cfg["email_enabled"] else []

    # Отметим месяц сразу — чтобы при повторном запуске в тот же час не задваивать.
    _save_month(month_key)

    if not rows and not email_rows:
        return

    end = now
    start = end - timedelta(days=30)
    bot: Bot | None = None
    try:
        bot = Bot(config.bot.token.get_secret_value())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"digest: не смог создать Bot ({e}) — TG пропущены")

    sent = 0
    for uid, lang, tg_id, uuid in rows:
        usage = await fetch_usage(sdk, uuid, start, end)
        if usage is None:
            logger.debug(f"digest: usage user_id={uid} не получен")
            await asyncio.sleep(_SLEEP)
            continue
        total, fav_name = usage

        if total <= 0:
            await asyncio.sleep(_SLEEP)
            continue

        gb = round(total / _GB, 1)
        l = (lang or "ru")[:2]
        fav_txt = ""
        if fav_name:
            fav_txt = _fill(_FAV.get(l, _FAV["ru"]), {"name": fav_name})
        title, body_tpl = _MSG.get(l, _MSG["ru"])
        # Через _fill, а не .format(): оба вызова стоят внутри цикла по людям и
        # вне try, а у задачи retry_on_error=False — расхождение шаблона и
        # аргументов уронило бы ВЕСЬ проход, и месячная сводка не ушла бы никому
        # из очереди после. Текст в норме тот же.
        body = _fill(body_tpl, {"gb": gb, "fav": fav_txt})

        await notify_user_push(
            session, SimpleNamespace(id=uid, language=lang),
            _MSG, url="/", tag="digest", gb=gb, fav=fav_txt,
        )
        if bot is not None and tg_id:
            try:
                await bot.send_message(int(tg_id), f"<b>{title}</b>\n\n{body}")
            except Exception as e:  # noqa: BLE001
                logger.debug(f"digest: TG user_id={uid} не доставлено: {e}")
        sent += 1
        await asyncio.sleep(_SLEEP)

    if bot is not None:
        try:
            await bot.session.close()
        except Exception:  # noqa: BLE001
            pass

    logger.info(f"digest: месячная сводка отправлена {sent} юзерам")

    if email_rows:
        # notify_user_push глотает ошибки базы без отката: одна сорвавшаяся запись
        # в ленту оставила бы транзакцию прерванной, и журнал писем упал бы на
        # первом же INSERT — проход писем обнулился бы по чужой вине.
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        try:
            summary = await run_email_pass(
                sender=email_sender,
                cfg=cfg,
                settings=sender_settings(email_sender),
                sdk=sdk,
                secret=config.crypt_key.get_secret_value(),
                month=month_key,
                start=start,
                end=end,
                recipients=email_rows,
                ledger=DbLedger(session),
                session_for_feed=session,
            )
            logger.info(f"digest: письма — {summary}")
        except Exception as e:  # noqa: BLE001
            # Журнал недоступен — проход прерван до следующей записи. Кому письмо
            # успело уйти, у того строка уже `sent`/`sending`: повтора не будет.
            logger.error(f"digest: проход писем прерван: {type(e).__name__}: {e}")
