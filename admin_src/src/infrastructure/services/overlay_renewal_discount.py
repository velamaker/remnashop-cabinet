"""Скидка на продление ДО окончания подписки (overlay RемнаShop).

ЧТО ДЕЛАЕТ. За N дней до конца ПЛАТНОЙ подписки человеку выдаётся разовая скидка
на продление — через `users.purchase_discount`, как у win-back и скидки
триальщикам: база сама применяет её во всех каналах оплаты (бот, витрина
кабинета, оплата с баланса, autopay) и сама гасит после покупки
(`use_cases/subscription/commands/purchase.py`). Сообщение уходит в Telegram и
push; людям только с почтой — строкой в письме за 72 ч (email_expiry_reminders).
Выдачу делает крон taskiq/tasks/renewal_discount.py, здесь — правила, тексты,
SQL, погашение, предпросмотр и итоги: их зовут и крон, и админка.

ПОЧЕМУ СТОЛЬКО ПРАВИЛ ОТКАЗА (`decide`). Скидка до окончания срока бьёт прежде
всего по тем, кто и так заплатил бы: значимая доля повторных покупок делается до
конца срока. Поэтому по умолчанию выключено, и даже включённая скидка
не выдаётся тем, кто платит сам (autopay, ранние продления без скидки), тем, кто
не платил вовсе (подарок, промокод, импорт из панели), и тем, у кого уже открыто
другое предложение — два предложения на одном поле затёрли бы друг друга.

РАЗВОД С НАПОМИНАНИЯМИ. `days_before` не меньше `min_days_before()`: push
«подписка заканчивается» приходит за PUSH_EXPIRING_DAYS, письмо и базовые
Telegram-напоминания — за 3 дня. Наше сообщение приходит минимум на сутки раньше,
а те напоминания дописывают строку про уже действующую скидку.

ВРЕМЯ — ПАРАМЕТРОМ. Все сравнения со временем идут с `now`, переданным снаружи,
а не с `now()` базы: предпросмотр считает решение на момент будущей выдачи, а
проверка на копии базы сдвигает время за срок скидки и смотрит погашение.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from loguru import logger
from sqlalchemy import text

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
CONFIG_PATH = ASSETS_DIR / "renewal_discount.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,             # по умолчанию выключено
    "percent": 10,                # % скидки на продление
    "days_before": 5,             # за сколько дней до конца подписки выдавать
    "lifetime_hours": 120,        # срок жизни скидки (не дольше подписки)
    "cooldown_days": 90,          # не чаще раза в N дней; 0 — на каждый срок
    "skip_early_renewers": True,  # не выдавать тем, кто и так продлевает заранее
}

PERCENT_MIN, PERCENT_MAX = 1, 90
DAYS_BEFORE_MAX = 30
LIFETIME_MIN, LIFETIME_MAX = 24, 720
COOLDOWN_MAX = 365
HORIZON_MAX = 60

# Окно поимки: крон почасовой, а выдачу нельзя пропустить из-за одного упавшего
# прогона. 12 часов — полсуток запаса, при этом сообщение всё ещё приходит
# «примерно за N дней», а не за N−1.
CATCH_HOURS = 12
# Письмо «подписка скоро закончится» уходит за 72 ч — людям только с почтой
# скидку можно сообщить лишь им.
EMAIL_REMINDER_HOURS = 72
# Счёт моложе часа: человек прямо сейчас платит. Выдача скидки поверх оплаты
# либо обнулится этой же покупкой, либо запутает цену на экране оплаты.
PAYMENT_PENDING_HOURS = 1
# Допуск «тот же срок»: срок сдвигают вручную из админки и разморозкой, и без
# допуска сдвиг на день выглядел бы новым периодом — вторая скидка на тот же срок.
PERIOD_TOLERANCE_MAX_DAYS = 6
# Опоздавший платёж: счёт выставлен со скидкой до её сгорания, а оплачен после.
USED_LATE_DAYS = 3
CANDIDATES_LIMIT = 500
SAMPLE_LIMIT = 20

ACTIVE, USED, EXPIRED, REVOKED = "active", "used", "expired", "revoked"
TG_SENT, TG_BLOCKED, TG_FAILED, TG_NO_TELEGRAM = "sent", "blocked", "failed", "no_telegram"

REASON_LABELS: dict[str, str] = {
    "staff": "персонал",
    "blocked": "заблокирован",
    "trial": "пробный период",
    "outside_window": "не в окне дней",
    "reserve": "на резервном доступе",
    "frozen": "подписка заморожена",
    "not_paid": "ни разу не платил (подарок, промокод, импорт)",
    "gift_period": "текущий срок подарен",
    "autopay": "автопродление с баланса",
    "payment_in_progress": "оплата в процессе",
    "open_grant": "скидка уже действует",
    "already_this_period": "уже выдавали на этот срок",
    "cooldown": "выдавали недавно",
    "other_offer_open": "открыто другое предложение",
    "has_discount": "уже есть другая скидка",
    "personal_discount": "личная скидка не меньше",
    "early_renewer": "и так продлевает заранее",
    "unreachable": "некуда написать",
}


# ── Конфиг ────────────────────────────────────────────────────────────────────


def push_expiring_days() -> int:
    """PUSH_EXPIRING_DAYS тем же разбором, что в tasks/push_notify.py."""
    try:
        return max(1, int(os.environ.get("PUSH_EXPIRING_DAYS") or "3"))
    except ValueError:
        return 3


def min_days_before() -> int:
    """Раньше всех напоминаний минимум на сутки (см. док-строку модуля)."""
    return max(4, push_expiring_days() + 1)


def autopay_env_on() -> bool:
    """AUTOPAY_ENABLED тем же разбором, что в tasks/autopay.py (по умолчанию вкл)."""
    return (os.environ.get("AUTOPAY_ENABLED") or "true").strip().lower() == "true"


def email_env_on() -> bool:
    """Включена ли почта эффективно: админка (assets/email.json) поверх .env.

    Имя историческое — раньше здесь читали только переменную окружения, и у
    установок, где почту настроили в админке, письмо со скидкой не уходило.
    """
    try:
        from src.infrastructure.services.email_settings import email_enabled_now

        return email_enabled_now()
    except Exception:  # noqa: BLE001 — настройки не прочитались: решаем по .env
        return (os.environ.get("EMAIL_ENABLED") or "").strip().lower() == "true"


@dataclass(frozen=True)
class Env:
    """Переключатели окружения, от которых зависит решение. Тесты подставляют свои."""

    email_enabled: bool
    autopay_enabled: bool

    @classmethod
    def current(cls) -> "Env":
        return cls(email_enabled=email_env_on(), autopay_enabled=autopay_env_on())


def _clamp(value: Any, default: int, lo: int, hi: int) -> int:
    # Дефолт тоже зажимаем: нижняя граница дней зависит от окружения, и дефолтные
    # 5 дней при PUSH_EXPIRING_DAYS=5 совпали бы с push-напоминанием.
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


def _flag(value: Any, default: bool) -> bool:
    # Строка "false" из руками правленного файла — не «включено».
    return value if isinstance(value, bool) else default


def _normalize(data: Mapping[str, Any]) -> dict[str, Any]:
    lo = min_days_before()
    return {
        "enabled": _flag(data.get("enabled"), DEFAULT_CONFIG["enabled"]),
        "percent": _clamp(data.get("percent"), DEFAULT_CONFIG["percent"], PERCENT_MIN, PERCENT_MAX),
        "days_before": _clamp(
            data.get("days_before"), DEFAULT_CONFIG["days_before"], lo, max(lo, DAYS_BEFORE_MAX)
        ),
        "lifetime_hours": _clamp(
            data.get("lifetime_hours"), DEFAULT_CONFIG["lifetime_hours"], LIFETIME_MIN, LIFETIME_MAX
        ),
        "cooldown_days": _clamp(
            data.get("cooldown_days"), DEFAULT_CONFIG["cooldown_days"], 0, COOLDOWN_MAX
        ),
        "skip_early_renewers": _flag(
            data.get("skip_early_renewers"), DEFAULT_CONFIG["skip_early_renewers"]
        ),
    }


def load_config() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text("utf-8"))
    except FileNotFoundError:
        return _normalize({})
    except Exception as exc:  # noqa: BLE001 — битый конфиг не должен ронять крон
        logger.warning(f"renewal_discount: не удалось прочитать конфиг ({exc}) — беру дефолт")
        return _normalize({})
    if not isinstance(data, dict):
        logger.warning("renewal_discount: конфиг не объект — беру дефолт")
        return _normalize({})
    return _normalize(data)


def save_config(config: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _normalize(config)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), "utf-8")
    return normalized


def config_note(cfg: Mapping[str, Any]) -> Optional[str]:
    """Предупреждение про win-back, если он щедрее.

    Win-back выдаётся через несколько дней ПОСЛЕ окончания и больше нашей скидки —
    внимательный клиент мог бы дождаться его. Но только однажды: win-back даётся
    человеку один раз навсегда. Владельцу это надо видеть, решать ему.
    """
    try:
        from src.infrastructure.services.overlay_winback import load_config as winback_config

        wb = winback_config()
    except Exception:  # noqa: BLE001 — подсказка не повод ронять настройки
        return None
    if not wb.get("enabled") or int(wb.get("percent") or 0) <= int(cfg.get("percent") or 0):
        return None
    return (
        f"Win-back сейчас даёт {wb['percent']}% через {wb['days_after']} дн. после "
        "окончания — больше этой скидки. Win-back выдаётся человеку один раз, так что "
        "дождаться его можно лишь однажды."
    )


# ── Решение «выдавать ли» ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Candidate:
    user_id: int
    role: str
    is_blocked: bool
    is_bot_blocked: bool
    telegram_id: Optional[int]
    email: Optional[str]
    is_email_verified: bool
    lang: str
    purchase_discount: int
    personal_discount: int
    autopay_enabled: bool
    subscription_id: int
    expire_at: datetime
    is_trial: bool
    has_push: bool = False
    on_reserve: bool = False
    frozen: bool = False
    payment_in_progress: bool = False
    last_gift_at: Optional[datetime] = None
    last_granted_at: Optional[datetime] = None
    last_grant_sub_expire_at: Optional[datetime] = None
    open_grant_until: Optional[datetime] = None
    other_offer_until: Optional[datetime] = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Candidate":
        return cls(
            user_id=int(row["user_id"]),
            role=str(row["role"] or ""),
            is_blocked=bool(row["is_blocked"]),
            is_bot_blocked=bool(row["is_bot_blocked"]),
            telegram_id=row["telegram_id"],
            email=row["email"],
            is_email_verified=bool(row["is_email_verified"]),
            lang=str(row["lang"] or "ru"),
            purchase_discount=int(row["purchase_discount"] or 0),
            personal_discount=int(row["personal_discount"] or 0),
            autopay_enabled=bool(row["autopay_enabled"]),
            subscription_id=int(row["subscription_id"]),
            expire_at=row["expire_at"],
            is_trial=bool(row["is_trial"]),
            has_push=bool(row["has_push"]),
            on_reserve=bool(row["on_reserve"]),
            frozen=bool(row["frozen"]),
            payment_in_progress=bool(row["payment_in_progress"]),
            last_gift_at=row["last_gift_at"],
            last_granted_at=row["last_granted_at"],
            last_grant_sub_expire_at=row["last_grant_sub_expire_at"],
            open_grant_until=row["open_grant_until"],
            other_offer_until=row["other_offer_until"],
        )


@dataclass(frozen=True)
class PaidFacts:
    paid_count: int = 0
    last_paid_at: Optional[datetime] = None
    # Оплаты без скидки, сделанные до конца уже оплаченного срока.
    early_renewals: int = 0


def paid_facts(payments: Iterable[Mapping[str, Any]]) -> PaidFacts:
    """Что известно о реальных оплатах человека (строки PAYMENTS_SQL).

    Конец периода считается ЦЕПОЧКОЙ, а не «дата оплаты + срок»: второе раннее
    продление добавляет дни к концу, продлённому первым, и без цепочки третья
    оплата, сделанная до настоящего конца, выглядела бы оплатой после него.
    RENEW прибавляет к прежнему концу (как база: max(expire_at, now) + срок),
    NEW и CHANGE начинают срок заново. Бессрочные (duration ≤ 0) конца не имеют.
    Оплата со скидкой ранней не считается: её могла вызвать сама скидка.
    """
    count = 0
    last: Optional[datetime] = None
    early = 0
    end: Optional[datetime] = None
    for p in sorted(payments, key=lambda r: r["created_at"]):
        created = p["created_at"]
        count += 1
        last = created
        if end is not None and created < end and int(p.get("discount_percent") or 0) == 0:
            early += 1
        duration = int(p.get("duration_days") or 0)
        if duration <= 0:
            end = None
            continue
        if str(p.get("purchase_type") or "") == "RENEW" and end is not None:
            end = max(end, created) + timedelta(days=duration)
        else:
            end = created + timedelta(days=duration)
    return PaidFacts(paid_count=count, last_paid_at=last, early_renewals=early)


def grant_expires_at(now_: datetime, cfg: Mapping[str, Any], sub_expire_at: datetime) -> datetime:
    """Скидка живёт не дольше подписки: после окончания работает уже win-back."""
    return min(now_ + timedelta(hours=int(cfg["lifetime_hours"])), sub_expire_at)


def channels(
    c: Candidate, cfg: Mapping[str, Any], now_: datetime, env: Env
) -> dict[str, bool]:
    """Куда можно сообщить о скидке.

    Почта — только тем, у кого нет Telegram: письмо за 72 ч уходит лишь таким
    (email_expiry_reminders), а заблокировавшему бота письмо не придёт вовсе.
    И только если скидка доживёт до этого письма — иначе она сгорит молча. Если
    она сгорит между письмом и концом подписки, письмо называет её срок датой
    (`email_discount_line`), а не «пока подписка не закончилась».
    """
    email = (
        env.email_enabled
        and c.telegram_id is None
        and bool(c.email)
        and c.is_email_verified
        and grant_expires_at(now_, cfg, c.expire_at)
        > c.expire_at - timedelta(hours=EMAIL_REMINDER_HOURS)
    )
    return {
        "telegram": c.telegram_id is not None and not c.is_bot_blocked,
        "push": c.has_push,
        "email": bool(email),
    }


def decide(
    c: Candidate,
    facts: PaidFacts,
    cfg: Mapping[str, Any],
    now_: datetime,
    env: Env,
    *,
    live: bool = True,
) -> Optional[str]:
    """None — выдаём; иначе код причины отказа (первая сработавшая проверка).

    `now_` — момент выдачи: у крона это «сейчас», у предпросмотра — будущий момент,
    когда кандидат попадёт в окно. `live=False` для будущего: счёт «в процессе»
    виден только сейчас и через неделю ничего не значит.
    """
    days_before = int(cfg["days_before"])
    percent = int(cfg["percent"])
    target = now_ + timedelta(days=days_before)

    if c.role != "USER":
        return "staff"
    if c.is_blocked:
        return "blocked"
    if c.is_trial:
        return "trial"
    # Верхняя граница включительно: предпросмотр ставит момент выдачи ровно на
    # expire_at − N. У крона выборка уже строгая, для него разницы нет.
    if not (target - timedelta(hours=CATCH_HOURS) <= c.expire_at <= target):
        return "outside_window"
    if c.on_reserve:
        return "reserve"
    if c.frozen:
        return "frozen"
    if facts.paid_count == 0:
        return "not_paid"
    if c.last_gift_at is not None and (
        facts.last_paid_at is None or c.last_gift_at > facts.last_paid_at
    ):
        return "gift_period"
    if c.autopay_enabled and env.autopay_enabled:
        return "autopay"
    if live and c.payment_in_progress:
        return "payment_in_progress"
    if c.open_grant_until is not None and c.open_grant_until > now_:
        return "open_grant"
    if c.last_grant_sub_expire_at is not None and abs(
        c.last_grant_sub_expire_at - c.expire_at
    ) < timedelta(days=min(days_before, PERIOD_TOLERANCE_MAX_DAYS)):
        return "already_this_period"
    cooldown = int(cfg["cooldown_days"])
    if (
        cooldown > 0
        and c.last_granted_at is not None
        and now_ - c.last_granted_at < timedelta(days=cooldown)
    ):
        return "cooldown"
    # Их проход сгорания снимает скидку при равном проценте — нашу в том числе.
    if c.other_offer_until is not None and c.other_offer_until > now_:
        return "other_offer_open"
    if c.purchase_discount > 0:
        return "has_discount"
    if c.personal_discount >= percent:
        return "personal_discount"
    if cfg["skip_early_renewers"] and facts.early_renewals > 0:
        return "early_renewer"
    if not any(channels(c, cfg, now_, env).values()):
        return "unreachable"
    return None


# ── Тексты ────────────────────────────────────────────────────────────────────


def _plural_ru(n: int, one: str, few: str, many: str) -> str:
    n100 = abs(n) % 100
    n10 = n100 % 10
    if 11 <= n100 <= 14:
        return many
    if n10 == 1:
        return one
    if 2 <= n10 <= 4:
        return few
    return many


def plural_days_ru(n: int) -> str:
    """«1 день / 2 дня / 5 дней». Своя, а не из модуля задачи: сервис не тянет taskiq."""
    return f"{n} {_plural_ru(n, 'день', 'дня', 'дней')}"


def _hours_ru(n: int) -> str:
    return f"{n} {_plural_ru(n, 'час', 'часа', 'часов')}"


def _en(n: int, unit: str) -> str:
    return f"{n} {unit}" if n == 1 else f"{n} {unit}s"


def _message_lang(lang: Optional[str]) -> str:
    # Тексты Telegram и push — только ru/en, как у win-back; остальные читают по-русски.
    return "en" if str(lang or "").lower()[:2] == "en" else "ru"


def build_messages(
    percent: int,
    lang: Optional[str],
    *,
    now_: datetime,
    sub_expire_at: datetime,
    grant_expires_at: datetime,
) -> dict[str, str]:
    """Готовые строки для Telegram (HTML) и push — без плейсхолдеров.

    Строки собираются здесь целиком: `notify_user_push` прогоняет шаблон через
    `.format`, и любая фигурная скобка в тексте стала бы подстановкой.
    """
    # До конца подписки — округление к ближайшему: выдача идёт в окне
    # [N дн − 12 ч; N дн), и при округлении вниз «за 5 дней» читалось бы «через 4».
    left = sub_expire_at - now_
    sub_days = max(1, int((left.total_seconds() + 43200) // 86400))
    offer_left = grant_expires_at - now_
    offer_days = int(offer_left.total_seconds() // 86400)
    offer_hours = max(1, -(-int(offer_left.total_seconds()) // 3600))
    until_end = grant_expires_at >= sub_expire_at

    if _message_lang(lang) == "en":
        ends_in = _en(sub_days, "day")
        if until_end:
            offer = "until your subscription ends"
        elif offer_days >= 1:
            offer = f"for {_en(offer_days, 'more day')}"
        else:
            offer = f"for {_en(offer_hours, 'more hour')}"
        title = f"🎁 {percent}% off your renewal"
        telegram = (
            f"<b>{title}</b>\n\n"
            f"Your subscription ends in {ends_in}. Renew now and pay {percent}% less — "
            "the discount is applied automatically at checkout.\n\n"
            f"The offer is valid {offer}."
        )
        push_body = (
            f"Your subscription ends in {ends_in}. "
            "Renew now — the discount is applied automatically."
        )
    else:
        ends_in = plural_days_ru(sub_days)
        if until_end:
            offer = "до окончания подписки"
        elif offer_days >= 1:
            offer = f"ещё {plural_days_ru(offer_days)}"
        else:
            offer = f"ещё {_hours_ru(offer_hours)}"
        title = f"🎁 Скидка {percent}% на продление"
        telegram = (
            f"<b>{title}</b>\n\n"
            f"Подписка закончится через {ends_in}. Продлите сейчас — цена будет на "
            f"{percent}% ниже, скидка применится автоматически при оплате.\n\n"
            f"Предложение действует {offer}."
        )
        push_body = (
            f"Подписка закончится через {ends_in}. "
            "Продлите сейчас — скидка применится автоматически."
        )
    return {"telegram_html": telegram, "push_title": title, "push_body": push_body}


# Письмо читают не в момент отправки, поэтому срок в нём — дата, а не «ещё N часов».
# Часовой пояс контейнера UTC, а письма русские: даём московское время. Смещение
# фиксированное — перехода на летнее время в Москве нет, и tzdata в образе не нужна.
EMAIL_TZ = timezone(timedelta(hours=3))
EMAIL_TZ_LABEL = "по московскому времени"
_MONTHS_GEN_RU = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def _email_deadline_ru(moment: datetime) -> str:
    """«19 сентября, 14:00 по московскому времени». Минуты — вниз: не обещаем позже."""
    local = moment.astimezone(EMAIL_TZ)
    return f"{local.day} {_MONTHS_GEN_RU[local.month - 1]}, {local:%H:%M} {EMAIL_TZ_LABEL}"


def email_discount_line(
    percent: int,
    *,
    grant_expires_at: Optional[datetime],
    sub_expire_at: Optional[datetime],
) -> str:
    """Строка в письмо за 72 ч тем, у кого только почта.

    «Пока подписка не закончилась» — только если скидка правда живёт до конца
    подписки. При `lifetime_hours` короче `days_before` она сгорает раньше
    (`grant_expires_at`), и письмо, обещавшее её до конца, отправило бы человека
    платить полную цену в последний день. Тогда называем срок датой. Срок не
    известен — не обещаем никакого.
    """
    head = f"Для вас действует скидка {percent}% на продление — она применится автоматически при оплате"
    if grant_expires_at is None:
        return f"{head}."
    if sub_expire_at is not None and grant_expires_at >= sub_expire_at:
        return f"{head}, пока подписка не закончилась."
    return f"{head} до {_email_deadline_ru(grant_expires_at)}."


def push_discount_tail(lang: Optional[str], percent: int) -> str:
    """Хвост push «подписка заканчивается» (tasks/push_notify.py)."""
    if _message_lang(lang) == "en":
        return f" Your {percent}% renewal discount is active."
    return f" Для вас действует скидка {percent}% на продление."


def build_tg_payload(html: str) -> Any:
    """Сообщение в Telegram: кнопки «Продлить» и «Закрыть».

    `delete_after=None` ОБЯЗАТЕЛЬНО: дефолт DTO — 5 секунд, сообщение исчезло бы
    из чата (та же грабля, что у рассылок). «Закрыть» база добавляет сама только
    при `disable_default_markup=False` и `delete_after=None`.
    """
    from src.application.dto import MessagePayloadDto
    from src.telegram.keyboards import get_renew_keyboard

    return MessagePayloadDto(
        i18n_key="raw-message",
        i18n_kwargs={"content": html},
        reply_markup=get_renew_keyboard(),
        disable_default_markup=False,
        delete_after=None,
    )


# ── SQL ───────────────────────────────────────────────────────────────────────

# Грубый отбор: активная подписка в окне. Решение принимает `decide` — так каждое
# правило видно в предпросмотре с причиной, а не теряется в WHERE.
CANDIDATES_SQL = """
SELECT u.id AS user_id, u.role::text AS role, u.is_blocked, u.is_bot_blocked, u.telegram_id,
       u.email, u.is_email_verified, lower(u.language::text) AS lang,
       u.purchase_discount, u.personal_discount, u.autopay_enabled,
       s.id AS subscription_id, s.expire_at, s.is_trial,
       EXISTS (SELECT 1 FROM push_subscriptions ps WHERE ps.user_id = u.id) AS has_push,
       EXISTS (SELECT 1 FROM reserve_grants r WHERE r.user_id = u.id AND r.ended = false) AS on_reserve,
       EXISTS (SELECT 1 FROM subscription_freezes f WHERE f.user_id = u.id AND f.active) AS frozen,
       EXISTS (SELECT 1 FROM transactions tp WHERE tp.user_id = u.id
               AND tp.status::text = 'PENDING' AND tp.created_at > :pending_since) AS payment_in_progress,
       (SELECT max(pa.activated_at) FROM promocode_activations pa
          JOIN promocodes p ON p.id = pa.promocode_id
         WHERE pa.user_id = u.id AND p.reward_type::text = 'SUBSCRIPTION') AS last_gift_at,
       (SELECT max(g.granted_at) FROM renewal_discount_grants g WHERE g.user_id = u.id) AS last_granted_at,
       (SELECT g.sub_expire_at FROM renewal_discount_grants g WHERE g.user_id = u.id
         ORDER BY g.granted_at DESC LIMIT 1) AS last_grant_sub_expire_at,
       (SELECT max(g.expires_at) FROM renewal_discount_grants g
         WHERE g.user_id = u.id AND g.status = 'active') AS open_grant_until,
       (SELECT max(x.until) FROM (
          SELECT w.expires_at AS until FROM winback_grants w WHERE w.user_id = u.id AND w.used = false
          UNION ALL
          SELECT td.expires_at FROM trial_discounts td WHERE td.user_id = u.id AND td.used = false
        ) x) AS other_offer_until
