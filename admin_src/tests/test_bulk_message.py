"""«Написать отфильтрованным»: лестница каналов и «не больше одного раза».

ЧТО ЗАПИРАЕМ.
  * Сообщение не самоудаляется (delete_after=None — однажды рассылки исчезали через
    5 секунд) и уходит тем же `raw-message`, что базовая рассылка.
  * Дошло в Telegram — ни push, ни письма; не дошло — push, затем письмо (только
    подтверждённая почта и включённая в админке почта); лента кабинета — всем при
    включённом канале.
  * Строка, застрявшая в отправке, не отправляется повторно, а становится UNKNOWN.
  * Флуд-лимит Telegram выжидается; три отказа Telegram подряд останавливают задачу,
    а ответ `None` (заблокировал бота) серией не считается.
  * Сбой записи ленты не срывает итог по человеку и не отравляет транзакцию.
  * Персонал, попавший в задачу, сообщения не получает — проверка ещё и в воркере.
  * Письма уходят через `send_branded` (адрес получателя не пишется в лог), а не
    через `send` письма с кодом.
  * «Проверить на себе» уходит от лица обычного клиента.

Запуск — внутри образа бота, как остальные тесты рядом (см. ci.yml).
"""

import importlib
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import SendMessage

from bulk_fakes import FakeStore, FakeUserDao, FakeWorld, Recorder

bulk = importlib.import_module("src.infrastructure.services.overlay_bulk")
task = importlib.import_module("src.infrastructure.taskiq.tasks.bulk_jobs")

from src.core.constants import BATCH_DELAY  # noqa: E402
from src.core.enums import Role  # noqa: E402

METHOD = SendMessage(chat_id=1, text="x")


@pytest.fixture(autouse=True)
def _brand(monkeypatch):
    # Бренд берётся из оформления установки — в тестах он свой, предсказуемый.
    monkeypatch.setattr(task, "brand_name", lambda: "VPN")
    monkeypatch.setattr(bulk, "brand_name", lambda: "VPN")


class FakeNotifier:
    def __init__(self):
        self.calls = []
        self.script = []  # что отвечать по очереди: объект, None или исключение

    async def notify_user(self, user, payload=None, i18n_key=None):
        self.calls.append((user, payload))
        answer = self.script.pop(0) if self.script else SimpleNamespace(message_id=700 + len(self.calls))
        if isinstance(answer, Exception):
            raise answer
        return answer


class PlainEmail:
    """Отправитель без overlay-патча: только `send`, как у базы."""

    def __init__(self, enabled=True):
        self._enabled = enabled
        self.sent = []
        self.plain = []

    @property
    def is_enabled(self):
        return self._enabled

    async def send(self, *, to, subject, body):
        self.plain.append((to, subject, body))
        self.sent.append((to, subject, body))


class FakeEmail(PlainEmail):
    def __init__(self, enabled=True):
        super().__init__(enabled)
        self.branded = []

    async def send_branded(self, *, to, subject, body, brand="", **opts):
        self.branded.append({"to": to, "subject": subject, "brand": brand, **opts})
        self.sent.append((to, subject, body))


class Env:
    def __init__(self, *, email_enabled=True):
        self.world = FakeWorld()
        self.store = FakeStore(self.world)
        self.notifier = FakeNotifier()
        self.email = FakeEmail(email_enabled)
        self.rec = Recorder()

    def job(self, uids, *, text="<b>Привет</b> &amp; спасибо", channels=("telegram", "cabinet", "email")):
        items = [(u, "PENDING", None, False) for u in uids]
        return self.world.add_job("message", {"text": text, "channels": list(channels)}, items)

    async def run(self, job_id):
        return await task.process_message_job(
            job_id,
            store=self.store,
            user_dao=FakeUserDao(self.world),
            notifier=self.notifier,
            email_sender=self.email,
            notify_admins=self.rec.notify_admins,
            token="w1",
            sleep=self.rec.sleep,
        )


def test_payload_never_self_deletes():
    payload = bulk.build_payload("текст")
    assert payload.delete_after is None
    assert payload.i18n_key == "raw-message"
    assert payload.i18n_kwargs == {"content": "текст"}


