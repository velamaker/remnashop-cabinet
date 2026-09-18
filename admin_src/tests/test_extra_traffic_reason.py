"""Почему докупки трафика нет: причина обязана быть настоящей.

18.09 владелец открыл кабинет на своём безлимитном тарифе и увидел «сервер не
ответил». Панель была жива: за тем, кому и так нельзя продать (безлимит, пробник,
истёкший), ручка в панель НЕ ходит вовсе — а ответ всё равно говорил про панель.
Человек читает это как аварию и идёт её чинить.

Причину знаем из своей базы; за панелью остаётся только настоящее молчание.

Данные синтетические.
"""

import importlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal

extra = importlib.import_module("src.infrastructure.services.overlay_extra_traffic")

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)

CFG = {
    "enabled": True,
    "gb_per_purchase": 50,
    "price_rub": 50,
    "min_amount_rub": 10,
    "show_from_percent": 70,
    "min_hours_left": 2,
    "max_gb_per_window": 1000,
}


def state(**kw):
    base = dict(
        user_id=7,
        balance=Decimal("500"),
        sub_id=100,
        sub_status="ACTIVE",
        is_trial=False,
        expire_at=NOW + timedelta(days=40),
        traffic_limit=300,
        strategy="MONTH_ROLLING",
        plan_id=7,
        plan_traffic_limit=300,
        remna_uuid="11111111-1111-1111-1111-111111111111",
        sub_updated_at=NOW - timedelta(days=1),
        frozen_at=None,
        reserve_expire_at=None,
        grants=(),
    )
    base.update(kw)
    return extra.UserState(**base)


def test_reason_for_unlimited_is_not_a_panel_failure():
    """«Сервер не ответил» — это про сбой, а не про безлимит.

    Ручка кабинета НЕ ходит в панель за тем, кому и так нельзя продать (безлимит,
    пробник, истёкший). Раньше в ответе для них стояло `panel_unavailable`, и
    владелец на своём безлимитном тарифе прочитал ровно это — «сервер не ответил», —
    решив, что сломана панель. Причина у нас есть и без панели: берём её из базы.
    """
    endpoint = importlib.import_module("src.web.endpoints.public.extra_traffic")
    data = endpoint._payload(state(traffic_limit=0, plan_traffic_limit=0), None, dict(CFG), NOW)
    assert data["offer"]["available"] is False
    assert data["offer"]["reason"] == "unlimited_traffic"
    assert extra.reason_ru("unlimited_traffic") == "у подписки безлимитный трафик"


def test_panel_failure_still_says_panel_failure():
    """А вот когда продать МОЖНО и панель молчит — причина честно про панель."""
    endpoint = importlib.import_module("src.web.endpoints.public.extra_traffic")
    data = endpoint._payload(state(), None, dict(CFG), NOW)
    assert data["offer"] == {"available": False, "reason": "panel_unavailable", "until": None}
