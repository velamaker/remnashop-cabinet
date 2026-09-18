"""Дата обновления трафика: один источник правды на три места.

ЗАЧЕМ ЭТОТ СТОРОЖ. Момент обнуления расхода называют человеку три разных экрана:
кабинет (карточка трафика и срок докупки), сообщение «трафик исчерпан» и карточка
подписки в меню бота. Пока они считали по-разному, расхождение было незаметным.
С докупкой оно становится обещанием за деньги: кабинет говорит «прибавка до 7-го»,
а бот — «сброс через 21 день». Все три обязаны дать ОДИН момент.

МУТАЦИИ, которые обязаны ронять файл: сменить часы в любом из трёх мест; вернуть
якорь на `subscription.created_at`; забыть `reset(token)` в обёртке вебхука.

Даты синтетические.
"""

import importlib
import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

extra = importlib.import_module("src.infrastructure.services.overlay_extra_traffic")
patch = importlib.import_module("overlay_patches.traffic_reset_date")
service = importlib.import_module("src.application.services.remnawave")

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
# Панельный пользователь создан 7-го: обнуление 7 октября в 00:10 UTC.
PANEL_CREATED = datetime(2025, 5, 7, 14, 30, tzinfo=timezone.utc)
# Наша строка подписки пересоздавалась при смене тарифа — день ДРУГОЙ.
OUR_CREATED = datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc)
WINDOW = datetime(2026, 10, 7, 0, 10, tzinfo=timezone.utc)

CASES = [
    ("DAY", None, datetime(2026, 9, 19, 0, 5, tzinfo=timezone.utc)),
    ("WEEK", None, datetime(2026, 9, 21, 0, 15, tzinfo=timezone.utc)),
    ("MONTH", None, datetime(2026, 10, 1, 0, 20, tzinfo=timezone.utc)),
    ("MONTH_ROLLING", PANEL_CREATED, WINDOW),
]


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    monkeypatch.setattr(extra, "now_utc", lambda: NOW)
    return NOW


@pytest.mark.parametrize("strategy, anchor, expected", CASES)
def test_three_paths_give_one_moment(strategy, anchor, expected):
    """Расчёт срока докупки, подставленная дельта базы и сама формула — одно число."""
    # 1. Формула (её же зовёт срок прибавки при покупке).
    assert extra.next_traffic_reset(strategy, anchor, NOW) == expected

    # 2. Подставленная замена базовой get_traffic_reset_delta: контекст с панельным
    #    якорем, как его кладёт обёртка вебхука.
    token = patch._PANEL_CREATED.set(anchor)
    try:
        delta = service.get_traffic_reset_delta(strategy, OUR_CREATED)
    finally:
        patch._PANEL_CREATED.reset(token)
    assert NOW + delta == expected

    # 3. Срок жизни прибавки, записываемый в extra_traffic_grants.
    q = extra.quote(
        {"gb_per_purchase": 50, "price_rub": 50, "min_amount_rub": 10},
        NOW,
        extra.next_traffic_reset(strategy, anchor, NOW),
        NOW + timedelta(days=365),
    )
    assert q.window_end == expected


def test_substitution_lives_in_the_consumer_module_not_in_core_utils():
    """Потребитель связал старый объект функции на своём импорте — правим у него."""
    assert service.get_traffic_reset_delta is patch.traffic_reset_delta
    import src.core.utils.time as base_time

    assert base_time.get_traffic_reset_delta is not patch.traffic_reset_delta


def test_no_reset_still_means_zero_not_now():
    """База отдаёт ноль при NO_RESET — замена обязана вести себя так же."""
    assert service.get_traffic_reset_delta("NO_RESET", OUR_CREATED) == timedelta(0)


def test_without_the_panel_anchor_we_fall_back_to_the_old_one():
    """Вне вебхука якоря нет: считаем по нашей дате, но по ПРАВИЛЬНЫМ часам и порогу.

    Наша строка создана 21 августа 09:00, «сейчас» — 18 сентября. Порог панели
    сравнивает ДАТЫ (`(created_at + 1 месяц)::date <= CURRENT_DATE`), поэтому 21
    сентября уже подходит: сравнение моментов («21-е 00:00 >= 21-е 09:00» ложно)
    увело бы дату на месяц вперёд — та самая ошибка, из-за которой у новых клиентов
    весь первый месяц называлось чужое число.
    """
    delta = service.get_traffic_reset_delta("MONTH_ROLLING", OUR_CREATED)
    assert NOW + delta == datetime(2026, 9, 21, 0, 10, tzinfo=timezone.utc)


async def test_wrapper_puts_the_panel_date_into_context_and_always_clears_it():
    seen = {}

    async def base(self, user, current_subscription, event, remna_user):
        seen["anchor"] = patch._PANEL_CREATED.get()
        raise RuntimeError("база упала посреди обработки")

    cls = type("Svc", (), {})
    cls._process_status = base
    # Собираем обёртку тем же кодом, что и правка, но вокруг своей «базы».
    original = cls._process_status

    async def wrapped(self, user, current_subscription, event, remna_user):
        token = patch._PANEL_CREATED.set(getattr(remna_user, "created_at", None))
        try:
            return await original(self, user, current_subscription, event, remna_user)
        finally:
            patch._PANEL_CREATED.reset(token)

    with pytest.raises(RuntimeError):
        await wrapped(cls(), None, None, "limited", SimpleNamespace(created_at=PANEL_CREATED))

    assert seen["anchor"] == PANEL_CREATED, "в контекст кладём дату ПАНЕЛИ"
    assert patch._PANEL_CREATED.get() is None, "без finally контекст утёк бы в чужой вебхук"


def test_real_wrapper_has_the_finally_and_sets_the_panel_date():
    source = inspect.getsource(patch.apply)
    assert "getattr(remna_user, \"created_at\", None)" in source
    assert "finally:" in source
    assert "_PANEL_CREATED.reset(token)" in source


def test_menu_card_uses_the_same_calculation():
    """Третье место: карточка подписки в меню бота (обёртка над геттером).

    Модуль правки берём ЧЕРЕЗ его цель: прямой импорт `overlay_patches.menu_dialog`
    запускает хук на `src.telegram.routers.menu.dialog`, который импортирует эту же
    правку, — и правка «не применяется» из-за кольца, созданного самим тестом.
    """
    import sys

    importlib.import_module("src.telegram.routers.menu.dialog")
    menu = sys.modules["overlay_patches.menu_dialog"]
    source = inspect.getsource(menu._fix_reset_time)
    body = source.split('"""')[2]
    assert "reset_moment_for_user" in body
    assert 'data["reset_time"]' in body
    # Тело базового геттера не копируем — только перезаписываем готовое поле.
    assert "get_traffic_reset_delta" not in body


def test_menu_helper_does_not_go_to_the_panel_when_it_is_not_needed():
    """Меню — горячий экран: панель трогаем только для MONTH_ROLLING без кеша."""
    source = inspect.getsource(extra.reset_moment_for_user)
    assert source.index("needs_anchor(strategy)") < source.index("get_user_by_uuid")
    assert "cached_created_at(uuid)" in source
