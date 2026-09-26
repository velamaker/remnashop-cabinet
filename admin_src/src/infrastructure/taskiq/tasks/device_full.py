"""«Все места для устройств заняты» — крон (overlay).

Раз в 20 минут: снимок устройств панели (тем же путём, что «новое устройство»:
GET /hwid/devices и мост t_id→uuid для панели 2.8+, но СТРОГО: неполный список —
сбой прохода), сколько устройств у каждого нашего человека, сравнение с прошлым
снимком, сообщение тем, у кого только что заняли последнее место. Правила, тексты и
состояние — services/overlay_device_full.py.

Решение «кому писать» — plan_run(), чистая функция: крон только собирает для неё
данные и исполняет результат. Так каждую ветку видно в тестах без панели и Telegram.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Optional

from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger
from remnapy import RemnawaveSDK
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Notifier
from src.application.common.dao import UserDao
from src.application.dto import MessagePayloadDto
from src.core.config import AppConfig
from src.core.utils.time import datetime_now
from src.infrastructure.services import overlay_device_full as df
from src.infrastructure.services.overlay_cron_guard import cron_failed, cron_guard, cron_skipped
from src.infrastructure.taskiq.broker import broker

SENT = "sent"
BLOCKED = "blocked"

# «Кому писали» храним не вечно: после окна cooldown запись больше ни на что не влияет.
_SENT_KEEP = timedelta(days=90)

SendTg = Callable[[Any, int], Awaitable[str]]


def count_devices(
    devices: list[dict[str, Any]], known_uuids: set[str], tid_to_uuid: dict[int, str]
) -> Counter:
    """Сколько РАЗНЫХ устройств (по hwid) у каждого нашего пользователя панели."""
    seen: set[tuple[str, str]] = set()
    counts: Counter = Counter()
    for dev in devices:
        uuid = dev.get("userUuid")
        if not uuid:
            tid = dev.get("userId")
            try:
                uuid = tid_to_uuid.get(int(tid)) if tid is not None else None
            except (TypeError, ValueError):
                uuid = None
        hwid = str(dev.get("hwid") or "").strip()
        if not uuid or not hwid or str(uuid) not in known_uuids:
            continue
        key = (str(uuid), hwid)
        if key in seen:
            continue
        seen.add(key)
        counts[str(uuid)] += 1
    return counts


def plan_run(
    rows: list[Any],
    counts: Counter,
    state: dict[str, Any],
    cfg: dict[str, Any],
    now: datetime,
    max_send: int = df.MAX_PER_RUN,
) -> tuple[list[tuple[Any, int]], dict[str, str], dict[str, Any]]:
    """Кому писать, какой снимок «заполнен» сохранить и итог прохода. Чистая функция.

    Кому не дошла очередь из-за потолка прохода, в снимок «заполнен» НЕ попадает:
    следующий проход увидит у него тот же переход и напишет.
    """
    report: dict[str, Any] = {"full_now": 0, "sent": 0, "queued": 0, "skipped": Counter()}
    prev_full: dict[str, str] = state.get("full") or {}
    new_full: dict[str, str] = {}
    to_send: list[tuple[Any, int]] = []
    stamp = now.isoformat()

    for row in rows:
        uid = str(row.user_id)
        used = int(counts.get(str(row.remna_uuid), 0))
        full = df.is_full(used, row.device_limit)
        if full:
            report["full_now"] += 1
            new_full[uid] = prev_full.get(uid) or stamp

        reason = df.skip_reason(row)
        if reason:
            report["skipped"][reason] += 1
            continue
        if not state.get("baselined"):
            continue
        verdict = df.decide(
            was_full=uid in prev_full,
            now_full=full,
            last_sent=(state.get("sent") or {}).get(uid),
            now=now,
            cooldown_days=int(cfg.get("cooldown_days", df.DEFAULT_CONFIG["cooldown_days"])),
        )
        if verdict != "send":
            report["skipped"][verdict] += 1
            continue
        if len(to_send) >= max_send:
            report["queued"] += 1
            new_full.pop(uid, None)  # напишем следующим проходом
            continue
        to_send.append((row, used))

    return to_send, new_full, report


async def run_once(
    session: Any,
    *,
    devices: list[dict[str, Any]],
    tid_to_uuid: dict[int, str],
    send_tg: SendTg,
    now: datetime,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    rows = (await session.execute(text(df.CANDIDATES_SQL), {"optout_kind": df.OPTOUT_KIND})).all()
    await session.rollback()  # только читали — держать транзакцию незачем
    known = {str(r.remna_uuid) for r in rows if r.remna_uuid}
    counts = count_devices(devices, known, tid_to_uuid)

    state = df.load_state()
    # Снимок устарел (крон долго падал или его выключали, а отметку baseline никто не
    # снял) — сравнивать с ним нельзя: этот проход только пересобирает снимок.
    stale = bool(state.get("baselined")) and df.snapshot_stale(state, now)
    if stale:
        state = {**state, "baselined": False}
    to_send, new_full, report = plan_run(rows, counts, state, cfg, now)

    sent_log: dict[str, str] = {
        uid: ts
        for uid, ts in (state.get("sent") or {}).items()
        if (parsed := df._parse(ts)) is not None and now - parsed < _SENT_KEEP
    }
    failed = 0
    for row, used in to_send:
        try:
            result = await send_tg(row, used)
        except Exception as exc:  # noqa: BLE001 — один человек не должен ронять проход
            logger.warning(f"device_full: user_id={row.user_id} не отправлено: {exc}")
            result = BLOCKED
        if result == SENT:
            report["sent"] += 1
            sent_log[str(row.user_id)] = now.isoformat()
        else:
            # Не дошло (заблокировал бота и т.п.) — в снимке он всё равно «заполнен»:
            # стучаться каждые 20 минут в закрытую дверь незачем.
            failed += 1

    baseline = not state.get("baselined")
    report["baseline"] = baseline
    report["stale"] = stale
    report["failed"] = failed
    report["skipped"] = dict(report["skipped"])
    df.save_state(
        {
            "baselined": True,
            "full": new_full,
            "sent": sent_log,
            "last_run": {"at": now.isoformat(), **{k: v for k, v in report.items()}},
        }
    )
    return report


def make_send_tg(notifier: Any, user_dao: Any, cabinet_url: str, can_buy: bool) -> SendTg:
    async def send_tg(row: Any, used: int) -> str:
        user = await user_dao.get_by_id(int(row.user_id))
        if user is None or user.telegram_id is None:
            return BLOCKED
        # Текст и кнопка — от одного решения: «можно докупить» без кнопки «Докупить»
        # (продажа мест выключена или нет адреса кабинета) звало бы в никуда.
        buy = df.offers_buy(cabinet_url, can_buy)
        payload = MessagePayloadDto(
            i18n_key="raw-message",
            i18n_kwargs={"content": df.message_text(used, int(row.device_limit), row.lang, buy=buy)},
            reply_markup=df.keyboard(cabinet_url, can_buy=can_buy, lang=row.lang),
            disable_default_markup=True,
            # delete_after=None ОБЯЗАТЕЛЬНО: дефолт DTO — 5 секунд, и сообщение исчезло бы
            # раньше, чем человек его прочитал бы (грабля рассылок).
            delete_after=None,
        )
        message = await notifier.notify_user(user, payload=payload)
        return SENT if message else BLOCKED

    return send_tg


def _can_buy_slot() -> bool:
    """Продаются ли сейчас места. Не просто тумблер: включённая докупка без цены — это
    «не продаём» (effective_enabled), и кнопка «Докупить» вела бы в пустой раздел."""
    try:
        from src.infrastructure.services.overlay_extra_device import effective_enabled
        from src.infrastructure.services.overlay_extra_device import load_config as extra_cfg

        return effective_enabled(extra_cfg())
    except Exception:  # noqa: BLE001 — без докупки остаётся «освободить место»
        return False


@broker.task(schedule=[{"cron": "*/20 * * * *"}], retry_on_error=False)
@inject(patch_module=True)
@cron_guard("device_full", "Сообщение «все места для устройств заняты»")
async def run_device_full(
    session: FromDishka[AsyncSession],
    notifier: FromDishka[Notifier],
    user_dao: FromDishka[UserDao],
    config: FromDishka[AppConfig],
    sdk: FromDishka[RemnawaveSDK],
) -> None:
    cfg = df.load_config()
    if not cfg["enabled"]:
        cron_skipped()
        return

    # Снимок устройств — тем же кодом, что у «нового устройства»: одна точка правды о
    # том, как читать /hwid/devices и как на 2.8+ сводить числовой userId с uuid.
    from src.infrastructure.taskiq.tasks.abuse_hwid import _fetch_tid_to_uuid
    from src.infrastructure.taskiq.tasks.new_device import _fetch_devices

    try:
        # Строго: сбой на второй+ странице иначе дал бы НЕПОЛНЫЙ список — недочитанные
        # люди выпали бы из снимка «заполнен», и следующий полный проход написал бы им,
        # хотя места заняты давно. Неполный список — сбой прохода, снимок не трогаем.
        devices = await _fetch_devices(config, strict=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"device_full: не получил устройства: {exc}")
        cron_failed(exc)
        return
    if not devices:
        # Пустой ответ — это «панель не ответила», а не «ни у кого нет устройств»: иначе
        # снимок «никто не заполнен» и следующий проход поздравил бы всех заполненных.
        cron_failed("панель не отдала ни одного устройства — проход пропущен")
        return

    tid_to_uuid: dict[int, str] = {}
    if any(d.get("userUuid") is None and d.get("userId") is not None for d in devices):
        uuids = [
            str(r[0])
            for r in (
                await session.execute(
                    text(
                        "SELECT DISTINCT s.user_remna_id FROM subscriptions s "
                        "WHERE s.user_remna_id IS NOT NULL AND s.status::text <> 'DELETED'"
                    )
                )
            ).all()
        ]
        await session.rollback()
        try:
            tid_to_uuid = await _fetch_tid_to_uuid(config, sdk, uuids)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"device_full: карта t_id→uuid не получена: {exc}")
        if not tid_to_uuid:
            cron_failed("карта t_id→uuid пуста — занятые места не проверены")
            return

    send_tg = make_send_tg(notifier, user_dao, getattr(config, "web_cabinet_url", "") or "", _can_buy_slot())
    try:
        report = await run_once(
            session,
            devices=devices,
            tid_to_uuid=tid_to_uuid,
            send_tg=send_tg,
            now=datetime_now(),
            cfg=cfg,
        )
    except Exception as exc:  # noqa: BLE001
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        logger.warning(f"device_full: проход не удался: {exc}")
        cron_failed(exc)
        return

    if report["baseline"]:
        why = "снимок устарел" if report.get("stale") else "первый проход после включения"
        logger.info(f"device_full: baseline ({why}) — заполнены сейчас {report['full_now']}, им не пишем")
    elif report["sent"] or report["failed"] or report["queued"]:
        logger.info(
            f"device_full: написали {report['sent']}, не дошло {report['failed']}, "
            f"в очереди {report['queued']}"
        )
