"""Админ: массовое «Добавить N дней» и «Написать отфильтрованным» (раздел «Пользователи»).

Запуск в три шага: предпросмотр (GET, ничего не пишет) → подтверждение с отпечатком
выборки → фоновая задача (taskiq/tasks/bulk_jobs.py). Правила — в
services/overlay_bulk.py.

Кто может:
  * смотреть предпросмотр и задачи — любой админ с разделом «Пользователи», в том
    числе read-only (без внутренних id, ошибок и автора);
  * запускать, останавливать и продолжать — только полный доступ с правом записи
    (`require_full_access`): массовая раздача дней — другой класс власти, чем
    правка одного человека.

Пути многосегментные (`/users/bulk/...`), а роутер подключён раньше `users_router`:
карточка `/users/{user_id}` их не перехватит. Изменяющие вызовы попадают в журнал
действий общим аудитом (overlay_app). Overlay-эндпоинты коммитят сессию сами.
"""

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Notifier
from src.application.common.email_sender import EmailSender
from src.infrastructure.services.overlay_bulk import (
    KIND_DAYS,
    KIND_MESSAGE,
    MAX_USERS,
    ActiveJobExists,
    BulkStore,
    DuplicateRequest,
    TelegramRejected,
    days_items,
    days_params_hash,
    days_preview_payload,
    evaluate_days,
    evaluate_message,
    is_missing_table,
    item_to_dict,
    job_to_dict,
    message_items,
    message_params_hash,
    message_preview_payload,
    replay_decision,
    require_full_access,
    send_test_message,
    text_sha256,
    validate_channels,
    validate_days,
    validate_text,
)
from src.infrastructure.taskiq.tasks.bulk_jobs import run_bulk_job

from ._common import AdminUser
from ._redact import is_readonly_admin
from .users import build_segment

router = APIRouter(prefix="/users", tags=["Admin - Users bulk"])

_JOB_STATUSES = ("PENDING", "RUNNING", "RETRY", "SENDING", "DONE", "SKIPPED", "FAILED", "UNKNOWN")


# ── Общее ─────────────────────────────────────────────────────────────────────


def _filters(
    search: Optional[str], blocked: Optional[bool], role: Optional[int], expiring_days: Optional[int]
) -> dict[str, Any]:
    return {
        "search": (search or "").strip() or None,
        "blocked": blocked,
        "role": role,
        "expiring_days": expiring_days,
    }


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _check_expiring(expiring_days: Optional[int]) -> None:
    if expiring_days is not None and not 1 <= int(expiring_days) <= 365:
        raise _bad_request("Фильтр «истекают» — от 1 до 365 дней")


def _require_writer(request: Request) -> None:
    denial = require_full_access(getattr(request.state, "admin_access", None))
    if denial:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=denial)


def _actor(request: Request) -> str:
    return str(getattr(request.state, "audit_actor", None) or "—")


async def _guard(session: AsyncSession, exc: Exception) -> None:
    """Таблиц задач ещё нет (бот не перезапустился после обновления) — честный 503."""
    try:
        await session.rollback()
    except Exception:  # noqa: BLE001
        pass
    if is_missing_table(exc):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Идёт обновление — повторите через минуту",
        )
    raise exc


async def _segment_ids(session: AsyncSession, filters: dict[str, Any]) -> list[int]:
    join_sql, where_sql, params = build_segment(
        filters["search"], filters["blocked"], filters["role"], filters["expiring_days"], users_only=True
    )
    rows = (
        await session.execute(
            text(f"SELECT u.id FROM users u {join_sql} {where_sql} ORDER BY u.id LIMIT :cap"),
            {**params, "cap": MAX_USERS + 1},
        )
    ).all()
    if len(rows) > MAX_USERS:
        # Сверх предела — отказ, а не обрезка: половина выборки молча осталась бы ни с чем.
        count = (
            await session.execute(text(f"SELECT count(*) FROM users u {join_sql} {where_sql}"), params)
        ).scalar_one()
        raise _bad_request(
            f"Слишком большая выборка: {count} человек (максимум {MAX_USERS}) — сузьте фильтр"
        )
    return [int(r[0]) for r in rows]


async def _kick(job_id: int) -> None:
    try:
        await run_bulk_job.kiq(job_id)  # type: ignore[call-overload]
    except Exception as exc:  # noqa: BLE001 — задача в журнале, крон подберёт её за 5 минут
        logger.warning(f"bulk: задача №{job_id} не поставлена в очередь сразу: {exc}")


