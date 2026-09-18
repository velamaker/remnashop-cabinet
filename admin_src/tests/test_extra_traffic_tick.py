"""Докупка трафика: жизнь прибавки — крон и сведение лимита.

ЧТО ЗАПИРАЕМ:
  * подошёл момент обнуления — лимит вниз ровно на докупленный объём;
  * лимит уже стал тарифным (продление, «Выдать», промокод) — только пометка,
    БЕЗ вызова панели;
  * сменилась строка подписки или тариф — прибавка `burned`, лимит не трогаем;
  * РЕЗЕРВ И ПАУЗА закрывают записи и НЕ зовут панель. Это не мелочь: резерв ставит
    в панели лимит 1 ГБ, и общая формула вернула бы истёкшему человеку полный объём
    (мутация «убрать проверку резерва» роняет test_reserve_never_calls_the_panel);
  * окно пересчитывается КАЖДЫЙ проход из текущей стратегии строки — админ мог её
    сменить после покупки;
  * неоплаченный `pending` крон НЕ закрывает никогда.

Числа, даты и идентификаторы синтетические.
"""

import importlib
import inspect
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

extra = importlib.import_module("src.infrastructure.services.overlay_extra_traffic")
tick = importlib.import_module("src.infrastructure.taskiq.tasks.extra_traffic")

from src.core.utils.converters import gb_to_bytes  # noqa: E402

from test_extra_traffic_buy import (  # noqa: E402
    EXPIRE,
    NOW,
    PANEL_CREATED,
    PLAN_GB,
    SUB_ID,
    WINDOW,
    FakeRemnawave,
    FakeSession,
    Log,
    config_on,  # noqa: F401 — фикстура включает продажи
    grant_row,
)

USER_ID = 7
CFG = {
    "enabled": True,
    "gb_per_purchase": 50,
    "price_rub": 40,
    "min_amount_rub": 10,
    "show_from_percent": 70,
    "min_hours_left": 2,
    "max_gb_per_window": 1000,
}
# Прибавка куплена ДО обнуления 7 октября; «сейчас» в тестах ниже двигаем сами.
GRANTED = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
AFTER_RESET = datetime(2026, 10, 7, 0, 30, tzinfo=timezone.utc)


class TickSession(FakeSession):
    """Подделка с журналом пометок прибавок: крон только ими и живёт."""

    async def execute(self, stmt: Any, params: Any = None):
        sql = " ".join(str(stmt).split())
        if "UPDATE extra_traffic_grants SET status = :st" in sql:
            self.log.append(("mark", {"st": params["st"], "r": params["r"], "ids": list(params["ids"])}))
            return await super().execute("noop", None)
        if "fail_count = fail_count + 1" in sql:
            self.log.append(("bump_fail", None))
            return await super().execute("noop", None)
        return await super().execute(stmt, params)


def a_grant(gb=50, grant_id=1, sub_id=SUB_ID, plan_id=7, strategy="MONTH_ROLLING", created=PANEL_CREATED):
    return (
        grant_id,
        sub_id,
        plan_id,
        gb,
        strategy,
        created,
        GRANTED,
        WINDOW,
        GRANTED,
    )


async def reconcile(session, remnawave, now, *, panel_gb=None):
    """Свести прибавки. `remnawave` — и SDK для PATCH, и источник чтения лимита.

    Крон считает новый лимит в БАЙТАХ от значения панели (иначе теряется дробная
    часть, 300,4 → 300). По умолчанию панель «согласна» с нашей строкой — это и есть
    обычное состояние; расхождение задаётся `panel_gb` там, где оно и проверяется.
    """
    remnawave.limit_bytes = gb_to_bytes(panel_gb if panel_gb is not None else session.traffic_limit)
    return await extra.reconcile_user(
        session, sdk=remnawave.sdk, user_id=USER_ID, config=CFG, now=now, remnawave=remnawave
    )


