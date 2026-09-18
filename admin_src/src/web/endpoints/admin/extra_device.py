"""Админ: настройки докупки «+1 устройство» и сводка по ней (overlay).

Страница «Докупка устройств»: тумблер продаж, цена за устройство на 30 дней, минимум
счёта, порог «не продавать перед концом срока», максимум мест на подписку, отключение
устройств по окончании места (по умолчанию ВЫКЛ) и тумблеры уведомлений.

ПОДСКАЗКА О ЦЕНЕ считается живьём из витрины и в код не попадает: шаг между соседними
тарифами включает ещё и трафик, поэтому «разница тарифов» — верхняя граница цены
устройства, а не сама цена. Владелец видит число и решает сам.

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
from src.infrastructure.services import overlay_extra_device as extra

from ._common import AdminUser
from ._redact import is_readonly_admin

router = APIRouter(prefix="/extra-device", tags=["Admin - Extra device"])


class ExtraDeviceConfigRequest(BaseModel):
    enabled: bool = False
    price_rub_30d: Optional[int] = None
    min_amount_rub: int = 10
    min_days_left: int = 3
    max_extra: int = 2
    remove_excess_devices: bool = False
    notify_users: bool = True
    notify_admins: bool = True


# Сводка за 30 дней — только по клиентам: персонал и тестовые счета из денег
# исключены везде, и здесь граница та же.
SUMMARY_SQL = """
SELECT
  count(*) FILTER (WHERE o.status = 'applied' AND o.created_at > now() - interval '30 days')  AS applied_30d,
  coalesce(sum(o.amount) FILTER (WHERE o.status = 'applied'
           AND o.created_at > now() - interval '30 days'), 0)                                  AS amount_30d,
  count(*) FILTER (WHERE o.status = 'rejected' AND o.created_at > now() - interval '30 days') AS rejected_30d,
  count(*) FILTER (WHERE o.status = 'credited')                                                AS credited_open
FROM extra_device_orders o
JOIN users u ON u.id = o.user_id
WHERE u.role::text = 'USER'
"""

ACTIVE_SLOTS_SQL = "SELECT count(*) FROM extra_device_slots WHERE status = 'active'"


async def _price_hint(plan_dao: PlanDao) -> list[dict[str, Any]]:
    """«Между тарифами на N и N+1 устройство за 30 дней: X ₽ и +Y ГБ».

    Считается по витрине в момент открытия страницы. Нужна только как ориентир:
    устройство без трафика разумно ставить дешевле шага, иначе переход на соседний
    тариф всегда выгоднее докупки.
    """
    try:
        plans = await plan_dao.get_all(only_active=True)
    except Exception:  # noqa: BLE001 — подсказка не имеет права ронять страницу настроек
        return []
    points: list[tuple[int, int, float]] = []
    for plan in plans:
        limit = int(getattr(plan, "device_limit", 0) or 0)
        if limit <= 0:
            continue
        duration = next((d for d in getattr(plan, "durations", []) or [] if d.days == 30), None)
        if duration is None:
            continue
        try:
            price = float(duration.get_price(Currency.RUB))
        except Exception:  # noqa: BLE001
            continue
        points.append((limit, int(getattr(plan, "traffic_limit", 0) or 0), price))
    points.sort()
    hint: list[dict[str, Any]] = []
    for (lo, lo_gb, lo_price), (hi, hi_gb, hi_price) in zip(points, points[1:]):
        if hi <= lo:
            continue
        hint.append(
            {
                "from_devices": lo,
                "to_devices": hi,
                "diff_30d_rub": round(hi_price - lo_price, 2),
                "traffic_diff_gb": hi_gb - lo_gb,
            }
        )
    return hint


@router.get("")
@inject
async def get_extra_device_config(
    admin: AdminUser,
    session: FromDishka[AsyncSession],
    plan_dao: FromDishka[PlanDao],
) -> dict[str, Any]:
    config = extra.load_config()
    summary: dict[str, Any] = {}
    try:
        row = (await session.execute(text(SUMMARY_SQL))).first()
        active = (await session.execute(text(ACTIVE_SLOTS_SQL))).scalar()
        # Админ только для просмотра денег не видит — как и в соседних ручках.
        hide_money = is_readonly_admin(admin)
        summary = {
            "applied_30d": int(row[0] or 0) if row else 0,
            "amount_30d": None if hide_money else (float(row[1] or 0) if row else 0.0),
            "rejected_30d": int(row[2] or 0) if row else 0,
            "credited_open": int(row[3] or 0) if row else 0,
            "active_slots": int(active or 0),
        }
    except Exception:  # noqa: BLE001 — таблиц может ещё не быть (порядок выкатки)
        await session.rollback()
    return {
        "config": config,
        "effective_enabled": extra.effective_enabled(config),
        "hint": await _price_hint(plan_dao),
        "summary": summary,
    }


@router.put("")
@inject
async def put_extra_device_config(
    body: ExtraDeviceConfigRequest,
    _admin: AdminUser,
) -> dict[str, Any]:
    config = extra.save_config(body.model_dump())
    return {"config": config, "effective_enabled": extra.effective_enabled(config)}
