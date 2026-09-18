"""Админские ручки докупки трафика: настройки, сводка и отзыв прибавки.

ЧТО ЗАПИРАЕМ:
  * продажи ВЫКЛЮЧЕНЫ по умолчанию и остаются выключенными без цены или объёма —
    выкатка образа не должна сама начать брать деньги;
  * конфиг сохраняется в assets/extra_traffic.json и нормализуется (мусор не роняет
    страницу, отрицательная цена = «не продавать», а не «бесплатно»);
  * сохранение КОММИТИТ сессию вручную (память admin-endpoints-commit);
  * сводка считается только по клиентам: персонал и тестовые счета из денег
    исключены везде, и здесь граница та же;
  * PREVIEW-админу суммы замаскированы;
  * отзыв прибавки опускает лимит тем же правилом, что и крон («тариф + оставшиеся»),
    и спрашивает про возврат — по умолчанию НЕ возвращает (решение владельца Р-4).

Числа и идентификаторы синтетические.
"""

import importlib
import inspect
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

extra = importlib.import_module("src.infrastructure.services.overlay_extra_traffic")
admin = importlib.import_module("src.web.endpoints.admin.extra_traffic")
subscriptions = importlib.import_module("src.web.endpoints.admin.subscriptions")

from src.core.utils.converters import gb_to_bytes  # noqa: E402

from test_extra_traffic_buy import (  # noqa: E402
    NOW,
    PLAN_GB,
    SUB_ID,
    FakeRemnawave,
    FakeSession,
    Log,
)
from test_extra_traffic_tick import TickSession, a_grant  # noqa: E402

USER_ID = 7


class FakePlanDao:
    def __init__(self, plans: Any = None, fails: bool = False) -> None:
        self._plans = plans or []
        self.fails = fails

    async def get_all(self, only_active: bool = False) -> list:
        if self.fails:
            raise RuntimeError("витрина недоступна")
        return list(self._plans)


def a_plan(traffic_gb: int, devices: int, price: float) -> Any:
    duration = SimpleNamespace(days=30, get_price=lambda _c, p=price: Decimal(str(p)))
    return SimpleNamespace(traffic_limit=traffic_gb, device_limit=devices, durations=[duration])


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    path = tmp_path / "extra_traffic.json"
    monkeypatch.setattr(extra, "CONFIG_PATH", path)
    monkeypatch.setattr(extra, "ASSETS_DIR", tmp_path)
    return path


# ── настройки ───────────────────────────────────────────────────────────────


def test_no_config_file_means_no_sales(config_path):
    """Состояние сразу после выкатки: файла нет — продажи закрыты."""
    assert not config_path.exists()
    config = extra.load_config()
    assert config["enabled"] is False
    assert extra.effective_enabled(config) is False


def test_toggle_without_price_still_sells_nothing(config_path):
    saved = extra.save_config({"enabled": True, "price_rub": 0, "gb_per_purchase": 50})
    assert saved["price_rub"] is None
    assert extra.effective_enabled(saved) is False


def test_broken_config_falls_back_instead_of_breaking_payments(config_path):
    config_path.write_text("{это не json", "utf-8")
    config = extra.load_config()
    assert config["enabled"] is False


def test_values_are_clamped_not_trusted(config_path):
    saved = extra.save_config(
        {
            "enabled": True,
            "price_rub": 50,
            "gb_per_purchase": 999_999,
            "show_from_percent": 250,
            "min_hours_left": -5,
            "max_gb_per_window": 0,
        }
    )
    assert saved["gb_per_purchase"] == 10_000
    assert saved["show_from_percent"] == 99
    assert saved["min_hours_left"] == 0
    assert saved["max_gb_per_window"] == 1


