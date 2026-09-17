"""renewal_discount_grants: скидка на продление ДО окончания подписки.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-17

Зачем журнал выдач, а не строка на человека, как у win-back. Win-back выдаётся
человеку один раз навсегда (PK user_id), а скидка на продление — на СРОК подписки:
помесячный клиент может получить её снова через cooldown. Поэтому строка на
выдачу и два уникальных индекса вместо одного ключа:
  * (user_id, sub_expire_at) — второй выдачи на тот же срок не будет даже при
    гонке двух прогонов крона (INSERT … ON CONFLICT DO NOTHING RETURNING);
  * (user_id) WHERE status = 'active' — открытой может быть только одна скидка:
    поле `users.purchase_discount` у человека одно, две выдачи сразу затёрли бы
    друг друга, и сгорание первой сняло бы вторую.

Статусы: active | used | expired | revoked. Выдача не удаляется после сгорания:
по ней считаются cooldown, «уже выдавали на этот срок» и итоги в админке.

notified_at занимается ДО отправки сообщения (UPDATE … WHERE notified_at IS NULL):
рестарт воркера посреди прохода не пришлёт человеку второе сообщение. Итог
доставки — tg_status (sent | blocked | failed | no_telegram), push_sent и
notify_error.

downgrade() удаляет таблицу вместе с историей выдач — на проде его не вызывать:
пропадёт cooldown, и помесячные клиенты получат скидку повторно.
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS renewal_discount_grants (
            id              BIGSERIAL    PRIMARY KEY,
            user_id         INTEGER      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            subscription_id INTEGER      NOT NULL,
            sub_expire_at   TIMESTAMPTZ  NOT NULL,
            percent         INTEGER      NOT NULL CHECK (percent BETWEEN 1 AND 100),
            granted_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
            expires_at      TIMESTAMPTZ  NOT NULL,
            status          VARCHAR(16)  NOT NULL DEFAULT 'active',
            closed_at       TIMESTAMPTZ,
            transaction_id  INTEGER,
            notified_at     TIMESTAMPTZ,
            tg_status       VARCHAR(16),
            push_sent       INTEGER      NOT NULL DEFAULT 0,
            notify_error    VARCHAR(300)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_renewal_discount_period "
        "ON renewal_discount_grants (user_id, sub_expire_at)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_renewal_discount_open "
        "ON renewal_discount_grants (user_id) WHERE status = 'active'"
    )
    # Погашение идёт по открытым выдачам с истёкшим сроком, итоги — по дате выдачи.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_renewal_discount_status_expires "
        "ON renewal_discount_grants (status, expires_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_renewal_discount_granted "
        "ON renewal_discount_grants (granted_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS renewal_discount_grants")
