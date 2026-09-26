"""«Все места для устройств заняты» — правила, снимок и проход крона.

Главное, что держим тестами:
  * пишем только на ПЕРЕХОДЕ «было свободное место → заняты все», один раз за заход;
  * первый проход после включения — baseline: давно заполненным не пишем;
  * потолок прохода не теряет людей: кому не хватило очереди, напишем следующим;
  * пустой ответ панели и мусор в данных не превращаются в рассылку.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.infrastructure.services import overlay_device_full as df
from src.infrastructure.taskiq.tasks import device_full as task

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def row(uid, *, uuid=None, limit=1, tg=100, role="USER", status="ACTIVE", trial=False,
        blocked=False, bot_blocked=False, opted_out=False, lang="ru"):
    return SimpleNamespace(
        user_id=uid, telegram_id=tg, lang=lang, is_blocked=blocked, is_bot_blocked=bot_blocked,
        role=role, remna_uuid=uuid or f"uuid-{uid}", device_limit=limit, is_trial=trial,
        status=status, opted_out=opted_out,
    )


def dev(uuid, hwid):
    return {"userUuid": uuid, "hwid": hwid}


# ── правила ──────────────────────────────────────────────────────────────────


def test_заполнено_только_при_положительном_лимите():
    assert df.is_full(1, 1) and df.is_full(3, 2)
    assert not df.is_full(0, 1)
    assert not df.is_full(5, 0), "0 в панели — безлимит, заполнить нельзя"
    assert not df.is_full(5, None)


@pytest.mark.parametrize(
    "was, now_full, last, expect",
    [
        (False, True, None, "send"),
        (True, True, None, "already_full"),
        (False, False, None, "has_free_slot"),
        (False, True, (NOW - timedelta(days=2)).isoformat(), "cooldown"),
        (False, True, (NOW - timedelta(days=8)).isoformat(), "send"),
        (False, True, "мусор", "send"),
    ],
)
def test_решение(was, now_full, last, expect):
    assert df.decide(was_full=was, now_full=now_full, last_sent=last, now=NOW, cooldown_days=7) == expect


def test_кому_не_писать_вообще():
    assert df.skip_reason(row(1)) is None
    assert df.skip_reason(row(1, role="ADMIN")) == "staff"
    assert df.skip_reason(row(1, tg=None)) == "no_telegram"
    assert df.skip_reason(row(1, blocked=True)) == "blocked"
    assert df.skip_reason(row(1, bot_blocked=True)) == "blocked"
    assert df.skip_reason(row(1, status="EXPIRED")) == "not_active"
    assert df.skip_reason(row(1, trial=True)) == "trial"
    assert df.skip_reason(row(1, limit=0)) == "unlimited"
    assert df.skip_reason(row(1, opted_out=True)) == "opted_out"


def test_настройки_строгие():
    assert df.normalize(None) == df.DEFAULT_CONFIG
    assert df.normalize({"enabled": "true"})["enabled"] is False, "строка не включает рассылку"
    assert df.normalize({"enabled": True, "cooldown_days": 999})["cooldown_days"] == 90
    assert df.normalize({"cooldown_days": "x"})["cooldown_days"] == 7


def test_кнопки_и_текст():
    kb = df.keyboard("https://cab.example", can_buy=True)
    urls = [b.url for r in kb.inline_keyboard for b in r if b.url]
    assert urls == ["https://cab.example/devices?buy=1", "https://cab.example/devices"]
    assert kb.inline_keyboard[-1][0].callback_data == df.OPTOUT_CALLBACK
    # Продажа мест выключена — «Докупить» не ведёт в никуда.
    kb = df.keyboard("https://cab.example", can_buy=False)
    assert [b.url for r in kb.inline_keyboard for b in r if b.url] == ["https://cab.example/devices"]
    # Нет адреса кабинета — только отказ (пустой url Telegram не принимает).
    kb = df.keyboard("", can_buy=True)
    assert [b.callback_data for r in kb.inline_keyboard for b in r] == [df.OPTOUT_CALLBACK]
    assert "2 из 2" in df.message_text(2, 2, "ru")
    assert "2 of 2" in df.message_text(2, 2, "en")


# ── снимок ───────────────────────────────────────────────────────────────────


def test_подсчёт_устройств_по_уникальному_hwid_и_мосту_tid():
    devices = [
        dev("u1", "A"), dev("u1", "A"), dev("u1", "B"),     # повтор hwid не считаем дважды
        {"userId": 7, "hwid": "C"},                           # панель 2.8+: числовой userId
        dev("чужой", "X"), dev("u1", ""),                     # не наш и пустой hwid
    ]
    counts = task.count_devices(devices, {"u1", "u7"}, {7: "u7"})
    assert counts == {"u1": 2, "u7": 1}


def test_baseline_никому_не_пишет_и_запоминает_заполненных():
    rows = [row(1), row(2, limit=2)]
    counts = {"uuid-1": 1, "uuid-2": 1}
    to_send, full, report = task.plan_run(rows, counts, {"baselined": False}, df.DEFAULT_CONFIG, NOW)
    assert to_send == []
    assert set(full) == {"1"}
    assert report["full_now"] == 1


def test_переход_пишет_один_раз():
    rows = [row(1)]
    state = {"baselined": True, "full": {}, "sent": {}}
    to_send, full, _ = task.plan_run(rows, {"uuid-1": 1}, state, df.DEFAULT_CONFIG, NOW)
    assert [r.user_id for r, _ in to_send] == [1]
    # Следующий проход: уже заполнен — молчим.
    to_send, _, report = task.plan_run(rows, {"uuid-1": 1}, {"baselined": True, "full": full}, df.DEFAULT_CONFIG, NOW)
    assert to_send == [] and report["skipped"]["already_full"] == 1


def test_освободил_и_снова_занял_после_паузы_пишем_снова():
    rows = [row(1)]
    state = {"baselined": True, "full": {}, "sent": {"1": (NOW - timedelta(days=10)).isoformat()}}
    to_send, _, _ = task.plan_run(rows, {"uuid-1": 1}, state, df.DEFAULT_CONFIG, NOW)
    assert len(to_send) == 1
    state["sent"]["1"] = (NOW - timedelta(days=1)).isoformat()
    to_send, _, report = task.plan_run(rows, {"uuid-1": 1}, state, df.DEFAULT_CONFIG, NOW)
    assert to_send == [] and report["skipped"]["cooldown"] == 1


def test_потолок_прохода_не_теряет_людей():
    rows = [row(i) for i in range(1, 6)]
    counts = {f"uuid-{i}": 1 for i in range(1, 6)}
    to_send, full, report = task.plan_run(rows, counts, {"baselined": True}, df.DEFAULT_CONFIG, NOW, max_send=2)
    assert len(to_send) == 2 and report["queued"] == 3
    # Тех, кому не хватило очереди, в снимке «заполнен» нет — следующий проход напишет.
    to_send2, _, _ = task.plan_run(rows, counts, {"baselined": True, "full": full}, df.DEFAULT_CONFIG, NOW, max_send=10)
    assert {r.user_id for r, _ in to_send2} == {3, 4, 5}


def test_пропущенные_всё_равно_попадают_в_снимок():
    # Триальщик заполнен: не пишем, но и после перехода на тариф не поздравляем задним числом.
    rows = [row(1, trial=True)]
    _, full, report = task.plan_run(rows, {"uuid-1": 1}, {"baselined": True}, df.DEFAULT_CONFIG, NOW)
    assert "1" in full and report["skipped"]["trial"] == 1


# ── проход крона ─────────────────────────────────────────────────────────────


class FakeSession:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, *_a, **_k):
        rows = self.rows
        return SimpleNamespace(all=lambda: rows)

    async def rollback(self):
        return None


def fresh_run(at=NOW - timedelta(minutes=20)):
    """Отметка прошлого прохода: снимок свежий, сравнивать с ним можно."""
    return {"at": at.isoformat()}


def test_проход_пишет_сохраняет_и_переживает_сбой_отправки(tmp_path, monkeypatch):
    monkeypatch.setattr(df, "STATE_PATH", tmp_path / "state.json")
    df.save_state({"baselined": True, "full": {}, "sent": {}, "last_run": fresh_run()})
    sent = []

    async def send_tg(r, used):
        if r.user_id == 2:
            raise RuntimeError("телега упала")
        sent.append((r.user_id, used))
        return task.SENT

    rows = [row(1), row(2)]
    devices = [dev("uuid-1", "A"), dev("uuid-2", "B")]
    report = asyncio.run(task.run_once(FakeSession(rows), devices=devices, tid_to_uuid={},
                                       send_tg=send_tg, now=NOW, cfg=df.DEFAULT_CONFIG))
    assert sent == [(1, 1)]
    assert report["sent"] == 1 and report["failed"] == 1
    state = df.load_state()
    # Оба в снимке «заполнен»: в закрытую дверь каждые 20 минут не стучимся.
    assert set(state["full"]) == {"1", "2"}
    assert set(state["sent"]) == {"1"}


def test_битое_состояние_начинается_с_baseline(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text("{не json", "utf-8")
    monkeypatch.setattr(df, "STATE_PATH", path)
    assert df.load_state()["baselined"] is False


# ── пауза: выключили и включили, крон долго падал ─────────────────────────────


class Pass:
    """Проходы крона по одному состоянию на диске: кто сколько устройств занял сейчас."""

    def __init__(self, rows):
        self.rows = rows
        self.sent: list[int] = []

    async def _send(self, r, _used):
        self.sent.append(r.user_id)
        return task.SENT

    def run(self, counts: dict[int, int], at: datetime) -> dict:
        devices = [dev(f"uuid-{uid}", f"hw-{uid}-{i}") for uid, n in counts.items() for i in range(n)]
        return asyncio.run(task.run_once(FakeSession(self.rows), devices=devices, tid_to_uuid={},
                                         send_tg=self._send, now=at, cfg=df.DEFAULT_CONFIG))


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(df, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(df, "CONFIG_PATH", tmp_path / "device_full.json")


def test_выключили_кто_то_заполнил_включили_первый_проход_молчит(files):
    """Выключение на полчаса: по часам снимок ещё «свежий», но того, что случилось за
    паузу, в нём нет. Включение обязано сбросить baseline — иначе всем, кто заполнил
    места, пока рассылка стояла, ушло бы «только что заняли последнее место»."""
    from src.web.endpoints.admin import device_full as endpoint

    def put(enabled: bool) -> dict:
        body = endpoint.DeviceFullConfigRequest(enabled=enabled)
        return asyncio.run(endpoint.put_device_full(body, None))

    p = Pass([row(1), row(2)])
    put(True)
    assert p.run({1: 1}, NOW)["baseline"] is True
    assert p.run({1: 1}, NOW + timedelta(minutes=20))["baseline"] is False
    put(False)
    # Пока выключено, второй занял последнее место. Крон в это время не ходит.
    put(True)
    report = p.run({1: 1, 2: 1}, NOW + timedelta(minutes=40))
    assert report["baseline"] is True and p.sent == [], "рассылка по истории после включения"
    assert put(True)["baselined"] is True, "«включить» уже включённое — не сброс"
    # Дальше — как обычно: новый переход после включения пишем.
    p.rows.append(row(3))
    p.run({1: 1, 2: 1, 3: 1}, NOW + timedelta(minutes=60))
    assert p.sent == [3]


def test_устаревший_снимок_проход_baseline(files):
    """Крон падал три часа (панель лежала), отметку baseline никто не снимал: снимок с
    тех пор не обновлялся, и всех, кто заполнил места за это время, он «не видел»."""
    p = Pass([row(1), row(2)])
    df.save_state({"baselined": True, "full": {}, "sent": {}, "last_run": fresh_run(NOW)})
    report = p.run({1: 1, 2: 1}, NOW + timedelta(hours=3))
    assert report["baseline"] is True and report["stale"] is True and p.sent == []
    assert set(df.load_state()["full"]) == {"1", "2"}

    # Контроль: свежий снимок — не baseline, настоящий переход пишем.
    p.rows.append(row(3))
    report = p.run({1: 1, 2: 1, 3: 1}, NOW + timedelta(hours=3, minutes=20))
    assert report["baseline"] is False and p.sent == [3]


def test_сброс_baseline_помнит_кому_писали(files):
    df.save_state({"baselined": True, "full": {"1": NOW.isoformat()},
                   "sent": {"1": NOW.isoformat()}, "last_run": fresh_run()})
    df.save_config({"enabled": True})
    state = df.load_state()
    assert state["baselined"] is False
    assert state["sent"] == {"1": NOW.isoformat()}, "пауза между сообщениями одному человеку не обнуляется"
