"""Проход крона «сигналов до ухода»: панель, захват, отправка и сбои.

ЧТО ЗАПИРАЕМ (правила «кому» — в test_churn_signals_rules.py):
  * выключено всё — ни базы, ни панели, ни сообщений;
  * панель упала или отдала ошибку — НИКОМУ не пишем, даже тем, про кого база
    «знает» всё: молчание панели ≠ «не подключался»;
  * человека нет в ответе панели — пропускаем, а не считаем пропавшим;
  * захват строки идёт ДО отправки, и пустой RETURNING (уникальный индекс) = не шлём;
  * один человек — не больше одного сообщения за проход;
  * сбой у одного не мешает следующему, потолок на проход соблюдается;
  * «вернулся после письма» отмечается, когда панель увидела подключение позже;
  * постраничное чтение панели: ошибка на любой странице — ответа нет целиком;
  * заморозка и статус в панели не ACTIVE — мимо, статус берётся из того же ответа;
  * сообщение, которое реально уходит в Telegram: delete_after=None (дефолт DTO —
    5 секунд, и вопрос исчез бы раньше нажатия) и клавиатура своего вида — для
    КАЖДОГО вида сигнала, через настоящий отправщик задачи.

База подделана: запросы узнаются по константам модуля, строки живут в памяти.
Настоящий SQL на настоящей схеме гоняет test_churn_signals_pg.py.
"""

import importlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Optional

import pytest

cs = importlib.import_module("src.infrastructure.services.overlay_churn_signals")
task = importlib.import_module("src.infrastructure.taskiq.tasks.churn_signals")

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
UUID_A = "aaaaaaaa-1111-1111-1111-111111111111"
UUID_B = "bbbbbbbb-1111-1111-1111-111111111111"


def cfg(**over):
    return cs.normalize({**cs.DEFAULT_CONFIG, "check_enabled": True, "idle_enabled": True, **over})


def row(**over) -> dict:
    base = dict(
        user_id=7,
        telegram_id=123456,
        lang="ru",
        is_blocked=False,
        is_bot_blocked=False,
        role="USER",
        remna_uuid=UUID_A,
        is_trial=False,
        expire_at=NOW + timedelta(days=20),
        opted_out=False,
        check_done=False,
        idle_last_sent=None,
        idle_open_id=None,
        idle_open_sent_at=None,
        frozen=False,
    )
    base.update(over)
    return base


def fresh(uuid: str = UUID_A) -> dict:
    """Подключился впервые 25 часов назад — самое время спросить."""
    return {uuid: cs.PanelSeen(NOW - timedelta(hours=25), NOW - timedelta(hours=1), "ACTIVE")}


def idle(uuid: str = UUID_A, days: int = 8) -> dict:
    return {uuid: cs.PanelSeen(NOW - timedelta(days=90), NOW - timedelta(days=days), "ACTIVE")}


class _Row:
    def __init__(self, data: Any) -> None:
        self._mapping = data if isinstance(data, dict) else {}
        self._tuple = data

    def __getitem__(self, i: int) -> Any:
        return self._tuple[i]


class FakeResult:
    def __init__(self, rows: list) -> None:
        self._rows = [_Row(r) for r in rows]

    def all(self) -> list:
        return list(self._rows)

    def first(self) -> Optional[_Row]:
        return self._rows[0] if self._rows else None


class FakeSession:
    """Помнит порядок запросов; захват отдаёт id или пусто (как уникальный индекс)."""

    def __init__(self, rows: list[dict], *, taken: bool = False, fail_on: Optional[str] = None):
        self.rows = rows
        self.taken = taken
        self.fail_on = fail_on
        self.log: list[tuple[str, dict]] = []
        self.commits = 0
        self.rollbacks = 0
        self._next_id = 100

    async def execute(self, statement: Any, params: Optional[dict] = None):
        sql = str(getattr(statement, "text", statement)).strip()
        if sql.startswith("SELECT u.id"):
            name = "candidates"
        elif sql.startswith("INSERT INTO churn_signals"):
            name = "claim"
        elif "SET returned_at" in sql:
            name = "returned"
        elif sql.startswith("UPDATE churn_signals"):
            name = "result"
        else:
            name = "other"
        self.log.append((name, params or {}))
        if self.fail_on == name:
            raise RuntimeError("база отказала")
        if name == "candidates":
            return FakeResult(self.rows)
        if name == "claim":
            if self.taken:
                return FakeResult([])
            self._next_id += 1
            return FakeResult([(self._next_id,)])
        return FakeResult([])

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


