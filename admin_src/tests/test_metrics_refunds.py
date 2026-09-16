"""Плитка «Возвраты (30 дн)»: что считаем возвратом, по какой дате и чьи деньги.

ЧТО БЫЛО. В «Ключевых метриках» возвратов не было вовсе, а комментарий в
`compute_metrics` объяснял это тем, что «возвратов как статуса в БД нет (только
COMPLETED/CANCELED)». Это неправда: статус REFUNDED в enum `transaction_status`
есть, и вебхуки Platega (CHARGEBACKED) и Valutix (CHARGEBACK) его ставят. Отзыв
платежа проходил мимо статистики: из выручки он молча пропадал, а увидеть, что
деньги ушли обратно, было негде.

ЧТО ЗАПЕРТО ЗДЕСЬ.
  * «Когда вернули» — это `updated_at`: отдельного поля нет, а переход базы
    ставит его ровно в момент возврата. Тесты держат обе половины этого довода:
    бамп даты в самом переходе и ПОЛНЫЙ перечень тех, кто пишет в транзакции.
    Появится новый писатель (апгрейд базы, наша правка) — тест упадёт и заставит
    проверить, не переписывает ли он дату уже возвращённого платежа.
  * Окно — по дате возврата, не по дате оплаты: чарджбэк приходит через недели.
  * Учитываются только клиенты: та же отсечка проверочных оплат и учёток персонала,
    что у выручки, — и она проверяется у КАЖДОГО запроса метрик к транзакциям.
  * Валюты не складываются: 499 ₽ и 5 $ — это не «504».
  * Список шлюзов, умеющих сообщить о возврате, сверяется с исходниками базы:
    «0» при шлюзе, который о возвратах молчит, значит «не знаем», а не «не было».

Настоящая БД не нужна: сессия подделана. Сам запрос на Postgres гоняет
test_metrics_refunds_sql.py (opt-in).

Запуск — внутри образа бота:

  docker run --rm --env-file ci.env \
    -v /opt/remnashop/admin_src/tests:/tmp/tests:ro \
    remnashop-ci-local sh -c 'pip install -q --target /tmp/pylibs pytest pytest-asyncio \
      && PYTHONPATH=/tmp/pylibs python -m pytest /tmp/tests/test_metrics_refunds.py \
         -v --asyncio-mode=auto'
"""

import ast
import collections
import importlib
import inspect
import json
import re
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

statistics = importlib.import_module("src.web.endpoints.admin.statistics")
dao_module = importlib.import_module("src.infrastructure.database.dao.transaction")

from src.core.enums import TransactionStatus  # noqa: E402

# Корень исходников бота в образе (/opt/remnashop): src/ — база с нашим overlay
# поверх, overlay_patches/ — рантайм-правки.
ROOT = Path(importlib.import_module("src.core.enums").__file__).parents[2]

EXCLUDE_TEST_AND_STAFF = (
    "is_test = false",
    "NOT IN (SELECT su.id FROM users su WHERE su.role::text <> 'USER')",
)


# ── Подделки ──────────────────────────────────────────────────────────────────


class _AnyZero:
    """Строка «одиночного» запроса метрик (MRR, выручка, отток…): любое поле — 0."""

    def __getattr__(self, name: str) -> int:
        return 0


class FakeResult:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def first(self):
        return _AnyZero()

    def scalar(self):
        return 0

    def all(self) -> list:
        return self._rows


class FakeSession:
    """Сессия метрик: запоминает SQL и отдаёт заданные возвраты и активные шлюзы.

    Суммы — Decimal, как их отдаёт asyncpg для numeric: иначе тест не заметил бы
    потерянного `float()`.
    """

    def __init__(self, refunds=(), active=()) -> None:
        self.seen: list[str] = []
        self.refunds = list(refunds)
        self.active = list(active)

    async def execute(self, statement, *args, **kwargs) -> FakeResult:
        sql = str(statement)
        self.seen.append(sql)
        if "'REFUNDED'" in sql:
            rows = [
                SimpleNamespace(currency=c, cnt=n, amt=Decimal(str(a)))
                for c, n, a in self.refunds
            ]
        elif "payment_gateways" in sql:
            rows = [SimpleNamespace(gw=g) for g in self.active]
        else:
            rows = []
        return FakeResult(rows)


class SpySession:
    """Сессия, которая ничего не выполняет, а запоминает пришедший запрос."""

    def __init__(self) -> None:
        self.seen = []

    async def scalar(self, statement, *args, **kwargs):
        self.seen.append(statement)
        return None


