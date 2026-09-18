"""Докупка устройства: жизнь мест — конец срока, сброс лимита, пауза, напоминания.

ЧТО ЗАПИРАЕМ:
  * срок места вышел — лимит вниз ровно на столько, сколько кончилось, но не ниже
    «тариф + оставшиеся»; ручная щедрость админа (лимит выше тарифного) не пропадает;
  * на паузе место НЕ кончается: срок подписки там стоит, и слот обязан стоять с ним;
  * лимит сбросили продлением — возвращаем докупленное (признак сброса);
  * другая строка подписки или другой тариф в строке — место сгорает, лимит не трогаем:
    его уже поставил новый тариф;
  * напоминание за 3 дня шлётся один раз (захват `reminded_at`) и только если подписка
    переживает место;
  * ОТКЛЮЧЕНИЕ УСТРОЙСТВ по умолчанию ВКЛЮЧЕНО (решение владельца «отключить и
    предложить снова») и трогает только подключённые ПОСЛЕ покупки места;
  * крон работает и при выключенных продажах: оплаченное обязано дожить свой срок;
  * пользователя нет в панели — правим только базу и не застреваем в повторах.

Числа и даты синтетические.
"""

import importlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Optional

import pytest

extra = importlib.import_module("src.infrastructure.services.overlay_extra_device")
tick = importlib.import_module("src.infrastructure.taskiq.tasks.extra_devices")

from test_extra_device_buy import (  # noqa: E402
    EXPIRE,
    NOW,
    PANEL_UUID,
    SUB_ID,
    USER_ID,
    FakeSession,
    Log,
)


class TickSession(FakeSession):
    """FakeSession плюс изменения слотов, которые делает сверка."""

    def __init__(self, log: Log, *, reminder_claimed: bool = True, **kw) -> None:
        super().__init__(log, **kw)
        self.reminder_claimed = reminder_claimed

    async def execute(self, stmt: Any, params: Any = None):
        sql = " ".join(str(stmt).split())
        if "SET status = 'burned'" in sql:
            self.log.append(("burn", dict(params or {})))
            return _Row(None)
        if "SET status = 'ended'" in sql:
            self.log.append(("end", dict(params or {})))
            return _Row(None)
        if "SET reminded_at = now()" in sql:
            self.log.append(("claim_reminder", dict(params or {})))
            return _Row((params["id"],) if self.reminder_claimed else None)
        if "SET removal_done = true" in sql:
            self.log.append(("removal_done", dict(params or {})))
            return _Row(None)
        if "fail_count = fail_count + 1" in sql:
            self.log.append(("fail_bump", None))
            return _Row(None)
        return await super().execute(stmt, params)


class _Row:
    def __init__(self, value: Any) -> None:
        self.value = value

    def first(self):
        return self.value

    def scalar(self):
        if self.value is None:
            return None
        return self.value[0] if isinstance(self.value, tuple) else self.value

    def all(self):
        return [self.value] if self.value is not None else []


class FakeUsers:
    def __init__(self, log: Log, *, fail: Optional[str] = None) -> None:
        self.log = log
        self.fail = fail

    async def update_user(self, body: Any) -> Any:
        self.log.append(("panel_patch", body.model_dump(exclude_unset=True, by_alias=True)))
        if self.fail:
            raise RuntimeError(self.fail)
        return SimpleNamespace(hwid_device_limit=body.hwid_device_limit)


class FakeSdk:
    def __init__(self, log: Log, **kw) -> None:
        self.users = FakeUsers(log, **kw)


class FakePanel:
    """Уровень Remnawave: список и удаление устройств."""

    def __init__(self, log: Log, devices: Optional[list] = None) -> None:
        self.log = log
        self.devices = devices or []

    async def get_devices(self, uuid: str) -> list:
        self.log.append(("get_devices", uuid))
        return list(self.devices)

    async def delete_device(self, uuid: str, hwid: str) -> None:
        self.log.append(("delete_device", hwid))


