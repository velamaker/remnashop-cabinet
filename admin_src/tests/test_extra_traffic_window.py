"""Момент обнуления трафика: наша формула против формулы панели.

ЗАЧЕМ ЭТОТ ФАЙЛ. Срок докупленных гигабайт — это ЧУЖОЙ момент: его назначает
планировщик панели, а мы обязаны назвать его человеку до оплаты и снять прибавку
ровно тогда. Ошибёмся на часы — человек либо теряет оплаченное, либо получает
лишнее каждый месяц.

Таблица случаев повторяет scheduler.js Remnawave 3.4.4:
  DAY 00:05, MONTH_ROLLING 00:10, WEEK (понедельник) 00:15, MONTH (1-е) 00:20 UTC;
  MONTH_ROLLING берёт день LEAST(day(created_at), последний день месяца) и не
  раньше, чем `created_at + 1 месяц <= текущая дата`.

МУТАЦИИ, которые обязаны ронять этот файл: заменить 00:10 на 00:00; убрать зажим
дня по длине месяца; убрать порог «не раньше месяца»; вернуть «конец подписки»
вместо ближайшего сброса.

Даты синтетические.
"""

import importlib
from datetime import datetime, timezone

import pytest

extra = importlib.import_module("src.infrastructure.services.overlay_extra_traffic")


