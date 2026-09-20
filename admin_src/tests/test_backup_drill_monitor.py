"""Мониторинг бэкапов знает не только «бэкап есть», но и «бэкап разворачивается».

ЗАЧЕМ. Свежий дамп, который никто не восстанавливал, — надежда, а не резервная копия.
Учение (scripts/db-restore-verify.sh) поднимает одноразовый Postgres, разворачивает
последний бэкап, прогоняет по нему миграции и считает строки — но его итог жил в логе
на сервере, и о провале никто не узнавал. Теперь итог лежит в assets, а крон
мониторинга доносит его владельцу тем же путём, что и «бэкап не делается».

Здесь заперто главное: молчание там, где учение не настроено (это не поломка, а
отсутствие функции), и тревога там, где оно упало или давно не проходило.
"""

import json
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture()
def monitor(tmp_path, monkeypatch):
    """Модуль крона без taskiq: берём из исходника только функции проверки."""
    for name in ("src.infrastructure.taskiq.tasks.backup_monitor",):
        sys.modules.pop(name, None)
    src_path = None
    for base in (Path("/opt/remnashop"), Path(__file__).resolve().parents[1]):
        candidate = base / "src/infrastructure/taskiq/tasks/backup_monitor.py"
        if candidate.exists():
            src_path = candidate
            break
    assert src_path, "не нашёл backup_monitor.py"
    src = src_path.read_text(encoding="utf-8")
    start = src.index("def _enabled()")
    end = src.index("@broker.task")
    ns: dict = {
        "os": __import__("os"),
        "json": json,
        "time": __import__("time"),
        "glob": __import__("glob").glob,
        "Path": Path,
        "ASSETS_DIR": tmp_path,
        "STATE_PATH": tmp_path / "backup_monitor.json",
        "logger": types.SimpleNamespace(warning=lambda *a, **k: None, info=lambda *a, **k: None),
        "Any": object,
    }
    exec(compile(src[start:end], "backup_monitor_pure", "exec"), ns)  # noqa: S102
    monkeypatch.setenv("RESTORE_DRILL_STATE", str(tmp_path / "restore_drill.json"))
    return ns, tmp_path


def write_drill(tmp_path: Path, **data) -> Path:
    path = tmp_path / "restore_drill.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_учения_нет_молчим(monitor):
    ns, tmp = monitor
    bad, reason = ns["_drill_check"]()
    assert bad is False
    assert reason == ""  # не настроено — не поломка


def test_учение_прошло_говорим_когда(monitor):
    ns, tmp = monitor
    write_drill(tmp, status="ok", message="восстановлен", at="2026-09-20T05:17:00+00:00")
    bad, reason = ns["_drill_check"]()
    assert bad is False
    assert "учение по бэкапу прошло" in reason


def test_учение_упало_это_тревога(monitor):
    ns, tmp = monitor
    write_drill(tmp, status="fail", message="в users строк: 0 (< 1)")
    bad, reason = ns["_drill_check"]()
    assert bad is True
    assert "УПАЛО" in reason and "users" in reason


def test_учение_давно_не_проходило(monitor, monkeypatch):
    ns, tmp = monitor
    path = write_drill(tmp, status="ok", message="ок")
    old = path.stat().st_mtime - 60 * 86400
    import os

    os.utime(path, (old, old))
    bad, reason = ns["_drill_check"]()
    assert bad is True
    assert "не проходило" in reason


def test_порог_возраста_настраивается(monitor, monkeypatch):
    ns, tmp = monitor
    path = write_drill(tmp, status="ok", message="ок")
    import os

    old = path.stat().st_mtime - 40 * 86400
    os.utime(path, (old, old))
    monkeypatch.setenv("RESTORE_DRILL_MAX_AGE_DAYS", "90")
    bad, _ = ns["_drill_check"]()
    assert bad is False


def test_битый_файл_итога_не_прячем(monitor):
    ns, tmp = monitor
    (tmp / "restore_drill.json").write_text("{не json", encoding="utf-8")
    bad, reason = ns["_drill_check"]()
    assert bad is True
    assert "нечитаем" in reason


def test_скрипт_учения_пишет_итог():
    """Сторож сторожа: без записи файла проверка выше проверяет пустоту."""
    for base in (Path("/opt/remnashop"), Path(__file__).resolve().parents[1].parent):
        script = base / "scripts/db-restore-verify.sh"
        if script.exists():
            text = script.read_text(encoding="utf-8")
            assert "write_state ok" in text, "успешное учение не отмечается"
            assert "write_state fail" in text, "провал учения не отмечается"
            assert "restore_drill.json" in text
            return
    pytest.skip("scripts/db-restore-verify.sh рядом нет (запуск внутри образа)")