FROM users u
JOIN subscriptions s ON s.id = u.current_subscription_id
WHERE s.status::text = 'ACTIVE' AND s.expire_at >= :lo AND s.expire_at < :hi
ORDER BY s.expire_at
LIMIT 500
"""

# Реальные оплаты подписки: не проверочные, не синтетические тарифы (id ≤ 0),
# не бесплатные и не купленные В ПОДАРОК кому-то другому.
PAYMENTS_SQL = """
SELECT t.user_id, t.created_at, t.purchase_type::text AS purchase_type,
       (t.plan_snapshot->>'duration')::int AS duration_days,
       coalesce((t.pricing->>'discount_percent')::int, 0) AS discount_percent
FROM transactions t
WHERE t.user_id = ANY(:ids) AND t.status::text = 'COMPLETED' AND t.is_test = false
  AND (t.plan_snapshot->>'id')::int > 0 AND (t.pricing->>'final_amount')::numeric > 0
  AND NOT EXISTS (SELECT 1 FROM gift_payments gp WHERE gp.payment_id = t.payment_id)
ORDER BY t.user_id, t.created_at
"""

INSERT_GRANT_SQL = """
INSERT INTO renewal_discount_grants
    (user_id, subscription_id, sub_expire_at, percent, granted_at, expires_at)
