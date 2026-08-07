"""Собственная память адаптера.

Заморозка — фича кабинета, а не бота: «Бедолага» о ней не знает и знать не должна.
Значит помнить, кто на паузе и сколько ему осталось, приходится самому адаптеру.

Хранилище нарочно примитивное — SQLite из стандартной библиотеки, один файл.
Папка с кодом примонтирована только для чтения, поэтому путь задаётся отдельно
(`ADAPTER_STATE`); если писать некуда, адаптер продолжает работать, просто без
заморозки — молча терять состояние хуже, чем честно её не предлагать.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS freezes (
    user_key          TEXT PRIMARY KEY,
    subscription_id   INTEGER NOT NULL,
    frozen_at         TEXT    NOT NULL,
    remaining_seconds INTEGER NOT NULL,
    end_date          TEXT
);
-- Их продление умеет только ЦЕЛЫЕ сутки, а паузы бывают короче. Остаток
-- держим здесь и учитываем в следующий раз, иначе частые короткие паузы
-- дарили бы по свободному дню каждая.
CREATE TABLE IF NOT EXISTS credits (
    user_key TEXT PRIMARY KEY,
    seconds  INTEGER NOT NULL
);
-- Ожидаемые покупки («интенты»). У «Бедолаги» нет покупки подписки картой:
-- есть только пополнение баланса и списание с него. Значит нашу кнопку «купить»
-- приходится собирать из двух шагов, и между ними нужно помнить, за что именно
-- человек заплатил. Без этой памяти деньги просто осели бы на балансе.
CREATE TABLE IF NOT EXISTS intents (
    id            TEXT PRIMARY KEY,
    user_key      TEXT NOT NULL,
    kind          TEXT NOT NULL,           -- purchase | extend
    tariff_id     INTEGER NOT NULL,
    period_days   INTEGER NOT NULL,
    price_kopeks  INTEGER NOT NULL,
    topup_kopeks  INTEGER NOT NULL,
    state         TEXT NOT NULL,           -- awaiting | charging | done | reconcile
    created_at    TEXT NOT NULL,
    claimed_at    TEXT,
    balance_before INTEGER NOT NULL DEFAULT 0
);
-- Настройки САМОГО кабинета, которых у чужого бота нет и быть не может:
-- показывать ли блок «Статус сервиса», кому он виден, какие ноды в нём.
-- У нашего бэкенда это файл в assets/, здесь — своя строка на ключ.
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- Расписание фоновых задач (scheduler.py). Помним время НАЧАЛА последнего
-- запуска: контейнер перезапускают при каждом обновлении, и расписание, живущее
-- в памяти процесса, означало бы либо потерянную ночную рассылку, либо вторую
-- такую же через минуту после старта.
CREATE TABLE IF NOT EXISTS schedule (
    name        TEXT PRIMARY KEY,
    last_run    TEXT,                      -- NULL = ни разу не запускалась
    finished_at TEXT,
    last_status TEXT,                      -- ok | error
    last_error  TEXT,
    runs        INTEGER NOT NULL DEFAULT 0,
    failures    INTEGER NOT NULL DEFAULT 0
);
"""

# Захват записи на время снятия с паузы (см. claim_unfreeze). Отдельно от _SCHEMA:
# на уже созданной базе таблица есть, а колонки в ней нет.
_MIGRATIONS = ("ALTER TABLE freezes ADD COLUMN claimed_at TEXT",)

# Дольше этого захват считается брошенным (адаптер перезапустили посреди снятия).
_CLAIM_TTL = 120


