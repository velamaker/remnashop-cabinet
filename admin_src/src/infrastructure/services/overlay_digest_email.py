"""Месячная сводка письмом (overlay RемнаShop).

КОМУ. Сводка «сколько трафика за 30 дней + любимый сервер» уходит в Telegram и
push (taskiq/tasks/digest.py). Тем, у кого нет ни того ни другого, она не
доходила вовсе, хотя подтверждённая почта у них есть. Письмо получают ровно они:
условия выборки взаимоисключающие с Telegram-частью (`TG_AUDIENCE_SQL` против
`EMAIL_AUDIENCE_SQL`), и обе выборки делаются до первой отправки — одному
человеку сводка дважды не приходит. Тот же проход, тот же день и час, те же цифры.

ЧЕМ ПИСЬМО ОПАСНЕЕ СООБЩЕНИЯ. Его не отзовёшь и не отредактируешь, повтор виден
сразу, а жалоба «спам» бьёт по доставке ВСЕХ писем сервиса, включая коды входа.
Отсюда защиты, каждая под тестом (tests/test_digest_email.py):
  • выключено по умолчанию, а при препятствиях (`email_blockers`) — 0 писем и
    0 записей, даже если тумблер включили;
  • журнал на человека (`digest_email_sends`): строка занимается ДО отправки, и
    кто её не занял — не шлёт; застрявший `sending` повторно не отправляется;
  • не больше `EMAIL_MAX_PER_RUN` за проход (Brevo free — 300 писем в день на всё);
  • подписанная ссылка «Отписаться» в каждом письме;
  • для Brevo — отдельный адрес отправителя. Brevo ставит свой List-Unsubscribe,
    и «Отписаться» в почтовике блокирует у него ВСЕ письма от этого отправителя:
    с общего адреса человек без Telegram потерял бы коды входа и сброс пароля.

Модуль без aiogram и без задач: его зовут и крон, и админка (холостой прогон,
тестовое письмо), и публичная ручка отписки.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional, Protocol
from urllib.parse import quote

import httpx
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.services.email_settings import PRESETS
from src.infrastructure.services.overlay_push import _fill, _record_user_notification

KIND = "digest"

# Brevo free — 300 писем в день на ВСЁ, коды входа в том числе. Сводка забирает
# не больше двух третей, остальным ставится `over_limit` (сейчас адресатов 6).
EMAIL_MAX_PER_RUN = 200
EMAIL_PAUSE = 0.5
DRY_RUN_LIMIT = 25
# Пауза между запросами к панели в холостом прогоне — как у Telegram-части.
USAGE_PAUSE = 0.1

SENDING = "sending"
SENT = "sent"
FAILED = "failed"
NO_TRAFFIC = "no_traffic"
USAGE_ERROR = "usage_error"
OVER_LIMIT = "over_limit"
PROVIDER_BLOCKED = "provider_blocked"
STATUSES = (SENT, FAILED, NO_TRAFFIC, USAGE_ERROR, OVER_LIMIT, PROVIDER_BLOCKED, SENDING)

_GB = 1024 ** 3

BREVO_BLOCKED_URL = "https://api.brevo.com/v3/smtp/blockedContacts"
_BREVO_PAGE = 100
_BREVO_MAX_PAGES = 20

# Пример для предпросмотра и тестового письма: панель и база не нужны.
SAMPLE_TOTAL = int(42.5 * _GB)
SAMPLE_FAVORITE = {"ru": "Нидерланды", "en": "Netherlands"}

# Telegram-часть: активные USER с подпиской и хотя бы одним каналом. Текст запроса
# перенесён из tasks/digest.py дословно — вынесен сюда, чтобы взаимоисключаемость
# с письмами запиралась тестом на живом Postgres, а не честным словом.
TG_AUDIENCE_SQL = (
    "SELECT u.id, lower(u.language::text), u.telegram_id, s.user_remna_id "
    "FROM users u "
    "JOIN subscriptions s ON u.current_subscription_id = s.id "
    "WHERE u.role = 'USER' AND s.status = 'ACTIVE' AND s.user_remna_id IS NOT NULL "
    "AND (u.telegram_id IS NOT NULL "
    "     OR EXISTS(SELECT 1 FROM push_subscriptions p WHERE p.user_id = u.id))"
)

# Письмом — ровно те, до кого Telegram-часть не дотягивается: нет Telegram И нет
# push. Резерв и пробный не исключаем — как и в Telegram-части. Персонал отсечён
# ролью. Заблокированным в админке не пишем — как и email-рассылки из админки
# (broadcast_email._BASE): письмо, в отличие от бота, дойдёт до них наверняка.
EMAIL_AUDIENCE_SQL = (
    "SELECT u.id, lower(u.language::text), u.email, s.user_remna_id "
    "FROM users u JOIN subscriptions s ON u.current_subscription_id = s.id "
    "WHERE u.role = 'USER' AND u.is_blocked = false "
    "AND s.status = 'ACTIVE' AND s.user_remna_id IS NOT NULL "
    "AND u.telegram_id IS NULL "
    "AND NOT EXISTS (SELECT 1 FROM push_subscriptions p WHERE p.user_id = u.id) "
    "AND u.email IS NOT NULL AND u.is_email_verified = true "
    "AND NOT EXISTS (SELECT 1 FROM email_opt_outs o "
    "WHERE o.user_id = u.id AND o.kind = 'digest') "
    "ORDER BY u.id"
)

# Короткая сводка — Telegram, push и лента уведомлений в кабинете. Письмо пишет в
# ленту ТОТ ЖЕ текст: в колокольчике у всех одна и та же сводка.
SHORT_MESSAGES = {
    "ru": (
        "📊 Ваш месяц с VPN",
        "За месяц вы использовали {gb} ГБ.{fav} Спасибо, что с нами!",
    ),
    "en": (
        "📊 Your month with VPN",
        "This month you used {gb} GB.{fav} Thanks for being with us!",
    ),
}
SHORT_FAV = {"ru": " Любимый сервер — {name}.", "en": " Favorite server — {name}."}

_EMAIL_TEXTS: dict[str, dict[str, str]] = {
    "ru": {
        "subject": "Ваш месяц с {brand}",
        "test_prefix": "[Тест] ",
        "body": (
            "Здравствуйте!\n\nЗа последние 30 дней вы использовали {gb} ГБ трафика.{fav}"
            "\n\nСпасибо, что вы с нами!"
        ),
        "fav": " Любимый сервер — {name}.",
        "button": "Открыть кабинет",
        "footer": "Сводка приходит раз в месяц, пока у вас активна подписка.",
        "unsubscribe": "Отписаться от сводки",
    },
    "en": {
        "subject": "Your month with {brand}",
        "test_prefix": "[Test] ",
        "body": (
            "Hello!\n\nOver the last 30 days you used {gb} GB of traffic.{fav}"
            "\n\nThanks for being with us!"
        ),
        "fav": " Favorite server — {name}.",
        "button": "Open your account",
        "footer": "This summary comes once a month while your subscription is active.",
        "unsubscribe": "Unsubscribe from the summary",
    },
}

BLOCK_MAIL = "Почта не настроена или выключена — раздел «Почта»."
BLOCK_CABINET = (
    "Не задан адрес кабинета (WEB_CABINET_URL) — без ссылки «Отписаться» "
    "письма не отправляются."
)
BLOCK_PATCH = (
    "Оформление писем не подключено (правка email_sender не применилась) — "
    "письма не отправляются."
)
BLOCK_BREVO = (
    "Почта идёт через Brevo: для сводки нужен отдельный адрес отправителя "
    "(например, digest@ваш-домен), добавленный в Brevo. С общего адреса нажатие "
    "«Отписаться» в почтовике закроет человеку и коды входа, и сброс пароля."
)
BLOCK_DISABLED = "Сводка письмом выключена."
BLOCK_SECRET = "Нет ключа подписи ссылок (APP_CRYPT_KEY) — письма не отправляются."


# ── Кому ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EmailRecipient:
    user_id: int
    lang: str
    email: str
    remna_uuid: str


async def select_email_audience(session: AsyncSession) -> list[EmailRecipient]:
    rows = (await session.execute(text(EMAIL_AUDIENCE_SQL))).all()
    return [
        EmailRecipient(int(r[0]), str(r[1] or "ru"), str(r[2]).strip(), str(r[3]))
        for r in rows
        if r[2] and str(r[2]).strip()
    ]


async def select_email_audience_safe(session: AsyncSession) -> list[EmailRecipient]:
    """Выборка для крона: сбой — пустой список, а не упавшая задача.

    Нет таблицы отписок (миграция не прошла) — это не повод терять Telegram-часть.
    Откат обязателен: запрос в прерванной транзакции оставил бы сессию сломанной, и
    следующий же запрос прохода упал бы уже по чужой вине.
    """
    try:
        return await select_email_audience(session)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"digest: адресаты писем не выбраны ({type(exc).__name__}) — письма пропущены")
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return []


async def audience_count(session: AsyncSession) -> int:
    return int(
        (await session.execute(text(f"SELECT count(*) FROM ({EMAIL_AUDIENCE_SQL}) a"))).scalar()
        or 0
    )


# ── Расход ────────────────────────────────────────────────────────────────────


async def fetch_usage(
    sdk: Any, uuid: str, start: datetime, end: datetime
) -> Optional[tuple[int, Optional[str]]]:
    """(байт за период, имя любимого сервера) или None, если панель не ответила.

    Перенесено из tasks/digest.py без изменения смысла: обе части сводки зовут
    это, и цифры в письме и в Telegram не могут разойтись.
    """
    try:
        result = await sdk.bandwidthstats.get_stats_user_usage(
            uuid=str(uuid),
            top_nodes_limit=5,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
        )
        data = getattr(result, "root", result)
        nodes = getattr(data, "top_nodes", None) or []
        total = sum(int(getattr(n, "total", 0) or 0) for n in nodes)
        fav = max(nodes, key=lambda n: int(getattr(n, "total", 0) or 0), default=None)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"digest: расход из панели не получен: {exc}")
        return None
    name = getattr(fav, "name", None) if fav is not None else None
    return total, (str(name) if name else None)


# ── Тексты ────────────────────────────────────────────────────────────────────


def email_lang(lang: Any) -> str:
    """ru/en; остальным языкам — ru (переводов письма пока два)."""
    value = str(getattr(lang, "value", lang) or "").lower()[:2]
    return value if value in _EMAIL_TEXTS else "ru"


def format_gb(total: int, lang: str) -> str:
    """Та же точность, что в Telegram (одна цифра после запятой), но по-русски — с запятой."""
    value = f"{round(total / _GB, 1):.1f}"
    return value.replace(".", ",") if email_lang(lang) == "ru" else value


def build_email_parts(
    lang: str, total: int, favorite: Optional[str], brand: str, *, test: bool = False
) -> tuple[str, str, dict[str, str]]:
    """(тема, тело, подписи оформления). Подстановки через _fill: скобка в имени
    сервера или бренда не должна ронять проход по всем адресатам."""
    t = _EMAIL_TEXTS[email_lang(lang)]
    fav = _fill(t["fav"], {"name": favorite}) if favorite else ""
    subject = _fill(t["subject"], {"brand": brand})
    if test:
        subject = f"{t['test_prefix']}{subject}"
    body = _fill(t["body"], {"gb": format_gb(total, lang), "fav": fav})
    opts = {
        "button_label": t["button"],
        "footer_note": t["footer"],
        "unsubscribe_label": t["unsubscribe"],
    }
    return subject, body, opts


def short_payload(lang: str, total: int, favorite: Optional[str]) -> dict[str, str]:
    """Запись в ленту — дословно то, что получает Telegram-часть."""
    l = str(lang or "ru").lower()[:2]
    gb = round(total / _GB, 1)
    fav = _fill(SHORT_FAV.get(l, SHORT_FAV["ru"]), {"name": favorite}) if favorite else ""
    title, body = SHORT_MESSAGES.get(l) or SHORT_MESSAGES["ru"]
    return {
        "title": _fill(title, {"gb": gb, "fav": fav}),
        "body": _fill(body, {"gb": gb, "fav": fav}),
        "url": "/",
        "tag": "digest",
    }


def brand_name(settings: dict) -> str:
    """Имя в теме и в шапке: имя отправителя, иначе бренд кабинета, иначе «VPN».

    Одно на тему и шапку: «Ваш месяц с Begemot VPN» под шапкой «VPN» выглядит как
    подделка.
    """
    from_name = str((settings or {}).get("from_name") or "").strip()
    if from_name:
        return from_name
    try:
        from src.web.endpoints.public.appearance import load_branding, resolve_brand_name

        return str(load_branding().get("brand_name") or "").strip() or resolve_brand_name() or "VPN"
    except Exception:  # noqa: BLE001 — бренд не повод не отправить письмо
        return "VPN"


# ── Ссылка отписки ────────────────────────────────────────────────────────────

# id без ведущих нулей: у одного человека ровно одна строка-токен.
_TOKEN_RE = re.compile(r"([1-9][0-9]{0,11})\.([0-9a-f]{32})")


def _sign(user_id: int, secret: str) -> str:
    msg = f"remnashop:email-optout:{KIND}:{user_id}"
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()[:32]


def make_optout_token(user_id: int, secret: str) -> str:
    """Токен без срока: ссылка в письме годовой давности обязана работать.

    Даёт ровно одно право — включить или выключить сводку этому id. Смена
    APP_CRYPT_KEY делает старые ссылки недействительными.
    """
    uid = int(user_id)
    return f"{uid}.{_sign(uid, secret)}"


def parse_optout_token(token: Any, secret: str) -> Optional[int]:
    # Длину режем здесь, а не валидацией запроса: 422 на странице отписки читался
    # бы как «попробуйте позже», а это просто испорченная ссылка.
    if not secret or not isinstance(token, str) or len(token) > 64:
        return None
    match = _TOKEN_RE.fullmatch(token)
    if match is None:
        return None
    uid = int(match.group(1))
    if not hmac.compare_digest(match.group(2), _sign(uid, secret)):
        return None
    return uid


def cabinet_url() -> str:
    return (os.environ.get("WEB_CABINET_URL") or "").strip().rstrip("/")


def unsubscribe_page_url(token: str) -> str:
    """Страница кабинета: GET ничего не меняет, отписка — кнопкой."""
    return f"{cabinet_url()}/email/unsubscribe?t={quote(token, safe='')}"


def one_click_url(token: str) -> str:
    """Адрес для List-Unsubscribe-Post: почтовик шлёт сюда POST без участия человека."""
    return f"{cabinet_url()}/api/email-optout/digest?t={quote(token, safe='')}"


# ── Препятствия ───────────────────────────────────────────────────────────────


def sender_settings(sender: Any) -> dict:
    """Эффективные настройки почты отправителя (.env + админка) или {}."""
    getter = getattr(sender, "_settings", None)
    if not callable(getter):
        return {}
    try:
        value = getter()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"digest: настройки почты не прочитаны ({type(exc).__name__})")
        return {}
    return dict(value) if isinstance(value, dict) else {}


def is_brevo_like(settings: dict) -> bool:
    """Письмо уйдёт через Brevo — API или его SMTP-релей (у старых установок host
    ещё sendinblue). Именно у Brevo «Отписаться» блокирует отправителя целиком."""
    provider = str(settings.get("provider") or "").lower()
    if provider == "brevo" and settings.get("brevo_api_key"):
        return True
    host = str(settings.get("host") or "").lower()
    return "brevo" in host or "sendinblue" in host


def effective_from(settings: dict, cfg: dict) -> str:
    """С какого адреса реально уйдёт сводка.

    Пресеты Gmail/Яндекс/Mail.ru подменить From не дают — письмо уйдёт с основного
    адреса; блок-листа Brevo у них нет, так что и защищать нечего. Правило то же,
    что в отправителе (email_sender.branded_from_allowed).
    """
    own = str(cfg.get("email_from") or "").strip()
    if own and str(settings.get("provider") or "").lower() not in PRESETS:
        return own
    return str(settings.get("from_email") or "").strip()


def _sender_enabled(sender: Any) -> bool:
    try:
        value = getattr(sender, "is_enabled", False)
        if callable(value):  # у подделок и старых отправителей — метод, а не свойство
            value = value()
        return bool(value)
    except Exception:  # noqa: BLE001
        return False


def email_blockers(sender: Any, cfg: dict, settings: dict) -> list[str]:
    """Что мешает слать. Непустой список — ни одного письма и ни одной записи.

    Одни и те же проверки на включении тумблера (409) и в момент рассылки: между
    ними могли выключить почту, сменить провайдера или потерять WEB_CABINET_URL.
    """
    blockers: list[str] = []
    if not _sender_enabled(sender):
        blockers.append(BLOCK_MAIL)
    if not cabinet_url():
        blockers.append(BLOCK_CABINET)
    if not callable(getattr(sender, "send_branded", None)):
        blockers.append(BLOCK_PATCH)
    if is_brevo_like(settings):
        own = str(cfg.get("email_from") or "").strip().lower()
        main = str(settings.get("from_email") or "").strip().lower()
        if not own or own == main:
            blockers.append(BLOCK_BREVO)
    return blockers


async def fetch_brevo_blocked(api_key: str, sender_email: str) -> Optional[set[str]]:
    """Адреса, заблокированные в Brevo для отправителя сводки. None — не узнали.

    Только чтение. Зачем: Brevo такому адресату письмо не доставит, но API ответит
    успехом — и в итогах месяца он числился бы «отправленным». Сбой чтения не
    останавливает проход: доставлять заблокированному Brevo всё равно не станет.
    """
    if not api_key or not sender_email:
        return None
    found: set[str] = set()
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            for page in range(_BREVO_MAX_PAGES):
                resp = await client.get(
                    BREVO_BLOCKED_URL,
                    params={
                        "senders": sender_email,
                        "limit": _BREVO_PAGE,
                        "offset": page * _BREVO_PAGE,
                    },
                    headers={"api-key": api_key, "accept": "application/json"},
                )
                if resp.status_code >= 400:
                    logger.warning(
                        f"digest: блок-лист Brevo не прочитан (HTTP {resp.status_code}) — шлём без него"
                    )
                    return None
                contacts = (resp.json() or {}).get("contacts") or []
                for contact in contacts:
                    email = contact.get("email") if isinstance(contact, dict) else None
                    if isinstance(email, str) and email.strip():
                        found.add(email.strip().lower())
                if len(contacts) < _BREVO_PAGE:
                    break
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"digest: блок-лист Brevo не прочитан ({type(exc).__name__}) — шлём без него")
        return None
    return found


# ── Журнал отправок ───────────────────────────────────────────────────────────


class Ledger(Protocol):
    async def claim(self, user_id: int, month: str) -> bool: ...

    async def finish(
        self, user_id: int, month: str, status: str, error: Optional[str] = None
    ) -> None: ...

    async def mark_if_absent(self, user_id: int, month: str, status: str) -> bool: ...

    async def status(self, user_id: int, month: str) -> Optional[str]: ...


class DbLedger:
    """digest_email_sends. Коммит после КАЖДОЙ записи: занятая строка должна пережить
    падение процесса на следующей же строке кода — иначе после рестарта письмо
    ушло бы второй раз."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _write(self, sql: str, params: dict, *, returning: bool = False) -> Any:
        try:
            result = await self.session.execute(text(sql), params)
            row = result.first() if returning else None
            await self.session.commit()
            return row
        except Exception:
            try:
                await self.session.rollback()
            except Exception:  # noqa: BLE001
                pass
            raise

    async def claim(self, user_id: int, month: str) -> bool:
        row = await self._write(
            "INSERT INTO digest_email_sends (user_id, month, status) VALUES (:u, :m, :s) "
            "ON CONFLICT (user_id, month) DO NOTHING RETURNING user_id",
            {"u": user_id, "m": month, "s": SENDING},
            returning=True,
        )
        return row is not None

    async def finish(
        self, user_id: int, month: str, status: str, error: Optional[str] = None
    ) -> None:
        await self._write(
            "UPDATE digest_email_sends SET status = :s, error = :e, updated_at = now() "
            "WHERE user_id = :u AND month = :m",
            {"u": user_id, "m": month, "s": status, "e": error[:300] if error else None},
        )

    async def mark_if_absent(self, user_id: int, month: str, status: str) -> bool:
        row = await self._write(
            "INSERT INTO digest_email_sends (user_id, month, status) VALUES (:u, :m, :s) "
            "ON CONFLICT (user_id, month) DO NOTHING RETURNING user_id",
            {"u": user_id, "m": month, "s": status},
            returning=True,
        )
        return row is not None

    async def status(self, user_id: int, month: str) -> Optional[str]:
        row = (
            await self.session.execute(
                text("SELECT status FROM digest_email_sends WHERE user_id = :u AND month = :m"),
                {"u": user_id, "m": month},
            )
        ).first()
        return str(row[0]) if row is not None else None