VALUES (:u, :sub, :sub_expire_at, :p, :now, :expires_at)
ON CONFLICT DO NOTHING
RETURNING id
"""

# Условия повторяют `decide` ещё раз, уже под записью: между отбором и выдачей
# человек мог получить другую скидку или включить autopay.
APPLY_DISCOUNT_SQL = """
UPDATE users SET purchase_discount = :p
WHERE id = :u AND purchase_discount = 0 AND personal_discount < :p AND is_blocked = false
  AND (autopay_enabled = false OR NOT CAST(:autopay_guard AS boolean))
RETURNING id
"""

CLAIM_SQL = """
UPDATE renewal_discount_grants SET notified_at = :now
WHERE id = :id AND notified_at IS NULL
RETURNING id
"""

NOTIFY_RESULT_SQL = """
UPDATE renewal_discount_grants SET tg_status = :tg, push_sent = :push, notify_error = :err
WHERE id = :id
"""

# Досылка: выдача есть, а сообщение не занято — прогон упал между ними.
PENDING_NOTIFY_SQL = """
SELECT g.id, g.user_id, g.percent, g.sub_expire_at, g.expires_at,
       lower(u.language::text) AS lang, u.telegram_id, u.is_bot_blocked
FROM renewal_discount_grants g
JOIN users u ON u.id = g.user_id
WHERE g.status = 'active' AND g.notified_at IS NULL
  AND g.granted_at < :stale_before AND g.expires_at > :now