class State:
    def __init__(self, path: str):
        self.path = path
        self._db: sqlite3.Connection | None = None
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(path, check_same_thread=False)
            db.row_factory = sqlite3.Row
            db.executescript(_SCHEMA)
            for statement in _MIGRATIONS:
                try:
                    db.execute(statement)
                except sqlite3.OperationalError:
                    pass  # колонка уже есть — обычное состояние после первого запуска
            db.commit()
            self._db = db
        except Exception:  # noqa: BLE001 — без памяти живём, но без заморозки
            self._db = None

    @property
    def available(self) -> bool:
        return self._db is not None

    def get_freeze(self, user_key: str) -> dict[str, Any] | None:
        if self._db is None:
            return None
        row = self._db.execute(
            "SELECT * FROM freezes WHERE user_key = ?", (user_key,)
        ).fetchone()
        return dict(row) if row else None

    def claim_freeze(
        self, user_key: str, subscription_id: int, remaining_seconds: int, end_date: str | None
    ) -> bool:
        """Занимает место под паузу ДО обращения к боту. False — уже занято.

        Проверка «а нет ли записи» отдельным запросом от гонки не спасает: два
        параллельных запроса (двойной клик) успевают прочитать пустоту оба.
        Здесь решает сама база — вставка по первичному ключу либо прошла, либо нет.
        """
        if self._db is None:
            return False
        cur = self._db.execute(
            "INSERT OR IGNORE INTO freezes "
            "(user_key, subscription_id, frozen_at, remaining_seconds, end_date, claimed_at) "
            "VALUES (?, ?, ?, ?, ?, NULL)",
            (
                user_key,
                subscription_id,
                datetime.now(timezone.utc).isoformat(),
                max(0, int(remaining_seconds)),
                end_date,
            ),
        )
        self._db.commit()
        return cur.rowcount == 1

    def claim_unfreeze(self, user_key: str) -> dict[str, Any] | None:
        """Забирает запись под снятие с паузы. None — не на паузе или уже снимают.

        Продление у бота идёт целыми сутками, поэтому два параллельных снятия
        сделали бы ДВА продления — то есть подарили день. Захват атомарен: строку
        получает тот, чей UPDATE изменил ровно одну строку. Брошенный захват
        (адаптер перезапустили в середине) через _CLAIM_TTL освобождается сам.
        """
        if self._db is None:
            return None
        now = datetime.now(timezone.utc)
        # Метки времени пишем одним форматом (ISO, UTC), поэтому сравнение строк
        # здесь равносильно сравнению дат — и не зависит от версии SQLite.
        stale = (now - timedelta(seconds=_CLAIM_TTL)).isoformat()
        cur = self._db.execute(
            "UPDATE freezes SET claimed_at = ? WHERE user_key = ? AND ("
            "  claimed_at IS NULL OR claimed_at < ?"
            ")",
            (now.isoformat(), user_key, stale),
        )
        self._db.commit()
        if cur.rowcount != 1:
            return None
        return self.get_freeze(user_key)

    def release_claim(self, user_key: str) -> None:
        """Снятие не удалось — отпускаем запись, человек сможет повторить."""
        if self._db is None:
            return
        self._db.execute(
            "UPDATE freezes SET claimed_at = NULL WHERE user_key = ?", (user_key,)
        )
        self._db.commit()

    def drop_freeze(self, user_key: str) -> None:
        if self._db is None:
            return
        self._db.execute("DELETE FROM freezes WHERE user_key = ?", (user_key,))
        self._db.commit()

    def credit(self, user_key: str) -> int:
        if self._db is None:
            return 0
        row = self._db.execute(
            "SELECT seconds FROM credits WHERE user_key = ?", (user_key,)
        ).fetchone()
        return int(row["seconds"]) if row else 0

    def set_credit(self, user_key: str, seconds: int) -> None:
        if self._db is None:
            return
        self._db.execute(
            "INSERT OR REPLACE INTO credits VALUES (?, ?)", (user_key, int(seconds))
        )
        self._db.commit()

    def get_setting(self, key: str, default: Any = None) -> Any:
        """Настройка кабинета. Хранится строкой JSON: формы у настроек разные,
        а заводить по таблице на каждую — лишнее."""
        if self._db is None:
            return default
        row = self._db.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except ValueError:
            return default

    def set_setting(self, key: str, value: Any) -> None:
        if self._db is None:
            return
        self._db.execute(
            "INSERT OR REPLACE INTO settings VALUES (?, ?)",
            (key, json.dumps(value, ensure_ascii=False)),
        )
        self._db.commit()

    # --- ожидаемые покупки ---------------------------------------------------

    def add_intent(self, intent: dict[str, Any]) -> None:
        if self._db is None:
            return
        self._db.execute(
            "INSERT OR REPLACE INTO intents "
            "(id, user_key, kind, tariff_id, period_days, price_kopeks, topup_kopeks,"
            " state, created_at, claimed_at, balance_before) "
            "VALUES (:id, :user_key, :kind, :tariff_id, :period_days, :price_kopeks,"
            " :topup_kopeks, :state, :created_at, NULL, :balance_before)",
            intent,
        )
        self._db.commit()

    def open_intents(self, user_key: str) -> list[dict[str, Any]]:
        """Ожидающие оплаты покупки. Старьё не отдаём: их окно ожидания платежа —
        сутки, дальше человек скорее просто передумал."""
        if self._db is None:
            return []
        old = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        # Брошенный захват: адаптер (или запрос) прервали посреди списания. Такую
        # покупку нужно вернуть в работу, иначе оплаченный тариф не включится
        # никогда. Повторить безопасно — перед списанием идёт сверка `_already_charged`.
        stale = (datetime.now(timezone.utc) - timedelta(seconds=_CLAIM_TTL)).isoformat()
        rows = self._db.execute(
            "SELECT * FROM intents WHERE user_key = ? AND created_at > ? AND ("
            "  state IN ('awaiting','reconcile')"
            "  OR (state = 'charging' AND (claimed_at IS NULL OR claimed_at < ?))"
            ") ORDER BY created_at",
            (user_key, old, stale),
        ).fetchall()
        return [dict(r) for r in rows]

    def any_open_intents(self) -> bool:
        """Есть ли вообще незакрытые покупки. Дешёвая проверка перед тем, как
        спрашивать у бота «кто это»: у подавляющего большинства открытий экрана
        ждать нечего, и лишний запрос к боту на каждый заход не нужен."""
        if self._db is None:
            return False
        old = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        row = self._db.execute(
            "SELECT 1 FROM intents WHERE state != 'done' AND created_at > ? LIMIT 1",
            (old,),
        ).fetchone()
        return row is not None

    def claim_intent(self, intent_id: str) -> bool:
        """Забирает покупку под списание. False — её уже кто-то забрал.

        Списание у них не идемпотентно: два параллельных прохода (ленивый и
        фоновый) списали бы деньги дважды. Решает база: строку получает тот, чей
        UPDATE изменил ровно одну.
        """
        if self._db is None:
            return False
        now = datetime.now(timezone.utc)
        stale = (now - timedelta(seconds=_CLAIM_TTL)).isoformat()
        cur = self._db.execute(
            "UPDATE intents SET state='charging', claimed_at=? WHERE id=? AND ("
            "  state IN ('awaiting','reconcile','charging')"
            "  AND (claimed_at IS NULL OR claimed_at < ?)"
            ")",
            (now.isoformat(), intent_id, stale),
        )
        self._db.commit()
        return cur.rowcount == 1

    def cancel_awaiting(self, user_key: str) -> int:
        """Снимает прежние неоплаченные покупки этого человека.

        Иначе так: человек нажал «купить» тариф подешевле, передумал, выбрал
        другой и оплатил его — а деньги, увидев пополнение, ушли бы на первую,
        забытую покупку. Начал новую — прежняя больше не ждёт. Списание, уже
        запущенное ('charging') или с неизвестным исходом ('reconcile'), не трогаем.
        """
        if self._db is None:
            return 0
        cur = self._db.execute(
            "UPDATE intents SET state='cancelled' WHERE user_key=? AND state='awaiting'",
            (user_key,),
        )
        self._db.commit()
        return cur.rowcount

    def set_intent_state(self, intent_id: str, state: str) -> None:
        if self._db is None:
            return
        self._db.execute(
            "UPDATE intents SET state=?, claimed_at=NULL WHERE id=?", (state, intent_id)
        )
        self._db.commit()

    def all_frozen(self) -> list[dict[str, Any]]:
        """Для авто-возобновления: кого пора будить."""
        if self._db is None:
            return []
        return [dict(r) for r in self._db.execute("SELECT * FROM freezes").fetchall()]

    # --- расписание фоновых задач --------------------------------------------

    def claim_task_run(
        self,
        name: str,
        interval_seconds: float,
        run_at_start: bool = True,
        now: datetime | None = None,
    ) -> bool:
        """Забирает очередной запуск задачи. False — ещё не время или уже забрали.

        Решает база, а не проверка в коде: «прочитать last_run, сравнить, потом
        записать» — это гонка, в которой второй проход (перезапуск на лету, две
        копии процесса) успевает прочитать старое значение и запустить рассылку
        второй раз. Здесь запуск достаётся тому, чей UPDATE изменил одну строку.
        """
        if self._db is None:
            return False
        now = now or datetime.now(timezone.utc)
        stamp = now.isoformat()
        interval = max(0.001, float(interval_seconds))
        due = (now - timedelta(seconds=interval)).isoformat()
        # Первое знакомство с задачей. run_at_start=False заводит её «как будто
        # только что отработала»: свежая установка иначе выстрелила бы рассылкой
        # в момент запуска.
        self._db.execute(
            "INSERT OR IGNORE INTO schedule (name, last_run) VALUES (?, ?)",
            (name, None if run_at_start else stamp),
        )
        # Метку ставим ДО работы, а не после: оборванный посередине запуск не
        # должен повториться сразу после перезапуска контейнера — он повторится
        # в свой черёд. Ветка «last_run > stamp» на случай съехавших назад часов:
        # без неё задача замерла бы, пока время не догонит запись.
        cur = self._db.execute(
            "UPDATE schedule SET last_run = ? WHERE name = ? AND ("
            "  last_run IS NULL OR last_run <= ? OR last_run > ?"
            ")",
            (stamp, name, due, stamp),
        )
        self._db.commit()
        return cur.rowcount == 1

    def finish_task_run(self, name: str, ok: bool, error: str | None = None) -> None:
        """Итог запуска. Нужен людям, а не расписанию: по этим полям видно, что
        задача не просто «давно не бегала», а падает каждый раз."""
        if self._db is None:
            return
        self._db.execute(
            "UPDATE schedule SET finished_at = ?, last_status = ?, last_error = ?,"
            " runs = runs + 1, failures = failures + ? WHERE name = ?",
            (
                datetime.now(timezone.utc).isoformat(),
                "ok" if ok else "error",
                None if ok else (error or "")[:500],
                0 if ok else 1,
                name,
            ),
        )
        self._db.commit()

    def task_runs(self) -> list[dict[str, Any]]:
        """Строки расписания: из них считается время до ближайшего запуска."""
        if self._db is None:
            return []
        return [dict(r) for r in self._db.execute("SELECT * FROM schedule").fetchall()]
