"""Витрина сообщает кабинету, что сгорит при смене тарифа.

ЧТО СЛУЧАЛОСЬ. Смена тарифа (CHANGE) у нас — срок с нуля: ветка «CHANGE или
триал» в базе зовёт `update_user(plan=…)`, а `_build_update_request` по тарифу
ставит `expire_at = сейчас + длительность`. Бот об этом предупреждает («без
пересчета оставшегося срока»), кабинет молчал. С блоком «Нужно больше
устройств?» кабинет сам начал вести людей к смене тарифа — и промолчать про
сгорающий месяц значило бы продать ему потерю денег. У бессрочной подписки
CHANGE стоит на всех тарифах: «навсегда» превращалось бы в 30 дней.

ЧТО ЗАПИРАЕМ.
  * `plan_change_terms` считает остаток теми же полными сутками, что кабинет,
    а на паузе берёт сохранённый остаток — `expire_at` там стоит на месте;
  * `/offers` отдаёт поля именно через оверлей-схему: FastAPI режет ответ по
    `response_model`, и с базовой схемой предупреждения молча пропали бы;
  * сбой чтения паузы витрину не роняет (это единственный путь к покупке),
    а «не знаем» не выдаётся за «паузы нет»;
  * СИГНАЛИЗАЦИЯ: база до сих пор сжигает остаток. Начнёт переносить — тест
    упадёт, и флаг `plan_change_keeps_days` надо перевернуть, иначе кабинет
    будет пугать людей потерей, которой нет.

Запуск — внутри образа бота, как остальные тесты рядом (см. ci.yml).
"""

import importlib
import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Optional

import pytest

# Порядок важен: сначала раздел публичных ручек — при его импорте overlay
# подставляет наш роутер. Импорт модуля правки первым ловил его недостроенным
# (циклический импорт через public/__init__.py).
importlib.import_module("src.web.endpoints.public")
offers_mod = importlib.import_module("overlay_patches.public_subscription")

from src.core.enums import SubscriptionStatus  # noqa: E402

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
DAY = 86400


def terms(**over: Any) -> dict:
    args: dict[str, Any] = {
        "expire_at": NOW + timedelta(days=30),
        "is_unlimited": False,
        "is_trial": False,
        "frozen_seconds": None,
        "now": NOW,
    }
    args.update(over)
    return offers_mod.plan_change_terms(**args)


# ── plan_change_terms ───────────────────────────────────────────────────────

def test_partial_day_is_not_counted():
    """40 дн. 23 ч — это 40: кабинет обещает «осталось N дн.» полными сутками."""
    got = terms(expire_at=NOW + timedelta(days=40, hours=23))
    assert got["current_days_left"] == 40
    assert got["current_frozen"] is False
    assert got["current_is_unlimited"] is False


def test_expired_subscription_loses_nothing():
    assert terms(expire_at=NOW - timedelta(days=3))["current_days_left"] == 0


def test_unlimited_has_no_day_count_but_is_flagged():
    got = terms(expire_at=NOW.replace(year=2099), is_unlimited=True)
    assert got["current_days_left"] is None
    assert got["current_is_unlimited"] is True


def test_frozen_remaining_wins_over_stale_expire_at():
    """На паузе срок в базе стоит (и уже в прошлом), а остаток лежит отдельно."""
    got = terms(expire_at=NOW - timedelta(days=5), frozen_seconds=int(30.5 * DAY))
    assert got["current_days_left"] == 30
    assert got["current_frozen"] is True


def test_trial_flag_is_passed_through():
    assert terms(is_trial=True)["current_is_trial"] is True


# ── маршрут и схема ─────────────────────────────────────────────────────────

def test_offers_route_serializes_overlay_fields():
    route = next(
        r for r in offers_mod.router.routes
        if getattr(r, "path", None) == "/subscription/offers" and "GET" in r.methods
    )
    assert route.response_model is offers_mod.SubscriptionOffersOverlayResponse

    empty = offers_mod.SubscriptionOffersOverlayResponse(
        gateways=[], plans=[], has_current_subscription=False,
    ).model_dump()
    assert empty["plan_change_keeps_days"] is False
    for key in ("current_days_left", "current_is_trial", "current_is_unlimited", "current_frozen"):
        assert key in empty and empty[key] is None


