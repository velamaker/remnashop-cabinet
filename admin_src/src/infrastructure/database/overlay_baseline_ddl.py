"""ЗАМОРОЖЕННЫЙ снимок DDL overlay-таблиц на момент введения alembic (baseline 0001).

НЕ РЕДАКТИРОВАТЬ: это исторический слепок. Любые изменения схемы overlay — НОВОЙ
миграцией в migrations_overlay/versions/ (0002+), а не правкой этого файла.

Все выражения идемпотентны (CREATE/ALTER ... IF NOT EXISTS), поэтому baseline
безопасно применяется и на новых установках (создаёт), и на существующих (no-op).
"""

BASELINE_DDL = (
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
    # Модель/платформа устройства (из Remnawave HWID) — для наглядности группы
    # «один девайс — разные аккаунты» в детекте абьюза.
    "ALTER TABLE hwid_devices ADD COLUMN IF NOT EXISTS device_model VARCHAR(128)",
    "ALTER TABLE hwid_devices ADD COLUMN IF NOT EXISTS platform VARCHAR(64)",
    # Скидка на первую покупку триальщикам (за N дней до конца триала). Одна строка
    # на юзера — трекает выданную скидку для дедупа, срока жизни и баннера в кабинете.
    # Саму скидку крон пишет в users.purchase_discount (база гасит её после покупки).
    """
    CREATE TABLE IF NOT EXISTS trial_discounts (
        user_id    INTEGER      NOT NULL PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        percent    INTEGER      NOT NULL,
        granted_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
        expires_at TIMESTAMPTZ  NOT NULL,
        used       BOOLEAN      NOT NULL DEFAULT false
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_trial_discounts_expires ON trial_discounts (expires_at)",
    # Резервный доступ истёкшим подпискам (1 ГБ на N дней). Одна строка на юзера —
    # дедуп «один резерв на юзера» + окончание окна (reserve_expire_at) → крон истекает.
    """
    CREATE TABLE IF NOT EXISTS reserve_grants (
        user_id           INTEGER      NOT NULL PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        remna_uuid        VARCHAR(64)  NOT NULL,
        granted_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
        reserve_expire_at TIMESTAMPTZ  NOT NULL,
        ended             BOOLEAN      NOT NULL DEFAULT false
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_reserve_grants_active ON reserve_grants (ended, reserve_expire_at)",
    # Win-back: скидка «вернись» истёкшим через N дней. Одна строка на юзера (дедуп +
    # срок жизни промо). Саму скидку крон пишет в users.purchase_discount.
    """
    CREATE TABLE IF NOT EXISTS winback_grants (
        user_id    INTEGER      NOT NULL PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        percent    INTEGER      NOT NULL,
        granted_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
        expires_at TIMESTAMPTZ  NOT NULL,
        used       BOOLEAN      NOT NULL DEFAULT false
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_winback_grants_expires ON winback_grants (expires_at)",
    # Известные HWID-устройства юзеров — база для детекта «новое устройство подключилось»
    # (крон new_device.py). Первый снимок юзера = baseline (без уведомления).
    """
    CREATE TABLE IF NOT EXISTS known_devices (
        user_id    INTEGER      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        hwid       VARCHAR(256) NOT NULL,
        first_seen TIMESTAMPTZ  NOT NULL DEFAULT now(),
        PRIMARY KEY (user_id, hwid)
    )
    """,
    # Заморозка (пауза) подписки: одна активная пауза на юзера. remaining_seconds —
    # сохранённый остаток срока на момент паузы (при возобновлении expire = now+остаток).
    """
    CREATE TABLE IF NOT EXISTS subscription_freezes (
        user_id           INTEGER      NOT NULL PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        remna_uuid        VARCHAR(64)  NOT NULL,
        frozen_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
        remaining_seconds BIGINT       NOT NULL,
        active            BOOLEAN      NOT NULL DEFAULT true
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_subscription_freezes_active ON subscription_freezes (active, frozen_at)",
    # 2FA (TOTP) админов — opt-in на админа (см. services/overlay_admin_2fa.py).
    """
    CREATE TABLE IF NOT EXISTS admin_2fa (
        user_id  INTEGER      NOT NULL PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        secret   VARCHAR(64)  NOT NULL,
        enabled  BOOLEAN      NOT NULL DEFAULT false
    )
    """,
    # «Выйти со всех устройств»: токены с iat раньше invalidated_at — недействительны.
    """
    CREATE TABLE IF NOT EXISTS session_invalidations (
        user_id        INTEGER      NOT NULL PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        invalidated_at TIMESTAMPTZ  NOT NULL DEFAULT now()
    )
    """,
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
    # История уведомлений админам (web-push «новый тикет» и т.п.). Пишется в
    # services/overlay_push.py::push_admins_standalone при каждой отправке админам,
    # даже если ни одного устройства не подписано — чтобы владелец видел ленту.
    # Показывается в админке (раздел «Уведомления»). Старые чистятся при вставке.
    """
    CREATE TABLE IF NOT EXISTS admin_notifications (
        id         BIGSERIAL PRIMARY KEY,
        title      VARCHAR(200) NOT NULL DEFAULT '',
        body       TEXT         NOT NULL DEFAULT '',
        url        VARCHAR(500) NOT NULL DEFAULT '/',
        created_at TIMESTAMPTZ  NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_admin_notifications_created ON admin_notifications (created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS user_notifications (
        id         BIGSERIAL    PRIMARY KEY,
        user_id    INTEGER      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title      VARCHAR(200) NOT NULL DEFAULT '',
        body       TEXT         NOT NULL DEFAULT '',
        url        VARCHAR(500) NOT NULL DEFAULT '/',
        is_read    BOOLEAN      NOT NULL DEFAULT false,
        created_at TIMESTAMPTZ  NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_user_notifications_user ON user_notifications (user_id, created_at DESC)",
)
