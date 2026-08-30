"""Опоздавший платёж: счёт отменён кроном, но человек всё равно заплатил.

ЧТО СЛУЧИЛОСЬ 29.08.2026. Счёт на 929 ₽ (RENEW, ЮMoney, транзакция ee0e36b6…)
создан в 06:17, крон `cancel_old_transactions` погасил его в 07:00 как PENDING
старше 30 минут, а покупательница оплатила по той же ссылке в 08:57 — ссылка
quickpay живёт вечно. Вебхук пришёл, база попробовала перевести счёт в COMPLETED
из (PENDING, FAILED), CANCELED в набор не входит, переход не совпал — и всё:
шлюзу отвечено 200, деньги на кошельке, подписки нет, владельцу не сказано
ничего. Нашли только потому, что человек пожаловался.

ЧТО ПРОВЕРЯЕМ. Правку `overlay_patches/gateway_payment.py`: перед вызовом базы
отменённый счёт поднимается обратно в PENDING, и дальше отрабатывает её
собственная, НЕ скопированная логика. Проверяем именно поведение, а не текст:
подделками подменены uow, DAO и уведомления, настоящая БД не нужна.

Ключевые инварианты, за которыми тут следим:
  * спасаем ТОЛЬКО оплату (COMPLETED) и ТОЛЬКО из CANCELED;
  * повторный вебхук по проведённому счёту не поднимает ничего и не выдаёт
    подписку второй раз — иначе спасение само стало бы источником двойных выдач;
  * обычная оплата вовремя идёт как раньше, без лишнего письма владельцу;
  * чужой шлюз не трогаем — про это ругается сама база.

Машина состояний базы описана в test_payment_idempotency.py; эта правка
добавляет к ней один переход: CANCELED → PENDING (только при оплате).

Запуск — внутри образа бота: исходников бота на хосте нет, а pytest нет в образе,
поэтому ставим его в отдельную папку (в venv образа не лезем, см. грабли venv/pip):

  docker run --rm --env-file .env --network remnawave-network \
    -v /opt/remnashop/admin_src/overlay_patches:/opt/remnashop/overlay_patches:ro \
    -v /opt/remnashop/admin_src/tests:/tmp/tests:ro \
    remnashop-remnashop sh -c 'pip install -q --target /tmp/pylibs pytest pytest-asyncio \
      && PYTHONPATH=/tmp/pylibs python -m pytest /tmp/tests/test_late_payment_revive.py \
         -v --asyncio-mode=auto'
"""

import importlib
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import pytest

payment = importlib.import_module("src.application.use_cases.gateways.commands.payment")

from src.core.enums import PaymentGatewayType, TransactionStatus  # noqa: E402

COMPLETED = TransactionStatus.COMPLETED
CANCELED = TransactionStatus.CANCELED
PENDING = TransactionStatus.PENDING
FAILED = TransactionStatus.FAILED
YOOMONEY = PaymentGatewayType.YOOMONEY


class FakeUow:
    """Границы транзакции нам важны только фактом коммита."""

    def __init__(self) -> None:
        self.commits = 0

    async def __aenter__(self) -> "FakeUow":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None


@dataclass
class FakePricing:
    final_amount: str = "929"
    original_amount: str = "929"
    discount_percent: int = 0
    is_free: bool = False


@dataclass
class FakeCurrency:
    symbol: str = "RUB"


@dataclass
class FakeTransaction:
    payment_id: Any
    status: Any
    gateway_type: Any = YOOMONEY
    user_id: int = 6
    is_test: bool = False
    created_at: str = "2026-08-29 06:17:03"
    pricing: FakePricing = field(default_factory=FakePricing)
    currency: FakeCurrency = field(default_factory=FakeCurrency)


class FakeTransactionDao:
    """CAS-переход как в настоящем DAO: совпал набор — меняем, нет — None."""

    def __init__(self, transaction: FakeTransaction) -> None:
        self.transaction = transaction
        self.transitions: list[tuple[Any, Any, tuple]] = []

    async def get_by_payment_id(self, payment_id: Any) -> Optional[FakeTransaction]:
        return self.transaction if payment_id == self.transaction.payment_id else None

    async def transition_status(self, payment_id: Any, new: Any, allowed: Any) -> Any:
        self.transitions.append((self.transaction.status, new, tuple(allowed)))
        if self.transaction.status not in allowed:
            return None
        self.transaction.status = new
        return self.transaction