def slot_row(slot_id=1, sub_id=SUB_ID, plan_id=7, starts=None, ends=None, applied=None, reminded=None):
    return (
        slot_id,
        sub_id,
        plan_id,
        starts or NOW - timedelta(days=25),
        ends or NOW + timedelta(days=10),
        applied or NOW - timedelta(days=25),
        reminded,
    )


def cfg(**over):
    return dict(extra.DEFAULT_CONFIG, enabled=True, price_rub_30d=90, **over)


async def reconcile(session, log, *, config=None, panel=None, sdk=None, **kw):
    return await extra.reconcile_user(
        session,
        sdk=sdk or FakeSdk(log),
        remnawave=panel or FakePanel(log),
        user_id=USER_ID,
        config=config or cfg(),
        now=NOW,
        **kw,
    )


# ── 1. конец срока места ─────────────────────────────────────────────────────


async def test_expired_slot_lowers_limit_by_one():
    log = Log()
    session = TickSession(log, device_limit=3, slots=(slot_row(ends=NOW - timedelta(minutes=1)),))
    out = await reconcile(session, log, config=cfg(remove_excess_devices=False))
    assert out["ended"] == [1]
    assert out["limit"] == 2
    assert dict(log[log.index_of("panel_patch")][1])["hwidDeviceLimit"] == 2
    # Тумблер выключен — панель за устройствами не спрашиваем вовсе.
    assert "get_devices" not in log.names()


async def test_admin_generosity_is_kept():
    """Лимит 6 при тарифе 3: конец места снимает одно, а не всё сверх тарифа."""
    log = Log()
    session = TickSession(
        log, device_limit=6, plan_device_limit=3, slots=(slot_row(ends=NOW - timedelta(minutes=1)),)
    )
    out = await reconcile(session, log)
    assert out["limit"] == 5


# ── 2. пауза ─────────────────────────────────────────────────────────────────


async def test_pause_stops_the_clock_for_the_slot():
    log = Log()
    session = TickSession(
        log,
        device_limit=3,
        frozen_at=NOW - timedelta(days=2),
        slots=(slot_row(ends=NOW - timedelta(minutes=1)),),
    )
    out = await reconcile(session, log)
    assert out["ended"] == []
    assert "panel_patch" not in log.names()


async def test_unfreeze_shifts_slot_end_by_pause_length():
    log = Log()
    session = TickSession(log)
    await extra.shift_on_unfreeze(session, USER_ID, NOW - timedelta(days=3))
    shifted = [p for name, p in log if name == "extend_slot"]
    assert shifted, "сдвиг конца места не отправлен"
    assert shifted[0]["frozen"] == NOW - timedelta(days=3)
    # Без момента паузы ничего не двигаем: сдвиг «на неизвестно сколько» хуже, чем ничего.
    log.clear()
    await extra.shift_on_unfreeze(session, USER_ID, None)
    assert log == []


# ── 3. истёкшая и удалённая подписка ─────────────────────────────────────────


async def test_expired_subscription_still_ends_the_slot_without_expire_in_patch():
    log = Log()
    session = TickSession(
        log,
        device_limit=3,
        status="EXPIRED",
        expire_at=NOW - timedelta(days=1),
        slots=(slot_row(ends=NOW - timedelta(days=1)),),
    )
    out = await reconcile(session, log)
    assert out["ended"] == [1]
    patch = dict(log[log.index_of("panel_patch")][1])
    # Срока в теле нет: панель 3.x запрещает expireAt в прошлом.
    assert set(patch) == {"uuid", "hwidDeviceLimit"}


async def test_deleted_subscription_touches_no_panel():
    log = Log()
    session = TickSession(
        log, device_limit=3, status="DELETED", slots=(slot_row(ends=NOW - timedelta(days=1)),)
    )
    out = await reconcile(session, log)
    assert out["ended"] == [1]
    assert "panel_patch" not in log.names()


# ── 4. чужая строка и чужой тариф ────────────────────────────────────────────


