import re
from typing import Any, Optional

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, status
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import BotService
from src.application.common.dao import AdLinkDao
from src.application.dto import AdLinkDto

from ._common import AdminUser

router = APIRouter(prefix="/ad-links", tags=["Admin - Ad Links"])

# Переход по рекламе бот засчитывает только из deep-link `t.me/бот?start=ad_<код>`
# (база: telegram/middlewares/_codes.py). Telegram пропускает в параметре start лишь
# латиницу, цифры, `_` и `-`, не длиннее 64 символов — вместе с префиксом `ad_`.
# Раньше код не проверялся вовсе: «сторис июнь» сохранялся, ссылка выглядела
# рабочей, а переходы по ней молча не считались.
_CODE_RE = re.compile(r"[A-Za-z0-9_-]{1,61}")
CODE_RULE = "Код ссылки: только латиница, цифры, «_» и «-», до 61 символа"


def validate_code(code: str) -> Optional[str]:
    """Текст ошибки для недопустимого кода; None — код годится."""
    if not code:
        return "Код ссылки не заполнен"
    if not _CODE_RE.fullmatch(code):
        return CODE_RULE
    return None


async def bot_link_base(bot_service: BotService) -> Optional[str]:
    """Основа рекламной ссылки бота (`https://t.me/<бот>?start=ad`); None — адрес не узнать.

    Готовая ссылка — удобство, а не условие работы раздела: если Telegram сейчас не
    отвечает на getMe, отдаём ссылки без адреса, и кабинет покажет голый код.
    """
    try:
        return await bot_service.get_ad_link_url("")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"ad_links: не удалось получить адрес бота для ссылок: {exc}")
        return None


def _link_to_dict(link: Any, base: Optional[str] = None) -> dict[str, Any]:
    data = {
        "id": link.id,
        "name": link.name,
        "code": link.code,
        "is_active": link.is_active,
        "created_at": link.created_at.isoformat() if link.created_at else None,
    }
    # `get_ad_link_url("")` даёт `…?start=ad` — ровно основу, к которой база сама
    # дописывает `_<код>` (Deeplink.build_url). Повторяем её правило, а не придумываем своё.
    if base and link.code:
        data["url"] = f"{base}_{link.code}"
    return data


@router.get("")
@inject
async def list_ad_links(
    _admin: AdminUser,
    ad_link_dao: FromDishka[AdLinkDao],
    bot_service: FromDishka[BotService],
) -> dict[str, Any]:
    links = await ad_link_dao.get_all()
    base = await bot_link_base(bot_service) if links else None
    return {"items": [_link_to_dict(l, base) for l in links], "total": len(links)}


@router.get("/{link_id}/stats")
@inject
async def get_ad_link_stats(
    link_id: int,
    _admin: AdminUser,
    ad_link_dao: FromDishka[AdLinkDao],
    bot_service: FromDishka[BotService],
) -> dict[str, Any]:
    link = await ad_link_dao.get_by_id(link_id)
    if not link:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Рекламная ссылка не найдена")
    stats = await ad_link_dao.get_stats(link_id)
    return {
        **_link_to_dict(link, await bot_link_base(bot_service)),
        "stats": {
            "registrations": stats.registrations,
            "trials": stats.trials,
            "buyers": stats.buyers,
            "trial_buyers": stats.trial_buyers,
            "revenue": stats.revenue,
            "reg_to_buy_rate": stats.reg_to_buy_rate,
            "trial_to_buy_rate": stats.trial_to_buy_rate,
        },
    }


class CreateAdLinkRequest(BaseModel):
    name: str
    code: str


class UpdateAdLinkRequest(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None


@router.post("", status_code=status.HTTP_201_CREATED)
@inject
async def create_ad_link(
    body: CreateAdLinkRequest,
    _admin: AdminUser,
    ad_link_dao: FromDishka[AdLinkDao],
    bot_service: FromDishka[BotService],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    name = (body.name or "").strip()
    code = (body.code or "").strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Название не заполнено")
    problem = validate_code(code)
    if problem:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=problem)
    existing = await ad_link_dao.get_by_code(code)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Такой код уже существует")
    link = AdLinkDto(id=0, name=name, code=code, is_active=True)
    created = await ad_link_dao.create(link)
    await session.commit()
    # Готовая ссылка — сразу в ответе: окно создания показывает её, чтобы владелец
    # скопировал именно то, что вставлять в рекламу, а не код.
    return _link_to_dict(created, await bot_link_base(bot_service))


@router.put("/{link_id}")
@inject
async def update_ad_link(
    link_id: int,
    body: UpdateAdLinkRequest,
    _admin: AdminUser,
    ad_link_dao: FromDishka[AdLinkDao],
    bot_service: FromDishka[BotService],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    link = await ad_link_dao.get_by_id(link_id)
    if not link:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Рекламная ссылка не найдена")
    if body.name is not None:
        if not body.name.strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Название не заполнено")
        link.name = body.name.strip()
    if body.is_active is not None:
        link.is_active = body.is_active
    updated = await ad_link_dao.update(link)
    if not updated:
        raise HTTPException(status_code=500, detail="Не удалось обновить")
    await session.commit()
    return _link_to_dict(updated, await bot_link_base(bot_service))


@router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def delete_ad_link(
    link_id: int,
    _admin: AdminUser,
    ad_link_dao: FromDishka[AdLinkDao],
    session: FromDishka[AsyncSession],
) -> None:
    link = await ad_link_dao.get_by_id(link_id)
    if not link:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Рекламная ссылка не найдена")
    await ad_link_dao.delete(link_id)
    await session.commit()
