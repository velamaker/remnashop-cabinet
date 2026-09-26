"""Кнопки «сигналов до ухода» через НАСТОЯЩИЙ диспетчер aiogram — как в проде.

ЧТО СЛУЧИЛОСЬ. Обработчик `on_signal_button` просил у dishka `config: FromDishka[AppConfig]`
и вдобавок принимал `**data`. Диспетчер базы создаётся как
`Dispatcher(storage=…, config=config)`, то есть `config` лежит в данных КАЖДОГО
апдейта. Обработчику с `**data` aiogram отдаёт все данные, обёртка dishka зовёт его
с `**kwargs, **solved` — и `config` приходил дважды: TypeError на каждое нажатие
✅/❌/🔕. Ответ не записывался, «часики» висели до таймаута, подсказка «не работает»
не уходила, отказ «не присылать» не сохранялся.

Юнит-тесты `handle()` (test_churn_signals_answer.py) этого не видели: они зовут
функцию напрямую, мимо aiogram и dishka. Здесь — полный путь апдейта, для каждой
кнопки, которую мы на самом деле отправляем (callback_data берём из клавиатур, а не
пишем руками). Telegram подделан: вызовы API записываются, в сеть ничего не уходит.
"""

import importlib
from types import SimpleNamespace
from typing import Any, Optional

import pytest
from aiogram.methods import AnswerCallbackQuery, EditMessageReplyMarkup, SendMessage
from sqlalchemy.ext.asyncio import AsyncSession

from _aiogram_harness import (  # noqa: E402 — соседний модуль тестов
    TG,
    press,
    real_dispatcher,
    vendor_workflow_keys,
)

signals = importlib.import_module("src.infrastructure.services.overlay_churn_signals")
handler = importlib.import_module("src.telegram.routers.overlay_churn_signals")

from src.core.config import AppConfig  # noqa: E402

CONFIG = SimpleNamespace(
    web_cabinet_url="https://cab.example",
    bot=SimpleNamespace(support_username=SimpleNamespace(get_secret_value=lambda: "help_bot")),
)
USER = SimpleNamespace(id=7, language="ru")
SIGNAL_ID = 42


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class FakeDb:
    """Сессия базы: отвечает на UPDATE ответа так, будто строка принадлежит нажавшему."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.commits = 0

    async def execute(self, statement: Any, params: Optional[dict] = None) -> _Result:
        sql = str(getattr(statement, "text", statement))
        self.calls.append((sql, params or {}))
        if "UPDATE churn_signals" in sql:
            return _Result((params["id"],))
        return _Result(None)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None


def _button(markup: Any, text: str) -> str:
    for row in markup.inline_keyboard:
        for button in row:
            if button.text == text:
                return button.callback_data
    raise AssertionError(f"кнопки «{text}» нет в клавиатуре")


WORDS = signals.WORDS["ru"]
CHECK = signals.check_keyboard(SIGNAL_ID, "ru")
IDLE = signals.idle_keyboard("https://cab.example", "https://t.me/help_bot", "ru")

# (кнопка, callback_data, что пишем в базу, что человек видит во всплывашке)
BUTTONS = [
    ("✅ из «Всё работает?»", _button(CHECK, WORDS["works"]),
     {"answer": "works", "id": SIGNAL_ID, "user_id": 7}, WORDS["thanks"]),
    ("❌ из «Всё работает?»", _button(CHECK, WORDS["broken"]),
     {"answer": "broken", "id": SIGNAL_ID, "user_id": 7}, WORDS["sorry"]),
    ("🔕 из «Всё работает?»", _button(CHECK, WORDS["off"]),
     {"user_id": 7, "kind": signals.OPTOUT_CHECK}, WORDS["off_done"]),
    ("🔕 из «Давно не видели»", _button(IDLE, WORDS["off"]),
     {"user_id": 7, "kind": signals.OPTOUT_IDLE}, WORDS["off_done"]),
]


@pytest.fixture
async def world():
    # Ключи workflow_data — те же, что передаёт в Dispatcher база; `config` — как у
    # неё, тот же объект, что dishka отдаёт по AppConfig.
    workflow = {key: object() for key in vendor_workflow_keys()}
    workflow["config"] = CONFIG
    db = FakeDb()
    async with real_dispatcher(
        [handler.router],
        workflow=workflow,
        user=USER,
        bindings={AsyncSession: db, AppConfig: CONFIG},
    ) as w:
        w.db = db
        yield w


def test_vendor_dispatcher_really_carries_config():
    """Без этого факта весь файл проверял бы выдуманный диспетчер."""
    assert "config" in vendor_workflow_keys()


@pytest.mark.parametrize(
    ("label", "data", "params", "toast"), BUTTONS, ids=[b[0] for b in BUTTONS]
)
async def test_every_button_works_through_real_dispatcher(world, label, data, params, toast):
    w = world
    # Раньше здесь вылетал TypeError: got multiple values for keyword argument 'config'.
    await press(w, data)

    assert [p for _, p in w.db.calls] == [params], label
    assert w.db.commits == 1
    answers = w.session.of(AnswerCallbackQuery)
    assert [a.text for a in answers] == [toast]
    # Кнопки убраны, текст вопроса остался.
    edits = w.session.of(EditMessageReplyMarkup)
    assert [(e.chat_id, e.message_id, e.reply_markup) for e in edits] == [(TG, 900, None)]


async def test_broken_sends_self_check_through_real_dispatcher(world):
    w = world
    await press(w, _button(CHECK, WORDS["broken"]))

    sent = w.session.of(SendMessage)
    assert len(sent) == 1
    assert sent[0].chat_id == TG
    assert "Давайте разберёмся" in sent[0].text
    urls = [b.url for row in sent[0].reply_markup.inline_keyboard for b in row]
    # Ссылки собраны из AppConfig, пришедшего от dishka.
    assert urls == ["https://cab.example/support", "https://t.me/help_bot"]

