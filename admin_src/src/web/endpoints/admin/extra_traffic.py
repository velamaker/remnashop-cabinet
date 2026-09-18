"""Админ: настройки докупки трафика и сводка по ней (overlay).

Страница «Докупка трафика»: тумблер продаж (ВЫКЛ по умолчанию), объём за покупку,
цена, порог показа на Главной, минимум часов до обновления трафика, потолок ГБ на
окно, тумблеры уведомлений и возврат ₽ при отзыве прибавки.

ПОДСКАЗКА О ЦЕНЕ считается живьём из витрины и в код не попадает: шаг между соседними
тарифами включает не только трафик, но и устройства, поэтому «разница тарифов» —
верхняя граница цены докупки, а не сама цена. Владелец видит число и решает сам.
Ориентир простой: докупка должна быть заметно дешевле шага, иначе выгоднее сразу
перейти на тариф побольше и докупка съест апгрейды.

ПРЕДУПРЕЖДЕНИЕ О КОРОТКИХ ОКНАХ отдаём отдельным полем: при стратегиях DAY и WEEK
прибавка живёт меньше суток или недели, и продавать её за ту же цену — почти обман.
На боевых тарифах таких стратегий нет, но чужая установка может стоять на них.

Правки настроек — не-GET, то есть PREVIEW-админу их отдаёт 403 общим механизмом
(admin/_common.py). Коммит сессии здесь ручной: overlay-ручки живут вне UoW базы.
"""

from typing import Any, Optional

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.dao import PlanDao
from src.core.enums import Currency
from src.infrastructure.services import overlay_extra_traffic as extra

from ._common import AdminUser
from ._redact import is_readonly_admin

router = APIRouter(prefix="/extra-traffic", tags=["Admin - Extra traffic"])


class ExtraTrafficConfigRequest(BaseModel):
    enabled: bool = False
    gb_per_purchase: int = 50
    price_rub: Optional[int] = None
    min_amount_rub: int = 10
    show_from_percent: int = 70
    min_hours_left: int = 2
    max_gb_per_window: int = 1000
    notify_users: bool = True
    notify_admins: bool = True
    notify_limited: bool = True
    refund_on_revoke: bool = False


# Сводка за 30 дней — только по клиентам: персонал и тестовые счета из денег
# исключены везде, и здесь граница та же.
SUMMARY_SQL = """
SELECT
  count(*) FILTER (WHERE o.status = 'applied' AND o.created_at > now() - interval '30 days')  AS applied_30d,
  coalesce(sum(o.gb) FILTER (WHERE o.status = 'applied'
           AND o.created_at > now() - interval '30 days'), 0)                                  AS gb_30d,
  coalesce(sum(o.amount) FILTER (WHERE o.status = 'applied'
           AND o.created_at > now() - interval '30 days'), 0)                                  AS amount_30d,
  count(*) FILTER (WHERE o.status = 'rejected' AND o.created_at > now() - interval '30 days') AS rejected_30d,
  count(*) FILTER (WHERE o.status = 'credited')                                                AS credited_open
FROM extra_traffic_orders o
JOIN users u ON u.id = o.user_id
WHERE u.role::text = 'USER'
"""

ACTIVE_GRANTS_SQL = (
    "SELECT count(*), coalesce(sum(gb), 0) FROM extra_traffic_grants WHERE status = 'active'"
)

# Стратегии обновления трафика у действующих подписок: короткие окна (DAY/WEEK)
# меняют смысл покупки, и владелец должен видеть, есть ли они у него вообще.
STRATEGIES_SQL = (
    "SELECT s.traffic_limit_strategy::text, count(*) FROM subscriptions s "
    "JOIN users u ON u.id = s.user_id "
    "WHERE u.role::text = 'USER' AND s.status::text = 'ACTIVE' AND s.traffic_limit > 0 "
    "GROUP BY 1 ORDER BY 2 DESC"
)


async def _price_hint(plan_dao: PlanDao) -> list[dict[str, Any]]:
    """«Между тарифами на N и N+M ГБ за 30 дней: X ₽».

    Считается по витрине в момент открытия страницы. Нужна только как ориентир:
    докупку разумно ставить заметно дешевле шага, иначе переход на соседний тариф
    всегда выгоднее — там объём остаётся навсегда, а прибавка живёт до обновления.
    """
    try:
        plans = await plan_dao.get_all(only_active=True)
    except Exception:  # noqa: BLE001 — подсказка не имеет права ронять страницу настроек
        return []
    points: list[tuple[int, int, float]] = []
    for plan in plans:
        limit = int(getattr(plan, "traffic_limit", 0) or 0)
        if limit <= 0:
            continue
        duration = next((d for d in getattr(plan, "durations", []) or [] if d.days == 30), None)
        if duration is None:
            continue
        try:
            price = float(duration.get_price(Currency.RUB))
        except Exception:  # noqa: BLE001
            continue
        points.append((limit, int(getattr(plan, "device_limit", 0) or 0), price))
    points.sort()
    hint: list[dict[str, Any]] = []
    for (lo, lo_dev, lo_price), (hi, hi_dev, hi_price) in zip(points, points[1:]):
        if hi <= lo:
            continue
        hint.append(
            {
                "from_gb": lo,
                "to_gb": hi,
                "diff_30d_rub": round(hi_price - lo_price, 2),
                "device_diff": hi_dev - lo_dev,
            }
        )
    return hint


@router.get("")
@inject
async def get_extra_traffic_config(
    admin: AdminUser,
    session: FromDishka[AsyncSession],
    plan_dao: FromDishka[PlanDao],
) -> dict[str, Any]:
    config = extra.load_config()
    summary: dict[str, Any] = {}
    strategies: list[dict[str, Any]] = []
    try:
        row = (await session.execute(text(SUMMARY_SQL))).first()
        active = (await session.execute(text(ACTIVE_GRANTS_SQL))).first()
        strategies = [
            {"strategy": r[0], "subscriptions": int(r[1] or 0)}
            for r in (await session.execute(text(STRATEGIES_SQL))).all()
        ]
        # Админ только для просмотра денег не видит — как и в соседних ручках.
        hide_money = is_readonly_admin(admin)
        summary = {
            "applied_30d": int(row[0] or 0) if row else 0,
            "gb_30d": int(row[1] or 0) if row else 0,
            "amount_30d": None if hide_money else (float(row[2] or 0) if row else 0.0),
            "rejected_30d": int(row[3] or 0) if row else 0,
            "credited_open": int(row[4] or 0) if row else 0,
            "active_grants": int(active[0] or 0) if active else 0,
            "active_gb": int(active[1] or 0) if active else 0,
        }
    except Exception:  # noqa: BLE001 — таблиц может ещё не быть (порядок выкатки)
        await session.rollback()
    return {
        "config": config,
        "effective_enabled": extra.effective_enabled(config),
        "hint": await _price_hint(plan_dao),
        "strategies": strategies,
        # Короткие окна: при DAY прибавка живёт меньше суток, при WEEK — меньше недели.
        "short_window": any(s["strategy"] in ("DAY", "WEEK") for s in strategies),
        "summary": summary,
    }


@router.put("")
@inject
async def put_extra_traffic_config(
    body: ExtraTrafficConfigRequest,
    _admin: AdminUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    config = extra.save_config(body.model_dump())
    # Ручной commit — правило overlay-ручек без исключений (память
    # admin-endpoints-commit): конфиг лежит в файле, но сессия открыта DI, и
    # оставлять её с незакрытой транзакцией нельзя.
    await session.commit()
    return {"config": config, "effective_enabled": extra.effective_enabled(config)}
