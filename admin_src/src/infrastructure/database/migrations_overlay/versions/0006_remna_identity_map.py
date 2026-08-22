"""remna_identity_map: связка «uuid панели ↔ числовой id» — снимок ДО апгрейда на 3.x.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-22

Зачем таблица. В Remnawave 3.0.0 у пользователя УБРАН `uuid`: миграция панели
`20260720132335_drop_user_uuid` дропает колонку `users.uuid` БЕЗ переноса данных,
и все маршруты переходят на числовой `id`. Наша база хранит именно uuid —
`subscriptions.user_remna_id`, `reserve_grants.remna_uuid`,
`subscription_freezes.remna_uuid`. После апгрейда сопоставить наши строки с
пользователями панели будет НЕЧЕМ: uuid в панели больше не существует.

Снимок надо снять, пока панель ещё 2.8.x — она отдаёт оба идентификатора рядом
(`UsersSchema` 2.8.35 содержит и `uuid`, и `id`). Таблица заполняется одним
read-only обходом `GET /api/users`, панель при этом не меняется.

Почему в базе, а не только файлом. Дамп в /opt/remnashop-backups — страховка на
случай потери базы; таблица — рабочий источник для перезаписи идентичности, её
видно из кода и её нельзя забыть перенести при переезде сервера.

Запасные пути восстановления существуют (имя вида `rs_<telegram_id>` / `rs_web_<id>`,
короткий uuid из ссылки подписки), но это именно восстановление постфактум, а не
страховка: у части людей имя могло быть изменено руками в панели.

downgrade() удаляет таблицу — данные снимка при этом теряются, поэтому в проде его
вызывать не нужно; снимок пересоздаётся только на живой панели 2.8.x.
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS remna_identity_map (
            panel_uuid   uuid PRIMARY KEY,
            panel_id     bigint NOT NULL,
            username     varchar(64),
            short_uuid   varchar(64),
            telegram_id  bigint,
            email        varchar(255),
            captured_at  timestamptz NOT NULL DEFAULT timezone('UTC', now())
        )
        """
    )
    # По числовому id ищем в обратную сторону — когда панель уже 3.x и uuid
    # приходит только из наших старых строк.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_remna_identity_map_panel_id "
        "ON remna_identity_map (panel_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS remna_identity_map")
