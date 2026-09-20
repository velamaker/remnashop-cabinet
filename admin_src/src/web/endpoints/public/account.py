"""Public: экспорт своих данных + самоудаление аккаунта (GDPR-стиль) — overlay.

- GET  /account/export — отдаёт все данные юзера одним JSON (профиль, подписка,
  платежи, история входов, рефералка, тикеты). Кабинет качает файлом.
- POST /account/delete — удаление аккаунта с подтверждением. Сами шаги живут в
  `overlay_user_purge`: там же их берёт админка, чтобы «удалить» значило одно и
  то же с обеих сторон. Коротко: аккаунт в панели удаляется (VPN перестаёт
  работать), личные данные вычищаются, а сама запись либо удаляется целиком,
  либо — если за человеком есть платежи — остаётся обезличенной ради отчётности.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Remnawave
from src.infrastructure.services.overlay_user_purge import (
    CONFIRM_PHRASE,
    PanelUnavailable,
    confirm_matches,
    purge_user,
)
from src.web.endpoints.public._common import CurrentUser
from src.web.endpoints.public.sub_alias import maybe_alias_url

router = APIRouter(prefix="/account", tags=["Public - Account (GDPR)"])

DELETE_CONFIRM_PHRASE = CONFIRM_PHRASE


def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v


async def _rows(session: AsyncSession, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        res = (await session.execute(text(sql), params)).mappings().all()
        return [{k: _iso(v) for k, v in r.items()} for r in res]
    except Exception:  # noqa: BLE001 — отсутствие таблицы не должно ронять экспорт
        return []


@router.get("/export")
@inject
async def export_account(
    user: CurrentUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    profile = await _rows(
        session,
        "SELECT id, name, username, email, telegram_id, language::text AS language, "
        "auth_type, referral_code, points, cabinet_balance, autopay_enabled, "
        "is_email_verified, personal_discount, purchase_discount, created_at "
        "FROM users WHERE id = :u",
        {"u": user.id},
    )
    subscription = await _rows(
        session,
        "SELECT s.status, s.expire_at, s.is_trial, s.traffic_limit, s.device_limit, "
        "s.url, s.plan_snapshot->>'name' AS plan_name "
        "FROM subscriptions s WHERE s.user_id = :u ORDER BY s.id DESC",
        {"u": user.id},
    )
    # Крипто-ссылки: экспорт не должен светить реальный sub-URL (файл экспорта могут
    # сохранить/расшарить) — та же маскировка, что и в /subscription/current.
    for _sub in subscription:
        if _sub.get("url"):
            _sub["url"] = maybe_alias_url(_sub["url"])
    transactions = await _rows(
        session,
        "SELECT id, status, purchase_type, gateway_type, gateway_display_name, "
        "currency, pricing->>'final_amount' AS amount, is_test, created_at "
        "FROM transactions WHERE user_id = :u ORDER BY created_at DESC",
        {"u": user.id},
    )
    logins = await _rows(
        session,
        "SELECT ip, user_agent, method, created_at FROM login_events "
        "WHERE user_id = :u ORDER BY created_at DESC LIMIT 200",
        {"u": user.id},
    )
    tickets = await _rows(
        session,
        "SELECT id, subject, status, created_at FROM support_tickets "
        "WHERE user_id = :u ORDER BY created_at DESC",
        {"u": user.id},
    )

    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "profile": profile[0] if profile else None,
        "subscriptions": subscription,
        "transactions": transactions,
        "login_history": logins,
        "support_tickets": tickets,
    }


class DeleteAccountRequest(BaseModel):
    confirm: str


@router.post("/delete")
@inject
async def delete_account(
    body: DeleteAccountRequest,
    user: CurrentUser,
    session: FromDishka[AsyncSession],
    remnawave: FromDishka[Remnawave],
    response: Response,
) -> dict[str, Any]:
    if not confirm_matches(body.confirm):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Для подтверждения введите «{DELETE_CONFIRM_PHRASE}»",
        )

    try:
        report = await purge_user(session, remnawave, user.id)
    except PanelUnavailable:
        raise HTTPException(
            status_code=502, detail="Не удалось отозвать подписку, попробуйте позже"
        )

    await session.commit()
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    return {"deleted": True, "mode": report["mode"]}
