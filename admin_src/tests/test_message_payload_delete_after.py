"""Сторож: каждое сообщение бота из нашего кода явно решает, самоудаляться ли ему.

ЗАЧЕМ. У вендорного MessagePayloadDto `delete_after=5` по умолчанию, а наш слой
уведомлений поднимает это до 30 секунд. Любое сообщение, где delete_after не
указан, исчезает у получателя через полминуты. На этом проект спотыкался ТРИЖДЫ:
рассылки пропадали из чатов, код подарка терялся навсегда, а сообщение «Баланс
пополнен» и тревоги владельцу «оплата не прошла» / «сбой реферальной награды»
исчезали через 30 секунд (найдено 25.09.2026).

Правило простое: у каждого `MessagePayloadDto(...)` в admin_src аргумент
`delete_after` написан явно — `None`, если сообщение должно остаться, или число,
если вспышка задумана. Забытый аргумент — падение этого теста, а не тихая пропажа
сообщения у человека.
"""

import ast
from pathlib import Path

import pytest


def _source_root() -> Path | None:
    """Где лежит НАШ код отдельно от вендорного.

    В образе наш код смешан с базой в /opt/remnashop/src, а у базы сообщений без
    delete_after много — и там это задумано. Поэтому смотрим именно admin_src: рядом
    с тестами (запуск из репозитория) или смонтированный в /tmp/admin_src (CI).
    """
    here = Path(__file__).resolve().parents[1]
    for candidate in (here, Path("/tmp/admin_src")):
        if (candidate / "src").is_dir() and (candidate / "overlay_patches").is_dir():
            return candidate
    return None


ROOT = _source_root()


def _payload_calls_without_delete_after() -> list[str]:
    found = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if "__pycache__" in rel.parts or rel.parts[:1] == ("tests",):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name != "MessagePayloadDto":
                continue
            # **kwargs-распаковка — решение принимает вызывающий, не проверить статически.
            if any(k.arg is None for k in node.keywords):
                continue
            if "delete_after" not in {k.arg for k in node.keywords}:
                found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return found


@pytest.mark.skipif(ROOT is None, reason="исходник admin_src не смонтирован (в CI: -v admin_src:/tmp/admin_src)")
def test_у_каждого_сообщения_delete_after_указан_явно():
    missing = _payload_calls_without_delete_after()
    assert not missing, (
        "сообщения без явного delete_after исчезнут у получателя через 30 секунд: "
        f"{missing}"
    )
