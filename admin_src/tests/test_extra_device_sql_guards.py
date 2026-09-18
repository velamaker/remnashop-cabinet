"""Докупка устройства: сторожа SQL — куски запросов, на которых держатся деньги.

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ. Подделки в соседних тестах отвечают на запрос по куску текста
и не умеют ловить то, что из запроса ПРОПАЛО: «FOR UPDATE OF u» можно стереть, и все
они останутся зелёными, а на бою две вкладки купят два места за одно. Здесь заперты
именно строки — дёшево и ровно там, где ошибка стоит денег.
"""

import importlib
import inspect

extra = importlib.import_module("src.infrastructure.services.overlay_extra_device")
tick = importlib.import_module("src.infrastructure.taskiq.tasks.extra_devices")
statistics = importlib.import_module("src.web.endpoints.admin.statistics")
reserve = importlib.import_module("src.infrastructure.taskiq.tasks.reserve")


def test_state_is_read_under_the_row_lock():
    """Без замка строки users две покупки разом дали бы два места при лимите один."""
    assert "FOR UPDATE OF u" in extra.LOCK_STATE_SQL
    # LEFT JOIN: человек без подписки не должен проваливать запрос вовсе.
    assert "LEFT JOIN subscriptions" in extra.LOCK_STATE_SQL


def test_spending_is_conditional_not_read_then_write():
    """Условный UPDATE — единственная защита от ухода баланса в минус."""
    assert "cabinet_balance >= :amount" in extra.SPEND_SQL
    assert "RETURNING cabinet_balance" in extra.SPEND_SQL


def test_reserve_and_pause_are_read_as_open_only():
    assert "ended = false" in extra.PAUSE_RESERVE_SQL
    assert "reserve_expire_at > now()" in extra.PAUSE_RESERVE_SQL
    assert "active = true" in extra.PAUSE_RESERVE_SQL


def test_order_is_taken_under_its_own_lock():
    assert "FOR UPDATE" in extra.ORDER_FOR_UPDATE_SQL


def test_lock_order_is_order_then_user():
    """Обратный порядок замков столкнул бы вебхук с кроном взаимоблокировкой."""
    source = inspect.getsource(extra.handle_paid_order)
    assert source.index("order_for_update") < source.index("apply_order")
    apply_source = inspect.getsource(extra.apply_order)
    # Внутри применения первым делом — состояние под замком users, заказ уже заперт.
    assert apply_source.index("lock_state") < apply_source.index("buy_from_balance")


def test_carry_orders_take_only_applied_orders_of_live_slots():
    """Иначе в дни ушла бы стоимость неоплаченных или уже сгоревших мест."""
    assert "o.status = 'applied'" in extra.CARRY_ORDERS_SQL
    assert "s.status = 'active'" in extra.CARRY_ORDERS_SQL


def test_cron_settles_only_orders_with_a_completed_payment():
    assert "t.status::text = 'COMPLETED'" in tick.OPEN_ORDERS_SQL
    assert "o.status IN ('pending', 'credited')" in tick.OPEN_ORDERS_SQL


def test_reminder_is_claimed_by_the_database_not_by_the_code():
    """Захват строки — единственное, что не даёт послать напоминание дважды.

    Подделка сессии в соседнем тесте сама решает, «занял» ли проход строку, и
    пропажу условия из UPDATE не заметит: вторым проходом крона человек получил бы
    второе «место скоро кончится», а при двух воркерах — и два разом.
    """
    source = inspect.getsource(extra._claim_reminders)
    assert "SET reminded_at = now()" in source
    assert "reminded_at IS NULL" in source
    assert "RETURNING id" in source


def test_mrr_counts_only_real_plan_payments():
    """Докупка за 40 ₽ последним платежом обрушила бы MRR человека."""
    source = inspect.getsource(statistics)
    lastpay = source[source.index("lastpay AS ("):]
    lastpay = lastpay[: lastpay.index(")\n                SELECT")]
    assert "(t.plan_snapshot->>'id')::int > 0" in lastpay


def test_reserve_requires_a_plan_purchase():
    """Правило владельца: резерв только после ПОКУПКИ, а не после пополнения."""
    source = inspect.getsource(reserve)
    assert "(t.plan_snapshot->>'id')::int > 0" in source
