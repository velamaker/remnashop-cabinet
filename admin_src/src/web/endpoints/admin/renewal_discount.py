"""Админ: скидка на продление ДО окончания подписки.

Конфиг — assets/renewal_discount.json (services/overlay_renewal_discount.py),
выдачу делает крон taskiq/tasks/renewal_discount.py. По умолчанию выключено.

Кроме настроек здесь всё, что нужно, чтобы включить фичу, не раздав живым людям
скидок вслепую:
  • предпросмотр — кому выдалась бы скидка на горизонте до 60 дней и почему
    остальным нет; ничего не пишет;
  • «пример себе» — то же сообщение, что получит клиент, но админу и без выдачи;
  • итоги — выдано / воспользовались / деньги, без оплат персонала и тестовых;
  • отзыв всех открытых скидок — только с явным `confirm: true`.
Изменяющие вызовы попадают в журнал действий общим аудитом (overlay_app).
Read-only админ видит те же цифры, но без внутренних id людей.
"""

from datetime import datetime, timezone
from typing import Any, Optional

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Notifier
from src.infrastructure.services.overlay_push import notify_user_push
from src.infrastructure.services.overlay_renewal_discount import (
    HORIZON_MAX,
    Env,
    config_note,
    load_config,
    min_days_before,
    preview,
    revoke_active,
    save_config,
    send_example,
    stats,
)

from ._common import AdminUser
from ._redact import is_readonly_admin

router = APIRouter(prefix="/renewal-discount", tags=["Admin - Renewal Discount"])

_FIELDS = ("enabled", "percent", "days_before", "lifetime_hours", "cooldown_days", "skip_early_renewers")


class RenewalDiscountUpdate(BaseModel):
    enabled: Optional[bool] = None
    percent: Optional[int] = None
    days_before: Optional[int] = None
    lifetime_hours: Optional[int] = None
    cooldown_days: Optional[int] = None
    skip_early_renewers: Optional[bool] = None


class RevokeBody(BaseModel):
    confirm: bool = False


def _with_meta(cfg: dict[str, Any]) -> dict[str, Any]:
    return {**cfg, "min_days_before": min_days_before(), "note": config_note(cfg)}


async def _table_guard(session: AsyncSession, exc: Exception) -> None:
    """Таблицы выдач ещё нет (бот не перезапускался после обновления) — честный 503."""
    try:
        await session.rollback()
    except Exception:  # noqa: BLE001
        pass
    msg = f"{getattr(getattr(exc, 'orig', None), 'sqlstate', '')} {exc}".lower()
    if "42p01" in msg or "undefinedtable" in msg or "does not exist" in msg:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Таблица выдач ещё не создана — перезапустите бота, чтобы прошла миграция",
        )
    raise exc


@router.get("")
async def get_renewal_discount(_admin: AdminUser) -> dict[str, Any]:
    return _with_meta(load_config())


@router.put("")
async def update_renewal_discount(body: RenewalDiscountUpdate, _admin: AdminUser) -> dict[str, Any]:
    current = load_config()
    for field in _FIELDS:
        val = getattr(body, field)
        if val is not None:
            current[field] = val
    return _with_meta(save_config(current))


@router.get("/preview")
@inject
async def preview_renewal_discount(
    admin: AdminUser,
    session: FromDishka[AsyncSession],
    horizon_days: int = Query(30, ge=0, le=HORIZON_MAX),
) -> dict[str, Any]:
    try:
        return await preview(
            session,
            load_config(),
            datetime.now(timezone.utc),
            horizon_days,
            Env.current(),
            hide_ids=is_readonly_admin(admin),
        )
    except Exception as exc:  # noqa: BLE001
        await _table_guard(session, exc)
        raise


@router.get("/stats")
@inject
async def renewal_discount_stats(
    admin: AdminUser,
    session: FromDishka[AsyncSession],
    days: int = Query(90, ge=7, le=365),
) -> dict[str, Any]:
    try:
        return await stats(session, days, hide_ids=is_readonly_admin(admin))
    except Exception as exc:  # noqa: BLE001
        await _table_guard(session, exc)
        raise


@router.post("/test-send")
@inject
async def send_renewal_discount_example(
    admin: AdminUser,
    notifier: FromDishka[Notifier],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    async def notify(user: Any, payload: Any) -> Any:
        return await notifier.notify_user(user, payload=payload)

    async def push(user: Any, messages: dict) -> int:
        return await notify_user_push(session, user, messages, url="/billing", tag="renewal-discount")

    return await send_example(admin, load_config(), notify_user=notify, send_push=push)


@router.post("/revoke-active")
@inject
async def revoke_active_renewal_discounts(
    body: RevokeBody,
    _admin: AdminUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    # Отзыв необратим (скидки снимаются с людей), поэтому подтверждение — в теле,
    # а не только в интерфейсе: случайный POST из консоли ничего не сделает.
    if body.confirm is not True:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Отзыв нужно подтвердить: confirm: true",
        )
    try:
        revoked = await revoke_active(session)
        # Overlay-эндпоинт: сессию коммитим сами, иначе отзыв откатится.
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        await _table_guard(session, exc)
        raise
    return {"revoked": revoked}
