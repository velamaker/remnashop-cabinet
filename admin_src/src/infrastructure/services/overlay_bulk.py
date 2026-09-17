"""Массовые действия по фильтру «Пользователей»: «Добавить N дней» и «Написать» (overlay).

ЗАЧЕМ. Компенсация за простой — это «+3 дня всем, у кого есть подписка» и
сообщение им же. Раньше это делалось вручную по одному человеку. Обе операции
идут фоновой задачей (taskiq/tasks/bulk_jobs.py) с журналом по людям
(миграция 0009): на человека уходят GET и PATCH панели, сотни людей в HTTP-таймаут
не влезают, а журнал позволяет продолжить после падения и объяснить пропуски.

ЧТО ЗДЕСЬ. Всё, что не зависит от воркера и веб-процесса:
  * правила «кому добавлять» (`classify_days`) и «как доставить» (`deliver_message`);
  * сверка с панелью перед записью (`panel_mismatch`, `protected_user`) и решение
    при возобновлении (`resolve_recovery`);
  * SQL и хранилище задач (`BulkStore`) — процессоры и ручки работают только через
    него, тесты подставляют своё хранилище в памяти, а инварианты самого SQL
    проверяет scripts/migration-e2e.sh на настоящем Postgres.

ГЛАВНОЕ ПРАВИЛО — ДНИ НЕ ВЫДАЮТСЯ ДВАЖДЫ И ЧУЖИЕ ДАННЫЕ ПАНЕЛИ НЕ ЗАТИРАЮТСЯ.
`remnawave.update_user` шлёт в панель ПОЛНОЕ состояние из нашей копии. Где наша
копия разошлась с панелью (оплата уже дошла до панели, сквад поменяли руками, у нас
нет почты), полный PATCH откатил бы панель к нашей устаревшей версии. Поэтому перед
каждой записью — GET панели и сравнение; разошлось — человека пропускаем с причиной,
а не «чиним» молча.
"""

from __future__ import annotations

import dataclasses
import hashlib
import html as html_lib
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Iterable, Literal, Mapping, Optional, Sequence
from uuid import UUID

from aiogram.exceptions import TelegramBadRequest
from loguru import logger
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.dto import MessagePayloadDto, RemnaSubscriptionDto
from src.core.constants import BATCH_DELAY
from src.core.enums import Role
from src.infrastructure.services.overlay_extend import compute_new_expire

# ── Пределы ───────────────────────────────────────────────────────────────────

MAX_DAYS = 365
# Сверх 5000 — отказ, а не молчаливая обрезка: «Массово по фильтру» с капом 2000
# тихо обрабатывал первые 2000, и админ не знал, что остальные ничего не получили.
MAX_USERS = 5000
TEXT_MAX = 4000
# Пауза между людьми: каждый PATCH панели рождает вебхук `user.modified` обратно к
# боту — сотня PATCH подряд без паузы нагружает и панель, и бота разом.
PANEL_PAUSE_SEC = 0.3
PANEL_FAIL_STREAK = 10
TG_PAUSE_SEC = 0.05
TG_BAD_STREAK = 3
TG_RETRY_MAX = 3
LEASE_SEC = 180
REPEAT_WINDOW_H = 24
EXPIRE_TOL = timedelta(seconds=2)
RESERVE_TOL = timedelta(hours=1)
RECENT_TX_MIN = 10
PENDING_TX_MIN = 30
RECENT_SUB_MIN = 2
CHANNELS = ("telegram", "cabinet", "email")
SAMPLE_SIZE = 5
TEXT_PREVIEW = 120

# Бессрочная подписка у базы — срок в 2099 году (core/constants UNLIMITED_EXPIRE_YEAR).
UNLIMITED_FROM = datetime(2099, 1, 1, tzinfo=timezone.utc)

KIND_DAYS = "days"
KIND_MESSAGE = "message"
ACTIVE_STATUSES = ("QUEUED", "PROCESSING", "PAUSED", "CANCELING")
FINISHED_STATUSES = ("COMPLETED", "CANCELED")


class Category(str, Enum):
    APPLY = "APPLY"
    APPLY_FROZEN = "APPLY_FROZEN"
    RECENT_CHANGE = "RECENT_CHANGE"
    NO_SUBSCRIPTION = "NO_SUBSCRIPTION"
    EXPIRED = "EXPIRED"
    RESERVE = "RESERVE"
    DISABLED = "DISABLED"
    UNLIMITED = "UNLIMITED"
    TRIAL = "TRIAL"
    LIMITED = "LIMITED"
    BLOCKED = "BLOCKED"
    STAFF = "STAFF"
    CANCELED = "CANCELED"
    MISMATCH = "MISMATCH"
    MANUAL = "MANUAL"
    NOT_IN_PANEL = "NOT_IN_PANEL"
    UNKNOWN = "UNKNOWN"
    NO_CHANNEL = "NO_CHANNEL"
    CABINET_ONLY = "CABINET_ONLY"


# Кому дни не добавляются вовсе — порядок совпадает с блоком «Не получат» в кабинете.
DAYS_SKIP = (
    Category.NO_SUBSCRIPTION,
    Category.EXPIRED,
    Category.RESERVE,
    Category.DISABLED,
    Category.UNLIMITED,
    Category.TRIAL,
    Category.LIMITED,
    Category.BLOCKED,
    Category.STAFF,
)
DAYS_ELIGIBLE = (Category.APPLY, Category.APPLY_FROZEN, Category.RECENT_CHANGE)

REASON_RU: dict[Category, str] = {
    Category.APPLY: "дни добавлены",
    Category.APPLY_FROZEN: "дни добавлены к остатку паузы",
    Category.NO_SUBSCRIPTION: "нет подписки",
    Category.EXPIRED: "подписка истекла",
    Category.RESERVE: "на резервном доступе",
    Category.DISABLED: "подписка отключена",
    Category.UNLIMITED: "бессрочная подписка",
    Category.TRIAL: "пробная подписка",
    Category.LIMITED: "исчерпан трафик",
    Category.BLOCKED: "заблокирован",
    Category.STAFF: "персонал",
    Category.RECENT_CHANGE: "недавно платил или менял подписку — добавьте вручную позже",
    Category.MISMATCH: (
        "данные в панели расходятся ({fields}) — нажмите «Синхронизировать» в карточке "
        "и добавьте вручную"
    ),
    Category.NOT_IN_PANEL: "нет в панели VPN",
    Category.MANUAL: "срок изменился во время сбоя — проверьте вручную",
    Category.CANCELED: "задача остановлена",
    Category.UNKNOWN: "сбой во время отправки — доставка неизвестна",
    Category.NO_CHANNEL: "нет доступного канала",
    Category.CABINET_ONLY: "только лента кабинета",
}
VERIFY_NOTE = "после добавления срок в панели не совпал — проверьте вручную"
VERIFY_UNAVAILABLE = "не удалось сверить с панелью VPN — проверьте вручную"
VERIFY_FREEZE = "пауза снята во время добавления — проверьте срок"

_FIELD_RU = {
    "status": "статус",
    "traffic_limit": "лимит трафика",
    "device_limit": "лимит устройств",
    "traffic_limit_strategy": "сброс трафика",
    "tag": "тег",
    "internal_squads": "сквады",
    "external_squad": "внешний сквад",
    "expire_at": "срок",
    "email": "почта",
    "telegram_id": "Telegram",
}

# Заголовок ленты, push и тема письма. Интерфейса клиента тут нет — только эти
# строки, поэтому словарь серверный, как `_MSG` у win-back; неизвестный язык → ru.
_MESSAGE_TITLE = {"ru": "Сообщение от {brand}", "en": "Message from {brand}"}

