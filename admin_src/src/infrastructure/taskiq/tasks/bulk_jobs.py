"""Фоновые массовые задачи из «Пользователей»: «+N дней» и «Написать» (overlay).

Задачу создаёт админка (web/endpoints/admin/users_bulk.py) вместе со строками людей
и отправляет сюда `run_bulk_job.kiq(id)`. Правила «кому» и «как доставить» — в
services/overlay_bulk.py; здесь — порядок записи и поведение при сбоях.

ВОРКЕР МОЖЕТ УМЕРЕТЬ В ЛЮБОЙ МОМЕНТ. Воркер берёт сообщения с `--ack-type
when_received`: упал посреди задачи — брокер её уже не вернёт. Поэтому:
  * задачу держит аренда (`lease_owner/lease_until`, 180 с), продлеваемая на каждом
    человеке; второй воркер её не возьмёт, пока аренда жива;
  * крон `resume_stalled_bulk_jobs` раз в 5 минут перезапускает задачи с истёкшей
    арендой и застрявшие в очереди;
  * каждый шаг человека — отдельный коммит, и порядок коммитов выбран так, чтобы
    повтор с любого места не выдал дни второй раз и не прислал сообщение дважды.

ДНИ, порядок на человека: захват строки → свежая классификация → GET панели и
сверка → запись target (коммит, ДО панели) → перечитывание срока → PATCH панели и
наша база → DONE (коммит). Строка, застрявшая между target и DONE, при следующем
запуске сверяется с панелью (`resolve_recovery`), а не выполняется заново.

СООБЩЕНИЕ, порядок на человека: захват в SENDING (коммит) → отправка → итог
(коммит). Застрявшее в SENDING не отправляется повторно, а становится UNKNOWN:
лучше недослать, чем прислать человеку одно и то же дважды.

Процессоры — обычные функции с зависимостями в аргументах: тесты подставляют
хранилище, панель и отправщиков в памяти.
"""

import asyncio
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Mapping, Optional
from uuid import UUID

from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Notifier, Remnawave
from src.application.common.dao import SubscriptionDao, UserDao
from src.application.common.email_sender import EmailSender
from src.application.dto import MessagePayloadDto
from src.infrastructure.services.overlay_bulk import (
    DAYS_SKIP,
    EXPIRE_TOL,
    KIND_DAYS,
    KIND_MESSAGE,
    PANEL_FAIL_STREAK,
    PANEL_PAUSE_SEC,
    REASON_RU,
    TG_BAD_STREAK,
    TG_PAUSE_SEC,
    VERIFY_FREEZE,
    VERIFY_NOTE,
    VERIFY_UNAVAILABLE,
    ActiveJobExists,
    BulkStore,
    Category,
    DuplicateRequest,
    as_aware,
    brand_name,
    build_payload,
    classify_days,
    days_paused,
    days_stopped,
    days_summary,
    deliver_message,
    error_text,
    evaluate_message,
    fields_ru,
    identity_conflicts,
    message_items,
    message_params_hash,
    message_stopped,
    message_summary,
    message_title,
    panel_mismatch,
    panel_pause_reason,
    plain_text,
    protected_user,
    resolve_recovery,
    same_moment,
    text_sha256,
)
from src.infrastructure.services.overlay_extend import compute_new_expire, push_subscription_expire
from src.infrastructure.taskiq.broker import broker

# Финальная сверка — только GET, без вебхуков обратно; пауза меньше, чем на записи.
VERIFY_PAUSE_SEC = 0.1
# Пауза снята, пока мы добавляли дни в её остаток: срок в панели должен быть не
# меньше «сейчас + остаток». Допуск — на время между снятием паузы и сверкой.
FREEZE_TOL = timedelta(hours=1)
PROGRESS_EVERY = 10

Clock = Callable[[], datetime]
Sleep = Callable[[float], Awaitable[Any]]
NotifyAdmins = Callable[[str], Awaitable[None]]
Kick = Callable[[int], Awaitable[None]]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _token() -> str:
    return f"{socket.gethostname()[:40]}:{uuid.uuid4().hex[:16]}"


class _LeaseLost(Exception):
    """Аренду перехватил другой воркер (эта копия слишком долго молчала) — выходим."""


