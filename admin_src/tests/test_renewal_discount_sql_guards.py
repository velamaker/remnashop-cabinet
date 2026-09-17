"""Скидка на продление и сегмент «Истекают скоро»: условия внутри SQL.

ЗАЧЕМ ПО ТЕКСТУ. FakeDb в test_renewal_discount_run.py узнаёт запросы по константам
и сам повторяет их смысл на Python, а настоящий SQL гоняет только drill на копии
базы — вручную и по агрегатам. Поэтому выпавшее из запроса условие CI не видит, а
цена у каждого из них — чужие деньги или не те получатели:
  * CLEAR_DISCOUNT без `purchase_discount = :p` — сгорание нашей выдачи обнулит
    пришедший позже win-back или скидку, выставленную админом;
  * APPLY_DISCOUNT без `purchase_discount = 0` — наша скидка затрёт чужую;
  * CLAIM без `notified_at IS NULL` — человеку придёт второе сообщение;
  * CANDIDATES без `r.ended = false` — закончившийся когда-то резерв навсегда
    отрежет человека от скидки;
  * PAYMENTS без отсева бесплатных, тестовых и подарочных оплат — скидку получат
    не платившие;
  * EXPIRING_WHERE без пробных, резерва и Telegram — рассылка уйдёт не тем.

Пробелы нормализуются: переносы строк и отступы при рефакторинге — не повод падать.
Запуск — внутри образа бота, как остальные тесты рядом (см. ci.yml).
"""

import importlib
import re

import pytest

rd = importlib.import_module("src.infrastructure.services.overlay_renewal_discount")
segment = importlib.import_module("src.infrastructure.services.overlay_expiring_segment")


def norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


@pytest.mark.parametrize(
    "name, fragments",
    [
        ("CLEAR_DISCOUNT_SQL", ["WHERE id = :u AND purchase_discount = :p"]),
        (
            "APPLY_DISCOUNT_SQL",
            [
                "purchase_discount = 0",
                "personal_discount < :p",
                "is_blocked = false",
                "(autopay_enabled = false OR NOT CAST(:autopay_guard AS boolean))",
            ],
        ),
        ("CLAIM_SQL", ["WHERE id = :id AND notified_at IS NULL", "RETURNING id"]),
        ("MARK_CLOSED_SQL", ["WHERE id = :id AND status = 'active'", "RETURNING id"]),
        ("MARK_USED_SQL", ["WHERE id = :id AND status = :was", "RETURNING id"]),
        ("INSERT_GRANT_SQL", ["ON CONFLICT DO NOTHING", "RETURNING id"]),
        (
            "CANDIDATES_SQL",
            [
                "FROM reserve_grants r WHERE r.user_id = u.id AND r.ended = false) AS on_reserve",
                "FROM subscription_freezes f WHERE f.user_id = u.id AND f.active) AS frozen",
                "tp.status::text = 'PENDING' AND tp.created_at > :pending_since",
                "p.reward_type::text = 'SUBSCRIPTION'",
                "WHERE w.user_id = u.id AND w.used = false",
                "WHERE td.user_id = u.id AND td.used = false",
                "WHERE g.user_id = u.id AND g.status = 'active') AS open_grant_until",
                "JOIN subscriptions s ON s.id = u.current_subscription_id",
                "s.status::text = 'ACTIVE' AND s.expire_at >= :lo AND s.expire_at < :hi",
            ],
        ),
        (
            "PAYMENTS_SQL",
            [
                "t.status::text = 'COMPLETED' AND t.is_test = false",
                "(t.plan_snapshot->>'id')::int > 0",
                "(t.pricing->>'final_amount')::numeric > 0",
                "AND NOT EXISTS (SELECT 1 FROM gift_payments gp WHERE gp.payment_id = t.payment_id)",
            ],
        ),
        (
            "USAGE_PAYMENTS_SQL",
            ["t.status::text = 'COMPLETED' AND t.is_test = false", "t.created_at >= :since"],
        ),
        (
            "ACTIVE_BY_USER_SQL",
            [
                "g.status = 'active' AND g.expires_at > :now",
                "u.purchase_discount >= g.percent",
                "min(g.expires_at) AS expires_at",
            ],
        ),
        ("DUE_EXPIRE_SQL", ["WHERE status = 'active' AND expires_at <= :now"]),
        (
            "PENDING_NOTIFY_SQL",
            ["g.status = 'active' AND g.notified_at IS NULL", "g.expires_at > :now"],
        ),
        (
            "STATS_SQL",
            [
                "AND t.is_test = false",
                "t.user_id NOT IN (SELECT su.id FROM users su WHERE su.role::text <> 'USER')",
            ],
        ),
    ],
)
def test_renewal_discount_sql_keeps_its_guards(name: str, fragments: list[str]):
    sql = norm(getattr(rd, name))
    for fragment in fragments:
        assert norm(fragment) in sql, f"{name}: пропало условие «{fragment}»"


def test_candidates_sql_limit_matches_constant():
    """Предпросмотр помечает «обрезано» по CANDIDATES_LIMIT — число в запросе то же."""
    assert f"LIMIT {rd.CANDIDATES_LIMIT}" in norm(rd.CANDIDATES_SQL)


@pytest.mark.parametrize(
    "fragment",
    [
        "u.is_blocked = false",
        "u.is_bot_blocked = false",
        "u.telegram_id IS NOT NULL",
        "s.status::text = 'ACTIVE'",
        "s.is_trial = false",
        "s.expire_at > now()",
        "s.expire_at <= now() + make_interval(days => :days)",
        "NOT EXISTS (SELECT 1 FROM reserve_grants r WHERE r.user_id = u.id AND r.ended = false)",
    ],
)
def test_expiring_segment_keeps_its_guards(fragment: str):
    assert norm(fragment) in norm(segment.EXPIRING_WHERE)


def test_expiring_segment_is_the_only_where_for_count_and_recipients():
    """Счётчик и получатели обязаны брать одно и то же условие, а не свою копию."""
    import inspect

    for fn in (segment.count_expiring, segment.expiring_user_ids):
        src = inspect.getsource(fn)
        assert "{EXPIRING_WHERE}" in src and "{_FROM}" in src
