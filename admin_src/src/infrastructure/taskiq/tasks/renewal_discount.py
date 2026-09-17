"""Скидка на продление ДО окончания подписки: крон выдачи и погашения (overlay).

Крон раз в час (:47):
  • ПОГАШЕНИЕ — всегда, даже при выключенной фиче: воспользовались → used,
    срок вышел → скидку снимаем (если она всё ещё наша) и expired;
  • ДОСЫЛКА — выдача есть, а сообщение так и не занято (прогон упал между ними);
  • ВЫДАЧА — платящим клиентам, у которых подписка кончается примерно через N дней
    и которых не отсеяли правила (`decide` в services/overlay_renewal_discount.py);
  • СООБЩЕНИЕ — Telegram с кнопками «Продлить» и «Закрыть» и push. Людям только с
    почтой скидку сообщает письмо за 72 ч (email_expiry_reminders).

Порядок записи обязателен, и каждый шаг — отдельной транзакцией:
  1) выдача и скидка на человеке вместе: обе строки вернулись — commit, иначе
     откат (человек успел получить другую скидку — нашей не будет и строки тоже);
  2) захват сообщения (`notified_at`) → commit — рестарт посреди прохода не
     пришлёт второе сообщение;
  3) отправка; 4) итог доставки → commit.

`run_once` не знает ни про Notifier, ни про push: отправщики приходят снаружи.
Крон собирает настоящие, тесты и проверка на копии базы подставляют списки.

Конфиг assets/renewal_discount.json (админка). Дефолт ВЫКЛ. Авто-обнаруживается
taskiq по глобу tasks/*.py.
"""

import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Mapping, Optional

from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Notifier
from src.application.common.dao import UserDao
from src.infrastructure.services.overlay_push import notify_user_push
from src.infrastructure.services.overlay_renewal_discount import (
    APPLY_DISCOUNT_SQL,
    CLAIM_SQL,
    INSERT_GRANT_SQL,
    NOTIFY_RESULT_SQL,
    PENDING_NOTIFY_SQL,
    TG_BLOCKED,
    TG_FAILED,
    TG_NO_TELEGRAM,
    TG_SENT,
    Candidate,
    Env,
    PaidFacts,
    build_messages,
    build_tg_payload,
    close_pass,
    decide,
    grant_expires_at,
    load_candidates,
    load_config,
)
from src.infrastructure.taskiq.broker import broker

# Не больше 50 выдач за прогон: крон почасовой, и хвост уйдёт следующим часом, а
# сотня сообщений подряд — это флуд-лимит Telegram и пачка жалоб разом.
MAX_GRANTS_PER_RUN = 50
_SEND_PAUSE = 0.1
# Досылаем только то, что висит дольше 10 минут: свежую выдачу, возможно, прямо
# сейчас отправляет параллельный прогон.
RESEND_AFTER = timedelta(minutes=10)

SendTg = Callable[[int, Any], Awaitable[str]]
SendPush = Callable[[int, str, dict], Awaitable[int]]


def _error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:300]


async def _send_tg(
    send_tg: SendTg, user_id: int, payload: Any, sleep: Callable[[float], Awaitable[Any]]
) -> tuple[str, Optional[str]]:
    """Отправка с одним повтором после флуд-лимита.

    `retry_after` читаем с исключения, а не ловим TelegramRetryAfter по классу:
    тесты и проверка на копии базы подставляют отправщиков без aiogram.
    """
    for attempt in (1, 2):
        try:
            return str(await send_tg(user_id, payload)), None
        except Exception as exc:  # noqa: BLE001 — один человек не роняет прогон
            retry_after = getattr(exc, "retry_after", None)
            if attempt == 1 and isinstance(retry_after, (int, float)):
                await sleep(float(retry_after) + 1)
                continue
            return TG_FAILED, _error(exc)
    return TG_FAILED, None  # недостижимо: цикл всегда возвращает


