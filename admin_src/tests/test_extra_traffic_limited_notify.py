"""Сообщение «трафик закончился — можно докупить».

ЧТО ЗАПИРАЕМ:
  * шлём ТОЛЬКО при включённых продажах и только тем, кто проходит те же проверки,
    что и покупка: предложение, на которое нельзя нажать, хуже молчания;
  * ОДИН РАЗ на окно трафика — дедуп в Redis, общий у вебхука и крона (в файле его
    держать нельзя: вебхук исполняет процесс бота, крон — воркер, и JSON они
    затирали бы друг другу);
  * после обновления трафика ключ другой — значит в новом окне сообщение снова
    возможно;
  * базовое уведомление бота про LIMITED мы не подавляем и не дублируем: наше идёт
    отдельным сообщением и с правильной датой;
  * продажи выключены (состояние по умолчанию) — не шлём ничего вовсе.

Числа, даты и идентификаторы синтетические.
"""

import importlib
import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Optional

import pytest

extra = importlib.import_module("src.infrastructure.services.overlay_extra_traffic")
patch = importlib.import_module("overlay_patches.traffic_reset_date")

from test_extra_traffic_buy import (  # noqa: E402
    EXPIRE,
    NOW,
    PANEL_CREATED,
    PLAN_GB,
    WINDOW,
    FakeSession,
    Log,
    config_on,  # noqa: F401 — фикстура включает продажи
)

from src.core.utils.converters import gb_to_bytes  # noqa: E402

USER_ID = 7
USER = SimpleNamespace(id=USER_ID, telegram_id=555000111, log="[USER:7]")
CONFIG = SimpleNamespace(
    web_cabinet_url="https://cabinet.example.test",
    bot=SimpleNamespace(token=SimpleNamespace(get_secret_value=lambda: "123:AAA")),
)


class FakeRedis:
    """SET NX с TTL — ровно то, на чём держится «один раз на окно»."""

    def __init__(self, fails: bool = False) -> None:
        self.store: dict[str, Any] = {}
        self.fails = fails
        self.calls: list[tuple[str, Optional[int]]] = []

    async def set(self, key: str, value: str, ex: Optional[int] = None, nx: bool = False):
        if self.fails:
            raise RuntimeError("redis недоступен")
        self.calls.append((key, ex))
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True


@pytest.fixture
def sent(monkeypatch):
    """Перехватываем отправку: настоящий Bot в тестах поднимать нечем и незачем."""
    box: list[tuple[Any, str]] = []

    async def fake_send(config, telegram_id, text_html):
        box.append((telegram_id, text_html))
        return True

    monkeypatch.setattr(extra, "send_raw_telegram", fake_send)
    return box


def panel(status: str = "LIMITED", limit_gb: int = PLAN_GB) -> extra.PanelView:
    return extra.PanelView(
        limit_bytes=gb_to_bytes(limit_gb),
        used_bytes=gb_to_bytes(limit_gb),
        status=status,
        created_at=PANEL_CREATED,
    )


async def test_offer_is_sent_once_per_window(sent):
    log = Log()
    session = FakeSession(log)
    redis = FakeRedis()

    first = await extra.offer_when_limited(session, redis, CONFIG, USER, panel(), NOW)
    second = await extra.offer_when_limited(session, redis, CONFIG, USER, panel(), NOW)

    assert first is True and second is False
    assert len(sent) == 1
    telegram_id, text = sent[0]
    assert telegram_id == USER.telegram_id
    assert "50 ГБ" in text and "40 ₽" in text
    assert "07.10.2026" in text, "дата обновления обязана быть названа"
    assert "cabinet.example.test/billing?extra_traffic=1" in text


async def test_new_window_allows_the_message_again(sent):
    log = Log()
    session = FakeSession(log)
    redis = FakeRedis()

    await extra.offer_when_limited(session, redis, CONFIG, USER, panel(), NOW)
    # Месяц спустя окно другое — ключ другой.
    later = NOW + timedelta(days=31)
    await extra.offer_when_limited(session, redis, CONFIG, USER, panel(), later)

    assert len(sent) == 2
    assert len({key for key, _ in redis.calls}) == 2