def _email_enabled(email_sender: EmailSender) -> bool:
    try:
        return bool(email_sender.is_enabled)
    except Exception:  # noqa: BLE001
        return False


def _duplicate_response(job: dict[str, Any], response: Response, count_key: str) -> dict[str, Any]:
    response.status_code = status.HTTP_200_OK
    params = job.get("params") or {}
    return {
        "job_id": int(job["id"]),
        "status": job["status"],
        "total": int(job.get("total") or 0),
        count_key: params.get(count_key),
        "duplicate": True,
    }


# ── «Добавить N дней» ─────────────────────────────────────────────────────────


@router.get("/bulk/days/preview")
@inject
async def preview_bulk_days(
    admin: AdminUser,
    session: FromDishka[AsyncSession],
    days: int = Query(...),
    search: Optional[str] = Query(default=None),
    blocked: Optional[bool] = Query(default=None),
    role: Optional[int] = Query(default=None),
    expiring_days: Optional[int] = Query(default=None),
    include_trial: bool = Query(default=False),
    include_limited: bool = Query(default=False),
) -> dict[str, Any]:
    try:
        days = validate_days(days)
    except ValueError as exc:
        raise _bad_request(str(exc))
    _check_expiring(expiring_days)
    store = BulkStore(session)
    try:
        ids = await _segment_ids(session, _filters(search, blocked, role, expiring_days))
        ev = await evaluate_days(
            store,
            ids,
            days=days,
            include_trial=include_trial,
            include_limited=include_limited,
            now=datetime.now(timezone.utc),
        )
        recently = await store.recent_days(ev.eligible_ids)
        active = await store.active_jobs()
        await session.rollback()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        await _guard(session, exc)
        raise
    return days_preview_payload(
        ev, recently=recently, active_job_id=active.get(KIND_DAYS), readonly=is_readonly_admin(admin)
    )


class BulkNotifyBody(BaseModel):
    text: str
    channels: list[str]


class BulkDaysBody(BaseModel):
    search: Optional[str] = None
    blocked: Optional[bool] = None
    role: Optional[int] = None
    expiring_days: Optional[int] = None
    days: int
    include_trial: bool = False
    include_limited: bool = False
    allow_repeat: bool = False
    segment_hash: str
    expected_apply: int = 0
    request_id: UUID
    notify: Optional[BulkNotifyBody] = None


