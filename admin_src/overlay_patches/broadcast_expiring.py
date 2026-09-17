"""Рассылка «Истекают скоро»: получатели по условному plan_id.

ЧТО МЕНЯЕМ. `GetBroadcastAudienceUsers._execute` — единственное место базового
конвейера, где аудитория превращается в список людей. Для аудитории PLAN с
plan_id вида −(1000+N) подставляем тех, у кого подписка кончается в ближайшие N
дней (`EXPIRING_WHERE` в services/overlay_expiring_segment.py). Любой другой
вызов уходит в оригинал как есть.

ПОЧЕМУ СЕНТИНЕЛ. Аудитория базы — PG-enum, новое значение потребовало бы
`ALTER TYPE` и сломало бы чтение истории самим ботом. Номер тарифа же идёт
отдельным аргументом задачи и в историю не пишется. Настоящие тарифы имеют
положительные id, служебные у базы — небольшие отрицательные: диапазон
−1001…−1365 ни с чем не пересекается.

ПОЧЕМУ ОТКАЗ БЕЗОПАСЕН. Не применилась правка (апстрим изменил метод — сверка
sha ниже) — база ищет активные подписки на тарифе −1007, находит ноль, и
рассылка завершается, не отправив ни одного сообщения. Веб проверяет флаг
`_overlay_expiring` перед запуском и прячет сегмент, так что пустую рассылку
не запустить и руками. В Telegram-админке самого бота такая рассылка видна как
«По тарифу» — другого имени у аудитории там нет.

ПОЧЕМУ ОБЁРТКА, А НЕ КОПИЯ. Ветки остальных аудиторий остаются кодом базы, и их
правки приедут сами; от нас — одна ветка до вызова оригинала.
"""

from __future__ import annotations

from typing import Any

from . import expect_source

# sha256 метода базы v0.8.2. Сверяем, хотя метод не копируем: мы решаем за него,
# кто получит сообщение, и незамеченная смена логики аудитории — это рассылка
# не тем людям.
BASE_METHODS = {
    "GetBroadcastAudienceUsers._execute": (
        "a38ba53afb6c1c394bdf7d6f5b3209e50fd7c78fdfeebbccb6ca91721d19e13c"
    ),
}


def apply() -> str:
    from loguru import logger

    import src.application.use_cases.broadcast.queries.audience as target
    from src.core.enums import BroadcastAudience

    cls = target.GetBroadcastAudienceUsers
    if getattr(cls, "_overlay_expiring", False):
        return "уже применено"

    for qualname, sha in BASE_METHODS.items():
        expect_source(target, qualname, sha, qualname)

    original = cls._execute
    plan_audience = BroadcastAudience.PLAN

    async def _execute(self: Any, actor: Any, data: Any) -> Any:
        if getattr(data, "audience", None) != plan_audience:
            return await original(self, actor, data)
        # Модуль сегмента импортируем при вызове, а не в apply(): хук срабатывает
        # посреди импорта пакета use_cases, а пакет сервисов тянет за собой
        # половину бота — ранний импорт рисковал бы циклом. И через атрибут
        # модуля, а не пойманной ссылкой: так тест может подменить выборку.
        import src.infrastructure.services.overlay_expiring_segment as segment

        days = segment.days_from_plan_id(getattr(data, "plan_id", None))
        if days is None:
            return await original(self, actor, data)
        ids = await segment.expiring_user_ids(self.user_dao.session, days)
        users = await self.user_dao.get_by_ids(ids)
        logger.info(f"Overlay: рассылка «истекают скоро» ({days} дн.) — получателей {len(users)}")
        return users

    _execute.__name__ = original.__name__
    _execute.__qualname__ = original.__qualname__
    _execute.__doc__ = original.__doc__
    cls._execute = _execute
    cls._overlay_expiring = True
    return "аудитория PLAN с plan_id −(1000+N) → подписка кончается в ближайшие N дней"
