"""Выдача и продление подписки из админки поверх «Бедолаги».

ЦЕНА ОШИБКИ ЗДЕСЬ — ЧУЖАЯ ПОДПИСКА. Эта ручка решает, ДОБАВИТЬ человеку подписку
или ПЕРЕВЕСТИ существующую на другой тариф. Раньше она безусловно переводила:
администратор хотел дать вторую, а отнимал первую. Тестов на неё не было ни одного,
и поймал это только сторонний разбор.

Второе, что здесь заперто, — закрепление подписки (`subscription_id`). У их
синхронизации с панелью оно включает «закреплённый» режим, который при пустом
`remnawave_id` молча выходит, не тронув панель: админка отвечала «успех», а до
панели не доезжало. Закреплять надо ровно там, где наш выбор расходится с их
собственным, и нигде больше.

Запуск — внутри образа адаптера:

  docker build -t remnashop-adapter-ci adapter
  docker run --rm -v "$PWD/adapter/tests:/tmp/tests:ro" remnashop-adapter-ci \
    sh -c 'pip install -q --target /tmp/pylibs pytest pytest-asyncio &&
           PYTHONPATH=/tmp/pylibs python -m pytest /tmp/tests -q --asyncio-mode=auto'
"""

import asyncio
import importlib
from typing import Any

import httpx
import pytest

admin_users = importlib.import_module("admin_users")
compose = importlib.import_module("compose")


def sub(sid: int, tariff: int, end: str, active: bool = True) -> dict[str, Any]:
    return {"id": sid, "tariff_id": tariff, "end_date": end, "is_active": active}


class FakeCtx:
    """Контекст ровно в том объёме, в каком его читает `grant`."""

    def __init__(self, subs: list[dict], body: dict, fail_create: int | None = None) -> None:
        self._subs = subs
        self._body = body
        self.fail_create = fail_create
        self.calls: list[dict] = []
        self.params = {"id": "42"}

    def param(self, name: str) -> str:
        return self.params[name]

    def payload(self) -> dict:
        return self._body

    async def call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        if method == "GET":
            return httpx.Response(200, json={"subscriptions": self._subs})
        body = kw.get("json") or {}
        self.calls.append(body)
        if body.get("action") == "create" and self.fail_create:
            return httpx.Response(self.fail_create, json={"detail": "User already has a subscription"})
        return httpx.Response(200, json={"subscription": {"id": 1, "tariff_id": body.get("tariff_id", 7)}})

    async def json(self, path: str, default: Any = None, **kw: Any) -> Any:
        return default


def run(ctx):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        admin_users.grant(ctx)
    )


def actions(ctx) -> list[str]:
    return [c.get("action") for c in ctx.calls]


BUY = {"plan_id": 7, "days": 30}


def test_no_subscription_creates_one():
    ctx = FakeCtx([], BUY)
    out = run(ctx)
    assert actions(ctx) == ["create"]
    assert out["action"] == "created"


def test_multi_tariff_adds_a_second_subscription():
    """Главная починка: другой тариф — ДОБАВИТЬ, а не перевести."""
    ctx = FakeCtx([sub(1, 3, "2026-12-01")], BUY)
    out = run(ctx)
    assert actions(ctx) == ["create"], "перевод существующей подписки недопустим"
    assert out["action"] == "created"


def test_single_tariff_backend_falls_back_to_change_tariff():
    """Их обычный режим отвечает на вторую подписку 400 — тогда переводим, как было."""
    ctx = FakeCtx([sub(1, 3, "2026-12-01")], BUY, fail_create=400)
    out = run(ctx)
    assert actions(ctx) == ["create", "change_tariff", "extend"]
    assert out["action"] == "extended"


def test_existing_subscription_on_the_same_tariff_is_extended():
    ctx = FakeCtx([sub(1, 7, "2026-12-01")], BUY)
    out = run(ctx)
    assert actions(ctx) == ["extend"], "на свой же тариф второй подписки не заводим"
    assert out["action"] == "extended"


def test_same_tariff_found_among_other_subscriptions():
    """Нужная подписка может быть НЕ «текущей»: у неё срок ближе.

    Раньше целились только в «текущую» (самый дальний срок), и выдача на тариф
    соседней строки упиралась в их 409 — то есть не делала ничего.
    """
    ctx = FakeCtx([sub(1, 3, "2027-01-01"), sub(2, 7, "2026-10-01")], BUY)
    out = run(ctx)
    assert actions(ctx) == ["extend"]
    assert ctx.calls[0].get("subscription_id") == 2, "продлить надо именно подписку нужного тарифа"
    assert out["action"] == "extended"