async def test_window_passed_lowers_the_limit_by_the_bought_volume():
    log = Log()
    session = TickSession(log, traffic_limit=PLAN_GB + 50, grants=(a_grant(),))
    remnawave = FakeRemnawave(log)

    out = await reconcile(session, remnawave, AFTER_RESET)

    assert out["ended"] == [1] and out["reason"] == "reset"
    assert out["limit"] == PLAN_GB
    assert log[log.index_of("panel_patch")][1]["trafficLimitBytes"] == gb_to_bytes(PLAN_GB)
    assert session.commits == 1


async def test_window_not_reached_yet_changes_nothing():
    log = Log()
    session = TickSession(log, traffic_limit=PLAN_GB + 50, grants=(a_grant(),))
    remnawave = FakeRemnawave(log)

    out = await reconcile(session, remnawave, GRANTED + timedelta(days=1))

    assert out["ended"] == [] and out["burned"] == []
    assert "panel_patch" not in log.names()


async def test_two_grants_one_expired_keeps_the_other():
    """Пол «тариф + ещё действующие» не даёт снять чужой объём."""
    log = Log()
    # Обе куплены в одном окне и кончаются вместе — значит снимаются вместе.
    session = TickSession(
        log,
        traffic_limit=PLAN_GB + 100,
        grants=(a_grant(grant_id=1), a_grant(grant_id=2)),
    )
    remnawave = FakeRemnawave(log)

    out = await reconcile(session, remnawave, AFTER_RESET)

    assert sorted(out["ended"]) == [1, 2]
    assert out["ended_gb"] == 100
    assert log[log.index_of("panel_patch")][1]["trafficLimitBytes"] == gb_to_bytes(PLAN_GB)


async def test_manual_generosity_is_not_taken_away():
    """Админ поднял лимит до 500 руками — кончившаяся прибавка снимает только 50."""
    log = Log()
    session = TickSession(log, traffic_limit=500, grants=(a_grant(),))
    remnawave = FakeRemnawave(log)

    out = await reconcile(session, remnawave, AFTER_RESET)

    assert out["limit"] == 450
    assert log[log.index_of("panel_patch")][1]["trafficLimitBytes"] == gb_to_bytes(450)


async def test_tariff_limit_already_restored_means_a_mark_without_the_panel():
    """Продление поставило тарифный лимит и обнулило расход — панель звать незачем."""
    log = Log()
    session = TickSession(log, traffic_limit=PLAN_GB, grants=(a_grant(),))
    remnawave = FakeRemnawave(log)

    out = await reconcile(session, remnawave, GRANTED + timedelta(days=1))

    assert out["ended"] == [1] and out["reason"] == "renew"
    assert "panel_patch" not in log.names()
    assert log[log.index_of("mark")][1]["r"] == "renew"


async def test_changed_subscription_burns_the_grant_without_touching_the_panel():
    log = Log()
    session = TickSession(log, traffic_limit=PLAN_GB + 50, grants=(a_grant(sub_id=SUB_ID + 1),))
    remnawave = FakeRemnawave(log)

    out = await reconcile(session, remnawave, GRANTED + timedelta(days=1))

    assert out["burned"] == [(1, "subscription_replaced")]
    assert "panel_patch" not in log.names()


async def test_changed_plan_burns_the_grant():
    log = Log()
    session = TickSession(log, traffic_limit=PLAN_GB + 50, grants=(a_grant(plan_id=99),))
    remnawave = FakeRemnawave(log)

    out = await reconcile(session, remnawave, GRANTED + timedelta(days=1))

    assert out["burned"] == [(1, "plan_replaced")]
    assert "panel_patch" not in log.names()


async def test_reserve_never_calls_the_panel():
    """ОБЯЗАТЕЛЬНЫЙ СЛУЧАЙ. Резерв ставит в панели 1 ГБ.

    Без этой ветки формула «не ниже тарифа + действующие» вернула бы 350 ГБ — то есть
    крон подарил бы полноценный трафик тому, кто уже не платит. Мутация «убрать
    проверку резерва» роняет этот тест.
    """
    log = Log()
    session = TickSession(
        log,
        traffic_limit=1,
        grants=(a_grant(),),
        reserve_expire_at=EXPIRE - timedelta(minutes=30),
    )
    remnawave = FakeRemnawave(log)

    out = await reconcile(session, remnawave, GRANTED + timedelta(days=1))

    assert out["ended"] == [1] and out["reason"] == "reserve"
    assert out["silent"] is True
    assert "panel_patch" not in log.names(), "лимит резерва (1 ГБ) трогать нельзя"


