"""Статистика шлюзов считает только выручку от КЛИЕНТОВ.

ЧТО БЫЛО. Четыре запроса статистики — `count_total`, `count_completed`,
`count_free` и `get_gateway_stats` — считали подряд всё, что лежит в таблице
транзакций. В эту сумму попадали две чужие для выручки вещи.

ПЕРВОЕ: пробные оплаты при настройке шлюза. Владелец проводит через новый способ
оплаты проверочный платёж, бот помечает такую транзакцию `is_test = True`
(`web/endpoints/admin/gateways.py`), но статистика на флаг не смотрела, и экран
статистики шлюза считал проверки наравне с оплатами клиентов.

ВТОРОЕ, И ОНО ДОРОЖЕ: покупки самого владельца и служебных учёток. Владелец
покупает тарифы, дарит подарки и пополняет баланс на своём же аккаунте, проверяя
магазин, — эти платежи `is_test` не помечаются вовсе, потому что идут обычным
путём. Деньги при этом ходят по кругу: из кармана владельца в его же кассу.

НА БОЕВЫХ ДАННЫХ вторая часть оказалась больше первой: по основному шлюзу
заметная доля показанной выручки была собственными деньгами владельца, а у пары
шлюзов показанный доход состоял ИЗ ОДНИХ проверок — то есть шлюз, через который
не прошло ни рубля от клиентов, выглядел работающим и зарабатывающим.

ПОЧЕМУ ИСКЛЮЧАЕМ, А НЕ ПОКАЗЫВАЕМ ОТДЕЛЬНО. Наш собственный кабинет уже считает
так: в `admin_src/src/web/endpoints/admin/statistics.py` каждый запрос несёт
`is_test = false`. То есть бот и кабинет расходились в цифрах по одному и тому же
магазину, и правым был кабинет.

ГРАНИЦА «СВОЙ / КЛИЕНТ» — ЭТО РОЛЬ, А НЕ СПИСОК ИМЁН. Исключаем всех, у кого роль
не `USER` (OWNER, ADMIN, DEV, PREVIEW, SYSTEM): это учётки, которыми магазин
проверяют, а не покупают в нём. Отдельный список id завёл бы вторую правду,
которую надо помнить и обновлять руками.
У правила есть цена, и её надо знать: выдадите админскую роль НАСТОЯЩЕМУ
покупателю — и его прошлые покупки уйдут из статистики задним числом. Если
такое понадобится — заводите человеку отдельный аккаунт, а не роль.

ЧЕГО ЭТО НЕ КАСАЕТСЯ. Списки транзакций, выгрузки и карточка пользователя
показывают всё как прежде: владелец обязан находить собственную покупку. Правка
меняет только СТАТИСТИКУ — ответ на вопрос «сколько магазин заработал».

ПОЧЕМУ ОБЁРТКА, А НЕ КОПИЯ ЗАПРОСОВ. `get_gateway_stats` — это девять оконных
сумм на семьдесят строк. Скопировать их сюда ради одного условия значит взять на
себя весь этот код навсегда. Вместо этого подменяем на время вызова ОДНУ вещь —
сессию: наш посредник дописывает «не тестовая» к любому SELECT, который метод
отправит. Запрос базы остаётся их, и новые поля в нём приедут сами.

ПРЕДОХРАНИТЕЛЬ. Условие дописывается ТОЛЬКО если в запросе действительно
участвует таблица транзакций. Иначе `.where()` по чужой таблице добавил бы
декартово произведение — тихо и с неверными числами. Перепишут метод на другую
таблицу — он просто останется без нашего условия, а sha256-сверка ниже об этом
сообщит.
"""

from __future__ import annotations

from typing import Any

from . import expect_source