def _python_files():
    """Все модули бота, кроме миграций: src/ (база + overlay) и overlay_patches/."""
    for base in (ROOT / "src", ROOT / "overlay_patches"):
        for path in sorted(base.rglob("*.py")):
            rel = path.relative_to(ROOT)
            # Миграции выполняются один раз при выкатке, а не во время работы.
            if any(part.startswith("migrations") for part in rel.parts):
                continue
            yield str(rel), ast.parse(path.read_text(encoding="utf-8"))


def _dao_calls(tree: ast.AST, methods: set[str]):
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in methods
            and "transaction_dao" in ast.unparse(node.func.value)
        ):
            yield node


def _arg(call: ast.Call, index: int, name: str):
    if len(call.args) > index:
        return call.args[index]
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


# ── Дата возврата ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refund_moment_is_written_by_base_transition() -> None:
    """Переход в REFUNDED сам ставит `updated_at` — на этом держится окно плитки.

    Уберёт база `onupdate` у колонки или перепишет переход мимо ORM — дата возврата
    перестанет существовать, и плитка начнёт показывать дату оплаты.
    """
    session = SpySession()
    dao = object.__new__(dao_module.TransactionDaoImpl)
    dao.session = session

    await dao.transition_status(
        uuid.uuid4(), TransactionStatus.REFUNDED, (TransactionStatus.COMPLETED,)
    )

    assert len(session.seen) == 1
    sql = str(session.seen[0].compile(dialect=postgresql.dialect()))
    set_part = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
    assert "updated_at=" in set_part, sql


def test_no_transition_out_of_refunded() -> None:
    """Из REFUNDED не уходят, а в REFUNDED попадают только из COMPLETED.

    Разреши кто-то переход ИЗ возврата — строка сменила бы статус, и возврат пропал
    бы из плитки вместе с датой.
    """
    refund_calls = []
    for rel, tree in _python_files():
        for call in _dao_calls(tree, {"transition_status"}):
            allowed = _arg(call, 2, "allowed_current")
            new_status = _arg(call, 1, "new_status")
            assert allowed is not None, f"{rel}:{call.lineno}: не разобрал allowed_current"
            assert "REFUNDED" not in ast.unparse(allowed), (
                f"{rel}:{call.lineno}: переход ИЗ REFUNDED — возврат исчезнет из плитки"
            )
            if new_status is not None and ast.unparse(new_status) == "TransactionStatus.REFUNDED":
                refund_calls.append((rel, call.lineno, ast.unparse(allowed)))

    assert len(refund_calls) == 1, refund_calls
    assert refund_calls[0][2] == "(TransactionStatus.COMPLETED,)", refund_calls


# Кто пишет в transactions: (файл, метод DAO) → число вызовов. Сверено с образом
# (база v0.8.2 + overlay) 16.09.2026. Почему ни один из них не сдвигает дату
# возврата:
#   payment.py — переходы CANCELED←PENDING, COMPLETED←PENDING/FAILED,
#     REFUNDED←COMPLETED и FAILED после сбоя выдачи (сразу за COMPLETED);
#   gateway_payment.py — PENDING←CANCELED и тот же FAILED в заменённом _handle_success;
#   payments.py — метод оплаты Platega (пишет, только если метод другой);
#   maintenance.py — отмена старых PENDING.
EXPECTED_WRITERS = {
    ("src/application/use_cases/gateways/commands/payment.py", "transition_status"): 3,
    ("src/application/use_cases/gateways/commands/payment.py", "update_status"): 1,
    ("overlay_patches/gateway_payment.py", "transition_status"): 1,
    ("overlay_patches/gateway_payment.py", "update_status"): 1,
    ("src/web/endpoints/payments.py", "update"): 1,
    ("src/application/use_cases/misc/commands/maintenance.py", "cancel_old"): 1,
}


def test_transaction_writers_are_known() -> None:
    """Перечень писателей в transactions закреплён — новый не пройдёт молча."""
    found: collections.Counter = collections.Counter()
    raw: list[str] = []
    for rel, tree in _python_files():
        for call in _dao_calls(tree, {"transition_status", "update_status", "update", "cancel_old"}):
            found[(rel, call.func.attr)] += 1
        for node in ast.walk(tree):
            # Сырой SQL мимо DAO.
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and re.search(r"UPDATE\s+\"?transactions\b", node.value, re.IGNORECASE)
            ):
                raw.append(f"{rel}:{node.lineno}")
            # Core-UPDATE по модели мимо DAO (в самом DAO он и есть переходы выше).
            if (
                rel != "src/infrastructure/database/dao/transaction.py"
                and isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "update"
                and node.args
                and ast.unparse(node.args[0]) == "Transaction"
            ):
                raw.append(f"{rel}:{node.lineno}")

    message = (
        "Появился/пропал писатель в transactions — проверь, не переписывает ли он "
        "REFUNDED-строки или их updated_at (дата возврата в плитке), и обнови перечень"
    )
    assert dict(found) == EXPECTED_WRITERS, f"{message}: {dict(found)}"
    assert raw == [], f"{message}: {raw}"