async def test_slot_of_another_subscription_row_burns():
    log = Log()
    session = TickSession(log, device_limit=3, slots=(slot_row(sub_id=SUB_ID + 1),))
    out = await reconcile(session, log)
    assert out["burned"] == [(1, "subscription_replaced")]
    # Лимит новой строки поставил новый тариф — не трогаем.
    assert "panel_patch" not in log.names()


async def test_slot_of_another_plan_burns_and_is_reported():
    log = Log()
    session = TickSession(log, device_limit=3, slots=(slot_row(plan_id=99),))
    out = await reconcile(session, log)
    assert out["burned"] == [(1, "plan_replaced")]


# ── 5. сброс лимита продлением ───────────────────────────────────────────────


async def test_renew_reset_returns_the_bought_place():
    log = Log()
    applied = NOW - timedelta(days=5)
    session = TickSession(
        log,
        device_limit=2,
        plan_device_limit=2,
        slots=(slot_row(applied=applied, ends=NOW + timedelta(days=10)),),
    )
    session.__dict__["expire_at"] = EXPIRE
    out = await reconcile(session, log, after_renew=True, mode="reapply_only")
    assert out["limit"] == 3
    assert dict(log[log.index_of("panel_patch")][1])["hwidDeviceLimit"] == 3


async def test_reapply_only_does_not_end_slots_or_remind():
    """В денежном пути лишних вызовов панели не делаем — остальное доделает крон."""
    log = Log()
    session = TickSession(
        log, device_limit=3, slots=(slot_row(ends=NOW - timedelta(days=1)),)
    )
    out = await reconcile(session, log, mode="reapply_only")
    assert out["ended"] == []
    assert out["reminded"] == []


# ── 6. напоминание ───────────────────────────────────────────────────────────


async def test_reminder_is_claimed_once_and_only_if_subscription_outlives_the_slot():
    log = Log()
    session = TickSession(
        log,
        device_limit=3,
        expire_at=NOW + timedelta(days=30),
        slots=(slot_row(ends=NOW + timedelta(days=2)),),
    )
    out = await reconcile(session, log)
    assert [c["slot_id"] for c in out["reminded"]] == [1]
    # Захват не удался (успел другой проход) — второго сообщения нет.
    log2 = Log()
    session2 = TickSession(
        log2,
        device_limit=3,
        expire_at=NOW + timedelta(days=30),
        slots=(slot_row(ends=NOW + timedelta(days=2)),),
        reminder_claimed=False,
    )
    assert (await reconcile(session2, log2))["reminded"] == []
    # Подписка кончается вместе с местом — беспокоить не о чем.
    log3 = Log()
    session3 = TickSession(
        log3,
        device_limit=3,
        expire_at=NOW + timedelta(days=2),
        slots=(slot_row(ends=NOW + timedelta(days=2)),),
    )
    assert (await reconcile(session3, log3))["reminded"] == []


# ── 7. панель не отвечает ────────────────────────────────────────────────────


async def test_panel_failure_rolls_back_and_counts_the_attempt():
    log = Log()
    session = TickSession(log, device_limit=3, slots=(slot_row(ends=NOW - timedelta(days=1)),))
    out = await reconcile(session, log, sdk=FakeSdk(log, fail="панель недоступна"))
    assert "error" in out
    names = log.names()
    # Откат — до счётчика неудач: снижение лимита в базе не должно пережить сбой панели.
    assert names.index("rollback") < names.index("fail_bump")
    # Единственный commit — короткая транзакция самого счётчика.
    assert session.commits == 1


async def test_missing_panel_user_updates_only_the_database():
    log = Log()
    session = TickSession(log, device_limit=3, slots=(slot_row(ends=NOW - timedelta(days=1)),))
    out = await reconcile(
        session,
        log,
        config=cfg(remove_excess_devices=False),
        sdk=FakeSdk(log, fail="NotFoundError: нет такого"),
    )
    assert out.get("panel_missing") is True
    assert out["limit"] == 2
    assert session.commits == 1


