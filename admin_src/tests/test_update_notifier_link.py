"""Ссылка «что нового» в уведомлении об обновлении не должна вести в 404.

Сравнение `compare/vA...vB` живёт, только пока существуют ОБА тега. 20.09.2026
четыре выпуска слили в один, теги 1.4.3–1.4.5 удалили — у того, кто сидит на таком
номере, ссылка отдавала 404. При отсутствии тега ведём на ленту изменений.
"""

from pathlib import Path

REL = "src/infrastructure/taskiq/tasks/update_notifier.py"


def _load():
    """Модуль тянет taskiq-брокер — берём из исходника только чистую функцию."""
    for base in (Path("/opt/remnashop"), Path(__file__).resolve().parents[1]):
        path = base / REL
        if path.exists():
            src = path.read_text(encoding="utf-8")
            break
    else:  # pragma: no cover
        raise AssertionError(f"не нашёл {REL}")

    start = src.index("def _parse(")
    end = src.index("@broker.task")
    body = src[start:end]
    ns: dict = {"re": __import__("re"), "os": __import__("os"), "REPO": "velamaker/remnashop-cabinet"}
    # Внутри куска есть асинхронные функции с httpx — объявление их не выполняет.
    ns["httpx"] = None
    ns["logger"] = type("L", (), {"debug": staticmethod(lambda *a, **k: None)})()
    exec(compile(body, "update_notifier_link", "exec"), ns)  # noqa: S102
    return ns


NS = _load()
whats_new_url = NS["whats_new_url"]


def test_оба_тега_есть_ведём_на_сравнение():
    url = whats_new_url("1.4.0", "v1.4.6", ["v1.4.0", "v1.4.1", "v1.4.6"])
    assert url == "https://github.com/velamaker/remnashop-cabinet/compare/v1.4.0...v1.4.6"


def test_тега_установленной_версии_нет_ведём_на_ленту():
    # Человек застрял на 1.4.4 — такого тега больше не существует.
    url = whats_new_url("1.4.4", "v1.4.6", ["v1.4.0", "v1.4.1", "v1.4.2", "v1.4.6"])
    assert url.endswith("/blob/main/CHANGELOG.md")


def test_теги_не_получены_ведём_на_ленту():
    assert whats_new_url("1.4.0", "v1.4.6", []).endswith("/blob/main/CHANGELOG.md")


def test_префикс_v_не_мешает():
    url = whats_new_url("v1.4.0", "v1.4.6", ["1.4.0", "1.4.6"])
    assert url == "https://github.com/velamaker/remnashop-cabinet/compare/v1.4.0...v1.4.6"
