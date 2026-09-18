"""Докупка трафика: сторожа SQL — куски запросов, на которых держатся деньги.

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ. Подделки в соседних тестах отвечают на запрос по куску текста
и не умеют ловить то, что из запроса ПРОПАЛО: «FOR UPDATE OF u» можно стереть, и все
они останутся зелёными, а на бою две вкладки купят два раза за одну цену. Здесь
заперты именно строки — дёшево и ровно там, где ошибка стоит денег.
"""

import importlib
import inspect

extra = importlib.import_module("src.infrastructure.services.overlay_extra_traffic")
tick = importlib.import_module("src.infrastructure.taskiq.tasks.extra_traffic")
endpoint = importlib.import_module("src.web.endpoints.public.extra_traffic")
statistics = importlib.import_module("src.web.endpoints.admin.statistics")
reserve = importlib.import_module("src.infrastructure.taskiq.tasks.reserve")


def test_state_is_read_under_the_row_lock():
    """Без замка строки users две покупки разом дали бы двойной объём за одну цену."""
    assert "FOR UPDATE OF u" in extra.LOCK_STATE_SQL
    # LEFT JOIN: человек без подписки не должен проваливать запрос вовсе.
    assert "LEFT JOIN subscriptions" in extra.LOCK_STATE_SQL
    # Показ читает ТО ЖЕ состояние, но без замка.
    assert "FOR UPDATE" not in extra.READ_STATE_SQL


def test_state_reads_both_limit_and_strategy():
    """Стратегия — половина ответа на вопрос «до какого момента живёт прибавка»."""
    assert "s.traffic_limit" in extra.LOCK_STATE_SQL
    assert "s.traffic_limit_strategy::text" in extra.LOCK_STATE_SQL
    assert "plan_snapshot->>'traffic_limit'" in extra.LOCK_STATE_SQL


def test_spending_is_conditional_not_read_then_write():
    """Условный UPDATE — единственная защита от ухода баланса в минус."""
    assert "cabinet_balance >= :amount" in extra.SPEND_SQL
    assert "RETURNING cabinet_balance" in extra.SPEND_SQL


def test_reserve_and_pause_are_read_as_open_only():
    assert "ended = false" in extra.PAUSE_RESERVE_SQL
    assert "reserve_expire_at > now()" in extra.PAUSE_RESERVE_SQL
    assert "active = true" in extra.PAUSE_RESERVE_SQL


def test_only_active_grants_are_loaded():
    """Кончившиеся прибавки не должны участвовать ни в потолке, ни в поле лимита."""
    assert "status = 'active'" in extra.ACTIVE_GRANTS_SQL
    assert "status = 'active'" in tick.ACTIVE_GRANT_USERS_SQL


def test_order_is_taken_under_its_own_lock():
    assert "FOR UPDATE" in extra.ORDER_FOR_UPDATE_SQL


def test_lock_order_is_order_then_user():
    """Обратный порядок замков столкнул бы вебхук с кроном взаимоблокировкой."""
    source = inspect.getsource(extra.handle_paid_order)
    assert source.index("order_for_update") < source.index("apply_order")
    apply_source = inspect.getsource(extra.apply_order)
    assert apply_source.index("lock_state") < apply_source.index("buy_from_balance")


def test_credited_money_lands_before_the_panel_is_asked():
    """Деньги на баланс — свой commit: панель может молчать сколько угодно."""
    source = inspect.getsource(extra.handle_paid_order)
    assert source.index("credit_order") < source.index("apply_order")
    assert "await session.commit()" in inspect.getsource(extra.credit_order)


def test_order_row_is_written_before_the_payment_link_is_returned():
    source = inspect.getsource(endpoint._checkout)
    assert source.index("create_payment(") < source.index("record_order")
    assert source.index("record_order") < source.rindex('"payment_url"')


def test_panel_body_is_narrow():
    """Полное тело вернуло бы человеку срок из нашей базы и съело дни паузы."""
    source = inspect.getsource(extra.set_traffic_limit)
    # Смотрим только на тело запроса к панели, а не на объяснение в док-строке.
    body = source[source.index("UpdateUserRequestDto(") : source.index("except Exception")]
    assert "traffic_limit_bytes=int(new_bytes)" in body
    assert "expire_at" not in body and "traffic_limit_strategy" not in body
    assert "internal_squads" not in body


def test_panel_answer_is_verified_in_bytes():
    source = inspect.getsource(extra.set_traffic_limit)
    assert "int(got) != int(new_bytes)" in source


def test_status_from_the_panel_answer_is_saved():
    """Панель сама снимает LIMITED — иначе кабинет врал бы до вебхука."""
    source = inspect.getsource(extra.buy_from_balance)
    assert "UPDATE subscriptions SET status" in source


def test_synthetic_plan_is_excluded_from_money_reports():
    """Снимок −5 не должен попасть ни в MRR, ни в топ тарифов, ни в право на резерв."""
    assert extra.SYNTHETIC_PLAN_ID == -5
    # Фильтры стоят с докупки устройства (−4) и отсекают ЛЮБОЙ синтетический снимок.
    stats_source = inspect.getsource(statistics)
    assert "plan_snapshot->>'id')::int ELSE 0 END) > 0" in stats_source
    assert "coalesce((plan_snapshot->>'id')::int, 0) >= 0" in stats_source
    assert "plan_snapshot->>'id')::int ELSE 0 END) > 0" in inspect.getsource(reserve)


def test_grants_are_marked_only_while_still_active():
    """Повторная пометка не должна воскрешать или переписывать причину."""
    assert "AND status = 'active'" in inspect.getsource(extra.mark_grants)
    assert "AND status = 'active'" in inspect.getsource(extra.close_due_grants)
    assert "AND status = 'active'" in inspect.getsource(extra.burn_on_renew)


def test_rejection_moves_only_forward():
    source = inspect.getsource(extra.reject_order)
    assert "status IN ('pending', 'credited')" in source
    assert "status = 'pending'" in inspect.getsource(extra.credit_order)