async def test_telegram_delivered_means_no_push_and_no_email_but_feed_written():
    env = Env()
    env.world.add_person(1, email="a@test", is_email_verified=True, has_push=True)
    job = env.job([1])
    assert await env.run(job) == "COMPLETED"
    user, payload = env.notifier.calls[0]
    assert payload.delete_after is None and payload.i18n_key == "raw-message"
    assert env.world.pushed == [] and env.email.sent == []
    assert env.world.feed == [(1, {"title": "Сообщение от VPN", "body": "Привет & спасибо", "url": "/"})]
    item = env.world.item(job, 1)
    assert item["status"] == "DONE" and item["category"] is None
    assert item["channels"] == "telegram,cabinet"
    assert item["tg_message_id"] == 701
    assert item["text_sha256"] == bulk.text_sha256("<b>Привет</b> &amp; спасибо")
    assert "Telegram 1" in env.rec.admin_messages[-1]


async def test_bot_blocked_goes_straight_to_email():
    env = Env()
    env.world.add_person(1, is_bot_blocked=True, email="a@test", is_email_verified=True)
    job = env.job([1])
    await env.run(job)
    assert env.notifier.calls == []
    assert env.email.sent == [("a@test", "Сообщение от VPN", "Привет & спасибо")]
    assert env.world.item(job, 1)["channels"] == "email,cabinet"


async def test_email_goes_through_send_branded_not_the_login_code_path():
    env = Env()
    env.world.add_person(1, telegram_id=None, email="a@test", is_email_verified=True)
    job = env.job([1])
    await env.run(job)
    assert env.email.plain == []
    assert env.email.branded == [{"to": "a@test", "subject": "Сообщение от VPN", "brand": "VPN"}]
    assert env.world.item(job, 1)["channels"] == "email,cabinet"


async def test_sender_without_overlay_patch_still_sends_through_send():
    env = Env()
    env.email = PlainEmail()
    env.world.add_person(1, telegram_id=None, email="a@test", is_email_verified=True)
    job = env.job([1])
    await env.run(job)
    assert env.email.plain == [("a@test", "Сообщение от VPN", "Привет & спасибо")]


async def test_staff_in_the_job_gets_nothing():
    """Выборка уже без персонала, но роль могли выдать, пока задача ждала очереди."""
    env = Env()
    env.world.add_person(1, role="ADMIN", email="a@test", is_email_verified=True, has_push=True)
    env.world.add_person(2)
    job = env.job([1, 2])
    assert await env.run(job) == "COMPLETED"
    item = env.world.item(job, 1)
    assert (item["status"], item["category"]) == ("SKIPPED", "STAFF")
    assert [u.id for u, _ in env.notifier.calls] == [2]
    assert env.email.sent == [] and env.world.pushed == []
    assert [uid for uid, _ in env.world.feed] == [2]


