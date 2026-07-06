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

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from dishka import AsyncContainer, Scope
from fastapi import APIRouter, FastAPI, Request
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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

# DDL таблиц поддержки. Идемпотентно (IF NOT EXISTS) — повторный старт безопасен.
_SUPPORT_TABLES_DDL = (
    """
    CREATE TABLE IF NOT EXISTS support_tickets (
        id          SERIAL PRIMARY KEY,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        subject     VARCHAR(200) NOT NULL,
        status      VARCHAR(20)  NOT NULL DEFAULT 'open',
        created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
        updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_support_tickets_user_id ON support_tickets (user_id)",
    """
    CREATE TABLE IF NOT EXISTS support_messages (
        id          SERIAL PRIMARY KEY,
        ticket_id   INTEGER NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,
        sender      VARCHAR(10) NOT NULL,
        body        TEXT        NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_support_messages_ticket_id ON support_messages (ticket_id)",
    """
    CREATE TABLE IF NOT EXISTS admin_audit_log (
        id          SERIAL PRIMARY KEY,
        actor       VARCHAR(120) NOT NULL,
        method      VARCHAR(10)  NOT NULL,
        path        VARCHAR(300) NOT NULL,
        status      INTEGER      NOT NULL,
        created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_admin_audit_created ON admin_audit_log (created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS login_events (
        id          BIGSERIAL PRIMARY KEY,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        ip          VARCHAR(64),
        user_agent  VARCHAR(400),
        method      VARCHAR(20),
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_login_events_user ON login_events (user_id, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS admin_grants (
        user_id      INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        full_access  BOOLEAN     NOT NULL DEFAULT false,
        can_write    BOOLEAN     NOT NULL DEFAULT true,
        sections     JSONB       NOT NULL DEFAULT '[]'::jsonb,
        expires_at   TIMESTAMPTZ,
        granted_by   VARCHAR(120),
        updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # Снимок HWID-устройств пользователей (для детекта абьюза «один девайс —
    # разные аккаунты»). В нашей БД HWID нет — периодическая задача снимает их
    # через Remnawave SDK (см. taskiq/tasks/abuse_hwid.py).
    """
    CREATE TABLE IF NOT EXISTS hwid_devices (
        user_id    INTEGER      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        hwid       VARCHAR(256) NOT NULL,
        updated_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
        PRIMARY KEY (user_id, hwid)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_hwid_devices_hwid ON hwid_devices (hwid)",
    # История email-рассылок из кабинета (TG-рассылки живут в базовой таблице
    # broadcasts; для email её enum-аудиторий не хватает — трекаем отдельно).
    # Фактическую отправку делает taskiq/tasks/broadcast_email.py.
    """
    CREATE TABLE IF NOT EXISTS email_broadcasts (
        id            SERIAL PRIMARY KEY,
        subject       VARCHAR(300) NOT NULL,
        body          TEXT         NOT NULL,
        status        VARCHAR(20)  NOT NULL DEFAULT 'PROCESSING',
        total_count   INTEGER      NOT NULL DEFAULT 0,
        success_count INTEGER      NOT NULL DEFAULT 0,
        failed_count  INTEGER      NOT NULL DEFAULT 0,
        created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_email_broadcasts_created ON email_broadcasts (created_at DESC)",
    # Web Push подписки PWA (браузерные push). Одна строка на устройство/endpoint.
    # Отправка через pywebpush (см. services/overlay_push.py). Мёртвые (404/410)
    # чистит send_to_user. VAPID-ключи — в assets/push_vapid.json.
    """
    CREATE TABLE IF NOT EXISTS push_subscriptions (
        id          BIGSERIAL PRIMARY KEY,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        endpoint    VARCHAR(1000) NOT NULL UNIQUE,
        p256dh      VARCHAR(200)  NOT NULL,
        auth        VARCHAR(100)  NOT NULL,
        created_at  TIMESTAMPTZ   NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_push_subscriptions_user ON push_subscriptions (user_id)",
    # Рублёвый баланс-кошелёк пользователя (ОТДЕЛЬНО от User.points/баллов рефералки).
    # Пополняется (админ/картой) и тратится на продление по цене тарифа. Overlay-колонка.
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS cabinet_balance NUMERIC(12,2) NOT NULL DEFAULT 0",
    # Автопродление с баланса: если включено, cron за N дней до конца подписки
    # продлевает её, списывая ₽ с cabinet_balance. См. taskiq/tasks/autopay.py.
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS autopay_enabled BOOLEAN NOT NULL DEFAULT false",
    # Сегмент аудитории email-рассылки (EMAIL_ALL / EMAIL_SUBSCRIBED / EMAIL_TRIAL /
    # EMAIL_EXPIRING / EMAIL_EXPIRED). ADD COLUMN IF NOT EXISTS — для уже созданной таблицы.
    "ALTER TABLE email_broadcasts ADD COLUMN IF NOT EXISTS segment VARCHAR(30) NOT NULL DEFAULT 'EMAIL_ALL'",
    # Пополнения ₽-баланса через платёжные шлюзы (+бонус). Строка пишется ДО отдачи
    # URL шлюза (по payment_id базовой транзакции); на вебхуке base ProcessPayment
    # (overlay, вариант B) зачисляет amount+bonus на users.cabinet_balance и ставит
    # credited. См. services/overlay_topup.py. Идемпотентность — по флагу credited.
    """
    CREATE TABLE IF NOT EXISTS balance_topups (
        payment_id  UUID PRIMARY KEY,
        user_id     INTEGER       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        amount      NUMERIC(12,2) NOT NULL,
        bonus       NUMERIC(12,2) NOT NULL DEFAULT 0,
        credited    BOOLEAN       NOT NULL DEFAULT false,
        created_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),
        credited_at TIMESTAMPTZ
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_balance_topups_user ON balance_topups (user_id)",
)


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
    return router


def _wrap_lifespan_with_support_tables(app: FastAPI, container: AsyncContainer) -> None:
    """Оборачивает base lifespan: перед ним создаёт таблицы поддержки.

    base lifespan НЕ копируется — берём тот, что выставил `get_app`, и вызываем
    его внутри. Контейнер dishka берём из app.state (его положил setup_dishka).
    """
    base_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        try:
            async with container(scope=Scope.REQUEST) as request_container:
                session = await request_container.get(AsyncSession)
                for stmt in _SUPPORT_TABLES_DDL:
                    await session.execute(text(stmt))
                await session.commit()
            logger.info("Overlay: таблицы поддержки готовы (IF NOT EXISTS)")
        except Exception as exc:  # не валим старт из-за overlay-таблиц
            logger.exception(f"Overlay: не удалось создать таблицы поддержки: {exc}")

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

    return app


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
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()[:64]
    xr = request.headers.get("x-real-ip")
    if xr:
        return xr.strip()[:64]
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
