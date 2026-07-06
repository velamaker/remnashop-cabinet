"""Утренняя сводка владельцу в Telegram — раз в сутки одним сообщением.

За прошедшие сутки (вчерашний календарный день): выручка по валютам + число
оплат, новые регистрации, всего активных подписок, сколько истекает в ближайшие
N дней. Данные считаются на лету из БД (как /statistics) — всегда актуальны.

Шлём штатным notify_admins (raw-message) только владельцу (Role.OWNER). Базовый
образ шлёт свои уведомления сам — в него не влезаем, эта сводка самодостаточна.

Крон почасовой; внутри проверяем, что текущий час = настроенному (время правится
без правки cron). Дедуп по дате в assets/morning_summary_state.json — чтобы при
рестарте/повторе не слать дважды за день.

Настройка (тумблер/час/окно дней) — assets/morning_summary.json, правится из
админки (Настройки), см. services/overlay_morning_summary.py. Если файла ещё нет,
дефолты берутся из прежних env (MORNING_SUMMARY_ENABLED/HOUR/EXPIRING_DAYS).

Auto-discover taskiq по глобу tasks/*.py.
"""

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Notifier
from src.application.dto import MessagePayloadDto
from src.core.enums import Role
from src.infrastructure.services.overlay_morning_summary import load_config
from src.infrastructure.taskiq.broker import broker

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
STATE_PATH = ASSETS_DIR / "morning_summary_state.json"

# Символы валют для красивого вывода (фолбэк — сам код валюты).
_CURRENCY_SIGN = {"RUB": "₽", "USD": "$", "EUR": "€", "XTR": "⭐"}


def _load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"morning_summary: не смог сохранить состояние: {e}")


def _fmt_amount(amount: float) -> str:
    # Целые суммы без хвоста .00, дробные — с двумя знаками.
    return f"{amount:.0f}" if float(amount).is_integer() else f"{amount:.2f}"


@broker.task(schedule=[{"cron": "0 * * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def send_morning_summary(
    session: FromDishka[AsyncSession],
    notifier: FromDishka[Notifier],
) -> None:
    cfg = load_config()
    if not cfg["enabled"]:
        return
    if datetime.now().hour != cfg["hour"]:
        return

    today = date.today().isoformat()
    state = _load_state()
    if state.get("last_sent") == today:
        return  # уже слали сегодня

    days = cfg["expiring_days"]

    # Регистрации за вчера (только обычные пользователи).
    new_users = (
        await session.execute(
            text(
                """
                SELECT count(*) FROM users
                WHERE role::text = 'USER'
                  AND created_at >= date_trunc('day', now()) - interval '1 day'
                  AND created_at <  date_trunc('day', now())
                """
            )
        )
    ).scalar_one() or 0

    # Выручка за вчера по валютам + число оплат.
    revenue_rows = (
        await session.execute(
            text(
                """
                SELECT currency::text AS currency,
                       sum((pricing->>'final_amount')::numeric) AS amount,
                       count(*) AS cnt
                FROM transactions
                WHERE status::text = 'COMPLETED'
                  AND is_test = false
                  AND (pricing->>'final_amount')::numeric > 0
                  AND created_at >= date_trunc('day', now()) - interval '1 day'
                  AND created_at <  date_trunc('day', now())
                GROUP BY currency
                ORDER BY currency
                """
            )
        )
    ).all()

    # Всего активных подписок + истекают в ближайшие N дней.
    active_subs = (
        await session.execute(
            text("SELECT count(*) FROM subscriptions WHERE status::text = 'ACTIVE'")
        )
    ).scalar_one() or 0
    expiring = (
        await session.execute(
            text(
                """
                SELECT count(*) FROM subscriptions
                WHERE status::text = 'ACTIVE'
                  AND expire_at >= now()
                  AND expire_at < now() + make_interval(days => :n)
                """
            ),
            {"n": days},
        )
    ).scalar_one() or 0

    pay_count = sum(int(r.cnt or 0) for r in revenue_rows)
    if revenue_rows:
        parts = []
        for r in revenue_rows:
            sign = _CURRENCY_SIGN.get(r.currency, r.currency)
            parts.append(f"{_fmt_amount(float(r.amount or 0))} {sign}")
        revenue_line = ", ".join(parts) + f" ({pay_count} опл.)"
    else:
        revenue_line = "нет оплат"

    yday_str = (datetime.now() - timedelta(days=1)).strftime("%d.%m")

    body = (
        f"☀️ <b>Сводка за {yday_str}</b>\n\n"
        f"💰 Выручка: {revenue_line}\n"
        f"🆕 Новых регистраций: {int(new_users)}\n"
        f"📊 Активных подписок: {int(active_subs)}\n"
        f"⏳ Истекают в ближайшие {days} дн.: {int(expiring)}"
    )

    try:
        await notifier.notify_admins(
            payload=MessagePayloadDto(
                i18n_key="raw-message",
                i18n_kwargs={"content": body},
                delete_after=None,
            ),
            roles=[Role.OWNER],
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"morning_summary: не смог отправить сводку: {e}")
        return

    state["last_sent"] = today
    _save_state(state)
    logger.info("morning_summary: сводка владельцу отправлена")