READONLY_DENIED = "Массовые действия доступны только администратору с полным доступом"


class ActiveJobExists(Exception):
    def __init__(self, job_id: Optional[int]) -> None:
        super().__init__(f"active job {job_id}")
        self.job_id = job_id


class DuplicateRequest(Exception):
    def __init__(self, job_id: Optional[int]) -> None:
        super().__init__(f"duplicate request {job_id}")
        self.job_id = job_id


class TelegramRejected(Exception):
    """Telegram не принял текст («Проверить на себе»): битая разметка и т.п."""


# ── Проверки ввода и прав ─────────────────────────────────────────────────────


def validate_days(days: Any) -> int:
    try:
        value = int(days)
    except (TypeError, ValueError):
        raise ValueError(f"Дней должно быть от 1 до {MAX_DAYS}") from None
    if not 1 <= value <= MAX_DAYS:
        raise ValueError(f"Дней должно быть от 1 до {MAX_DAYS}")
    return value


def validate_text(value: Any) -> str:
    content = str(value or "").strip()
    if not content:
        raise ValueError("Текст сообщения пуст")
    if len(content) > TEXT_MAX:
        raise ValueError(f"Текст длиннее {TEXT_MAX} символов")
    return content


def validate_channels(channels: Any) -> list[str]:
    picked = [c for c in dict.fromkeys(channels or []) if c in CHANNELS]
    if not picked:
        raise ValueError("Не выбран ни один канал")
    return picked


def require_full_access(access: Optional[Mapping[str, Any]]) -> Optional[str]:
    """None — можно запускать, иначе текст отказа.

    Мало просто права на запись в раздел «Пользователи»: массовая раздача дней и
    сообщение сотням людей — другой класс власти. Модератор с грантом на
    «Пользователей» блокирует одного, но не раздаёт дни всем. Read-only отсекает
    ещё общая проверка в _common (can_write=false → 403 на любой не-GET).
    """
    if not access or not access.get("full_access") or not access.get("can_write"):
        return READONLY_DENIED
    return None


# ── Хэши и текст ──────────────────────────────────────────────────────────────


def segment_hash(ids: Iterable[int]) -> str:
    """Отпечаток выборки: админ подтверждает ровно тех, кого видел в предпросмотре."""
    joined = ",".join(str(i) for i in sorted(set(int(x) for x in ids)))
    return hashlib.sha256(joined.encode()).hexdigest()


def params_hash(body: Mapping[str, Any]) -> str:
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


_TAG = re.compile(r"<[^>]+>")


def plain_text(value: str) -> str:
    """Текст без разметки Telegram — для ленты кабинета, push и письма."""
    return html_lib.unescape(_TAG.sub("", value or "")).strip()


def replay_decision(existing_hash: Optional[str], new_hash: str) -> Optional[str]:
    """Повтор запроса с тем же request_id: None — новый запуск, duplicate или conflict.

    Тот же request_id с теми же параметрами — это повтор после обрыва сети или
    двойной клик: отдаём уже созданную задачу. С другими параметрами — кабинет
    переиспользовал идентификатор, и молча запускать «не то» нельзя.
    """
    if existing_hash is None:
        return None
    return "duplicate" if existing_hash == new_hash else "conflict"


def message_title(lang: Any, brand: str) -> str:
    code = str(lang or "ru").lower()[:2]
    return (_MESSAGE_TITLE.get(code) or _MESSAGE_TITLE["ru"]).format(brand=brand)


def brand_name() -> str:
    # Сначала имя из «Оформления» (branding.json), потом авто-резолв: resolve_brand_name
    # сам branding.json не читает, и в заголовке стояло бы имя бота, а не сервиса
    # (та же грабля уже чинилась в подписи админских уведомлений).
    try:
        from src.web.endpoints.public.appearance import load_branding, resolve_brand_name

        saved = str((load_branding() or {}).get("brand_name") or "").strip()
        return saved or resolve_brand_name() or "VPN"
    except Exception:  # noqa: BLE001 — заголовок не должен ронять рассылку
        return "VPN"


def build_payload(content: str) -> MessagePayloadDto:
    # delete_after=None обязателен: по умолчанию у payload 5 секунд, и рассылка
    # самоудалялась у людей раньше, чем они успевали её прочитать.
    return MessagePayloadDto(
        i18n_key="raw-message",
        i18n_kwargs={"content": content},
        delete_after=None,
    )


def reason_text(category: Optional[str], error: Optional[str], verify_note: Optional[str] = None) -> Optional[str]:
    if verify_note:
        return verify_note
    if not category:
        return None
    try:
        cat = Category(category)
    except ValueError:
        return category
    template = REASON_RU.get(cat)
    if template is None:
        return category
    if cat == Category.MISMATCH:
        return template.format(fields=error or "поля")
    return template


def fields_ru(fields: Sequence[str]) -> str:
    return ", ".join(_FIELD_RU.get(f, f) for f in fields)


def error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:500]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def same_moment(a: Optional[datetime], b: Optional[datetime], tol: timedelta = EXPIRE_TOL) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(as_aware(a) - as_aware(b)) <= tol


def _value(v: Any) -> Any:
    return getattr(v, "value", v)


# ── Кому добавлять дни ────────────────────────────────────────────────────────


def classify_days(
    row: Mapping[str, Any],
    *,
    now: datetime,
    include_trial: bool,
    include_limited: bool,
) -> Category:
    """Категория человека для «+N дней». Первое совпадение выигрывает.

    Почему такие правила:
      * истёкшим «+N от сегодня» — это бесплатный доступ, а не компенсация;
      * резерв продлить нельзя: продлится сам резерв (1 ГБ, сквад-резерв). Резерв
        узнаём по сроку, а не только по открытой выдаче: оплативший во время
        резерва уже на настоящем сроке, и ему дни положены;
      * пауза — отдельный путь: `update_user` снял бы паузу (status=ACTIVE), а
        добавленные дни пропали бы при возобновлении, поэтому дни идут в остаток;
      * LIMITED по умолчанию пропускаем: PATCH со status=ACTIVE вернёт такого
        человека в LIMITED, и панель ещё раз пришлёт ему «трафик исчерпан»;
      * недавние оплаты и правки откладываем в конец прохода: база коммитит
        COMPLETED раньше, чем продлевает, а автоплатёж и подарки меняют срок без
        всякой PENDING-транзакции.
    """
    if str(row.get("role") or "") != "USER":
        return Category.STAFF
    if row.get("is_blocked"):
        return Category.BLOCKED
    expire_at = as_aware(row.get("expire_at"))
    if (
        row.get("sub_id") is None
        or str(row.get("sub_status") or "") == "DELETED"
        or row.get("user_remna_id") is None
        or expire_at is None
    ):
        return Category.NO_SUBSCRIPTION
    is_trial = bool(row.get("is_trial"))
    if row.get("frozen_remaining") is not None:
        if expire_at >= UNLIMITED_FROM:
            return Category.UNLIMITED
        if is_trial and not include_trial:
            return Category.TRIAL
        return Category.APPLY_FROZEN
    reserve_expire_at = as_aware(row.get("reserve_expire_at"))
    if reserve_expire_at is not None and expire_at <= reserve_expire_at + RESERVE_TOL:
        return Category.RESERVE
    status = str(row.get("sub_status") or "")
    if status == "DISABLED":
        return Category.DISABLED
    if expire_at >= UNLIMITED_FROM:
        return Category.UNLIMITED
    if expire_at <= now:
        return Category.EXPIRED
    if is_trial and not include_trial:
        return Category.TRIAL
    if status == "LIMITED" and not include_limited:
        return Category.LIMITED
    sub_updated_at = as_aware(row.get("sub_updated_at"))
    if row.get("recent_payment") or (
        sub_updated_at is not None and sub_updated_at > now - timedelta(minutes=RECENT_SUB_MIN)
    ):
        return Category.RECENT_CHANGE
    return Category.APPLY


