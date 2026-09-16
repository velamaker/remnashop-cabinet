"""Резервный доступ: что адаптер пускает на запись, а что отбивает.

ЭТА ПРАВКА РЕШАЕТ, ПОЛУЧИТ ЛИ ЧЕЛОВЕК ДОСТУП. Ошибка здесь не абстрактная: при
резерве с нулём трафика человек переезжает на резервный сквад и остаётся без
интернета, а при неверно закрытом переходе админ не может штатно выключить
функцию. Тестов на раздел не было ни одного.

Главное, что заперто:
  • «живой» резерв — это true в ЛЮБОМ из двух источников (сохранённый режим и
    режим ПРОЦЕССА): их запись применяется к настройкам сразу, а рантайм читает
    режим один раз на старте, поэтому расхождение — обычное дело;
  • прыжок «Включён → Выключен» закрыт, а «Слив → Выключен» ОТКРЫТ. Первый заход
    правки закрыл и его — то есть отказ советовал сделать «Слив» тому, кто его уже
    сделал, и выхода не оставалось вовсе.
"""

import importlib

import pytest

reserve = importlib.import_module("admin_reserve")
compose = importlib.import_module("compose")


def view(mode: str = "false", gb: int = 10, squad: str = "a" * 36, limited: str = "b" * 36):
    return {
        "enabled": mode == "true",
        "mode": mode,
        "reserve_gb": gb,
        "window_hours": 72,
        "window_days": 3,
        "squad_uuid": squad,
        "squad_uuid_limited": limited,
        "trial_enabled": False,
        "daily_enabled": False,
        "free_enabled": False,
    }


def plan(payload, current=None, running=""):
    return reserve._plan(payload, current or view(), running)


def refused(payload, current=None, running=""):
    with pytest.raises(compose.UpstreamError) as exc:
        plan(payload, current, running)
    return exc.value.resp.json().get("detail", "")


# ── прыжок в «Выключен» ─────────────────────────────────────────────────────

def test_direct_jump_from_enabled_to_off_is_refused():
    assert "Слив" in refused({"mode": "false"}, view("true"))


def test_drain_to_off_is_allowed_even_while_the_process_still_runs():
    """Штатный выход. Админ уже сделал «Слив» — блокировать его нельзя.

    Именно это и сломал первый заход правки: `live_now` по режиму процесса закрыл
    и «Слив → Выключен», хотя текст отказа советует ровно «Слив».
    """
    changes = plan({"mode": "false"}, view("drain"), running="true")
    assert changes  # переход принят, что-то меняем


def test_jump_to_off_is_refused_when_only_the_process_is_live():
    """Сохранено «Наблюдение», а процесс работает «Включён» — резерв выдан живым
    людям, и выключать его одним шагом так же опасно."""
    assert "Слив" in refused({"mode": "false"}, view("observe"), running="true")


def test_off_to_off_is_not_a_jump():
    assert plan({"mode": "false"}, view("false")) == {}


# ── рабочая ли настройка ────────────────────────────────────────────────────

def test_zero_traffic_is_refused_when_enabling():
    assert "1 ГБ" in refused({"mode": "true", "reserve_gb": 0}, view("false"))


def test_zero_traffic_is_refused_when_the_process_is_already_live():
    """Сохранено «Выключен», а процесс работает: 0 ГБ — это доступ без интернета.

    Прежний гейт судил только по сохранённому значению и такое пропускал.
    """
    assert "1 ГБ" in refused({"reserve_gb": 0}, view("false"), running="true")


def test_empty_squad_is_refused_when_live():
    assert refused({"squad_uuid": ""}, view("true"))
    assert refused({"squad_uuid_limited": ""}, view("true"))


def test_settings_pass_when_reserve_is_not_live():
    """Всё выключено — проверять рабочесть нечего, правки принимаем."""
    assert plan({"reserve_gb": 0}, view("false")) == {reserve._KEY_GB: 0}


def test_unknown_mode_is_refused():
    assert "неизвестен" in refused({"mode": "какой-то"}, view("false"))


# ── режим процесса ──────────────────────────────────────────────────────────

def test_mode_is_case_insensitive():
    """Их справочник хранит значение как прислали, поэтому «TRUE» — законно."""
    # Их строка настройки: значение лежит в `current`.
    defs = {reserve._KEY_MODE: {"current": "TRUE"}}
    assert reserve._mode(defs) == "true"
