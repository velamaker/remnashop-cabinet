"""Админ: 2FA (TOTP) — включение, выключение, разблокировка.

Каждый админ включает себе сам (opt-in). setup → секрет+QR; enable → подтвердить кодом;
unlock → ввести код для доступа (ставит куку admin_2fa на ~12ч); disable → выключить.
Гейт (_common.py) требует разблокировку только у тех, у кого 2FA включена.
Пути /admin/2fa/* гейт пропускает мимо 2FA-проверки.
"""

from typing import Any

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import AppConfig
from src.infrastructure.services.overlay_admin_2fa import (
    gen_secret,
    make_unlock,
    otpauth_uri,
    verify_totp,
)

from ._common import AdminUser

router = APIRouter(prefix="/2fa", tags=["Admin - 2FA"])


class CodeBody(BaseModel):
    code: str


async def _row(session: AsyncSession, uid: int):
    return (
        await session.execute(
            text("SELECT secret, enabled FROM admin_2fa WHERE user_id = :u"), {"u": uid}
        )
    ).first()


def _set_cookie(response: Response, admin_id: int, config: AppConfig) -> None:
    secret = config.jwt_secret.get_secret_value() if config.jwt_secret else ""
    response.set_cookie(
        "admin_2fa", make_unlock(admin_id, secret),
        max_age=12 * 3600, httponly=True, samesite="lax", secure=True, path="/",
    )


@router.get("/status")
@inject
async def status_2fa(admin: AdminUser, session: FromDishka[AsyncSession]) -> dict[str, Any]:
    row = await _row(session, admin.id)
    return {"enabled": bool(row and row[1])}


@router.post("/setup")
@inject
async def setup_2fa(admin: AdminUser, session: FromDishka[AsyncSession]) -> dict[str, Any]:
    secret = gen_secret()
    await session.execute(
        text(
            "INSERT INTO admin_2fa (user_id, secret, enabled) VALUES (:u, :s, false) "
            "ON CONFLICT (user_id) DO UPDATE SET secret = :s, enabled = false"
        ),
        {"u": admin.id, "s": secret},
    )
    await session.commit()
    account = getattr(admin, "email", None) or getattr(admin, "username", None) or f"admin{admin.id}"
    return {"secret": secret, "otpauth": otpauth_uri(secret, str(account), "RemnaShop Admin")}


@router.post("/enable")
@inject
async def enable_2fa(
    body: CodeBody, admin: AdminUser, session: FromDishka[AsyncSession],
    config: FromDishka[AppConfig], response: Response,
) -> dict[str, Any]:
    row = await _row(session, admin.id)
    if not row:
        raise HTTPException(status_code=400, detail="Сначала сгенерируйте секрет")
    if not verify_totp(row[0], body.code):
        raise HTTPException(status_code=400, detail="Неверный код")
    await session.execute(
        text("UPDATE admin_2fa SET enabled = true WHERE user_id = :u"), {"u": admin.id}
    )
    await session.commit()
    _set_cookie(response, admin.id, config)  # сразу разблокируем текущую сессию
    return {"enabled": True}


@router.post("/unlock")
@inject
async def unlock_2fa(
    body: CodeBody, admin: AdminUser, session: FromDishka[AsyncSession],
    config: FromDishka[AppConfig], response: Response,
) -> dict[str, Any]:
    row = await _row(session, admin.id)
    if not row or not row[1]:
        raise HTTPException(status_code=400, detail="2FA не включена")
    if not verify_totp(row[0], body.code):
        raise HTTPException(status_code=400, detail="Неверный код")
    _set_cookie(response, admin.id, config)
    return {"unlocked": True}


@router.post("/disable")
@inject
async def disable_2fa(
    body: CodeBody, admin: AdminUser, session: FromDishka[AsyncSession], response: Response,
) -> dict[str, Any]:
    row = await _row(session, admin.id)
    if not row:
        return {"enabled": False}
    if row[1] and not verify_totp(row[0], body.code):
        raise HTTPException(status_code=400, detail="Неверный код")
    await session.execute(text("DELETE FROM admin_2fa WHERE user_id = :u"), {"u": admin.id})
    await session.commit()
    response.delete_cookie("admin_2fa", path="/")
    return {"enabled": False}