# ── Сверка с панелью ──────────────────────────────────────────────────────────


def panel_mismatch(local_sub: Any, panel_user: Any, *, check_expire: bool = True) -> list[str]:
    """Поля, в которых наша подписка разошлась с панелью. Пусто — можно писать.

    Сравнение идёт через `RemnaSubscriptionDto.from_remna_user` — ту же нормализацию,
    что у синхронизации базы: device_limit null в панели и 0 у нас — одно и то же,
    трафик сравнивается в ГБ, сквады без учёта порядка. Ссылку подписки не сравниваем:
    она меняется при перевыпуске и на срок не влияет.
    """
    remote = RemnaSubscriptionDto.from_remna_user(panel_user)
    pairs = (
        ("status", str(_value(local_sub.status)), str(_value(remote.status))),
        ("traffic_limit", int(local_sub.traffic_limit or 0), int(remote.traffic_limit or 0)),
        ("device_limit", int(local_sub.device_limit or 0), int(remote.device_limit or 0)),
        (
            "traffic_limit_strategy",
            str(_value(local_sub.traffic_limit_strategy)),
            str(_value(remote.traffic_limit_strategy)),
        ),
        ("tag", local_sub.tag or None, remote.tag or None),
        (
            "internal_squads",
            sorted(str(s) for s in (local_sub.internal_squads or [])),
            sorted(str(s) for s in (remote.internal_squads or [])),
        ),
        (
            "external_squad",
            str(local_sub.external_squad) if local_sub.external_squad else None,
            str(remote.external_squad) if remote.external_squad else None,
        ),
    )
    out = [name for name, ours, theirs in pairs if ours != theirs]
    if check_expire and not same_moment(local_sub.expire_at, remote.expire_at):
        out.append("expire_at")
    return out


def identity_conflicts(user: Any, panel_user: Any) -> list[str]:
    """Почта и Telegram, заданные И у нас, И в панели, но разные."""
    out: list[str] = []
    ours_email = (getattr(user, "email", None) or "").strip().lower()
    theirs_email = (getattr(panel_user, "email", None) or "").strip().lower()
    if ours_email and theirs_email and ours_email != theirs_email:
        out.append("email")
    ours_tg = getattr(user, "telegram_id", None)
    theirs_tg = getattr(panel_user, "telegram_id", None)
    if ours_tg is not None and theirs_tg is not None and int(ours_tg) != int(theirs_tg):
        out.append("telegram_id")
    return out


def protected_user(user: Any, panel_user: Any) -> Any:
    """Кем представить человека панели, чтобы PATCH не стёр её данные. None — конфликт.

    На живых данных у одного из 54 подписчиков почта была только в панели: полный
    PATCH из нашей копии записал бы туда null. Где у нас пусто, а в панели задано —
    берём панельное значение в НЕСОХРАНЯЕМУЮ копию (`dataclasses.replace` даёт новый
    объект со своим журналом изменений: исходный DTO и наша база не меняются).
    Задано и там, и там, но по-разному — это не нам решать: None.
    """
    if identity_conflicts(user, panel_user):
        return None
    changes: dict[str, Any] = {}
    panel_email = getattr(panel_user, "email", None)
    if not getattr(user, "email", None) and panel_email:
        changes["email"] = panel_email
    panel_tg = getattr(panel_user, "telegram_id", None)
    if getattr(user, "telegram_id", None) is None and panel_tg is not None:
        changes["telegram_id"] = int(panel_tg)
    return dataclasses.replace(user, **changes) if changes else user


def resolve_recovery(
    old: Optional[datetime],
    target: Optional[datetime],
    panel_expire: Optional[datetime],
    now: datetime,
) -> Literal["DONE", "RETRY", "MANUAL"]:
    """Что делать со строкой, упавшей между записью target и итогом.

    DONE  — в панели уже target: PATCH дошёл, повторять нельзя;
    RETRY — в панели всё ещё прежний срок: повторяем С ТЕМ ЖЕ target (не
            пересчитывая от нового now — иначе человек получил бы лишние часы);
    MANUAL — в панели третье значение (оплата, ручная правка) или target уже в
            прошлом: автоматически тут ничего не решить.
    """
    if target is not None and panel_expire is not None and same_moment(panel_expire, target):
        return "DONE"
    if (
        old is not None
        and target is not None
        and panel_expire is not None
        and same_moment(panel_expire, old)
        and as_aware(target) > now + timedelta(minutes=1)
    ):
        return "RETRY"
    return "MANUAL"


# ── Доставка сообщения ────────────────────────────────────────────────────────


def message_route(row: Mapping[str, Any], channels: Sequence[str], email_enabled: bool) -> str:
    """Куда уйдёт сообщение человеку — та же лестница, что у `deliver_message`."""
    if "telegram" in channels and row.get("telegram_id") is not None and not row.get("is_bot_blocked"):
        return "telegram"
    if "cabinet" in channels and row.get("has_push"):
        return "push_only"
    if "email" in channels and email_enabled and row.get("email") and row.get("is_email_verified"):
        return "email_only"
    if "cabinet" in channels:
        return "cabinet_only"
    return "unreachable"


@dataclasses.dataclass
class Delivery:
    status: str
    category: Optional[str]
    channels: list[str]
    tg_message_id: Optional[int] = None
    error: Optional[str] = None
    # Текст TelegramBadRequest: три подряд — задача останавливается, текст битый.
    bad_request: Optional[str] = None


async def deliver_message(
    row: Mapping[str, Any],
    *,
    channels: Sequence[str],
    send_tg: Callable[[Mapping[str, Any]], Awaitable[Any]],
    record_feed: Callable[[Mapping[str, Any]], Awaitable[bool]],
    send_push: Callable[[Mapping[str, Any]], Awaitable[int]],
    send_email: Callable[[Mapping[str, Any]], Awaitable[Any]],
    email_enabled: bool,
    on_retry_after: Callable[[float], Awaitable[Any]],
) -> Delivery:
    """Лестница каналов на одного человека: Telegram → push → письмо; лента — всем.

    Push и письмо — только тем, до кого не дошёл Telegram: человек, получивший
    сообщение в бота, не должен ловить его ещё и письмом. Лента кабинета пишется
    при включённом канале всегда — это история, а не уведомление.

    Ответ Telegram `None` (заблокировал бота, чата нет) — штатный недошедший
    случай, дальше по лестнице. RetryAfter — ждём и повторяем, не больше трёх раз.
    """
    got: list[str] = []
    errors: list[str] = []
    tg_message_id: Optional[int] = None
    bad_request: Optional[str] = None

    if "telegram" in channels and row.get("telegram_id") is not None and not row.get("is_bot_blocked"):
        retries = 0
        while True:
            try:
                sent = await send_tg(row)
            except TelegramBadRequest as exc:
                bad_request = exc.message
                errors.append(error_text(exc))
                break
            except Exception as exc:  # noqa: BLE001 — один человек не роняет задачу
                # retry_after читаем с исключения, а не ловим TelegramRetryAfter по
                # классу: так же сделано в renewal_discount, и подделки в тестах
                # не обязаны тянуть aiogram.
                retry_after = getattr(exc, "retry_after", None)
                if isinstance(retry_after, (int, float)) and retries < TG_RETRY_MAX:
                    retries += 1
                    await on_retry_after(float(retry_after) + BATCH_DELAY)
                    continue
                errors.append(error_text(exc))
                break
            if sent:
                got.append("telegram")
                tg_message_id = getattr(sent, "message_id", None)
            break

    fed = False
    if "cabinet" in channels:
        fed = bool(await record_feed(row))
        if "telegram" not in got and row.get("has_push"):
            try:
                if int(await send_push(row) or 0) > 0:
                    got.append("push")
            except Exception as exc:  # noqa: BLE001
                errors.append(error_text(exc))

    if (
        not got
        and "email" in channels
        and email_enabled
        and row.get("email")
        and row.get("is_email_verified")
    ):
        try:
            await send_email(row)
            got.append("email")
        except Exception as exc:  # noqa: BLE001
            errors.append(error_text(exc))

    delivered = got + (["cabinet"] if fed else [])
    error = "; ".join(errors)[:500] or None
    if got:
        return Delivery("DONE", None, delivered, tg_message_id, error, bad_request)
    if fed:
        return Delivery("DONE", Category.CABINET_ONLY.value, delivered, None, error, bad_request)
    if errors:
        return Delivery("FAILED", None, [], None, error, bad_request)
    return Delivery("SKIPPED", Category.NO_CHANNEL.value, [], None, None, None)