# ── 8. выключатель не останавливает крон ─────────────────────────────────────


async def test_disabled_sales_do_not_stop_the_life_of_paid_places():
    log = Log()
    session = TickSession(log, device_limit=3, slots=(slot_row(ends=NOW - timedelta(days=1)),))
    out = await reconcile(session, log, config=dict(extra.DEFAULT_CONFIG))
    assert out["ended"] == [1]
    assert out["limit"] == 2


# ── отключение устройств: только по настройке и только «после покупки» ───────


def device(hwid, created):
    return SimpleNamespace(hwid=hwid, created_at=created, updated_at=created, device_model=hwid, platform="ios")


async def test_removal_is_on_by_default():
    """Решение владельца: без отключения место, оплаченное на неделю, работало бы вечно."""
    assert extra.DEFAULT_CONFIG["remove_excess_devices"] is True
    log = Log()
    session = TickSession(log, device_limit=3, slots=(slot_row(ends=NOW - timedelta(days=1)),))
    await reconcile(session, log, config=dict(extra.DEFAULT_CONFIG, enabled=True))
    # Строка закрывается как «ещё не убрали»: отметим только после фактического снятия.
    assert log[log.index_of("end")][1]["done"] is False
    assert "get_devices" in log.names()


async def test_removal_disabled_marks_the_slot_done_at_once():
    log = Log()
    session = TickSession(log, device_limit=3, slots=(slot_row(ends=NOW - timedelta(days=1)),))
    await reconcile(session, log, config=cfg(remove_excess_devices=False))
    assert log[log.index_of("end")][1]["done"] is True
    assert "delete_device" not in log.names()


async def test_removal_when_enabled_touches_only_devices_added_after_purchase():
    log = Log()
    bought_at = NOW - timedelta(days=25)
    panel = FakePanel(
        log,
        devices=[
            device("старое", bought_at - timedelta(days=10)),
            device("тоже-старое", bought_at - timedelta(days=1)),
            device("новое", bought_at + timedelta(days=3)),
        ],
    )
    session = TickSession(
        log,
        device_limit=3,
        slots=(slot_row(starts=bought_at, ends=NOW - timedelta(days=1)),),
    )
    out = await reconcile(session, log, config=cfg(remove_excess_devices=True), panel=panel)
    assert out["limit"] == 2
    assert [h for name, h in log if name == "delete_device"] == ["новое"]
    ended = log[log.index_of("end")][1]
    assert ended["done"] is False  # закроем после фактического отключения
    assert "removal_done" in log.names()


# ── сам крон ─────────────────────────────────────────────────────────────────


def test_cron_runs_every_quarter_hour():
    schedule = getattr(tick.run_extra_devices_tick, "labels", {}).get("schedule")
    if schedule is None:  # taskiq хранит расписание по-разному в зависимости от версии
        schedule = tick.run_extra_devices_tick.labels["schedule"]
    assert schedule[0]["cron"] == "*/15 * * * *"


def test_unpaid_pending_orders_are_never_closed():
    """Отменённый счёт оживает опоздавшей оплатой — закрытый заказ потерял бы деньги."""
    assert "JOIN transactions t ON t.payment_id = o.payment_id" in tick.OPEN_ORDERS_SQL
    assert "t.status::text = 'COMPLETED'" in tick.OPEN_ORDERS_SQL


# ── проверки после ревью ─────────────────────────────────────────────────────


async def test_user_without_slots_is_not_touched_at_all():
    """Ни одного места — сводить нечего.

    Сюда попадало КАЖДОЕ продление: сверка звалась из денежного пути и на человеке,
    который в жизни ничего не докупал, дёргала лимит и коммитила сессию посреди
    выдачи подписки.
    """
    log = Log()
    session = TickSession(log, device_limit=2, slots=())
    out = await reconcile(session, log, after_renew=True, mode="reapply_only")
    assert out["ended"] == [] and out["burned"] == []
    assert "panel_patch" not in log.names()
    assert session.commits == 0