async def status_summary(session: AsyncSession) -> Optional[dict[str, Any]]:
    """Итог последнего месяца рассылки по статусам или None, если писем ещё не было."""
    rows = (
        await session.execute(
            text(
                "SELECT month, status, count(*) FROM digest_email_sends "
                "WHERE month = (SELECT max(month) FROM digest_email_sends) "
                "GROUP BY month, status"
            )
        )
    ).all()
    if not rows:
        return None
    summary: dict[str, Any] = {"month": str(rows[0][0]).strip(), **{s: 0 for s in STATUSES}}
    for _month, status, count in rows:
        if status in summary:
            summary[status] = int(count)
    return summary


# ── Отписка ───────────────────────────────────────────────────────────────────


async def user_exists(session: AsyncSession, user_id: int) -> bool:
    row = (
        await session.execute(text("SELECT 1 FROM users WHERE id = :u"), {"u": user_id})
    ).first()
    return row is not None


async def is_opted_out(session: AsyncSession, user_id: int) -> bool:
    row = (
        await session.execute(
            text("SELECT 1 FROM email_opt_outs WHERE user_id = :u AND kind = :k"),
            {"u": user_id, "k": KIND},
        )
    ).first()
    return row is not None


async def set_opt_out(session: AsyncSession, user_id: int, opted_out: bool) -> None:
    """Без коммита — коммитит вызывающая ручка."""
    if opted_out:
        await session.execute(
            text(
                "INSERT INTO email_opt_outs (user_id, kind) VALUES (:u, :k) "
                "ON CONFLICT (user_id, kind) DO NOTHING"
            ),
            {"u": user_id, "k": KIND},
        )
    else:
        await session.execute(
            text("DELETE FROM email_opt_outs WHERE user_id = :u AND kind = :k"),
            {"u": user_id, "k": KIND},
        )