class FakeUserDao:
    async def get_by_id(self, user_id: int) -> Any:
        class User:
            id = user_id
            log = f"[USER:{user_id}]"
            remna_name = f"rs_{user_id}"
            telegram_id = None

        return User()


class FakeNotifier:
    def __init__(self) -> None:
        self.admin_messages: list[Any] = []

    async def notify_admins(self, payload: Any, roles: Any = None) -> None:
        self.admin_messages.append(payload)

    async def notify_user(self, *args: Any, **kwargs: Any) -> None:
        return None


class Harness:
    """Подделка ProcessPayment: только те поля, которых касаются обе стороны."""

    def __init__(self, status: Any, gateway: Any = YOOMONEY) -> None:
        self.transaction = FakeTransaction(
            payment_id=uuid.uuid4(), status=status, gateway_type=gateway
        )
        self.uow = FakeUow()
        self.transaction_dao = FakeTransactionDao(self.transaction)
        self.user_dao = FakeUserDao()
        self.notifier = FakeNotifier()
        self.granted = 0

    async def _handle_success(self, user: Any, transaction: Any) -> None:
        self.granted += 1


@dataclass
class Data:
    payment_id: Any
    new_transaction_status: Any
    gateway_type: Any = YOOMONEY


async def process(harness: Harness, new_status: Any) -> None:
    await payment.ProcessPayment._execute(
        harness, None, Data(harness.transaction.payment_id, new_status)
    )


@pytest.mark.asyncio
async def test_canceled_invoice_paid_is_revived_and_granted() -> None:
    """Тот самый случай: заплатили по отменённой ссылке — подписка выдаётся."""
    harness = Harness(CANCELED)
    await process(harness, COMPLETED)

    assert harness.transaction.status == COMPLETED
    assert harness.granted == 1, "подписка обязана выдаться, деньги уже на кошельке"
    assert harness.transaction_dao.transitions == [
        (CANCELED, PENDING, (CANCELED,)),  # наша правка подняла счёт
        (PENDING, COMPLETED, (PENDING, FAILED)),  # база провела его как обычно
    ]
    assert len(harness.notifier.admin_messages) == 1, "владелец обязан узнать"


@pytest.mark.asyncio
async def test_admin_alert_survives_five_seconds() -> None:
    """delete_after=None обязателен: по умолчанию payload самоуничтожается за 5 с."""
    harness = Harness(CANCELED)
    await process(harness, COMPLETED)

    assert harness.notifier.admin_messages[0].delete_after is None


@pytest.mark.asyncio
async def test_paid_in_time_is_untouched() -> None:
    """Обычная оплата: ни спасения, ни письма — иначе владельца зальёт шумом."""
    harness = Harness(PENDING)
    await process(harness, COMPLETED)

    assert harness.transaction.status == COMPLETED
    assert harness.granted == 1
    assert harness.transaction_dao.transitions == [(PENDING, COMPLETED, (PENDING, FAILED))]
    assert harness.notifier.admin_messages == []


@pytest.mark.asyncio
async def test_repeated_webhook_does_not_grant_twice() -> None:
    """Повтор вебхука по проведённому счёту: спасение не должно стать дублем выдачи."""
    harness = Harness(COMPLETED)
    await process(harness, COMPLETED)

    assert harness.granted == 0
    assert harness.notifier.admin_messages == []
    assert (CANCELED, PENDING, (CANCELED,)) not in harness.transaction_dao.transitions


@pytest.mark.asyncio
async def test_cancel_webhook_never_revives() -> None:
    """Поднимаем только на оплату. Вебхук об отмене — не повод оживлять счёт."""
    harness = Harness(CANCELED)
    await process(harness, CANCELED)

    assert harness.transaction.status == CANCELED
    assert harness.granted == 0
    assert harness.uow.commits == 0
    assert (CANCELED, PENDING, (CANCELED,)) not in harness.transaction_dao.transitions


@pytest.mark.asyncio
async def test_foreign_gateway_is_left_to_the_base() -> None:
    """Не наш шлюз — не наше дело: про это ругается сама база."""
    harness = Harness(CANCELED, gateway=PaymentGatewayType.TELEGRAM_STARS)
    await process(harness, COMPLETED)

    assert harness.transaction.status == CANCELED
    assert harness.granted == 0
    assert harness.transaction_dao.transitions == []
    assert harness.notifier.admin_messages == []
