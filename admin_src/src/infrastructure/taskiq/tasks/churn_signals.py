"""Крон «сигналов до ухода» (overlay).

Раз в час: берём всех с действующей подпиской, спрашиваем панель, когда каждый
подключился впервые и когда был онлайн последний раз, и по `decide_check` /
`decide_idle` решаем, кому написать. Не больше одного сообщения человеку за проход.

ЧТО ЗДЕСЬ ВАЖНО ЗНАТЬ:
  • панель спрашиваем ОДИН раз за проход, постранично, целиком. Любая ошибка — весь
    ответ считается неизвестным, и проход никому не пишет: половина списка из
    упавшей панели сделала бы «давно не был» из каждого, кто не попал в первую
    страницу. Молчание панели — это «не знаем», а не «не подключался»;
  • выключено всё — крон выходит сразу, ни базы, ни панели не трогая;
  • захват строки идёт ДО отправки, отдельной транзакцией, и только по RETURNING:
    рестарт посреди прохода или параллельный проход второго сообщения не дают;
  • ошибка одного человека не роняет проход, ошибка прохода не роняет воркер
    (например, воркер стартовал раньше миграции 0015).

`run_once` не знает ни про Notifier, ни про aiogram, ни про панель: отправщик и
источник данных панели приходят снаружи, и в тестах подменяются. Сам отправщик
собирает `make_send_tg`, а сообщение — `build_payload`: обе вынесены из тела задачи,
чтобы тест видел ровно то, что уйдёт в Telegram (в первую очередь delete_after=None).
"""

import asyncio
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Optional

from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Notifier, Remnawave
from src.application.common.dao import UserDao
from src.application.dto import MessagePayloadDto
from src.core.config import AppConfig
from src.infrastructure.services.overlay_churn_signals import (
    CANDIDATES_SQL,
    CLAIM_SQL,
    KIND_CHECK,
    KIND_IDLE,
    OPTOUT_CHECK,
    OPTOUT_IDLE,
    RESULT_SQL,
    RETURNED_SQL,
    Candidate,
    PanelSeen,
    any_enabled,
    as_utc,
    check_keyboard,
    check_message,
    decide_check,
    decide_idle,
    idle_keyboard,
    idle_message,
    load_config,
    now_utc,
    panel_status,
    save_last_run,
    support_link,
)
from src.infrastructure.taskiq.broker import broker

# Потолок на проход: хвост подберёт следующий час, а полсотни сообщений подряд — это
# флуд-лимит Telegram. Окна у сигналов шире часа, поэтому никто не «проскакивает».
MAX_PER_RUN = 30
_SEND_PAUSE = 0.1
# Сколько людей максимум читаем из базы за проход — страховка, а не выборка.
MAX_CANDIDATES = 5000
# «Вернулся ли после письма» смотрим месяц: дальше это уже не про наше сообщение.
RETURN_WATCH_DAYS = 30

PANEL_PAGE = 500
PANEL_MAX_PAGES = 200

TG_SENT = "sent"
TG_BLOCKED = "blocked"
TG_FAILED = "failed"

# (кандидат, вид сигнала, html, id строки) → итог доставки.
SendTg = Callable[[Candidate, str, str, int], Awaitable[str]]
FetchPanel = Callable[[], Awaitable[Optional[dict[str, PanelSeen]]]]


def _row_to_candidate(row: Any) -> Candidate:
    data = row._mapping if hasattr(row, "_mapping") else row
    return Candidate(
        user_id=int(data["user_id"]),
        telegram_id=data["telegram_id"],
        lang=data["lang"],
        is_blocked=bool(data["is_blocked"]),
        is_bot_blocked=bool(data["is_bot_blocked"]),
        role=str(data["role"]),
        remna_uuid=str(data["remna_uuid"]).lower(),
        is_trial=bool(data["is_trial"]),
        expire_at=data["expire_at"],
        opted_out=bool(data["opted_out"]),
        frozen=bool(data["frozen"]),
        check_done=bool(data["check_done"]),
        idle_last_sent=data["idle_last_sent"],
        idle_open_id=data["idle_open_id"],
        idle_open_sent_at=data["idle_open_sent_at"],
    )


def _new_report() -> dict[str, Any]:
    return {
        "sent": Counter(),
        "failed": 0,
        "errors": 0,
        "returned": 0,
        "skipped_check": Counter(),
        "skipped_idle": Counter(),
    }