async def _safe_rollback(store: Any) -> None:
    try:
        await store.rollback()
    except Exception:  # noqa: BLE001
        pass


def _failed_text(job: Mapping[str, Any], reason: str) -> str:
    if job.get("kind") == KIND_MESSAGE:
        return message_stopped(job, reason)
    return days_stopped(job, reason)


async def _fail(store: Any, job: Mapping[str, Any], token: str, exc: BaseException, notify_admins: NotifyAdmins) -> str:
    """Непойманная ошибка (база, баг): задача ERROR, начатое не теряется.

    Захваченные, но не дошедшие до записи срока строки возвращаются в PENDING —
    «Продолжить» обработает их заново. Строки с записанным target остаются как есть:
    их разберёт сверка при возобновлении.
    """
    logger.exception(f"bulk: задача №{job.get('id')} упала: {exc}")
    await _safe_rollback(store)
    reason = f"Внутренняя ошибка: {error_text(exc)}"[:500]
    try:
        await store.unclaim_unstarted(int(job["id"]))
        await store.recount(int(job["id"]))
        await store.release(int(job["id"]), token, "ERROR", reason=reason, finished=True)
        await store.commit()
    except Exception as inner:  # noqa: BLE001
        await _safe_rollback(store)
        logger.warning(f"bulk: не смог записать ошибку задачи №{job.get('id')}: {inner}")
    await notify_admins(_failed_text(job, reason))
    return "ERROR"


# ── «Добавить N дней» ────────────────────────────────────────────────────────