async def send_test_message(
    admin: Any,
    content: str,
    *,
    notify_user: Callable[[Any, MessagePayloadDto], Awaitable[Any]],
    record_feed: Callable[[int, dict], Awaitable[bool]],
) -> dict[str, Any]:
    """«Проверить на себе»: то же сообщение админу, от лица обычного клиента.

    Копия с ролью USER: для админских ролей overlay-уведомления рисуют rich-таблицу
    и зеркалят сообщение в админскую ленту — проверка показала бы не то, что увидят
    люди. Исходный объект админа не меняется.
    """
    as_client = dataclasses.replace(admin, role=Role.USER)
    await record_feed(
        int(admin.id),
        {"title": message_title(getattr(admin, "language", None), brand_name()), "body": plain_text(content), "url": "/"},
    )
    if getattr(as_client, "telegram_id", None) is None:
        return {"telegram": False, "reason": "no_telegram"}
    try:
        sent = await notify_user(as_client, build_payload(content))
    except TelegramBadRequest as exc:
        raise TelegramRejected(exc.message) from exc
    except Exception as exc:  # noqa: BLE001 — проверка не должна ронять админку
        logger.warning(f"bulk: проверка сообщения на себе не отправлена: {exc}")
        return {"telegram": False, "reason": "send_failed"}
    return {"telegram": bool(sent), "reason": None if sent else "send_failed"}


# ── Тексты админам ────────────────────────────────────────────────────────────


def _esc(value: Any) -> str:
    return html_lib.escape(str(value if value is not None else ""))


def days_summary(job: Mapping[str, Any], totals: Mapping[str, Any]) -> str:
    params = job.get("params") or {}
    return (
        f"<b>Массовое продление №{job['id']} завершено</b>\n"
        f"+{_esc(params.get('days'))} дн.: добавлено {totals.get('applied', 0)} "
        f"(из них на паузе {totals.get('frozen', 0)}), пропущено {totals.get('skipped', 0)}, "
        f"ошибок {totals.get('failed', 0)}, проверить вручную {totals.get('flagged', 0)}. "
        f"Запустил: {_esc(job.get('created_by_label'))}"
    )


def days_paused(job: Mapping[str, Any], reason: str) -> str:
    return f"<b>Массовое продление №{job['id']} на паузе</b>\n{_esc(reason)}"


def days_stopped(job: Mapping[str, Any], reason: str) -> str:
    return f"<b>Массовое продление №{job['id']} остановлено</b>\n{_esc(reason)}"


def message_summary(job: Mapping[str, Any], totals: Mapping[str, Any]) -> str:
    return (
        f"<b>Сообщение №{job['id']} разослано</b>\n"
        f"Telegram {totals.get('tg', 0)}, push {totals.get('push', 0)}, "
        f"письмом {totals.get('email', 0)}, только в кабинете {totals.get('cabinet', 0)}, "
        f"не доставлено {totals.get('failed', 0)}. Запустил: {_esc(job.get('created_by_label'))}"
    )


def message_stopped(job: Mapping[str, Any], reason: str) -> str:
    return f"<b>Сообщение №{job['id']} остановлено</b>\n{_esc(reason)}"


def panel_pause_reason(error: Optional[str]) -> str:
    return (
        f"Панель VPN не отвечает: {error or 'нет ответа'}. Уже добавленное сохранено — "
        "нажмите «Продолжить», когда панель заработает."
    )


# ── SQL ───────────────────────────────────────────────────────────────────────

# Одна выборка на список id — и для предпросмотра, и перед каждым человеком. Дублей
# строк нет: открытая выдача резерва у человека одна (ux_reserve_grants_open),
# пауза — одна (PK user_id).
CLASSIFY_SQL = f"""
SELECT u.id, u.role::text AS role, u.is_blocked, u.name, u.telegram_id, u.is_bot_blocked,
       u.email, u.is_email_verified, lower(u.language::text) AS language,
       s.id AS sub_id, s.status::text AS sub_status, s.is_trial, s.expire_at, s.user_remna_id,
       s.updated_at AS sub_updated_at,
       f.remaining_seconds AS frozen_remaining,
       r.reserve_expire_at,
       EXISTS (
           SELECT 1 FROM transactions t WHERE t.user_id = u.id AND (
               (t.status::text = 'PENDING'
                AND t.created_at > now() - interval '{PENDING_TX_MIN} minutes')
               OR (t.status::text = 'COMPLETED'
                AND GREATEST(t.created_at, t.updated_at) > now() - interval '{RECENT_TX_MIN} minutes')
           )
       ) AS recent_payment,
       EXISTS (SELECT 1 FROM push_subscriptions p WHERE p.user_id = u.id) AS has_push
FROM users u
LEFT JOIN subscriptions s ON s.id = u.current_subscription_id
LEFT JOIN subscription_freezes f ON f.user_id = u.id AND f.active = true
LEFT JOIN reserve_grants r ON r.user_id = u.id AND r.ended = false AND r.reserve_expire_at > now()
WHERE u.id = ANY(:ids)
"""

CREATE_JOB_SQL = """
INSERT INTO bulk_jobs (kind, request_id, params_hash, parent_job_id, created_by,
                       created_by_label, params, segment_hash, total)
VALUES (:kind, :request_id, :params_hash, :parent_job_id, :created_by,
        :created_by_label, CAST(:params AS JSONB), :segment_hash, :total)
RETURNING id
"""

INSERT_ITEMS_SQL = """
INSERT INTO bulk_job_items (job_id, user_id, status, category, deferred)
SELECT :job_id, x.user_id, x.status, x.category, x.deferred
FROM unnest(CAST(:user_ids AS INTEGER[]), CAST(:statuses AS VARCHAR[]),
            CAST(:categories AS VARCHAR[]), CAST(:deferreds AS BOOLEAN[]))
     AS x(user_id, status, category, deferred)
"""

# CANCELING не превращаем в PROCESSING: остановленная задача, подхваченная воркером,
# должна только досверить начатое, а не продолжить выдачу.
ACQUIRE_LEASE_SQL = f"""
UPDATE bulk_jobs
SET status = CASE WHEN status = 'QUEUED' THEN 'PROCESSING' ELSE status END,
    lease_owner = :tok,
    lease_until = now() + interval '{LEASE_SEC} seconds',
    started_at = COALESCE(started_at, now())
WHERE id = :id
  AND status IN ('QUEUED', 'PROCESSING', 'CANCELING')
  AND (lease_until IS NULL OR lease_until < now() OR lease_owner = :tok)
RETURNING status
"""

