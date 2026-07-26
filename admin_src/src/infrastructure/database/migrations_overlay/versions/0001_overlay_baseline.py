"""overlay baseline — все overlay-таблицы на момент введения alembic.

Revision ID: 0001
Revises:
Create Date: 2026-07-26

upgrade() применяет ЗАМОРОЖЕННЫЙ идемпотентный DDL (CREATE/ALTER ... IF NOT EXISTS)
из overlay_baseline_ddl.BASELINE_DDL. Благодаря идемпотентности baseline безопасен и на
НОВОЙ установке (создаёт таблицы), и на СУЩЕСТВУЮЩЕЙ (таблицы уже есть от прежнего
bootstrap-DDL → no-op; alembic лишь запишет версию 0001 в alembic_version_overlay).
Отдельного `stamp` для существующих установок не требуется.

downgrade() — деструктивен (дропает overlay-таблицы и overlay-колонки users). Нужен
только для полного отката; в проде не вызывается.
"""

from alembic import op

from src.infrastructure.database.overlay_baseline_ddl import BASELINE_DDL

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


# Порядок дропа — обратный зависимостям (сначала зависимые/дочерние, потом users-колонки).
_DOWNGRADE = (
    "DROP TABLE IF EXISTS user_notifications",
    "DROP TABLE IF EXISTS admin_notifications",
    "DROP TABLE IF EXISTS balance_topups",
    "DROP TABLE IF EXISTS push_subscriptions",
    "DROP TABLE IF EXISTS email_broadcasts",
    "DROP TABLE IF EXISTS session_invalidations",
    "DROP TABLE IF EXISTS admin_2fa",
    "DROP TABLE IF EXISTS subscription_freezes",
    "DROP TABLE IF EXISTS known_devices",
    "DROP TABLE IF EXISTS winback_grants",
    "DROP TABLE IF EXISTS reserve_grants",
    "DROP TABLE IF EXISTS trial_discounts",
    "DROP TABLE IF EXISTS hwid_devices",
    "DROP TABLE IF EXISTS admin_grants",
    "DROP TABLE IF EXISTS login_events",
    "DROP TABLE IF EXISTS admin_audit_log",
    "DROP TABLE IF EXISTS support_messages",
    "DROP TABLE IF EXISTS support_tickets",
    "ALTER TABLE users DROP COLUMN IF EXISTS cabinet_balance",
    "ALTER TABLE users DROP COLUMN IF EXISTS autopay_enabled",
)


def upgrade() -> None:
    for stmt in BASELINE_DDL:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in _DOWNGRADE:
        op.execute(stmt)
