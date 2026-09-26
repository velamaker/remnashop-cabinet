"""churn_signals: «Всё работает?» после первого подключения и «давно не подключался».

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-25

ЗАЧЕМ. Люди уходят молча: из 200 пробных 169 не подключились ни разу и ничего не
написали, а платящие перестают пользоваться раньше, чем перестают платить, — и мы
узнаём об этом только по непродлённой подписке. Два сигнала ДО ухода:

  * `check` — через сутки после ПЕРВОГО подключения один вопрос «Всё работает?» с
    ответом одной кнопкой; «не работает» ведёт в самодиагностику кабинета;
  * `idle` — платящему, который давно не подключался, одно мягкое «всё в порядке?».

ПОЧЕМУ ТАБЛИЦА, А НЕ ФАЙЛ СОСТОЯНИЯ. Нужно помнить три вещи, и все три — про людей:
кого уже спрашивали (второго «Всё работает?» не бывает), что человек ответил (это и
есть смысл первого сигнала — владелец видит долю «не работает») и когда в последний
раз писали про простой. Крон исполняет воркер, ответ пишет процесс бота — JSON они
затирали бы друг другу (урок стейта «трафик 80 %»).

ПОВТОР ЗАПРЕЩАЕТ БАЗА, А НЕ КОД:
  * `ux_cs_check_once` — один вопрос на человека за всю жизнь;
  * `ux_cs_idle_stretch` — одно сообщение на один и тот же «последний онлайн»: пока
    человек не подключался, отметка панели не меняется, и второй прогон, даже
    параллельный, упрётся в индекс.
Захват строки идёт ДО отправки (INSERT … RETURNING): не вставилось — не шлём.

`seen_at` — тот факт панели, на который мы отреагировали: время первого подключения
для `check` и последнего онлайна для `idle`. По нему видно, почему человек попал в
выборку, и на нём же держится второй индекс.

`returned_at` — подключился ли человек после сообщения о простое: это единственная
честная мера того, помогло ли сообщение.

FK на users(id) обязателен: scripts/merge-duplicate.py переносит таблицы двойника по
FK, а удаление человека должно уносить и его ответы.

downgrade() удаляет таблицу: пропадут ответы людей, и вопрос «Всё работает?» сможет
уйти второй раз. На проде не вызывать.
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS churn_signals (
            id           BIGSERIAL     PRIMARY KEY,
            user_id      INTEGER       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind         VARCHAR(16)   NOT NULL,
            status       VARCHAR(16)   NOT NULL DEFAULT 'claimed',
            seen_at      TIMESTAMPTZ,
            tg_result    VARCHAR(16),
            answer       VARCHAR(16),
            answered_at  TIMESTAMPTZ,
            returned_at  TIMESTAMPTZ,
            claimed_at   TIMESTAMPTZ,
            sent_at      TIMESTAMPTZ,
            created_at   TIMESTAMPTZ   NOT NULL DEFAULT now(),
            CONSTRAINT ck_cs_kind CHECK (kind IN ('check', 'idle')),
            CONSTRAINT ck_cs_status CHECK (status IN ('claimed', 'sent', 'failed')),
            CONSTRAINT ck_cs_answer CHECK (answer IS NULL OR answer IN ('works', 'broken'))
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_cs_check_once "
        "ON churn_signals (user_id) WHERE kind = 'check'"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_cs_idle_stretch "
        "ON churn_signals (user_id, seen_at) WHERE kind = 'idle'"
    )
    # Сводка админки и кулдаун крона: «что было за 30 дней» и «когда писали этому».
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cs_kind_created ON churn_signals (kind, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cs_user_kind ON churn_signals (user_id, kind, sent_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS churn_signals")