@router.post("/bulk/days", status_code=status.HTTP_202_ACCEPTED)
@inject
async def start_bulk_days(
    body: BulkDaysBody,
    request: Request,
    response: Response,
    admin: AdminUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    _require_writer(request)
    try:
        days = validate_days(body.days)
        notify = (
            {"text": validate_text(body.notify.text), "channels": validate_channels(body.notify.channels)}
            if body.notify is not None
            else None
        )
    except ValueError as exc:
        raise _bad_request(str(exc))
    _check_expiring(body.expiring_days)
    filters = _filters(body.search, body.blocked, body.role, body.expiring_days)
    phash = days_params_hash(filters, days, body.include_trial, body.include_limited, notify)
    store = BulkStore(session)

    try:
        existing = await store.job_by_request(body.request_id)
        decision = replay_decision(existing["params_hash"] if existing else None, phash)
        if decision == "duplicate":
            await session.rollback()
            return _duplicate_response(existing, response, "apply")
        if decision == "conflict":
            raise _conflict("Этот запуск уже выполнен с другими параметрами — обновите страницу")

        ids = await _segment_ids(session, filters)
        ev = await evaluate_days(
            store,
            ids,
            days=days,
            include_trial=body.include_trial,
            include_limited=body.include_limited,
            now=datetime.now(timezone.utc),
        )
        eligible = ev.eligible_ids
        if ev.segment_hash != body.segment_hash:
            raise _conflict(
                "Выборка изменилась, пока вы смотрели предпросмотр: было "
                f"{body.expected_apply}, стало {len(eligible)}. Проверьте цифры ещё раз"
            )
        if not eligible:
            raise _bad_request("Некому добавлять дни: в выборке нет подходящих подписок")
        recently = await store.recent_days(eligible)
        if recently["count"] and not body.allow_repeat:
            raise _conflict(
                f"За последние 24 часа {recently['count']} из этих людей уже получили дни "
                f"(задача №{recently['job_id']}, +{recently['days']} дн.). Подтвердите повтор"
            )

        counts = ev.counts
        try:
            job_id = await store.create_job(
                kind=KIND_DAYS,
                request_id=body.request_id,
                params_hash=phash,
                parent_job_id=None,
                created_by=admin.id,
                created_by_label=_actor(request),
                params={
                    "filters": filters,
                    "days": days,
                    "include_trial": body.include_trial,
                    "include_limited": body.include_limited,
                    "notify": notify,
                    "apply": len(eligible),
                    "apply_frozen": counts.get("APPLY_FROZEN", 0),
                    "deferred": counts.get("RECENT_CHANGE", 0),
                },
                segment_hash=ev.segment_hash,
                items=days_items(ev),
            )
            await session.commit()
        except ActiveJobExists as exc:
            raise _conflict(f"Уже идёт задача №{exc.job_id} — дождитесь её или остановите")
        except DuplicateRequest:
            # Два одинаковых запроса разошлись на миллисекунды — второй получает первый.
            again = await store.job_by_request(body.request_id)
            await session.rollback()
            if again and again["params_hash"] == phash:
                return _duplicate_response(again, response, "apply")
            raise _conflict("Этот запуск уже выполнен с другими параметрами — обновите страницу")
    except HTTPException:
        await session.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        await _guard(session, exc)
        raise

    await _kick(job_id)
    return {"job_id": job_id, "status": "QUEUED", "total": len(ids), "apply": len(eligible), "duplicate": False}


# ── «Написать отфильтрованным» ────────────────────────────────────────────────


async def _message_ids(
    store: BulkStore, session: AsyncSession, filters: dict[str, Any], source_job_id: Optional[int]
) -> list[int]:
    if source_job_id is None:
        return await _segment_ids(session, filters)
    source = await store.get_job(int(source_job_id))
    if source is None or source["kind"] != KIND_DAYS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задача не найдена")
    return await store.done_user_ids(int(source_job_id))


@router.get("/bulk/message/preview")
@inject
async def preview_bulk_message(
    _admin: AdminUser,
    session: FromDishka[AsyncSession],
    email_sender: FromDishka[EmailSender],
    search: Optional[str] = Query(default=None),
    blocked: Optional[bool] = Query(default=None),
    role: Optional[int] = Query(default=None),
    expiring_days: Optional[int] = Query(default=None),
    source_job_id: Optional[int] = Query(default=None),
    channels: str = Query(default="telegram,cabinet,email"),
) -> dict[str, Any]:
    _check_expiring(expiring_days)
    picked = [c for c in channels.split(",") if c]
    try:
        picked = validate_channels(picked)
    except ValueError as exc:
        raise _bad_request(str(exc))
    email_enabled = _email_enabled(email_sender)
    store = BulkStore(session)
    try:
        ids = await _message_ids(store, session, _filters(search, blocked, role, expiring_days), source_job_id)
        ev = await evaluate_message(store, ids, channels=picked, email_enabled=email_enabled)
        active = await store.active_jobs()
        await session.rollback()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        await _guard(session, exc)
        raise
    return message_preview_payload(ev, email_enabled=email_enabled, active_job_id=active.get(KIND_MESSAGE))


class BulkMessageBody(BaseModel):
    search: Optional[str] = None
    blocked: Optional[bool] = None
    role: Optional[int] = None
    expiring_days: Optional[int] = None
    source_job_id: Optional[int] = None
    text: str
    channels: list[str]
    segment_hash: str
    expected_recipients: int = 0
    request_id: UUID
    allow_repeat: bool = False


@router.post("/bulk/message", status_code=status.HTTP_202_ACCEPTED)
@inject
async def start_bulk_message(
    body: BulkMessageBody,
    request: Request,
    response: Response,
    admin: AdminUser,
    session: FromDishka[AsyncSession],
    email_sender: FromDishka[EmailSender],
) -> dict[str, Any]:
    _require_writer(request)
    try:
        content = validate_text(body.text)
        channels = validate_channels(body.channels)
    except ValueError as exc:
        raise _bad_request(str(exc))
    _check_expiring(body.expiring_days)
    filters = (
        _filters(body.search, body.blocked, body.role, body.expiring_days) if body.source_job_id is None else None
    )
    phash = message_params_hash(filters, body.source_job_id, content, channels)
    store = BulkStore(session)

    try:
        existing = await store.job_by_request(body.request_id)
        decision = replay_decision(existing["params_hash"] if existing else None, phash)
        if decision == "duplicate":
            await session.rollback()
            return _duplicate_response(existing, response, "recipients")
        if decision == "conflict":
            raise _conflict("Этот запуск уже выполнен с другими параметрами — обновите страницу")

        ids = await _message_ids(store, session, filters or _filters(None, None, None, None), body.source_job_id)
        ev = await evaluate_message(store, ids, channels=channels, email_enabled=_email_enabled(email_sender))
        if ev.segment_hash != body.segment_hash:
            raise _conflict(
                "Выборка изменилась, пока вы смотрели предпросмотр: было "
                f"{body.expected_recipients}, стало {len(ev.recipients)}. Проверьте цифры ещё раз"
            )
        if not ev.recipients:
            raise _bad_request("Некому отправлять: в выборке нет получателей")
        repeated = await store.recent_text(ev.recipients, text_sha256(content))
        if repeated and not body.allow_repeat:
            raise _conflict(
                f"{repeated} получателей уже получили это же сообщение за последние 24 часа. "
                "Подтвердите повтор"
            )
        try:
            job_id = await store.create_job(
                kind=KIND_MESSAGE,
                request_id=body.request_id,
                params_hash=phash,
                parent_job_id=body.source_job_id,
                created_by=admin.id,
                created_by_label=_actor(request),
                params={
                    "filters": filters,
                    "source_job_id": body.source_job_id,
                    "text": content,
                    "channels": channels,
                    "recipients": len(ev.recipients),
                },
                segment_hash=ev.segment_hash,
                items=message_items(ev),
            )
            await session.commit()
        except ActiveJobExists as exc:
            raise _conflict(f"Уже идёт задача №{exc.job_id} — дождитесь её или остановите")
        except DuplicateRequest:
            again = await store.job_by_request(body.request_id)
            await session.rollback()
            if again and again["params_hash"] == phash:
                return _duplicate_response(again, response, "recipients")
            raise _conflict("Этот запуск уже выполнен с другими параметрами — обновите страницу")
    except HTTPException:
        await session.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        await _guard(session, exc)
        raise

    await _kick(job_id)
    return {
        "job_id": job_id,
        "status": "QUEUED",
        "total": len(ids),
        "recipients": len(ev.recipients),
        "duplicate": False,
    }


class BulkTestBody(BaseModel):
    text: str


@router.post("/bulk/message/test")
@inject
async def test_bulk_message(
    body: BulkTestBody,
    request: Request,
    admin: AdminUser,
    notifier: FromDishka[Notifier],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    _require_writer(request)
    try:
        content = validate_text(body.text)
    except ValueError as exc:
        raise _bad_request(str(exc))
    store = BulkStore(session)

    async def notify_user(user: Any, payload: Any) -> Any:
        return await notifier.notify_user(user, payload=payload)

    try:
        result = await send_test_message(admin, content, notify_user=notify_user, record_feed=store.record_feed)
    except TelegramRejected as exc:
        await session.commit()
        raise _bad_request(f"Telegram не принимает текст: {exc}")
    await session.commit()
    return result


# ── Задачи ────────────────────────────────────────────────────────────────────


@router.get("/bulk/jobs")
@inject
async def list_bulk_jobs(
    admin: AdminUser,
    session: FromDishka[AsyncSession],
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    store = BulkStore(session)
    readonly = is_readonly_admin(admin)
    try:
        jobs = await store.list_jobs(limit)
        breakdown = await store.breakdown([int(j["id"]) for j in jobs])
        active = await store.active_jobs()
        await session.rollback()
    except Exception as exc:  # noqa: BLE001
        await _guard(session, exc)
        raise
    return {
        "items": [job_to_dict(j, breakdown.get(int(j["id"]), {}), readonly=readonly) for j in jobs],
        "active": {KIND_DAYS: active.get(KIND_DAYS), KIND_MESSAGE: active.get(KIND_MESSAGE)},
    }


async def _load_job(store: BulkStore, session: AsyncSession, job_id: int) -> dict[str, Any]:
    try:
        job = await store.get_job(job_id)
    except Exception as exc:  # noqa: BLE001
        await _guard(session, exc)
        raise
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задача не найдена")
    return job


@router.get("/bulk/jobs/{job_id}")
@inject
async def get_bulk_job(job_id: int, admin: AdminUser, session: FromDishka[AsyncSession]) -> dict[str, Any]:
    store = BulkStore(session)
    job = await _load_job(store, session, job_id)
    breakdown = await store.breakdown([job_id])
    await session.rollback()
    return job_to_dict(job, breakdown.get(job_id, {}), readonly=is_readonly_admin(admin))


@router.get("/bulk/jobs/{job_id}/items")
@inject
async def list_bulk_job_items(
    job_id: int,
    admin: AdminUser,
    session: FromDishka[AsyncSession],
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    statuses = [s for s in (status_filter or "").upper().split(",") if s in _JOB_STATUSES] or None
    store = BulkStore(session)
    await _load_job(store, session, job_id)
    total, items = await store.items_page(job_id, statuses, limit, offset)
    await session.rollback()
    readonly = is_readonly_admin(admin)
    return {"total": total, "items": [item_to_dict(i, readonly=readonly) for i in items]}


@router.post("/bulk/jobs/{job_id}/cancel")
@inject
async def cancel_bulk_job(
    job_id: int,
    request: Request,
    _admin: AdminUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    _require_writer(request)
    store = BulkStore(session)
    by = _actor(request)
    # Статус может смениться между чтением и записью (воркер подхватил задачу из
    # очереди) — условная запись по ожидаемому статусу и пара повторов.
    for _attempt in range(3):
        job = await _load_job(store, session, job_id)
        current = job["status"]
        if current in ("COMPLETED", "CANCELED"):
            raise _conflict("Задача уже завершена")
        if current == "CANCELING":
            return {"job_id": job_id, "status": current}
        if current == "PROCESSING":
            # Воркер остановится на границе человека: начатое у человека он досверит.
            if await store.mark_canceling(job_id, current, by):
                await session.commit()
                return {"job_id": job_id, "status": "CANCELING"}
        else:  # QUEUED | PAUSED | ERROR
            # QUEUED бывает и после «Продолжить» у паузы: строки на полпути к панели там
            # уже есть. Отменить такую задачу сразу — бросить их без сверки: крон CANCELED
            # не подбирает, в «Кто не получил» их нет, а «уже получали дни» считает только
            # DONE — повторный запуск выдал бы дни второй раз без предупреждения.
            inflight = await store.inflight(job_id, ("RUNNING", "RETRY", "SENDING"))
            if not inflight:
                if await store.cancel_now(job_id, current, by):
                    await session.commit()
                    return {"job_id": job_id, "status": "CANCELED"}
            else:
                # Есть строки на полпути к панели — их надо досверить, а не бросить.
                try:
                    marked = await store.mark_canceling(job_id, current, by)
                    await session.commit()
                except Exception as exc:  # noqa: BLE001 — ERROR → CANCELING упёрся в активную
                    await session.rollback()
                    if "ux_bulk_jobs_one_active" not in str(exc):
                        raise
                    active = (await store.active_jobs()).get(job["kind"])
                    raise _conflict(f"Сначала завершите задачу №{active}")
                if marked:
                    await _kick(job_id)
                    return {"job_id": job_id, "status": "CANCELING"}
        await session.rollback()
    raise _conflict("Статус задачи изменился — обновите страницу")


@router.post("/bulk/jobs/{job_id}/resume")
@inject
async def resume_bulk_job(
    job_id: int,
    request: Request,
    _admin: AdminUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    _require_writer(request)
    store = BulkStore(session)
    job = await _load_job(store, session, job_id)
    current = job["status"]
    if current in ("QUEUED", "PROCESSING", "CANCELING"):
        raise _conflict("Задача уже идёт")
    if current not in ("PAUSED", "ERROR"):
        raise _conflict("Продолжать нечего")
    left = await store.inflight(job_id, ("PENDING", "RUNNING", "RETRY", "SENDING"))
    if not left:
        await session.rollback()
        raise _conflict("Продолжать нечего")
    try:
        requeued = await store.requeue(job_id, current)
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        if "ux_bulk_jobs_one_active" not in str(exc):
            raise
        active = (await store.active_jobs()).get(job["kind"])
        raise _conflict(f"Сначала завершите задачу №{active}")
    if not requeued:
        raise _conflict("Статус задачи изменился — обновите страницу")
    await _kick(job_id)
    return {"job_id": job_id, "status": "QUEUED"}
