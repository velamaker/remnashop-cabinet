"""Дата обновления трафика: один источник правды вместо трёх приблизительных.

ЧТО БЫЛО НЕ ТАК. Базовая `core.utils.time.get_traffic_reset_delta` считает момент
обнуления расхода по двум неверным основаниям:

  1. ЯКОРЬ НЕ ТОТ. Для `MONTH_ROLLING` панель берёт `created_at` ПОЛЬЗОВАТЕЛЯ ПАНЕЛИ
     (`scheduler.js`, `getNextTrafficResetAt`), а база подставляет `created_at` НАШЕЙ
     строки подписки. Наша строка пересоздаётся при каждой смене тарифа, панельная
     дата — никогда: на живых данных дни у большинства активных подписок РАЗНЫЕ.
  2. ЧАСЫ НЕ ТЕ. База: DAY 00:00, WEEK 00:05, MONTH 00:10. Панель: 00:05 / 00:15 /
     00:20. Совпадает только `MONTH_ROLLING` (00:10). И база не знает правила
     «не раньше, чем через месяц после создания».

Из-за этого сообщение «трафик исчерпан» называет людям неверное число. Пока это
было единственным следствием, с ним жили. С докупкой трафика так нельзя: срок
прибавки считается ПРАВИЛЬНОЙ формулой, и расхождение означало бы, что кабинет
обещает одну дату, а бот называет другую — человек читает это как обман.

ЧТО ДЕЛАЕМ. Единственная реализация — `overlay_extra_traffic.next_traffic_reset`.
Здесь её результат подставляется в два места базы, и оба раза БЕЗ КОПИРОВАНИЯ ТЕЛА
базового метода (урок `NameError` в подтверждении подарка: перенести тело можно, а
шапку модуля с его именами — нельзя):

  * обёртка над `RemnaWebhookService._process_status` кладёт правильный якорь
    (`remna_user.created_at` — это дата ИЗ ПАНЕЛИ) в `ContextVar` на время вызова;
  * имя `get_traffic_reset_delta` подменяется В МОДУЛЕ-ПОТРЕБИТЕЛЕ, а не в
    `core.utils.time`: потребители делают `from … import get_traffic_reset_delta` на
    уровне модуля, то есть связывают старый объект функции при загрузке.

ПОЧЕМУ ContextVar, А НЕ ГЛОБАЛЬНАЯ ПЕРЕМЕННАЯ. Вебхуки идут параллельно, и глобаль
перепутала бы людей. Внутри `_process_status` есть `await`, но расчёт стоит ИНЛАЙНОМ
в теле, при сборке события, ДО `event_bus.publish` — то есть в той же задаче, где
контекст установлен. `finally: reset(token)` обязателен.

ТРЕТЬЕ МЕСТО — карточка подписки в меню бота (`menu/getters.py`) — чинится в
`menu_dialog.py`: там уже стоит обёртка над геттером, и ей есть куда сходить за
панельной датой. Остаётся неисправленным алерт «подключение недоступно»
(`menu/handlers.py`): у него на руках только `SubscriptionDto`, панельной даты взять
неоткуда, а ради одной строки заводить третью правку в чужом окне — плохая сделка.
"""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timedelta
from typing import Any, Optional

from loguru import logger

from src.infrastructure.services import overlay_extra_traffic as etraffic

from . import expect_source

# sha256 метода базы v0.8.2. Сверяем, потому что наша обёртка держится за ДВЕ вещи:
# что якорь приходит параметром `remna_user`, и что расчёт даты стоит внутри метода
# инлайном. Перенесёт апстрим расчёт в другое место — правка не применится и
# закричит, а не подменит молча изменившуюся логику.
BASE_METHODS = {
    "RemnaWebhookService._process_status": (
        "d28b88c10c25bf766ba7d2dbe03c842d7b023f982ea04e8f2a0b4ddb7f7eefb8"
    ),
}

# Дата создания пользователя ПАНЕЛИ на время обработки одного вебхука.
_PANEL_CREATED: ContextVar[Optional[datetime]] = ContextVar("rs_panel_created_at", default=None)


def panel_created_at() -> Optional[datetime]:
    """Якорь текущего вебхука. Пусто — значит считаем как раньше, по нашей строке."""
    return _PANEL_CREATED.get()


def traffic_reset_delta(strategy: Any, subscription_created_at: Optional[datetime] = None):
    """Замена базовой `get_traffic_reset_delta` для модуля вебхуков.

    Подпись и тип результата те же (`timedelta`), чтобы вызывающий код базы не
    заметил разницы: он сразу отдаёт результат в `i18n_format_expire_time`.
    Ноль — «сброса нет» (`NO_RESET`) ровно как у базы, а не «сброс прямо сейчас».
    """
    anchor = _PANEL_CREATED.get() or subscription_created_at
    now = etraffic.now_utc()
    moment = etraffic.next_traffic_reset(strategy, anchor, now)
    if moment is None:
        return timedelta(seconds=0)
    return moment - now


def apply() -> str:
    """Обёртка над `_process_status` + подстановка имени в модуле вебхуков."""
    import src.application.services.remnawave as target

    for qualname, sha in BASE_METHODS.items():
        expect_source(target, qualname, sha, qualname)

    cls = target.RemnaWebhookService
    # Флаг на КЛАССЕ и со своим именем: этот же класс оборачивают ещё две правки
    # (восстановление uuid, фильтр напоминаний), и кто окажется снаружи — зависит от
    # порядка хуков. По флагу на функции повторный вызов не узнал бы нас под чужой
    # обёрткой.
    if not cls.__dict__.get("_overlay_traffic_reset", False):
        base_process_status = cls._process_status

        async def _process_status(self, user, current_subscription, event, remna_user):  # noqa: ANN001, ANN202
            # Тело базы НЕ трогаем — только кладём правильный якорь на время вызова.
            token = _PANEL_CREATED.set(getattr(remna_user, "created_at", None))
            try:
                return await base_process_status(self, user, current_subscription, event, remna_user)
            finally:
                # Без reset контекст утёк бы в соседний вебхук той же задачи.
                _PANEL_CREATED.reset(token)

        cls._process_status = _process_status
        cls._overlay_traffic_reset = True

    # Подстановка имени в МОДУЛЕ-ПОТРЕБИТЕЛЕ: `core.utils.time` править бесполезно,
    # потребитель связал старый объект функции на своём импорте.
    if getattr(target.get_traffic_reset_delta, "_overlay_traffic_reset", False):
        return "уже применено"
    traffic_reset_delta._overlay_traffic_reset = True
    target.get_traffic_reset_delta = traffic_reset_delta
    logger.debug("Overlay: дата обновления трафика считается формулой панели")
    return "дата обновления трафика — по формуле панели и по её же дате создания"