ORDER BY g.granted_at
LIMIT 50
"""

USAGE_GRANTS_SQL = """
SELECT g.id, g.user_id, g.percent, g.granted_at, g.expires_at, g.status
FROM renewal_discount_grants g
WHERE g.status = 'active' OR (g.status = 'expired' AND g.closed_at > :late_since)
"""

USAGE_PAYMENTS_SQL = """
SELECT t.id, t.user_id, t.created_at,
       coalesce((t.pricing->>'discount_percent')::int, 0) AS discount_percent
FROM transactions t
WHERE t.user_id = ANY(:ids) AND t.status::text = 'COMPLETED' AND t.is_test = false
  AND (t.plan_snapshot->>'id')::int > 0 AND t.created_at >= :since
ORDER BY t.created_at
"""

MARK_USED_SQL = """
UPDATE renewal_discount_grants SET status = 'used', transaction_id = :tx, closed_at = :now
WHERE id = :id AND status = :was
RETURNING id
"""

DUE_EXPIRE_SQL = """
SELECT id, user_id, percent FROM renewal_discount_grants
WHERE status = 'active' AND expires_at <= :now
"""

ACTIVE_GRANTS_SQL = """
SELECT id, user_id, percent FROM renewal_discount_grants WHERE status = 'active'
"""

MARK_CLOSED_SQL = """
UPDATE renewal_discount_grants SET status = :to, closed_at = :now
WHERE id = :id AND status = 'active'
RETURNING id
"""

# Снимаем скидку, только если она всё ещё НАША: пришедший после неё win-back с
# другим процентом или скидку, выставленную админом, не трогаем.
CLEAR_DISCOUNT_SQL = """
UPDATE users SET purchase_discount = 0 WHERE id = :u AND purchase_discount = :p
"""

# Открытая выдача у человека одна (ux_renewal_discount_open), агрегаты — на всякий
# случай: процент наибольший, срок САМЫЙ РАННИЙ — письмо не обещает дольше, чем есть.
ACTIVE_BY_USER_SQL = """
SELECT g.user_id, max(g.percent) AS percent, min(g.expires_at) AS expires_at
FROM renewal_discount_grants g
JOIN users u ON u.id = g.user_id
WHERE g.user_id = ANY(:ids) AND g.status = 'active' AND g.expires_at > :now
  AND u.purchase_discount >= g.percent