class _DaysRun:
    def __init__(
        self,
        job: Mapping[str, Any],
        *,
        store: Any,
        user_dao: Any,
        subscription_dao: Any,
        remnawave: Any,
        notify_admins: NotifyAdmins,
        kick: Kick,
        token: str,
        clock: Clock,
        sleep: Sleep,
    ) -> None:
        self.job = job
        self.job_id = int(job["id"])
        params = job.get("params") or {}
        self.days = int(params["days"])
        self.include_trial = bool(params.get("include_trial"))
        self.include_limited = bool(params.get("include_limited"))
        self.store = store
        self.user_dao = user_dao
        self.subscription_dao = subscription_dao
        self.remnawave = remnawave
        self.notify_admins = notify_admins
        self.kick = kick
        self.token = token
        self.clock = clock
        self.sleep = sleep
        self.cancel = False
        self.streak = 0
        self.last_error: Optional[str] = None
        self.touched = 0

    # служебное

    async def _renew(self) -> None:
        status = await self.store.renew_lease(self.job_id, self.token)
        await self.store.commit()
        if status is None:
            raise _LeaseLost()
        if status == "CANCELING":
            self.cancel = True

    async def _progress(self) -> None:
        self.touched += 1
        if self.touched % PROGRESS_EVERY == 0:
            await self.store.recount(self.job_id)
            await self.store.commit()

    def _panel_failed(self, exc: BaseException) -> None:
        self.streak += 1
        self.last_error = error_text(exc)
        logger.warning(f"bulk: задача №{self.job_id}: панель не ответила ({self.streak} подряд): {exc}")

    @property
    def _panel_down(self) -> bool:
        return self.streak >= PANEL_FAIL_STREAK

    async def _finish(self, uid: int, status: str, **fields: Any) -> None:
        await self.store.set_item(self.job_id, uid, status=status, **fields)
        await self.store.commit()

    async def _defer(self, uid: int, final: bool, final_category: Category) -> None:
        """Отложить в конец прохода; на последней попытке — FAILED с причиной."""
        if final:
            await self._finish(
                uid, "FAILED", category=final_category.value, old_expire_at=None, target_expire_at=None
            )
            return
        await self.store.set_item(
            self.job_id, uid, status="PENDING", deferred=True, category=None, old_expire_at=None, target_expire_at=None
        )
        await self.store.commit()

    # проход

    async def run(self, status: str) -> str:
        self.cancel = status == "CANCELING"
        await self._recover_all()
        if self._panel_down:
            return await self._pause()

        if not self.cancel:
            for final in (False, True):
                pending = await self.store.items(self.job_id, ("PENDING",), deferred=final)
                for item in pending:
                    await self._renew()
                    if self.cancel:
                        break
                    await self._one(int(item["user_id"]), final=final)
                    await self._progress()
                    if self._panel_down:
                        return await self._pause()
                if self.cancel:
                    break

        if not self.cancel:
            # Хвост: люди, на которых панель не ответила в этом проходе, — ещё одна попытка.
            for item in await self.store.items(self.job_id, ("RETRY", "PENDING")):
                await self._renew()
                if self.cancel:
                    break
                if item["status"] == "RETRY":
                    await self._recover(item)
                else:
                    await self._one(int(item["user_id"]), final=True)
                await self._progress()
                if self._panel_down:
                    return await self._pause()

        if self.cancel:
            # Остановка: новых записей в панель нет, но начатое досверяется — иначе
            # строка с уже дошедшим PATCH так и висела бы «в процессе».
            await self._recover_all()
            await self.store.skip_pending(self.job_id, Category.CANCELED.value)
            await self.store.commit()

        leftovers = await self.store.items(self.job_id, ("PENDING", "RUNNING", "RETRY"))
        if leftovers:
            return await self._pause()

        await self._verify()
        return await self._complete()

    async def _one(self, uid: int, *, final: bool) -> None:
        if not await self.store.claim(self.job_id, uid, "RUNNING"):
            await self.store.commit()
            return
        await self.store.commit()

        for _attempt in range(2):
            row = (await self.store.classify_rows([uid])).get(uid)
            now = self.clock()
            category = (
                Category.NO_SUBSCRIPTION
                if row is None
                else classify_days(
                    row, now=now, include_trial=self.include_trial, include_limited=self.include_limited
                )
            )
            if category in DAYS_SKIP:
                await self._finish(uid, "SKIPPED", category=category.value)
                return
            if category == Category.RECENT_CHANGE:
                await self._defer(uid, final, Category.RECENT_CHANGE)
                return
            if category == Category.APPLY_FROZEN:
                # Пауза: дни — в сохранённый остаток, панель не трогаем вовсе
                # (`update_user` снял бы паузу). Строка DONE — в той же транзакции.
                seconds = self.days * 86400
                if await self.store.add_frozen_seconds(uid, seconds):
                    await self._finish(
                        uid,
                        "DONE",
                        category=category.value,
                        added_seconds=seconds,
                        subscription_id=row.get("sub_id"),
                        error=None,
                    )
                    return
                # Паузу сняли между выборкой и записью — классифицируем заново.
                continue
            await self._apply(uid, row, final=final, now=now)
            return
        await self._finish(uid, "FAILED", category=Category.MANUAL.value)

    async def _apply(self, uid: int, row: Mapping[str, Any], *, final: bool, now: datetime) -> None:
        user = await self.user_dao.get_by_id(uid)
        sub = await self.subscription_dao.get_current(uid)
        if user is None or sub is None:
            await self._finish(uid, "SKIPPED", category=Category.NO_SUBSCRIPTION.value)
            return
        if int(sub.id) != int(row["sub_id"]):
            await self._defer(uid, final, Category.RECENT_CHANGE)
            return

        try:
            panel = await self.remnawave.get_user_by_uuid(sub.user_remna_id)
        except Exception as exc:  # noqa: BLE001 — до записи ничего не тронуто
            self._panel_failed(exc)
            await self._finish(uid, "PENDING", error=error_text(exc))
            return
        self.streak = 0
        if panel is None:
            await self._finish(uid, "FAILED", category=Category.NOT_IN_PANEL.value)
            return
        fields = panel_mismatch(sub, panel)
        if fields:
            await self._finish(uid, "FAILED", category=Category.MISMATCH.value, error=fields_ru(fields))
            return
        as_panel_sees = protected_user(user, panel)
        if as_panel_sees is None:
            await self._finish(
                uid,
                "FAILED",
                category=Category.MISMATCH.value,
                error=fields_ru(identity_conflicts(user, panel)),
            )
            return

        old = sub.expire_at
        target = compute_new_expire(as_aware(old), self.days, now).replace(microsecond=0)
        # Write-ahead: target в журнале ДО панели. Упадём после PATCH — повтор увидит
        # в панели именно target и не прибавит дни второй раз.
        await self.store.set_item(
            self.job_id,
            uid,
            category=Category.APPLY.value,
            subscription_id=int(sub.id),
            old_expire_at=old,
            target_expire_at=target,
            error=None,
        )
        await self.store.commit()

        # Перечитываем срок прямо перед PATCH: оплата, дошедшая до нашей базы за эти
        # доли секунды, иначе затёрлась бы нашим сроком, посчитанным от старого.
        state = await self.store.sub_state(int(sub.id))
        if (
            state is None
            or not same_moment(state.get("expire_at"), old, timedelta(seconds=1))
            or str(state.get("status")) != str(row.get("sub_status"))
        ):
            await self._defer(uid, final, Category.MANUAL)
            return

        try:
            updated = await push_subscription_expire(
                user=as_panel_sees,
                sub=sub,
                target=target,
                remnawave=self.remnawave,
                subscription_dao=self.subscription_dao,
            )
            if updated is None:
                raise RuntimeError("подписка не обновилась в нашей базе")
        except Exception as exc:  # noqa: BLE001 — сверка при повторе решит по панели
            await _safe_rollback(self.store)
            self._panel_failed(exc)
            await self._finish(uid, "RETRY", error=error_text(exc))
            return
        self.streak = 0
        await self._finish(uid, "DONE", error=None)
        await self.sleep(PANEL_PAUSE_SEC)

    async def _recover_all(self) -> None:
        for item in await self.store.items(self.job_id, ("RUNNING", "RETRY")):
            await self._renew()
            await self._recover(item)
            if self._panel_down:
                return

    async def _recover(self, item: Mapping[str, Any]) -> None:
        uid = int(item["user_id"])
        old = item.get("old_expire_at")
        target = item.get("target_expire_at")
        if target is None:
            # До записи в панель дело не дошло — ни панель, ни пауза не тронуты.
            if self.cancel:
                await self._finish(uid, "SKIPPED", category=Category.CANCELED.value)
            else:
                await self._finish(uid, "PENDING")
            return

        sub = await self.subscription_dao.get_current(uid)
        if sub is None or (item.get("subscription_id") and int(sub.id) != int(item["subscription_id"])):
            await self._finish(uid, "FAILED", category=Category.MANUAL.value)
            return
        try:
            panel = await self.remnawave.get_user_by_uuid(sub.user_remna_id)
        except Exception as exc:  # noqa: BLE001
            self._panel_failed(exc)
            await self._finish(uid, "RETRY", error=error_text(exc))
            return
        self.streak = 0
        if panel is None:
            await self._finish(uid, "FAILED", category=Category.NOT_IN_PANEL.value)
            return

        decision = resolve_recovery(as_aware(old), as_aware(target), as_aware(panel.expire_at), self.clock())
        if decision == "DONE":
            note = None
            if not same_moment(sub.expire_at, target):
                if same_moment(sub.expire_at, old):
                    # PATCH дошёл, а наша база — нет: догоняем её до панели.
                    sub.expire_at = target
                    await self.subscription_dao.update(sub)
                else:
                    note = VERIFY_NOTE
            await self._finish(uid, "DONE", error=None, verify_note=note)
            return
        if decision == "RETRY":
            if self.cancel:
                await self._finish(uid, "SKIPPED", category=Category.CANCELED.value)
                return
            user = await self.user_dao.get_by_id(uid)
            fields = panel_mismatch(sub, panel, check_expire=False)
            if not same_moment(sub.expire_at, old):
                fields.append("expire_at")
            if fields:
                await self._finish(uid, "FAILED", category=Category.MISMATCH.value, error=fields_ru(fields))
                return
            as_panel_sees = protected_user(user, panel) if user is not None else None
            if as_panel_sees is None:
                await self._finish(
                    uid,
                    "FAILED",
                    category=Category.MISMATCH.value,
                    error=fields_ru(identity_conflicts(user, panel)) if user is not None else None,
                )
                return
            try:
                # Тот же target, что записан до сбоя, — не пересчитанный от нового now.
                updated = await push_subscription_expire(
                    user=as_panel_sees,
                    sub=sub,
                    target=as_aware(target),
                    remnawave=self.remnawave,
                    subscription_dao=self.subscription_dao,
                )
                if updated is None:
                    raise RuntimeError("подписка не обновилась в нашей базе")
            except Exception as exc:  # noqa: BLE001
                await _safe_rollback(self.store)
                self._panel_failed(exc)
                await self._finish(uid, "RETRY", error=error_text(exc))
                return
            self.streak = 0
            await self._finish(uid, "DONE", error=None)
            await self.sleep(PANEL_PAUSE_SEC)
            return
        await self._finish(uid, "FAILED", category=Category.MANUAL.value)

    async def _verify(self) -> None:
        """Финальная сверка: окно в сотни миллисекунд между перечитыванием и PATCH не
        закрыть, но его последствия видно — такие люди попадут в «проверить вручную»."""
        for item in await self.store.items(self.job_id, ("DONE",)):
            if item.get("verify_note"):
                continue
            await self._renew()
            uid = int(item["user_id"])
            try:
                if item.get("added_seconds") is not None:
                    note = await self._verify_frozen(uid)
                else:
                    note = await self._verify_expire(uid, item.get("target_expire_at"))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"bulk: задача №{self.job_id}: сверка user_id={uid} не удалась: {exc}")
                note = VERIFY_UNAVAILABLE
            if note:
                await self._finish(uid, "DONE", verify_note=note)
            await self.sleep(VERIFY_PAUSE_SEC)

    async def _verify_expire(self, uid: int, target: Optional[datetime]) -> Optional[str]:
        sub = await self.subscription_dao.get_current(uid)
        if sub is None or target is None:
            return VERIFY_NOTE
        panel = await self.remnawave.get_user_by_uuid(sub.user_remna_id)
        if panel is None:
            return VERIFY_NOTE
        panel_expire = as_aware(panel.expire_at)
        if panel_expire < as_aware(target) - EXPIRE_TOL or not same_moment(sub.expire_at, panel_expire):
            return VERIFY_NOTE
        return None

    async def _verify_frozen(self, uid: int) -> Optional[str]:
        state = await self.store.freeze_state(uid)
        if state is None:
            return VERIFY_FREEZE
        if state.get("active"):
            return None
        panel = await self.remnawave.get_user_by_uuid(UUID(str(state["remna_uuid"])))
        due = self.clock() + timedelta(seconds=int(state.get("remaining_seconds") or 0)) - FREEZE_TOL
        if panel is None or as_aware(panel.expire_at) < due:
            return VERIFY_FREEZE
        return None

    async def _pause(self) -> str:
        reason = panel_pause_reason(self.last_error)
        # Остановку не превращаем в паузу: крон подберёт CANCELING и досверит начатое.
        status = "CANCELING" if self.cancel else "PAUSED"
        await self.store.unclaim_unstarted(self.job_id)
        await self.store.recount(self.job_id)
        await self.store.release(self.job_id, self.token, status, reason=reason, finished=False)
        await self.store.commit()
        if not self.cancel:
            # Недосверенную остановку крон перезапускает раз в 5 минут — сообщать о
            # каждой такой попытке значило бы засыпать админов одним и тем же.
            await self.notify_admins(days_paused(self.job, reason))
        return status

    async def _complete(self) -> str:
        final = "CANCELED" if self.cancel else "COMPLETED"
        await self.store.recount(self.job_id)
        if not await self.store.release(self.job_id, self.token, final, reason=None, finished=True):
            await _safe_rollback(self.store)
            raise _LeaseLost()
        await self.store.commit()
        if final == "COMPLETED":
            await self._spawn_message()
            totals = await self.store.totals(self.job_id)
            await self.notify_admins(days_summary(self.job, totals))
        return final

    async def _spawn_message(self) -> None:
        """«Сообщить им об этом»: дочерняя задача-сообщение ровно тем, кто получил дни."""
        notify = (self.job.get("params") or {}).get("notify")
        if not notify or not notify.get("text"):
            return
        done = await self.store.done_user_ids(self.job_id)
        if not done:
            return
        channels = list(notify.get("channels") or [])
        content = str(notify["text"])
        ev = await evaluate_message(self.store, done, channels=channels, email_enabled=True)
        if not ev.recipients:
            return
        # request_id выводится из задачи дней: перезапуск после падения на этом шаге
        # не создаст второе сообщение тем же людям.
        request_id = uuid.uuid5(uuid.NAMESPACE_URL, f"remnashop:bulk-days-notify:{self.job_id}")
        try:
            child_id = await self.store.create_job(
                kind=KIND_MESSAGE,
                request_id=request_id,
                params_hash=message_params_hash(None, self.job_id, content, channels),
                parent_job_id=self.job_id,
                created_by=self.job.get("created_by"),
                created_by_label=str(self.job.get("created_by_label") or ""),
                params={
                    "text": content,
                    "channels": channels,
                    "source_job_id": self.job_id,
                    "recipients": len(ev.recipients),
                },
                segment_hash=ev.segment_hash,
                items=message_items(ev),
            )
            await self.store.commit()
        except ActiveJobExists as exc:
            await self.store.set_reason(
                self.job_id,
                f"Сообщение не отправлено: идёт другая задача №{exc.job_id} — "
                "отправьте кнопкой «Написать получившим»",
            )
            await self.store.commit()
            return
        except DuplicateRequest:
            return
        await self.kick(child_id)


