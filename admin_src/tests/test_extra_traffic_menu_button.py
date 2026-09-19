"""Кнопка «Докупить трафик» в главном меню бота.

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ. Кнопка живёт в геттере ГЛАВНОГО окна — того, которое
открывают все и всегда. Цена ошибки здесь — не «нет кнопки», а «нет меню»: ровно
так 18.09 легло окно «Устройства». Поэтому проверяем не только когда кнопка есть,
но и что любая беда внутри оставляет меню живым.

ВТОРОЕ, ЧТО ЗАПЕРТО, — ДЕНЬГИ НА ЗАПРОСАХ. В панель ходим только за тем, кому
вообще можно продать: безлимитному, пробнику и истёкшему запрос не делается вовсе.
Без этого каждое открытие меню каждым человеком стоило бы запроса в панель.

Порог показа тот же, что в кабинете (show_from_percent): кнопка «купи ещё» у того,
кто потратил 3 ГБ из 200, — навязывание, а не помощь.

Данные синтетические.
"""

import importlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

extra = importlib.import_module("src.infrastructure.services.overlay_extra_traffic")

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
PANEL_CREATED = datetime(2026, 1, 7, 15, 30, tzinfo=timezone.utc)
GB = 1024**3

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


def panel(used_gb=250, limit_gb=300, status="ACTIVE"):
    return extra.PanelView(
        limit_bytes=limit_gb * GB,
        used_bytes=int(used_gb * GB),
        status=status,
        created_at=PANEL_CREATED,
    )


@pytest.fixture
def menu(monkeypatch):
    """Модуль правки с подменёнными походами наружу: база, панель, конфиг, часы."""
    module = importlib.import_module("overlay_patches.menu_dialog")
    monkeypatch.setattr(extra, "load_config", lambda: dict(CFG))
    monkeypatch.setattr(extra, "now_utc", lambda: NOW)
    return module


async def call(menu, *, st, panel_view, data=None, calls=None):
    async def lock_state(session, user_id, lock=False):
        assert lock is False, "меню читает, а не покупает: строку блокировать нельзя"
        return st

    async def read_panel(remnawave, state_):
        if calls is not None:
            calls.append(state_.user_id)
        return panel_view

    extra.lock_state = lock_state  # noqa: B010 — подмена на время теста, см. fixture
    extra.read_panel = read_panel  # noqa: B010
    payload = {"has_subscription": True, "web_cabinet_url": "https://cabinet.example.test"}
    payload.update(data or {})
    await menu._extra_traffic_button(payload, object(), object(), {"user": SimpleNamespace(id=7)})
    return payload


@pytest.fixture(autouse=True)
def restore_service():
    """Возвращаем настоящие функции: соседние файлы гоняются в том же процессе."""
    real_lock, real_read = extra.lock_state, extra.read_panel
    yield
    extra.lock_state, extra.read_panel = real_lock, real_read


async def test_button_appears_when_traffic_is_running_out(menu):
    data = await call(menu, st=state(), panel_view=panel(used_gb=250))
    assert data["extra_traffic_button"] is True
    # Ведём СРАЗУ на оплату, а не на Главную: иначе человек, нажавший «докупить»,
    # ищет карточку под ползунком расхода (жалоба владельца 19.09). Адрес тот же,
    # что в сообщении «трафик закончился», — он один на весь проект.
    assert data["extra_traffic_url"] == "https://cabinet.example.test/billing?extra_traffic=1"
    assert data["extra_traffic_url"] == extra.cabinet_offer_url(
        type("C", (), {"web_cabinet_url": "https://cabinet.example.test"})
    )
    assert "50 ГБ" in data["extra_traffic_text"]
    assert "50 ₽" in data["extra_traffic_text"]


async def test_no_button_until_the_threshold(menu):
    """70% — порог показа: раньше кнопки нет."""
    data = await call(menu, st=state(), panel_view=panel(used_gb=100))
    assert data["extra_traffic_button"] is False
    assert data["extra_traffic_text"] == ""


async def test_limited_sees_the_button_whatever_the_percent(menu):
    """LIMITED — это и есть покупатель: трафик у него уже кончился."""
    data = await call(menu, st=state(sub_status="LIMITED"), panel_view=panel(used_gb=300, status="LIMITED"))
    assert data["extra_traffic_button"] is True


async def test_unlimited_does_not_touch_the_panel(menu):
    """Безлимиту добавлять нечего — и запрос в панель за ним делать незачем."""
    calls = []
    data = await call(menu, st=state(traffic_limit=0, plan_traffic_limit=0), panel_view=panel(), calls=calls)
    assert data["extra_traffic_button"] is False
    assert calls == [], "в панель сходили за тем, кому и так нельзя продать"


async def test_trial_does_not_touch_the_panel(menu):
    calls = []
    data = await call(menu, st=state(is_trial=True), panel_view=panel(), calls=calls)
    assert data["extra_traffic_button"] is False
    assert calls == []


async def test_sales_switch_off_hides_the_button(menu, monkeypatch):
    monkeypatch.setattr(extra, "load_config", lambda: dict(CFG, enabled=False))
    data = await call(menu, st=state(), panel_view=panel(used_gb=290))
    assert data["extra_traffic_button"] is False


async def test_silent_panel_hides_the_button(menu):
    """Панель молчит — считать долю не от чего, кнопки нет, меню живо."""
    data = await call(menu, st=state(), panel_view=None)
    assert data["extra_traffic_button"] is False


async def test_no_subscription_no_button(menu):
    data = await call(menu, st=state(), panel_view=panel(), data={"has_subscription": False})
    assert data["extra_traffic_button"] is False


async def test_no_cabinet_address_no_button(menu):
    """Кнопка ведёт в кабинет: без адреса вести некуда."""
    data = await call(menu, st=state(), panel_view=panel(used_gb=290), data={"web_cabinet_url": ""})
    assert data["extra_traffic_button"] is False


async def test_breakage_leaves_the_menu_alive(menu, monkeypatch):
    """Главное окно открывают все и всегда: падение здесь = бот без меню."""

    def boom():
        raise RuntimeError("конфиг не прочитался")

    monkeypatch.setattr(extra, "load_config", boom)
    data = await call(menu, st=state(), panel_view=panel())
    assert data["extra_traffic_button"] is False
    assert data["has_subscription"] is True


async def test_price_reads_as_a_price(menu, monkeypatch):
    """50, а не 50.00: цену человек читает глазами."""
    monkeypatch.setattr(extra, "load_config", lambda: dict(CFG, price_rub=Decimal("50.00"), gb_per_purchase=100))
    data = await call(menu, st=state(), panel_view=panel(used_gb=290))
    assert data["extra_traffic_text"] == "➕ 100 ГБ к текущему лимиту · 50 ₽"
