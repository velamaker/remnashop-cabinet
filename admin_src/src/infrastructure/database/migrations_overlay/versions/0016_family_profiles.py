"""family_profiles: семейный тариф — отдельные профили для близких владельца подписки.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-26

ЗАЧЕМ. Семейный тариф отличается от обычного тем, что к подписке владельца можно
завести N профилей «для своих»: у каждого своя ссылка подписки, свои D устройств и
весь трафик тарифа. Участнику аккаунт не нужен — владелец пересылает ему ссылку.
Профили НЕ отнимают устройства у владельца: его лимит не трогаем вообще.

ПОЧЕМУ ПРОФИЛЬ — ЭТО СВОЯ СТРОКА users, А НЕ ВТОРАЯ ПОДПИСКА ВЛАДЕЛЬЦА. У человека в
боте одна ТЕКУЩАЯ подписка (`users.current_subscription_id`), и вебхуки панели ищут
человека по uuid только через неё. Вторую живую подписку того же человека бот не
увидит: её не продлит вебхук, а синхрон панели заведёт под неё безымянного двойника.
Поэтому профиль — «теневой» аккаунт без телеграма, почты и пароля (войти нельзя) со
своей текущей подпиской и своим пользователем панели `rs_fam_<id>`. Кто чей — знает
эта таблица.

ТРИ ТАБЛИЦЫ:
  * `family_plan_terms` — какие тарифы семейные: «N профилей × D устройств на профиль».
    Строки нет — тариф обычный. По умолчанию таблица пуста, то есть функция выключена
    и тумблером (assets/family.json), и отсутствием семейных тарифов;
  * `family_profiles` — профили и их жизненный цикл
    (creating → active ⇄ suspended → deleting; сбой создания — failed);
  * `family_events` — журнал: кто что сделал с профилем (разбор с владельцем).

ИДЕМПОТЕНТНОСТЬ ДЕРЖИТ БАЗА, А НЕ КОД:
  * `request_id UNIQUE` — двойной клик «создать» даёт один профиль и один вызов панели;
  * `ux_fp_owner_label` — два живых профиля с одним именем у владельца не заведутся
    (без учёта регистра; неудачные попытки имя не занимают);
  * `panel_username UNIQUE` — по имени крон находит в панели профиль, созданный перед
    падением процесса, и не заводит второго.

`traffic_reset_at` — когда профиль в последний раз получил свежий трафик (создание
или обнуление после оплаты владельца). Обнулять снова можно только за оплату позже
этой отметки: «сверил» и «обнулил» — разные события, и отметка сверки для этого не
годится (её сбрасывают и выключение, и только что заведённый профиль).

FK на users(id) обязательны: scripts/merge-duplicate.py переносит строки двойника по
FK, а удаление владельца должно уносить и его профили. `profile_user_id` — nullable:
у неудачной попытки теневой аккаунт уже удалён, а строка остаётся, чтобы повтор с тем
же `request_id` получил честный ответ «не вышло», а не создал профиль заново.

downgrade() удаляет таблицы: пропадёт связь «чей профиль», а пользователи панели
`rs_fam_*` и теневые аккаунты останутся — их придётся удалять руками. На проде не
вызывать.
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS family_plan_terms (
            plan_id             INTEGER      PRIMARY KEY REFERENCES plans(id) ON DELETE CASCADE,
            max_profiles        INTEGER      NOT NULL CHECK (max_profiles BETWEEN 1 AND 10),
            devices_per_profile INTEGER      NOT NULL CHECK (devices_per_profile BETWEEN 1 AND 10),
            updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS family_profiles (
            id                 BIGSERIAL    PRIMARY KEY,
            owner_user_id      INTEGER      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            profile_user_id    INTEGER      UNIQUE REFERENCES users(id) ON DELETE CASCADE,
            request_id         UUID         NOT NULL UNIQUE,
            label              VARCHAR(24)  NOT NULL,
            status             VARCHAR(16)  NOT NULL DEFAULT 'creating',
            suspend_reason     VARCHAR(24),
            device_limit       INTEGER      NOT NULL CHECK (device_limit >= 1),
            panel_username     VARCHAR(64)  UNIQUE,
            panel_uuid         UUID,
            suspended_at       TIMESTAMPTZ,
            last_reconciled_at TIMESTAMPTZ,
            traffic_reset_at   TIMESTAMPTZ,
            fail_count         INTEGER      NOT NULL DEFAULT 0,
            last_error         TEXT,
            created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
            CONSTRAINT ck_fp_status
                CHECK (status IN ('creating', 'active', 'suspended', 'deleting', 'failed'))
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_fp_owner_label "
        "ON family_profiles (owner_user_id, lower(label)) WHERE status <> 'failed'"
    )
    # Сверка семьи: «живые» профили владельца — их считает лимит и их трогает крон.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_fp_owner_live "
        "ON family_profiles (owner_user_id) WHERE status IN ('active', 'suspended')"
    )
    # Доводка зависших «создаю/удаляю» кроном: их единицы, индекс частичный.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_fp_pending "
        "ON family_profiles (updated_at) WHERE status IN ('creating', 'deleting')"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS family_events (
            id            BIGSERIAL    PRIMARY KEY,
            owner_user_id INTEGER      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            profile_id    BIGINT       REFERENCES family_profiles(id) ON DELETE SET NULL,
            kind          VARCHAR(24)  NOT NULL,
            actor         VARCHAR(16)  NOT NULL,
            details       JSONB,
            created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_fe_owner_created "
        "ON family_events (owner_user_id, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS family_events")
    op.execute("DROP TABLE IF EXISTS family_profiles")
    op.execute("DROP TABLE IF EXISTS family_plan_terms")
