"""Вход через Telegram возвращает туда, откуда пришли, и никуда больше.

ЗАЧЕМ. Новичок со ссылки-подарка идёт «войти через Telegram» и должен вернуться к
подарку. Раньше вход по OIDC всегда уводил на главную кабинета: адрес возврата
терялся по дороге через Telegram, и код подарка пропадал.

Вторая половина не менее важная: адрес возврата приходит из адресной строки, и
подсунуть туда чужой сайт — классическая дыра входа. Правило проверки здесь то же,
что в кабинете (cabinet/src/lib/nav.ts): парсер адреса молча выкидывает управляющие
символы, поэтому «/\\t/evil.com» иначе превратился бы в «//evil.com» — чужой хост.
"""

import importlib

import pytest

oidc = importlib.import_module("src.web.endpoints.public.auth_oidc")


class TestКудаВозвращаем:
    @pytest.mark.parametrize(
        "path",
        [
            "/billing?promo=GIFT-" + "A" * 32,
            "/subscription",
            "/info#faq",
            "/billing?a=1&b=2#x",
        ],
    )
    def test_обычный_внутренний_путь_проходит(self, path):
        assert oidc.safe_next_path(path) == path

    @pytest.mark.parametrize(
        "raw",
        [
            "//evil.com",            # адрес чужого хоста
            "/\\evil.com",           # обратный слэш роутер читает как слэш
            "/\t/evil.com",          # табуляцию парсер адреса выкидывает молча
            "/\n/evil.com",
            "/\r/evil.com",
            "/\x00/evil.com",
            "/ /evil.com",
            "/%2F/evil.com",         # закодированный разделитель
            "/%5C/evil.com",
            "/.//evil.com",          # сегмент «.» схлопывается
            "/..//evil.com",
            "/%2e//evil.com",
            "https://evil.com",
            "javascript:alert(1)",
            "evil.com",
            "",
            None,
        ],
    )
    def test_чужое_и_подозрительное_не_проходит(self, raw):
        assert oidc.safe_next_path(raw) == "", f"пропущено: {raw!r}"

    def test_пустой_путь_значит_на_главную(self):
        # Пустая строка — сигнал «адреса нет»: callback склеит только адрес кабинета.
        assert oidc.safe_next_path(None) == ""


class TestПравилоОдноСКабинетом:
    def test_те_же_запреты_что_на_фронте(self):
        """Если правило разъедется с cabinet/src/lib/nav.ts, дыра вернётся с одной из сторон."""
        from pathlib import Path

        candidates = [
            Path("/opt/remnashop/cabinet/src/lib/nav.ts"),
            Path(__file__).resolve().parents[2] / "cabinet" / "src" / "lib" / "nav.ts",
        ]
        source = next((p.read_text(encoding="utf-8") for p in candidates if p.exists()), None)
        if source is None:
            pytest.skip("рядом нет кабинета (запуск внутри образа бота)")
        assert "safeInternalPath" in source
        # Обе стороны обязаны резать управляющие символы и двойной ведущий разделитель.
        assert "u0000" in source or "\\x00" in source
        assert "%2f" in source.lower()