async def noop_sleep(_seconds: float) -> None:
    return None


def panel_of(data: Optional[dict]):
    calls: list[int] = []

    async def fetch():
        calls.append(1)
        return data

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def kinds(session: FakeSession) -> list[str]:
    return [name for name, _ in session.log]


class Outbox:
    def __init__(self, result: str = task.TG_SENT, fail_for: Optional[set] = None) -> None:
        self.sent: list[tuple[int, str, str, int]] = []
        self.result = result
        self.fail_for = fail_for or set()

    async def __call__(self, c, kind, html, signal_id):
        self.sent.append((c.user_id, kind, html, signal_id))
        if c.user_id in self.fail_for:
            raise RuntimeError("телеграм молчит")
        return self.result


async def run(session, panel, out=None, **cfg_over):
    out = out or Outbox()
    report = await task.run_once(
        session, fetch_panel=panel, send_tg=out, now=NOW, cfg=cfg(**cfg_over), sleep=noop_sleep
    )
    return report, out


# ── выключатель и панель ────────────────────────────────────────────────────


async def test_disabled_touches_nothing():
    session = FakeSession([row()])
    panel = panel_of(fresh())
    out = Outbox()
    report = await task.run_once(
        session, fetch_panel=panel, send_tg=out, now=NOW, cfg=cs.normalize({}), sleep=noop_sleep
    )
    assert report["disabled"] is True
    assert session.log == [] and panel.calls == [] and out.sent == []


async def test_silent_panel_means_nobody_gets_a_message():
    session = FakeSession([row(), row(user_id=8, remna_uuid=UUID_B)])
    report, out = await run(session, panel_of(None))
    assert report["panel_unavailable"] is True
    assert out.sent == []
    assert kinds(session) == ["candidates"]


async def test_panel_exception_is_the_same_as_silence():
    session = FakeSession([row()])

    async def broken():
        raise RuntimeError("502 Bad Gateway")

    report, out = await run(session, broken)
    assert report["panel_unavailable"] is True and out.sent == []


async def test_person_missing_from_panel_answer_is_skipped_not_idle():
    session = FakeSession([row(remna_uuid=UUID_B)])
    report, out = await run(session, panel_of(fresh(UUID_A)))
    assert out.sent == []
    assert report["skipped_check"]["no_panel_data"] == 1
    assert report["skipped_idle"]["no_panel_data"] == 1


async def test_no_candidates_means_no_panel_call():
    session = FakeSession([])
    panel = panel_of(fresh())
    report, out = await run(session, panel)
    assert panel.calls == [] and out.sent == []


# ── «Всё работает?» ─────────────────────────────────────────────────────────


async def test_check_is_claimed_before_sending_and_result_is_written():
    session = FakeSession([row()])
    report, out = await run(session, panel_of(fresh()))
    assert report["sent"]["check"] == 1
    assert kinds(session) == ["candidates", "claim", "result"]
    user_id, kind, html, signal_id = out.sent[0]
    assert kind == "check" and "Всё работает?" in html
    # Колбэк ответа понесёт именно id захваченной строки.
    assert signal_id == 101
    claim = session.log[1][1]
    assert claim["kind"] == "check"
    assert claim["seen_at"] == NOW - timedelta(hours=25)
    result = session.log[2][1]
    assert result["id"] == 101 and result["status"] == "sent" and result["sent"] is True


async def test_claim_taken_by_someone_else_means_no_message():
    """Параллельный проход уже вставил строку — RETURNING пуст, второго вопроса нет."""
    session = FakeSession([row()], taken=True)
    report, out = await run(session, panel_of(fresh()))
    assert out.sent == []
    assert report["skipped_check"]["already_handled"] == 1
    assert kinds(session) == ["candidates", "claim"]


