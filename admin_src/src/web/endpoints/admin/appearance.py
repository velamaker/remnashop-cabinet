"""Админ: изменение оформления кабинета (название, акцент, фон, логотип)."""

import json
import re
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel

from src.web.endpoints.public.appearance import (
    ASSETS_DIR,
    BRANDING_PATH,
    LOGO_MEDIA_TYPES,
    load_branding,
    logo_url,
    resolve_brand_name,
)

from ._common import AdminUser

router = APIRouter(prefix="/appearance", tags=["Admin - Appearance"])

# Лимит размера логотипа (nginx admin-локация поднята до 4m под этот аплоад).
MAX_LOGO_BYTES = 2 * 1024 * 1024


def _save_branding(data: dict[str, Any]) -> None:
    try:
        BRANDING_PATH.parent.mkdir(parents=True, exist_ok=True)
        with BRANDING_PATH.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Не удалось сохранить оформление: {exc}",
        )

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _valid_color(value: Optional[str]) -> bool:
    return value is None or value == "" or bool(_HEX.match(value))


class AppearanceUpdate(BaseModel):
    brand_name: Optional[str] = None
    accent: Optional[str] = None      # hex (#rgb/#rrggbb) или "" чтобы сбросить
    background: Optional[str] = None  # legacy общий фон
    background_dark: Optional[str] = None   # фон тёмной темы (hex / "" сброс)
    background_light: Optional[str] = None  # фон светлой темы (hex / "" сброс)
    # Тумблеры кабинета
    sub_link_enabled: Optional[bool] = None        # показывать прямую ссылку/QR подписки
    maintenance_enabled: Optional[bool] = None     # тех-работы (свой тумблер)
    maintenance_follow_bot: Optional[bool] = None  # тех-работы = следовать за режимом бота
    maintenance_message: Optional[str] = None      # текст на экране тех-работ
    maintenance_block_login: Optional[bool] = None         # ограничивать вход
    maintenance_block_registration: Optional[bool] = None  # ограничивать регистрацию
    maintenance_block_payments: Optional[bool] = None      # ограничивать оплату
    enabled_languages: Optional[list[str]] = None          # доступные языки (None = все)


@router.get("")
async def get_appearance(_admin: AdminUser) -> dict[str, Any]:
    # brand_name «как есть» (None = авто); brand_name_resolved — что показывается,
    # если поле оставить пустым (для подсказки/placeholder в админке).
    data = load_branding()
    data["brand_name_resolved"] = resolve_brand_name()
    data["logo_url"] = logo_url(data.pop("logo_file", None))
    return data


@router.put("")
async def update_appearance(body: AppearanceUpdate, _admin: AdminUser) -> dict[str, Any]:
    data = load_branding()

    if body.brand_name is not None:
        name = body.brand_name.strip()
        if name == "":
            data["brand_name"] = None  # пусто → авто-подхват из конфигурации
        elif len(name) > 40:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Название должно быть не длиннее 40 символов",
            )
        else:
            data["brand_name"] = name

    for field in ("accent", "background", "background_dark", "background_light"):
        value = getattr(body, field)
        if value is not None:
            if not _valid_color(value):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{field}: ожидается hex-цвет (#RRGGBB) или пустая строка",
                )
            data[field] = value or None

    # Тумблеры кабинета (bool — как есть; сообщение — обрезаем).
    for flag in (
        "sub_link_enabled", "maintenance_enabled", "maintenance_follow_bot",
        "maintenance_block_login", "maintenance_block_registration", "maintenance_block_payments",
    ):
        value = getattr(body, flag)
        if value is not None:
            data[flag] = bool(value)
    if body.maintenance_message is not None:
        data["maintenance_message"] = body.maintenance_message.strip()[:280]

    # Доступные языки: нормализуем (2-буквенные коды, ru всегда включён, без дублей).
    if body.enabled_languages is not None:
        codes: list[str] = []
        for raw in body.enabled_languages:
            c = str(raw).strip().lower()[:5]
            if c and c.isalpha() and c not in codes:
                codes.append(c)
        if "ru" not in codes:
            codes.insert(0, "ru")  # русский — язык-исходник, отключить нельзя
        data["enabled_languages"] = codes[:32]

    _save_branding(data)
    return data


@router.post("/logo")
async def upload_logo(
    _admin: AdminUser,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Загрузить/заменить логотип кабинета. Хранится как assets/logo.<ext>."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in LOGO_MEDIA_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Поддерживаются PNG, JPG, WEBP, SVG, GIF",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Пустой файл")
    if len(content) > MAX_LOGO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Файл больше 2 МБ — уменьшите логотип",
        )

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    target = ASSETS_DIR / f"logo{ext}"
    # Удаляем прежний логотип с другим расширением, чтобы не плодить logo.*.
    for old in ASSETS_DIR.glob("logo.*"):
        if old != target:
            old.unlink(missing_ok=True)
    target.write_bytes(content)

    data = load_branding()
    data["logo_file"] = target.name
    _save_branding(data)
    return {"logo_url": logo_url(target.name)}


@router.delete("/logo")
async def delete_logo(_admin: AdminUser) -> dict[str, Any]:
    """Удалить логотип — кабинет вернётся к дефолтной иконке."""
    data = load_branding()
    current = data.get("logo_file")
    if current:
        (ASSETS_DIR / Path(current).name).unlink(missing_ok=True)
    data["logo_file"] = None
    _save_branding(data)
    return {"logo_url": None}