async def test_pure_reapply_failure_counts_and_alerts():
    """Постоянный сбой панели при восстановлении лимита не должен быть молчаливым.

    Раньше счётчик неудач рос только когда что-то кончалось или сгорало: чистое
    восстановление после продления падало вечно и без единого алерта.
    """
    log = Log()
    applied = NOW - timedelta(days=5)
    session = TickSession(
        log, device_limit=2, plan_device_limit=2, slots=(slot_row(applied=applied),)
    )
    out = await reconcile(
        session, log, after_renew=True, mode="reapply_only", sdk=FakeSdk(log, fail="панель молчит")
    )
    assert "error" in out
    assert "fail_bump" in log.names(), "неудача восстановления не посчитана"


async def test_nothing_changed_keeps_last_applied_at():
    """Отметку «применено» двигаем только при реальном изменении.

    Иначе last_applied_at уезжал вперёд каждые 15 минут и размывал признак сброса:
    сравнение с updated_at строки подписки переставало что-либо значить.
    """
    log = Log()
    session = TickSession(
        log,
        device_limit=3,
        plan_device_limit=2,
        expire_at=NOW + timedelta(days=30),
        slots=(slot_row(ends=NOW + timedelta(days=20)),),
    )
    await reconcile(session, log)
    assert "touch_slots" not in log.names()
    assert "panel_patch" not in log.names()


class Notifier:
    def __init__(self, log: Log) -> None:
        self.log = log

    async def notify_admins(self, payload):
        self.log.append(("notify_admins", payload.i18n_kwargs["content"]))

    async def notify_user(self, user, payload=None, **kw):
        self.log.append(("notify_user", payload.i18n_kwargs["content"]))


class UserDao:
    async def get_by_id(self, uid):
        return SimpleNamespace(id=uid, log=f"[USER:{uid}]", language="ru")


async def test_burned_by_subscription_change_is_reported_to_the_owner():
    """Место, сгоревшее вместе со сменой строки подписки, — это деньги человека.

    Раньше про `subscription_replaced` молчали: строку меняют выдача и промокод мимо
    переноса остатка, и оплаченное место пропадало совсем тихо. Проверяем ОТПРАВКУ,
    а не только текст: молчала именно ветка отчёта, а не словарь сообщений.
    """
    log = Log()
    session = TickSession(log, device_limit=3, slots=(slot_row(sub_id=SUB_ID + 1),))
    out = await reconcile(session, log)
    assert out["burned"] == [(1, "subscription_replaced")]

    report = Log()
    await tick._report_reconcile(
        TickSession(report), UserDao(), Notifier(report), cfg(), USER_ID, out
    )
    alerts = [t for n, t in report if n == "notify_admins"]
    assert len(alerts) == 1, "владельцу не сказали про сгоревшее место"
    assert "сменилась подписка" in alerts[0]


async def test_burned_by_plan_change_keeps_its_own_wording():
    """Смена ТАРИФА в той же строке — другая причина и другой текст."""
    log = Log()
    session = TickSession(log, device_limit=3, slots=(slot_row(plan_id=99),))
    out = await reconcile(session, log)
    report = Log()
    await tick._report_reconcile(
        TickSession(report), UserDao(), Notifier(report), cfg(), USER_ID, out
    )
    alerts = [t for n, t in report if n == "notify_admins"]
    assert len(alerts) == 1 and "замене тарифа" in alerts[0]


async def test_removal_retry_knows_when_the_place_was_bought():
    """Повтор отключения обязан знать момент покупки.

    Без него `pick_excess` не находил НИ ОДНОГО кандидата (все устройства «старые»),
    зато строка помечалась `removal_done = true` — хвост закрывался, ничего не сняв.
    """
    assert "starts_at" in tick.STUCK_REMOVALS_SQL
    source = importlib.import_module("inspect").getsource(tick._retry_removals)
    assert "since=starts_at" in source
    assert "since=None" not in source