# ── ручка целиком, на подделках ─────────────────────────────────────────────

class FakeResult:
    def __init__(self, row: Optional[tuple]) -> None:
        self._row = row

    def first(self) -> Optional[tuple]:
        return self._row


class FakeSession:
    def __init__(self, row: Optional[tuple] = None, fail: bool = False) -> None:
        self.row = row
        self.fail = fail
        self.rollbacks = 0
        self.commits = 0
        self.sql: list[str] = []

    async def execute(self, stmt: Any, params: Any = None) -> FakeResult:
        self.sql.append(str(stmt))
        if self.fail:
            raise RuntimeError("relation subscription_freezes does not exist")
        return FakeResult(self.row)

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def commit(self) -> None:
        self.commits += 1


class FakeSubscriptionDao:
    def __init__(self, sub: Any) -> None:
        self.sub = sub

    async def get_current(self, user_id: int) -> Any:
        return self.sub


class NoGateways:
    async def get_active(self) -> list:
        return []


class NoPlans:
    async def system(self, *args: Any) -> list:
        return []


def subscription(days: float = 29.5, trial: bool = False) -> Any:
    expire = datetime.now(timezone.utc) + timedelta(days=days)
    return SimpleNamespace(
        expire_at=expire,
        is_unlimited=False,
        is_trial=trial,
        current_status=SubscriptionStatus.ACTIVE,
        plan_snapshot=None,
    )


async def call_offers(session: FakeSession, sub: Any) -> Any:
    raw = offers_mod.get_subscription_offers.__dishka_orig_func__
    return await raw(
        user=SimpleNamespace(id=7),
        session=session,
        subscription_dao=FakeSubscriptionDao(sub),
        payment_gateway_dao=NoGateways(),
        pricing_service=None,
        get_available_plans=NoPlans(),
        match_plan=NoPlans(),
    )


async def test_offers_report_days_left_for_active_subscription():
    session = FakeSession(row=None)
    resp = await call_offers(session, subscription(days=29.5))
    assert resp.plan_change_keeps_days is False
    assert resp.current_days_left == 29
    assert resp.current_frozen is False
    assert resp.current_is_trial is False
    # Только чтение: витрина ничего не коммитит.
    assert session.commits == 0
    assert any("subscription_freezes" in q for q in session.sql)


async def test_offers_use_frozen_remaining():
    session = FakeSession(row=(12 * DAY + 100,))
    resp = await call_offers(session, subscription(days=-2))
    assert resp.current_days_left == 12
    assert resp.current_frozen is True


async def test_freeze_lookup_failure_keeps_showcase_and_says_unknown():
    session = FakeSession(fail=True)
    resp = await call_offers(session, subscription(days=10.2))
    assert session.rollbacks == 1
    assert resp.current_days_left == 10
    assert resp.current_frozen is None


async def test_offers_without_subscription_leave_terms_empty():
    session = FakeSession()
    resp = await call_offers(session, None)
    assert resp.has_current_subscription is False
    assert resp.plan_change_keeps_days is False
    assert resp.current_days_left is None
    assert resp.current_frozen is None
    # Без подписки паузу и не спрашиваем.
    assert session.sql == []


# ── сигнализация: база всё ещё сжигает остаток ─────────────────────────────

def test_base_still_restarts_term_on_plan_change():
    from src.application.use_cases.subscription.commands.purchase import PurchaseSubscription
    from src.infrastructure.services.remnawave import RemnawaveImpl

    build = inspect.getsource(RemnawaveImpl._build_update_request)
    assert "days_to_datetime(plan.duration)" in build, (
        "база больше не ставит срок «с нуля» по тарифу — проверьте перенос остатка "
        "и переверните plan_change_keeps_days в public_subscription.py"
    )

    source = inspect.getsource(PurchaseSubscription._execute)
    marker = "elif purchase_type == PurchaseType.CHANGE"
    assert marker in source, "ветка CHANGE в PurchaseSubscription перестроена — сверить перенос остатка"
    change_branch = source.split(marker, 1)[1].split("\n            else:", 1)[0]
    assert "update_user(" in change_branch and "plan=plan" in change_branch, (
        "CHANGE больше не отдаёт панели тариф — возможно, остаток теперь переносится; "
        "сверить и перевернуть plan_change_keeps_days"
    )
