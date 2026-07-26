"""Юнит-тесты security-критичных чистых overlay-функций (2FA/TOTP + подпись unlock).

Покрывают код, относящийся к находкам аудита C-01/H-02 (обход и fail-open 2FA): сам
TOTP-движок и подписанную куку разблокировки. Не требуют базового образа/БД — stdlib.

Запуск: pytest admin_src/tests/test_overlay_2fa.py
(интеграционные auth-matrix/IDOR/платёжные тесты — отдельный слой, нужна upstream-инфра.)
"""

import sys
import time
from pathlib import Path

# Делаем overlay-пакет `src` импортируемым без установки базового образа.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.infrastructure.services.overlay_admin_2fa import (  # noqa: E402
    _hotp,
    gen_secret,
    make_unlock,
    verify_totp,
    verify_unlock,
)


def _code_now(secret: str, skew: int = 0) -> str:
    return _hotp(secret, int(time.time()) // 30 + skew)


def test_totp_accepts_current_code():
    s = gen_secret()
    assert verify_totp(s, _code_now(s)) is True


def test_totp_accepts_adjacent_window():
    s = gen_secret()
    assert verify_totp(s, _code_now(s, -1)) is True
    assert verify_totp(s, _code_now(s, +1)) is True


def test_totp_rejects_out_of_window():
    s = gen_secret()
    assert verify_totp(s, _code_now(s, +5)) is False


def test_totp_rejects_garbage():
    s = gen_secret()
    assert verify_totp(s, "") is False
    assert verify_totp(s, "abcdef") is False
    assert verify_totp(s, "000000") is False  # почти наверняка не совпадёт с текущим


def test_unlock_roundtrip_valid():
    key = "secret-key"
    cookie = make_unlock(42, key)
    assert verify_unlock(cookie, 42, key) is True


def test_unlock_rejects_wrong_user():
    key = "secret-key"
    cookie = make_unlock(42, key)
    assert verify_unlock(cookie, 43, key) is False  # кука другого админа не подходит


def test_unlock_rejects_wrong_key():
    cookie = make_unlock(42, "key-A")
    assert verify_unlock(cookie, 42, "key-B") is False  # смена jwt_secret инвалидирует


def test_unlock_rejects_expired():
    key = "secret-key"
    cookie = make_unlock(42, key, ttl=-1)  # уже истёкшая
    assert verify_unlock(cookie, 42, key) is False


def test_unlock_rejects_tampered_signature():
    key = "secret-key"
    cookie = make_unlock(42, key)
    uid, exp, _sig = cookie.split(":")
    forged = f"{uid}:{exp}:{'0' * 32}"
    assert verify_unlock(forged, 42, key) is False


def test_unlock_rejects_tampered_expiry():
    key = "secret-key"
    uid, _exp, sig = make_unlock(42, key).split(":")
    forged = f"{uid}:{int(time.time()) + 999999}:{sig}"  # продлить срок, не пересчитав подпись
    assert verify_unlock(forged, 42, key) is False
