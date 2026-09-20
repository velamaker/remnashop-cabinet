"""Предупреждение «не хватит на автопродление»: тексты, ссылка и выборка.

ЗАЧЕМ ЭТО ВООБЩЕ ЕСТЬ. Автопродление списывает с рублёвого баланса, а баланс больше
нуля у троих человек из 1124. При нехватке денег крон молчит: выборка требует
`cabinet_balance > 0`, само списание отваливается на `cabinet_balance >= :amt`.
Человек с включённым тумблером уверен, что подписка продлится сама, — и остаётся без
VPN. Здесь заперто то, что должно доехать до него ЗА СУТКИ до списания.

Модуль задачи тянет taskiq и dishka, поэтому чистые функции берём из исходника, как в
соседних тестах: проверяем текст, ссылку и сам SQL выборки, а не фреймворк.
"""

import os
import re
from decimal import Decimal
from pathlib import Path

import pytest

REL = "src/infrastructure/taskiq/tasks/autopay_warning.py"


def _source() -> str:
    for base in (Path("/opt/remnashop"), Path(__file__).resolve().parents[1]):
        path = base / REL
        if path.exists():
            return path.read_text(encoding="utf-8")
    raise AssertionError(f"не нашёл {REL}")


SRC = _source()


def _pure():
    """Чистые функции модуля без импорта taskiq/dishka."""
    start = SRC.index("def _enabled()")
    end = SRC.index("async def _tell(")
    ns: dict = {"os": os, "Decimal": Decimal}
    exec(compile(SRC[start:end], "autopay_warning_pure", "exec"), ns)  # noqa: S102
    return ns


NS = _pure()


@pytest.fixture(autouse=True)
def _cabinet_url(monkeypatch):
    monkeypatch.setenv("WEB_CABINET_URL", "https://cab.example")


def test_ссылка_ведёт_на_пополнение_недостающей_суммы():
    assert NS["topup_link"](Decimal("150")) == "https://cab.example/balance?topup=150"


def test_копейки_округляются_вверх():
    # 149.01 ₽ округляем до 150: пополнение на 149 оставит списание без рубля.
    assert NS["topup_link"](Decimal("149.01")).endswith("topup=150")


def test_нулевой_недобор_не_даёт_нулевой_ссылки():
    assert NS["topup_link"](Decimal("0.4")).endswith("topup=1")


def test_без_адреса_кабинета_ссылки_нет(monkeypatch):
    monkeypatch.delenv("WEB_CABINET_URL", raising=False)
    assert NS["topup_link"](Decimal("100")) == ""


def test_текст_называет_три_суммы_и_дату():
    text = NS["message_text"](
        price=Decimal("150"),
        balance=Decimal("40"),
        short_by=Decimal("110"),
        date="23.09.2026",
        link="https://cab.example/balance?topup=110",
    )
    assert "150" in text and "40" in text and "110" in text
    assert "23.09.2026" in text
    assert "https://cab.example/balance?topup=110" in text


def test_английский_текст_тоже_с_суммами():
    text = NS["message_text"](
        price=Decimal("150"),
        balance=Decimal("0"),
        short_by=Decimal("150"),
        date="23.09.2026",
        link="",
        lang="en",
    )
    assert "Auto-renewal" in text and "150" in text
    assert text.strip().endswith(".")  # без ссылки хвоста с пустой строкой не остаётся


def test_срок_списания_и_окно_предупреждения_согласованы():
    """Окно выборки — ровно сутки перед попыткой списания, не «когда-нибудь раньше»."""
    assert "make_interval(days => :days)" in NS["CANDIDATES_SQL"] if "CANDIDATES_SQL" in NS else True
    sql = re.search(r'CANDIDATES_SQL = """(.*?)"""', SRC, re.S).group(1)
    assert "s.expire_at >= now() + make_interval(days => :days)" in sql
    assert "hours => :hours" in sql
    assert re.search(r"WARN_AHEAD_HOURS\s*=\s*24", SRC)


def test_выборка_не_трогает_отказавшихся_и_заблокированных():
    sql = re.search(r'CANDIDATES_SQL = """(.*?)"""', SRC, re.S).group(1)
    assert "notification_optouts" in sql and "'autopay_warning'" in sql
    assert "is_blocked" in sql
    assert "u.autopay_enabled = true" in sql


def test_повтор_по_тому_же_сроку_невозможен():
    sql = re.search(r'CANDIDATES_SQL = """(.*?)"""', SRC, re.S).group(1)
    assert "autopay_warnings" in sql and "w.sent_for = s.expire_at" in sql
    mark = re.search(r'MARK_SQL = """(.*?)"""', SRC, re.S).group(1)
    assert "ON CONFLICT (subscription_id) DO UPDATE" in mark


def test_цена_берётся_из_общего_расчёта_а_не_своего():
    # Иначе человеку назовут не ту сумму, которую спишут.
    assert "from src.infrastructure.services.overlay_balance import renewal_quote" in SRC
    assert "quote.price" in SRC


def test_хватает_денег_значит_молчим():
    assert "if balance >= quote.price:" in SRC
    assert "continue  # денег хватает" in SRC


def test_миграция_таблицы_на_месте():
    versions = Path(__file__).resolve().parents[1] / "src/infrastructure/database/migrations_overlay/versions"
    if not versions.exists():
        versions = Path("/opt/remnashop/src/infrastructure/database/migrations_overlay/versions")
    text = (versions / "0014_autopay_warnings.py").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS autopay_warnings" in text
    assert 'down_revision = "0013"' in text
    assert "REFERENCES subscriptions(id) ON DELETE CASCADE" in text
