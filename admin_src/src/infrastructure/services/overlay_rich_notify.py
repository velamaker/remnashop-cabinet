"""[OVERLAY] Rich-вид админ-уведомлений (Bot API 10.1, метод sendRichMessage).

Владелец попросил вид как у бота «Бедолага»: жирный заголовок, таблица
«показатель → значение» и футер со временем вместо простыни текста.

Наши уведомления уже собраны из фрагментов вида «• <b>Ключ</b>: значение»
внутри <blockquote> (см. utils.ftl, frg-*), поэтому ничего переписывать в
шаблонах не нужно: здесь разбираем готовый HTML и перекладываем буллеты в
<table>. Если разобрать нечего (нет ни одной пары ключ-значение) — возвращаем
None, и уведомление уходит обычным send_message.

В aiogram 3.25 метода sendRichMessage нет (появился в 3.29), а поднимать
aiogram нельзя — на нём завязан aiogram_dialog со всеми диалогами бота.
Поэтому зовём Bot API напрямую. Если сервер метод не знает — запоминаем это
до перезапуска и больше не пробуем, всё продолжает работать по-старому.

Тумблер: NOTIFY_RICH (по умолчанию включён).
"""

import html as html_lib
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

from aiogram import Bot
from loguru import logger

# Сервер не поддерживает rich — латч до перезапуска процесса.
_rich_unavailable = False

# «• <b>Название</b>: 🇩🇪 Germany» → ключ + значение (значение остаётся сырым HTML).
_ROW_RE = re.compile(r"^\s*•\s*<b>(?P<key>.+?)</b>\s*:\s*(?P<value>.*?)\s*$")
# Заголовок уведомления — первая строка целиком в <b>…</b>.
_TITLE_RE = re.compile(r"^\s*<b>(?P<title>.+?)</b>\s*$")
_TAG_RE = re.compile(r"<[^>]+>")


def rich_enabled() -> bool:
    if _rich_unavailable:
        return False
    return (os.environ.get("NOTIFY_RICH") or "true").strip().lower() in (
        "1", "true", "yes", "on", "да",
    )


def _mark_unavailable(reason: str) -> None:
    global _rich_unavailable
    if not _rich_unavailable:
        logger.warning(f"[OVERLAY] Bot API не поддерживает sendRichMessage ({reason}) — обычный вид")
    _rich_unavailable = True


def logo_src() -> str:
    """Абсолютный URL логотипа для шапки rich-сообщения (или '' — нет логотипа).

    Тот же логотип, что в кабинете и в письмах: assets/branding.json →
    /api/appearance/logo, абсолютный по WEB_CABINET_URL (Telegram грузит картинку
    снаружи, относительный путь ему не годится).
    """
    try:
        from src.web.endpoints.public.appearance import (  # noqa: PLC0415
            load_branding,
            logo_url,
        )

        rel = logo_url(load_branding().get("logo_file"))
        if not rel:
            return ""
        base = (os.environ.get("WEB_CABINET_URL") or "").strip().rstrip("/")
        if not base:
            origins = (os.environ.get("APP_ORIGINS") or "").strip()
            base = origins.split(",")[0].strip().rstrip("/") if origins else ""
        return f"{base}{rel}" if base.startswith("http") else ""
    except Exception:  # noqa: BLE001
        return ""


# Сколько знаков помещается в колонку значений на телефоне. Больше — таблица
# перестаёт влезать в ширину экрана и уезжает вбок вместе с подписями: значения
# видно, только если тянуть её пальцем (жалоба владельца 7 августа: «переезжают
# поля»). Причина всегда одна — ОДНО длинное слово без пробелов (UUID подписки,
# токен, длинная ссылка): перенести его Telegram не может, и колонка растягивается.
_CELL_LIMIT = 22
# Сколько оставляем с краёв при сокращении. Начала UUID хватает, чтобы узнать
# запись, а хвост оставляем, чтобы две похожие не выглядели одинаково.
_CELL_HEAD, _CELL_TAIL = 12, 6


