"""Overlay-обёртка над базовой точкой входа.

Раньше overlay копировал обвязку base (`src/__main__.py`) — при изменении этой
обвязки в новой версии base мы бы не подхватили её автоматически. Здесь base
`application()` вызывается КАК ЕСТЬ (не копируется), а overlay только ДОБАВЛЯЕТ:
  • admin-роутер (`/api/v1/admin/*`);
  • дополнительные public-ручки (`/api/v1/public/*`), которых нет в base;
  • идемпотентное создание таблиц поддержки в обёрнутом lifespan (вне alembic).

Точку входа uvicorn на этот модуль переключает sed-патч в корневом Dockerfile
(`src.__main__:application` → `src.overlay_app:application`); если патч не
сматчится на новом base — билд падает сразу (а не молча в рантайме).

Развязка от alembic и примирение alembic_version — см. src/overlay_bootstrap.py.
"""

import asyncio
import hashlib
import os
import shutil
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from dishka import AsyncContainer, Scope
from fastapi import APIRouter, FastAPI, Request
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.__main__ import application as _base_application
from src.core.config import AppConfig
from src.core.constants import API_V1

# overlay-роутеры
from src.web.endpoints.admin import router as admin_router
from src.web.endpoints.public.appearance import router as appearance_router
from src.web.endpoints.public.apps import router as apps_router
from src.web.endpoints.public.auth_oidc import router as auth_oidc_router
from src.web.endpoints.public.balance import router as balance_router
from src.web.endpoints.public.email_manage import router as email_manage_router
from src.web.endpoints.public.info_content import router as info_content_router
from src.web.endpoints.public.me_role import router as me_role_router
from src.web.endpoints.public.password_reset import router as password_reset_router
from src.web.endpoints.public.server_stats import router as server_stats_router
from src.web.endpoints.public.traffic_history import router as traffic_history_router
from src.web.endpoints.public.service_status import router as service_status_router
from src.web.endpoints.public.status_page import router as status_page_router
from src.web.endpoints.public.set_password import router as set_password_router
from src.web.endpoints.public.support import router as support_router
from src.web.endpoints.public.push import router as push_router
from src.web.endpoints.public.promocode import router as promocode_router
from src.web.endpoints.public.referral_stats import router as referral_stats_router
from src.web.endpoints.public.trial_discount import router as trial_discount_public_router
from src.web.endpoints.public.promo_banner import router as promo_banner_public_router
from src.web.endpoints.public.freeze import router as freeze_public_router
from src.web.endpoints.public.gift import router as gift_public_router
from src.web.endpoints.public.sessions import router as sessions_public_router
from src.web.endpoints.public.account import router as account_public_router
from src.web.endpoints.public.notifications import router as notifications_public_router
from src.web.endpoints.public.sub_alias import router as sub_alias_router

# Overlay-схема управляется ОТДЕЛЬНЫМ alembic-env (infrastructure/database/migrations_overlay/).
# Baseline-миграция 0001 создаёт все overlay-таблицы идемпотентным DDL из
# overlay_baseline_ddl.BASELINE_DDL. Прогон — в lifespan под advisory-lock (сериализует
# старты primary+HA). Своя version-таблица alembic_version_overlay → без конфликта с
# линейкой base (историю развязки см. overlay_bootstrap.py).
_OVERLAY_ALEMBIC_INI = "src/infrastructure/database/migrations_overlay/alembic_overlay.ini"
_OVERLAY_MIGRATE_LOCK = 728411