async def process_days_job(
    job_id: int,
    *,
    store: Any,
    user_dao: Any,
    subscription_dao: Any,
    remnawave: Any,
    notify_admins: NotifyAdmins,
    kick: Kick,
    token: str,
    clock: Clock = _utcnow,
    sleep: Sleep = asyncio.sleep,
) -> Optional[str]:
    """Прогон задачи «+N дней». Итоговый статус; None — задачу держит другой воркер."""
    status = await store.acquire_lease(job_id, token)
    await store.commit()
    if status is None:
        return None
    job = await store.get_job(job_id)
    if job is None:
        return None
    run = _DaysRun(
        job,
        store=store,
        user_dao=user_dao,
        subscription_dao=subscription_dao,
        remnawave=remnawave,
        notify_admins=notify_admins,
        kick=kick,
        token=token,
        clock=clock,
        sleep=sleep,
    )
    try:
        return await run.run(status)
    except _LeaseLost:
        await _safe_rollback(store)
        logger.warning(f"bulk: задача №{job_id}: аренду перехватил другой воркер — выхожу")
        return None
    except Exception as exc:  # noqa: BLE001 — наружу не выпускаем: задача ERROR
        return await _fail(store, job, token, exc, notify_admins)


# ── «Написать отфильтрованным» ───────────────────────────────────────────────


