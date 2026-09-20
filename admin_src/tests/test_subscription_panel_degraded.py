"""Моргнула панель — это не «у вас нет подписки».

ЗАЧЕМ. Расход трафика и последний онлайн живут только в Remnawave, и ручка
/subscription/current ходит в панель за ними. Раньше исключение оттуда (таймаут,
рестарт панели, blackhole на исходящем IP — всё это в истории проекта уже случалось)
улетало пятисоткой, а кабинет трактует неудачу запроса как ОТСУТСТВИЕ подписки: у
платящего человека на главной появлялось «подписки нет», а самодиагностика начинала
с «оформите тариф».

Здесь заперто, что молчание панели деградирует ответ (нет расхода и онлайна), а не
отменяет подписку.
"""

import re
from pathlib import Path

REL = "overlay_patches/public_subscription.py"


def _source() -> str:
    for base in (
        Path(__file__).resolve().parents[1],  # admin_src/
        Path("/opt/remnashop/admin_src"),
    ):
        path = base / REL
        if path.exists():
            return path.read_text(encoding="utf-8")
    raise AssertionError(f"не нашёл {REL}")


SRC = _source()


def _handler() -> str:
    start = SRC.index("async def get_current_subscription(")
    end = SRC.index("@router.get(\"/devices\"", start)
    return SRC[start:end]


def test_запрос_в_панель_обёрнут():
    body = _handler()
    assert "try:" in body, "вызов панели не обёрнут"
    assert "remna_user = await remnawave.get_user_by_uuid" in body
    assert "except Exception" in body


def test_при_сбое_подписка_всё_равно_возвращается():
    body = _handler()
    # После except остаётся путь к сборке ответа, а не raise/return None.
    tail = body[body.index("except Exception") :]
    assert "remna_user = None" in tail
    assert "return SubscriptionInfoResponse(" in tail
    assert "raise" not in tail.split("return SubscriptionInfoResponse(")[0]


def test_поля_панели_становятся_пустыми_а_не_нулями():
    """Ноль расхода — это факт «не тратил», а не «не знаем»: врать нельзя."""
    body = _handler()
    for field in ("used_traffic_bytes", "lifetime_used_traffic_bytes", "online_at"):
        assert re.search(rf"{field}=remna_user\.{field} if remna_user else None", body), field


def test_сбой_записывается_в_лог():
    body = _handler()
    assert "logger.warning" in body
    assert "панель не ответила" in body


def test_отсутствие_подписки_по_прежнему_none():
    body = _handler()
    head = body[: body.index("try:")]
    assert "if not current_subscription:" in head and "return None" in head
