"""Тревога владельцу о падении крона целиком (services/overlay_cron_guard.py).

ЧТО ЗАПИРАЕМ:
  * работало → упало: ровно одно сообщение; падает дальше — тишина;
  * упало → снова работает: одно «снова работает», дальше тишина;
  * не дошедшее сообщение повторяется при следующем переходе, а не считается сказанным;
  * сторож не роняет крон ни сбоем отправки, ни сбоем файла состояния;
  * ошибки по одному человеку, пойманные внутри крона, тревогой не считаются;
  * прогон «ничего не делал по настройке» и «таблицы ещё нет» — не поломка и не починка;
  * декоратор совместим с dishka (@inject) и не меняет имя задачи taskiq;
  * каждый наш крон по расписанию стоит под сторожем.

Telegram подделан: отправка — функция теста, в сеть ничего не уходит.
"""

import importlib
import json
from pathlib import Path
from typing import Any

import pytest
from dishka import Provider, Scope, make_async_container
from dishka.integrations.taskiq import FromDishka, inject
from sqlalchemy.exc import ProgrammingError

guard = importlib.import_module("src.infrastructure.services.overlay_cron_guard")


class Owner:
    """Вместо Telegram: что ушло владельцу. `broken` — Telegram отвечает ошибкой."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.broken = False

    async def send(self, text: str) -> None:
        if self.broken:
            raise RuntimeError("Telegram не ответил")
        self.messages.append(text)


@pytest.fixture
def owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Owner:
    fake = Owner()
    monkeypatch.setattr(guard, "STATE_PATH", tmp_path / "cron_guard_state.json")
    monkeypatch.setattr(guard, "LOCK_PATH", tmp_path / ".cron_guard_state.lock")
    monkeypatch.setattr(guard, "send_to_owner", fake.send)
    monkeypatch.delenv("CRON_GUARD_ALERTS", raising=False)
    return fake


def state() -> dict[str, Any]:
    try:
        return json.loads(guard.STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def flaky(name: str = "demo"):
    """Крон, который падает или работает по команде теста."""
    control = {"fail": False, "calls": 0}

    @guard.cron_guard(name, "Демо-крон")
    async def job() -> str:
        control["calls"] += 1
        if control["fail"]:
            raise RuntimeError("панель легла")
        return "done"

    return job, control


# ── переходы ─────────────────────────────────────────────────────────────────


async def test_worked_then_failed_sends_one_message(owner: Owner):
    job, control = flaky()
    assert await job() == "done"
    assert owner.messages == [] and state() == {}, "удачный прогон ничего не пишет"

    control["fail"] = True
    assert await job() is None, "падение не должно вылетать из крона"

    assert len(owner.messages) == 1
    assert "Демо-крон" in owner.messages[0]
    assert "не выполнилась" in owner.messages[0]
    assert "RuntimeError: панель легла" in owner.messages[0]
    assert state()["demo"]["alerted"] is True


async def test_repeated_failure_is_silent(owner: Owner):
    job, control = flaky()
    control["fail"] = True
    for _ in range(4):
        await job()

    assert len(owner.messages) == 1
    assert state()["demo"]["failures"] == 4


async def test_recovery_sends_back_to_work_once(owner: Owner):
    job, control = flaky()
    control["fail"] = True
    await job()
    await job()

    control["fail"] = False
    await job()
    await job()

    assert len(owner.messages) == 2
    assert "снова работает" in owner.messages[1]
    assert "неудачных прогонов подряд: 2" in owner.messages[1]
    assert state() == {}

    # Следующая поломка — снова одно сообщение.
    control["fail"] = True
    await job()
    assert len(owner.messages) == 3 and "не выполнилась" in owner.messages[2]


async def test_send_failure_does_not_crash_and_is_retried(owner: Owner):
    job, control = flaky()
    control["fail"] = True
    owner.broken = True

    assert await job() is None
    assert owner.messages == []
    assert state()["demo"]["alerted"] is False, "не дошедшая тревога не считается сказанной"

    owner.broken = False
    await job()
    assert len(owner.messages) == 1 and "не выполнилась" in owner.messages[0]


async def test_recovery_send_failure_is_retried(owner: Owner):
    job, control = flaky()
    control["fail"] = True
    await job()

    control["fail"] = False
    owner.broken = True
    assert await job() == "done"
    assert state()["demo"]["alerted"] is True, "«снова работает» ещё должны"

    owner.broken = False
    await job()
    assert len(owner.messages) == 2 and "снова работает" in owner.messages[1]
    assert state() == {}


async def test_state_file_failure_does_not_crash(owner: Owner, tmp_path: Path, monkeypatch):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    monkeypatch.setattr(guard, "STATE_PATH", blocker / "state.json")
    monkeypatch.setattr(guard, "LOCK_PATH", blocker / "state.lock")
    job, control = flaky()
    control["fail"] = True

    assert await job() is None
    # Состояние не записать — значит не обещать «больше не повторю»: молчим и пишем лог.
    assert owner.messages == []


# ── что считается падением ───────────────────────────────────────────────────


async def test_cron_failed_marks_handled_whole_run_failure(owner: Owner):
    @guard.cron_guard("handled", "Сама ловит")
    async def job() -> str:
        try:
            raise ConnectionError("панель не отвечает")
        except ConnectionError as exc:
            guard.cron_failed(exc)
            return "вышла штатно"

    assert await job() == "вышла штатно"
    assert len(owner.messages) == 1
    assert "ConnectionError: панель не отвечает" in owner.messages[0]


async def test_errors_per_person_are_not_failures(owner: Owner):
    @guard.cron_guard("per_person", "По людям")
    async def job() -> int:
        done = 0
        for uid in (1, 2, 3):
            try:
                if uid == 2:
                    raise ValueError(f"user_id={uid}")
                done += 1
            except ValueError:
                continue
        return done

    assert await job() == 2
    assert owner.messages == [] and state() == {}


async def test_skipped_run_neither_breaks_nor_fixes(owner: Owner):
    mode = {"value": "fail"}

    @guard.cron_guard("hourly", "Сводка")
    async def job() -> None:
        if mode["value"] == "skip":
            guard.cron_skipped()  # «сейчас не 9 утра»
            return
        if mode["value"] == "fail":
            raise RuntimeError("сводка не собралась")

    await job()
    mode["value"] = "skip"
    await job()
    await job()
    assert len(owner.messages) == 1, "пропуск по часам — не починка"
    assert state()["hourly"]["failures"] == 1

    mode["value"] = "ok"
    await job()
    assert len(owner.messages) == 2 and "снова работает" in owner.messages[1]


class _UndefinedTable(Exception):
    sqlstate = "42P01"


async def test_missing_table_is_not_a_failure(owner: Owner):
    @guard.cron_guard("rollout", "Окно выкатки")
    async def raises() -> None:
        raise ProgrammingError("SELECT 1 FROM new_table", {}, _UndefinedTable("relation does not exist"))

    @guard.cron_guard("rollout_handled", "Окно выкатки, поймано")
    async def handled() -> None:
        try:
            raise ProgrammingError("SELECT 1", {}, _UndefinedTable("relation does not exist"))
        except ProgrammingError as exc:
            guard.cron_failed(exc)

    await raises()
    await handled()
    assert owner.messages == [] and state() == {}


def test_missing_table_recognizes_asyncpg():
    asyncpg = pytest.importorskip("asyncpg")
    assert guard.missing_table(asyncpg.exceptions.UndefinedTableError("relation does not exist"))
    assert not guard.missing_table(RuntimeError("boom"))


async def test_helpers_outside_guard_do_nothing(owner: Owner):
    guard.cron_failed(RuntimeError("вне крона"))
    guard.cron_skipped()
    assert owner.messages == [] and state() == {}


async def test_disabled_by_env(owner: Owner, monkeypatch):
    monkeypatch.setenv("CRON_GUARD_ALERTS", "false")
    job, control = flaky()
    control["fail"] = True
    assert await job() is None
    assert owner.messages == [] and state() == {}


# ── совместимость с dishka и taskiq ──────────────────────────────────────────


class Dependency:
    pass


async def test_under_dishka_inject(owner: Owner):
    """Как в кронах: @inject над сторожем — зависимости приходят, падение ловится."""
    seen: list[Any] = []

    @inject(patch_module=True)
    @guard.cron_guard("dishka", "Через dishka")
    async def job(dep: FromDishka[Dependency]) -> None:
        seen.append(dep)
        raise RuntimeError("упала после получения зависимости")

    provider = Provider(scope=Scope.APP)
    instance = Dependency()
    provider.provide(lambda: instance, provides=Dependency)
    container = make_async_container(provider)
    try:
        assert await job(dishka_container=container) is None
    finally:
        await container.close()

    assert seen == [instance]
    assert len(owner.messages) == 1
    assert job.__name__ == "job"


def _our_cron_modules() -> list[str]:
    here = Path(__file__).resolve().parents[1]
    for root in (here, Path("/tmp/admin_src")):
        tasks = root / "src" / "infrastructure" / "taskiq" / "tasks"
        if tasks.is_dir():
            return sorted(
                f"src.infrastructure.taskiq.tasks.{p.stem}"
                for p in tasks.glob("*.py")
                if p.stem != "__init__"
            )
    pytest.skip("исходник admin_src не смонтирован (в CI: -v admin_src:/tmp/admin_src)")


def test_every_scheduled_cron_is_guarded():
    """Новый крон без сторожа снова молчал бы о падении — ловим это здесь."""
    from taskiq.decor import AsyncTaskiqDecoratedTask

    names: dict[str, str] = {}
    unguarded: list[str] = []
    scheduled = 0
    for module_name in _our_cron_modules():
        module = importlib.import_module(module_name)
        for attr, task in vars(module).items():
            if not isinstance(task, AsyncTaskiqDecoratedTask) or "schedule" not in task.labels:
                continue
            func = task.original_func
            if func.__module__ != module_name:
                continue  # импортирован из соседнего модуля
            scheduled += 1
            # Имя задачи — то, по которому её находят планировщик и воркер.
            assert task.task_name == f"{module_name}:{attr}", task.task_name
            inner = getattr(func, "__dishka_orig_func__", func)
            name = getattr(inner, "__cron_guard__", None)
            if name is None:
                unguarded.append(f"{module_name}:{attr}")
                continue
            assert name not in names, f"ключ «{name}» у двух кронов: {names[name]} и {attr}"
            names[name] = attr

    assert scheduled >= 20, f"нашлось всего {scheduled} кронов — обход сломался"
    assert not unguarded, f"кроны без cron_guard: {unguarded}"
