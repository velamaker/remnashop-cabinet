"""Собственная память адаптера.

Заморозка — фича кабинета, а не бота: «Бедолага» о ней не знает и знать не должна.
Значит помнить, кто на паузе и сколько ему осталось, приходится самому адаптеру.

Хранилище нарочно примитивное — SQLite из стандартной библиотеки, один файл.
Папка с кодом примонтирована только для чтения, поэтому путь задаётся отдельно
(`ADAPTER_STATE`); если писать некуда, адаптер продолжает работать, просто без
заморозки — молча терять состояние хуже, чем честно её не предлагать.
"""

from __future__ import annotations

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

    def all_frozen(self) -> list[dict[str, Any]]:
        """Для авто-возобновления: кого пора будить."""
        if self._db is None:
            return []
        return [dict(r) for r in self._db.execute("SELECT * FROM freezes").fetchall()]
