"""revoked_tokens: отзыв конкретного access-токена при выходе.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-31

«Выйти» гасил только refresh-токен, а выданный access-JWT оставался годным до конца
срока (~15 минут): куки, скопированные до выхода, продолжали работать. «Выйти со всех
устройств» (session_invalidations) для этого не годится — оно рубит и остальные
устройства человека. Здесь храним отпечаток одного отозванного токена до его истечения.
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_UPGRADE = (
    """
    CREATE TABLE IF NOT EXISTS revoked_tokens (
        token_hash CHAR(64)    NOT NULL PRIMARY KEY,
        expires_at TIMESTAMPTZ NOT NULL
    )
    """,
    # По нему же чистим протухшие записи, чтобы таблица не росла бесконечно.
    "CREATE INDEX IF NOT EXISTS ix_revoked_tokens_expires ON revoked_tokens (expires_at)",
)


def upgrade() -> None:
    for statement in _UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS revoked_tokens")