async def test_pause_does_not_give_away_the_volume():
    """ПАУЗА — НЕ РЕЗЕРВ, и это ровно та разница, на которой раньше терялись деньги.

    Прежняя ветка гасила записи и НЕ трогала панель. Значит: поставил паузу на
    двадцать минут, снял — и докупленные ГБ остались в лимите панели навсегда,
    повторяясь каждый месяц бесплатно. Пауза обязана идти обычной веткой: срок
    прибавки кончается сам, а лимит опускается тем же узким PATCH.
    """
    log = Log()
    session = TickSession(
        log,
        traffic_limit=PLAN_GB + 50,
        grants=(a_grant(),),
        frozen_at=GRANTED - timedelta(days=1),
    )
    remnawave = FakeRemnawave(log)

    # Срок прибавки уже вышел — на паузе это ничего не меняет.
    out = await reconcile(session, remnawave, AFTER_RESET)

    assert out["ended"] == [1]
    assert out.get("reason") != "frozen", "пауза не должна быть отдельной причиной"
    assert log[log.index_of("panel_patch")][1]["trafficLimitBytes"] == gb_to_bytes(PLAN_GB)


async def test_pause_before_the_window_ends_changes_nothing():
    """Пока срок прибавки не вышел, пауза её не гасит — человек за неё заплатил."""
    log = Log()
    session = TickSession(
        log,
        traffic_limit=PLAN_GB + 50,
        grants=(a_grant(),),
        frozen_at=GRANTED - timedelta(days=1),
    )
    remnawave = FakeRemnawave(log)

    out = await reconcile(session, remnawave, GRANTED + timedelta(days=1))

    assert out["ended"] == []
    assert "panel_patch" not in log.names()


async def test_strategy_changed_after_purchase_is_recomputed_every_pass():
    """Админ сменил тариф на MONTH: окно теперь 1-е число, а не 7-е."""
    log = Log()
    session = TickSession(
        log, traffic_limit=PLAN_GB + 50, strategy="MONTH", grants=(a_grant(),)
    )
    remnawave = FakeRemnawave(log)

    # 1 октября 00:20 уже прошло — прибавка, купленная 18 сентября, кончилась.
    out = await reconcile(session, remnawave, datetime(2026, 10, 1, 1, 0, tzinfo=timezone.utc))

    assert out["ended"] == [1]
    assert log[log.index_of("panel_patch")][1]["trafficLimitBytes"] == gb_to_bytes(PLAN_GB)


async def test_missing_anchor_closes_by_the_promised_date_not_forever():
    """Якоря нет — считать момент нечем, но вечной прибавка быть не может.

    Раньше такая запись оставалась `active` НАВСЕГДА: крон её не закрывал, и лимит
    в панели оставался поднятым до следующего продления. Закрываем по сроку, который
    был НАЗВАН человеку при покупке и лежит в самой строке.
    """
    log = Log()
    session = TickSession(log, traffic_limit=PLAN_GB + 50, grants=(a_grant(created=None),))
    remnawave = FakeRemnawave(log)

    out = await reconcile(session, remnawave, AFTER_RESET)

    assert out["ended"] == [1]
    assert log[log.index_of("panel_patch")][1]["trafficLimitBytes"] == gb_to_bytes(PLAN_GB)


async def test_missing_anchor_before_the_promised_date_waits():
    """До обещанного срока прибавка живёт: платили именно за него."""
    log = Log()
    session = TickSession(log, traffic_limit=PLAN_GB + 50, grants=(a_grant(created=None),))
    remnawave = FakeRemnawave(log)

    out = await reconcile(session, remnawave, GRANTED + timedelta(days=1))

    assert out["ended"] == []
    assert "panel_patch" not in log.names()


