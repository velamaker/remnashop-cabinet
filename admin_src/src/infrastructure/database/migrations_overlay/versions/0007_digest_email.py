"""digest_email_sends + email_opt_outs: месячная сводка письмом и отписка от неё.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-16

Зачем журнал на человека. Месячная отметка в assets/digest_state.json защищает от
второго прохода целиком, но не от обрыва посреди прохода: рестарт воркера или
дубль задачи после неё не видят, кому письмо уже ушло. Письмо, в отличие от
сообщения в Telegram, не отзовёшь и не отредактируешь — повтор виден человеку
сразу. Поэтому строка (user_id, month) занимается ДО отправки: INSERT … ON
CONFLICT DO NOTHING, и кто не занял строку, тот не шлёт. Застрявшая в `sending`
строка (упали между занятием и отправкой) повторно не отправляется — лучше одно
пропущенное письмо, чем два одинаковых.

Статусы: sending | sent | failed | no_traffic | usage_error | over_limit |
provider_blocked. Итог месяца в админке считается по ним же.

Зачем отдельная таблица отписок. Письмо обязано нести рабочую ссылку «Отписаться»,
и отписка должна переживать смену почты и повторную подписку; флаг на users —
правка базовой таблицы ради одной рассылки. `kind` оставлен, чтобы следующей
почтовой рассылке (например, email-рассылкам из админки) не понадобилась ещё одна
таблица.

downgrade() удаляет обе таблицы вместе с журналом и отписками — на проде его не
вызывать: люди, отписавшиеся по ссылке, снова начнут получать сводку.
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS digest_email_sends (
            user_id    INTEGER     NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            month      CHAR(7)     NOT NULL,
            status     VARCHAR(20) NOT NULL,
            error      VARCHAR(300),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (user_id, month)
        )
        """
    )
    # Итог месяца в админке выбирается по месяцу, а не по человеку.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_digest_email_sends_month "
        "ON digest_email_sends (month)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS email_opt_outs (
            user_id    INTEGER     NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind       VARCHAR(20) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (user_id, kind)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS email_opt_outs")
    op.execute("DROP TABLE IF EXISTS digest_email_sends")
