"""Месячный дайджест пользователю: трафик за месяц + любимый сервер (overlay).

Крон почасовой, но реально работает раз в месяц: в настроенный день месяца и час
обходит активных USER с подпиской и (Telegram или push) и шлёт сводку за 30 дней —
сколько ГБ использовал и любимый сервер (данные из Remnawave bandwidthstats). Дедуп
по месяцу (assets/digest_state.json), чтобы не слать дважды.

Конфиг assets/digest.json (админка). Дефолт ВЫКЛ. Юзеров без трафика пропускаем.
Best-effort: ошибка по одному не роняет проход; пауза между вызовами SDK.
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
from src.core.config import AppConfig
from src.infrastructure.services.overlay_digest import load_config
from src.infrastructure.services.overlay_push import notify_user_push
from src.infrastructure.taskiq.broker import broker

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
STATE_PATH = ASSETS_DIR / "digest_state.json"
_GB = 1024 ** 3
_SLEEP = 0.1

_MSG = {
    "ru": (
        "📊 Ваш месяц с VPN",
        "За месяц вы использовали {gb} ГБ.{fav} Спасибо, что с нами!",
    ),
    "en": (
        "📊 Your month with VPN",
        "This month you used {gb} GB.{fav} Thanks for being with us!",
    ),
}
_FAV = {"ru": " Любимый сервер — {name}.", "en": " Favorite server — {name}."}


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
    rows = (
        await session.execute(
            text(
                "SELECT u.id, lower(u.language::text), u.telegram_id, s.user_remna_id "
                "FROM users u "
                "JOIN subscriptions s ON u.current_subscription_id = s.id "
                "WHERE u.role = 'USER' AND s.status = 'ACTIVE' AND s.user_remna_id IS NOT NULL "
                "AND (u.telegram_id IS NOT NULL "
                "     OR EXISTS(SELECT 1 FROM push_subscriptions p WHERE p.user_id = u.id))"
            )
        )
    ).all()

    # Отметим месяц сразу — чтобы при повторном запуске в тот же час не задваивать.
    _save_month(month_key)

    if not rows:
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
        try:
            result = await sdk.bandwidthstats.get_stats_user_usage(
                uuid=str(uuid),
                top_nodes_limit=5,
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
            )
            data = getattr(result, "root", result)
            nodes = getattr(data, "top_nodes", None) or []
            total = sum(int(getattr(n, "total", 0) or 0) for n in nodes)
            fav = max(nodes, key=lambda n: int(getattr(n, "total", 0) or 0), default=None)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"digest: usage user_id={uid} не получен: {e}")
            await asyncio.sleep(_SLEEP)
            continue

        if total <= 0:
            await asyncio.sleep(_SLEEP)
            continue

        gb = round(total / _GB, 1)
        l = (lang or "ru")[:2]
        fav_txt = ""
        if fav is not None and getattr(fav, "name", None):
            fav_txt = _FAV.get(l, _FAV["ru"]).format(name=fav.name)
        title, body_tpl = _MSG.get(l, _MSG["ru"])
        body = body_tpl.format(gb=gb, fav=fav_txt)

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
