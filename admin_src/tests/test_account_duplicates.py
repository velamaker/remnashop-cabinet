"""Двойники аккаунтов: что считать сломанной парой, а что нормой.

ЦЕНА ОШИБКИ ЗДЕСЬ НЕСИММЕТРИЧНА. Пропустить пару — человек с оплаченной подпиской
видит «Нет подписки» и идёт в поддержку. Но объявить парой то, что ею не является,
хуже: владельцу предложат СЛИТЬ две записи, а слияние необратимо. На боевой базе
среди безымянных записей с подпиской сломанных пар не нашлось ни одной — это
честные панельные юзеры без телеграма и записи с удалённым панельным юзером. Ровно
на этом разборе и проверяется, что мы не объявим их двойниками.

Отдельно стережём разбор идентичности: панель 3.x выдаёт ОБРАТИМЫЙ синтетический
uuid вместо настоящего, и разбор, знающий только карту `remna_identity_map`, живые
подписки счёл бы «панельный юзер не найден».
"""

import importlib
from typing import Any

import pytest

dup = importlib.import_module("src.infrastructure.services.overlay_duplicates")
v3 = importlib.import_module("src.infrastructure.services.remnawave_v3")


class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class FakeSession:
    """Отдаёт заранее заданные ответы по узнаваемому куску запроса."""

    def __init__(self, orphans: list[dict], live: list[tuple], id_map: list[tuple]) -> None:
        self.orphans, self.live, self.id_map = orphans, live, id_map

    async def execute(self, statement, *_a, **_kw):
        sql = str(statement)
        if "remna_identity_map" in sql:
            return FakeResult(self.id_map)
        if "telegram_id IS NULL" in sql:
            return FakeResult(self.orphans)
        return FakeResult(self.live)


def orphan(uid: int, remna: str, sid: int = 1) -> dict:
    return {"uid": uid, "sid": sid, "remna": remna, "expire_at": "2026-12-01"}


def panel(pid: int, telegram: Any) -> dict:
    return {"id": pid, "telegramId": telegram}


REAL_UUID = "3b0c2f1e-7a4d-4e8b-9c61-5f2a9d8e1b47"


@pytest.mark.asyncio
async def test_broken_pair_is_found():
    """Телеграм в панели есть, и на него заведена вторая запись — это пара."""
    session = FakeSession(
        orphans=[orphan(5001, REAL_UUID)],
        live=[(100000001, 5002)],
        id_map=[(REAL_UUID, 601)],
    )
    pairs = await dup.find_broken_pairs(session, [panel(601, 100000001)])
    assert len(pairs) == 1
    p = pairs[0]
    assert (p.orphan_user_id, p.live_user_id, p.telegram_id) == (5001, 5002, 100000001)
    assert p.key == "5001:5002"


@pytest.mark.asyncio
async def test_panel_user_without_telegram_is_not_a_duplicate():
    """Самый частый случай на боевой базе — подавляющее большинство. Слить их — потерять людей."""
    session = FakeSession([orphan(5001, REAL_UUID)], [(100000001, 5002)], [(REAL_UUID, 601)])
    assert await dup.find_broken_pairs(session, [panel(601, None)]) == []


@pytest.mark.asyncio
async def test_no_second_account_yet_is_not_a_duplicate():
    """Телеграм в панели есть, но человек ещё не нажал /start: синхрон свяжет сам."""
    session = FakeSession([orphan(5001, REAL_UUID)], [], [(REAL_UUID, 601)])
    assert await dup.find_broken_pairs(session, [panel(601, 100000001)]) == []


@pytest.mark.asyncio
async def test_deleted_panel_user_is_skipped():
    """Панельного юзера нет вовсе — сливать не с чем."""
    session = FakeSession([orphan(5001, REAL_UUID)], [(100000001, 5002)], [(REAL_UUID, 601)])
    assert await dup.find_broken_pairs(session, [panel(777, 100000001)]) == []


@pytest.mark.asyncio
async def test_synthetic_uuid_is_resolved_without_the_map():
    """Записи после перехода на 3.x в карте не лежат — id вынимается из uuid.

    Без этого живые подписки, заведённые после перехода, выпали бы из разбора.
    """
    synthetic = str(v3.synthetic_uuid(603))
    session = FakeSession([orphan(5003, synthetic)], [(100000001, 5002)], id_map=[])
    pairs = await dup.find_broken_pairs(session, [panel(603, 100000001)])
    assert len(pairs) == 1 and pairs[0].panel_id == 603


def test_map_wins_over_synthetic_parsing():
    """Карта — источник правды там, где она есть."""
    synthetic = str(v3.synthetic_uuid(603))
    assert dup.panel_id_from_remna(synthetic, {synthetic: 999}) == 999
    assert dup.panel_id_from_remna(synthetic, {}) == 603


def test_garbage_remna_id_does_not_raise():
    for junk in ("", None, "не-uuid", "12345"):
        assert dup.panel_id_from_remna(junk, {}) is None


def test_description_carries_what_is_needed_to_act():
    """В уведомлении должны быть обе записи и готовая команда — иначе владелец
    пойдёт выяснять это руками, ради чего всё и затевалось."""
    pair = dup.BrokenPair(5001, 5002, 100000001, 602, 601, "2026-12-01")
    text = dup.describe(pair)
    assert "#5001" in text and "#5002" in text
    assert "merge-duplicate.py --from 5001 --to 5002" in text
    assert "--apply" in text, "владелец должен знать, что сначала будет сухой прогон"