GROUP BY g.user_id
"""

# Итоги. Деньги — только клиентов: оплаты персонала и проверочные платежи шлюзов
# в соединение не попадают (то же правило, что у статистики, admin/statistics.py),
# и `summarize` проверяет это ещё раз.
STATS_SQL = """
SELECT g.id, g.user_id, g.percent, g.granted_at, g.expires_at, g.status, g.tg_status,
       g.push_sent, u.role::text AS user_role,
       t.is_test, t.currency::text AS currency,
       (t.pricing->>'final_amount')::numeric AS final_amount,
       (t.pricing->>'original_amount')::numeric AS original_amount
FROM renewal_discount_grants g
JOIN users u ON u.id = g.user_id
LEFT JOIN transactions t ON t.id = g.transaction_id
  AND t.is_test = false
  AND t.user_id NOT IN (SELECT su.id FROM users su WHERE su.role::text <> 'USER')
WHERE g.granted_at >= :since
ORDER BY g.granted_at DESC
"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def _mappings(session: Any, sql: str, params: Optional[dict] = None) -> list[Mapping[str, Any]]:
    result = await session.execute(text(sql), params or {})
    return list(result.mappings().all())


def match_usage(
    grant: Mapping[str, Any], payments: Iterable[Mapping[str, Any]]
) -> Optional[int]:
    """Первая оплата, в которой скидка сработала: в сроке выдачи и не меньше её."""
    for p in payments:
        if (
            grant["granted_at"] <= p["created_at"] <= grant["expires_at"]
            and int(p.get("discount_percent") or 0) >= int(grant["percent"])
        ):
            return int(p["id"])
    return None


