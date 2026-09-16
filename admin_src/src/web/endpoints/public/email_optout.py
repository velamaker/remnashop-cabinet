"""Отписка от месячной сводки по ссылке из письма — без входа в кабинет.

  GET  /email-optout/digest?t=              → {subscribed}; только чтение
  POST /email-optout/digest?t=              → отписать; тело игнорируется
  POST /email-optout/digest/resubscribe?t=  → подписать обратно

ПОЧЕМУ GET НИЧЕГО НЕ МЕНЯЕТ. Почтовые сканеры (корпоративные фильтры, антивирусы,
превью ссылок) открывают ссылки из писем сами, без человека. Отписывай GET —
сводка тихо выключалась бы у всех, чья почта проходит через такой фильтр. Поэтому
ссылка в письме ведёт на страницу кабинета, а отписка — кнопкой (POST).

ПОЧЕМУ POST БЕЗ ТЕЛА. Это же адрес для заголовка List-Unsubscribe-Post
(RFC 8058): почтовик присылает `List-Unsubscribe=One-Click` формой, и разбирать
её незачем — важен сам факт запроса с верным токеном. CSRF не мешает: запрос
почтовика приходит без нашей куки (src/web/csrf.py пропускает такие).

Токен даёт одно право — включить или выключить сводку одному id (HMAC, см.
overlay_digest_email.make_optout_token). В ответах нет ни почты, ни id: ссылку
пересылают, и по ней не должно быть видно, чья она.
"""

from typing import Any

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import AppConfig
from src.infrastructure.services import overlay_digest_email as digest_email

router = APIRouter(prefix="/email-optout", tags=["Public - Email opt-out"])

INVALID_LINK = "Ссылка недействительна"


async def _user_from_token(session: AsyncSession, secret: str, token: str) -> int:
    # Подпись проверяем ДО базы: кривая ссылка не должна стоить запроса.
    user_id = digest_email.parse_optout_token(token, secret)
    if user_id is None or not await digest_email.user_exists(session, user_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=INVALID_LINK)
    return user_id


async def optout_status(session: AsyncSession, secret: str, token: str) -> dict[str, Any]:
    user_id = await _user_from_token(session, secret, token)
    return {"subscribed": not await digest_email.is_opted_out(session, user_id)}


async def optout_set(
    session: AsyncSession, secret: str, token: str, *, opted_out: bool
) -> dict[str, Any]:
    user_id = await _user_from_token(session, secret, token)
    await digest_email.set_opt_out(session, user_id, opted_out)
    # Overlay-ручка: сессию DI сам не коммитит.
    await session.commit()
    return {"subscribed": not opted_out}


def _secret(config: AppConfig) -> str:
    return config.crypt_key.get_secret_value()


@router.get("/digest")
@inject
async def get_digest_optout(
    session: FromDishka[AsyncSession],
    config: FromDishka[AppConfig],
    t: str = Query(default=""),
) -> dict[str, Any]:
    return await optout_status(session, _secret(config), t)


@router.post("/digest")
@inject
async def unsubscribe_digest(
    session: FromDishka[AsyncSession],
    config: FromDishka[AppConfig],
    t: str = Query(default=""),
) -> dict[str, Any]:
    return await optout_set(session, _secret(config), t, opted_out=True)


@router.post("/digest/resubscribe")
@inject
async def resubscribe_digest(
    session: FromDishka[AsyncSession],
    config: FromDishka[AppConfig],
    t: str = Query(default=""),
) -> dict[str, Any]:
    return await optout_set(session, _secret(config), t, opted_out=False)
