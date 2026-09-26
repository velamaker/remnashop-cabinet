"""Админ: семейные профили — тумблер, отсрочка удаления и семейные тарифы (overlay).

Страница «Семейные профили»: выключатель функции, сколько дней живёт приостановленный
профиль, и список тарифов с их условиями «N профилей × D устройств на профиль».
Тариф без условий — обычный; снять условия — сделать тариф обычным (профили его
владельцев приостановятся и через отсрочку удалятся).

После любой правки условий все живые профили получают «сверить заново»: иначе крон
заметил бы изменение только в полном часовом проходе.

Правки — не-GET, то есть PREVIEW-админу их отдаёт 403 общим механизмом
(admin/_common.py). Коммит сессии здесь ручной: overlay-ручки живут вне UoW базы.
"""

from typing import Any

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.services import overlay_family as family

from ._common import AdminUser

router = APIRouter(prefix="/family", tags=["Admin - Family"])


class FamilyConfigRequest(BaseModel):
    enabled: bool = False
    suspend_grace_days: int = 30


class FamilyTermsRequest(BaseModel):
    max_profiles: int
    devices_per_profile: int


PLANS_SQL = """
SELECT p.id, p.name, p.is_active, p.is_trial, p.device_limit, p.traffic_limit,
       t.max_profiles, t.devices_per_profile
FROM plans p
LEFT JOIN family_plan_terms t ON t.plan_id = p.id
ORDER BY p.order_index, p.id
"""

# Сводка — по живым профилям: сколько семей, сколько работает, сколько стоит.
SUMMARY_SQL = """
SELECT
  count(DISTINCT owner_user_id) FILTER (WHERE status IN ('active', 'suspended')) AS owners,
  count(*) FILTER (WHERE status = 'active')                                     AS active,
  count(*) FILTER (WHERE status = 'suspended')                                  AS suspended,
  count(*) FILTER (WHERE status IN ('creating', 'deleting'))                    AS pending,
  count(*) FILTER (WHERE fail_count >= 3 AND status <> 'failed')                AS failing
FROM family_profiles
"""

# «Сверить заново»: крон сравнивает отметку сверки с изменениями, и снятые условия
# (строки больше нет) иначе не заметил бы до часового прохода.
RESYNC_SQL = (
    "UPDATE family_profiles SET last_reconciled_at = NULL "
    "WHERE status IN ('active', 'suspended')"
)


@router.get("")
@inject
async def get_family_admin(
    _admin: AdminUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    config = family.load_config()
    plans: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    try:
        for row in (await session.execute(text(PLANS_SQL))).all():
            plans.append(
                {
                    "id": int(row[0]),
                    "name": row[1],
                    "is_active": bool(row[2]),
                    "is_trial": bool(row[3]),
                    "device_limit": int(row[4] or 0),
                    "traffic_limit": int(row[5] or 0),
                    "terms": (
                        {"max_profiles": int(row[6]), "devices_per_profile": int(row[7])}
                        if row[6] is not None
                        else None
                    ),
                }
            )
        s = (await session.execute(text(SUMMARY_SQL))).first()
        summary = {
            "owners": int(s[0] or 0) if s else 0,
            "active": int(s[1] or 0) if s else 0,
            "suspended": int(s[2] or 0) if s else 0,
            "pending": int(s[3] or 0) if s else 0,
            "failing": int(s[4] or 0) if s else 0,
        }
    except Exception:  # noqa: BLE001 — таблиц может ещё не быть (порядок выкатки)
        await session.rollback()
    return {"config": config, "plans": plans, "summary": summary}


@router.put("")
@inject
async def put_family_admin(
    body: FamilyConfigRequest,
    _admin: AdminUser,
) -> dict[str, Any]:
    return {"config": family.save_config(body.model_dump())}


@router.put("/plans/{plan_id}")
@inject
async def put_family_terms(
    plan_id: int,
    body: FamilyTermsRequest,
    _admin: AdminUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    try:
        terms = family.normalize_terms(body.max_profiles, body.devices_per_profile)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    plan = (
        await session.execute(text("SELECT is_trial FROM plans WHERE id = :id"), {"id": plan_id})
    ).first()
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тариф не найден")
    if bool(plan[0]):
        # Пробник семейным не бывает: семья заводится только на оплаченной подписке,
        # и условия на пробном тарифе обещали бы то, чего кнопка не даст.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Пробный тариф не может быть семейным",
        )
    await session.execute(
        text(
            "INSERT INTO family_plan_terms (plan_id, max_profiles, devices_per_profile, updated_at) "
            "VALUES (:p, :m, :d, now()) "
            "ON CONFLICT (plan_id) DO UPDATE SET max_profiles = :m, "
            "devices_per_profile = :d, updated_at = now()"
        ),
        {"p": plan_id, "m": terms.max_profiles, "d": terms.devices_per_profile},
    )
    await session.execute(text(RESYNC_SQL))
    await session.commit()
    return {
        "plan_id": plan_id,
        "terms": {"max_profiles": terms.max_profiles, "devices_per_profile": terms.devices_per_profile},
    }


@router.delete("/plans/{plan_id}")
@inject
async def delete_family_terms(
    plan_id: int,
    _admin: AdminUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    await session.execute(text("DELETE FROM family_plan_terms WHERE plan_id = :p"), {"p": plan_id})
    await session.execute(text(RESYNC_SQL))
    await session.commit()
    return {"plan_id": plan_id, "terms": None}