async def test_claim_failure_does_not_send():
    session = FakeSession([row()], fail_on="claim")
    report, out = await run(session, panel_of(fresh()))
    assert out.sent == [] and report["errors"] == 1 and session.rollbacks >= 1


async def test_failed_delivery_is_recorded_as_failed():
    session = FakeSession([row()])
    report, out = await run(session, panel_of(fresh()), Outbox(result=task.TG_BLOCKED))
    assert report["failed"] == 1 and not report["sent"]
    assert session.log[-1][1]["status"] == "failed" and session.log[-1][1]["sent"] is False


async def test_one_broken_person_does_not_stop_the_rest():
    rows = [row(user_id=1), row(user_id=2, remna_uuid=UUID_B)]
    session = FakeSession(rows)
    panel = {**fresh(UUID_A), **fresh(UUID_B)}
    report, out = await run(session, panel_of(panel), Outbox(fail_for={1}))
    assert [s[0] for s in out.sent] == [1, 2]
    assert report["sent"]["check"] == 1 and report["failed"] == 1


async def test_only_enabled_signal_is_evaluated():
    session = FakeSession([row()])
    report, out = await run(session, panel_of(idle()), check_enabled=False)
    assert [s[1] for s in out.sent] == ["idle"]
    assert not report["skipped_check"]


# ── «Давно не подключался» ──────────────────────────────────────────────────


async def test_idle_message_names_the_days_and_claims_the_online_mark():
    session = FakeSession([row()])
    report, out = await run(session, panel_of(idle(days=8)))
    assert report["sent"]["idle"] == 1
    _, kind, html, _ = out.sent[0]
    assert kind == "idle" and "8 дней назад" in html
    # Ключ повтора — отметка «последний онлайн»: пока она та же, второе письмо упрётся
    # в уникальный индекс.
    assert session.log[1][1]["seen_at"] == NOW - timedelta(days=8)


async def test_idle_disabled_writes_nothing_to_idle_people():
    session = FakeSession([row()])
    report, out = await run(session, panel_of(idle()), idle_enabled=False)
    assert out.sent == []
    assert report["skipped_check"]["too_late"] == 1


async def test_one_message_per_person_per_run():
    """Оба сигнала подходят (в теории) — уходит только вопрос."""
    session = FakeSession([row()])
    both = {UUID_A: cs.PanelSeen(NOW - timedelta(hours=25), NOW - timedelta(days=8), "ACTIVE")}
    report, out = await run(session, panel_of(both))
    assert [s[1] for s in out.sent] == ["check"]


async def test_returned_after_message_is_marked():
    sent_at = NOW - timedelta(days=5)
    session = FakeSession([row(check_done=True, idle_open_id=55, idle_open_sent_at=sent_at)])
    back = {UUID_A: cs.PanelSeen(NOW - timedelta(days=90), NOW - timedelta(hours=3), "ACTIVE")}
    report, out = await run(session, panel_of(back))
    assert report["returned"] == 1
    returned = [p for name, p in session.log if name == "returned"]
    assert returned == [{"id": 55, "returned_at": NOW - timedelta(hours=3)}]


async def test_not_returned_while_still_offline():
    sent_at = NOW - timedelta(days=1)
    session = FakeSession([row(check_done=True, idle_open_id=55, idle_open_sent_at=sent_at,
                               idle_last_sent=sent_at)])
    report, out = await run(session, panel_of(idle(days=9)))
    assert report["returned"] == 0
    assert "returned" not in kinds(session)
    # И второго «давно не был» вдогонку нет: писали вчера, кулдаун.
    assert out.sent == [] and report["skipped_idle"]["cooldown"] == 1


# ── потолок и параметры ─────────────────────────────────────────────────────


async def test_run_cap():
    rows = [row(user_id=i, remna_uuid=f"{i:08d}-1111-1111-1111-111111111111")
            for i in range(task.MAX_PER_RUN + 4)]
    panel = {}
    for r in rows:
        panel.update(fresh(r["remna_uuid"]))
    session = FakeSession(rows)
    report, out = await run(session, panel_of(panel))
    assert len(out.sent) == task.MAX_PER_RUN
    assert report["skipped_check"]["run_cap"] == 4


