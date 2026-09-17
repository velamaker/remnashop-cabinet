"""Итоги скидки на продление: считаем только деньги клиентов.

ЧТО ЗАПИРАЕМ. Владелец проверяет магазин собственными покупками, а шлюзы —
проверочными платежами. Попади они в «оплачено со скидкой» — кампания выглядела
бы окупившейся на деньгах, которые ходят по кругу (у статистики шлюзов так было:
треть показанной выручки — деньги самого владельца, см. stats_exclude_test.py).
  * SQL не пускает в соединение тестовые оплаты и оплаты персонала;
  * `summarize` проверяет то же самое ещё раз — на случай, если соединение
    однажды перепишут;
  * рубли не складываются с другими валютами;
  * read-only админ получает итоги без внутренних id людей.

Запуск — внутри образа бота, как остальные тесты рядом (см. ci.yml).
"""

import importlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

rd = importlib.import_module("src.infrastructure.services.overlay_renewal_discount")

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)


def row(gid: int, **over: Any) -> dict:
    base = {
        "id": gid,
        "user_id": 100 + gid,
        "percent": 10,
        "granted_at": NOW - timedelta(days=gid),
        "expires_at": NOW - timedelta(days=gid) + timedelta(days=5),
        "status": "used",
        "tg_status": "sent",
        "push_sent": 0,
        "user_role": "USER",
        "is_test": False,
        "currency": "RUB",
        "final_amount": Decimal("449.10"),
        "original_amount": Decimal("499.00"),
    }
    base.update(over)
    return base


ROWS = [
    row(1),
    # Проверочный платёж шлюза — соединение его не отдало бы, но пусть и отдало.
    row(2, is_test=True, final_amount=Decimal("1.80"), original_amount=Decimal("2.00")),
    # Выдача человеку, который с тех пор стал админом: деньги «свои».
    row(3, user_role="OWNER", final_amount=Decimal("899.10"), original_amount=Decimal("999.00")),
    # Оплата в долларах — в рубли не складывается, но «воспользовались» считается.
    row(4, currency="USD", final_amount=Decimal("4.50"), original_amount=Decimal("5.00")),
    # used, но оплату фильтр отрезал (LEFT JOIN дал пустые поля).
    row(5, is_test=None, currency=None, final_amount=None, original_amount=None),
    row(6, status="expired", is_test=None, currency=None, final_amount=None, original_amount=None),
    row(7, status="active", tg_status="failed", push_sent=1, is_test=None, currency=None,
        final_amount=None, original_amount=None),
    row(8, status="revoked", tg_status="blocked", is_test=None, currency=None,
        final_amount=None, original_amount=None),
]


def test_staff_and_test_payments_are_not_counted():
    got = rd.summarize(ROWS, period_days=90)

    assert got["granted"] == 7  # без выдачи персоналу
    assert got["used"] == 2  # клиентская в рублях + клиентская в долларах
    assert got["paid_rub"] == pytest.approx(449.10)
    assert got["discount_given_rub"] == pytest.approx(49.90)
    assert got["expired"] == 1 and got["active"] == 1 and got["revoked"] == 1
    assert got["tg_failed"] == 2
    assert got["push_delivered"] == 1
    assert got["period_days"] == 90
    assert all(r["user_id"] is not None for r in got["recent"])


def test_readonly_admin_sees_no_user_ids():
    got = rd.summarize(ROWS, period_days=30, hide_ids=True)
    assert got["recent"] and all(r["user_id"] is None for r in got["recent"])


def test_sql_excludes_test_payments_and_staff():
    sql = " ".join(rd.STATS_SQL.split())
    join = sql.split("LEFT JOIN transactions t", 1)[1].split("WHERE g.granted_at", 1)[0]
    assert "t.is_test = false" in join
    assert "t.user_id NOT IN (SELECT su.id FROM users su WHERE su.role::text <> 'USER')" in join
    # Использование скидки тоже не засчитывается проверочным платежом.
    assert "t.is_test = false" in " ".join(rd.USAGE_PAYMENTS_SQL.split())


class StatsSession:
    def __init__(self) -> None:
        self.params: dict = {}

    async def execute(self, statement: Any, params: Any = None) -> Any:
        assert str(statement) == rd.STATS_SQL
        self.params = dict(params or {})
        rows = ROWS

        class _R:
            def mappings(self) -> "_R":
                return self

            def all(self) -> list:
                return rows

        return _R()


@pytest.mark.asyncio
async def test_stats_window_is_period_days():
    session = StatsSession()
    got = await rd.stats(session, 30, now=NOW, hide_ids=True)
    assert session.params["since"] == NOW - timedelta(days=30)
    assert got["period_days"] == 30
    assert all(r["user_id"] is None for r in got["recent"])