# sha256 методов базы v0.8.2. Пинуем все четыре: наша правка меняет СМЫСЛ цифры,
# которую они считают, и незамеченное изменение запроса — это незамеченная ошибка
# в деньгах.
BASE_METHODS = {
    "TransactionDaoImpl.count_total": (
        "55057ee9c9aaf63abccc9d02c7d88f71b7b03d78bf9e0e6293ba1e7b392a0f8d"
    ),
    "TransactionDaoImpl.count_completed": (
        "6bdd7b2f8509ae430e6cd8bbebbf057a47b6b93b9e24576f6a0d8f12cbb6c382"
    ),
    "TransactionDaoImpl.count_free": (
        "32b14c0c21d26bec026506d4a448930951348a387b1cecdeba893452375f0cb3"
    ),
    "TransactionDaoImpl.get_gateway_stats": (
        "bc6b3ce63362b197eb4f1e8a998209288aa3589ece0cd59651f8453e17ae4cab"
    ),
}

# Методы статистики. Только они: списки транзакций, выгрузки и карточка человека
# пробные оплаты показывать ОБЯЗАНЫ — иначе владелец не найдёт собственную проверку.
PATCHED = tuple(name.split(".", 1)[1] for name in BASE_METHODS)


def _touches_transactions(statement: Any, table: Any) -> bool:
    """Участвует ли в запросе таблица транзакций."""
    try:
        return table in statement.get_final_froms()
    except Exception:  # noqa: BLE001 — незнакомая форма запроса: не трогаем
        return False


def customers_only(transaction: Any, user: Any, role_user: Any) -> Any:
    """Условие «покупатель — клиент, а не своя учётка».

    Подзапросом, а не соединением: у `get_gateway_stats` есть GROUP BY и девять
    оконных сумм, и лишняя таблица во FROM размножила бы строки — числа стали бы
    неверными, но правдоподобными. `NOT IN` по скалярному подзапросу на группы не
    влияет вовсе.

    `user_id` в схеме отмечен обязательным, но пустое значение всё равно
    оговариваем: в SQL `NULL NOT IN (…)` даёт NULL, то есть строка выпала бы из
    статистики молча. Транзакция без покупателя — это не «своя учётка».
    """
    from sqlalchemy import or_, select

    staff = select(user.id).where(user.role != role_user)
    return or_(transaction.user_id.is_(None), transaction.user_id.notin_(staff))


def apply() -> str:
    from sqlalchemy import Select

    import src.infrastructure.database.dao.transaction as target
    from src.core.enums import Role
    from src.infrastructure.database.models import Transaction, User

    dao = target.TransactionDaoImpl
    if getattr(dao, "_overlay_no_test_stats", False):
        return "уже применено"

    for qualname, sha in BASE_METHODS.items():
        expect_source(target, qualname, sha, qualname)

    table = Transaction.__table__
    not_test = Transaction.is_test.is_(False)
    from_customer = customers_only(Transaction, User, Role.USER)

    def exclude_test(statement: Any) -> Any:
        if isinstance(statement, Select) and _touches_transactions(statement, table):
            return statement.where(not_test).where(from_customer)
        return statement

    class NoTestSession:
        """Сессия, которая для этих четырёх методов не видит пробных оплат.

        Всё, кроме чтения, уходит настоящей сессии как есть: подменять поведение
        записи мы не собираемся, а методы статистики ничего и не пишут.
        """

        def __init__(self, session: Any) -> None:
            self._session = session

        def __getattr__(self, name: str) -> Any:
            return getattr(self._session, name)

        async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
            return await self._session.execute(exclude_test(statement), *args, **kwargs)

        async def scalar(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
            return await self._session.scalar(exclude_test(statement), *args, **kwargs)

    def without_test(method: Any) -> Any:
        async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            # Свой `self` вместо подмены поля у настоящего: DAO живёт на всё время
            # запроса, и временная порча его сессии задела бы соседний вызов, если
            # он окажется в том же запросе.
            shim = object.__new__(type(self))
            shim.__dict__ = dict(self.__dict__)
            shim.session = NoTestSession(self.session)
            return await method(shim, *args, **kwargs)

        wrapper.__name__ = method.__name__
        wrapper.__qualname__ = method.__qualname__
        wrapper.__doc__ = method.__doc__
        return wrapper

    for name in PATCHED:
        setattr(dao, name, without_test(getattr(dao, name)))

    dao._overlay_no_test_stats = True
    return "статистика считает только выручку от клиентов"