async def test_query_parameters():
    session = FakeSession([])
    await run(session, panel_of({}))
    params = session.log[0][1]
    assert params["optout_check"] == cs.OPTOUT_CHECK and params["optout_idle"] == cs.OPTOUT_IDLE
    assert params["now"] == NOW
    assert params["returned_since"] == NOW - timedelta(days=task.RETURN_WATCH_DAYS)


async def test_report_file_says_when_panel_was_silent():
    report = task._new_report()
    report["panel_unavailable"] = True
    saved = task._report_for_file(report, NOW)
    assert saved["panel_ok"] is False and saved["at"] == NOW.isoformat()


# ── чтение панели ───────────────────────────────────────────────────────────


class FakeRemnawave:
    def __init__(self, total: int, fail_at_offset: Optional[int] = None) -> None:
        self.total = total
        self.fail_at_offset = fail_at_offset
        self.calls: list[tuple[int, int]] = []

    async def get_all_users(self, limit: int, offset: int):
        self.calls.append((limit, offset))
        if self.fail_at_offset is not None and offset >= self.fail_at_offset:
            raise RuntimeError("панель легла на второй странице")
        end = min(self.total, offset + limit)
        return [
            SimpleNamespace(
                uuid=f"{i:08d}-AAAA-1111-1111-111111111111",
                first_connected_at=NOW - timedelta(days=3),
                online_at=(NOW - timedelta(days=1)).replace(tzinfo=None),
            )
            for i in range(offset, end)
        ]


async def test_panel_is_read_page_by_page():
    rw = FakeRemnawave(total=task.PANEL_PAGE + 3)
    seen = await task.fetch_panel_seen(rw)
    assert len(seen) == task.PANEL_PAGE + 3
    assert rw.calls == [(task.PANEL_PAGE, 0), (task.PANEL_PAGE, task.PANEL_PAGE)]
    # uuid приводится к нижнему регистру, как в нашей базе; наивное время — UTC.
    one = seen["00000000-aaaa-1111-1111-111111111111"]
    assert one.online_at.tzinfo is not None


async def test_panel_error_on_a_later_page_is_not_half_an_answer():
    rw = FakeRemnawave(total=task.PANEL_PAGE * 2, fail_at_offset=task.PANEL_PAGE)
    with pytest.raises(RuntimeError):
        await task.fetch_panel_seen(rw)


async def test_panel_status_comes_from_the_same_answer():
    """Статус — из того же get_all_users, в виде enum remnapy; отдельного запроса нет."""
    from remnapy.enums.users import UserStatus

    class Panel:
        async def get_all_users(self, limit: int, offset: int):
            return [
                SimpleNamespace(uuid=UUID_A, first_connected_at=None, online_at=None,
                                status=UserStatus.ACTIVE),
                SimpleNamespace(uuid=UUID_B, first_connected_at=None, online_at=None,
                                status=UserStatus.DISABLED),
            ]

    seen = await task.fetch_panel_seen(Panel())
    assert seen[UUID_A].status == "ACTIVE"
    assert seen[UUID_B].status == "DISABLED"


# ── заморозка и статус в панели — в настоящем проходе ───────────────────────


async def test_frozen_person_gets_nothing_even_inside_the_window():
    session = FakeSession([row(frozen=True)])
    both = {UUID_A: cs.PanelSeen(NOW - timedelta(hours=25), NOW - timedelta(days=8), "ACTIVE")}
    report, out = await run(session, panel_of(both))
    assert out.sent == []
    assert report["skipped_check"]["frozen"] == 1 and report["skipped_idle"]["frozen"] == 1
    assert "claim" not in kinds(session)


@pytest.mark.parametrize("status", ["DISABLED", "LIMITED", "EXPIRED"])
async def test_not_active_in_panel_gets_nothing(status):
    session = FakeSession([row()])
    both = {UUID_A: cs.PanelSeen(NOW - timedelta(hours=25), NOW - timedelta(days=8), status)}
    report, out = await run(session, panel_of(both))
    assert out.sent == []
    assert report["skipped_check"]["panel_inactive"] == 1
    assert report["skipped_idle"]["panel_inactive"] == 1