def test_reporting_gateways_match_base() -> None:
    """Список «сообщающих о возвратах» шлюзов — ровно те, чей код ставит REFUNDED.

    Берём исходник КЛАССА из файла, а не метод из памяти: overlay оборачивает
    `handle_webhook` некоторых шлюзов (ЮMoney), и обёртка спрятала бы код базы.
    """
    gateways = importlib.import_module("src.infrastructure.di.providers.payment_gateways")
    reporting = {
        gateway_type.value
        for gateway_type, cls in gateways.GATEWAY_MAP.items()
        if "TransactionStatus.REFUNDED" in inspect.getsource(cls)
    }
    assert reporting == statistics.REFUND_REPORTING_GATEWAYS


# ── Запросы ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_window_is_refund_date_not_payment_date() -> None:
    """Окно — по дню возврата. По дате оплаты чарджбэк старой покупки не попал бы."""
    session = FakeSession()
    await statistics.compute_refunds_30d(session)

    refund_sql = [s for s in session.seen if "'REFUNDED'" in s]
    assert len(refund_sql) == 1
    sql = refund_sql[0]
    assert "status::text = 'REFUNDED'" in sql
    assert "updated_at >= now() - interval '30 days'" in sql
    assert "created_at" not in sql


@pytest.mark.asyncio
async def test_every_transactions_query_excludes_test_and_staff() -> None:
    """Каждое обращение метрик к транзакциям несёт обе отсечки — и возвраты тоже.

    Считаем по числу вхождений: запрос с двумя подзапросами к транзакциям обязан
    отсечь чужие деньги в обоих.
    """
    session = FakeSession()
    await statistics.compute_metrics(session)

    touching = 0
    for sql in session.seen:
        refs = len(re.findall(r"\b(?:FROM|JOIN)\s+transactions\b", sql, re.IGNORECASE))
        if not refs:
            continue
        touching += 1
        for condition in EXCLUDE_TEST_AND_STAFF:
            assert sql.count(condition) >= refs, f"нет «{condition}» в:\n{sql}"
    # Предохранитель от пустого прогона: запросы к транзакциям действительно пойманы,
    # включая запрос возвратов.
    assert touching >= 6
    assert any("'REFUNDED'" in sql for sql in session.seen)


# ── Ответ ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_currencies_are_not_summed() -> None:
    """499 ₽ и 5 $ — две строки, а не «504»."""
    session = FakeSession(refunds=[("USD", 1, "5"), ("RUB", 1, "499")], active=["VALUTIX"])
    metrics = await statistics.compute_metrics(session)

    refunds = metrics["refunds"]
    assert refunds["by_currency"] == [
        {"currency": "RUB", "count": 1, "amount": 499.0},
        {"currency": "USD", "count": 1, "amount": 5.0},
    ]
    assert refunds["count_30d"] == 2
    assert "504" not in json.dumps(metrics)


@pytest.mark.asyncio
async def test_no_refunds_is_an_explicit_zero() -> None:
    """Возвратов нет — блок всё равно есть: пустой блок кабинет читает как «ноль»."""
    metrics = await statistics.compute_metrics(FakeSession(active=["VALUTIX"]))

    assert "refunds" in metrics
    assert metrics["refunds"]["count_30d"] == 0
    assert metrics["refunds"]["by_currency"] == []


@pytest.mark.asyncio
async def test_silent_gateways() -> None:
    """Кто из подключённых шлюзов о возвратах молчит — плитка обязана об этом сказать."""
    mixed = await statistics.compute_refunds_30d(
        FakeSession(active=["YOOMONEY", "VALUTIX", "TELEGRAM_STARS"])
    )
    assert mixed["reporting_gateways"] == ["VALUTIX"]
    assert mixed["silent_gateways"] == ["TELEGRAM_STARS", "YOOMONEY"]

    only_silent = await statistics.compute_refunds_30d(FakeSession(active=["YOOMONEY"]))
    assert only_silent["reporting_gateways"] == []
    assert only_silent["silent_gateways"] == ["YOOMONEY"]


@pytest.mark.asyncio
async def test_metrics_contract_kept() -> None:
    """Прежние поля на месте: старый кабинет и адаптер читают ответ как раньше."""
    metrics = await statistics.compute_metrics(FakeSession())

    for key in (
        "currency", "mrr", "mrr_subs", "arpu", "arppu", "revenue_30d", "active_users",
        "payers_30d", "conversion", "churn", "payments", "top_plans", "top_gateways",
        "refunds",
    ):
        assert key in metrics, key
    assert set(metrics["refunds"]) == {
        "count_30d", "by_currency", "reporting_gateways", "silent_gateways",
    }
