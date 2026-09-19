"""Крон напоминаний о незавершённой оплате (overlay).

Раз в 5 минут: берём неоплаченные счета в окне [задержка; потолок], по каждому решаем
`decide()` и, если писать можно, отправляем ОДНО сообщение в Telegram.

ЧТО ЗДЕСЬ ВАЖНО ЗНАТЬ:
  • ссылку на старый счёт не шлём никогда — кнопка ведёт в кабинет, где счёт создаётся
    заново (почему именно так — в миграции 0013 и в services/overlay_payment_reminder);
  • захват строки идёт ДО отправки и отдельной транзакцией: рестарт посреди прохода не
    даёт второго сообщения, а досылки нет намеренно;
  • выключатель гасит именно рассылку, а не только запись: выключено — крон выходит
    сразу, ничего не читая;
  • ошибка одного человека не роняет прогон, а ошибка прогона не роняет воркер
    (например, воркер стартовал раньше миграции 0013).

`run_once` не знает ни про Notifier, ни про aiogram: отправщик приходит снаружи, и в
тестах он подменяется списком.
"""

import asyncio
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Optional

from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Notifier
from src.application.common.dao import UserDao
from src.core.config import AppConfig
from src.infrastructure.services.overlay_payment_reminder import (
    CANDIDATES_SQL,
    CLAIM_SQL,
    OPTOUT_KIND,
    RESULT_SQL,
    SKIP_SQL,
    TRANSIENT_REASONS,
    Candidate,
    build_message,
    button_url,
    decide,
    effective_enabled,
    kind_of,
    load_config,
    now_utc,
    window_bounds,
)
from src.infrastructure.taskiq.broker import broker

# Потолок на прогон: крон частый, хвост подберёт следующий заход, а полсотни сообщений
# подряд — это флуд-лимит Telegram и пачка жалоб разом.
MAX_PER_RUN = 25
_SEND_PAUSE = 0.1

SendTg = Callable[[int, str, str], Awaitable[str]]

TG_SENT = "sent"
TG_BLOCKED = "blocked"
TG_FAILED = "failed"


def _row_to_candidate(row: Any) -> Candidate:
    data = row._mapping if hasattr(row, "_mapping") else row
    return Candidate(
        payment_id=str(data["payment_id"]),
        user_id=int(data["user_id"]),
        telegram_id=data["telegram_id"],
        lang=data["lang"],
        is_blocked=bool(data["is_blocked"]),
        is_bot_blocked=bool(data["is_bot_blocked"]),
        is_test=bool(data["is_test"]),
        role=str(data["role"]),
        plan_id=data["plan_id"],
        plan_name=data["plan_name"],
        amount=data["amount"],
        currency=data["currency"],
        created_at=data["created_at"],
        paid_after=bool(data["paid_after"]),
        newer_txn=bool(data["newer_txn"]),
        sub_touched=bool(data["sub_touched"]),
        opted_out=bool(data["opted_out"]),
        reminded_24h=bool(data["reminded_24h"]),
        reminded_30d=int(data["reminded_30d"] or 0),
        already_row=bool(data["already_row"]),
    )