async def test_long_expired_subscription_is_closed_silently():
    log = Log()
    session = TickSession(
        log,
        traffic_limit=PLAN_GB + 50,
        grants=(a_grant(),),
        expire_at=GRANTED - timedelta(days=10),
    )
    remnawave = FakeRemnawave(log)

    out = await reconcile(session, remnawave, AFTER_RESET)

    assert out["ended"] == [1] and out["reason"] == "expired"
    assert out["silent"] is True
    assert "panel_patch" not in log.names()


async def test_panel_failure_counts_an_attempt_and_reports_an_error():
    log = Log()
    session = TickSession(log, traffic_limit=PLAN_GB + 50, grants=(a_grant(),))
    remnawave = FakeRemnawave(log, fail=True)

    out = await reconcile(session, remnawave, AFTER_RESET)

    assert out.get("error")
    assert "bump_fail" in log.names()


def test_cron_settles_only_paid_orders_and_never_closes_pending():
    """Неоплаченный счёт оживает опоздавшей оплатой — закрывать его нельзя."""
    assert "t.status::text = 'COMPLETED'" in tick.OPEN_ORDERS_SQL
    assert "o.status IN ('pending', 'credited')" in tick.OPEN_ORDERS_SQL
    import inspect

    source = inspect.getsource(tick)
    assert "reject_order(session, order[\"id\"], \"panel_timeout\")" in source
    # Единственная причина отклонить по таймауту — заказ, по которому УЖЕ платили.
    assert "order[\"attempts\"] > 0" in source


def test_cron_runs_apart_from_the_device_cron():
    """Оба крона ходят в панель и берут те же строки users — расходимся по минутам."""
    schedule = tick.run_extra_traffic_tick.task_name, tick.run_extra_traffic_tick
    assert schedule is not None
    import inspect

    source = inspect.getsource(tick)
    assert '"cron": "*/17 * * * *"' in source


# ── окно выкатки: таблиц ещё нет ────────────────────────────────────────────


class NoTablesSession:
    """Любой запрос — «таблицы нет»: так выглядит воркер до накатки 0012."""

    def __init__(self) -> None:
        self.queries = 0

    async def execute(self, *a, **kw):
        self.queries += 1
        raise RuntimeError('relation "extra_traffic_orders" does not exist')

    async def commit(self):
        raise AssertionError("без таблиц коммитить нечего")

    async def rollback(self):
        return None


async def test_cron_is_silent_until_the_migration_lands(caplog):
    """Миграции катает ТОЛЬКО контейнер бота, а крон живёт в воркере.

    В окне выкатки воркер уже новый, таблиц ещё нет. Раньше первый же запрос
    поднимал ошибку наружу, и шедулер писал её в лог КАЖДЫЕ 17 МИНУТ — верный способ
    приучить владельца не читать свой лог. Молчим до следующего прохода.
    """
    import logging

    session = NoTablesSession()
    remnawave = FakeRemnawave(Log())

    raw = tick.run_extra_traffic_tick
    raw = getattr(raw, "__dishka_orig_func__", getattr(raw, "original_func", raw))
    with caplog.at_level(logging.WARNING):
        await raw(
            session=session,
            remnawave=remnawave,
            user_dao=SimpleNamespace(get_by_id=lambda _uid: None),
            notifier=SimpleNamespace(),
        )

    assert session.queries >= 1, "крон обязан попробовать — он не знает заранее"
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_missing_tables_have_their_own_error_type():
    """«Таблиц нет» — отдельный тип, чтобы молчать ТОЛЬКО о нём.

    Общий `except Exception` заодно проглотил бы настоящие поломки: сломанный SQL,
    отвалившуюся базу, ошибку в нашем же запросе — и крон молчал бы о них тоже.
    """
    assert issubclass(tick.TablesMissing, RuntimeError)
    # `@inject` dishka прячет нашу функцию — читаем исходную.
    raw = getattr(tick.run_extra_traffic_tick, "__dishka_orig_func__", tick.run_extra_traffic_tick)
    source = inspect.getsource(raw)
    assert "except TablesMissing" in source
    assert "except Exception" not in source
