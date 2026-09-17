"""Сегмент рассылки «Истекают скоро» (TG_EXPIRING) поверх базового конвейера.

ПОЧЕМУ НЕ СВОЙ ОТПРАВЩИК. У базовой рассылки есть всё, что нужно и что дорого
повторять: история в админке бота, отмена посреди отправки, обработка флуд-лимита
Telegram, удаление разосланного. Не хватает одного — аудитории «подписка
кончается в ближайшие N дней».

ПОЧЕМУ УСЛОВНЫЙ plan_id, А НЕ НОВОЕ ЗНАЧЕНИЕ ENUM. Аудитория базы — PG-enum
`broadcast_audience`; `ALTER TYPE` меняет схему бота, и новое значение сломало бы
чтение истории самим ботом. Зато у аудитории PLAN есть второй аргумент задачи —
`plan_id`, и он не хранится в истории. Настоящие id тарифов положительные, служебные
у базы — небольшие отрицательные, поэтому −(1000+N) ни с чем не пересекается:
правка `overlay_patches/broadcast_expiring.py` узнаёт его и подставляет получателей.

ОТКАЗ БЕЗОПАСЕН. Не применилась правка — база ищет тариф −1007, получателей ноль,
ни одного сообщения не уходит. Веб при этом сам прячет сегмент
(`expiring_patch_ready`), чтобы не запускать пустую рассылку.

Кого берём — одно условие `EXPIRING_WHERE`: и счётчик, и получатели, и соседние
пункты, которым нужен тот же сегмент, читают его отсюда.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import text

CHANNEL = "TG_EXPIRING"
DAYS_MIN, DAYS_MAX = 1, 60
DEFAULT_DAYS = 7

# Разбираем шире, чем разрешает форма (до года): если однажды пределы расширят,
# старая задача в очереди не уйдёт в поиск несуществующего тарифа.
_BASE = 1000
_DECODE_MAX_DAYS = 365

_FROM = "FROM users u JOIN subscriptions s ON s.id = u.current_subscription_id"

# Активная платная (без пробных) подписка, которая закончится в ближайшие N дней;
# до человека можно достучаться в Telegram. Резерв исключён: у сидящего на нём
# expire_at — срок бесплатной страховки, а не подписки, и «продлите» про неё
# только сбивает с толку.
EXPIRING_WHERE = (
    "u.is_blocked = false AND u.is_bot_blocked = false AND u.telegram_id IS NOT NULL "
    "AND s.status::text = 'ACTIVE' AND s.is_trial = false "
    "AND s.expire_at > now() AND s.expire_at <= now() + make_interval(days => :days) "
    "AND NOT EXISTS (SELECT 1 FROM reserve_grants r WHERE r.user_id = u.id AND r.ended = false)"
)


def valid_days(days: Any) -> bool:
    return isinstance(days, int) and not isinstance(days, bool) and DAYS_MIN <= days <= DAYS_MAX


def encode_expiring_plan_id(days: int) -> int:
    if not valid_days(days):
        raise ValueError(f"дни сегмента «Истекают скоро» вне {DAYS_MIN}–{DAYS_MAX}: {days!r}")
    return -(_BASE + days)


def days_from_plan_id(plan_id: Any) -> Optional[int]:
    """N из условного plan_id или None, если это настоящий тариф (или служебный id базы)."""
    if not isinstance(plan_id, int) or isinstance(plan_id, bool):
        return None
    if -(_BASE + _DECODE_MAX_DAYS) <= plan_id <= -(_BASE + 1):
        return -plan_id - _BASE
    return None


async def count_expiring(session: Any, days: int) -> int:
    result = await session.execute(text(f"SELECT count(*) {_FROM} WHERE {EXPIRING_WHERE}"), {"days": days})
    return int(result.scalar_one())


async def expiring_user_ids(session: Any, days: int) -> list[int]:
    result = await session.execute(
        text(f"SELECT u.id {_FROM} WHERE {EXPIRING_WHERE} ORDER BY u.id"), {"days": days}
    )
    return [int(r[0]) for r in result.all()]


def expiring_patch_ready() -> bool:
    """Применилась ли правка аудитории в ЭТОМ процессе.

    Импорт модуля и есть проверка: правка ставится хуком на его импорт
    (overlay_patches/_hooks.py), так что после импорта флаг либо стоит, либо
    правка не встала — и тогда рассылка ушла бы нулю получателей.
    """
    try:
        from src.application.use_cases.broadcast.queries.audience import (
            GetBroadcastAudienceUsers,
        )
    except Exception:  # noqa: BLE001 — нет модуля базы → сегмента нет
        return False
    return bool(getattr(GetBroadcastAudienceUsers, "_overlay_expiring", False))