# ── что именно уходит в Telegram ────────────────────────────────────────────

CABINET = "https://cab.example"
SUPPORT = "https://t.me/help"


class FakeUserDao:
    def __init__(self, telegram_id: Optional[int] = 123456) -> None:
        self.telegram_id = telegram_id

    async def get_by_id(self, user_id: int):
        if self.telegram_id is None:
            return None
        return SimpleNamespace(id=user_id, telegram_id=self.telegram_id)


class FakeNotifier:
    def __init__(self, delivered: bool = True) -> None:
        self.delivered = delivered
        self.calls: list[tuple[Any, Any]] = []

    async def notify_user(self, user, payload):
        self.calls.append((user, payload))
        return object() if self.delivered else None


def _buttons(markup) -> list:
    return [b for line in markup.inline_keyboard for b in line]


def _expected_buttons(kind: str, signal_id: int) -> list[tuple]:
    if kind == cs.KIND_CHECK:
        return [
            ("✅ Всё работает", None, f"rs_sig:ok:{signal_id}"),
            ("❌ Не работает", None, f"rs_sig:bad:{signal_id}"),
            ("🔕 Не присылать такое", None, "rs_sig:off:check"),
        ]
    return [
        ("🩺 Самопроверка", f"{CABINET}/support", None),
        ("💬 Поддержка", SUPPORT, None),
        ("🔕 Не присылать такое", None, "rs_sig:off:idle"),
    ]


@pytest.mark.parametrize("kind", [cs.KIND_CHECK, cs.KIND_IDLE])
def test_payload_of_every_signal_stays_in_the_chat(kind):
    payload = task.build_payload(kind, "<b>текст</b>", 42, "ru", CABINET, SUPPORT)
    assert payload.delete_after is None, "дефолт DTO — 5 секунд: сообщение исчезло бы"
    assert payload.i18n_key == "raw-message"
    assert payload.i18n_kwargs == {"content": "<b>текст</b>"}
    assert payload.disable_default_markup is True
    got = [(b.text, b.url, b.callback_data) for b in _buttons(payload.reply_markup)]
    assert got == _expected_buttons(kind, 42)


@pytest.mark.parametrize(
    "panel, kind",
    [(fresh(), cs.KIND_CHECK), (idle(days=8), cs.KIND_IDLE)],
)
async def test_real_sender_of_the_task_sends_a_lasting_message(panel, kind):
    """Проход целиком через отправщик, который собирает задача: тест видит ровно то,
    что уйдёт в Telegram."""
    notifier = FakeNotifier()
    send_tg = task.make_send_tg(notifier, FakeUserDao(), CABINET, SUPPORT)
    session = FakeSession([row()])
    report = await task.run_once(
        session, fetch_panel=panel_of(panel), send_tg=send_tg, now=NOW, cfg=cfg(), sleep=noop_sleep
    )
    assert report["sent"][kind] == 1
    [(user, payload)] = notifier.calls
    assert user.telegram_id == 123456
    assert payload.delete_after is None
    # Колбэк ответа несёт id захваченной строки — тот, чей итог записан после отправки.
    signal_id = [params for name, params in session.log if name == "result"][0]["id"]
    got = [(b.text, b.url, b.callback_data) for b in _buttons(payload.reply_markup)]
    assert got == _expected_buttons(kind, signal_id)


async def test_real_sender_reports_undelivered_and_unknown_person():
    c = task._row_to_candidate(row())
    blocked = FakeNotifier(delivered=False)
    sender = task.make_send_tg(blocked, FakeUserDao(), CABINET, SUPPORT)
    assert await sender(c, cs.KIND_IDLE, "x", 1) == task.TG_BLOCKED
    gone = FakeNotifier()
    sender = task.make_send_tg(gone, FakeUserDao(telegram_id=None), CABINET, SUPPORT)
    assert await sender(c, cs.KIND_IDLE, "x", 1) == task.TG_BLOCKED
    assert gone.calls == []