async def close_pass(session: Any, now: datetime) -> tuple[int, int]:
    """Погашение: воспользовались → used, срок вышел → снять скидку и expired.

    Идёт на КАЖДОМ прогоне, даже при выключенной фиче: выключение не должно
    оставлять выданные скидки жить вечно.
    """
    used = 0
    grants = await _mappings(
        session, USAGE_GRANTS_SQL, {"late_since": now - timedelta(days=USED_LATE_DAYS)}
    )
    if grants:
        ids = sorted({int(g["user_id"]) for g in grants})
        since = min(g["granted_at"] for g in grants)
        pays = await _mappings(session, USAGE_PAYMENTS_SQL, {"ids": ids, "since": since})
        by_user: dict[int, list[Mapping[str, Any]]] = {}
        for p in pays:
            by_user.setdefault(int(p["user_id"]), []).append(p)
        for g in grants:
            tx = match_usage(g, by_user.get(int(g["user_id"]), []))
            if tx is None:
                continue
            # users не трогаем: скидку погасила сама покупка, а у сгоревшей
            # выдачи её уже сняли — опоздавший платёж лишь попадает в итоги.
            res = await session.execute(
                text(MARK_USED_SQL),
                {"tx": tx, "now": now, "id": g["id"], "was": g["status"]},
            )
            if res.first() is not None:
                used += 1
        await session.commit()

    expired = 0
    due = await _mappings(session, DUE_EXPIRE_SQL, {"now": now})
    for g in due:
        res = await session.execute(
            text(MARK_CLOSED_SQL), {"to": EXPIRED, "now": now, "id": g["id"]}
        )
        if res.first() is None:
            continue
        await session.execute(
            text(CLEAR_DISCOUNT_SQL), {"u": g["user_id"], "p": g["percent"]}
        )
        expired += 1
    if due:
        await session.commit()
    return used, expired


