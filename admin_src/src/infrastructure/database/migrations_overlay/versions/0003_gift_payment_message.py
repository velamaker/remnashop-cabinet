"""gift_payments: сообщение бота с кнопкой оплаты (чтобы убрать его после оплаты).

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-28

Без этих колонок у покупателя навсегда оставалось сообщение «Перейти к оплате» с
кнопкой на уже оплаченный (и протухший) счёт. Колонки заполняет только бот; при
покупке из кабинета остаются NULL.
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_UPGRADE = (
    "ALTER TABLE gift_payments ADD COLUMN IF NOT EXISTS chat_id BIGINT",
    "ALTER TABLE gift_payments ADD COLUMN IF NOT EXISTS message_id BIGINT",
)


def upgrade() -> None:
    for statement in _UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    op.execute("ALTER TABLE gift_payments DROP COLUMN IF EXISTS chat_id")
    op.execute("ALTER TABLE gift_payments DROP COLUMN IF EXISTS message_id")
