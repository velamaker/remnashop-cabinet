"""extra_traffic_grants / extra_traffic_orders: докупка трафика к текущему окну.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-18

Человек докупает +N ГБ к ТЕКУЩЕМУ окну трафика за фиксированную цену
(services/overlay_extra_traffic.py). Лимит остаётся одним числом —
`subscriptions.traffic_limit` (в ГИГАБАЙТАХ) и `trafficLimitBytes` в панели; здесь
живут две вещи, которых в нём нет:

  * `extra_traffic_grants` — сколько ГБ докуплено и ДО КАКОГО МОМЕНТА. Без журнала
    прибавка была бы вечной: панель обнуляет РАСХОД по своему расписанию, но ЛИМИТ
    не трогает, и поднятый лимит давал бы +N ГБ каждый месяц за один платёж.
    `panel_created_at` — дата создания пользователя В ПАНЕЛИ: только по ней считается
    день обнуления при стратегии MONTH_ROLLING, а наша строка подписки пересоздаётся
    при каждой смене тарифа и якорем быть не может.
  * `extra_traffic_orders` — деньги. `request_id UNIQUE` держит идемпотентность кнопки
    (двойной клик и повтор запроса дают одну прибавку), `payment_id UNIQUE` —
    идемпотентность вебхука шлюза. Строка заказа пишется ДО отдачи ссылки на оплату:
    не записали — ссылку не отдаём, платить нечем, денег не теряем.

`CHECK (ends_at > granted_at)` НЕ ставим намеренно: при стратегии NO_RESET там NULL,
а у опоздавшего платежа пересчитанное окно может оказаться в прошлом — такой заказ
отклоняется КОДОМ до записи, и падать на constraint посреди вебхука незачем.

FK на users(id) обязательны: scripts/merge-duplicate.py переносит таблицы двойника
по FK сам (урок переноса остатка) — без FK записи остались бы у удалённой копии.

ПОРЯДОК ВЫКАТКИ ЖЁСТКИЙ, и вот почему. Миграции накатывает ТОЛЬКО контейнер бота
(`remnashop`), а вебхуки оплат исполняет taskiq-воркер. Пока эта миграция не прошла,
воркер с новым кодом не должен работать вовсе: его обработчик оплаты обращается к
`extra_traffic_orders`. Поэтому перед пересозданием воркера и шедулера обязателен гейт:

    docker compose exec -T remnashop psql ... -c "SELECT version_num FROM alembic_version_overlay"  # 0012
    docker compose exec -T remnashop psql ... -c "SELECT 1 FROM extra_traffic_orders LIMIT 0"        # без ошибки
    docker compose exec -T remnashop psql ... -c "SELECT 1 FROM extra_traffic_grants LIMIT 0"        # без ошибки

Сам код к отсутствию таблиц тоже устойчив (ветка докупки отсекается по снимку тарифа
до первого запроса, и обычный платёж выдаётся как раньше), но это страховка, а не
разрешение выкатывать в произвольном порядке.

downgrade() удаляет таблицы — на проде НЕ вызывать: прибавки перестанут кончаться, и
лимиты у всех останутся завышенными до следующего продления. Если откат всё же нужен,
сначала руками опустить лимиты по строкам со `status='active'`, потом дропать.
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS extra_traffic_grants (
            id                BIGSERIAL     PRIMARY KEY,
            user_id           INTEGER       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            subscription_id   INTEGER       NOT NULL,
            plan_id           INTEGER       NOT NULL,
            status            VARCHAR(16)   NOT NULL DEFAULT 'active',
            gb                INTEGER       NOT NULL CHECK (gb > 0),
            strategy          VARCHAR(16)   NOT NULL,
            panel_created_at  TIMESTAMPTZ,
            granted_at        TIMESTAMPTZ   NOT NULL,
            ends_at           TIMESTAMPTZ,
            last_applied_at   TIMESTAMPTZ   NOT NULL,
            ended_at          TIMESTAMPTZ,
            end_reason        VARCHAR(32),
            fail_count        INTEGER       NOT NULL DEFAULT 0,
            carried_value     NUMERIC(12,2),
            created_at        TIMESTAMPTZ   NOT NULL DEFAULT now(),
            CONSTRAINT ck_etg_status CHECK (status IN ('active','ended','burned','revoked'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_etg_active_end "
        "ON extra_traffic_grants (ends_at) WHERE status = 'active'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_etg_active_sub "
        "ON extra_traffic_grants (subscription_id) WHERE status = 'active'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_etg_user ON extra_traffic_grants (user_id, created_at DESC)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS extra_traffic_orders (
            id               BIGSERIAL     PRIMARY KEY,
            request_id       UUID          NOT NULL UNIQUE,
            payment_id       UUID          UNIQUE,
            source           VARCHAR(16)   NOT NULL,
            user_id          INTEGER       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            subscription_id  INTEGER       NOT NULL,
            grant_id         BIGINT        REFERENCES extra_traffic_grants(id) ON DELETE SET NULL,
            status           VARCHAR(16)   NOT NULL,
            gb               INTEGER       NOT NULL CHECK (gb > 0),
            amount           NUMERIC(12,2) NOT NULL CHECK (amount > 0),
            currency         VARCHAR(8)    NOT NULL DEFAULT 'RUB',
            window_end       TIMESTAMPTZ,
            panel_created_at TIMESTAMPTZ,
            payment_url      TEXT,
            attempts         INTEGER       NOT NULL DEFAULT 0,
            last_error       TEXT,
            reason           VARCHAR(32),
            credited_at      TIMESTAMPTZ,
            applied_at       TIMESTAMPTZ,
            rejected_at      TIMESTAMPTZ,
            created_at       TIMESTAMPTZ   NOT NULL DEFAULT now(),
            CONSTRAINT ck_eto_status CHECK (status IN ('pending','credited','applied','rejected'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_eto_open "
        "ON extra_traffic_orders (created_at) WHERE status IN ('pending','credited')"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_eto_grant ON extra_traffic_orders (grant_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_eto_user ON extra_traffic_orders (user_id, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS extra_traffic_orders")
    op.execute("DROP TABLE IF EXISTS extra_traffic_grants")
