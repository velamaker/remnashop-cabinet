"""Web Push — публичные ручки подписки на браузерные push-уведомления PWA.

Пользователь в кабинете включает push → фронт подписывается через service worker
(pushManager.subscribe с VAPID public key) и шлёт подписку сюда. Хранится в
overlay-таблице push_subscriptions (DDL в overlay_app.py).
"""

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.services.overlay_push import (
    send_to_user,
    vapid_public_key,
)
from src.web.endpoints.public._common import CurrentUser

router = APIRouter(prefix="/push", tags=["Public - Push"])


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscription(BaseModel):
    endpoint: str = Field(min_length=8, max_length=1000)
    keys: PushKeys


class UnsubscribeRequest(BaseModel):
    endpoint: str = Field(min_length=8, max_length=1000)


@router.get("/vapid-key")
async def get_vapid_key() -> dict:
    """Публичный VAPID-ключ (applicationServerKey) для подписки в браузере."""
    return {"public_key": vapid_public_key()}


@router.get("/status")
@inject
async def push_status(
    user: CurrentUser,
    session: FromDishka[AsyncSession],
) -> dict:
    count = (
        await session.execute(
            text("SELECT count(*) FROM push_subscriptions WHERE user_id = :uid"),
            {"uid": user.id},
        )
    ).scalar_one()
    return {"enabled": count > 0, "devices": int(count)}


@router.post("/subscribe")
@inject
async def subscribe(
    body: PushSubscription,
    user: CurrentUser,
    session: FromDishka[AsyncSession],
) -> dict:
    """Сохранить (или обновить) push-подписку устройства пользователя."""
    await session.execute(
        text(
            # endpoint — глобально UNIQUE. При конфликте обновляем ключи ТОЛЬКО если
            # строка уже принадлежит тому же юзеру (WHERE user_id = EXCLUDED.user_id).
            # Иначе (endpoint принадлежит другому юзеру) DO UPDATE отфильтровывается —
            # 0 строк, без ошибки — чтобы нельзя было перепривязать чужую подписку на
            # себя, зная её endpoint (push-угон/DoS). НЕ ставим user_id = EXCLUDED.*.
            "INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth) "
            "VALUES (:uid, :ep, :p256dh, :auth) "
            "ON CONFLICT (endpoint) DO UPDATE SET "
            "p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth, created_at = now() "
            "WHERE push_subscriptions.user_id = EXCLUDED.user_id"
        ),
        {
            "uid": user.id,
            "ep": body.endpoint,
            "p256dh": body.keys.p256dh,
            "auth": body.keys.auth,
        },
    )
    # Кап: держим не больше 20 новейших подписок на юзера (защита от накопления строк —
    # каждый браузер/девайс = запись; без капа юзер мог бы забить таблицу).
    await session.execute(
        text(
            "DELETE FROM push_subscriptions WHERE user_id = :uid AND id NOT IN ("
            "  SELECT id FROM push_subscriptions WHERE user_id = :uid "
            "  ORDER BY created_at DESC LIMIT 20)"
        ),
        {"uid": user.id},
    )
    await session.commit()
    return {"ok": True}


@router.post("/unsubscribe")
@inject
async def unsubscribe(
    body: UnsubscribeRequest,
    user: CurrentUser,
    session: FromDishka[AsyncSession],
) -> dict:
    """Удалить подписку этого устройства (пользователь выключил push)."""
    await session.execute(
        text(
            "DELETE FROM push_subscriptions "
            "WHERE user_id = :uid AND endpoint = :ep"
        ),
        {"uid": user.id, "ep": body.endpoint},
    )
    await session.commit()
    return {"ok": True}


@router.post("/test")
@inject
async def send_test(
    user: CurrentUser,
    session: FromDishka[AsyncSession],
) -> dict:
    """Отправить тестовый push на все устройства пользователя (проверка работы)."""
    count = (
        await session.execute(
            text("SELECT count(*) FROM push_subscriptions WHERE user_id = :uid"),
            {"uid": user.id},
        )
    ).scalar_one()
    if int(count) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Нет активных подписок на этом аккаунте.",
        )

    payload = {
        "title": "🔔 Тест уведомления",
        "body": "Push-уведомления работают.",
        "url": "/",
        "tag": "test",
    }
    try:
        ok = await send_to_user(session, user.id, payload)
        await session.commit()
    except Exception as exc:
        logger.exception(f"push: тестовая отправка не удалась: {exc}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Не удалось отправить уведомление.",
        )
    if ok == 0:
        # Подписки есть, но доставка не удалась (напр. отозвана на устройстве).
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Уведомление не доставлено (подписка могла быть отозвана).",
        )
    return {"ok": True, "delivered": ok}
