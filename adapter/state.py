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
from datetime import datetime, timezone
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


class State:
    def __init__(self, path: str):
        self.path = path
        self._db: sqlite3.Connection | None = None
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(path, check_same_thread=False)
            db.row_factory = sqlite3.Row
            db.executescript(_SCHEMA)
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

    def add_freeze(
        self, user_key: str, subscription_id: int, remaining_seconds: int, end_date: str | None
    ) -> None:
        if self._db is None:
            return
        self._db.execute(
            "INSERT OR REPLACE INTO freezes VALUES (?, ?, ?, ?, ?)",
            (
                user_key,
                subscription_id,
                datetime.now(timezone.utc).isoformat(),
                max(0, int(remaining_seconds)),
                end_date,
            ),
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
