"""reserve_grants: резерв выдаётся заново после каждого истечения, а не один раз в жизни.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-21

Таблица была заведена с PRIMARY KEY по user_id — то есть физически одна строка на
человека НАВСЕГДА. Человек, у которого подписка истекла второй раз, резерва уже не
получал: дедуп крона видел старую строку и пропускал его. Владельцу это нужно иначе —
резерв положен на каждое истечение.

Меняем ключ: суррогатный id как PK (история выдач копится), а «не выдать второй резерв
поверх действующего» обеспечивает ЧАСТИЧНЫЙ уникальный индекс по user_id среди НЕзакрытых
строк. Ограничение «одна выдача на цикл подписки» живёт в запросе крона (granted_at
относительно текущего subscriptions.expire_at), в схеме ему места нет: срок подписки
таблице резерва не принадлежит.

Данные не теряются: старые строки получают id автоматически (BIGSERIAL заполняет их при
добавлении колонки) и остаются в истории.

downgrade() возвращает прежний PK по user_id и потому НЕ пройдёт, если у кого-то успело
накопиться больше одной выдачи, — это осознанно: молча удалять чужую историю выдач
хуже, чем упасть с внятной ошибкой. В проде downgrade не вызывается.
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_UPGRADE = (
    # Суррогатный ключ. BIGSERIAL проставит значения и уже существующим строкам.
    "ALTER TABLE reserve_grants ADD COLUMN IF NOT EXISTS id BIGSERIAL",
    # Снимаем старый PK, только если он действительно по user_id: на повторном прогоне
    # (или на установке, где 0005 уже отработала) PK стоит по id и трогать его нельзя.
    """
    DO $$
    DECLARE pk_name text;
    BEGIN
        SELECT c.conname INTO pk_name
          FROM pg_constraint c
         WHERE c.conrelid = to_regclass('reserve_grants') AND c.contype = 'p';
        IF pk_name IS NOT NULL AND EXISTS (
            SELECT 1
              FROM pg_constraint c
              JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
             WHERE c.conname = pk_name AND a.attname = 'user_id'
        ) THEN
            EXECUTE format('ALTER TABLE reserve_grants DROP CONSTRAINT %I', pk_name);
        END IF;
    END $$
    """,
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conrelid = to_regclass('reserve_grants') AND contype = 'p'
        ) THEN
            ALTER TABLE reserve_grants ADD PRIMARY KEY (id);
        END IF;
    END $$
    """,
    # Действующая выдача у человека может быть только одна. Закрытые (ended = true)
    # индекс не покрывает — на них история и копится.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_reserve_grants_open "
    "ON reserve_grants (user_id) WHERE ended = false",
)

_DOWNGRADE = (
    "DROP INDEX IF EXISTS ux_reserve_grants_open",
    """
    DO $$
    DECLARE pk_name text;
    BEGIN
        SELECT c.conname INTO pk_name
          FROM pg_constraint c
         WHERE c.conrelid = to_regclass('reserve_grants') AND c.contype = 'p';
        IF pk_name IS NOT NULL THEN
            EXECUTE format('ALTER TABLE reserve_grants DROP CONSTRAINT %I', pk_name);
        END IF;
    END $$
    """,
    # Упадёт, если у кого-то больше одной выдачи, — см. докстринг.
    "ALTER TABLE reserve_grants ADD PRIMARY KEY (user_id)",
    "ALTER TABLE reserve_grants DROP COLUMN IF EXISTS id",
)


def upgrade() -> None:
    for statement in _UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in _DOWNGRADE:
        op.execute(statement)