async def opted_out_count(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(
                text("SELECT count(*) FROM email_opt_outs WHERE kind = :k"), {"k": KIND}
            )
        ).scalar()
        or 0
    )


# ── Проход ────────────────────────────────────────────────────────────────────


async def record_feed(session: AsyncSession, user_id: int, payload: dict) -> None:
    await _record_user_notification(session, user_id, payload)
    await session.commit()


def _summary() -> dict[str, Any]:
    return {
        SENT: 0,
        FAILED: 0,
        NO_TRAFFIC: 0,
        USAGE_ERROR: 0,
        OVER_LIMIT: 0,
        PROVIDER_BLOCKED: 0,
        "already": 0,
        "blocked": [],
    }


async def run_email_pass(
    *,
    sender: Any,
    cfg: dict,
    settings: dict,
    sdk: Any,
    secret: str,
    month: str,
    start: datetime,
    end: datetime,
    recipients: list[EmailRecipient],
    ledger: Ledger,
    session_for_feed: Optional[AsyncSession] = None,
    usage: Callable[..., Awaitable[Optional[tuple[int, Optional[str]]]]] = fetch_usage,
    blocked_fetch: Callable[[str, str], Awaitable[Optional[set[str]]]] = fetch_brevo_blocked,
    feed: Callable[[Any, int, dict], Awaitable[None]] = record_feed,
    max_per_run: int = EMAIL_MAX_PER_RUN,
    pause: float = EMAIL_PAUSE,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    brand: Optional[str] = None,
) -> dict[str, Any]:
    """Письма одного месячного прохода. Возвращает счётчики по исходам.

    Исключение журнала (нет таблицы, база отвалилась) прерывает проход целиком:
    слать, не записав, — значит слать повторно на следующем запуске.
    """
    summary = _summary()
    blockers = email_blockers(sender, cfg, settings)
    if not cfg.get("email_enabled"):
        blockers.insert(0, BLOCK_DISABLED)
    if not secret:
        blockers.append(BLOCK_SECRET)
    if blockers:
        summary["blocked"] = blockers
        return summary

    sender_email = effective_from(settings, cfg)
    blocked: Optional[set[str]] = None
    if is_brevo_like(settings):
        try:
            blocked = await blocked_fetch(str(settings.get("brevo_api_key") or ""), sender_email)
        except Exception as exc:  # noqa: BLE001 — предфильтр, не условие отправки
            logger.warning(f"digest: блок-лист Brevo не прочитан ({type(exc).__name__})")
            blocked = None

    brand = brand or brand_name(settings)
    attempts = 0
    for r in recipients:
        if attempts >= max_per_run:
            await ledger.mark_if_absent(r.user_id, month, OVER_LIMIT)
            summary[OVER_LIMIT] += 1
            continue
        if not await ledger.claim(r.user_id, month):
            summary["already"] += 1
            continue
        if blocked is not None and r.email.strip().lower() in blocked:
            await ledger.finish(r.user_id, month, PROVIDER_BLOCKED)
            summary[PROVIDER_BLOCKED] += 1
            continue

        got = await usage(sdk, r.remna_uuid, start, end)
        if got is None:
            logger.debug(f"digest: письмо user_id={r.user_id} — панель не отдала расход")
            await ledger.finish(r.user_id, month, USAGE_ERROR)
            summary[USAGE_ERROR] += 1
            await sleep(pause)
            continue
        total, favorite = got
        if total <= 0:
            await ledger.finish(r.user_id, month, NO_TRAFFIC)
            summary[NO_TRAFFIC] += 1
            await sleep(pause)
            continue

        subject, body, opts = build_email_parts(r.lang, total, favorite, brand)
        token = make_optout_token(r.user_id, secret)
        attempts += 1
        try:
            await sender.send_branded(
                to=r.email,
                subject=subject,
                body=body,
                brand=brand,
                from_email=str(cfg.get("email_from") or ""),
                list_unsubscribe_url=one_click_url(token),
                unsubscribe_url=unsubscribe_page_url(token),
                **opts,
            )
        except Exception as exc:  # noqa: BLE001
            cause = exc.__cause__ or exc
            await ledger.finish(r.user_id, month, FAILED, repr(cause)[:300])
            summary[FAILED] += 1
            logger.warning(f"digest: письмо user_id={r.user_id} не отправлено ({type(cause).__name__})")
        else:
            await ledger.finish(r.user_id, month, SENT)
            summary[SENT] += 1
            if session_for_feed is not None:
                # Лента — довесок: письмо уже ушло и записано, её сбой ничего не отменяет.
                try:
                    await feed(session_for_feed, r.user_id, short_payload(r.lang, total, favorite))
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"digest: лента user_id={r.user_id} не записана: {exc}")
                    try:
                        await session_for_feed.rollback()
                    except Exception:  # noqa: BLE001
                        pass
        await sleep(pause)

    return summary