async def _deliver(
    session: AsyncSession,
    grant: Mapping[str, Any],
    *,
    send_tg: SendTg,
    send_push: SendPush,
    now: datetime,
    sleep: Callable[[float], Awaitable[Any]],
    report: dict[str, Any],
) -> None:
    claimed = (await session.execute(text(CLAIM_SQL), {"now": now, "id": grant["id"]})).first()
    await session.commit()
    if claimed is None:
        report["already_claimed"] += 1
        return

    lang = str(grant.get("lang") or "ru")
    msg = build_messages(
        int(grant["percent"]),
        lang,
        now_=now,
        sub_expire_at=grant["sub_expire_at"],
        grant_expires_at=grant["expires_at"],
    )
    errors: list[str] = []
    if grant.get("telegram_id") is None:
        tg = TG_NO_TELEGRAM
    elif grant.get("is_bot_blocked"):
        # Бот заблокирован — отправка заведомо не дойдёт, лишний запрос к Telegram ни к чему.
        tg = TG_BLOCKED
    else:
        tg, err = await _send_tg(send_tg, int(grant["user_id"]), build_tg_payload(msg["telegram_html"]), sleep)
        if err:
            errors.append(err)

    push = 0
    try:
        # Push пишет и в ленту уведомлений кабинета — поэтому зовём для всех, даже
        # без подписанных устройств: предложение увидят в колокольчике.
        push = int(
            await send_push(
                int(grant["user_id"]),
                lang,
                {"ru": (msg["push_title"], msg["push_body"]), lang[:2]: (msg["push_title"], msg["push_body"])},
            )
            or 0
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(_error(exc))

    report["tg"][tg] += 1
    report["push"] += push
    await session.execute(
        text(NOTIFY_RESULT_SQL),
        {"tg": tg, "push": push, "err": ("; ".join(errors)[:300] or None), "id": grant["id"]},
    )
    await session.commit()


async def _grant(
    session: AsyncSession, c: Candidate, cfg: Mapping[str, Any], now: datetime, env: Env
) -> Optional[int]:
    """Выдача и скидка на человеке — одной транзакцией. None — не выдали."""
    percent = int(cfg["percent"])
    row = (
        await session.execute(
            text(INSERT_GRANT_SQL),
            {
                "u": c.user_id,
                "sub": c.subscription_id,
                "sub_expire_at": c.expire_at,
                "p": percent,
                "now": now,
                "expires_at": grant_expires_at(now, cfg, c.expire_at),
            },
        )
    ).first()
    if row is None:
        await session.rollback()
        return None
    applied = (
        await session.execute(
            text(APPLY_DISCOUNT_SQL),
            {"p": percent, "u": c.user_id, "autopay_guard": env.autopay_enabled},
        )
    ).first()
    if applied is None:
        await session.rollback()
        return None
    await session.commit()
    return int(row[0])


def _grant_row(grant_id: int, c: Candidate, cfg: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    return {
        "id": grant_id,
        "user_id": c.user_id,
        "percent": int(cfg["percent"]),
        "sub_expire_at": c.expire_at,
        "expires_at": grant_expires_at(now, cfg, c.expire_at),
        "lang": c.lang,
        "telegram_id": c.telegram_id,
        "is_bot_blocked": c.is_bot_blocked,
    }


async def run_once(
    session: AsyncSession,
    *,
    send_tg: SendTg,
    send_push: SendPush,
    now: Optional[datetime] = None,
    cfg: Optional[Mapping[str, Any]] = None,
    env: Optional[Env] = None,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    max_grants: int = MAX_GRANTS_PER_RUN,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    cfg = cfg if cfg is not None else load_config()
    env = env or Env.current()
    report: dict[str, Any] = {
        "used": 0,
        "expired": 0,
        "resent": 0,
        "examined": 0,
        "granted": 0,
        "lost_race": 0,
        "already_claimed": 0,
        "errors": 0,
        "skipped": Counter(),
        "tg": Counter(),
        "push": 0,
    }

    report["used"], report["expired"] = await close_pass(session, now)
    # Выключено — никому ничего не выдаём и не пишем, досылку тоже не делаем.
    if not cfg["enabled"]:
        return report

    pending = (
        await session.execute(
            text(PENDING_NOTIFY_SQL), {"stale_before": now - RESEND_AFTER, "now": now}
        )
    ).mappings().all()
    for grant in pending:
        try:
            await _deliver(
                session, grant, send_tg=send_tg, send_push=send_push, now=now, sleep=sleep, report=report
            )
            report["resent"] += 1
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            report["errors"] += 1
            logger.warning(f"renewal_discount: досылка grant={grant['id']} не удалась: {exc}")
        await sleep(_SEND_PAUSE)

    candidates, facts = await load_candidates(session, cfg, now)
    report["examined"] = len(candidates)
    for c in candidates:
        if report["granted"] >= max_grants:
            report["skipped"]["run_limit"] += 1
            continue
        reason = decide(c, facts.get(c.user_id, PaidFacts()), cfg, now, env)
        if reason is not None:
            report["skipped"][reason] += 1
            continue
        try:
            grant_id = await _grant(session, c, cfg, now, env)
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            report["errors"] += 1
            logger.warning(f"renewal_discount: выдача user_id={c.user_id} не удалась: {exc}")
            continue
        if grant_id is None:
            report["lost_race"] += 1
            continue
        report["granted"] += 1
        try:
            await _deliver(
                session,
                _grant_row(grant_id, c, cfg, now),
                send_tg=send_tg,
                send_push=send_push,
                now=now,
                sleep=sleep,
                report=report,
            )
        except Exception as exc:  # noqa: BLE001 — выдача уже есть, досылка подберёт
            await session.rollback()
            report["errors"] += 1
            logger.warning(f"renewal_discount: сообщение user_id={c.user_id} не отправлено: {exc}")
        await sleep(_SEND_PAUSE)
    return report


@broker.task(schedule=[{"cron": "47 * * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def run_renewal_discount(
    session: FromDishka[AsyncSession],
    notifier: FromDishka[Notifier],
    user_dao: FromDishka[UserDao],
) -> None:
    async def send_tg(user_id: int, payload: Any) -> str:
        user = await user_dao.get_by_id(user_id)
        if user is None or user.telegram_id is None:
            return TG_NO_TELEGRAM
        # None — заблокировал бота или чата нет (overlay notifications гасит это
        # тихо); остальные ошибки летят наружу и становятся `failed`.
        message = await notifier.notify_user(user, payload=payload)
        return TG_SENT if message else TG_BLOCKED

    async def send_push(user_id: int, lang: str, messages: dict) -> int:
        return await notify_user_push(
            session,
            SimpleNamespace(id=user_id, language=lang),
            messages,
            url="/billing",
            tag="renewal-discount",
        )

    try:
        report = await run_once(session, send_tg=send_tg, send_push=send_push)
    except Exception as exc:  # noqa: BLE001 — например, воркер стартовал раньше миграции
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        logger.warning(f"renewal_discount: прогон не удался: {exc}")
        return

    if report["granted"] or report["used"] or report["expired"] or report["resent"] or report["errors"]:
        logger.info(
            "renewal_discount: выдано {granted}, досылка {resent}, воспользовались {used}, "
            "сгорело {expired}, ошибок {errors}; telegram {tg}; push {push}; отказы {skipped}".format(
                granted=report["granted"],
                resent=report["resent"],
                used=report["used"],
                expired=report["expired"],
                errors=report["errors"],
                tg=dict(report["tg"]),
                push=report["push"],
                skipped=dict(report["skipped"]),
            )
        )