async def revoke_active(session: Any, now: Optional[datetime] = None) -> int:
    """Отозвать все открытые выдачи. Людям ничего не пишем. Коммит — у вызывающего."""
    now = now or _utc_now()
    revoked = 0
    for g in await _mappings(session, ACTIVE_GRANTS_SQL):
        res = await session.execute(
            text(MARK_CLOSED_SQL), {"to": REVOKED, "now": now, "id": g["id"]}
        )
        if res.first() is None:
            continue
        await session.execute(
            text(CLEAR_DISCOUNT_SQL), {"u": g["user_id"], "p": g["percent"]}
        )
        revoked += 1
    return revoked


@dataclass(frozen=True)
class ActiveGrant:
    """Действующая скидка человека: процент и когда она сгорит."""

    percent: int
    expires_at: Optional[datetime]


async def active_grant_offers_by_user(
    session: Any, user_ids: Iterable[int], now: Optional[datetime] = None
) -> dict[int, ActiveGrant]:
    """{user_id: ActiveGrant} действующих скидок. Best-effort: письма и push важнее строки.

    Воркер может стартовать раньше миграции — таблицы ещё нет. Тогда откатываем
    транзакцию: иначе упавший запрос оставил бы сессию в aborted, и напоминание,
    ради которого нас позвали, не ушло бы вовсе.
    """
    ids = sorted({int(u) for u in user_ids})
    if not ids:
        return {}
    try:
        rows = await _mappings(session, ACTIVE_BY_USER_SQL, {"ids": ids, "now": now or _utc_now()})
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"renewal_discount: действующие скидки не прочитаны ({exc})")
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {}
    return {
        int(r["user_id"]): ActiveGrant(percent=int(r["percent"]), expires_at=r.get("expires_at"))
        for r in rows
    }


async def active_grants_by_user(
    session: Any, user_ids: Iterable[int], now: Optional[datetime] = None
) -> dict[int, int]:
    """{user_id: percent} — для push, где срок не называется (см. push_discount_tail)."""
    offers = await active_grant_offers_by_user(session, user_ids, now)
    return {uid: g.percent for uid, g in offers.items()}


# ── Предпросмотр ─────────────────────────────────────────────────────────────


async def load_candidates(
    session: Any, cfg: Mapping[str, Any], now: datetime, horizon_days: int = 0
) -> tuple[list[Candidate], dict[int, PaidFacts]]:
    target = now + timedelta(days=int(cfg["days_before"]))
    rows = await _mappings(
        session,
        CANDIDATES_SQL,
        {
            "lo": target - timedelta(hours=CATCH_HOURS),
            "hi": target + timedelta(days=horizon_days),
            "pending_since": now - timedelta(hours=PAYMENT_PENDING_HOURS),
        },
    )
    candidates = [Candidate.from_row(r) for r in rows]
    facts: dict[int, PaidFacts] = {}
    if candidates:
        pays = await _mappings(
            session, PAYMENTS_SQL, {"ids": sorted({c.user_id for c in candidates})}
        )
        by_user: dict[int, list[Mapping[str, Any]]] = {}
        for p in pays:
            by_user.setdefault(int(p["user_id"]), []).append(p)
        facts = {c.user_id: paid_facts(by_user.get(c.user_id, [])) for c in candidates}
    return candidates, facts


async def preview(
    session: Any,
    cfg: Mapping[str, Any],
    now: datetime,
    horizon_days: int,
    env: Env,
    *,
    hide_ids: bool = False,
) -> dict[str, Any]:
    """Кому выдалась бы скидка в ближайшие `horizon_days`. Ничего не пишет.

    Каждого кандидата судим на момент, когда он попадёт в окно:
    sim_now = max(now, expire_at − N дн). Cooldown и открытые предложения тем
    самым сравниваются с будущим моментом выдачи, а не с сегодняшним днём.
    """
    horizon = max(0, min(HORIZON_MAX, int(horizon_days)))
    days_before = int(cfg["days_before"])
    candidates, facts = await load_candidates(session, cfg, now, horizon)

    skipped: Counter = Counter()
    would = 0
    sample: list[dict[str, Any]] = []
    for c in candidates:
        sim_now = max(now, c.expire_at - timedelta(days=days_before))
        reason = decide(
            c, facts.get(c.user_id, PaidFacts()), cfg, sim_now, env, live=sim_now <= now
        )
        if reason is None:
            would += 1
        else:
            skipped[reason] += 1
        if len(sample) < SAMPLE_LIMIT:
            sample.append(
                {
                    "user_id": None if hide_ids else c.user_id,
                    "expire_at": c.expire_at.isoformat(),
                    "grant_at": sim_now.isoformat(),
                    "channels": channels(c, cfg, sim_now, env),
                    "would_grant": reason is None,
                    "reason": reason,
                }
            )

    target = now + timedelta(days=days_before)
    return {
        "window_from": (target - timedelta(hours=CATCH_HOURS)).isoformat(),
        "window_to": (target + timedelta(days=horizon)).isoformat(),
        "horizon_days": horizon,
        "examined": len(candidates),
        "would_grant": would,
        "truncated": len(candidates) >= CANDIDATES_LIMIT,
        "skipped": dict(skipped),
        "reason_labels": dict(REASON_LABELS),
        "sample": sample,
        "message": _example_message(cfg, now, target),
    }