async def dry_run(
    session: AsyncSession,
    *,
    sender: Any,
    cfg: dict,
    settings: dict,
    sdk: Any,
    month: str,
    start: datetime,
    end: datetime,
    ledger: Optional[Ledger] = None,
    usage: Callable[..., Awaitable[Optional[tuple[int, Optional[str]]]]] = fetch_usage,
    limit: int = DRY_RUN_LIMIT,
    pause: float = USAGE_PAUSE,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> dict[str, Any]:
    """«Кому уйдёт»: первые `limit` адресатов с исходом, ничего не отправляя и не
    записывая. Панель только читается; блок-лист Brevo здесь не трогаем."""
    recipients = await select_email_audience(session)
    ledger = ledger or DbLedger(session)
    items: list[dict[str, Any]] = []
    would_send = 0
    for r in recipients[:limit]:
        if await ledger.status(r.user_id, month) is not None:
            items.append(
                {"user_id": r.user_id, "gb": None, "favorite": None, "outcome": "already_this_month"}
            )
            continue
        got = await usage(sdk, r.remna_uuid, start, end)
        if got is None:
            items.append({"user_id": r.user_id, "gb": None, "favorite": None, "outcome": USAGE_ERROR})
        elif got[0] <= 0:
            items.append({"user_id": r.user_id, "gb": 0, "favorite": None, "outcome": NO_TRAFFIC})
        else:
            would_send += 1
            items.append(
                {
                    "user_id": r.user_id,
                    "gb": round(got[0] / _GB, 1),
                    "favorite": got[1],
                    "outcome": "would_send",
                }
            )
        await sleep(pause)
    return {
        "audience": len(recipients),
        "examined": len(items),
        "truncated": len(recipients) > limit,
        "would_send": would_send,
        "blockers": email_blockers(sender, cfg, settings),
        "items": items,
    }


async def send_test(
    *,
    sender: Any,
    cfg: dict,
    settings: dict,
    secret: str,
    user_id: int,
    lang: Any,
    to: str,
) -> str:
    """Тестовое письмо с примерными цифрами — только на указанный адрес.

    Ссылка отписки подписана id админа: нажмёт её — отпишется он сам, а не
    случайный клиент. Возвращает адрес, с которого письмо ушло.
    """
    l = email_lang(lang)
    brand = brand_name(settings)
    subject, body, opts = build_email_parts(l, SAMPLE_TOTAL, SAMPLE_FAVORITE[l], brand, test=True)
    token = make_optout_token(user_id, secret)
    await sender.send_branded(
        to=to,
        subject=subject,
        body=body,
        brand=brand,
        from_email=str(cfg.get("email_from") or ""),
        list_unsubscribe_url=one_click_url(token),
        unsubscribe_url=unsubscribe_page_url(token),
        **opts,
    )
    return effective_from(settings, cfg)
