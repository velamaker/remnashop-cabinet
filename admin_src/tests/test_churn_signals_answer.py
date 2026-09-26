"""Ответ на «Всё работает?», отказ «Не присылать такое» и админская страница сигналов.

ЧТО ЗАПИРАЕМ:
  * «✅ Всё работает» записывает ответ, благодарит и убирает кнопки;
  * «❌ Не работает» записывает ответ и присылает ОТДЕЛЬНОЕ сообщение с самопроверкой
    в кабинете (/support) и кнопкой поддержки — а не «напишите нам»;
  * чужой или устаревший id ничего не меняет и не присылает подсказку;
  * «Не присылать такое» пишет СВОЙ вид отказа, повтор безопасен;
  * чужие колбэки и мусор в базу не пишутся;
  * сбой базы не падает человеку в лицо;
  * админка: оба сигнала выключены по умолчанию, сохранение коммитит сессию вручную,
    доля «не работает» считается от ответивших, а не от спрошенных.

Данные синтетические.
"""

import importlib
import inspect
from types import SimpleNamespace
from typing import Any, Optional

import pytest

cs = importlib.import_module("src.infrastructure.services.overlay_churn_signals")
handler = importlib.import_module("src.telegram.routers.overlay_churn_signals")
admin = importlib.import_module("src.web.endpoints.admin.churn_signals")

CONFIG = SimpleNamespace(
    web_cabinet_url="https://cab.example",
    bot=SimpleNamespace(support_username=SimpleNamespace(get_secret_value=lambda: "help_bot")),
)


class FakeMessage:
    def __init__(self) -> None:
        self.markup_edits: list[Any] = []
        self.replies: list[tuple[str, Any]] = []

    async def edit_reply_markup(self, reply_markup=None):
        self.markup_edits.append(reply_markup)

    async def answer(self, text: str, reply_markup=None):
        self.replies.append((text, reply_markup))


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, Any]] = []

    async def send_message(self, chat_id: int, text: str, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))


class FakeCallback:
    def __init__(self, data: Optional[str], *, bot: Any = "default") -> None:
        self.data = data
        self.message = FakeMessage()
        self.answers: list[tuple[Optional[str], bool]] = []
        self.bot = FakeBot() if bot == "default" else bot
        self.from_user = SimpleNamespace(id=555)

    async def answer(self, text: Optional[str] = None, show_alert: bool = False):
        self.answers.append((text, show_alert))


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self):
        return self._row


class FakeSession:
    def __init__(self, *, owns: bool = True, fails: bool = False) -> None:
        self.owns = owns
        self.fails = fails
        self.calls: list[tuple[str, dict]] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement: Any, params: Optional[dict] = None):
        sql = str(getattr(statement, "text", statement))
        self.calls.append((sql, params or {}))
        if self.fails:
            raise RuntimeError("база недоступна")
        if "UPDATE churn_signals" in sql:
            return _Result((params["id"],) if self.owns else None)
        return _Result(None)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


USER = SimpleNamespace(id=7, language="ru")


def _urls(markup) -> list[str]:
    return [b.url for row in markup.inline_keyboard for b in row]


async def test_works_answer_is_saved_and_buttons_go_away():
    cb, session = FakeCallback("rs_sig:ok:42"), FakeSession()
    assert await handler.handle(cb, session, CONFIG, USER) == "works"
    sql, params = session.calls[0]
    assert "UPDATE churn_signals" in sql
    assert params == {"answer": "works", "id": 42, "user_id": 7}
    assert session.commits == 1
    assert cb.answers == [(cs.WORDS["ru"]["thanks"], False)]
    assert cb.message.markup_edits == [None]
    assert cb.message.replies == [] and cb.bot.sent == []


async def test_broken_answer_leads_to_self_check_and_support():
    cb, session = FakeCallback("rs_sig:bad:42"), FakeSession()
    assert await handler.handle(cb, session, CONFIG, USER) == "broken"
    assert session.calls[0][1]["answer"] == "broken"
    # В личку нажавшему: вопрос мог устареть, и ответить на него уже нельзя.
    chat_id, text, markup = cb.bot.sent[0]
    assert chat_id == 555
    assert "Давайте разберёмся" in text
    assert _urls(markup) == ["https://cab.example/support", "https://t.me/help_bot"]
    assert cb.message.markup_edits == [None]