def _example_message(cfg: Mapping[str, Any], now: datetime, sub_expire_at: datetime) -> dict[str, str]:
    msg = build_messages(
        int(cfg["percent"]),
        "ru",
        now_=now,
        sub_expire_at=sub_expire_at,
        grant_expires_at=grant_expires_at(now, cfg, sub_expire_at),
    )
    return {
        "telegram_html": msg["telegram_html"],
        "push_title": msg["push_title"],
        "push_body": msg["push_body"],
    }


# ── Пример себе ──────────────────────────────────────────────────────────────


async def send_example(
    admin: Any,
    cfg: Mapping[str, Any],
    *,
    notify_user: Any,
    send_push: Any,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Прислать админу то же сообщение, что получит клиент. Скидку НЕ выдаёт.

    Шлём копию админа с ролью USER: для админских ролей overlay-уведомления
    рисуют rich-таблицу и зеркалят сообщение в админские push — пример выглядел
    бы не так, как у клиента, и засорил бы центр уведомлений.
    """
    import dataclasses

    from src.core.enums import Role

    now = now or _utc_now()
    sub_expire_at = now + timedelta(days=int(cfg["days_before"]))
    msg = build_messages(
        int(cfg["percent"]),
        # Locale — StrEnum: str() даёт код языка, _message_lang сведёт к ru/en.
        str(getattr(admin, "language", "") or ""),
        now_=now,
        sub_expire_at=sub_expire_at,
        grant_expires_at=grant_expires_at(now, cfg, sub_expire_at),
    )
    as_client = dataclasses.replace(admin, role=Role.USER)

    if getattr(as_client, "telegram_id", None) is None:
        telegram = TG_NO_TELEGRAM
    else:
        try:
            sent = await notify_user(as_client, build_tg_payload(msg["telegram_html"]))
            telegram = TG_SENT if sent else TG_BLOCKED
        except Exception as exc:  # noqa: BLE001 — пример не должен ронять админку
            logger.warning(f"renewal_discount: пример в Telegram не отправлен: {exc}")
            telegram = TG_FAILED

    push = 0
    try:
        push = int(await send_push(as_client, {"ru": (msg["push_title"], msg["push_body"])}) or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"renewal_discount: пример в push не отправлен: {exc}")
    return {"telegram": telegram, "push": push}


# ── Итоги ────────────────────────────────────────────────────────────────────


def _money(value: Any) -> Decimal:
    try:
        return Decimal(str(value)) if value is not None else Decimal(0)
    except Exception:  # noqa: BLE001
        return Decimal(0)


def summarize(
    rows: Iterable[Mapping[str, Any]], *, period_days: int, hide_ids: bool = False
) -> dict[str, Any]:
    """Итоги по строкам STATS_SQL.

    Фильтр «только клиенты» повторён здесь, а не только в SQL: смени кто-нибудь
    соединение — деньги владельца снова попали бы в «оплачено со скидкой», и никто
    бы этого не заметил. Выдача человеку, ставшему персоналом, из итогов выпадает
    целиком. «Воспользовались» — только если оплата прошла этот же фильтр.
    """
    counts: Counter = Counter()
    paid = Decimal(0)
    given = Decimal(0)
    recent: list[dict[str, Any]] = []
    for r in rows:
        if str(r.get("user_role") or "") != "USER":
            continue
        status = str(r.get("status") or "")
        counts["granted"] += 1
        tx_ok = r.get("is_test") is False
        if status == USED:
            if tx_ok:
                counts["used"] += 1
                if str(r.get("currency") or "") == "RUB":
                    final = _money(r.get("final_amount"))
                    original = _money(r.get("original_amount"))
                    paid += final
                    given += max(Decimal(0), original - final)
        elif status in (ACTIVE, EXPIRED, REVOKED):
            counts[status] += 1
        if r.get("tg_status") in (TG_FAILED, TG_BLOCKED):
            counts["tg_failed"] += 1
        if int(r.get("push_sent") or 0) > 0:
            counts["push_delivered"] += 1
        if len(recent) < SAMPLE_LIMIT:
            recent.append(
                {
                    "user_id": None if hide_ids else r.get("user_id"),
                    "percent": r.get("percent"),
                    "granted_at": _iso(r.get("granted_at")),
                    "expires_at": _iso(r.get("expires_at")),
                    "status": status,
                    "tg_status": r.get("tg_status"),
                }
            )
    return {
        "period_days": period_days,
        "granted": counts["granted"],
        "used": counts["used"],
        "expired": counts[EXPIRED],
        "active": counts[ACTIVE],
        "revoked": counts[REVOKED],
        "paid_rub": float(paid),
        "discount_given_rub": float(given),
        "tg_failed": counts["tg_failed"],
        "push_delivered": counts["push_delivered"],
        "recent": recent,
    }


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if isinstance(value, datetime) else value


async def stats(
    session: Any, days: int, *, now: Optional[datetime] = None, hide_ids: bool = False
) -> dict[str, Any]:
    now = now or _utc_now()
    rows = await _mappings(session, STATS_SQL, {"since": now - timedelta(days=days)})
    return summarize(rows, period_days=days, hide_ids=hide_ids)
