"""Overlay-миграция тикетов поддержки.

Перенумерована с 0040 на 0041, т.к. в базовом образе появилась своя 0040
(add_default_notification_route). Идемпотентна: таблицы могли быть созданы
прежней 0040 — создаём только если их ещё нет.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0041_support_tickets"
down_revision: Union[str, None] = "0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    if not _has_table("support_tickets"):
        op.create_table(
            "support_tickets",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("subject", sa.String(length=200), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
    if not _has_table("support_messages"):
        op.create_table(
            "support_messages",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "ticket_id",
                sa.Integer(),
                sa.ForeignKey("support_tickets.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("sender", sa.String(length=10), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )


def downgrade() -> None:
    op.drop_table("support_messages")
    op.drop_table("support_tickets")