def test_dedup_key_names_the_window_and_ttl_outlives_it():
    a = extra.limited_dedup_key(USER_ID, WINDOW)
    b = extra.limited_dedup_key(USER_ID, WINDOW + timedelta(days=30))
    assert a != b and str(USER_ID) in a
    # Без окна (NO_RESET) ключ тоже валиден — иначе такие люди получали бы спам.
    assert extra.limited_dedup_key(USER_ID, None).endswith("noreset")
    # TTL переживает окно, иначе после сброса пришло бы второе сообщение о старом.
    assert extra.limited_ttl(WINDOW, NOW) > int((WINDOW - NOW).total_seconds())


async def test_sales_off_means_no_message_at_all(sent, config_on):
    """Установка без докупки обязана вести себя ровно как раньше."""
    config_on.write_text('{"enabled": false, "gb_per_purchase": 50, "price_rub": 40}', "utf-8")
    log = Log()
    session = FakeSession(log)

    assert await extra.offer_when_limited(session, FakeRedis(), CONFIG, USER, panel(), NOW) is False
    assert sent == []


async def test_notify_limited_toggle_is_respected(sent, config_on):
    config_on.write_text(
        '{"enabled": true, "gb_per_purchase": 50, "price_rub": 40, "notify_limited": false}',
        "utf-8",
    )
    log = Log()
    assert (
        await extra.offer_when_limited(FakeSession(log), FakeRedis(), CONFIG, USER, panel(), NOW)
        is False
    )
    assert sent == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"is_trial": True},
        {"traffic_limit": 0},
        {"frozen_at": NOW - timedelta(days=1)},
        {"reserve_expire_at": EXPIRE - timedelta(minutes=30)},
        {"expire_at": NOW - timedelta(days=1)},
    ],
)
async def test_people_who_cannot_buy_get_nothing(sent, kwargs):
    """Предложение, на которое нельзя нажать, — это второе сообщение подряд без толку."""
    log = Log()
    session = FakeSession(log, **kwargs)
    assert await extra.offer_when_limited(session, FakeRedis(), CONFIG, USER, panel(), NOW) is False
    assert sent == []


async def test_no_cabinet_url_means_nowhere_to_send(sent):
    log = Log()
    config = SimpleNamespace(web_cabinet_url="", bot=CONFIG.bot)
    assert await extra.offer_when_limited(FakeSession(log), FakeRedis(), config, USER, panel(), NOW) is False
    assert sent == []


async def test_broken_redis_keeps_quiet_instead_of_spamming(sent):
    """Без дедупа лучше промолчать: повтор вебхука иначе шлёт по сообщению на каждый."""
    log = Log()
    assert (
        await extra.offer_when_limited(
            FakeSession(log), FakeRedis(fails=True), CONFIG, USER, panel(), NOW
        )
        is False
    )
    assert sent == []


def test_webhook_wrapper_sends_after_the_base_and_only_for_limited():
    """Базовое уведомление не подавляем: наше идёт ПОСЛЕ и только на LIMITED."""
    source = inspect.getsource(patch.apply)
    body = source[source.index("async def _process_status") :]
    assert body.index("base_process_status") < body.index("_offer_extra_traffic")
    assert "RemnaUserEvent" in body and "LIMITED" in body
    # Падение уведомления не должно срывать обработку вебхука.
    assert "except Exception" in body


def test_wrapper_takes_the_session_from_uow_not_from_a_rewritten_init():
    """Замена __init__ ради одного атрибута — копия чужого тела в денежном соседстве."""
    source = inspect.getsource(patch._offer_extra_traffic)
    assert 'getattr(self, "uow", None), "session"' in source


def test_cron_catch_up_uses_the_same_helper_and_the_same_dedup():
    cron = importlib.import_module("src.infrastructure.taskiq.tasks.traffic_alert")
    assert "offer_when_limited" in inspect.getsource(cron._catch_up_limited)
    # Сам крон обёрнут `@inject` dishka — читаем модуль, а не подменённую функцию.
    loop = inspect.getsource(cron)
    # LIMITED раньше отсекался вместе со всеми неактивными — теперь он адресат.
    assert '("ACTIVE", "LIMITED")' in loop
    assert '_catch_up_limited' in loop


def test_almost_out_line_names_the_date_and_the_price():
    text = extra.user_text("almost_out", gb=50, price=40, until=WINDOW)
    assert "50 ГБ" in text and "40 ₽" in text and "07.10.2026" in text
