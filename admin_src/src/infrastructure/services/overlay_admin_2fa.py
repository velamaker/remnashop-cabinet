"""2FA (TOTP) для админов — helpers (overlay RемнаShop).

TOTP (RFC 6238) на stdlib (без pyotp). Секрет хранится в overlay-таблице admin_2fa
(user_id, secret, enabled). Opt-in НА АДМИНА: гейт требует 2FA только у тех, кто сам
включил (никакого массового локаута). После ввода кода — подписанная кука unlock
(HMAC на jwt_secret, ~12ч), которую проверяет `_get_admin_user`.

Восстановление при потере аутентификатора: удалить строку admin_2fa в БД
(`DELETE FROM admin_2fa WHERE user_id=<id>`).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

_UNLOCK_TTL = 12 * 3600  # кука разблокировки живёт 12 часов


# ── TOTP ──────────────────────────────────────────────────────────────────────
def gen_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _hotp(secret_b32: str, counter: int) -> str:
    pad = "=" * (-len(secret_b32) % 8)
    key = base64.b32decode(secret_b32.upper() + pad)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def verify_totp(secret_b32: str, code: str, window: int = 1) -> bool:
    if not code or not str(code).strip().isdigit():
        return False
    code = str(code).strip().zfill(6)
    counter = int(time.time()) // 30
    for w in range(-window, window + 1):
        if hmac.compare_digest(_hotp(secret_b32, counter + w), code):
            return True
    return False


def otpauth_uri(secret_b32: str, account: str, issuer: str) -> str:
    label = f"{quote(issuer)}:{quote(account)}"
    return f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(issuer)}&digits=6&period=30"


# ── Подписанная кука разблокировки ────────────────────────────────────────────
def make_unlock(user_id: int, secret_key: str, ttl: int = _UNLOCK_TTL) -> str:
    exp = int(time.time()) + ttl
    payload = f"{user_id}:{exp}"
    sig = hmac.new(secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}:{sig}"


def verify_unlock(cookie: str, user_id: int, secret_key: str) -> bool:
    try:
        uid, exp, sig = cookie.split(":")
        if int(uid) != int(user_id) or int(exp) < int(time.time()):
            return False
        expect = hmac.new(secret_key.encode(), f"{uid}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]
        return hmac.compare_digest(sig, expect)
    except Exception:  # noqa: BLE001
        return False