# Продление аренды заодно отдаёт статус: «Остановить» видно на каждом человеке,
# без отдельного запроса.
RENEW_LEASE_SQL = f"""
UPDATE bulk_jobs SET lease_until = now() + interval '{LEASE_SEC} seconds'
WHERE id = :id AND lease_owner = :tok AND status IN ('PROCESSING', 'CANCELING')
RETURNING status
"""

RELEASE_SQL = """
UPDATE bulk_jobs
SET status = :status,
    pause_reason = :reason,
    finished_at = CASE WHEN CAST(:finished AS BOOLEAN) THEN now() ELSE NULL END,
    lease_owner = NULL,
    lease_until = NULL
WHERE id = :id AND lease_owner = :tok
RETURNING id
"""

CLAIM_ITEM_SQL = """
UPDATE bulk_job_items SET status = :to, attempts = attempts + 1, updated_at = now()
WHERE job_id = :job_id AND user_id = :user_id AND status = 'PENDING'
RETURNING 1
"""

COUNTERS_SQL = """
UPDATE bulk_jobs j
SET done_count = c.done, applied_count = c.applied, skipped_count = c.skipped,
    failed_count = c.failed, unknown_count = c.unknown, verify_flagged = c.flagged
FROM (
    SELECT count(*) FILTER (WHERE status IN ('DONE', 'SKIPPED', 'FAILED', 'UNKNOWN')) AS done,
           count(*) FILTER (WHERE status = 'DONE') AS applied,
           count(*) FILTER (WHERE status = 'SKIPPED') AS skipped,
           count(*) FILTER (WHERE status = 'FAILED') AS failed,
           count(*) FILTER (WHERE status = 'UNKNOWN') AS unknown,
           count(*) FILTER (WHERE verify_note IS NOT NULL) AS flagged
    FROM bulk_job_items WHERE job_id = :id
) c
WHERE j.id = :id
"""

TOTALS_SQL = """
SELECT count(*) FILTER (WHERE status = 'DONE') AS applied,
       count(*) FILTER (WHERE status = 'DONE' AND added_seconds IS NOT NULL) AS frozen,
       count(*) FILTER (WHERE status = 'SKIPPED') AS skipped,
       count(*) FILTER (WHERE status = 'FAILED') AS failed,
       count(*) FILTER (WHERE status = 'UNKNOWN') AS unknown,
       count(*) FILTER (WHERE verify_note IS NOT NULL) AS flagged,
       count(*) FILTER (WHERE channels LIKE '%telegram%') AS tg,
       count(*) FILTER (WHERE channels LIKE '%push%') AS push,
       count(*) FILTER (WHERE channels LIKE '%email%') AS email,
       count(*) FILTER (WHERE category = 'CABINET_ONLY') AS cabinet
FROM bulk_job_items WHERE job_id = :id
"""

_JOB_COLUMNS = """
j.id, j.kind, j.status, j.request_id, j.params_hash, j.parent_job_id, j.created_by,
j.created_by_label, j.params, j.segment_hash, j.total, j.done_count, j.applied_count,
j.skipped_count, j.failed_count, j.unknown_count, j.verify_flagged, j.pause_reason,
j.canceled_by_label, j.created_at, j.started_at, j.finished_at,
(SELECT max(c.id) FROM bulk_jobs c WHERE c.parent_job_id = j.id) AS child_job_id
"""

JOB_SQL = f"SELECT {_JOB_COLUMNS} FROM bulk_jobs j WHERE j.id = :id"
JOBS_LIST_SQL = f"SELECT {_JOB_COLUMNS} FROM bulk_jobs j ORDER BY j.id DESC LIMIT :limit"
JOB_BY_REQUEST_SQL = f"SELECT {_JOB_COLUMNS} FROM bulk_jobs j WHERE j.request_id = :rid"
ACTIVE_JOBS_SQL = "SELECT kind, id FROM bulk_jobs WHERE status IN ('QUEUED', 'PROCESSING', 'PAUSED', 'CANCELING')"

BREAKDOWN_SQL = """
SELECT job_id, COALESCE(category, status) AS k, count(*) AS n
FROM bulk_job_items WHERE job_id = ANY(CAST(:ids AS BIGINT[]))
GROUP BY job_id, COALESCE(category, status)
"""

ITEMS_SQL = """
SELECT user_id, status, category, subscription_id, old_expire_at, target_expire_at,
       added_seconds, deferred, verify_note, error, attempts
FROM bulk_job_items
WHERE job_id = :job_id AND status = ANY(CAST(:statuses AS VARCHAR[]))
  AND (CAST(:deferred AS BOOLEAN) IS NULL OR deferred = CAST(:deferred AS BOOLEAN))
ORDER BY user_id
"""

ITEMS_PAGE_SQL = """
SELECT i.user_id, u.name, i.status, i.category, i.old_expire_at, i.target_expire_at,
       i.channels, i.verify_note, i.error
FROM bulk_job_items i LEFT JOIN users u ON u.id = i.user_id
WHERE i.job_id = :job_id
  AND (CAST(:statuses AS VARCHAR[]) IS NULL OR i.status = ANY(CAST(:statuses AS VARCHAR[])))
ORDER BY i.user_id
LIMIT :limit OFFSET :offset
"""

ITEMS_COUNT_SQL = """
SELECT count(*) FROM bulk_job_items i
WHERE i.job_id = :job_id
  AND (CAST(:statuses AS VARCHAR[]) IS NULL OR i.status = ANY(CAST(:statuses AS VARCHAR[])))
"""

SKIP_PENDING_SQL = """
UPDATE bulk_job_items SET status = 'SKIPPED', category = :category, updated_at = now()
WHERE job_id = :job_id AND status = 'PENDING'
"""

# Строки, захваченные, но так и не дошедшие до записи срока: ни панель, ни пауза
# не тронуты, их можно спокойно вернуть в очередь.
UNCLAIM_SQL = """
UPDATE bulk_job_items SET status = 'PENDING', updated_at = now()
WHERE job_id = :job_id AND status = 'RUNNING'
  AND target_expire_at IS NULL AND added_seconds IS NULL
"""

SUB_STATE_SQL = "SELECT expire_at, status::text AS status, updated_at FROM subscriptions WHERE id = :id"

ADD_FROZEN_SQL = """
UPDATE subscription_freezes SET remaining_seconds = remaining_seconds + :sec
WHERE user_id = :u AND active = true
RETURNING remaining_seconds
"""

FREEZE_STATE_SQL = "SELECT active, remaining_seconds, remna_uuid FROM subscription_freezes WHERE user_id = :u"

RECENT_DAYS_SQL = f"""
SELECT count(DISTINCT i.user_id) AS n, max(i.job_id) AS job_id
FROM bulk_job_items i JOIN bulk_jobs j ON j.id = i.job_id
WHERE j.kind = 'days' AND i.status = 'DONE'
  AND i.updated_at > now() - interval '{REPEAT_WINDOW_H} hours'
  AND i.user_id = ANY(CAST(:ids AS INTEGER[]))
"""

RECENT_TEXT_SQL = f"""
SELECT count(DISTINCT i.user_id)
FROM bulk_job_items i JOIN bulk_jobs j ON j.id = i.job_id
WHERE j.kind = 'message' AND i.text_sha256 = :sha AND i.status IN ('DONE', 'UNKNOWN')
  AND i.updated_at > now() - interval '{REPEAT_WINDOW_H} hours'
  AND i.user_id = ANY(CAST(:ids AS INTEGER[]))
"""