async def test_saving_commits_the_session_by_hand(config_path):
    """Overlay-ручки живут вне UoW базы — commit здесь ручной, без исключений."""
    log = Log()
    session = FakeSession(log)
    body = admin.ExtraTrafficConfigRequest(enabled=True, price_rub=50, gb_per_purchase=50)

    result = await admin.put_extra_traffic_config.__dishka_orig_func__(
        body=body, _admin=SimpleNamespace(role=None), session=session
    )

    assert result["effective_enabled"] is True
    assert session.commits == 1
    # `@inject` dishka прячет нашу функцию — читаем исходную.
    assert "await session.commit()" in inspect.getsource(
        admin.put_extra_traffic_config.__dishka_orig_func__
    )


# ── сводка ──────────────────────────────────────────────────────────────────


def test_summary_counts_clients_only():
    """Персонал и тестовые счета из денег исключены везде — здесь граница та же."""
    assert "u.role::text = 'USER'" in admin.SUMMARY_SQL
    assert "JOIN users u ON u.id = o.user_id" in admin.SUMMARY_SQL


def test_summary_separates_applied_from_rejected_and_stuck():
    assert "status = 'applied'" in admin.SUMMARY_SQL
    assert "status = 'rejected'" in admin.SUMMARY_SQL
    # «Оплачено, но не применено» — это то, за чем владелец следит первые сутки.
    assert "status = 'credited'" in admin.SUMMARY_SQL


def test_strategies_are_read_only_for_live_limited_subscriptions():
    assert "s.status::text = 'ACTIVE'" in admin.STRATEGIES_SQL
    assert "s.traffic_limit > 0" in admin.STRATEGIES_SQL


async def test_price_hint_is_a_step_between_neighbouring_plans():
    hint = await admin._price_hint(FakePlanDao([a_plan(200, 2, 250), a_plan(300, 3, 350)]))
    assert hint == [{"from_gb": 200, "to_gb": 300, "diff_30d_rub": 100.0, "device_diff": 1}]


async def test_price_hint_never_breaks_the_settings_page():
    assert await admin._price_hint(FakePlanDao(fails=True)) == []
    # Безлимитные тарифы в шаг не входят: с бесконечностью шаг не считается.
    assert await admin._price_hint(FakePlanDao([a_plan(0, 2, 250), a_plan(300, 3, 350)])) == []


# ── отзыв прибавки ──────────────────────────────────────────────────────────


class RevokeSession(TickSession):
    """TickSession плюс чтение суммы заказов прибавки — её возвращают при отзыве."""

    def __init__(self, log: Log, order_amount: Decimal = Decimal(50), **kw) -> None:
        super().__init__(log, **kw)
        self.order_amount = order_amount
        self.credited = Decimal(0)

    async def execute(self, stmt: Any, params: Any = None):
        sql = " ".join(str(stmt).split())
        if "coalesce(sum(amount), 0) FROM extra_traffic_orders" in sql:
            self.log.append(("refund_amount", None))
            return await super().execute("noop", None) or None
        if "cabinet_balance = cabinet_balance + :a" in sql:
            self.credited += Decimal(str(params["a"]))
            self.log.append(("credit", str(params["a"])))
            return await super().execute("noop", None)
        if "SET status = 'revoked'" in sql:
            self.log.append(("revoke", dict(params)))
            return await super().execute("noop", None)
        return await super().execute(stmt, params)


class FakeTransactionDaoSpy:
    def __init__(self, log: Log) -> None:
        self.log = log

    async def create(self, transaction: Any) -> Any:
        self.log.append(
            (
                "refund_transaction",
                {
                    "status": transaction.status.value,
                    "display": transaction.gateway_display_name,
                    "amount": transaction.pricing.final_amount,
                },
            )
        )
        return transaction


async def call_revoke(session, log, *, refund: bool):
    body = subscriptions.RevokeExtraTrafficRequest(refund=refund)
    return await subscriptions.revoke_extra_traffic.__dishka_orig_func__(
        user_id=USER_ID,
        grant_id=1,
        body=body,
        admin=SimpleNamespace(role=None),
        session=session,
        remnawave=FakeRemnawave(log),
        transaction_dao=FakeTransactionDaoSpy(log),
    )


