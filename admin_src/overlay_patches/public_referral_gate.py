"""Реферальная программа в кабинете не требует подтверждённой почты у тех, у кого её нет.

ЧТО БЫЛО. Базовая ручка `/public/referral/program` открывается только тем, у кого
`is_email_verified`. Регистрация через Telegram почты не спрашивает вовсе, поэтому
пригласить друга не мог никто из них: на 30.08.2026 это 1069 человек из 1090, то
есть практически все. Человеку показывали «подтвердите почту» — подтверждать
нечего, или, если он всё же попадал в ветку телеграма на фронте, ещё хуже:
«реферальная программа отключена», хотя она включена.

ПОЧЕМУ НЕ ТРОГАЕМ ВТОРОЕ УСЛОВИЕ. Требование действующей подписки — осознанное
правило владельца, оно остаётся как есть. Здесь снимается ровно почтовый гейт и
ровно у тех, кто почтой не регистрировался.

КАК ОБХОДИМСЯ БЕЗ КОПИИ ОБРАБОТЧИКА. Проверка стоит ВНУТРИ базовой функции, и
соблазн — перенести её тело к себе, выкинув одну строку. Именно так 23.08 родился
`NameError: PENDING_PROMO_KEY`: перенесённое тело ищет глобали в НАШЕМ модуле.
Поэтому тело не трогаем совсем: подменяем то, что функция ЧИТАЕТ. Обёртка отдаёт
базе КОПИЮ пользователя с поднятым флагом, и дальше отрабатывает её собственный,
нетронутый код.

Копия обязательно через `dataclasses.replace`, а не `copy.copy`: у DTO бота есть
`TrackableMixin` с общим словарём `_changed_data`, и поверхностная копия пишет
изменение В ОРИГИНАЛ — запросный объект пользователя оказался бы «грязным», с
`is_email_verified=True` наготове к записи в базу. `replace` строит объект заново,
`__post_init__` даёт ему свой словарь (проверено).

КУДА ВСТРАИВАЕМСЯ. FastAPI 0.140 читает `dependant.call` на КАЖДОМ запросе
(routing.py:350), а не захватывает функцию в замыкание, поэтому подмена видна
сразу. `public/__init__.py` подключает роутер ПОСЛЕ импорта модуля, то есть после
нашего хука, и держит ссылку на него (`_IncludedRouter`), а не копию маршрутов.
На всякий случай проставляем и `endpoint`, и `dependant.call`, и имя в модуле:
если апстрим когда-нибудь начнёт пересобирать маршрут из `endpoint`, сигнатура
обёртки совпадает с базовой (`user`, `___dishka_request`), и dependant соберётся
тот же самый.

ПРОВЕРЕНО СКВОЗНЫМ ЗАПРОСОМ на боевом образе: до правки ручка отвечала
`403 verified email`, после — `200` с реферальным кодом.
"""

from __future__ import annotations

import dataclasses

# Импорты модульного уровня: FastAPI разбирает аннотации обработчика, а ищет имена
# в глобалях модуля, где он объявлен. Держать их внутри apply() — тот же NameError.
from fastapi import Request

from src.core.enums import AuthType
from src.web.endpoints.public._common import CurrentUser
from src.web.schemas import ReferralProgramResponse

from . import PatchTargetChanged, expect_source

# sha256 обработчика get_referral_program в базе v0.8.2 (вместе с декораторами).
# Изменится — правка не применится и скажет, что именно трогал апстрим.
BASE_HANDLER_SHA256 = "e4e249bdc7628345fd3aa7c14d35deae939fe7969d60e2099bb0b2143392cecf"

ROUTE_PATH = "/referral/program"


def _email_gate_applies(user: object) -> bool:
    """Нужно ли этому человеку подтверждать почту. Тот же ответ, что и на покупке.

    Держим ОДНО правило на весь кабинет (см. `_assert_web_purchase_email_verified`
    в public_subscription.py): гейт выключен тумблером — не нужно никому; человек
    регистрировался не почтой — не нужно ему.
    """
    from src.infrastructure.services.overlay_email_gate import is_enabled

    if not is_enabled():
        return False
    return getattr(user, "auth_type", None) == AuthType.EMAIL


def apply() -> str:
    import src.web.endpoints.public.referral as target

    original = getattr(target, "get_referral_program", None)
    if original is None:
        raise PatchTargetChanged(
            "в public/referral.py больше нет get_referral_program — база перестроила "
            "реферальную ручку, и почтовый гейт снова закроет всех телеграм-юзеров"
        )
    if getattr(original, "_overlay_wrapped", False):
        return "уже обёрнут"

    expect_source(target, "get_referral_program", BASE_HANDLER_SHA256, "get_referral_program")

    route = next(
        (r for r in target.router.routes if getattr(r, "path", None) == ROUTE_PATH), None
    )
    if route is None:
        raise PatchTargetChanged(
            f"в роутере base нет маршрута {ROUTE_PATH} — база переименовала путь "
            "реферальной программы, обёртка встала бы в пустоту"
        )

    async def get_referral_program(
        user: CurrentUser, *, ___dishka_request: Request
    ) -> ReferralProgramResponse:
        if not _email_gate_applies(user) and not user.is_email_verified:
            # Копия, а не исходный объект: он живёт весь запрос и уедет в другие места.
            user = dataclasses.replace(user, is_email_verified=True)
        return await original(user, ___dishka_request=___dishka_request)

    get_referral_program._overlay_wrapped = True  # type: ignore[attr-defined]
    route.endpoint = get_referral_program
    route.dependant.call = get_referral_program
    target.get_referral_program = get_referral_program
    return "реферальная программа открыта тем, кто регистрировался не почтой"