DONE_USERS_SQL = "SELECT user_id FROM bulk_job_items WHERE job_id = :id AND status = 'DONE' ORDER BY user_id"

INFLIGHT_SQL = "SELECT count(*) FROM bulk_job_items WHERE job_id = :id AND status = ANY(CAST(:statuses AS VARCHAR[]))"

CANCEL_NOW_SQL = """
UPDATE bulk_jobs
SET status = 'CANCELED', finished_at = now(), canceled_by_label = :by,
    lease_owner = NULL, lease_until = NULL
WHERE id = :id AND status = :expected
RETURNING id
"""

MARK_CANCELING_SQL = """
UPDATE bulk_jobs SET status = 'CANCELING', canceled_by_label = :by, finished_at = NULL
WHERE id = :id AND status = :expected
RETURNING id
"""

REQUEUE_SQL = """
UPDATE bulk_jobs
SET status = 'QUEUED', pause_reason = NULL, finished_at = NULL, lease_owner = NULL, lease_until = NULL
WHERE id = :id AND status = :expected
RETURNING id
"""

SET_REASON_SQL = "UPDATE bulk_jobs SET pause_reason = :reason WHERE id = :id"

# Воркер умер при `ack when_received` — задача потеряна для брокера, но не для
# журнала. Свежие QUEUED (меньше минуты) не трогаем: их прямо сейчас подхватывает
# отправленная из админки задача.
STALLED_SQL = """
SELECT id FROM bulk_jobs
WHERE (status = 'QUEUED' AND (lease_until IS NULL OR lease_until < now())
       AND created_at < now() - interval '1 minute')
   OR (status IN ('PROCESSING', 'CANCELING') AND (lease_until IS NULL OR lease_until < now()))
ORDER BY id
"""

FEED_INSERT_SQL = "INSERT INTO user_notifications (user_id, title, body, url) VALUES (:u, :t, :b, :url)"
FEED_TRIM_SQL = (
    "DELETE FROM user_notifications WHERE user_id = :u AND id <= "
    "(SELECT id FROM user_notifications WHERE user_id = :u ORDER BY id DESC OFFSET 100 LIMIT 1)"
)

_ITEM_FIELDS = frozenset(
    {
        "status",
        "category",
        "subscription_id",
        "old_expire_at",
        "target_expire_at",
        "added_seconds",
        "deferred",
        "channels",
        "tg_message_id",
        "text_sha256",
        "verify_note",
        "error",
    }
)


def is_missing_table(exc: BaseException) -> bool:
    msg = f"{getattr(getattr(exc, 'orig', None), 'sqlstate', '')} {exc}".lower()
    return "42p01" in msg or "undefinedtable" in msg or ("does not exist" in msg and "relation" in msg)


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


def _row(mapping: Any) -> dict[str, Any]:
    out = dict(mapping)
    if "params" in out:
        out["params"] = _as_dict(out["params"])
    return out


