"""bulk_jobs: массовое «Добавить N дней» и «Написать отфильтрованным».

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-17

Обе операции идут фоновой задачей, а не одним запросом: на человека уходят GET и
PATCH панели, и сотни людей в таймаут HTTP не влезают. Журнал нужен по людям, а
не одной строкой на задачу: по нему задача продолжается после падения воркера, по
нему же админ видит, кто и почему не получил.

Однократность держится здесь, в схеме, а не только в коде:
  * `request_id UNIQUE` — повтор того же запуска (двойной клик, повтор после
    обрыва сети) не создаёт вторую задачу;
  * `ux_bulk_jobs_one_active` — одновременно идёт не больше одной задачи каждого
    вида: две задачи «+3 дня» на одну выборку дали бы людям +6;
  * `target_expire_at` пишется ДО вызова панели (write-ahead): после падения на
    полпути задача сверяет панель именно с ним и не прибавляет дни второй раз;
  * строку человека захватывает UPDATE … WHERE status = 'PENDING' — два воркера
    не обработают её оба.

`subscription_id` — подписка, которой добавляли дни: если к повтору у человека уже
другая подписка, повторять запись в панель нельзя, это ручной разбор.

downgrade() удаляет журнал вместе с историей — на проде его не вызывать: пропадёт
защита «этим людям уже добавляли дни за последние 24 часа».
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS bulk_jobs (
            id                BIGSERIAL    PRIMARY KEY,
            kind              VARCHAR(16)  NOT NULL,
            status            VARCHAR(16)  NOT NULL DEFAULT 'QUEUED',
            request_id        UUID         NOT NULL UNIQUE,
            params_hash       VARCHAR(64)  NOT NULL,
            parent_job_id     BIGINT       REFERENCES bulk_jobs(id) ON DELETE SET NULL,
            created_by        INTEGER      REFERENCES users(id) ON DELETE SET NULL,
            created_by_label  VARCHAR(120) NOT NULL,
            params            JSONB        NOT NULL,
            segment_hash      VARCHAR(64)  NOT NULL,
            total             INTEGER      NOT NULL DEFAULT 0,
            done_count        INTEGER      NOT NULL DEFAULT 0,
            applied_count     INTEGER      NOT NULL DEFAULT 0,
            skipped_count     INTEGER      NOT NULL DEFAULT 0,
            failed_count      INTEGER      NOT NULL DEFAULT 0,
            unknown_count     INTEGER      NOT NULL DEFAULT 0,
            verify_flagged    INTEGER      NOT NULL DEFAULT 0,
            pause_reason      TEXT,
            lease_owner       VARCHAR(64),
            lease_until       TIMESTAMPTZ,
            canceled_by_label VARCHAR(120),
            created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
            started_at        TIMESTAMPTZ,
            finished_at       TIMESTAMPTZ
        )
        """
    )
    # Статусы, в которых задача ещё может что-то изменить у людей. ERROR сюда не
    # входит: упавшая задача не должна навсегда запирать новый запуск.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_bulk_jobs_one_active ON bulk_jobs (kind) "
        "WHERE status IN ('QUEUED', 'PROCESSING', 'PAUSED', 'CANCELING')"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_bulk_jobs_created ON bulk_jobs (created_at DESC)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS bulk_job_items (
            job_id           BIGINT       NOT NULL REFERENCES bulk_jobs(id) ON DELETE CASCADE,
            user_id          INTEGER      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            status           VARCHAR(16)  NOT NULL DEFAULT 'PENDING',
            category         VARCHAR(24),
            subscription_id  INTEGER,
            old_expire_at    TIMESTAMPTZ,
            target_expire_at TIMESTAMPTZ,
            added_seconds    BIGINT,
            deferred         BOOLEAN      NOT NULL DEFAULT false,
            channels         VARCHAR(64),
            tg_message_id    BIGINT,
            text_sha256      VARCHAR(64),
            verify_note      TEXT,
            error            TEXT,
            attempts         SMALLINT     NOT NULL DEFAULT 0,
            updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
            PRIMARY KEY (job_id, user_id)
        )
        """
    )
    # «Этим людям уже добавляли дни / слали этот текст за 24 часа» ищется по людям.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bulk_job_items_user "
        "ON bulk_job_items (user_id, updated_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_bulk_job_items_status ON bulk_job_items (job_id, status)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS bulk_job_items")
    op.execute("DROP TABLE IF EXISTS bulk_jobs")
