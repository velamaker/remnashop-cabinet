"""Досчёт приглашения после входа через Telegram: кому засчитываем, кому нет.

ЗАЧЕМ ЭТА РУЧКА. У почтовой регистрации реф-код едет в теле формы. У входа через
Telegram поля под него нет ни в схеме запроса, ни в `_get_or_create_telegram_user`
(тот зовёт `RegisterWebUserDto(user=...)` без кода), поэтому приглашение,
завершённое телеграмом, не засчитывалось вовсе и молча. При 1069 телеграм-аккаунтах
из 1090 это означало, что реферальная программа кабинета не работала ни разу.

ЧТО ЗДЕСЬ СТЕРЕЖЁМ. Ручка вызывается уже ПОСЛЕ входа, то есть в момент, когда
человек — обычный авторизованный пользователь. Значит появляется соблазн, которого
у формы регистрации не было: давний пользователь может позвать её сам и записаться
под чужой код, чтобы приносить тому награды. Поэтому окно новизны аккаунта — не
украшение, а единственная защита, которой нет у базового `AttachReferral`.

Остальное (выключенная рефералка, несуществующий код, свой собственный код, уже
имеющийся пригласивший) базовый сценарий отсекает сам — здесь только проверяем,
что мы честно отдаём ему решение и не подменяем его ответ.

Запуск — внутри образа бота (исходников бота на хосте нет, pytest в образе тоже):

  docker run --rm --env-file .env --network remnawave-network \
    -v /opt/remnashop/admin_src/src/web/endpoints/public/referral_stats.py:\
/opt/remnashop/src/web/endpoints/public/referral_stats.py:ro \
    -v /opt/remnashop/admin_src/tests:/tmp/tests:ro \
    remnashop-remnashop sh -c 'pip install -q --target /tmp/pylibs pytest pytest-asyncio \
      && PYTHONPATH=/tmp/pylibs python -m pytest /tmp/tests/test_referral_attach.py \
         -v --asyncio-mode=auto'
"""

import importlib
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Optional

import pytest

from src.core.utils.time import datetime_now

referral_stats = importlib.import_module("src.web.endpoints.public.referral_stats")


@dataclass
class FakeUser:
    id: int = 42
    created_at: Any = None
    log: str = "[USER:42]"


class FakeAttachReferral:
    """Подделка базового сценария: помним, звали ли нас и с чем."""

    def __init__(self, result: Optional[object] = None) -> None:
        self.result = result
        self.calls: list[tuple[int, str]] = []

    async def system(self, dto: Any) -> Optional[object]:
        self.calls.append((dto.user_id, dto.referral_code))
        return self.result


async def call(user: FakeUser, attach: FakeAttachReferral, code: str = "ABC123") -> dict:
    # Зовём решение, а не саму ручку: у той декоратор @inject из dishka, который
    # в тесте пошёл бы искать зависимости в контексте запроса и упал бы KeyError.
    return {"success": await referral_stats.decide_attach(user, code, attach)}


@pytest.mark.asyncio
async def test_fresh_signup_is_counted() -> None:
    """Обычный случай: человек только что вошёл телеграмом по ссылке приглашения."""
    user = FakeUser(created_at=datetime_now() - timedelta(seconds=30))
    attach = FakeAttachReferral(result=object())  # база нашла пригласившего

    assert await call(user, attach) == {"success": True}
    assert attach.calls == [(42, "ABC123")], "код обязан уйти в базовый сценарий как есть"


@pytest.mark.asyncio
async def test_old_account_cannot_attach_retroactively() -> None:
    """Главная защита: давний пользователь не запишется под чужой код задним числом.

    Именно этого базовый AttachReferral не знает — для него вызов неотличим от
    регистрации. Проверяем, что до него дело вообще не доходит.
    """
    user = FakeUser(created_at=datetime_now() - timedelta(days=30))
    attach = FakeAttachReferral(result=object())

    assert await call(user, attach) == {"success": False}
    assert attach.calls == [], "старый аккаунт не должен доходить до привязки"


@pytest.mark.asyncio
async def test_window_edge_is_rejected() -> None:
    """Ровно за границей окна — уже нельзя (граница закрыта, а не приоткрыта)."""
    user = FakeUser(created_at=datetime_now() - referral_stats.ATTACH_WINDOW - timedelta(minutes=1))
    attach = FakeAttachReferral(result=object())

    assert await call(user, attach) == {"success": False}
    assert attach.calls == []


@pytest.mark.asyncio
async def test_unknown_created_at_is_rejected() -> None:
    """Не знаем возраст аккаунта — отказываем. Молчаливое «да» тут дороже отказа."""
    attach = FakeAttachReferral(result=object())

    assert await call(FakeUser(created_at=None), attach) == {"success": False}
    assert attach.calls == []


@pytest.mark.asyncio
async def test_base_refusal_is_passed_through() -> None:
    """Отказ базы (чужой код, свой код, уже есть пригласивший) отдаём как есть."""
    user = FakeUser(created_at=datetime_now() - timedelta(minutes=1))
    attach = FakeAttachReferral(result=None)  # база отказала

    assert await call(user, attach) == {"success": False}
    assert attach.calls == [(42, "ABC123")], "решение принимает база, мы его не подменяем"


@pytest.mark.asyncio
async def test_code_is_trimmed() -> None:
    """Код из адресной строки может приехать с пробелами — база их не ждёт."""
    user = FakeUser(created_at=datetime_now() - timedelta(minutes=1))
    attach = FakeAttachReferral(result=object())

    await call(user, attach, code="  ABC123  ")
    assert attach.calls == [(42, "ABC123")]
