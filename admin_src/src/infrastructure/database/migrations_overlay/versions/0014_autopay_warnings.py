"""autopay_warnings: предупреждение «на балансе не хватит на автопродление».

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-20

ЗАЧЕМ. Автопродление списывает с рублёвого баланса за N дней до конца подписки. Если
денег не хватает, крон молча ничего не делает: человек включил тумблер, уверен, что
подписка продлится сама, — и обнаруживает отключённый VPN. Тумблер включён у людей,
у которых баланс нулевой, поэтому молчание здесь дороже лишнего сообщения.

Теперь за сутки ДО попытки списания приходит одно сообщение: сколько на балансе,
сколько нужно и до какого числа пополнить, с кнопкой пополнения на нужную сумму.

ПОЧЕМУ ТАБЛИЦА, А НЕ ФАЙЛ. Нужно помнить, кому и за какой СРОК уже писали: повтор
по тому же сроку — спам, а после продления (expire_at уехал вперёд) человек снова
имеет право на предупреждение. Ключ — подписка, а не человек: у одного человека
строка подписки меняется при смене тарифа, и новая подписка заслуживает своего
предупреждения.

`sent_for` хранит тот самый expire_at, к которому относилось предупреждение: сравнение
с текущим сроком и есть ответ «это уже другой период». Хранить дату отправки мало —
продление в тот же день дало бы «уже писали» на новый срок.

downgrade() удаляет таблицу: журнал отправок потеряется, и предупреждения уйдут
повторно по тем же срокам. На проде не вызывать.
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS autopay_warnings (
            subscription_id INTEGER      PRIMARY KEY REFERENCES subscriptions(id) ON DELETE CASCADE,
            user_id         INTEGER      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            sent_for        TIMESTAMPTZ  NOT NULL,
            short_by        NUMERIC(12,2),
            sent_at         TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
        """
    )
    # Выборка «кому писали недавно» в админской сводке и в самом кроне.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_autopay_warn_sent ON autopay_warnings (sent_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS autopay_warnings")