async def test_email_switched_off_in_admin_wins_over_env(monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    env = Env(email_enabled=False)
    env.world.add_person(1, telegram_id=None, email="a@test", is_email_verified=True)
    job = env.job([1])
    await env.run(job)
    assert env.email.sent == []
    item = env.world.item(job, 1)
    assert (item["status"], item["category"]) == ("DONE", "CABINET_ONLY")


async def test_unverified_email_is_not_used():
    env = Env()
    env.world.add_person(1, telegram_id=None, email="a@test", is_email_verified=False)
    job = env.job([1], channels=("telegram", "email"))
    await env.run(job)
    assert env.email.sent == []
    assert (env.world.item(job, 1)["status"], env.world.item(job, 1)["category"]) == ("SKIPPED", "NO_CHANNEL")


async def test_nothing_reachable_and_cabinet_off_is_no_channel():
    env = Env()
    env.world.add_person(1, telegram_id=None)
    job = env.job([1], channels=("telegram", "email"))
    await env.run(job)
    item = env.world.item(job, 1)
    assert (item["status"], item["category"]) == ("SKIPPED", "NO_CHANNEL")
    assert env.world.feed == []


async def test_push_when_telegram_missing():
    env = Env()
    env.world.add_person(1, telegram_id=None, has_push=True, email="a@test", is_email_verified=True)
    job = env.job([1])
    await env.run(job)
    assert [uid for uid, _ in env.world.pushed] == [1]
    assert env.email.sent == []
    assert env.world.item(job, 1)["channels"] == "push,cabinet"


async def test_stuck_sending_row_becomes_unknown_and_is_not_resent():
    env = Env()
    env.world.add_person(1)
    env.world.add_person(2)
    job = env.job([1, 2])
    env.world.items[(job, 1)]["status"] = "SENDING"
    env.world.jobs[job]["status"] = "PROCESSING"
    await env.run(job)
    item = env.world.item(job, 1)
    assert (item["status"], item["category"]) == ("UNKNOWN", "UNKNOWN")
    assert [u.id for u, _ in env.notifier.calls] == [2]
    assert env.world.jobs[job]["unknown_count"] == 1


async def test_retry_after_waits_then_delivers_once():
    env = Env()
    env.world.add_person(1)
    env.notifier.script = [TelegramRetryAfter(method=METHOD, message="Too Many Requests", retry_after=7)]
    job = env.job([1], channels=("telegram",))
    assert await env.run(job) == "COMPLETED"
    assert 7 + BATCH_DELAY in env.rec.sleeps
    assert len(env.notifier.calls) == 2
    item = env.world.item(job, 1)
    assert item["status"] == "DONE" and item["channels"] == "telegram"


async def test_three_bad_requests_in_a_row_stop_the_job():
    env = Env()
    for uid in range(1, 7):
        env.world.add_person(uid)
    bad = lambda: TelegramBadRequest(method=METHOD, message="Bad Request: can't parse entities")  # noqa: E731
    # None (заблокировал бота) посреди серии её не обнуляет и не наращивает.
    env.notifier.script = [bad(), None, bad(), bad()]
    job = env.job(list(range(1, 7)), channels=("telegram",))
    assert await env.run(job) == "ERROR"
    assert len(env.notifier.calls) == 4
    statuses = env.world.statuses(job)
    assert statuses[1] == "FAILED" and statuses[2] == "SKIPPED" and statuses[4] == "FAILED"
    assert statuses[5] == statuses[6] == "SKIPPED"
    assert env.world.item(job, 5)["category"] == "CANCELED"
    reason = env.world.jobs[job]["pause_reason"]
    assert reason == "Telegram не принимает текст: Bad Request: can't parse entities"
    assert "остановлено" in env.rec.admin_messages[-1]


async def test_feed_failure_still_records_result():
    env = Env()
    env.world.add_person(1)
    env.store.feed_fails = True
    job = env.job([1])
    await env.run(job)
    item = env.world.item(job, 1)
    assert item["status"] == "DONE" and item["channels"] == "telegram"


class _NestedFailSession:
    """Сессия, у которой вставка в ленту падает внутри точки сохранения."""

    def __init__(self):
        self.rolled_back_savepoint = False

    @asynccontextmanager
    async def begin_nested(self):
        try:
            yield
        except Exception:
            self.rolled_back_savepoint = True
            raise

    async def execute(self, *args, **kwargs):
        raise RuntimeError("relation user_notifications is broken")


async def test_sql_store_feed_error_rolls_back_only_its_savepoint():
    session = _NestedFailSession()
    ok = await bulk.BulkStore(session).record_feed(1, {"title": "t", "body": "b"})
    assert ok is False
    assert session.rolled_back_savepoint is True


async def test_test_message_goes_as_plain_client():
    world = FakeWorld()
    world.add_person(1, role="OWNER")
    admin = world.users[1]
    seen = []

    async def notify_user(user, payload):
        seen.append((user, payload))
        return SimpleNamespace(message_id=1)

    feed = []

    async def record_feed(uid, payload):
        feed.append(uid)
        return True

    result = await bulk.send_test_message(admin, "<b>x</b>", notify_user=notify_user, record_feed=record_feed)
    assert result == {"telegram": True, "reason": None}
    assert seen[0][0].role == Role.USER
    assert admin.role == Role.OWNER
    assert seen[0][1].delete_after is None
    assert feed == [1]


async def test_test_message_bad_html_is_reported():
    world = FakeWorld()
    world.add_person(1, role="OWNER")

    async def notify_user(user, payload):
        raise TelegramBadRequest(method=METHOD, message="Bad Request: can't parse entities")

    async def record_feed(uid, payload):
        return True

    with pytest.raises(bulk.TelegramRejected):
        await bulk.send_test_message(world.users[1], "<b>", notify_user=notify_user, record_feed=record_feed)

    world.add_person(2, role="OWNER", telegram_id=None)
    result = await bulk.send_test_message(world.users[2], "x", notify_user=notify_user, record_feed=record_feed)
    assert result == {"telegram": False, "reason": "no_telegram"}


def test_message_route_matches_ladder():
    base = {"telegram_id": 1, "is_bot_blocked": False, "has_push": True, "email": "a@t", "is_email_verified": True}
    all_ch = ("telegram", "cabinet", "email")
    assert bulk.message_route(base, all_ch, True) == "telegram"
    assert bulk.message_route({**base, "is_bot_blocked": True}, all_ch, True) == "push_only"
    assert bulk.message_route({**base, "telegram_id": None, "has_push": False}, all_ch, True) == "email_only"
    assert bulk.message_route({**base, "telegram_id": None, "has_push": False}, all_ch, False) == "cabinet_only"
    assert bulk.message_route({**base, "telegram_id": None, "has_push": False}, ("telegram",), True) == "unreachable"
