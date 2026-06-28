"""Вход через Telegram по OpenID Connect (новый флоу Telegram).

Аддитивно и НЕ ломает существующий вход:
  • включается ТОЛЬКО если заданы env TELEGRAM_OIDC_CLIENT_ID и
    TELEGRAM_OIDC_CLIENT_SECRET (их выдаёт @BotFather → Bot Settings → Web Login);
  • если их нет — обе ручки отдают 404, кабинет показывает классический
    Telegram Login Widget, как раньше.

Поток (Authorization Code + PKCE):
  /auth/telegram/oidc/start    → 302 на https://oauth.telegram.org/auth
  /auth/telegram/oidc/callback → обмен code→id_token, проверка подписи (JWKS),
                                 find/create пользователя по telegram_id,
                                 выдача нашей сессии (как у /auth/telegram).

redirect_uri (зарегистрировать в BotFather → Allowed URLs):
  https://<домен_кабинета>/api/auth/telegram/oidc/callback

Базовые внутренности импортируются ЛЕНИВО внутри обработчиков, чтобы смена
базового образа не уронила старт overlay — пострадает лишь сам OIDC-вход.
"""

import base64
import hashlib
import os
import secrets
import time
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
import jwt
from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from src.application.common.dao import UserDao
from src.application.common.dao.auth import AuthSessionDao
from src.application.use_cases.user.commands.web_registration import RegisterWebUser
from src.core.config import AppConfig

router = APIRouter(prefix="/auth/telegram/oidc", tags=["Public - Telegram OIDC"])

AUTH_URL = "https://oauth.telegram.org/auth"
TOKEN_URL = "https://oauth.telegram.org/token"
JWKS_URL = "https://oauth.telegram.org/.well-known/jwks.json"
ISSUER = "https://oauth.telegram.org"

TX_COOKIE = "tg_oidc_tx"  # короткоживущая подписанная кука state/nonce/PKCE
TX_TTL = 600  # 10 минут на завершение входа

# PyJWKClient сам кэширует ключи; создаём один на процесс.
_jwk_client: Optional["jwt.PyJWKClient"] = None


def _client_id() -> str:
    return (os.environ.get("TELEGRAM_OIDC_CLIENT_ID") or "").strip()


def _client_secret() -> str:
    return (os.environ.get("TELEGRAM_OIDC_CLIENT_SECRET") or "").strip()


def oidc_enabled() -> bool:
    return bool(_client_id() and _client_secret())


def _cabinet_url() -> str:
    return (os.environ.get("WEB_CABINET_URL") or "").strip().rstrip("/")


def _redirect_uri() -> str:
    return f"{_cabinet_url()}/api/auth/telegram/oidc/callback"


def _signing_secret(config: AppConfig) -> str:
    if getattr(config, "jwt_secret", None) is not None:
        return config.jwt_secret.get_secret_value()
    env = (os.environ.get("APP_JWT_SECRET") or "").strip()
    if not env:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT secret not configured",
        )
    return env


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _jwks() -> "jwt.PyJWKClient":
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = jwt.PyJWKClient(JWKS_URL)
    return _jwk_client


@router.get("/start")
@inject
async def oidc_start(config: FromDishka[AppConfig]) -> RedirectResponse:
    if not oidc_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    code_verifier = secrets.token_urlsafe(48)
    code_challenge = _b64url(hashlib.sha256(code_verifier.encode("ascii")).digest())

    tx = jwt.encode(
        {"st": state, "no": nonce, "cv": code_verifier, "exp": int(time.time()) + TX_TTL},
        _signing_secret(config),
        algorithm="HS256",
    )

    params = {
        "client_id": _client_id(),
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": "openid",
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    resp = RedirectResponse(f"{AUTH_URL}?{urlencode(params)}", status_code=302)
    resp.set_cookie(
        TX_COOKIE, tx, max_age=TX_TTL, httponly=True, secure=True, samesite="lax"
    )
    return resp


@router.get("/callback")
@inject
async def oidc_callback(
    request: Request,
    config: FromDishka[AppConfig],
    user_dao: FromDishka[UserDao],
    register_web_user: FromDishka[RegisterWebUser],
    auth_session: FromDishka[AuthSessionDao],
) -> RedirectResponse:
    if not oidc_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    # Ленивые импорты базовых внутренностей (см. модульный docstring).
    from src.application.use_cases.auth.commands.telegram import (
        TelegramAuthData,
        _get_or_create_telegram_user,
    )
    from src.web.endpoints.public._common import issue_session, set_auth_cookies

    login_url = f"{_cabinet_url()}/login"

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    tx_raw = request.cookies.get(TX_COOKIE)
    if not code or not state or not tx_raw:
        return RedirectResponse(f"{login_url}?error=telegram", status_code=302)

    try:
        tx = jwt.decode(tx_raw, _signing_secret(config), algorithms=["HS256"])
        if not secrets.compare_digest(str(tx.get("st", "")), state):
            raise ValueError("state mismatch")
        nonce = tx.get("no", "")
        code_verifier = tx["cv"]

        # 1) code → id_token
        async with httpx.AsyncClient(timeout=10) as cli:
            tok = await cli.post(
                TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": _redirect_uri(),
                    "code_verifier": code_verifier,
                },
                auth=(_client_id(), _client_secret()),
            )
        tok.raise_for_status()
        id_token = tok.json()["id_token"]

        # 2) проверка подписи и claims
        signing_key = _jwks().get_signing_key_from_jwt(id_token).key
        claims: dict[str, Any] = jwt.decode(
            id_token,
            signing_key,
            algorithms=["RS256"],
            audience=_client_id(),
            issuer=ISSUER,
        )
        if nonce and claims.get("nonce") not in (None, nonce):
            raise ValueError("nonce mismatch")

        tg_id = int(claims["sub"])
    except Exception:
        # Любой сбой обмена/проверки → мягко возвращаем на экран входа.
        return RedirectResponse(f"{login_url}?error=telegram", status_code=302)

    # 3) find/create пользователя (hash не нужен — доверяем подписи Telegram) и
    #    выдача нашей сессии — теми же кирпичами, что у классического /auth/telegram.
    data = TelegramAuthData(
        id=tg_id,
        first_name=(claims.get("given_name") or claims.get("name") or "Telegram"),
        last_name=claims.get("family_name"),
        username=claims.get("preferred_username") or claims.get("username"),
        payload={},
    )
    user = await _get_or_create_telegram_user(user_dao, register_web_user, config, data)
    access_token, refresh_token, _ = await issue_session(user, config, auth_session)

    resp = RedirectResponse(_cabinet_url() or "/", status_code=302)
    set_auth_cookies(resp, access_token, refresh_token)
    resp.delete_cookie(TX_COOKIE, httponly=True, secure=True, samesite="lax")
    return resp
