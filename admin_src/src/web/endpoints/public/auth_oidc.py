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

from src.application.common import Remnawave
from src.application.common.dao import SubscriptionDao, UserDao
from src.application.common.dao.auth import AuthSessionDao
from src.application.common.uow import UnitOfWork
from src.application.use_cases.user.commands.web_registration import RegisterWebUser
from src.core.config import AppConfig
from src.core.enums import Role

router = APIRouter(prefix="/auth/telegram/oidc", tags=["Public - Telegram OIDC"])

AUTH_URL = "https://oauth.telegram.org/auth"
TOKEN_URL = "https://oauth.telegram.org/token"
JWKS_URL = "https://oauth.telegram.org/.well-known/jwks.json"
ISSUER = "https://oauth.telegram.org"

TX_COOKIE = "tg_oidc_tx"  # короткоживущая подписанная кука state/nonce/PKCE
TX_TTL = 600  # 10 минут на завершение входа

# PyJWKClient сам кэширует ключи; создаём один на процесс.
_jwk_client: Optional["jwt.PyJWKClient"] = None


# Креды/тумблер берём из assets/auth.json (правятся в админке кабинета), с
# фолбэком на .env. Так включение OIDC не требует переустановки/пересборки.
def _client_id() -> str:
    from src.infrastructure.services.auth_settings import telegram_oidc_client_id

    return telegram_oidc_client_id()


def _client_secret() -> str:
    from src.infrastructure.services.auth_settings import telegram_oidc_client_secret

    return telegram_oidc_client_secret()