class _MessageRun:
    def __init__(
        self,
        job: Mapping[str, Any],
        *,
        store: Any,
        user_dao: Any,
        notifier: Any,
        email_sender: Any,
        notify_admins: NotifyAdmins,
        token: str,
        sleep: Sleep,
    ) -> None:
        self.job = job
        self.job_id = int(job["id"])
        self.store = store
        self.user_dao = user_dao
        self.notifier = notifier
        self.email_sender = email_sender
        self.notify_admins = notify_admins
        self.token = token
        self.sleep = sleep
        self.cancel = False

    async def _renew(self) -> None:
        status = await self.store.renew_lease(self.job_id, self.token)
        await self.store.commit()
        if status is None:
            raise _LeaseLost()
        if status == "CANCELING":
            self.cancel = True

    async def _retry_after(self, seconds: float) -> None:
        # Флуд-лимит Telegram может держать минуты: аренду продлеваем ДО сна, иначе
        # крон решил бы, что воркер умер, и запустил бы вторую копию рассылки.
        await self._renew()
        await self.sleep(seconds)

    async def _send_tg(self, uid: int, payload: MessagePayloadDto) -> Any:
        user = await self.user_dao.get_by_id(uid)
        if user is None:
            return None
        return await self.notifier.notify_user(user, payload=payload)

    async def run(self, status: str) -> str:
        params = self.job.get("params") or {}
        content = str(params.get("text") or "")
        channels = list(params.get("channels") or [])
        payload = build_payload(content)
        sha = text_sha256(content)
        body = plain_text(content)
        brand = brand_name()
        try:
            # Действующий выключатель — свойство отправителя (assets/email.json поверх
            # .env), а не переменная окружения: выключенная в админке почта не шлёт.
            email_enabled = bool(self.email_sender.is_enabled)
        except Exception:  # noqa: BLE001
            email_enabled = False
        self.cancel = status == "CANCELING"

        # Сбой посреди отправки: дошло ли — неизвестно. Повторять нельзя.
        for item in await self.store.items(self.job_id, ("SENDING", "RUNNING")):
            await self.store.set_item(
                self.job_id,
                int(item["user_id"]),
                status="UNKNOWN",
                category=Category.UNKNOWN.value,
                error=REASON_RU[Category.UNKNOWN],
            )
        await self.store.commit()

        bad_streak = 0
        last_bad: Optional[str] = None
        if not self.cancel:
            for n, item in enumerate(await self.store.items(self.job_id, ("PENDING",))):
                uid = int(item["user_id"])
                await self._renew()
                if self.cancel:
                    break
                if n and n % 20 == 0:
                    await self.store.recount(self.job_id)
                    await self.store.commit()
                if not await self.store.claim(self.job_id, uid, "SENDING"):
                    await self.store.commit()
                    continue
                await self.store.commit()

                row = (await self.store.classify_rows([uid])).get(uid)
                if row is None:
                    await self._finish(uid, "SKIPPED", category=Category.NO_CHANNEL.value)
                    continue
                if str(row.get("role") or "") != "USER":
                    await self._finish(uid, "SKIPPED", category=Category.STAFF.value)
                    continue
                if row.get("is_blocked"):
                    await self._finish(uid, "SKIPPED", category=Category.BLOCKED.value)
                    continue

                title = message_title(row.get("language"), brand)
                feed = {"title": title, "body": body, "url": "/"}

                async def send_tg(_row: Mapping[str, Any], uid: int = uid) -> Any:
                    return await self._send_tg(uid, payload)

                async def record_feed(_row: Mapping[str, Any], uid: int = uid, feed: dict = feed) -> bool:
                    return await self.store.record_feed(uid, feed)

                async def send_push(_row: Mapping[str, Any], uid: int = uid, feed: dict = feed) -> int:
                    return await self.store.push(uid, {**feed, "tag": "bulk-message"})

                async def send_email(r: Mapping[str, Any], title: str = title) -> Any:
                    return await self.email_sender.send(to=str(r["email"]), subject=title, body=body)

                delivery = await deliver_message(
                    row,
                    channels=channels,
                    send_tg=send_tg,
                    record_feed=record_feed,
                    send_push=send_push,
                    send_email=send_email,
                    email_enabled=email_enabled,
                    on_retry_after=self._retry_after,
                )
                await self._finish(
                    uid,
                    delivery.status,
                    category=delivery.category,
                    channels=",".join(delivery.channels) or None,
                    tg_message_id=delivery.tg_message_id,
                    text_sha256=sha,
                    error=delivery.error,
                )
                if delivery.bad_request:
                    bad_streak += 1
                    last_bad = delivery.bad_request
                elif "telegram" in delivery.channels:
                    bad_streak = 0
                if bad_streak >= TG_BAD_STREAK:
                    return await self._stop_bad_text(last_bad)
                await self.sleep(TG_PAUSE_SEC)

        if self.cancel:
            await self.store.skip_pending(self.job_id, Category.CANCELED.value)
            return await self._release("CANCELED")
        final = await self._release("COMPLETED")
        await self.notify_admins(message_summary(self.job, await self.store.totals(self.job_id)))
        return final

    async def _finish(self, uid: int, status: str, **fields: Any) -> None:
        await self.store.set_item(self.job_id, uid, status=status, **fields)
        await self.store.commit()

    async def _release(self, status: str, reason: Optional[str] = None) -> str:
        await self.store.recount(self.job_id)
        if not await self.store.release(self.job_id, self.token, status, reason=reason, finished=True):
            await _safe_rollback(self.store)
            raise _LeaseLost()
        await self.store.commit()
        return status

    async def _stop_bad_text(self, message: Optional[str]) -> str:
        # Три отказа Telegram подряд — это не люди, а текст (битая разметка):
        # остальным он тоже не уйдёт, и слать дальше значит собирать ошибки.
        reason = f"Telegram не принимает текст: {message or 'ошибка разметки'}"
        await self.store.skip_pending(self.job_id, Category.CANCELED.value)
        status = await self._release("ERROR", reason)
        await self.notify_admins(message_stopped(self.job, reason))
        return status


