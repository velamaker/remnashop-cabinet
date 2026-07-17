"""Public: экспорт своих данных + самоудаление аккаунта (GDPR-стиль) — overlay.

- GET  /account/export — отдаёт все данные юзера одним JSON (профиль, подписка,
  платежи, история входов, рефералка, тикеты). Кабинет качает файлом.
- POST /account/delete — удаление аккаунта с подтверждением. Каскады аккуратно:
  подписка в Remnawave УДАЛЯЕТСЯ (VPN перестаёт работать), локальный юзер
  АНОНИМИЗИРУЕТСЯ (PII затирается, is_blocked=true) — транзакции остаются для
  финучёта (обезличены через анонимный юзер). Личные данные (входы, push) чистим.
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
from src.infrastructure.services.overlay_sessions import invalidate_all
from src.web.endpoints.public._common import CurrentUser

router = APIRouter(prefix="/account", tags=["Public - Account (GDPR)"])

DELETE_CONFIRM_PHRASE = "УДАЛИТЬ"


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
    if (body.confirm or "").strip().upper() != DELETE_CONFIRM_PHRASE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Для подтверждения введите «{DELETE_CONFIRM_PHRASE}»",
        )

    # 1) Удаляем пользователя в Remnawave — VPN перестаёт работать. Если панель
    #    недоступна — прерываемся (не оставляем «полуудалённое» состояние с живым VPN).
    sub = (
        await session.execute(
            text(
                "SELECT s.user_remna_id FROM users u "
                "JOIN subscriptions s ON u.current_subscription_id = s.id WHERE u.id = :u"
            ),
            {"u": user.id},
        )
    ).first()
    if sub and sub[0]:
        sdk = getattr(remnawave, "sdk", None)
        if sdk is None:
            raise HTTPException(status_code=500, detail="Панель недоступна, попробуйте позже")
        try:
            await sdk.users.delete_user(str(sub[0]))
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            # «уже нет в панели» трактуем как успех, прочее — ошибка панели
            if "not found" not in msg and "404" not in msg:
                raise HTTPException(status_code=502, detail="Не удалось отозвать подписку, попробуйте позже") from e

    # 2) Анонимизируем локального юзера (PII затираем; транзакции остаются обезличенными).
    await session.execute(
        text(
            "UPDATE users SET "
            "email = NULL, pending_email = NULL, password_hash = NULL, "
            "email_verification_code_hash = NULL, email_verification_expires_at = NULL, "
            "is_email_verified = false, username = NULL, name = 'Удалённый аккаунт', "
            "telegram_id = NULL, referral_code = 'deleted_' || id::text, auth_type = 'deleted', "
            "is_blocked = true, cabinet_balance = 0, points = 0, autopay_enabled = false, "
            "current_subscription_id = NULL, updated_at = now() "
            "WHERE id = :u"
        ),
        {"u": user.id},
    )

    # 3) Чистим личные данные (история входов, push-подписки).
    for tbl in ("login_events", "push_subscriptions"):
        try:
            await session.execute(text(f"DELETE FROM {tbl} WHERE user_id = :u"), {"u": user.id})
        except Exception:  # noqa: BLE001 — таблицы может не быть
            pass

    # 4) Инвалидируем все сессии и чистим куки.
    await invalidate_all(session, user.id)
    await session.commit()
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    return {"deleted": True}