async def _run_overlay_migrations() -> None:
    """`alembic upgrade head` для overlay-схемы под advisory-lock.

    Lock на ОТДЕЛЬНОМ соединении сериализует конкурентные старты (primary remnashop и
    HA remnashop-web гонят lifespan одновременно): второй ждёт первого и видит версию
    на head (no-op), без гонки на version-таблице. alembic запускается субпроцессом —
    у него свой event loop (env.py делает asyncio.run), из работающего loop не позвать.
    Падение alembic → RuntimeError → старт прерывается (fail-closed).
    """
    config = AppConfig.get()
    alembic_bin = shutil.which("alembic") or "/opt/remnashop/.venv/bin/alembic"
    engine = create_async_engine(config.database.dsn)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _OVERLAY_MIGRATE_LOCK})
            try:
                proc = await asyncio.create_subprocess_exec(
                    alembic_bin, "-c", _OVERLAY_ALEMBIC_INI, "upgrade", "head",
                    cwd="/opt/remnashop",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                out, _ = await proc.communicate()
                if proc.returncode != 0:
                    raise RuntimeError(
                        f"overlay alembic upgrade завершился rc={proc.returncode}: "
                        f"{out.decode(errors='replace')[-1000:]}"
                    )
                logger.info("Overlay: alembic upgrade head — overlay-схема актуальна")
            finally:
                await conn.execute(
                    text("SELECT pg_advisory_unlock(:k)"), {"k": _OVERLAY_MIGRATE_LOCK}
                )
    finally:
        await engine.dispose()


def _overlay_public_router() -> APIRouter:
    """Дополнительные public-ручки overlay (base про них не знает).

    subscription/plans/auth/referral остаются за base (его public-роутер уже их
    подключает; файл subscription.py overlay переопределяет точечно).
    """
    router = APIRouter(prefix=API_V1 + "/public")
    router.include_router(balance_router)
    router.include_router(me_role_router)
    router.include_router(set_password_router)
    router.include_router(password_reset_router)
    router.include_router(support_router)
    router.include_router(server_stats_router)
    router.include_router(traffic_history_router)
    router.include_router(service_status_router)
    router.include_router(status_page_router)
    router.include_router(appearance_router)
    router.include_router(apps_router)
    router.include_router(info_content_router)
    router.include_router(auth_oidc_router)
    router.include_router(email_manage_router)
    router.include_router(push_router)
    router.include_router(promocode_router)
    router.include_router(referral_stats_router)
    router.include_router(trial_discount_public_router)
    router.include_router(promo_banner_public_router)
    router.include_router(freeze_public_router)
    router.include_router(gift_public_router)
    router.include_router(sessions_public_router)
    router.include_router(account_public_router)
    router.include_router(notifications_public_router)
    router.include_router(sub_alias_router)
    return router


def _wrap_lifespan_with_support_tables(app: FastAPI, container: AsyncContainer) -> None:
    """Оборачивает base lifespan: перед ним создаёт таблицы поддержки.

    base lifespan НЕ копируется — берём тот, что выставил `get_app`, и вызываем
    его внутри. Контейнер dishka берём из app.state (его положил setup_dishka).
    """
    base_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        # Overlay-схема: alembic upgrade head (под advisory-lock). Падение → RuntimeError
        # → старт прерван (fail-closed: без таблиц кабинет работать не должен). Запускается
        # ПОСЛЕ base-alembic (тот прошёл в docker-entrypoint до uvicorn), поэтому FK на
        # users уже валидны.
        await _run_overlay_migrations()

        # FAIL-CLOSED: без security-critical таблиц (2FA-гейт и отзыв сессий) кабинет
        # работал бы небезопасно (2FA «выключена», logout-all не действует). Проверяем
        # их наличие и НЕ стартуем, если их нет — лучше явный отказ, чем тихий fail-open.
        critical = ("admin_2fa", "session_invalidations", "revoked_tokens")
        async with container(scope=Scope.REQUEST) as request_container:
            session = await request_container.get(AsyncSession)
            missing = [
                t
                for t in critical
                if not (
                    await session.execute(text("SELECT to_regclass(:t)"), {"t": t})
                ).scalar()
            ]
        if missing:
            raise RuntimeError(
                f"Overlay: отсутствуют security-critical таблицы {missing} — старт прерван "
                f"(без них 2FA/отзыв сессий не работают). Проверьте миграции/права БД."
            )

        # Сверка роли владельца: гарантируем, что у BOT_OWNER_ID роль OWNER.
        # Веб-вход в кабинет (через Telegram) создаёт юзера БЕЗ owner-проверки —
        # поэтому владелец, впервые зашедший через сайт, оставался USER и не видел
        # админку. Идемпотентно чиним при каждом старте (как бот делает на /start).
        try:
            owner_id = getattr(AppConfig.get().bot, "owner_id", None)
            if owner_id:
                async with container(scope=Scope.REQUEST) as request_container:
                    session = await request_container.get(AsyncSession)
                    res = await session.execute(
                        text(
                            "UPDATE users SET role = 'OWNER' "
                            "WHERE telegram_id = :oid AND role <> 'OWNER'"
                        ),
                        {"oid": owner_id},
                    )
                    await session.commit()
                    if res.rowcount:
                        logger.info(
                            f"Overlay: роль OWNER восстановлена для telegram_id={owner_id}"
                        )
        except Exception as exc:  # роль не критична для старта
            logger.exception(f"Overlay: не удалось сверить роль владельца: {exc}")

        async with base_lifespan(app):
            yield

    app.router.lifespan_context = lifespan


def application() -> FastAPI:
    # base-обвязка целиком (get_app + dishka + всё, что base добавит в будущем)
    app = _base_application()
    container: AsyncContainer = app.state.dishka_container

    # ── overlay: добавляем роуты поверх нетронутого base app ──
    app.include_router(admin_router)
    if AppConfig.get().web_enabled:
        app.include_router(_overlay_public_router())

    _wrap_lifespan_with_support_tables(app, container)
    _add_admin_audit_middleware(app, container)
    _add_login_tracking_middleware(app, container)
    _add_session_check_middleware(app, container)
    _add_csrf_protection_middleware(app)
    _add_refresh_reuse_detection_middleware(app, container)

    return app


def _add_refresh_reuse_detection_middleware(app: FastAPI, container: AsyncContainer) -> None:
    """Reuse-detection ротации refresh-токенов (H-01).

    Base-эндпоинт `/auth/refresh` ротирует refresh (single-use: старый удаляется, выдаётся
    новый). Если УКРАДЕННЫЙ старый токен предъявят ПОВТОРНО после потребления — это признак
    компрометации: рвём ВСЁ семейство токенов пользователя (revoke_all_user_tokens).

    Механика: валидный refresh помечаем в Redis (sha256(token) → uid, TTL). При следующем
    запросе, если токен уже НЕ валиден, но есть его consumed-маркер и прошло больше
    GRACE секунд (окно гонки одновременных refresh из одной вкладки/двух вкладок) —
    считаем переиспользованием и отзываем сессии. Детектор best-effort: не роняет refresh.

    Секрет в Redis не храним — только sha256 токена.
    """
    from fastapi.responses import JSONResponse

    CONSUMED_TTL = 7 * 24 * 3600  # маркер живёт неделю (окно возможного replay кражи)
    GRACE = 15  # сек: одновременные refresh (гонка) — не караем

    @app.middleware("http")
    async def _reuse(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.method == "POST" and request.url.path.endswith("/auth/refresh"):
            rt = request.cookies.get("refresh_token")
            if rt:
                try:
                    from redis.asyncio import Redis

                    from src.application.common.dao.auth import AuthSessionDao

                    h = hashlib.sha256(rt.encode()).hexdigest()
                    key = f"overlay_consumed_refresh:{h}"
                    async with container(scope=Scope.REQUEST) as rc:
                        dao = await rc.get(AuthSessionDao)
                        redis = await rc.get(Redis)
                        uid = await dao.get_user_id_by_refresh_token(rt)
                        if uid is None:
                            raw = await redis.get(key)
                            if raw:
                                val = raw.decode() if isinstance(raw, bytes) else str(raw)
                                cuid_s, _, cts_s = val.partition(":")
                                cuid = int(cuid_s)
                                cts = float(cts_s or 0)
                                if time.time() - cts > GRACE:
                                    # Реальное переиспользование → компрометация семейства.
                                    await dao.revoke_all_user_tokens(cuid)
                                    logger.warning(
                                        f"Refresh reuse detected for user {cuid} — все токены отозваны"
                                    )
                                    return JSONResponse(
                                        status_code=401,
                                        content={"detail": "Сессия скомпрометирована — войдите заново"},
                                    )
                                # В пределах GRACE — гонка одновременных refresh, пропускаем.
                        else:
                            # Валидный токен → сейчас будет ротирован base-хендлером: помечаем.
                            await redis.set(key, f"{uid}:{int(time.time())}", ex=CONSUMED_TTL)
                except Exception:  # noqa: BLE001 — детектор не должен ломать refresh
                    pass
        return await call_next(request)


def _add_csrf_protection_middleware(app: FastAPI) -> None:
    """CSRF-защита для cookie-аутентифицированных мутаций (Origin/Referer allowlist).

    Кабинет ходит cookie-сессией → уязвим к CSRF; SameSite=Lax снижает, но не закрывает
    риск. Для небезопасных методов (POST/PUT/PATCH/DELETE), несущих НАШУ auth-куку,
    требуем совпадения Origin (или, если его нет, Referer) с доменом кабинета.

    ВАЖНО: платёжные вебхуки и серверные интеграции НЕ несут нашу куку → проверку не
    проходят (у них своя подпись). Так CSRF-защита не ломает webhook'и, но закрывает
    браузерные сессии. GET/HEAD/OPTIONS и запросы без нашей куки — пропускаем.
    """
    from urllib.parse import urlsplit

    from fastapi.responses import JSONResponse

    from src.web.csrf import csrf_blocked  # чистая логика (юнит-тесты — tests/test_csrf.py)

    def _allowed_hosts(request: Request) -> set[str]:
        hosts: set[str] = set()
        cab = (os.environ.get("WEB_CABINET_URL") or "").strip()
        if cab:
            hosts.add(urlsplit(cab).netloc.lower())
        # Собственный хост запроса (за прокси — X-Forwarded-Host), same-origin fetch.
        for h in (
            request.headers.get("x-forwarded-host", "").split(",")[0].strip(),
            request.headers.get("host", "").strip(),
        ):
            if h:
                hosts.add(h.lower())
        return {x for x in hosts if x}

    @app.middleware("http")
    async def _csrf(request: Request, call_next):  # type: ignore[no-untyped-def]
        has_cookie = bool(
            request.cookies.get("access_token") or request.cookies.get("refresh_token")
        )
        if csrf_blocked(
            request.method,
            request.url.path,
            has_cookie,
            request.headers.get("origin"),
            request.headers.get("referer"),
            _allowed_hosts(request),
        ):
            return JSONResponse(
                status_code=403, content={"detail": "CSRF: недопустимый источник запроса"}
            )
        return await call_next(request)


def _add_session_check_middleware(app: FastAPI, container: AsyncContainer) -> None:
    """Отзыв сессий: «выйти со всех устройств» (iat < invalidated_at) и обычный «Выйти».

    Проверяет только аутентифицированные /api-запросы (кроме /auth/* — чтобы можно
    было войти заново). Битый/истёкший токен пропускаем — с ним разберётся auth роута.

    Обычный выход базовая роута закрывала наполовину: гасила refresh и куки, а выданный
    access-JWT оставался годным ещё ~15 минут — куки, скопированные до выхода, работали.
    Поэтому здесь же, на успешный /auth/logout, кладём токен в revoked_tokens.
    """
    import jwt as _jwt
    from fastapi.responses import JSONResponse

    # /auth/* исключаем ТОЧЕЧНО — только неаутентифицированные пути повторного входа
    # (с отозванной сессией юзер должен мочь войти заново / обновить токен / сбросить
    # пароль). Аутентифицированные /auth/* (me/whoami/telegram/link/email/*/password/set/
    # change-password) РАНЬШЕ тоже пропускались мимо проверки → logout-all и сброс пароля
    # НЕ отзывали access на них (украденный токен жил дальше). Теперь они проверяются.
    _SESSION_CHECK_EXEMPT = (
        "/auth/login",
        "/auth/register",
        "/auth/refresh",
        "/auth/logout",
        "/auth/telegram",  # вход через Telegram (exact end; /telegram/link не сматчится)
        "/auth/telegram/webapp",
        "/auth/telegram/oidc/start",
        "/auth/telegram/oidc/callback",
        "/auth/password/reset/request",
        "/auth/password/reset/confirm",
    )

    @app.middleware("http")
    async def _check_session(request: Request, call_next):  # type: ignore[no-untyped-def]
        token = request.cookies.get("access_token")
        path = request.url.path
        exempt = any(path.endswith(s) for s in _SESSION_CHECK_EXEMPT)
        if token and "/api/" in path and not exempt:
            try:
                cfg = AppConfig.get()
                secret = (
                    cfg.jwt_secret.get_secret_value() if getattr(cfg, "jwt_secret", None) else None
                )
                if secret:
                    payload = _jwt.decode(
                        token, secret, algorithms=["HS256"], options={"verify_sub": False}
                    )
                    uid = int(payload["sub"])
                    iat = payload.get("iat")
                    from src.infrastructure.services.overlay_sessions import (
                        token_invalidated,
                        token_revoked,
                    )

                    sm = await container.get(async_sessionmaker[AsyncSession])
                    async with sm() as s:
                        if await token_invalidated(s, uid, iat) or await token_revoked(s, token):
                            return JSONResponse(
                                status_code=401,
                                content={"detail": "Сессия завершена — войдите заново"},
                            )
            except Exception:  # noqa: BLE001 — не наша забота валидировать токен здесь
                pass

        response = await call_next(request)

        # Успешный выход — гасим и access-токен, иначе он живёт до конца срока.
        if token and path.endswith("/auth/logout") and response.status_code < 400:
            try:
                cfg = AppConfig.get()
                secret = (
                    cfg.jwt_secret.get_secret_value() if getattr(cfg, "jwt_secret", None) else None
                )
                exp = None
                if secret:
                    exp = _jwt.decode(
                        token,
                        secret,
                        algorithms=["HS256"],
                        options={"verify_sub": False, "verify_exp": False},
                    ).get("exp")
                from src.infrastructure.services.overlay_sessions import revoke_token

                sm = await container.get(async_sessionmaker[AsyncSession])
                async with sm() as s:
                    await revoke_token(s, token, exp)
            except Exception:  # noqa: BLE001 — выход не должен падать из-за отзыва
                pass

        return response


# Пути входа (по суффиксу полного пути). При успехе они выставляют access_token —
# по нему и пишем событие входа. /auth/refresh и /auth/telegram/link исключены
# (refresh не login; link не выставляет access_token).
_LOGIN_PATH_SUFFIXES = (
    "/auth/login",
    "/auth/register",
    "/auth/telegram",
    "/auth/telegram/webapp",
    "/auth/telegram/oidc/callback",
)


def _login_method(path: str) -> str:
    if path.endswith("/oidc/callback"):
        return "telegram_oidc"
    if path.endswith("/telegram/webapp"):
        return "telegram_webapp"
    if path.endswith("/auth/telegram"):
        return "telegram"
    if path.endswith("/auth/register"):
        return "register"
    return "email"


def _client_ip(request: Request) -> str:
    # X-Real-IP ставит НАШ nginx из реального клиентского IP (после real_ip, который
    # разрешает XFF через доверенный фронт-прокси) — доверяем ему для аудита/allowlist.
    # Крайний ЛЕВЫЙ X-Forwarded-For клиентоуправляем (спуфится) → НЕ используем его;
    # как фолбэк берём ПРАВЫЙ элемент XFF (добавлен последним доверенным хопом).
    xr = request.headers.get("x-real-ip")
    if xr:
        return xr.strip()[:64]
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[-1].strip()[:64]
    return (request.client.host if request.client else "")[:64]


def _access_token_from_setcookie(response) -> "str | None":  # type: ignore[name-defined]
    for h in response.headers.getlist("set-cookie"):
        if h.startswith("access_token="):
            val = h.split("access_token=", 1)[1].split(";", 1)[0]
            return val or None
    return None


def _add_login_tracking_middleware(app: FastAPI, container: AsyncContainer) -> None:
    """Пишет событие входа в login_events при успешном логине (любым способом).

    Распознаём вход по: путь — один из login-суффиксов И в ответе выставлен свежий
    access_token (значит логин удался). По токену получаем user_id. Ошибки записи
    не влияют на ответ.
    """

    @app.middleware("http")
    async def _track_login(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        try:
            path = request.url.path
            if response.status_code < 400 and any(
                path.endswith(s) for s in _LOGIN_PATH_SUFFIXES
            ):
                token = _access_token_from_setcookie(response)
                if token:
                    from src.web.endpoints.public._common import decode_access_token

                    cfg = AppConfig.get()
                    secret = (
                        cfg.jwt_secret.get_secret_value()
                        if getattr(cfg, "jwt_secret", None)
                        else None
                    )
                    if secret:
                        uid = decode_access_token(token, secret)
                        sm = await container.get(async_sessionmaker[AsyncSession])
                        async with sm() as s:
                            await s.execute(
                                text(
                                    "INSERT INTO login_events (user_id, ip, user_agent, method) "
                                    "VALUES (:u, :ip, :ua, :m)"
                                ),
                                {
                                    "u": uid,
                                    "ip": _client_ip(request),
                                    "ua": (request.headers.get("user-agent") or "")[:400],
                                    "m": _login_method(path),
                                },
                            )
                            await s.commit()
                            from src.infrastructure.services.overlay_login_alert import (
                                maybe_alert_new_login,
                            )

                            await maybe_alert_new_login(
                                s,
                                uid,
                                _client_ip(request),
                                (request.headers.get("user-agent") or "")[:400],
                            )
        except Exception:  # noqa: BLE001 — трекинг не должен ронять запрос
            logger.debug("login event write skipped")
        return response


_AUDIT_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_ADMIN_PREFIX = API_V1 + "/admin/"


def _add_admin_audit_middleware(app: FastAPI, container: AsyncContainer) -> None:
    """Логирует успешные изменяющие админ-запросы в admin_audit_log.

    «Кто» берётся из request.state.audit_actor (его выставляет admin-зависимость).
    Сессия — из app-scoped sessionmaker. Ошибки логирования не влияют на ответ.
    """

    @app.middleware("http")
    async def _audit(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        try:
            if (
                request.method in _AUDIT_METHODS
                and request.url.path.startswith(_ADMIN_PREFIX)
                and response.status_code < 400
            ):
                actor = getattr(request.state, "audit_actor", None)
                if actor:
                    sm = await container.get(async_sessionmaker[AsyncSession])
                    async with sm() as s:
                        await s.execute(
                            text(
                                "INSERT INTO admin_audit_log (actor, method, path, status) "
                                "VALUES (:a, :m, :p, :st)"
                            ),
                            {
                                "a": str(actor)[:120],
                                "m": request.method,
                                "p": request.url.path[:300],
                                "st": response.status_code,
                            },
                        )
                        await s.commit()
        except Exception:  # noqa: BLE001 — аудит не должен ронять запрос
            logger.debug("audit log write skipped")
        return response
