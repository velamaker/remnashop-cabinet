"""extra_device_slots / extra_device_orders: докупка +1 устройства к подписке.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-18

Человек докупает место под устройство к ТЕКУЩЕЙ подписке до конца её срока и платит
только за оставшиеся дни (services/overlay_extra_device.py). Лимит устройств остаётся
одним числом в `subscriptions.device_limit`; здесь живут две вещи, которых в нём нет:

  * `extra_device_slots` — сколько мест докуплено и ДО КОГДА. Без журнала «+1» было бы
    навсегда: админское «Продлить», массовые дни и пауза лимит не сбрасывают, и
    оплаченное на неделю место работало бы годами.
  * `extra_device_orders` — деньги. `request_id UNIQUE` держит идемпотентность кнопки
    (двойной клик и повтор запроса дают один слот), `payment_id UNIQUE` — идемпотентность
    вебхука шлюза. Строка заказа пишется ДО отдачи ссылки на оплату: не записали —
    ссылку не отдаём, платить нечем, денег не теряем.

`ck_edo_period` (period_end > cov_start) НЕ ставим намеренно: у опоздавшего платежа
`cov_start` берётся как «сейчас» и может обогнать конец периода — такой заказ
отклоняется кодом до записи, и падать на constraint посреди вебхука незачем.

FK на users(id) обязательны: scripts/merge-duplicate.py переносит таблицы двойника
по FK сам (урок переноса остатка) — без FK слоты остались бы у удалённой копии.

ПОРЯДОК ВЫКАТКИ ЖЁСТКИЙ, и вот почему. Миграции накатывает ТОЛЬКО контейнер бота
(`remnashop`), а вебхуки оплат исполняет taskiq-воркер. Пока эта миграция не прошла,
воркер с новым кодом не должен работать вовсе: его обработчик оплаты обращается к
`extra_device_orders`. Поэтому перед пересозданием воркера и шедулера обязателен гейт:

    docker compose exec -T remnashop psql ... -c "SELECT version_num FROM alembic_version_overlay"   # 0011
    docker compose exec -T remnashop psql ... -c "SELECT 1 FROM extra_device_orders LIMIT 0"          # без ошибки

Сам код к отсутствию таблиц тоже устойчив (ветка докупки отсекается по снимку тарифа
до первого запроса, и обычный платёж выдаётся как раньше), но это страховка, а не
разрешение выкатывать в произвольном порядке.

downgrade() удаляет таблицы — на проде НЕ вызывать: слоты перестанут кончаться, и
лимиты у всех останутся завышенными до следующего продления.
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS extra_device_slots (
            id               BIGSERIAL     PRIMARY KEY,
            user_id          INTEGER       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            subscription_id  INTEGER       NOT NULL,
            plan_id          INTEGER       NOT NULL,
            status           VARCHAR(16)   NOT NULL DEFAULT 'active',
            starts_at        TIMESTAMPTZ   NOT NULL,
            ends_at          TIMESTAMPTZ   NOT NULL,
            last_applied_at  TIMESTAMPTZ   NOT NULL,
            reminded_at      TIMESTAMPTZ,
            ended_at         TIMESTAMPTZ,
            end_reason       VARCHAR(32),
            devices_removed  INTEGER       NOT NULL DEFAULT 0,
            removal_done     BOOLEAN       NOT NULL DEFAULT true,
            fail_count       INTEGER       NOT NULL DEFAULT 0,
            carried_value    NUMERIC(12,2),
            created_at       TIMESTAMPTZ   NOT NULL DEFAULT now(),
            CONSTRAINT ck_eds_period CHECK (ends_at > starts_at),
            CONSTRAINT ck_eds_status CHECK (status IN ('active','ended','burned','revoked'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_eds_active_end "
        "ON extra_device_slots (ends_at) WHERE status = 'active'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_eds_active_sub "
        "ON extra_device_slots (subscription_id) WHERE status = 'active'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_eds_user ON extra_device_slots (user_id, created_at DESC)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS extra_device_orders (
            id               BIGSERIAL     PRIMARY KEY,
            request_id       UUID          NOT NULL UNIQUE,
            payment_id       UUID          UNIQUE,
            source           VARCHAR(16)   NOT NULL,
            kind             VARCHAR(8)    NOT NULL,
            user_id          INTEGER       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            subscription_id  INTEGER       NOT NULL,
            slot_id          BIGINT        REFERENCES extra_device_slots(id) ON DELETE SET NULL,
            status           VARCHAR(16)   NOT NULL,
            amount           NUMERIC(12,2) NOT NULL CHECK (amount > 0),
            currency         VARCHAR(8)    NOT NULL DEFAULT 'RUB',
            price_per_30d    NUMERIC(12,2) NOT NULL,
            cov_start        TIMESTAMPTZ   NOT NULL,
            period_end       TIMESTAMPTZ   NOT NULL,
            payment_url      TEXT,
            attempts         INTEGER       NOT NULL DEFAULT 0,
            last_error       TEXT,
            reason           VARCHAR(32),
            credited_at      TIMESTAMPTZ,
            applied_at       TIMESTAMPTZ,
            rejected_at      TIMESTAMPTZ,
            created_at       TIMESTAMPTZ   NOT NULL DEFAULT now(),
            CONSTRAINT ck_edo_status CHECK (status IN ('pending','credited','applied','rejected')),
            CONSTRAINT ck_edo_kind CHECK (kind IN ('new','extend'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_edo_open "
        "ON extra_device_orders (created_at) WHERE status IN ('pending','credited')"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_edo_slot ON extra_device_orders (slot_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_edo_user ON extra_device_orders (user_id, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS extra_device_orders")
    op.execute("DROP TABLE IF EXISTS extra_device_slots")
