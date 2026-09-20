"""Админ: редактирование контента страницы «Информация» (FAQ/Правила/
Конфиденциальность/Оферта/Статусы). Хранится в assets/info_content.json.

GET отдаёт эффективный контент (сохранённое или брендированные дефолты) — им и
наполняется форма редактора. PUT сохраняет присланный контент целиком.

ЯЗЫКИ. `?lang=xx` открывает перевод: GET отдаёт и то, что показываем (`content`,
с фолбэком на русский), и то, что РЕАЛЬНО сохранено для этого языка (`own`) —
редактор должен отличать «перевели точно так же» от «не переводили вовсе». PUT с
тем же параметром пишет в ветку `i18n.xx`; пустое поле стирает перевод, и раздел
снова показывается по-русски. Русский язык — корень файла, как раньше.
"""

import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from src.web.endpoints.public.info_content import (
    BASE_LANG,
    INFO_PATH,
    MAX_LANGS,
    TEXT_SECTIONS,
    effective_content,
    load_stored,
    normalize_lang,
    stored_for_lang,
    translated_langs,
    translations,
)

from ._common import AdminUser

router = APIRouter(prefix="/info", tags=["Admin - Info"])

MAX_TEXT = 20000
MAX_FAQ_ITEMS = 100


class FaqItem(BaseModel):
    q: str = Field(min_length=1, max_length=300)
    a: str = Field(min_length=1, max_length=4000)


class InfoUpdate(BaseModel):
    faq: Optional[list[FaqItem]] = None
    rules: Optional[str] = None
    privacy: Optional[str] = None
    offer: Optional[str] = None
    statuses: Optional[str] = None


def _save(data: dict[str, Any]) -> None:
    try:
        INFO_PATH.parent.mkdir(parents=True, exist_ok=True)
        with INFO_PATH.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Не удалось сохранить контент: {exc}",
        )


@router.get("")
async def get_info_admin(_admin: AdminUser, lang: Optional[str] = None) -> dict[str, Any]:
    code = normalize_lang(lang) or BASE_LANG
    # Плоский контент остаётся В КОРНЕ ответа: старый кабинет читает именно его и
    # о языках не знает. Новое — рядом, отдельными полями.
    return {
        **effective_content(code),
        "lang": code,
        "base_lang": BASE_LANG,
        # Что реально сохранено именно для этого языка: пусто = не переводили.
        "own": stored_for_lang(code),
        # Русский всегда под рукой: редактор показывает его подсказкой.
        "base": effective_content(BASE_LANG),
        "translated_langs": translated_langs(),
    }


def _update_translation(body: InfoUpdate, code: str) -> dict[str, Any]:
    """Записать перевод в ветку i18n. Пустое поле — снять перевод (вернуть русский)."""
    stored = load_stored()
    i18n = {k: dict(v) for k, v in translations(stored).items()}
    if code not in i18n and len(i18n) >= MAX_LANGS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Слишком много языков (максимум {MAX_LANGS})",
        )
    current = i18n.get(code, {})

    if body.faq is not None:
        if len(body.faq) > MAX_FAQ_ITEMS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Слишком много вопросов (максимум {MAX_FAQ_ITEMS})",
            )
        items = [
            {"q": i.q.strip(), "a": i.a.strip()} for i in body.faq if i.q.strip() and i.a.strip()
        ]
        if items:
            current["faq"] = items
        else:
            current.pop("faq", None)

    for key in TEXT_SECTIONS:
        value = getattr(body, key)
        if value is None:
            continue
        if len(value) > MAX_TEXT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{key}: текст длиннее {MAX_TEXT} символов",
            )
        if value.strip():
            current[key] = value
        else:
            current.pop(key, None)

    if current:
        i18n[code] = current
    else:
        i18n.pop(code, None)

    data = {k: v for k, v in stored.items() if k != "i18n"}
    if not data:
        # Русского ещё не сохраняли — фиксируем дефолты, иначе перевод повис бы
        # над пустотой и любой будущий дефолт менял бы смысл готового перевода.
        data = effective_content(BASE_LANG)
    if i18n:
        data["i18n"] = i18n
    _save(data)
    return {
        **effective_content(code),
        "lang": code,
        "base_lang": BASE_LANG,
        "own": dict(i18n.get(code) or {}),
        "base": effective_content(BASE_LANG),
        "translated_langs": translated_langs(),
    }


@router.put("")
async def update_info_admin(
    body: InfoUpdate, _admin: AdminUser, lang: Optional[str] = None
) -> dict[str, Any]:
    code = normalize_lang(lang) or BASE_LANG
    if code != BASE_LANG:
        return _update_translation(body, code)

    # Стартуем от текущего эффективного контента, чтобы не затирать незаданные
    # в запросе разделы (PUT может прислать только изменённое).
    data: dict[str, Any] = effective_content()
    # Переводы живут отдельной веткой и русской правкой не трогаются.
    saved_i18n = translations()

    if body.faq is not None:
        if len(body.faq) > MAX_FAQ_ITEMS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Слишком много вопросов (максимум {MAX_FAQ_ITEMS})",
            )
        data["faq"] = [{"q": i.q.strip(), "a": i.a.strip()} for i in body.faq]

    for key in TEXT_SECTIONS:
        value = getattr(body, key)
        if value is not None:
            if len(value) > MAX_TEXT:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{key}: текст длиннее {MAX_TEXT} символов",
                )
            data[key] = value

    if saved_i18n:
        data["i18n"] = saved_i18n
    _save(data)
    return data