async def process_message_job(
    job_id: int,
    *,
    store: Any,
    user_dao: Any,
    notifier: Any,
    email_sender: Any,
    notify_admins: NotifyAdmins,
    token: str,
    sleep: Sleep = asyncio.sleep,
) -> Optional[str]:
    status = await store.acquire_lease(job_id, token)
    await store.commit()
    if status is None:
        return None
    job = await store.get_job(job_id)
    if job is None:
        return None
    run = _MessageRun(
        job,
        store=store,
        user_dao=user_dao,
        notifier=notifier,
        email_sender=email_sender,
        notify_admins=notify_admins,
        token=token,
        sleep=sleep,
    )
    try:
        return await run.run(status)
    except _LeaseLost:
        await _safe_rollback(store)
        logger.warning(f"bulk: задача №{job_id}: аренду перехватил другой воркер — выхожу")
        return None
    except Exception as exc:  # noqa: BLE001
        return await _fail(store, job, token, exc, notify_admins)


# ── Задачи taskiq ─────────────────────────────────────────────────────────────


@broker.task(retry_on_error=False)
@inject(patch_module=True)
async def run_bulk_job(
    job_id: int,
    session: FromDishka[AsyncSession],
    user_dao: FromDishka[UserDao],
    subscription_dao: FromDishka[SubscriptionDao],
    remnawave: FromDishka[Remnawave],
    notifier: FromDishka[Notifier],
    email_sender: FromDishka[EmailSender],
) -> None:
    store = BulkStore(session)
    token = _token()

    async def notify_admins(content: str) -> None:
        # Только штатный notify_admins: overlay-уведомления сами зеркалят админское
        # сообщение в ленту админки и push, отдельная запись дала бы дубль в ленте.
        try:
            await notifier.notify_admins(
                MessagePayloadDto(
                    i18n_key="raw-message",
                    i18n_kwargs={"content": content},
                    delete_after=None,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"bulk: не смог уведомить админов о задаче №{job_id}: {exc}")

    async def kick(child_id: int) -> None:
        try:
            await run_bulk_job.kiq(child_id)  # type: ignore[call-overload]
        except Exception as exc:  # noqa: BLE001 — крон подберёт задачу из очереди
            logger.warning(f"bulk: не смог поставить задачу №{child_id} в очередь: {exc}")

    try:
        job = await store.get_job(job_id)
        await store.rollback()
        if job is None:
            return
        if job["kind"] == KIND_DAYS:
            result = await process_days_job(
                job_id,
                store=store,
                user_dao=user_dao,
                subscription_dao=subscription_dao,
                remnawave=remnawave,
                notify_admins=notify_admins,
                kick=kick,
                token=token,
            )
        elif job["kind"] == KIND_MESSAGE:
            result = await process_message_job(
                job_id,
                store=store,
                user_dao=user_dao,
                notifier=notifier,
                email_sender=email_sender,
                notify_admins=notify_admins,
                token=token,
            )
        else:
            logger.warning(f"bulk: задача №{job_id}: неизвестный вид {job['kind']!r}")
            return
    except Exception as exc:  # noqa: BLE001 — например, воркер стартовал раньше миграции
        await _safe_rollback(store)
        logger.warning(f"bulk: задача №{job_id} не запустилась: {exc}")
        return
    if result:
        logger.info(f"bulk: задача №{job_id} ({job['kind']}) → {result}")


@broker.task(schedule=[{"cron": "*/5 * * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def resume_stalled_bulk_jobs(session: FromDishka[AsyncSession]) -> None:
    store = BulkStore(session)
    try:
        stalled = await store.stalled_jobs()
        await store.rollback()
    except Exception as exc:  # noqa: BLE001 — таблицы ещё нет: миграция не прошла
        await _safe_rollback(store)
        logger.debug(f"bulk: подбор зависших задач пропущен: {exc}")
        return
    for job_id in stalled:
        try:
            await run_bulk_job.kiq(job_id)  # type: ignore[call-overload]
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"bulk: не смог перезапустить задачу №{job_id}: {exc}")
    if stalled:
        logger.info(f"bulk: перезапущены зависшие задачи {stalled}")
