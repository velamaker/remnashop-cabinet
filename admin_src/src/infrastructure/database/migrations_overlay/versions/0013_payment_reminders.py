"""payment_reminders / notification_optouts: одно напоминание о незавершённой оплате.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-19

ЗАЧЕМ. Человек доходит до создания счёта и не доводит оплату: за 90 дней 34 брошенных
счёта, и каждый третий из них человек потом оплачивает сам, а остальные не возвращаются
никогда. Через ~10 минут после такого счёта бот пишет ОДНО сообщение.

ЧЕГО ЗДЕСЬ НАМЕРЕННО НЕТ — ПЛАТЁЖНОЙ ССЫЛКИ. Первоначальный проект хранил ссылку счёта
и присылал её же; восемь скептиков нашли в этом пять дыр, из них четыре денежные:

  * у пополнения, подарка и докупок метка заказа пишется ПОСЛЕ создания счёта, и счёт
    «двойника» остаётся без метки — намеренно не показанный человеку. Его ссылка,
    присланная напоминанием, провела бы платёж как покупку синтетического тарифа с
    нулевой длительностью;
  * оплата ОДНОГО счёта не гасит остальные PENDING того же человека, и напоминание по
    соседнему счёту уводило бы платить второй раз;
  * повторная оплата уже проведённого счёта съедается молча (COMPLETED → COMPLETED
    пишет warning и выходит), а ссылка ЮMoney живёт вечно;
  * в счёте заморожены цена, скидка и снимок тарифа — оплата через час применяет их
    поверх изменившегося состояния.

Поэтому напоминание ведёт человека в кабинет, где счёт создаётся заново обычным путём,
а эти таблицы хранят только факты отправки.

`payment_reminders.payment_id` — PRIMARY KEY: второго напоминания по счёту не бывает в
принципе, а не «по проверке в коде». Захват (`status` 'new' → 'claimed') — тот же CAS,
что у скидки на продление: рестарт посреди прохода не даёт второго сообщения, досылки
нет намеренно.

`notification_optouts` — общий отказ «не пишите мне» по видам сообщений. Такого поля в
базе не было вовсе: `is_blocked` — это бан со стороны магазина, `is_bot_blocked` — уже
случившаяся блокировка бота, а способа сказать «не пиши» у человека не существовало.
Кнопка «Не напоминать» под сообщением пишет строку сюда.

FK на users(id) обязательны: scripts/merge-duplicate.py переносит таблицы двойника по
FK (урок переноса остатка).

downgrade() удаляет таблицы: журнал отправок потеряется, и напоминания смогут уйти
повторно по тем же счетам. На проде не вызывать.
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS payment_reminders (
            payment_id   UUID          PRIMARY KEY,
            user_id      INTEGER       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind         VARCHAR(16)   NOT NULL,
            amount       NUMERIC(12,2),
            currency     VARCHAR(8),
            status       VARCHAR(16)   NOT NULL DEFAULT 'new',
            skip_reason  VARCHAR(32),
            invoice_at   TIMESTAMPTZ   NOT NULL,
            claimed_at   TIMESTAMPTZ,
            sent_at      TIMESTAMPTZ,
            tg_result    VARCHAR(16),
            paid_at      TIMESTAMPTZ,
            created_at   TIMESTAMPTZ   NOT NULL DEFAULT now(),
            CONSTRAINT ck_pr_status CHECK (status IN ('new','claimed','sent','skipped','failed'))
        )
        """
    )
    # Выборка крона: «что уже разобрано по этому человеку за сутки» и «что осталось».
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pr_user_sent ON payment_reminders (user_id, sent_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pr_status ON payment_reminders (status, invoice_at)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS notification_optouts (
            user_id    INTEGER      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind       VARCHAR(32)  NOT NULL,
            created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
            PRIMARY KEY (user_id, kind)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS notification_optouts")
    op.execute("DROP TABLE IF EXISTS payment_reminders")
