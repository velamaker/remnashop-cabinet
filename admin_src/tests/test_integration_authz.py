"""Интеграционные тесты авторизации против ЖИВОГО стека (auth-matrix + IDOR).

Закрывают аудит: «нет integration/auth-matrix тестов». Запускаются только при заданном
`RS_INTEGRATION_URL` (иначе skip — в обычном unit-прогоне и CI без стека не мешают).

  RS_INTEGRATION_URL=http://remnashop-web:5000  pytest tests/test_integration_authz.py

Read-only auth-matrix безопасен и против прода. IDOR-часть создаёт пользователей —
включается ТОЛЬКО с RS_INTEGRATION_ALLOW_WRITES=1 (для изолированного стенда, не прод).
"""

import os
import time

import pytest

httpx = pytest.importorskip("httpx")

BASE = os.environ.get("RS_INTEGRATION_URL")
API = "/api/v1/public"
pytestmark = pytest.mark.skipif(not BASE, reason="RS_INTEGRATION_URL не задан — интеграционный стек не поднят")

ALLOW_WRITES = os.environ.get("RS_INTEGRATION_ALLOW_WRITES") == "1"


def _client():
    # verify=False — внутренний http; follow_redirects=False — проверяем сами коды.
    return httpx.Client(base_url=BASE, timeout=15, follow_redirects=False, verify=False)


# ── Auth-matrix (read-only, безопасно и против прода) ────────────────────────
def test_protected_requires_auth():
    with _client() as c:
        assert c.get(f"{API}/sessions").status_code == 401


def test_csrf_blocks_foreign_origin_on_mutation():
    with _client() as c:
        r = c.post(
            f"{API}/sessions/logout-all",
            headers={"Cookie": "access_token=x", "Origin": "https://evil.tld"},
        )
        assert r.status_code == 403  # CSRF-middleware


def test_webhook_like_no_cookie_not_csrf_blocked():
    # Без нашей куки CSRF не срабатывает (вебхуки со своей подписью) → не 403.
    with _client() as c:
        r = c.post(f"{API}/sessions/logout-all", headers={"Origin": "https://evil.tld"})
        assert r.status_code == 401


def test_password_reset_no_user_enumeration():
    with _client() as c:
        r = c.post(f"{API}/auth/password/reset/request", json={"email": "nobody-xyz@example.com"})
        assert r.status_code == 200 and r.json().get("success") is True


def test_oidc_start_redirects():
    with _client() as c:
        assert c.get(f"{API}/auth/telegram/oidc/start").status_code in (302, 404)


def test_garbage_refresh_rejected():
    # Origin=свой, чтобы пройти CSRF и дойти до логики refresh (иначе 403 от CSRF —
    # что тоже корректно: CSRF покрывает и /auth/refresh).
    with _client() as c:
        r = c.post(
            f"{API}/auth/refresh",
            headers={"Cookie": "refresh_token=garbage-xyz", "Origin": BASE},
        )
        assert r.status_code == 401


# ── IDOR / изоляция пользователей (создаёт юзеров — только с ALLOW_WRITES) ────
def _access_token(resp) -> str:
    # Достаём access_token из Set-Cookie вручную: httpx НЕ хранит Secure-куки поверх http.
    for h in resp.headers.get_list("set-cookie"):
        if h.startswith("access_token="):
            return h.split("access_token=", 1)[1].split(";", 1)[0]
    return ""


@pytest.mark.skipif(not ALLOW_WRITES, reason="RS_INTEGRATION_ALLOW_WRITES!=1 (создаёт юзеров — не для прода)")
def test_user_sessions_are_isolated():
    ts = int(time.time())
    ua = f"idor-a-{ts}@example.com"
    ub = f"idor-b-{ts}@example.com"
    with _client() as c:
        ra = c.post(f"{API}/auth/register", json={"email": ua, "password": "Test12345!"},
                    headers={"Origin": BASE})
        rb = c.post(f"{API}/auth/register", json={"email": ub, "password": "Test12345!"},
                    headers={"Origin": BASE})
        assert ra.status_code == 201 and rb.status_code == 201
        ta, tb = _access_token(ra), _access_token(rb)
        assert ta and tb and ta != tb
        ha = {"Cookie": f"access_token={ta}"}
        hb = {"Cookie": f"access_token={tb}"}
        # /auth/me возвращает профиль ТЕКУЩЕГО юзера. Проверяем изоляцию: каждый видит
        # только свой email и НЕ видит чужого (сессия строго привязана к своему юзеру).
        aa = c.get(f"{API}/auth/me", headers=ha)
        ab = c.get(f"{API}/auth/me", headers=hb)
        assert aa.status_code == 200 and ab.status_code == 200
        assert aa.json()["email"] == ua and ab.json()["email"] == ub
        assert ua not in ab.text and ub not in aa.text  # нет перекрёстной утечки