class BulkStore:
    """Журнал задач на AsyncSession. Сам ничего не коммитит — кроме оговорённого.

    Коммит — решение вызывающего: запись target «до панели» обязана уйти в базу
    отдельным коммитом ДО вызова панели, а строка DONE — вместе с локальным сроком.
    Эти границы видны в процессорах, а не спрятаны здесь.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()

    # задачи

    async def acquire_lease(self, job_id: int, token: str) -> Optional[str]:
        row = (await self.session.execute(text(ACQUIRE_LEASE_SQL), {"id": job_id, "tok": token})).first()
        return str(row[0]) if row else None

    async def renew_lease(self, job_id: int, token: str) -> Optional[str]:
        row = (await self.session.execute(text(RENEW_LEASE_SQL), {"id": job_id, "tok": token})).first()
        return str(row[0]) if row else None

    async def release(
        self, job_id: int, token: str, status: str, *, reason: Optional[str] = None, finished: bool
    ) -> bool:
        row = (
            await self.session.execute(
                text(RELEASE_SQL),
                {"id": job_id, "tok": token, "status": status, "reason": reason, "finished": finished},
            )
        ).first()
        return row is not None

    async def set_reason(self, job_id: int, reason: Optional[str]) -> None:
        await self.session.execute(text(SET_REASON_SQL), {"id": job_id, "reason": reason})

    async def get_job(self, job_id: int) -> Optional[dict[str, Any]]:
        row = (await self.session.execute(text(JOB_SQL), {"id": job_id})).mappings().first()
        return _row(row) if row else None

    async def job_by_request(self, request_id: UUID) -> Optional[dict[str, Any]]:
        row = (await self.session.execute(text(JOB_BY_REQUEST_SQL), {"rid": request_id})).mappings().first()
        return _row(row) if row else None

    async def list_jobs(self, limit: int) -> list[dict[str, Any]]:
        rows = (await self.session.execute(text(JOBS_LIST_SQL), {"limit": limit})).mappings().all()
        return [_row(r) for r in rows]

    async def active_jobs(self) -> dict[str, int]:
        rows = (await self.session.execute(text(ACTIVE_JOBS_SQL))).all()
        return {str(kind): int(job_id) for kind, job_id in rows}

    async def breakdown(self, job_ids: Sequence[int]) -> dict[int, dict[str, int]]:
        if not job_ids:
            return {}
        rows = (await self.session.execute(text(BREAKDOWN_SQL), {"ids": list(job_ids)})).all()
        out: dict[int, dict[str, int]] = {}
        for job_id, key, n in rows:
            out.setdefault(int(job_id), {})[str(key)] = int(n)
        return out

    async def recount(self, job_id: int) -> None:
        await self.session.execute(text(COUNTERS_SQL), {"id": job_id})

    async def totals(self, job_id: int) -> dict[str, int]:
        row = (await self.session.execute(text(TOTALS_SQL), {"id": job_id})).mappings().first()
        return {k: int(v or 0) for k, v in dict(row or {}).items()}

    async def stalled_jobs(self) -> list[int]:
        return [int(r[0]) for r in (await self.session.execute(text(STALLED_SQL))).all()]

    async def create_job(
        self,
        *,
        kind: str,
        request_id: UUID,
        params_hash: str,
        parent_job_id: Optional[int],
        created_by: Optional[int],
        created_by_label: str,
        params: Mapping[str, Any],
        segment_hash: str,
        items: Sequence[tuple[int, str, Optional[str], bool]],
    ) -> int:
        """Задача и строки людей одной транзакцией. Коммит — за вызывающим.

        Нарушение уникальности откатывает транзакцию и превращается в понятное
        исключение: вторая активная задача того же вида или повтор request_id.
        """
        try:
            job_id = (
                await self.session.execute(
                    text(CREATE_JOB_SQL),
                    {
                        "kind": kind,
                        "request_id": request_id,
                        "params_hash": params_hash,
                        "parent_job_id": parent_job_id,
                        "created_by": created_by,
                        "created_by_label": (created_by_label or "—")[:120],
                        "params": json.dumps(params, ensure_ascii=False, default=str),
                        "segment_hash": segment_hash,
                        "total": len(items),
                    },
                )
            ).scalar_one()
            if items:
                await self.session.execute(
                    text(INSERT_ITEMS_SQL),
                    {
                        "job_id": job_id,
                        "user_ids": [int(i[0]) for i in items],
                        "statuses": [i[1] for i in items],
                        "categories": [i[2] for i in items],
                        "deferreds": [bool(i[3]) for i in items],
                    },
                )
        except IntegrityError as exc:
            await self.session.rollback()
            message = str(exc)
            if "ux_bulk_jobs_one_active" in message:
                raise ActiveJobExists((await self.active_jobs()).get(kind)) from exc
            if "request_id" in message:
                existing = await self.job_by_request(request_id)
                raise DuplicateRequest(existing["id"] if existing else None) from exc
            raise
        return int(job_id)

    async def cancel_now(self, job_id: int, expected: str, by: str) -> bool:
        row = (
            await self.session.execute(text(CANCEL_NOW_SQL), {"id": job_id, "expected": expected, "by": by[:120]})
        ).first()
        if row is None:
            return False
        await self.skip_pending(job_id, Category.CANCELED.value)
        await self.recount(job_id)
        return True

    async def mark_canceling(self, job_id: int, expected: str, by: str) -> bool:
        row = (
            await self.session.execute(
                text(MARK_CANCELING_SQL), {"id": job_id, "expected": expected, "by": by[:120]}
            )
        ).first()
        return row is not None

    async def requeue(self, job_id: int, expected: str) -> bool:
        row = (await self.session.execute(text(REQUEUE_SQL), {"id": job_id, "expected": expected})).first()
        return row is not None

    # строки людей

    async def items(
        self, job_id: int, statuses: Sequence[str], *, deferred: Optional[bool] = None
    ) -> list[dict[str, Any]]:
        rows = (
            await self.session.execute(
                text(ITEMS_SQL), {"job_id": job_id, "statuses": list(statuses), "deferred": deferred}
            )
        ).mappings().all()
        return [dict(r) for r in rows]

    async def items_page(
        self, job_id: int, statuses: Optional[Sequence[str]], limit: int, offset: int
    ) -> tuple[int, list[dict[str, Any]]]:
        params = {"job_id": job_id, "statuses": list(statuses) if statuses else None}
        total = int((await self.session.execute(text(ITEMS_COUNT_SQL), params)).scalar_one())
        rows = (
            await self.session.execute(text(ITEMS_PAGE_SQL), {**params, "limit": limit, "offset": offset})
        ).mappings().all()
        return total, [dict(r) for r in rows]

    async def inflight(self, job_id: int, statuses: Sequence[str]) -> int:
        return int(
            (await self.session.execute(text(INFLIGHT_SQL), {"id": job_id, "statuses": list(statuses)})).scalar_one()
        )

    async def claim(self, job_id: int, user_id: int, to: str) -> bool:
        row = (
            await self.session.execute(text(CLAIM_ITEM_SQL), {"job_id": job_id, "user_id": user_id, "to": to})
        ).first()
        return row is not None

    async def set_item(self, job_id: int, user_id: int, **fields: Any) -> None:
        unknown = set(fields) - _ITEM_FIELDS
        if unknown:
            raise ValueError(f"bulk_job_items: неизвестные поля {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{name} = :{name}" for name in fields)
        await self.session.execute(
            text(
                f"UPDATE bulk_job_items SET {assignments}, updated_at = now() "
                "WHERE job_id = :job_id AND user_id = :user_id"
            ),
            {**fields, "job_id": job_id, "user_id": user_id},
        )

    async def skip_pending(self, job_id: int, category: str) -> int:
        result = await self.session.execute(text(SKIP_PENDING_SQL), {"job_id": job_id, "category": category})
        return int(result.rowcount or 0)

    async def unclaim_unstarted(self, job_id: int) -> None:
        await self.session.execute(text(UNCLAIM_SQL), {"job_id": job_id})

    async def done_user_ids(self, job_id: int) -> list[int]:
        return [int(r[0]) for r in (await self.session.execute(text(DONE_USERS_SQL), {"id": job_id})).all()]

    # данные людей

    async def classify_rows(self, ids: Sequence[int]) -> dict[int, dict[str, Any]]:
        if not ids:
            return {}
        rows = (await self.session.execute(text(CLASSIFY_SQL), {"ids": [int(i) for i in ids]})).mappings().all()
        return {int(r["id"]): dict(r) for r in rows}

    async def sub_state(self, sub_id: int) -> Optional[dict[str, Any]]:
        row = (await self.session.execute(text(SUB_STATE_SQL), {"id": sub_id})).mappings().first()
        return dict(row) if row else None

    async def add_frozen_seconds(self, user_id: int, seconds: int) -> bool:
        row = (await self.session.execute(text(ADD_FROZEN_SQL), {"u": user_id, "sec": int(seconds)})).first()
        return row is not None

    async def freeze_state(self, user_id: int) -> Optional[dict[str, Any]]:
        row = (await self.session.execute(text(FREEZE_STATE_SQL), {"u": user_id})).mappings().first()
        return dict(row) if row else None

    async def recent_days(self, ids: Sequence[int]) -> dict[str, Any]:
        empty = {"count": 0, "job_id": None, "days": None}
        if not ids:
            return empty
        row = (await self.session.execute(text(RECENT_DAYS_SQL), {"ids": list(ids)})).first()
        if not row or not row[0]:
            return empty
        job = await self.get_job(int(row[1]))
        days = (job or {}).get("params", {}).get("days")
        return {"count": int(row[0]), "job_id": int(row[1]), "days": days}

    async def recent_text(self, ids: Sequence[int], sha: str) -> int:
        if not ids:
            return 0
        return int(
            (await self.session.execute(text(RECENT_TEXT_SQL), {"ids": list(ids), "sha": sha})).scalar_one() or 0
        )

    async def record_feed(self, user_id: int, payload: Mapping[str, Any]) -> bool:
        """Строка в ленте уведомлений кабинета. Сбой не отравляет сессию.

        Своя вставка, а не `overlay_push._record_user_notification`: та гасит ошибку
        ВНУТРИ себя, и после неудачного INSERT транзакция Postgres осталась бы
        прерванной — следующая запись итога по человеку упала бы уже на ней.
        Точка сохранения откатывает ровно эту вставку.
        """
        try:
            async with self.session.begin_nested():
                await self.session.execute(
                    text(FEED_INSERT_SQL),
                    {
                        "u": user_id,
                        "t": str(payload.get("title") or "")[:200],
                        "b": str(payload.get("body") or ""),
                        "url": str(payload.get("url") or "/")[:500],
                    },
                )
                await self.session.execute(text(FEED_TRIM_SQL), {"u": user_id})
            return True
        except Exception as exc:  # noqa: BLE001 — лента не должна срывать доставку
            logger.warning(f"bulk: лента кабинета user_id={user_id} не записана: {exc}")
            return False

    async def push(self, user_id: int, payload: Mapping[str, Any]) -> int:
        from src.infrastructure.services.overlay_push import send_to_user

        try:
            async with self.session.begin_nested():
                return int(await send_to_user(self.session, user_id, dict(payload)) or 0)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"bulk: push user_id={user_id} не отправлен: {exc}")
            return 0


# ── Выборка и предпросмотр ────────────────────────────────────────────────────


@dataclasses.dataclass
class DaysEvaluation:
    matched: int
    categories: dict[int, Category]
    sample: list[dict[str, Any]]

    def ids(self, *cats: Category) -> list[int]:
        return sorted(uid for uid, cat in self.categories.items() if cat in cats)

    @property
    def counts(self) -> Counter:
        return Counter(cat.value for cat in self.categories.values())

    @property
    def eligible_ids(self) -> list[int]:
        return self.ids(*DAYS_ELIGIBLE)

    @property
    def segment_hash(self) -> str:
        return segment_hash(self.eligible_ids)


async def evaluate_days(
    store: BulkStore,
    ids: Sequence[int],
    *,
    days: int,
    include_trial: bool,
    include_limited: bool,
    now: datetime,
) -> DaysEvaluation:
    rows = await store.classify_rows(ids)
    categories: dict[int, Category] = {}
    sample: list[dict[str, Any]] = []
    for uid in ids:
        row = rows.get(int(uid))
        cat = (
            classify_days(row, now=now, include_trial=include_trial, include_limited=include_limited)
            if row
            else Category.NO_SUBSCRIPTION
        )
        categories[int(uid)] = cat
        if cat == Category.APPLY and len(sample) < SAMPLE_SIZE:
            expire_at = as_aware(row["expire_at"])
            sample.append(
                {
                    "name": row.get("name"),
                    "expire_at": expire_at.isoformat(),
                    "new_expire_at": compute_new_expire(expire_at, days, now).isoformat(),
                }
            )
    return DaysEvaluation(matched=len(ids), categories=categories, sample=sample)


def days_items(ev: DaysEvaluation) -> list[tuple[int, str, Optional[str], bool]]:
    """Строки журнала: подходящим PENDING (недавние — сразу отложенными), остальным SKIPPED."""
    out: list[tuple[int, str, Optional[str], bool]] = []
    for uid in sorted(ev.categories):
        cat = ev.categories[uid]
        if cat in (Category.APPLY, Category.APPLY_FROZEN):
            out.append((uid, "PENDING", None, False))
        elif cat == Category.RECENT_CHANGE:
            out.append((uid, "PENDING", None, True))
        else:
            out.append((uid, "SKIPPED", cat.value, False))
    return out


def days_preview_payload(
    ev: DaysEvaluation,
    *,
    recently: Mapping[str, Any],
    active_job_id: Optional[int],
    readonly: bool,
) -> dict[str, Any]:
    counts = ev.counts
    sample = [{"name": s.get("name")} for s in ev.sample] if readonly else ev.sample
    return {
        "matched": ev.matched,
        "apply": counts.get(Category.APPLY.value, 0),
        "apply_frozen": counts.get(Category.APPLY_FROZEN.value, 0),
        "deferred": counts.get(Category.RECENT_CHANGE.value, 0),
        "skipped": {cat.value: counts.get(cat.value, 0) for cat in DAYS_SKIP},
        "recently_extended": dict(recently),
        "sample": sample,
        "segment_hash": ev.segment_hash,
        "active_job_id": active_job_id,
        "limits": {"max_days": MAX_DAYS, "max_users": MAX_USERS},
    }


@dataclasses.dataclass
class MessageEvaluation:
    matched: int
    recipients: list[int]
    skipped: dict[int, Category]
    by_channel: dict[str, int]

    @property
    def segment_hash(self) -> str:
        return segment_hash(self.recipients)


async def evaluate_message(
    store: BulkStore, ids: Sequence[int], *, channels: Sequence[str], email_enabled: bool
) -> MessageEvaluation:
    rows = await store.classify_rows(ids)
    recipients: list[int] = []
    skipped: dict[int, Category] = {}
    by_channel = {
        "telegram": 0,
        "telegram_bot_blocked": 0,
        "push_only": 0,
        "email_only": 0,
        "cabinet_only": 0,
        "unreachable": 0,
    }
    for uid in sorted(int(i) for i in ids):
        row = rows.get(uid)
        if row is None:
            continue
        if str(row.get("role") or "") != "USER":
            skipped[uid] = Category.STAFF
            continue
        if row.get("is_blocked"):
            skipped[uid] = Category.BLOCKED
            continue
        recipients.append(uid)
        route = message_route(row, channels, email_enabled)
        by_channel[route] += 1
        if route != "telegram" and row.get("telegram_id") is not None and row.get("is_bot_blocked"):
            by_channel["telegram_bot_blocked"] += 1
    return MessageEvaluation(matched=len(ids), recipients=recipients, skipped=skipped, by_channel=by_channel)


def message_items(ev: MessageEvaluation) -> list[tuple[int, str, Optional[str], bool]]:
    out = [(uid, "PENDING", None, False) for uid in ev.recipients]
    out += [(uid, "SKIPPED", cat.value, False) for uid, cat in ev.skipped.items()]
    return sorted(out)


def message_preview_payload(
    ev: MessageEvaluation, *, email_enabled: bool, active_job_id: Optional[int]
) -> dict[str, Any]:
    counts = Counter(cat.value for cat in ev.skipped.values())
    return {
        "matched": ev.matched,
        "recipients": len(ev.recipients),
        "skipped": {Category.BLOCKED.value: counts.get("BLOCKED", 0), Category.STAFF.value: counts.get("STAFF", 0)},
        "by_channel": dict(ev.by_channel),
        "email_enabled": bool(email_enabled),
        "segment_hash": ev.segment_hash,
        "active_job_id": active_job_id,
    }


def days_params_hash(filters: Mapping[str, Any], days: int, include_trial: bool, include_limited: bool, notify: Any) -> str:
    return params_hash(
        {
            "kind": KIND_DAYS,
            "filters": dict(filters),
            "days": days,
            "include_trial": include_trial,
            "include_limited": include_limited,
            "notify": notify,
        }
    )


def message_params_hash(
    filters: Optional[Mapping[str, Any]], source_job_id: Optional[int], content: str, channels: Sequence[str]
) -> str:
    return params_hash(
        {
            "kind": KIND_MESSAGE,
            "filters": dict(filters) if filters is not None else None,
            "source_job_id": source_job_id,
            "text_sha256": text_sha256(content),
            "channels": sorted(channels),
        }
    )


def job_to_dict(
    job: Mapping[str, Any], breakdown: Mapping[str, int], *, readonly: bool
) -> dict[str, Any]:
    params = job.get("params") or {}
    content = params.get("text")
    notify = params.get("notify") or {}
    preview_source = content if content is not None else notify.get("text")

    def iso(v: Any) -> Optional[str]:
        return v.isoformat() if isinstance(v, datetime) else v

    return {
        "id": int(job["id"]),
        "kind": job["kind"],
        "status": job["status"],
        "created_by": None if readonly else job.get("created_by_label"),
        "created_at": iso(job.get("created_at")),
        "started_at": iso(job.get("started_at")),
        "finished_at": iso(job.get("finished_at")),
        "total": int(job.get("total") or 0),
        "done": int(job.get("done_count") or 0),
        "applied": int(job.get("applied_count") or 0),
        "skipped": int(job.get("skipped_count") or 0),
        "failed": int(job.get("failed_count") or 0),
        "unknown": int(job.get("unknown_count") or 0),
        "verify_flagged": int(job.get("verify_flagged") or 0),
        "params": {
            "days": params.get("days"),
            "include_trial": params.get("include_trial"),
            "include_limited": params.get("include_limited"),
            "channels": params.get("channels") or notify.get("channels"),
            "text_preview": plain_text(preview_source)[:TEXT_PREVIEW] if preview_source else None,
        },
        "pause_reason": job.get("pause_reason"),
        "parent_job_id": job.get("parent_job_id"),
        "child_job_id": job.get("child_job_id"),
        "breakdown": dict(breakdown),
    }


def item_to_dict(item: Mapping[str, Any], *, readonly: bool) -> dict[str, Any]:
    def iso(v: Any) -> Optional[str]:
        return v.isoformat() if isinstance(v, datetime) else v

    return {
        "user_id": None if readonly else item.get("user_id"),
        "name": item.get("name"),
        "status": item.get("status"),
        "category": item.get("category"),
        "reason": reason_text(item.get("category"), item.get("error"), item.get("verify_note")),
        "old_expire_at": iso(item.get("old_expire_at")),
        "target_expire_at": iso(item.get("target_expire_at")),
        "channels": item.get("channels"),
        "verify_note": item.get("verify_note"),
        "error": None if readonly else item.get("error"),
    }