async def run_once(
    session: AsyncSession,
    *,
    send_tg: SendTg,
    cabinet_url: Optional[str] = None,
    now: Optional[datetime] = None,
    cfg: Optional[dict[str, Any]] = None,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> dict[str, Any]:
    """Один проход. Возвращает отчёт: сколько отправлено и кого пропустили почему."""
    cfg = cfg or load_config()
    report: dict[str, Any] = {"sent": 0, "failed": 0, "skipped": Counter(), "errors": 0}
    if not effective_enabled(cfg):
        report["disabled"] = True
        return report

    now = now or now_utc()
    start, end = window_bounds(cfg, now)
    rows = (
        await session.execute(
            text(CANDIDATES_SQL),
            {
                "window_start": start,
                "window_end": end,
                "optout_kind": OPTOUT_KIND,
                "cooldown_since": now - timedelta(hours=int(cfg["cooldown_hours"])),
                "month_since": now - timedelta(days=30),
                "limit": MAX_PER_RUN * 4,
            },
        )
    ).all()
    await session.rollback()  # выборка только читает: держать транзакцию незачем

    for row in rows:
        if report["sent"] >= MAX_PER_RUN:
            report["skipped"]["run_cap"] += 1
            continue
        c = _row_to_candidate(row)
        reason = decide(c, cfg, now)
        if reason is not None:
            report["skipped"][reason] += 1
            if reason not in TRANSIENT_REASONS and not c.already_row:
                # Причина окончательная — запоминаем, чтобы не считать этот счёт снова
                # и чтобы владелец видел в сводке, кого и почему не трогали.
                try:
                    await session.execute(text(SKIP_SQL), _row_params(c, reason=reason))
                    await session.commit()
                except Exception as exc:  # noqa: BLE001 — журнал не важнее прогона
                    await session.rollback()
                    logger.debug(f"payment_reminder: пропуск не записан: {exc}")
            continue

        try:
            await session.execute(text(CLAIM_SQL), _row_params(c))
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            report["errors"] += 1
            logger.warning(f"payment_reminder: захват счёта не удался: {exc}")
            continue

        kind = kind_of(c.plan_id)
        html = build_message(kind, c.amount, c.currency, c.lang)
        url = button_url(cabinet_url, kind)
        try:
            result = await send_tg(c.user_id, html, url)
        except Exception as exc:  # noqa: BLE001 — один человек не роняет прогон
            result = TG_FAILED
            logger.warning(f"payment_reminder: сообщение user_id={c.user_id} не ушло: {exc}")

        status = "sent" if result == TG_SENT else "failed"
        report["sent" if status == "sent" else "failed"] += 1
        try:
            await session.execute(
                text(RESULT_SQL),
                {
                    "payment_id": c.payment_id,
                    "status": status,
                    "tg_result": result,
                    "sent": status == "sent",
                },
            )
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            logger.warning(f"payment_reminder: итог отправки не записан: {exc}")
        await sleep(_SEND_PAUSE)
    return report


def _row_params(c: Candidate, reason: Optional[str] = None) -> dict[str, Any]:
    params = {
        "payment_id": c.payment_id,
        "user_id": c.user_id,
        "kind": kind_of(c.plan_id),
        "amount": c.amount,
        "currency": c.currency,
        "invoice_at": c.created_at,
    }
    if reason is not None:
        params["skip_reason"] = reason
    return params


@broker.task(schedule=[{"cron": "*/5 * * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def run_payment_reminder(
    session: FromDishka[AsyncSession],
    notifier: FromDishka[Notifier],
    user_dao: FromDishka[UserDao],
    config: FromDishka[AppConfig],
) -> None:
    async def send_tg(user_id: int, html: str, url: str) -> str:
        user = await user_dao.get_by_id(user_id)
        if user is None or user.telegram_id is None:
            return TG_BLOCKED
        from src.application.dto import MessagePayloadDto

        from src.infrastructure.services.overlay_payment_reminder_kb import reminder_keyboard

        payload = MessagePayloadDto(
            i18n_key="raw-message",
            i18n_kwargs={"content": html},
            reply_markup=reminder_keyboard(url, user.language),
            disable_default_markup=True,
            # delete_after=None ОБЯЗАТЕЛЬНО: дефолт DTO — 5 секунд, и сообщение
            # исчезло бы из чата (грабля рассылок).
            delete_after=None,
        )
        message = await notifier.notify_user(user, payload=payload)
        return TG_SENT if message else TG_BLOCKED

    try:
        report = await run_once(
            session, send_tg=send_tg, cabinet_url=getattr(config, "web_cabinet_url", "")
        )
    except Exception as exc:  # noqa: BLE001 — например, воркер стартовал раньше миграции
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        logger.warning(f"payment_reminder: прогон не удался: {exc}")
        return

    if report.get("sent") or report.get("failed") or report.get("errors"):
        logger.info(
            "payment_reminder: отправлено {sent}, не дошло {failed}, ошибок {errors}; "
            "пропуски {skipped}".format(
                sent=report["sent"],
                failed=report["failed"],
                errors=report["errors"],
                skipped=dict(report["skipped"]),
            )
        )