async def test_broken_answer_in_english():
    cb = FakeCallback("rs_sig:bad:42")
    await handler.handle(cb, FakeSession(), CONFIG, SimpleNamespace(id=7, language="en"))
    _, text, markup = cb.bot.sent[0]
    assert "Let's sort it out" in text
    assert [b.text for row in markup.inline_keyboard for b in row][0] == "🩺 Check my connection"


async def test_broken_answer_without_bound_bot_replies_to_the_message():
    cb = FakeCallback("rs_sig:bad:42", bot=None)
    assert await handler.handle(cb, FakeSession(), CONFIG, USER) == "broken"
    text, _ = cb.message.replies[0]
    assert "Давайте разберёмся" in text


async def test_sending_help_failure_does_not_break_the_answer():
    class DeadBot:
        async def send_message(self, **_kwargs):
            raise RuntimeError("Forbidden: bot was blocked by the user")

    cb, session = FakeCallback("rs_sig:bad:42", bot=DeadBot()), FakeSession()
    assert await handler.handle(cb, session, CONFIG, USER) == "broken"
    assert session.commits == 1


async def test_foreign_or_stale_row_changes_nothing():
    cb, session = FakeCallback("rs_sig:bad:42"), FakeSession(owns=False)
    assert await handler.handle(cb, session, CONFIG, USER) == "stale"
    assert cb.message.replies == [] and cb.bot.sent == []
    assert cb.answers == [(cs.WORDS["ru"]["stale"], False)]


async def test_optout_writes_its_own_kind():
    cb, session = FakeCallback("rs_sig:off:idle"), FakeSession()
    assert await handler.handle(cb, session, CONFIG, USER) == "opted_out"
    sql, params = session.calls[0]
    assert "notification_optouts" in sql and "ON CONFLICT" in sql
    assert params == {"user_id": 7, "kind": cs.OPTOUT_IDLE}
    cb2, session2 = FakeCallback("rs_sig:off:check"), FakeSession()
    await handler.handle(cb2, session2, CONFIG, USER)
    assert session2.calls[0][1]["kind"] == cs.OPTOUT_CHECK


@pytest.mark.parametrize("data", ["rs_sig:ok:abc", "rs_sig:off:everything", "rs_sig:", None])
async def test_junk_is_ignored_without_touching_the_database(data):
    cb, session = FakeCallback(data), FakeSession()
    assert await handler.handle(cb, session, CONFIG, USER) == "ignored"
    assert session.calls == [] and cb.answers == [(None, False)]


async def test_unknown_sender_is_ignored():
    cb, session = FakeCallback("rs_sig:ok:1"), FakeSession()
    assert await handler.handle(cb, session, CONFIG, None) == "ignored"
    assert session.calls == []


async def test_database_failure_is_a_polite_alert():
    cb, session = FakeCallback("rs_sig:ok:1"), FakeSession(fails=True)
    assert await handler.handle(cb, session, CONFIG, USER) == "error"
    assert session.rollbacks == 1
    assert cb.answers[-1][1] is True


def test_router_filters_only_its_own_prefix():
    """Наши роутеры встают ПЕРЕД базовыми — широкий фильтр съел бы чужие кнопки."""
    source = inspect.getsource(handler)
    assert "F.data.startswith(signals.CALLBACK_PREFIX)" in source
    assert cs.CALLBACK_PREFIX == "rs_sig:"


def test_router_is_registered_before_base_routers():
    routers = importlib.import_module("overlay_patches.bot_routers")
    assert ("src.telegram.routers.overlay_churn_signals" in dict(routers._ROUTERS))


# ── админка ─────────────────────────────────────────────────────────────────


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    path = tmp_path / "churn_signals.json"
    monkeypatch.setattr(cs, "CONFIG_PATH", path)
    monkeypatch.setattr(cs, "LAST_RUN_PATH", tmp_path / "churn_signals_last_run.json")
    return path


class StatsSession:
    def __init__(self, stats=(10, 8, 6, 2, 1, 5, 2, 0), fails: bool = False) -> None:
        self.stats = stats
        self.fails = fails
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement: Any, params: Optional[dict] = None):
        sql = str(getattr(statement, "text", statement))
        if self.fails:
            raise RuntimeError("relation churn_signals does not exist")
        if "FILTER" in sql:
            return SimpleNamespace(first=lambda: self.stats)
        if "answer = 'broken'" in sql:
            from datetime import datetime, timezone

            return SimpleNamespace(all=lambda: [(7, datetime(2026, 9, 25, tzinfo=timezone.utc))])
        return SimpleNamespace(all=lambda: [(cs.OPTOUT_IDLE, 3)])

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


