"""Статистика шлюзов должна считать выручку от КЛИЕНТОВ, а не всё подряд.

ЧТО БЫЛО, ЧАСТЬ ПЕРВАЯ. Проверочные платежи при настройке шлюза бот помечает
`is_test = True`, но четыре запроса статистики флага не видели: экран «Статистика
ЮMoney» показывал 164 транзакции, из которых 24 — проверки. У двух шлюзов было
хуже: весь показанный «доход» (4 ₽ у Heleket, 2 ₽ у Telegram Stars) состоял ИЗ
ОДНИХ проверок, и шлюз выглядел работающим и зарабатывающим.

ЧАСТЬ ВТОРАЯ, И ОНА ДОРОЖЕ. Владелец покупает тарифы и дарит подарки на своём же
аккаунте, проверяя магазин. Эти платежи `is_test` НЕ помечаются — они идут
обычным путём, — а деньги ходят по кругу: из его кармана в его же кассу. На
боевых данных 15 сентября 2026 это 11 463 ₽ из 31 695 ₽ по ЮMoney, больше трети
показанной выручки.

Поэтому условий два, и тест стережёт оба: статистика считает только транзакции с
`is_test = false` И только тех, у кого роль `USER`.

ЧТО ЗАПЕРТО ЗДЕСЬ. Правка не переписывает их запросы, а подменяет на время
вызова сессию: посредник дописывает «не тестовая» к SELECT, который метод
отправит. Тест проверяет именно это поведение на настоящих объектах SQLAlchemy —
подделана только сессия, запросы собирает код базы.

Отдельно стережём предохранитель: условие дописывается ТОЛЬКО там, где в запросе
участвует таблица транзакций. Без него `.where()` по чужой таблице добавил бы
декартово произведение — тихо и с неверными числами.

Запуск — внутри образа бота:

  docker run --rm --env-file .env --network remnawave-network \
    -v /opt/remnashop/admin_src/overlay_patches:/opt/remnashop/overlay_patches:ro \
    -v /opt/remnashop/admin_src/tests:/tmp/tests:ro \
    remnashop-remnashop sh -c 'pip install -q --target /tmp/pylibs pytest pytest-asyncio \
      && PYTHONPATH=/tmp/pylibs python -m pytest /tmp/tests/test_stats_exclude_test.py \
         -v --asyncio-mode=auto'
"""

import importlib

import pytest
from sqlalchemy import func, select

patch = importlib.import_module("overlay_patches.stats_exclude_test")
target = importlib.import_module("src.infrastructure.database.dao.transaction")
models = importlib.import_module("src.infrastructure.database.models")

Transaction = models.Transaction


class SpySession:
    """Сессия, которая ничего не выполняет, а запоминает пришедший запрос."""

    def __init__(self, scalar_value: int = 0) -> None:
        self.seen = []
        self.scalar_value = scalar_value

    async def execute(self, statement, *args, **kwargs):
        self.seen.append(statement)
        raise _Stop()

    async def scalar(self, statement, *args, **kwargs):
        self.seen.append(statement)
        return self.scalar_value


class _Stop(Exception):
    """Дальше выполнять нечего: нас интересует только сам запрос."""


def sql(statement) -> str:
    return str(statement.compile(compile_kwargs={"literal_binds": True}))


def dao(session):
    """DAO без __init__: он строит конвертеры DTO, а статистике нужна лишь сессия."""
    instance = object.__new__(target.TransactionDaoImpl)
    instance.session = session
    return instance


@pytest.fixture(autouse=True)
def applied():
    """Правка идемпотентна, но порядок тестов не должен на это влиять."""
    patch.apply()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["count_total", "count_completed", "count_free"])
async def test_counters_exclude_test_payments(method: str) -> None:
    """Все три счётчика уходят в базу уже с обоими условиями."""
    session = SpySession()
    await getattr(dao(session), method)()
    assert len(session.seen) == 1
    text = sql(session.seen[0])
    assert "is_test IS false" in text
    assert "NOT IN" in text and "users" in text, "покупки своих учёток тоже не считаем"


@pytest.mark.asyncio
async def test_gateway_stats_excludes_test_payments() -> None:
    """Главный запрос — тот, что рисует экран «Статистика ЮMoney»."""
    session = SpySession()
    with pytest.raises(_Stop):
        await dao(session).get_gateway_stats()
    text = sql(session.seen[0])
    assert "is_test IS false" in text
    # Условие обязано стоять в WHERE, а не внутри какой-нибудь из сумм: иначе
    # отфильтруется одна колонка, а счётчик транзакций останется прежним.
    assert "GROUP BY" in text
    assert text.index("is_test IS false") < text.index("GROUP BY")

    # Свои учётки отсекаются ПОДЗАПРОСОМ. Соединение с users добавило бы таблицу
    # во FROM рядом с GROUP BY — строки размножились бы, и девять сумм стали бы
    # неверными, оставаясь правдоподобными.
    assert "NOT IN (SELECT" in text.replace("\n", " ")
    from_part = text[: text.index("GROUP BY")].split("WHERE")[0]
    assert "users" not in from_part, "users не должна попасть во FROM основного запроса"


@pytest.mark.asyncio
async def test_other_reads_are_untouched() -> None:
    """Списки и карточки пробные оплаты показывать ОБЯЗАНЫ.

    Владелец должен находить собственную проверку в списке транзакций — иначе
    правка превратилась бы из «не считать» в «спрятать».
    """
    session = SpySession()
    instance = dao(session)
    # Метод не из списка статистики — сессию ему не подменяют вовсе.
    assert isinstance(instance.session, SpySession)
    assert set(patch.PATCHED) == {
        "count_total",
        "count_completed",
        "count_free",
        "get_gateway_stats",
    }


def test_foreign_table_is_not_filtered() -> None:
    """Предохранитель: запрос не про транзакции остаётся нетронутым.

    Допиши мы `is_test` к чужому запросу — SQLAlchemy добавил бы таблицу
    транзакций в FROM, и получилось бы декартово произведение: цифры не просто
    неверные, а правдоподобно неверные.
    """
    foreign = select(func.count()).select_from(models.User)
    assert patch._touches_transactions(foreign, Transaction.__table__) is False

    ours = select(func.count()).select_from(Transaction)
    assert patch._touches_transactions(ours, Transaction.__table__) is True


def test_garbage_statement_does_not_raise() -> None:
    """Незнакомая форма запроса не должна ронять статистику."""
    assert patch._touches_transactions(object(), Transaction.__table__) is False
    assert patch._touches_transactions(None, Transaction.__table__) is False


def test_transaction_without_a_buyer_is_not_dropped() -> None:
    """Пустой `user_id` — это не «своя учётка».

    В SQL `NULL NOT IN (…)` даёт NULL, то есть такая строка выпала бы из
    статистики МОЛЧА. Сегодня таких строк нет ни одной, но условие пишем так,
    чтобы завтрашняя не потерялась.
    """
    from src.core.enums import Role

    cond = str(patch.customers_only(Transaction, models.User, Role.USER)
               .compile(compile_kwargs={"literal_binds": True}))
    assert "IS NULL" in cond
    assert " OR " in cond


def test_only_regular_customers_count() -> None:
    """Отсекаем по РОЛИ: любая неклиентская учётка выпадает из выручки."""
    from src.core.enums import Role

    cond = str(patch.customers_only(Transaction, models.User, Role.USER)
               .compile(compile_kwargs={"literal_binds": True}))
    assert "users.role !=" in cond
