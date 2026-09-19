"""Кому МОЖНО написать о незавершённой оплате — и, главное, кому нельзя.

ЦЕНА ОШИБКИ ЗДЕСЬ — ДЕНЬГИ ЧЕЛОВЕКА. Восемь скептиков разобрали первоначальный
проект и нашли пять дыр; четыре из них денежные, и каждая начиналась с одного и того
же заблуждения: «счёт в PENDING — значит человек не заплатил». Это неправда:
  • оплата ОДНОГО счёта не гасит остальные счета того же человека (на каждый
    перебранный шлюз создаётся свой), и они висят до получасового крона базы;
  • оплата с баланса и автоплатёж тоже оставляют PENDING-строку;
  • подтверждение по СБП/3-D Secure и повтор вебхука занимают минуты.
Поэтому правила смотрят на ЧЕЛОВЕКА (`paid_after`, `newer_txn`, `sub_touched`), а не
на строку счёта, и каждый такой случай заперт отдельным тестом.

Остальное здесь — про уважение: тестовые счета владельца, персонал, заблокированные,
отказавшиеся, частота и окно. Данные синтетические.
"""

import importlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

pr = importlib.import_module("src.infrastructure.services.overlay_payment_reminder")

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


def cfg(**over):
    return pr.normalize({**pr.DEFAULT_CONFIG, "enabled": True, **over})


def candidate(**over) -> pr.Candidate:
    base = dict(
        payment_id="11111111-1111-1111-1111-111111111111",
        user_id=7,
        telegram_id=123456,
        lang="ru",
        is_blocked=False,
        is_bot_blocked=False,
        is_test=False,
        role="USER",
        plan_id=5,
        plan_name="HOME",
        amount=Decimal("339"),
        currency="RUB",
        created_at=NOW - timedelta(minutes=12),
        paid_after=False,
        newer_txn=False,
        sub_touched=False,
        opted_out=False,
        reminded_24h=False,
        reminded_30d=0,
        already_row=False,
    )
    base.update(over)
    return pr.Candidate(**base)


def test_normal_case_gets_a_message():
    assert pr.decide(candidate(), cfg(), NOW) is None


@pytest.mark.parametrize(
    "field, reason",
    [
        ("paid_after", "paid"),          # заплатил другим шлюзом / с баланса / автоплатежом
        ("newer_txn", "newer_attempt"),  # ушёл в другой шлюз — этот счёт брошен сознательно
        ("sub_touched", "subscription_changed"),
        ("opted_out", "opted_out"),
        ("is_blocked", "blocked"),
        ("is_bot_blocked", "blocked"),
        ("is_test", "test_payment"),
        ("already_row", "already_handled"),
        ("reminded_24h", "cooldown"),
    ],
)
def test_reasons_not_to_write(field, reason):
    assert pr.decide(candidate(**{field: True}), cfg(), NOW) == reason


def test_paid_wins_over_cooldown():
    """Причина в сводке должна быть честной: «уже заплатил» важнее «кулдауна»."""
    c = candidate(paid_after=True, reminded_24h=True)
    assert pr.decide(c, cfg(), NOW) == "paid"


def test_test_payment_of_the_owner_is_never_touched():
    """Проверочный платёж владельца — синтетический тариф −1."""
    assert pr.decide(candidate(plan_id=pr.TEST_PLAN_ID), cfg(), NOW) == "test_payment"


def test_staff_invoices_are_skipped():
    for role in ("OWNER", "ADMIN", "DEV", "PREVIEW"):
        assert pr.decide(candidate(role=role), cfg(), NOW) == "staff"


def test_user_without_telegram_is_skipped():
    """В кабинете регистрируются по почте: телеграма может не быть вовсе."""
    assert pr.decide(candidate(telegram_id=None), cfg(), NOW) == "no_telegram"


def test_unknown_synthetic_kind_is_skipped():
    """Незнакомый синтетический id — не наше дело: что предлагать, мы не знаем."""
    assert pr.decide(candidate(plan_id=-9), cfg(), NOW) == "unknown_kind"


def test_too_early_and_too_late():
    early = candidate(created_at=NOW - timedelta(minutes=3))
    assert pr.decide(early, cfg(), NOW) == "too_early"
    late = candidate(created_at=NOW - timedelta(hours=3))
    # Досылки нет намеренно: «оплата не завершилась» через три часа читается как спам.
    assert pr.decide(late, cfg(), NOW) == "too_late"


def test_month_cap():
    assert pr.decide(candidate(reminded_30d=3), cfg(), NOW) == "month_cap"
    assert pr.decide(candidate(reminded_30d=2), cfg(), NOW) is None


def test_switch_defaults_to_off_and_survives_broken_values():
    assert pr.DEFAULT_CONFIG["enabled"] is False
    # Строка "true" — это не включено: руками правленый файл не должен запускать рассылку.
    assert pr.normalize({"enabled": "true"})["enabled"] is False
    assert pr.normalize({"enabled": True})["enabled"] is True
    # Окно обязано быть окном.
    fixed = pr.normalize({"enabled": True, "delay_minutes": 20, "max_age_minutes": 15})
    assert fixed["max_age_minutes"] > fixed["delay_minutes"]
    # Значения за границами зажимаются, а мусор не роняет конфиг.
    assert pr.normalize({"delay_minutes": 999})["delay_minutes"] == 25
    assert pr.normalize({"delay_minutes": "скоро"})["delay_minutes"] == 10


def test_window_bounds_are_the_age_window():
    start, end = pr.window_bounds(cfg(delay_minutes=10, max_age_minutes=45), NOW)
    assert end == NOW - timedelta(minutes=10)
    assert start == NOW - timedelta(minutes=45)


def test_message_says_nothing_it_cannot_know():
    """Мы не знаем, почему человек не заплатил, — и не выдумываем причину."""
    html = pr.build_message("plan", Decimal("339"), "RUB", "ru")
    assert "339 ₽" in html
    for invented in ("карта", "страница", "передумали", "банк"):
        assert invented not in html.lower()
    # Ни слова про «ту же ссылку»: старый счёт мы не воскрешаем.
    assert "ссылка" not in html.lower()


def test_message_escapes_plan_names_from_the_admin_panel():
    html = pr.build_message("plan", None, None, "ru")
    assert "<b>" in html  # разметка своя
    hacked = pr._escape("<script>alert(1)</script>")
    assert "<script>" not in hacked


def test_button_leads_to_the_cabinet_not_to_the_old_invoice():
    assert pr.button_url("https://cab.example", "topup") == "https://cab.example/balance"
    assert pr.button_url("https://cab.example/", "plan") == "https://cab.example/billing"
    assert pr.button_url("https://cab.example", "traffic").endswith("extra_traffic=1")
    # Без адреса кабинета кнопки нет: пустой url Telegram отверг бы вместе с сообщением.
    assert pr.button_url("", "plan") == ""