async def test_revoke_lowers_the_limit_by_the_same_rule_as_the_cron():
    log = Log()
    session = RevokeSession(log, traffic_limit=PLAN_GB + 50, grants=(a_grant(),))

    result = await call_revoke(session, log, refund=False)

    assert result["traffic_limit_gb"] == PLAN_GB
    assert log[log.index_of("panel_patch")][1]["trafficLimitBytes"] == gb_to_bytes(PLAN_GB)
    assert result["refunded"] is None
    assert "credit" not in log.names(), "по умолчанию деньги НЕ возвращаем"
    assert session.commits == 1


async def test_revoke_keeps_manual_generosity():
    """Лимит 500 при тарифе 300 — отзыв снимает только объём прибавки."""
    log = Log()
    session = RevokeSession(log, traffic_limit=500, grants=(a_grant(),))
    result = await call_revoke(session, log, refund=False)
    assert result["traffic_limit_gb"] == 450


async def test_revoke_with_refund_writes_a_transaction_not_just_balance():
    """Без строки возврата отчёты показывали бы доход, которого уже нет."""
    log = Log()
    session = RevokeSession(log, traffic_limit=PLAN_GB + 50, grants=(a_grant(),))
    session.refund_amount = Decimal(50)

    # Подделка возвращает сумму заказов через общий путь FakeSession: подменяем ответ.
    async def execute(stmt, params=None, _orig=session.execute):
        sql = " ".join(str(stmt).split())
        if "coalesce(sum(amount), 0) FROM extra_traffic_orders" in sql:
            log.append(("refund_amount", None))

            class _R:
                def scalar(self_inner):
                    return Decimal(50)

                def first(self_inner):
                    return None

                def all(self_inner):
                    return []

            return _R()
        return await _orig(stmt, params)

    session.execute = execute  # type: ignore[method-assign]

    result = await call_revoke(session, log, refund=True)

    assert result["refunded"] == 50.0
    assert log[log.index_of("credit")][1] == "50"
    row = log[log.index_of("refund_transaction")][1]
    assert row["status"] == "REFUNDED"
    assert row["display"] == "Возврат · трафик"


async def test_revoke_of_a_missing_grant_is_404_not_a_silent_success():
    from fastapi import HTTPException

    log = Log()
    session = RevokeSession(log, traffic_limit=PLAN_GB, grants=())
    with pytest.raises(HTTPException) as exc:
        await call_revoke(session, log, refund=False)
    assert exc.value.status_code == 404


async def test_revoke_rolls_back_when_the_panel_refuses():
    from fastapi import HTTPException

    log = Log()
    session = RevokeSession(log, traffic_limit=PLAN_GB + 50, grants=(a_grant(),))
    remnawave = FakeRemnawave(log, fail=True)
    body = subscriptions.RevokeExtraTrafficRequest(refund=False)
    with pytest.raises(HTTPException) as exc:
        await subscriptions.revoke_extra_traffic.__dishka_orig_func__(
            user_id=USER_ID,
            grant_id=1,
            body=body,
            admin=SimpleNamespace(role=None),
            session=session,
            remnawave=remnawave,
            transaction_dao=FakeTransactionDaoSpy(log),
        )
    assert exc.value.status_code == 502
    assert session.commits == 0


def test_revoke_defaults_to_no_refund_in_the_request_model():
    """Решение владельца Р-4: кнопка спрашивает, по умолчанию не возвращаем."""
    assert subscriptions.RevokeExtraTrafficRequest().refund is False
    assert extra.DEFAULT_CONFIG["refund_on_revoke"] is False


def test_user_card_masks_money_for_preview_admin():
    source = inspect.getsource(subscriptions.user_extra_traffic.__dishka_orig_func__)
    assert "is_readonly_admin(admin)" in source
    assert "None if hide_money else float" in source
