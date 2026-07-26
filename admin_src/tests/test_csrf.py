"""Юнит-тесты чистой CSRF-логики (src/web/csrf.py). Закрывают аудит: «нет CSRF-тестов»."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.web.csrf import csrf_blocked  # noqa: E402

ALLOWED = {"cab.example.com"}


def _blocked(method="POST", path="/api/v1/public/sessions/logout-all", cookie=True,
             origin=None, referer=None, allowed=ALLOWED):
    return csrf_blocked(method, path, cookie, origin, referer, allowed)


def test_safe_methods_never_blocked():
    assert _blocked(method="GET", origin="https://evil.tld") is False
    assert _blocked(method="HEAD", origin="https://evil.tld") is False


def test_no_cookie_not_blocked():
    # Вебхуки/серверные интеграции без нашей куки — не трогаем (у них своя подпись).
    assert _blocked(cookie=False, origin="https://evil.tld") is False


def test_non_api_path_not_blocked():
    assert _blocked(path="/healthz", origin="https://evil.tld") is False


def test_foreign_origin_blocked():
    assert _blocked(origin="https://evil.tld") is True


def test_same_origin_allowed():
    assert _blocked(origin="https://cab.example.com") is False


def test_missing_source_blocked():
    # Кука есть, метод небезопасный, но ни Origin, ни Referer — не наш браузер → блок.
    assert _blocked(origin=None, referer=None) is True


def test_referer_fallback_allowed():
    assert _blocked(origin=None, referer="https://cab.example.com/settings") is False


def test_referer_foreign_blocked():
    assert _blocked(origin=None, referer="https://evil.tld/x") is True


def test_origin_null_treated_as_missing():
    # Origin: null (sandbox/redirect) → как отсутствие источника → блок.
    assert _blocked(origin="null") is True


def test_empty_allowlist_permits_when_source_present():
    # Пустой allowlist (нет WEB_CABINET_URL и Host) — не блокируем при наличии источника.
    assert _blocked(origin="https://anything.tld", allowed=set()) is False
    # но отсутствие источника всё равно блок
    assert _blocked(origin=None, referer=None, allowed=set()) is True
