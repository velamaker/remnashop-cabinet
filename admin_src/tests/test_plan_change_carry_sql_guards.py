"""Перенос остатка: условия внутри SQL, от которых зависят деньги.

ЗАЧЕМ ПО ТЕКСТУ. Тесты оркестровки подменяют загрузку состояния шпионом, а смысл
запросов на настоящем Postgres проверяет opt-in test_plan_change_carry_pg.py — в CI без
базы он пропускается. Выпавшее условие здесь стоит чужих денег:
  * слои без `status = 'COMPLETED'` / `is_test = false` / `final_amount > 0` — в перенос
    уйдут неоплаченные, тестовые или бесплатные счета;
  * без `payment_id <> :exclude` счёт смены посчитает сам себя;
  * RENEW не строго после отсечки — продление, уже пересчитанное в перенос, посчитается
    второй раз; окно создающего шире — чужой счёт станет слоем;
  * резерв без `ended = false` и `reserve_expire_at > now()` — старая выдача навсегда
    отключит перенос оплаченных дней;
  * возвраты без `REFUNDED` за 365 дней — возвращённые деньги уедут в дни;
  * замок без `FOR UPDATE OF u` — два вебхука перенесут одну строку дважды;
  * журнал без `ON CONFLICT (payment_id) DO NOTHING` — повтор упадёт или задвоится.

Пробелы нормализуются: переносы строк при рефакторинге — не повод падать.
"""

import importlib
import re

import pytest

carry = importlib.import_module("src.infrastructure.services.overlay_plan_change")


def norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


@pytest.mark.parametrize(
    "name, fragments",
    [
        (
            "LAYERS_SQL",
            [
                "t.status = 'COMPLETED'",
                "t.is_test = false",
                "(t.pricing->>'final_amount')::numeric > 0",
                "(t.plan_snapshot->>'id')::int > 0",
                "(t.plan_snapshot->>'duration')::int > 0",
                "t.payment_id <> CAST(:exclude AS uuid)",
                "NOT EXISTS (SELECT 1 FROM gift_payments gp WHERE gp.payment_id = t.payment_id)",
                "COALESCE(t.gateway_display_name, '') <> 'Баланс · подарок'",
                "t.purchase_type = 'RENEW' AND t.updated_at > CAST(:cut AS timestamptz)",
                "OR t.payment_id = CAST(:create_pid AS uuid)",
                "CAST(:with_window AS boolean) AND t.purchase_type IN ('NEW', 'CHANGE')",
                "(t.plan_snapshot->>'id')::int = CAST(:row_plan_id AS integer)",
                "CAST(:row_created AS timestamptz) - interval '120 seconds'",
                "CAST(:row_created AS timestamptz) + interval '5 seconds'",
                "WHERE t.user_id = :uid",
                "ORDER BY t.updated_at DESC",
            ],
        ),
        ("RESERVE_SQL", ["ended = false", "reserve_expire_at > now()", "user_id = :uid"]),
        (
            "REFUND_SQL",
            [
                "t.status = 'REFUNDED'",
                "interval '365 days'",
                "t.user_id = :uid",
                "t.is_test = false",
                "(t.plan_snapshot->>'id')::int > 0",
                "NOT EXISTS (SELECT 1 FROM gift_payments gp WHERE gp.payment_id = t.payment_id)",
            ],
        ),
        ("FREEZE_SQL", ["user_id = :uid AND active = true"]),
        ("CLOSE_FREEZE_SQL", ["SET active = false", "WHERE user_id = :uid AND active = true"]),
        ("LOCK_CURRENT_SQL", ["JOIN subscriptions s ON s.id = u.current_subscription_id", "WHERE u.id = :uid", "FOR UPDATE OF u"]),
        ("LAST_CARRY_SQL", ["WHERE subscription_id = :sid", "ORDER BY created_at DESC", "payment_id::text"]),
        ("PRICES_SQL", ["d.plan_id = ANY(CAST(:ids AS integer[]))", "pl.is_active"]),
        ("INSERT_CARRY_SQL", ["ON CONFLICT (payment_id) DO NOTHING"]),
        ("CARRY_APPLIED_SQL", ["WHERE payment_id = CAST(:pid AS uuid)"]),
        ("CARRY_FOR_SUBSCRIPTION_SQL", ["subscription_id = :sid AND source = 'purchase'"]),
        ("CARRIES_BY_SOURCE_SQL", ["CAST(:pid AS uuid) = ANY(source_payment_ids) OR payment_id = CAST(:pid AS uuid)"]),
    ],
)
def test_sql_keeps_money_conditions(name, fragments):
    sql = norm(getattr(carry, name))
    for fragment in fragments:
        assert norm(fragment) in sql, f"{name}: пропало «{fragment}»"


def test_windows_match_constants():
    """Окно создающего счёта в SQL и в константах — одно и то же."""
    sql = norm(carry.LAYERS_SQL)
    assert f"interval '{carry.CREATE_WINDOW_BEFORE} seconds'" in sql
    assert f"interval '{carry.CREATE_WINDOW_AFTER} seconds'" in sql


def test_renew_cut_is_strict():
    """`>=` или сдвиг отсечки назад вернули бы в слои продление, уже ушедшее в перенос."""
    sql = norm(carry.LAYERS_SQL)
    assert "updated_at >= CAST(:cut" not in sql
    assert ":cut AS timestamptz) - interval" not in sql


def test_lock_waits_are_bounded():
    """Замок строки users ждёт не дольше lock_timeout — и только на этом запросе."""
    import inspect

    src = norm(inspect.getsource(carry.lock_current_subscription))
    assert "SET LOCAL lock_timeout = '{int(timeout_ms)}ms'" in src
    assert "SET LOCAL lock_timeout TO DEFAULT" in src
    assert src.index("SET LOCAL lock_timeout =") < src.index("LOCK_CURRENT_SQL") < src.index("TO DEFAULT")


def test_reserve_is_not_granted_over_active_pause():
    """Резерв поверх паузы сдвигал срок строки на окно и прятал оплаченный остаток паузы."""
    import importlib.util
    import pathlib

    origin = importlib.util.find_spec("src.infrastructure.taskiq.tasks.reserve").origin
    src = pathlib.Path(origin).read_text("utf-8")
    assert "SELECT 1 FROM subscription_freezes f " in src
    assert "WHERE f.user_id = u.id AND f.active = true" in src
