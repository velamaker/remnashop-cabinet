"""Админ: месячный дайджест пользователю (трафик за месяц, любимый сервер).

Хранится в assets/digest.json (см. services/overlay_digest.py). Тумблер + день месяца +
час. Рассылку делает крон taskiq/tasks/digest.py (данные из Remnawave bandwidthstats).

`/digest/email*` — сводка письмом тем, у кого нет ни Telegram, ни push (механика и
защиты — services/overlay_digest_email.py). Отдельные ручки, а не поля `/digest`:
карточка дайджеста есть и у кабинета поверх «Бедолаги», где писем нет, — там эти
пути отвечают 501 и карточка писем просто не рисуется.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Remnawave
from src.application.common.email_sender import EmailSender
from src.application.dto import UserDto
from src.core.config import AppConfig
from src.infrastructure.services import overlay_digest_email as digest_email
from src.infrastructure.services.overlay_digest import (
    load_config,
    normalize_email_from,
    save_config,
)

from ._common import AdminUser
from ._redact import is_readonly_admin

router = APIRouter(prefix="/digest", tags=["Admin - Digest"])


class DigestUpdate(BaseModel):
    enabled: Optional[bool] = None
    day_of_month: Optional[int] = None
    hour: Optional[int] = None


class DigestEmailUpdate(BaseModel):
    email_enabled: Optional[bool] = None
    email_from: Optional[str] = Field(default=None, max_length=255)


class DigestEmailTest(BaseModel):
    to: str = Field(max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _mask(admin: UserDto, value: str) -> str:
    # Режим «только просмотр»: адреса отправителя не показываем. Пустое остаётся
    # пустым — иначе карточка решила бы, что адрес задан.
    return "***" if value and is_readonly_admin(admin) else value


@router.get("")
async def get_digest(admin: AdminUser) -> dict[str, Any]:
    config = load_config()
    config["email_from"] = _mask(admin, config["email_from"])
    return config


@router.put("")
async def update_digest(body: DigestUpdate, admin: AdminUser) -> dict[str, Any]:
    # load → три поля → save: поля сводки письмом (email_enabled, email_from)
    # переезжают из загруженного конфига как были — карточка дайджеста их не знает.
    current = load_config()
    for field in ("enabled", "day_of_month", "hour"):
        val = getattr(body, field)
        if val is not None:
            current[field] = val
    saved = save_config(current)
    return {**saved, "email_from": _mask(admin, saved["email_from"])}


async def _email_status(
    admin: UserDto, session: AsyncSession, sender: EmailSender
) -> dict[str, Any]:
    cfg = load_config()
    settings = digest_email.sender_settings(sender)
    return {
        "email_enabled": cfg["email_enabled"],
        "email_from": _mask(admin, cfg["email_from"]),
        "effective_from": _mask(admin, digest_email.effective_from(settings, cfg)),
        "needs_separate_sender": digest_email.is_brevo_like(settings),
        "digest_enabled": cfg["enabled"],
        "day_of_month": cfg["day_of_month"],
        "hour": cfg["hour"],
        "audience": await digest_email.audience_count(session),
        "opted_out": await digest_email.opted_out_count(session),
        "max_per_run": digest_email.EMAIL_MAX_PER_RUN,
        "blockers": digest_email.email_blockers(sender, cfg, settings),
        "last": await digest_email.status_summary(session),
    }


@router.get("/email")
@inject
async def get_digest_email(
    admin: AdminUser,
    session: FromDishka[AsyncSession],
    email_sender: FromDishka[EmailSender],
) -> dict[str, Any]:
    return await _email_status(admin, session, email_sender)


@router.put("/email")
@inject
async def update_digest_email(
    body: DigestEmailUpdate,
    admin: AdminUser,
    session: FromDishka[AsyncSession],
    email_sender: FromDishka[EmailSender],
) -> dict[str, Any]:
    cfg = load_config()
    if body.email_from is not None:
        raw = body.email_from.strip()
        normalized = normalize_email_from(raw)
        if raw and not normalized:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Адрес отправителя сводки указан неверно",
            )
        cfg["email_from"] = normalized
    if body.email_enabled is not None:
        if body.email_enabled:
            # Включить при препятствиях нельзя, и сохранение тогда не проходит
            # целиком: тумблер «включено» при молчащей рассылке — ложь в админке.
            blockers = digest_email.email_blockers(
                email_sender, cfg, digest_email.sender_settings(email_sender)
            )
            if blockers:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=" ".join(blockers))
        cfg["email_enabled"] = body.email_enabled
    save_config(cfg)
    return await _email_status(admin, session, email_sender)


@router.get("/email/preview")
@inject
async def preview_digest_email(
    _admin: AdminUser,
    email_sender: FromDishka[EmailSender],
    lang: str = Query(default="ru", max_length=8),
) -> dict[str, Any]:
    """Письмо с примерными цифрами. Ни база, ни панель, ни Brevo не трогаются."""
    render = getattr(email_sender, "render_branded", None)
    if not callable(render):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=digest_email.BLOCK_PATCH)
    settings = digest_email.sender_settings(email_sender)
    brand = digest_email.brand_name(settings)
    lang = digest_email.email_lang(lang)
    subject, body, opts = digest_email.build_email_parts(
        lang, digest_email.SAMPLE_TOTAL, digest_email.SAMPLE_FAVORITE[lang], brand
    )
    # Токен-заглушка: по ссылке из предпросмотра отписать никого нельзя.
    subject, text, html = render(
        subject=subject,
        body=body,
        brand=brand,
        unsubscribe_url=digest_email.unsubscribe_page_url("preview"),
        **opts,
    )
    return {"subject": subject, "text": text, "html": html}


@router.get("/email/dry-run")
@inject
async def dry_run_digest_email(
    admin: AdminUser,
    session: FromDishka[AsyncSession],
    email_sender: FromDishka[EmailSender],
    remnawave: FromDishka[Remnawave],
) -> dict[str, Any]:
    """«Кому уйдёт»: первые адресаты с исходом. Ничего не отправляет и не пишет."""
    now = datetime.now(timezone.utc)
    result = await digest_email.dry_run(
        session,
        sender=email_sender,
        cfg=load_config(),
        settings=digest_email.sender_settings(email_sender),
        sdk=getattr(remnawave, "sdk", None),
        month=now.strftime("%Y-%m"),
        start=now - timedelta(days=30),
        end=now,
    )
    if is_readonly_admin(admin):
        for item in result["items"]:
            item["user_id"] = None
    return result


@router.post("/email/test")
@inject
async def send_digest_email_test(
    body: DigestEmailTest,
    admin: AdminUser,
    email_sender: FromDishka[EmailSender],
    config: FromDishka[AppConfig],
) -> dict[str, Any]:
    """Одно письмо «[Тест] …» только на указанный адрес, с отправителя сводки."""
    cfg = load_config()
    settings = digest_email.sender_settings(email_sender)
    blockers = digest_email.email_blockers(email_sender, cfg, settings)
    if blockers:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=" ".join(blockers))
    to = body.to.strip()
    try:
        sent_from = await digest_email.send_test(
            sender=email_sender,
            cfg=cfg,
            settings=settings,
            secret=config.crypt_key.get_secret_value(),
            user_id=admin.id,
            lang=getattr(admin, "language", None),
            to=to,
        )
    except Exception as exc:  # noqa: BLE001
        # Текст причины нужен владельцу дословно: «sender not valid» от Brevo значит
        # «адрес сводки не добавлен в Senders», и иначе это не понять.
        cause = exc.__cause__ or exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Не удалось отправить тестовое письмо: {cause}",
        )
    return {"success": True, "to": to, "from": sent_from}