def oidc_enabled() -> bool:
    from src.infrastructure.services.auth_settings import telegram_oidc_enabled

    return telegram_oidc_enabled()


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
            detail="JWT-секрет не настроен",
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
async def oidc_start(
    request: Request,
    config: FromDishka[AppConfig],
) -> RedirectResponse:
    if not oidc_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    code_verifier = secrets.token_urlsafe(48)
    code_challenge = _b64url(hashlib.sha256(code_verifier.encode("ascii")).digest())

    tx_payload: dict[str, Any] = {
        "st": state,
        "no": nonce,
        "cv": code_verifier,
        "exp": int(time.time()) + TX_TTL,
    }

    # Режим ПРИВЯЗКИ (?mode=link): не логинимся, а привязываем Telegram к уже
    # залогиненному аккаунту. Запоминаем id текущего пользователя в подписанной
    # tx-куке, чтобы callback знал, к кому привязывать (а не создавал новый акк).
    if (request.query_params.get("mode") or "").strip() == "link":
        from src.web.endpoints.public._common import decode_access_token

        token = request.cookies.get("access_token")
        uid: Optional[int] = None
        if token:
            try:
                uid = decode_access_token(token, _signing_secret(config))
            except Exception:
                uid = None
        if uid is None:
            # Нет валидной сессии — привязывать некого, отправляем на вход.
            return RedirectResponse(f"{_cabinet_url()}/login?error=auth", status_code=302)
        tx_payload["uid"] = uid

    tx = jwt.encode(
        tx_payload,
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
    uow: FromDishka[UnitOfWork],
    subscription_dao: FromDishka[SubscriptionDao],
    remnawave: FromDishka[Remnawave],
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

    link_uid: Optional[int] = None
    try:
        tx = jwt.decode(tx_raw, _signing_secret(config), algorithms=["HS256"])
        if not secrets.compare_digest(str(tx.get("st", "")), state):
            raise ValueError("state mismatch")
        nonce = tx.get("no", "")
        code_verifier = tx["cv"]
        if tx.get("uid") is not None:
            link_uid = int(tx["uid"])

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

        # ВАЖНО: настоящий числовой Telegram-ID лежит в claim "id", а НЕ в "sub".
        # В id_token Telegram OIDC "sub" — это отдельный идентификатор и НЕ равен
        # Telegram user id, тогда как "id" совпадает с тем, что отдаёт классический
        # Login Widget и что хранится у нас в БД (users.telegram_id). Если брать "sub",
        # get_by_telegram_id ничего не находит → создаётся новый USER → у админа/
        # владельца «слетает» роль. Поэтому берём "id" (с фолбэком на "sub").
        raw_tg_id = claims.get("id")
        if raw_tg_id is None:
            raw_tg_id = claims.get("sub")
        tg_id = int(raw_tg_id)
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

    # Режим ПРИВЯЗКИ: привязываем Telegram к текущему аккаунту, НЕ создаём новый и
    # НЕ выдаём новую сессию. Зеркалит проверки конфликтов из базового LinkTelegram
    # (verify_telegram_auth здесь не нужен — доверяем подписи id_token Telegram).
    if link_uid is not None:
        settings_url = f"{_cabinet_url()}/settings"
        current = await user_dao.get_by_id(link_uid)
        if current is None:
            return RedirectResponse(f"{login_url}?error=auth", status_code=302)

        def _back(tag: str) -> RedirectResponse:
            r = RedirectResponse(f"{settings_url}?tg={tag}", status_code=302)
            r.delete_cookie(TX_COOKIE, httponly=True, secure=True, samesite="lax")
            return r

        if current.telegram_id == tg_id:
            return _back("linked")  # уже привязан этот же Telegram — идемпотентно
        if current.telegram_id is not None:
            return _back("already")  # к аккаунту уже привязан ДРУГОЙ Telegram

        existing = await user_dao.get_by_telegram_id(tg_id)
        if existing and existing.id != current.id:
            # У этого Telegram уже есть отдельный аккаунт.
            #  • активная подписка → не трогаем (платный акк), безопасно отказываем;
            #  • истёкшая/нет → освобождаем Telegram у старого, чтобы текущий принял
            #    его как быстрый вход. Старый НЕ удаляем — только снимаем telegram_id
            #    и чистим его юзера в панели (иначе при синке коллизия rs_<tg_id>).
            other_sub = await subscription_dao.get_current(existing.id)
            if other_sub is not None and other_sub.is_active:
                return _back("conflict")

            try:
                seen: set = set()
                for s in await subscription_dao.get_all_by_user(existing.id):
                    remna_id = getattr(s, "user_remna_id", None)
                    if remna_id and remna_id not in seen:
                        seen.add(remna_id)
                        try:
                            await remnawave.delete_user(remna_id)
                        except Exception:
                            pass  # панель недоступна — привязку не валим
            except Exception:
                pass

            existing.telegram_id = None
            async with uow:
                await user_dao.update(existing)
                await uow.commit()
            # дальше — обычная привязка tg_id к current (telegram_id уже свободен)

        current.telegram_id = tg_id
        if data.username is not None:
            current.username = data.username
        # Если привязали владельческий Telegram (BOT_OWNER_ID) — сразу OWNER.
        if config.bot.owner_id and tg_id == config.bot.owner_id and current.role != Role.OWNER:
            current.role = Role.OWNER
        async with uow:
            await user_dao.update(current)
            await uow.commit()
        return _back("linked")

    user = await _get_or_create_telegram_user(user_dao, register_web_user, config, data)
    # Веб-вход не применяет правило BOT_OWNER_ID — поднимаем роль владельца здесь,
    # чтобы он не оказался обычным USER без админки (см. overlay_app startup-сверку).
    if (
        config.bot.owner_id
        and user.telegram_id == config.bot.owner_id
        and user.role != Role.OWNER
    ):
        user.role = Role.OWNER
        async with uow:
            await user_dao.update(user)
            await uow.commit()
    access_token, refresh_token, _ = await issue_session(user, config, auth_session)

    resp = RedirectResponse(_cabinet_url() or "/", status_code=302)
    set_auth_cookies(resp, access_token, refresh_token)
    resp.delete_cookie(TX_COOKIE, httponly=True, secure=True, samesite="lax")
    return resp