def _fit(value: str) -> str:
    """Значение для ячейки: длинное СЛОВО сокращаем серединой, остальное как есть.

    Текст с пробелами («2,32 МБ из 100 ГБ») не трогаем — его Telegram переносит
    сам. Значение с разметкой (`<code>`, ссылка) тоже не трогаем: резать внутри
    тегов значит их сломать, а такие значения в наших шаблонах короткие.
    """
    if "<" in value:
        return value
    plain = value.strip()
    if len(plain) <= _CELL_LIMIT or " " in plain:
        return value
    return f"{plain[:_CELL_HEAD]}…{plain[-_CELL_TAIL:]}"


def build_rich_html(text: str, footer_label: str, logo: str = "") -> Optional[str]:
    """Переводит наш HTML-текст уведомления в rich-разметку.

    None — если структуру не распознали (тогда шлём как раньше).
    """
    if not text or not text.strip():
        return None

    title: Optional[str] = None
    rows: list[tuple[str, str]] = []
    paragraphs: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line in ("<blockquote>", "</blockquote>"):
            continue
        line = line.removeprefix("<blockquote>").removesuffix("</blockquote>").strip()
        if not line:
            continue

        row = _ROW_RE.match(line)
        if row:
            rows.append((row.group("key"), row.group("value")))
            continue

        if title is None:
            heading = _TITLE_RE.match(line)
            if heading:
                title = heading.group("title")
                continue

        # Строки-подзаголовки вида «<b>🖥 Нода</b>:» в таблице не нужны — они
        # только дублируют заголовок секции, которая и так стала таблицей.
        if line.endswith(":") and _TAG_RE.sub("", line).strip().endswith(":"):
            continue

        paragraphs.append(line)

    # Таблица есть не всегда: алерты мониторинга — это фразы, а не пары
    # «ключ-значение». Такие уведомления тоже показываем в новом виде (логотип,
    # заголовок, футер со временем), просто абзацами. Пусто совсем — отдаём None,
    # и уведомление уходит как раньше.
    if not rows and not paragraphs and title is None:
        return None

    if title is None:
        title = _TAG_RE.sub("", paragraphs.pop(0)).strip() if paragraphs else footer_label

    now = datetime.now(timezone.utc)
    stamp = (
        f'<tg-time unix="{int(now.timestamp())}" format="dt">'
        f'{now.strftime("%d.%m.%Y %H:%M")} UTC</tg-time>'
    )

    parts = []
    if logo:
        parts.append(f'<img src="{html_lib.escape(logo, quote=True)}"/>')
    parts.append(f"<h5>{title}</h5>")
    parts.extend(f"<p>{p}</p>" for p in paragraphs)
    if rows:
        body = "".join(
            f"<tr><td>{html_lib.escape(_TAG_RE.sub('', key).strip())}</td>"
            f'<td align="right">{_fit(value)}</td></tr>'
            for key, value in rows
        )
        parts.append(f"<table bordered striped>{body}</table>")
    parts.append("<hr/>")
    parts.append(f"<footer>{html_lib.escape(footer_label)} · {stamp}</footer>")
    return "".join(parts)


async def send_rich_message(
    bot: Bot,
    chat_id: int,
    rich_html: str,
    reply_markup: Any = None,
    disable_notification: Optional[bool] = None,
) -> bool:
    """Шлёт rich-сообщение через Bot API. False — не вышло, шлите обычным путём."""
    if not rich_enabled():
        return False

    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "rich_message": {"html": rich_html},
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup.model_dump(exclude_none=True)
    if disable_notification is not None:
        payload["disable_notification"] = disable_notification

    url = bot.session.api.api_url(token=bot.token, method="sendRichMessage")

    try:
        session = await bot.session.create_session()
        async with session.post(url, json=payload) as response:
            status = response.status
            data = await response.json()
    except Exception as exc:  # noqa: BLE001 — сеть/парсинг: молча падаем на обычный вид
        logger.warning(f"[OVERLAY] rich-отправка не удалась ({exc}) — обычный вид")
        return False

    if data.get("ok"):
        return True

    description = str(data.get("description", ""))
    if status == 404 or "not found" in description.lower():
        _mark_unavailable(description or "404")
        return False

    logger.warning(f"[OVERLAY] rich-отправка отклонена: {description} — обычный вид")
    return False