def dt(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


def test_no_reset_has_no_moment():
    """NO_RESET: расход не обнуляется, значит прибавка живёт до продления/смены."""
    assert extra.next_traffic_reset("NO_RESET", dt(2026, 1, 1), dt(2026, 3, 5, 10)) is None
    assert extra.next_traffic_reset("SOMETHING_NEW", None, dt(2026, 3, 5, 10)) is None


@pytest.mark.parametrize(
    "now, expected",
    [
        # До 00:05 сегодня — сброс сегодня же.
        (dt(2026, 3, 5, 0, 1), dt(2026, 3, 5, 0, 5)),
        # Ровно в 00:05 он уже прошёл — значит завтра.
        (dt(2026, 3, 5, 0, 5), dt(2026, 3, 6, 0, 5)),
        (dt(2026, 3, 5, 23, 59), dt(2026, 3, 6, 0, 5)),
        # Конец месяца и года — переход календаря.
        (dt(2026, 12, 31, 12, 0), dt(2027, 1, 1, 0, 5)),
    ],
)
def test_day_resets_at_00_05(now, expected):
    assert extra.next_traffic_reset("DAY", None, now) == expected


@pytest.mark.parametrize(
    "now, expected",
    [
        # 5 марта 2026 — четверг. Ближайший понедельник — 9-е.
        (dt(2026, 3, 5, 12), dt(2026, 3, 9, 0, 15)),
        # Понедельник до 00:15 — сегодня.
        (dt(2026, 3, 9, 0, 1), dt(2026, 3, 9, 0, 15)),
        # Понедельник после 00:15 — через неделю.
        (dt(2026, 3, 9, 0, 15), dt(2026, 3, 16, 0, 15)),
    ],
)
def test_week_resets_monday_00_15(now, expected):
    assert extra.next_traffic_reset("WEEK", None, now) == expected


@pytest.mark.parametrize(
    "now, expected",
    [
        (dt(2026, 3, 5, 12), dt(2026, 4, 1, 0, 20)),
        (dt(2026, 3, 1, 0, 1), dt(2026, 3, 1, 0, 20)),
        (dt(2026, 3, 1, 0, 20), dt(2026, 4, 1, 0, 20)),
        (dt(2026, 12, 15, 12), dt(2027, 1, 1, 0, 20)),
    ],
)
def test_month_resets_first_day_00_20(now, expected):
    assert extra.next_traffic_reset("MONTH", None, now) == expected


def test_month_rolling_uses_panel_created_day():
    """День сброса — день создания пользователя ПАНЕЛИ, час — 00:10 UTC."""
    created = dt(2025, 5, 7, 14, 30)
    assert extra.next_traffic_reset("MONTH_ROLLING", created, dt(2026, 3, 5, 12)) == dt(
        2026, 3, 7, 0, 10
    )
    # Тот же день, но 00:10 уже прошли — значит следующий месяц.
    assert extra.next_traffic_reset("MONTH_ROLLING", created, dt(2026, 3, 7, 0, 10)) == dt(
        2026, 4, 7, 0, 10
    )


def test_month_rolling_clamps_day_to_short_month():
    """Создан 31-го — в феврале сброс 28-го (LEAST(day, последний день месяца))."""
    created = dt(2025, 1, 31, 9, 0)
    assert extra.next_traffic_reset("MONTH_ROLLING", created, dt(2026, 2, 1, 12)) == dt(
        2026, 2, 28, 0, 10
    )
    # Високосный 2028-й — 29-е.
    assert extra.next_traffic_reset("MONTH_ROLLING", created, dt(2028, 2, 1, 12)) == dt(
        2028, 2, 29, 0, 10
    )
    # В марте день возвращается к 31-му: зажим не «прилипает».
    assert extra.next_traffic_reset("MONTH_ROLLING", created, dt(2026, 3, 1, 12)) == dt(
        2026, 3, 31, 0, 10
    )


def test_month_rolling_not_earlier_than_a_month_after_creation():
    """Первый сброс не раньше «создан + месяц»: так фильтрует крон панели.

    Создан 15 марта в 12:00 — 15 апреля крон сравнивает `created_at + 1 месяц`
    (15 апреля 12:00) с полуночью текущей даты и человека НЕ берёт. Значит первый
    сброс — 15 мая.
    """
    created = dt(2026, 3, 15, 12, 0)
    assert extra.next_traffic_reset("MONTH_ROLLING", created, dt(2026, 3, 20, 10)) == dt(
        2026, 5, 15, 0, 10
    )
    # Создан в полночь — порог выполняется ровно в день «плюс месяц».
    midnight = dt(2026, 3, 15, 0, 0)
    assert extra.next_traffic_reset("MONTH_ROLLING", midnight, dt(2026, 3, 20, 10)) == dt(
        2026, 4, 15, 0, 10
    )


def test_month_rolling_without_anchor_is_unknown_not_guessed():
    """Без панельной даты честнее не знать, чем назвать чужое число."""
    assert extra.next_traffic_reset("MONTH_ROLLING", None, dt(2026, 3, 5, 12)) is None
    assert extra.needs_anchor("MONTH_ROLLING") is True
    assert extra.needs_anchor("MONTH") is False


def test_strategy_accepts_enum_and_string():
    """В базе это enum, в панели строка — считать надо одинаково."""
    from remnapy.enums.users import TrafficLimitStrategy

    now = dt(2026, 3, 5, 12)
    assert extra.next_traffic_reset(TrafficLimitStrategy.MONTH, None, now) == dt(2026, 4, 1, 0, 20)
    assert extra.strategy_name(TrafficLimitStrategy.NO_RESET) == "NO_RESET"


def test_reset_hours_are_the_panel_hours_not_the_base_ones():
    """Часы базовой get_traffic_reset_delta (00:00/00:05/00:10) нам не подходят."""
    assert extra.RESET_AT_UTC == {
        "DAY": (0, 5),
        "MONTH_ROLLING": (0, 10),
        "WEEK": (0, 15),
        "MONTH": (0, 20),
    }


def test_reset_is_not_the_end_of_subscription():
    """Главная мутация: вернуть конец подписки вместо ближайшего обнуления.

    Годовая подписка на MONTH_ROLLING обязана дать момент внутри месяца, а не через
    год — иначе один платёж давал бы +N ГБ двенадцать раз.
    """
    created = dt(2025, 6, 10, 8)
    moment = extra.next_traffic_reset("MONTH_ROLLING", created, dt(2026, 3, 5, 12))
    assert moment == dt(2026, 3, 10, 0, 10)
    assert (moment - dt(2026, 3, 5, 12)).days < 40