async def run_once(
    session: AsyncSession,
    *,
    fetch_panel: FetchPanel,
    send_tg: SendTg,
    now: Optional[datetime] = None,
    cfg: Optional[dict[str, Any]] = None,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> dict[str, Any]:
    """Один проход. Отчёт: сколько ушло и кого пропустили почему."""
    cfg = cfg or load_config()
    report = _new_report()
    if not any_enabled(cfg):
        report["disabled"] = True
        return report

    now = now or now_utc()
    rows = (
        await session.execute(
            text(CANDIDATES_SQL),
            {
                "optout_check": OPTOUT_CHECK,
                "optout_idle": OPTOUT_IDLE,
                "returned_since": now - timedelta(days=RETURN_WATCH_DAYS),
                "now": now,
                "limit": MAX_CANDIDATES,
            },
        )
    ).all()
    await session.rollback()  # выборка только читает: держать транзакцию незачем
    if len(rows) >= MAX_CANDIDATES:
        logger.warning(
            f"churn_signals: выборка упёрлась в потолок {MAX_CANDIDATES} — часть людей "
            "этот проход не увидел. Читаем от новых к старым, поэтому дальше всех "
            "оказываются самые давние аккаунты."
        )
    if not rows:
        return report

    try:
        panel = await fetch_panel()
    except Exception as exc:  # noqa: BLE001 — молчание панели ≠ «не подключался»
        logger.warning(f"churn_signals: панель не ответила ({exc}) — проход без сообщений")
        panel = None
    if panel is None:
        report["panel_unavailable"] = True
        return report

    for row in rows:
        c = _row_to_candidate(row)
        seen = panel.get(c.remna_uuid)
        await _mark_returned(session, c, seen, report)

        kind: Optional[str] = None
        if cfg["check_enabled"]:
            reason = decide_check(c, seen, cfg, now)
            if reason is None:
                kind = KIND_CHECK
            else:
                report["skipped_check"][reason] += 1
        if kind is None and cfg["idle_enabled"]:
            reason = decide_idle(c, seen, cfg, now)
            if reason is None:
                kind = KIND_IDLE
            else:
                report["skipped_idle"][reason] += 1
        if kind is None:
            continue
        if sum(report["sent"].values()) + report["failed"] >= MAX_PER_RUN:
            # Не пишем строку: окно шире часа, следующий проход подберёт.
            report[f"skipped_{kind}"]["run_cap"] += 1
            continue

        seen_at = (
            as_utc(seen.first_connected_at if kind == KIND_CHECK else seen.online_at)
            if seen is not None
            else None
        )
        if seen_at is None:
            # decide_* без данных панели писать не разрешают; вторая дверь на случай,
            # если правило когда-нибудь ослабят, — без факта из панели не пишем никогда.
            report[f"skipped_{kind}"]["no_panel_data"] += 1
            continue
        try:
            claimed = (
                await session.execute(
                    text(CLAIM_SQL),
                    {"user_id": c.user_id, "kind": kind, "seen_at": seen_at, "now": now},
                )
            ).first()
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            report["errors"] += 1
            logger.warning(f"churn_signals: захват user_id={c.user_id} не удался: {exc}")
            continue
        if claimed is None:
            # Уникальный индекс: уже спрашивали / уже писали про этот простой.
            report[f"skipped_{kind}"]["already_handled"] += 1
            continue
        signal_id = int(claimed[0])

        if kind == KIND_CHECK:
            html = check_message(c.lang)
        else:
            html = idle_message(int((now - seen_at).days), c.lang)
        try:
            result = await send_tg(c, kind, html, signal_id)
        except Exception as exc:  # noqa: BLE001 — один человек не роняет проход
            result = TG_FAILED
            logger.warning(f"churn_signals: сообщение user_id={c.user_id} не ушло: {exc}")

        sent = result == TG_SENT
        if sent:
            report["sent"][kind] += 1
        else:
            report["failed"] += 1
        try:
            await session.execute(
                text(RESULT_SQL),
                {
                    "id": signal_id,
                    "status": "sent" if sent else "failed",
                    "tg_result": result,
                    "sent": sent,
                    "now": now,
                },
            )
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            logger.warning(f"churn_signals: итог отправки не записан: {exc}")
        await sleep(_SEND_PAUSE)
    return report


async def _mark_returned(
    session: AsyncSession, c: Candidate, seen: Optional[PanelSeen], report: dict[str, Any]
) -> None:
    """После «давно не был» человек подключился — сообщение, похоже, сработало."""
    if c.idle_open_id is None or seen is None:
        return
    online = as_utc(seen.online_at)
    sent_at = as_utc(c.idle_open_sent_at)
    if online is None or sent_at is None or online <= sent_at:
        return
    try:
        await session.execute(text(RETURNED_SQL), {"id": c.idle_open_id, "returned_at": online})
        await session.commit()
        report["returned"] += 1
    except Exception as exc:  # noqa: BLE001 — отметка не важнее прохода
        await session.rollback()
        logger.debug(f"churn_signals: возвращение не отмечено: {exc}")


async def fetch_panel_seen(remnawave: Any) -> dict[str, PanelSeen]:
    """Первое подключение и последний онлайн всех пользователей панели.

    Исключение отсюда НЕ глотается: его ловит run_once и считает весь ответ
    неизвестным. Отдать половину списка хуже, чем ничего.
    """
    seen: dict[str, PanelSeen] = {}
    offset = 0
    for _ in range(PANEL_MAX_PAGES):
        batch = await remnawave.get_all_users(limit=PANEL_PAGE, offset=offset)
        for user in batch or []:
            uuid = getattr(user, "uuid", None)
            if uuid is None:
                continue
            seen[str(uuid).lower()] = PanelSeen(
                first_connected_at=as_utc(getattr(user, "first_connected_at", None)),
                online_at=as_utc(getattr(user, "online_at", None)),
                # Из того же ответа: отдельный запрос за статусом не нужен.
                status=panel_status(getattr(user, "status", None)),
            )
        if not batch or len(batch) < PANEL_PAGE:
            return seen
        offset += len(batch)
    # Панель длиннее потолка: кого не дочитали — тех просто нет в ответе, и они
    # пропускаются как «нет данных», а не считаются пропавшими.
    logger.warning(f"churn_signals: панель длиннее {PANEL_MAX_PAGES * PANEL_PAGE} — читаю часть")
    return seen


def _report_for_file(report: dict[str, Any], now: datetime) -> dict[str, Any]:
    return {
        "at": now.isoformat(),
        "panel_ok": not report.get("panel_unavailable", False),
        "sent_check": int(report["sent"].get(KIND_CHECK, 0)),
        "sent_idle": int(report["sent"].get(KIND_IDLE, 0)),
        "failed": int(report["failed"]),
        "errors": int(report["errors"]),
        "returned": int(report["returned"]),
        "skipped_check": dict(report["skipped_check"]),
        "skipped_idle": dict(report["skipped_idle"]),
    }


def build_payload(
    kind: str,
    html: str,
    signal_id: int,
    lang: Optional[str],
    cabinet_url: str,
    support_url: str,
) -> MessagePayloadDto:
    """Сообщение сигнала целиком: текст, кнопки своего вида и срок жизни."""
    markup = (
        check_keyboard(signal_id, lang)
        if kind == KIND_CHECK
        else idle_keyboard(cabinet_url, support_url, lang)
    )
    return MessagePayloadDto(
        i18n_key="raw-message",
        i18n_kwargs={"content": html},
        reply_markup=markup,
        disable_default_markup=True,
        # delete_after=None ОБЯЗАТЕЛЬНО: дефолт DTO — 5 секунд, и вопрос исчез бы
        # из чата раньше, чем человек успел бы нажать кнопку (грабля рассылок).
        delete_after=None,
    )


def make_send_tg(notifier: Any, user_dao: Any, cabinet_url: str, support_url: str) -> SendTg:
    """Отправщик для run_once: находит человека и шлёт ему собранное build_payload."""

    async def send_tg(c: Candidate, kind: str, html: str, signal_id: int) -> str:
        user = await user_dao.get_by_id(c.user_id)
        if user is None or user.telegram_id is None:
            return TG_BLOCKED
        payload = build_payload(kind, html, signal_id, c.lang, cabinet_url, support_url)
        message = await notifier.notify_user(user, payload=payload)
        return TG_SENT if message else TG_BLOCKED

    return send_tg


@broker.task(schedule=[{"cron": "17 * * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def run_churn_signals(
    session: FromDishka[AsyncSession],
    notifier: FromDishka[Notifier],
    user_dao: FromDishka[UserDao],
    remnawave: FromDishka[Remnawave],
    config: FromDishka[AppConfig],
) -> None:
    cfg = load_config()
    if not any_enabled(cfg):
        return
    send_tg = make_send_tg(
        notifier,
        user_dao,
        getattr(config, "web_cabinet_url", "") or "",
        support_link(config),
    )

    now = now_utc()
    try:
        report = await run_once(
            session,
            fetch_panel=lambda: fetch_panel_seen(remnawave),
            send_tg=send_tg,
            now=now,
            cfg=cfg,
        )
    except Exception as exc:  # noqa: BLE001 — например, воркер стартовал раньше миграции
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        logger.warning(f"churn_signals: проход не удался: {exc}")
        return

    save_last_run(_report_for_file(report, now))
    if report["sent"] or report["failed"] or report["errors"] or report.get("panel_unavailable"):
        logger.info(
            "churn_signals: «всё работает?» {check}, «давно не был» {idle}, не дошло {failed}, "
            "ошибок {errors}, панель {panel}".format(
                check=report["sent"].get(KIND_CHECK, 0),
                idle=report["sent"].get(KIND_IDLE, 0),
                failed=report["failed"],
                errors=report["errors"],
                panel="молчала" if report.get("panel_unavailable") else "ответила",
            )
        )