async def test_admin_page_both_off_by_default_and_share_from_answers(config_path):
    got = await admin.get_churn_signals.__dishka_orig_func__(_admin=None, session=StatsSession())
    assert got["config"]["check_enabled"] is False and got["config"]["idle_enabled"] is False
    stats = got["stats"]
    # 2 «не работает» из 8 ответивших — 25 %, а не 2 из 10 спрошенных.
    assert stats["check_broken_percent"] == 25
    assert stats["check_answered_percent"] == 80
    assert stats["idle_returned_percent"] == 40
    assert got["broken"] == [{"user_id": 7, "answered_at": "2026-09-25T00:00:00+00:00"}]
    assert got["optouts"] == {"check": 0, "idle": 3}
    assert got["last_run"] is None
    assert got["check_window_hours"] == cs.CHECK_WINDOW_HOURS


async def test_admin_page_opens_before_the_migration(config_path):
    session = StatsSession(fails=True)
    got = await admin.get_churn_signals.__dishka_orig_func__(_admin=None, session=session)
    assert got["stats"] == {} and got["broken"] == []
    assert session.rollbacks == 1 and session.commits == 1


async def test_admin_page_shows_last_run(config_path):
    cs.save_config({"idle_enabled": True})
    cs.save_last_run({"at": "2026-09-25T12:17:00+00:00", "panel_ok": False, "errors": 2})
    got = await admin.get_churn_signals.__dishka_orig_func__(_admin=None, session=StatsSession())
    assert got["last_run"]["panel_ok"] is False and got["last_run"]["errors"] == 2


async def test_admin_page_hides_stale_last_run_when_both_are_off(config_path):
    """Выключено всё — крон не ходит; старый итог (и «панель молчала») не выдаётся
    за текущий, даже если файл остался (например, конфиг правили руками)."""
    cs.save_last_run({"at": "2026-09-01T12:17:00+00:00", "panel_ok": False})
    got = await admin.get_churn_signals.__dishka_orig_func__(_admin=None, session=StatsSession())
    assert got["last_run"] is None


async def test_admin_save_all_off_forgets_last_run_and_on_keeps_it(config_path):
    cs.save_config({"check_enabled": True})
    cs.save_last_run({"at": "2026-09-01T12:17:00+00:00", "panel_ok": False})
    keep = admin.ChurnSignalsConfigRequest(check_enabled=True, idle_enabled=True)
    await admin.put_churn_signals.__dishka_orig_func__(body=keep, _admin=None, session=StatsSession())
    assert cs.load_last_run() is not None
    off = admin.ChurnSignalsConfigRequest(check_enabled=False, idle_enabled=False)
    await admin.put_churn_signals.__dishka_orig_func__(body=off, _admin=None, session=StatsSession())
    assert cs.load_last_run() is None
    # Включили снова — до первого нового прохода «проходов ещё не было», а не старый итог.
    await admin.put_churn_signals.__dishka_orig_func__(body=keep, _admin=None, session=StatsSession())
    got = await admin.get_churn_signals.__dishka_orig_func__(_admin=None, session=StatsSession())
    assert got["last_run"] is None


async def test_admin_save_commits_by_hand_and_normalizes(config_path):
    session = StatsSession()
    body = admin.ChurnSignalsConfigRequest(check_enabled=True, idle_days=999)
    got = await admin.put_churn_signals.__dishka_orig_func__(body=body, _admin=None, session=session)
    assert got["config"]["check_enabled"] is True and got["config"]["idle_enabled"] is False
    assert got["config"]["idle_days"] == 60
    assert session.commits == 1
    assert cs.load_config()["check_enabled"] is True


def test_admin_section_is_in_settings_permissions():
    perms = importlib.import_module("src.web.permissions")
    assert perms.section_for_path("/api/v1/admin/churn-signals") == "settings"


def test_capability_token_is_published():
    caps = importlib.import_module("src.web.cabinet_capabilities")
    assert "churn_signals" in caps.CABINET_CAPABILITIES
