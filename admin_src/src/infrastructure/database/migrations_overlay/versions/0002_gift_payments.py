"""gift_payments — подарок, оплаченный через шлюз (в обход баланса).

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-28

Строка пишется ДО отдачи URL шлюза (overlay_gift.record_gift_payment), а на вебхуке
по ней выпускается подарочный код (overlay_gift.try_issue_gift) вместо выдачи
подписки покупателю. DDL идемпотентный — безопасен и на новой установке, и на
существующей (там таблица уже могла появиться из BASELINE_DDL).
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_UPGRADE = (
    """
    CREATE TABLE IF NOT EXISTS gift_payments (
        payment_id    UUID PRIMARY KEY,
        user_id       INTEGER       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        plan_snapshot JSONB         NOT NULL,
        duration_days INTEGER       NOT NULL,
        amount        NUMERIC(12,2) NOT NULL,
        code          VARCHAR(64),
        issued        BOOLEAN       NOT NULL DEFAULT false,
        created_at    TIMESTAMPTZ   NOT NULL DEFAULT now(),
        issued_at     TIMESTAMPTZ
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_gift_payments_user ON gift_payments (user_id)",
)


def upgrade() -> None:
    for statement in _UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS gift_payments")
