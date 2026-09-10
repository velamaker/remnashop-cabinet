"""Продление с баланса: когда деньги возвращать, а когда это бесплатное продление.

ЧТО БЫЛО. `renew_current_from_balance` накрывает одним `try` не только оплату, но и
ПОСЛЕДНИЙ шаг успешного пути — живой вызов Telegram (`redirect.to_success_payment`
внутри ProcessPayment). Человек заблокировал бота → исключение прилетает уже ПОСЛЕ
того, как подписка закоммичена и в Remnawave, и в БД, а `except` безусловно
возвращал деньги на баланс. Автоплатёж гасит это одним `logger.warning`, поэтому
каждый его прогон по такому пользователю = бесплатное продление, и никто не видит.

ПОЧЕМУ СУДИМ ПО СРОКУ, А НЕ ПО СТАТУСУ СЧЁТА. Базовый ProcessPayment переводит счёт
в COMPLETED ДО выдачи (`transition_status` + commit, и только потом
`_handle_success`). Значит «счёт проведён» ещё НЕ значит «подписка есть»: упади
выдача в Remnawave — счёт остался бы COMPLETED, а услуги нет, и человек потерял бы
и деньги, и подписку. Сдвинутый вперёд срок подписки — единственный признак,
который нельзя истолковать двояко.

Запуск — внутри образа бота:

  docker run --rm --env-file .env --network remnawave-network \
    -v /opt/remnashop/admin_src/src/infrastructure/services/overlay_balance.py:\
/opt/remnashop/src/infrastructure/services/overlay_balance.py:ro \
    -v /opt/remnashop/admin_src/tests:/tmp/tests:ro \
    remnashop-remnashop sh -c 'pip install -q --target /tmp/pylibs pytest pytest-asyncio \
      && PYTHONPATH=/tmp/pylibs python -m pytest /tmp/tests/test_balance_refund.py \
         -v --asyncio-mode=auto'
"""

import importlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

balance = importlib.import_module("src.infrastructure.services.overlay_balance")

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


class FakeSubscriptionDao:
    """Отдаёт подписку, какой она стала ПОСЛЕ попытки продления."""

    def __init__(self, expire_after=None, raises: bool = False) -> None:
        self.expire_after = expire_after
        self.raises = raises

    async def get_current(self, user_id: int):
        if self.raises:
            raise RuntimeError("база недоступна")
        if self.expire_after is _MISSING:
            return None
        return SimpleNamespace(expire_at=self.expire_after)


_MISSING = object()


@pytest.mark.asyncio
async def test_granted_when_expiry_moved_forward() -> None:
    """Срок уехал вперёд — подписка выдана, деньги возвращать НЕЛЬЗЯ.

    Это и есть тот случай: человек заблокировал бота, упало уведомление, а услуга
    у него уже есть.
    """
    dao = FakeSubscriptionDao(expire_after=NOW + timedelta(days=30))
    assert await balance.was_subscription_granted(dao, 42, NOW) is True


@pytest.mark.asyncio
async def test_not_granted_when_expiry_unchanged() -> None:
    """Срок не изменился — выдачи не было, деньги возвращаем (обычный сбой)."""
    dao = FakeSubscriptionDao(expire_after=NOW)
    assert await balance.was_subscription_granted(dao, 42, NOW) is False


@pytest.mark.asyncio
async def test_not_granted_when_expiry_went_back() -> None:
    """Срок назад уехать не может, но если так — это точно не выдача."""
    dao = FakeSubscriptionDao(expire_after=NOW - timedelta(days=1))
    assert await balance.was_subscription_granted(dao, 42, NOW) is False


@pytest.mark.asyncio
async def test_unknown_when_dao_raises() -> None:
    """База не ответила — честно говорим «не знаю», а не выдумываем ответ.

    Вызывающий на None возвращает деньги (как раньше), но пишет ERROR и зовёт
    владельца: человек мог остаться и с деньгами, и с подпиской.
    """
    dao = FakeSubscriptionDao(raises=True)
    assert await balance.was_subscription_granted(dao, 42, NOW) is None


@pytest.mark.asyncio
async def test_unknown_when_no_subscription_after() -> None:
    """Подписки после попытки нет вовсе — сравнивать не с чем."""
    dao = FakeSubscriptionDao(expire_after=_MISSING)
    assert await balance.was_subscription_granted(dao, 42, NOW) is None


@pytest.mark.asyncio
async def test_unknown_when_no_expiry_before() -> None:
    """Не знали срок ДО списания — вывод сделать не из чего."""
    dao = FakeSubscriptionDao(expire_after=NOW + timedelta(days=30))
    assert await balance.was_subscription_granted(dao, 42, None) is None
