"""plan_change_carryovers: журнал переноса остатка при смене тарифа.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-17

Смена тарифа больше не сжигает оставшиеся дни: остаток пересчитывается в дни нового
тарифа по цене дня (services/overlay_plan_change.py). Запись пишется на КАЖДУЮ смену
не-триала, даже с бонусом 0, и держит три вещи:
  * идемпотентность — `payment_id UNIQUE`: повтор вебхука или «Выдать» второй раз не
    перенесёт остаток дважды (у промокода счёта нет — NULL, их может быть много);
  * отсечку следующего переноса — всё, что оплачено до записи, уже пересчитано в
    перенос; без неё старые оплаты посчитались бы второй раз;
  * алерт при возврате — `source_payment_ids` называет платежи, из которых дни ушли
    в перенос (GIN-индекс для поиска по одному платежу).

FK на users(id) обязателен: scripts/merge-duplicate.py переносит таблицы двойника
по FK сам. Без FK журнал остался бы у удалённого двойника (ON DELETE CASCADE стёр
бы его), и отсечка пропала бы — создающий счёт снова попал бы в слои.

downgrade() удаляет журнал — на проде не вызывать: пропадут отсечки, и следующая
смена тарифа оценит уже перенесённые дни второй раз.
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS plan_change_carryovers (
            id                  BIGSERIAL     PRIMARY KEY,
            payment_id          UUID          UNIQUE,
            source              VARCHAR(16)   NOT NULL,
            user_id             INTEGER       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            old_subscription_id INTEGER       NOT NULL,
            subscription_id     INTEGER       NOT NULL,
            old_plan_id         INTEGER,
            new_plan_id         INTEGER       NOT NULL,
            new_duration        INTEGER       NOT NULL,
            mode                VARCHAR(16)   NOT NULL,
            remaining_seconds   BIGINT        NOT NULL DEFAULT 0,
            currency            VARCHAR(8)    NOT NULL,
            value_amount        NUMERIC(14,4) NOT NULL DEFAULT 0,
            new_day_price       NUMERIC(14,6),
            bonus_days          INTEGER       NOT NULL DEFAULT 0,
            bonus_seconds       BIGINT        NOT NULL DEFAULT 0,
            lost_days           INTEGER       NOT NULL DEFAULT 0,
            capped              BOOLEAN       NOT NULL DEFAULT false,
            source_payment_ids  UUID[]        NOT NULL DEFAULT '{}',
            breakdown           JSONB         NOT NULL DEFAULT '[]'::jsonb,
            expire_before       TIMESTAMPTZ,
            expire_after        TIMESTAMPTZ   NOT NULL,
            created_at          TIMESTAMPTZ   NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pcc_subscription "
        "ON plan_change_carryovers (subscription_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pcc_user ON plan_change_carryovers (user_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pcc_sources "
        "ON plan_change_carryovers USING gin (source_payment_ids)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS plan_change_carryovers")