def test_pin_only_when_our_choice_differs_from_theirs():
    """Закрепляем подписку ТОЛЬКО при расхождении выбора.

    Их выбор — первая активная, наш — активная с самым дальним сроком. Совпали —
    `subscription_id` слать нельзя: он включает их «закреплённый» режим, который
    при пустом `remnawave_id` молча не доезжает до панели.
    """
    # Совпадение: одна подписка — их выбор и наш один и тот же.
    ctx = FakeCtx([sub(1, 7, "2026-12-01")], BUY)
    run(ctx)
    assert "subscription_id" not in ctx.calls[0]

    # Совпадение при двух подписках: нужный тариф лежит на ПЕРВОЙ активной — это и
    # есть их выбор, закреплять нечего даже при нескольких подписках.
    ctx = FakeCtx([sub(1, 7, "2026-10-01"), sub(2, 7, "2027-01-01")], BUY)
    run(ctx)
    assert "subscription_id" not in ctx.calls[0]

    # НАСТОЯЩЕЕ расхождение: нужного тарифа нет ни на одной подписке, поэтому
    # целимся в «текущую» (срок дальше, id 2), а бот без адреса взял бы первую
    # активную (id 1) — то есть продлил бы ЧУЖУЮ подписку.
    ctx = FakeCtx(
        [sub(1, 3, "2026-10-01"), sub(2, 5, "2027-01-01")], BUY, fail_create=400
    )
    run(ctx)
    change = next(c for c in ctx.calls if c["action"] == "change_tariff")
    extend = next(c for c in ctx.calls if c["action"] == "extend")
    assert change.get("subscription_id") == 2
    assert extend.get("subscription_id") == 2


def test_inactive_subscriptions_do_not_win_over_active_one():
    """Мёртвая строка не должна победить живую при выборе «текущей».

    Прежняя проверка была тавтологией: `get("subscription_id", 2) == 2` проходила и
    когда ключа нет вовсе. Проверяем то, что важно: продлеваем ЖИВУЮ подписку.
    """
    ctx = FakeCtx([sub(1, 7, "2027-01-01", active=False), sub(2, 7, "2026-10-01")], BUY)
    run(ctx)
    assert actions(ctx) == ["extend"]
    # Их выбор — первая активная (id 2), наш — она же, значит адрес не нужен.
    assert "subscription_id" not in ctx.calls[0]


def test_trial_flag_only_on_creation():
    """У существующей подписки их API этот флаг не меняет — не притворяемся."""
    ctx = FakeCtx([sub(1, 7, "2026-12-01")], {**BUY, "is_trial": True})
    run(ctx)
    assert "is_trial" not in ctx.calls[0]

    ctx = FakeCtx([], {**BUY, "is_trial": True})
    run(ctx)
    assert ctx.calls[0].get("is_trial") is True


def test_expired_subscription_on_that_tariff_is_not_resurrected():
    """Мёртвая строка нужного тарифа НЕ должна оживать вместо новой выдачи.

    Это поломка, которую я внёс сам, расширив поиск «подписки того же тарифа» на
    все статусы: «Выдать подписку» продлевало бы давно истёкшую, а действующая
    оставалась бы нетронутой. Ищем только среди живых.
    """
    ctx = FakeCtx(
        [sub(1, 3, "2027-01-01"), sub(2, 7, "2025-01-01", active=False)], BUY
    )
    out = run(ctx)
    assert actions(ctx) == ["create"], "истёкшую подписку не продлеваем, выдаём новую"
    assert out["action"] == "created"


def test_expired_subscription_on_that_tariff_with_single_tariff_backend():
    """Тот же набор, но их бэкенд без мультитарифа: откат на смену тарифа."""
    ctx = FakeCtx(
        [sub(1, 3, "2027-01-01"), sub(2, 7, "2025-01-01", active=False)],
        BUY,
        fail_create=400,
    )
    out = run(ctx)
    assert actions(ctx) == ["create", "change_tariff", "extend"]
    # Переводим ЖИВУЮ подписку, а не мёртвую.
    assert ctx.calls[1].get("tariff_id") == 7
    assert out["action"] == "extended"


def run_extend(ctx):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        admin_users.extend(ctx)
    )


def test_extend_does_not_pin_when_choices_coincide():
    """Две подписки, но их выбор и наш совпадают — закреплять нельзя.

    Раньше `extend` решал по количеству подписок, и закрепление включалось ровно
    там, где оно ломает синхронизацию с панелью: их «закреплённый» режим при пустом
    remnawave_id молча выходит, не тронув панель.
    """
    ctx = FakeCtx([sub(1, 7, "2027-01-01"), sub(2, 3, "2026-10-01")], {"days": 30})
    run_extend(ctx)
    assert ctx.calls[0]["action"] == "extend"
    assert "subscription_id" not in ctx.calls[0]


def test_extend_pins_when_choices_differ():
    """Первая активная — не та, у которой срок дальше: адрес обязателен."""
    ctx = FakeCtx([sub(1, 3, "2026-10-01"), sub(2, 5, "2027-01-01")], {"days": 30})
    run_extend(ctx)
    assert ctx.calls[0].get("subscription_id") == 2


def test_extend_single_subscription_never_pins():
    ctx = FakeCtx([sub(1, 7, "2026-12-01")], {"days": 30})
    run_extend(ctx)
    assert "subscription_id" not in ctx.calls[0]
