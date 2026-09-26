"""Семейные профили: N профилей × D устройств к семейному тарифу владельца (overlay).

ЧТО ЭТО. Тариф, помеченный в админке как семейный («N профилей × D устройств на
профиль»), даёт владельцу подписки право завести до N профилей для близких. У каждого
профиля своя ссылка подписки, свои D устройств и ВЕСЬ трафик тарифа (лимит и
стратегия тарифа целиком, как у владельца). Участнику аккаунт не нужен: владелец
пересылает ему ссылку. Лимит устройств владельца профили НЕ трогают вовсе.

РЕШЕНИЯ ВЛАДЕЛЬЦА ПРОДУКТА (26.09):
  * отдельный семейный тариф, а не пул устройств владельца;
  * приглашения с привязкой к аккаунту — позже, сейчас только пересылка ссылки;
  * трафик каждому профилю — тарифный целиком.

ПОЧЕМУ ПРОФИЛЬ — «ТЕНЕВОЙ» АККАУНТ. У человека в боте одна текущая подписка, и
вебхуки панели ищут человека по uuid только через неё (`get_by_remna_uuid`). Вторую
подписку того же человека бот не видит: её не продлит вебхук, а синхрон панели
заведёт под неё безымянного двойника. Поэтому профиль — своя строка `users` (без
телеграма, почты и пароля — войти нельзя) со своей текущей подпиской и своим
пользователем панели `rs_fam_<id профиля в users>`. Так вебхуки находят профиль по
uuid, синхрон не заводит двойников, резерв его не выдаёт (нет своей оплаты), письма
и сообщения ему не уходят (нет ни почты, ни телеграма). Сброс устройств владельца
профили не задевает — ради этого всё и затевалось.

ИСТОЧНИК ПРАВДЫ — ТЕКУЩАЯ ПОДПИСКА ВЛАДЕЛЬЦА. Срок профиля в панели равен сроку
владельца, поэтому истекают они одновременно силами самой панели. Сверка всегда
«привести к равенству»: повтор безопасен, пропущенный проход доделает следующий.
Любое изменение — под замком строки владельца `users … FOR UPDATE` (тем же, что у
докупки устройства и переноса остатка), поэтому создание, удаление, продление и
смена тарифа одного человека взаимно последовательны.

ТУМБЛЕР (assets/family.json) гасит создание профилей и их показ людям. Сверку он
НЕ гасит: уже выданные профили обязаны приостановиться вместе с владельцем, а не
жить бесплатно, пока функция выключена. Пока функция не включалась ни разу, профилей
нет, и сверке нечего трогать.

Модульный уровень — только stdlib/sqlalchemy/loguru: модуль импортирует правка
шлюза посреди денежного пути (урок overlay_topup). Всё остальное — лениво.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import unicodedata
import uuid as uuid_lib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Optional, Sequence

from loguru import logger
from sqlalchemy import text

if TYPE_CHECKING:  # pragma: no cover — только для подсказок типов
    from sqlalchemy.ext.asyncio import AsyncSession

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
CONFIG_PATH = ASSETS_DIR / "family.json"
ORPHANS_STATE_PATH = ASSETS_DIR / "family_orphans_state.json"

DEFAULT_CONFIG: dict[str, Any] = {
    # Выключено, пока владелец не откроет функцию в админке: выкатка образа сама по
    # себе не должна начать выдавать людям новые подписки.
    "enabled": False,
    # Сколько дней живёт приостановленный профиль, прежде чем удалиться: владелец
    # сменил тариф на обычный или удалил подписку — у него есть месяц передумать.
    "suspend_grace_days": 30,
}
_GRACE_BOUNDS = (1, 365)

# Пределы условий тарифа — те же, что CHECK в миграции 0016.
TERMS_BOUNDS = (1, 10)

# Синтетический «тариф» в снимке подписки профиля. Настоящие тарифы положительные,
# служебные: −1 импорт, −2 пополнение, −3 подарок, −4 докупка устройства, −5 трафик.
# Отрицательный id не пускает профиль ни в выборки «по тарифу», ни в MRR.
FAMILY_PLAN_ID = -6

USERNAME_PREFIX = "rs_fam_"
REFERRAL_PREFIX = "fam_"
LABEL_MAX = 24

# Строку «создаю/удаляю» крон трогает не раньше двух минут: прямо сейчас её, возможно,
# доводит веб-процесс, который ждёт ответа панели.
PENDING_GRACE_MINUTES = 2
# Секунда расхождения срока — не расхождение: панель отдаёт срок без долей секунды.
EXPIRE_TOLERANCE = timedelta(seconds=1)
# Сколько ждём панель, собирая витрину профилей (устройства, трафик) для показа.
VIEW_TIMEOUT_SECONDS = 8.0

LIVE_STATUSES = ("creating", "active", "suspended")
# Причины, по которым приостановленный профиль через `suspend_grace_days` удаляется.
# Пауза и блокировка — временные состояния владельца, их профили ждут без срока.
# `panel_missing` — панель ПОДТВЕРДИЛА, что пользователя профиля у неё нет: удаляем не
# сразу, а через ту же отсрочку (вдруг сопоставление uuid починят и он найдётся).
GRACE_REASONS = ("plan", "owner_gone", "panel_missing")
SUSPEND_REASONS = (
    "plan", "owner_expired", "owner_frozen", "owner_gone", "owner_blocked", "panel_missing",
)


# ── конфиг ──────────────────────────────────────────────────────────────────


def _norm_int(raw: Any, default: int, bounds: tuple[int, int]) -> int:
    low, high = bounds
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return min(max(value, low), high)


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": bool(data.get("enabled", False)),
        "suspend_grace_days": _norm_int(
            data.get("suspend_grace_days"), DEFAULT_CONFIG["suspend_grace_days"], _GRACE_BOUNDS
        ),
    }


def load_config() -> dict[str, Any]:
    """Конфиг на каждый вызов: админка правит его на лету."""
    try:
        data = json.loads(CONFIG_PATH.read_text("utf-8"))
    except FileNotFoundError:
        return dict(DEFAULT_CONFIG)
    except Exception as exc:  # noqa: BLE001 — битый конфиг не имеет права ронять оплату
        logger.warning(f"family: конфиг не прочитан ({exc}) — беру дефолт (выключено)")
        return dict(DEFAULT_CONFIG)
    if not isinstance(data, dict):
        return dict(DEFAULT_CONFIG)
    return _normalize(data)


def save_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize(config)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), "utf-8")
    return normalized


# ── данные ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Terms:
    max_profiles: int
    devices_per_profile: int


def normalize_terms(max_profiles: Any, devices_per_profile: Any) -> Terms:
    """Условия тарифа из админки. Вне 1…10 — ValueError, а не молчаливая подрезка:
    владелец должен увидеть, что его число не принято."""
    low, high = TERMS_BOUNDS
    try:
        m, d = int(max_profiles), int(devices_per_profile)
    except (TypeError, ValueError) as exc:
        raise ValueError("условия семейного тарифа — целые числа") from exc
    if not (low <= m <= high) or not (low <= d <= high):
        raise ValueError(f"профилей и устройств на профиль — от {low} до {high}")
    return Terms(max_profiles=m, devices_per_profile=d)


@dataclass(frozen=True)
class OwnerState:
    """Всё, из чего выводится желаемое состояние профилей. Читается под замком."""

    user_id: int
    exists: bool = True
    telegram_id: Optional[int] = None
    is_blocked: bool = False
    language: Optional[str] = None
    sub_id: Optional[int] = None
    sub_status: Optional[str] = None
    is_trial: bool = False
    expire_at: Optional[datetime] = None
    plan: Optional[dict] = None
    sub_updated_at: Optional[datetime] = None
    frozen: bool = False
    reserve_expire_at: Optional[datetime] = None
    terms: Optional[Terms] = None

    @property
    def plan_id(self) -> Optional[int]:
        return _as_int((self.plan or {}).get("id"))

    @property
    def plan_name(self) -> str:
        return str((self.plan or {}).get("name") or "")

    @property
    def on_reserve(self) -> bool:
        """Живёт на бесплатном резерве, а не на оплаченном сроке.

        Резерв двигает срок строки вперёд и оставляет статус ACTIVE — без этой
        проверки истёкший владелец выглядел бы платящим, и семья получила бы
        бесплатные дни. Купил во время резерва — срок уходит дальше конца резерва.
        """
        if self.reserve_expire_at is None or self.expire_at is None:
            return False
        return self.expire_at <= self.reserve_expire_at + timedelta(hours=1)

    @property
    def remna_name(self) -> str:
        """Имя владельца в панели — для описания профиля («Семья rs_…»)."""
        if self.telegram_id is not None:
            return f"rs_{self.telegram_id}"
        return f"rs_web_{self.user_id}"


@dataclass(frozen=True)
class ProfileRow:
    id: int
    owner_user_id: int
    profile_user_id: Optional[int]
    request_id: str
    label: str
    status: str
    suspend_reason: Optional[str]
    device_limit: int
    panel_username: Optional[str]
    panel_uuid: Optional[str]
    suspended_at: Optional[datetime]
    last_reconciled_at: Optional[datetime]
    fail_count: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    # Текущая подписка теневого аккаунта — зеркало панели (её правят и вебхуки).
    sub_id: Optional[int] = None
    sub_status: Optional[str] = None
    sub_expire_at: Optional[datetime] = None
    sub_device_limit: Optional[int] = None
    sub_traffic_limit: Optional[int] = None
    sub_strategy: Optional[str] = None
    sub_squads: tuple[str, ...] = ()
    sub_external_squad: Optional[str] = None
    sub_url: Optional[str] = None
    sub_remna_id: Optional[str] = None
    sub_device_reset_at: Optional[datetime] = None
    # Когда профиль в последний раз получил свежий трафик (создание или обнуление).
    traffic_reset_at: Optional[datetime] = None

    @property
    def remna_uuid(self) -> Optional[str]:
        return self.sub_remna_id or self.panel_uuid

    @property
    def panel_missing(self) -> bool:
        """Подписки у теневого аккаунта больше нет: пользователя панели удалили."""
        return self.sub_id is None or (self.sub_status or "").upper() == "DELETED"


@dataclass(frozen=True)
class Target:
    """Каким профиль должен быть в панели, пока владелец платит."""

    expire_at: datetime
    device_limit: int
    traffic_limit_gb: int
    strategy: str
    internal_squads: tuple[str, ...]
    external_squad: Optional[str]
    tag: Optional[str] = None


@dataclass(frozen=True)
class Decision:
    profile_id: int
    action: str  # noop | sync | resume | suspend | relabel | delete | gone
    reason: Optional[str] = None
    target: Optional[Target] = None
    reset: bool = False


# ── чистые функции ──────────────────────────────────────────────────────────


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# Невидимые символы, которыми подделывают имя: управление направлением текста
# (U+202A…U+202E, U+2066…U+2069), нулевые пробелы и метки. U+200D (склейка эмодзи
# «👨‍👩‍👧») оставляем: без него семейный смайлик распадается на три лица.
_INVISIBLE = {0x061C, 0x200B, 0x200C, 0x200E, 0x200F, 0xFEFF, 0x2060, 0x2061, 0x2062, 0x2063, 0x2064}
_INVISIBLE.update(range(0x202A, 0x202F))
_INVISIBLE.update(range(0x2066, 0x206A))


def clean_label(raw: Any) -> Optional[str]:
    """Имя профиля: обрезать, убрать управляющие и невидимые символы. Пусто — None.

    Экранирует показывающий (бот — html.escape, кабинет — React); здесь только то,
    что опасно везде: перевод строки в имени ломает раскладку сообщения, а символ
    смены направления текста выдаёт «Мама» за что угодно.
    """
    if not isinstance(raw, str):
        return None
    value = unicodedata.normalize("NFC", raw)
    kept = []
    for ch in value:
        if unicodedata.category(ch) == "Cc" or ord(ch) in _INVISIBLE:
            kept.append(" ")
            continue
        kept.append(ch)
    value = " ".join("".join(kept).split())
    value = value[:LABEL_MAX].strip()
    return value or None


def panel_username(profile_user_id: int) -> str:
    return f"{USERNAME_PREFIX}{int(profile_user_id)}"


def is_family_username(name: Any) -> bool:
    return isinstance(name, str) and name.startswith(USERNAME_PREFIX)


def owner_condition(owner: OwnerState, now: datetime) -> str:
    """Что сейчас с владельцем с точки зрения семьи.

    "ok" — платит, тариф семейный: профили живут. "expired" — срок кончился или он на
    резерве: профили истекают сами, ничего не копируем. Остальное — причина
    приостановки. Порядок проверок — часть смысла: блокировка важнее срока, пауза
    важнее тарифа.
    """
    status = (owner.sub_status or "").upper()
    if not owner.exists:
        return "owner_gone"
    if owner.is_blocked:
        return "owner_blocked"
    if owner.sub_id is None or status == "DELETED":
        return "owner_gone"
    if owner.frozen or status == "DISABLED":
        return "owner_frozen"
    if owner.on_reserve:
        return "expired"
    if owner.expire_at is None or owner.expire_at <= now or status == "EXPIRED":
        return "expired"
    if owner.is_trial or owner.terms is None:
        return "plan"
    return "ok"


def create_eligibility(
    owner: OwnerState, config: dict[str, Any], live_count: int, now: datetime
) -> Optional[str]:
    """Почему НЕЛЬЗЯ завести профиль (код причины) или None."""
    if not config.get("enabled"):
        return "disabled"
    if not owner.exists or owner.sub_id is None:
        return "no_subscription"
    if owner.is_blocked:
        return "blocked"
    if owner.is_trial:
        return "trial"
    if owner.terms is None:
        return "not_family"
    if owner.frozen:
        return "frozen"
    if owner.on_reserve:
        return "reserve"
    # LIMITED — у владельца кончился СВОЙ трафик; у профиля он свой, семью это не
    # останавливает. Истёкший, отключённый и удалённый — нет.
    status = (owner.sub_status or "").upper()
    if status not in ("ACTIVE", "LIMITED") or owner.expire_at is None or owner.expire_at <= now:
        return "not_active"
    if live_count >= owner.terms.max_profiles:
        return "max_reached"
    return None


def target_for(owner: OwnerState) -> Target:
    """Желаемое состояние профиля: срок владельца, тарифные трафик и сквады.

    Трафик — из СНИМКА ТАРИФА, а не из строки подписки: в строке может лежать
    докупленный владельцем трафик, а он оплачен им для себя. Устройства — условие
    семейного тарифа и НИКОГДА не 0: в панели 0 — это безлимит устройств.
    """
    if owner.expire_at is None or owner.terms is None:
        raise ValueError("у владельца нет срока или тариф не семейный")
    plan = owner.plan or {}
    squads = tuple(sorted(str(s).lower() for s in (plan.get("internal_squads") or []) if s))
    external = plan.get("external_squad")
    tag = plan.get("tag") or None
    if tag is not None:
        tag = str(tag)
        # IMPORTED — метка импорта из панели: с ней вебхук создания завёл бы
        # профилю отдельный аккаунт. Метка вне формата панели — её не примут.
        if tag == "IMPORTED" or not _tag_ok(tag):
            tag = None
    return Target(
        expire_at=owner.expire_at,
        device_limit=max(1, int(owner.terms.devices_per_profile)),
        traffic_limit_gb=max(0, _as_int(plan.get("traffic_limit")) or 0),
        strategy=str(plan.get("traffic_limit_strategy") or "NO_RESET").upper(),
        internal_squads=squads,
        external_squad=str(external).lower() if external else None,
        tag=tag,
    )


def _tag_ok(tag: str) -> bool:
    """Метка в формате панели: заглавные латинские, цифры и «_», не длиннее 16."""
    return 0 < len(tag) <= 16 and all(c.isascii() and (c.isupper() or c.isdigit() or c == "_") for c in tag)


def needs_sync(profile: ProfileRow, target: Target) -> bool:
    """Расходится ли зеркало профиля с желаемым. Равно — в панель не ходим."""
    if profile.sub_expire_at is None or abs(profile.sub_expire_at - target.expire_at) > EXPIRE_TOLERANCE:
        return True
    if profile.sub_device_limit != target.device_limit:
        return True
    if (profile.sub_traffic_limit or 0) != target.traffic_limit_gb:
        return True
    if (profile.sub_strategy or "").upper() != target.strategy:
        return True
    if tuple(sorted(s.lower() for s in profile.sub_squads)) != target.internal_squads:
        return True
    return (profile.sub_external_squad or None) != target.external_squad


def suspension_order(profiles: Iterable[ProfileRow]) -> list[ProfileRow]:
    """Кого приостанавливать первым: самые НОВЫЕ. Старые профили семья уже раздала."""
    return sorted(profiles, key=lambda p: (p.created_at, p.id), reverse=True)


def plan_decisions(
    owner: OwnerState,
    profiles: Sequence[ProfileRow],
    now: datetime,
    grace_days: int,
    *,
    reset_traffic: bool = False,
    purchase_at: Optional[datetime] = None,
) -> list[Decision]:
    """Таблица решений по каждому живому профилю. Ни базы, ни панели — только логика.

    `reset_traffic` — хук оплаты ЗНАЕТ, что владелец только что купил период: трафик
    профилей обнуляется так же, как база обнуляет его владельцу. `purchase_at` — то же
    для крона: оплата позже последней сверки профиля значит, что хук не дошёл.
    """
    live = [p for p in profiles if p.status in ("active", "suspended")]
    condition = owner_condition(owner, now)
    grace = timedelta(days=max(1, int(grace_days)))
    out: list[Decision] = []

    def expired_grace(p: ProfileRow, reason: str) -> bool:
        return (
            reason in GRACE_REASONS
            and p.suspend_reason == reason
            and p.suspended_at is not None
            and p.suspended_at + grace <= now
        )

    def still_on(p: ProfileRow) -> bool:
        # Мы профиль выключили (намерение записано), а панель говорит «работает»:
        # выключение не дошло, или профиль включили руками. Выключаем снова —
        # приостановленный профиль не должен давать доступ.
        return (p.sub_status or "").upper() in ("ACTIVE", "LIMITED")

    def stay_suspended(p: ProfileRow, reason: str) -> Decision:
        if p.status == "suspended" and p.suspend_reason == "panel_missing":
            # Пользователя в панели нет — выключать и переименовывать причину нечего;
            # причину не меняем, чтобы не сдвинуть отсрочку удаления.
            if expired_grace(p, "panel_missing"):
                return Decision(p.id, "delete", reason="panel_missing")
            return Decision(p.id, "noop", reason="panel_missing")
        if p.status == "active":
            return Decision(p.id, "suspend", reason=reason)
        if p.suspend_reason != reason:
            return Decision(p.id, "relabel", reason=reason)
        if expired_grace(p, reason):
            return Decision(p.id, "delete", reason=reason)
        if still_on(p):
            return Decision(p.id, "suspend", reason=reason)
        return Decision(p.id, "noop", reason=reason)

    for p in live:
        if p.panel_missing:
            out.append(Decision(p.id, "gone"))

    alive = [p for p in live if not p.panel_missing]

    if condition == "expired":
        for p in alive:
            if p.status == "active":
                # Срок профиля обязан кончаться вместе со сроком владельца. Если он
                # живёт дольше (владельцу срок сократили), а в прошлое панель дату
                # не примет — выключаем.
                if p.sub_expire_at is not None and p.sub_expire_at > now + EXPIRE_TOLERANCE:
                    out.append(Decision(p.id, "suspend", reason="owner_expired"))
                else:
                    out.append(Decision(p.id, "noop"))
            elif p.suspend_reason and expired_grace(p, p.suspend_reason):
                out.append(Decision(p.id, "delete", reason=p.suspend_reason))
            elif still_on(p) and p.suspend_reason != "panel_missing":
                out.append(Decision(p.id, "suspend", reason=p.suspend_reason or "owner_expired"))
            else:
                out.append(Decision(p.id, "noop", reason=p.suspend_reason))
        return out

    if condition != "ok":
        for p in alive:
            out.append(stay_suspended(p, condition))
        return out

    target = target_for(owner)
    ordered = sorted(alive, key=lambda p: (p.created_at, p.id))
    keep = ordered[: owner.terms.max_profiles] if owner.terms else []
    excess = ordered[len(keep):]
    for p in keep:
        if p.status == "suspended" and p.suspend_reason == "panel_missing" and expired_grace(
            p, "panel_missing"
        ):
            out.append(Decision(p.id, "delete", reason="panel_missing"))
            continue
        # Оплата позже последнего свежего трафика профиля — значит, хук оплаты не
        # дошёл, и обнулить должен крон. Отметка — именно трафика, а не сверки:
        # сверку сбрасывают и выключение, и только что заведённый профиль.
        fresh_at = p.traffic_reset_at or p.created_at
        reset = reset_traffic or (
            purchase_at is not None and fresh_at is not None and purchase_at > fresh_at
        )
        if p.status == "suspended":
            out.append(Decision(p.id, "resume", target=target, reset=reset))
        elif reset or needs_sync(p, target):
            out.append(Decision(p.id, "sync", target=target, reset=reset))
        else:
            out.append(Decision(p.id, "noop"))
    for p in excess:
        out.append(stay_suspended(p, "plan"))
    return out


def family_snapshot(owner: OwnerState, device_limit: int, traffic_gb: int, strategy: str) -> Any:
    """Снимок «тарифа» подписки профиля. Ленивый импорт: application-слой не модульный."""
    from remnapy.enums import TrafficLimitStrategy

    from src.application.dto import PlanSnapshotDto
    from src.core.utils.converters import limits_to_plan_type

    name = f"Семья · {owner.plan_name}" if owner.plan_name else "Семья"
    return PlanSnapshotDto(
        id=FAMILY_PLAN_ID,
        name=name[:120],
        type=limits_to_plan_type(int(traffic_gb), int(device_limit)),
        traffic_limit_strategy=TrafficLimitStrategy(strategy),
        traffic_limit=int(traffic_gb),
        device_limit=int(device_limit),
        duration=0,
        is_trial=False,
    )


# ── SQL ─────────────────────────────────────────────────────────────────────

# Состояние владельца — БЕЗ замка строки users. Раньше семья запирала `users … FOR
# UPDATE` и держала замок, пока ходила в панель; база же при покупке (CHANGE/NEW)
# сначала меняет панель, а потом пишет в эту самую строку. Медленная панель и пять
# профилей — и оплата владельца упиралась в statement_timeout, падала PurchaseError,
# а панель уже была изменена. Очередь семьи держит свой замок (family_lock ниже),
# чужих строк семья не запирает вовсе. LEFT JOIN — владелец без подписки не должен
# проваливать запрос.
OWNER_READ_SQL = (
    "SELECT u.id, u.telegram_id, u.is_blocked, u.language::text, "
    "s.id, s.status::text, s.is_trial, s.expire_at, s.plan_snapshot, s.updated_at "
    "FROM users u LEFT JOIN subscriptions s ON s.id = u.current_subscription_id "
    "WHERE u.id = :uid"
)

# Очередь семьи одного владельца: рекомендательный замок транзакции с парой ключей
# (пространство семьи, id владельца). Его берут ТОЛЬКО семейные операции — создание,
# удаление, сброс устройств, сверка, удаление владельца, — поэтому ожидание здесь
# никогда не задерживает оплату, докупку или вебхук базы. Пространство «пара int4»
# у Postgres отдельное от одиночного bigint-ключа миграций (overlay_app).
FAMILY_LOCK_NS = 0x46414D49  # «FAMI»
FAMILY_LOCK_SQL = "SELECT pg_advisory_xact_lock(:ns, :owner)"
FAMILY_TRY_LOCK_SQL = "SELECT pg_try_advisory_xact_lock(:ns, :owner)"
# Сколько ждём очередь семьи. Дольше — «занято, повторите»: ждать дольше значит
# держать веб-запрос человека, пока крон ходит в медленную панель.
LOCK_WAIT_MS = 5_000
# Хук оплаты не ждёт очередь вовсе и укладывается в общий дедлайн: не успел —
# доведёт крон (оплата позже последней сверки профиля = «обнулить трафик»).
HOOK_DEADLINE_SECONDS = 15.0

TERMS_SQL = "SELECT max_profiles, devices_per_profile FROM family_plan_terms WHERE plan_id = :pid"

# Пауза и резерв одним запросом — оба меняют судьбу семьи (см. owner_condition).
PAUSE_RESERVE_SQL = (
    "SELECT EXISTS (SELECT 1 FROM subscription_freezes WHERE user_id = :uid AND active = true), "
    "(SELECT max(reserve_expire_at) FROM reserve_grants "
    " WHERE user_id = :uid AND ended = false AND reserve_expire_at > now())"
)

PROFILES_SQL = (
    "SELECT fp.id, fp.owner_user_id, fp.profile_user_id, fp.request_id::text, fp.label, "
    "fp.status, fp.suspend_reason, fp.device_limit, fp.panel_username, fp.panel_uuid::text, "
    "fp.suspended_at, fp.last_reconciled_at, fp.fail_count, fp.created_at, fp.updated_at, "
    "s.id, s.status::text, s.expire_at, s.device_limit, s.traffic_limit, "
    "s.traffic_limit_strategy::text, s.internal_squads::text[], s.external_squad::text, "
    "s.url, s.user_remna_id::text, s.device_all_reset_at, fp.traffic_reset_at "
    "FROM family_profiles fp "
    "LEFT JOIN users pu ON pu.id = fp.profile_user_id "
    "LEFT JOIN subscriptions s ON s.id = pu.current_subscription_id "
    "WHERE fp.owner_user_id = :uid AND fp.status <> 'failed' "
    "ORDER BY fp.created_at, fp.id"
)
PROFILE_BY_ID_SQL = PROFILES_SQL.replace(
    "WHERE fp.owner_user_id = :uid AND fp.status <> 'failed' ", "WHERE fp.id = :pid "
)

PROFILE_BY_REQUEST_SQL = (
    "SELECT id, owner_user_id, status, label FROM family_profiles WHERE request_id = :rid"
)

# Последняя НАСТОЯЩАЯ покупка владельца: синтетические снимки (пополнение, подарок,
# докупки — id < 0) период не продлевают и трафик не обнуляют. CASE, а не голый ::int:
# нечисловой id уронил бы всю сверку, а не одну строку.
LAST_PURCHASE_SQL = (
    "SELECT max(t.updated_at) FROM transactions t "
    "WHERE t.user_id = :uid AND t.status::text = 'COMPLETED' AND t.is_test = false "
    "AND (CASE WHEN t.plan_snapshot->>'id' ~ '^-?[0-9]+$' "
    "     THEN (t.plan_snapshot->>'id')::int ELSE 0 END) > 0"
)

# Семьи, у которых что-то поменялось после последней сверки: подписка владельца
# (продление, смена тарифа, пауза — всё это меняет её строку), сам владелец
# (блокировка, удаление подписки снимает current_subscription_id) и условия тарифа.
# Раз в час крон сверяет всех (ALL_OWNERS_SQL) — это страховка от того, что сюда
# не попадёт.
CHANGED_OWNERS_SQL = (
    "SELECT DISTINCT fp.owner_user_id FROM family_profiles fp "
    "JOIN users u ON u.id = fp.owner_user_id "
    "LEFT JOIN subscriptions s ON s.id = u.current_subscription_id "
    "LEFT JOIN family_plan_terms ft ON ft.plan_id = "
    "  (CASE WHEN s.plan_snapshot->>'id' ~ '^-?[0-9]+$' THEN (s.plan_snapshot->>'id')::int END) "
    "WHERE fp.status IN ('active', 'suspended') AND ("
    "  fp.last_reconciled_at IS NULL "
    "  OR s.updated_at > fp.last_reconciled_at "
    "  OR u.updated_at > fp.last_reconciled_at "
    "  OR ft.updated_at > fp.last_reconciled_at) "
    "LIMIT :lim"
)
ALL_OWNERS_SQL = (
    "SELECT DISTINCT owner_user_id FROM family_profiles "
    "WHERE status IN ('active', 'suspended') LIMIT :lim"
)
PENDING_SQL = (
    "SELECT id, owner_user_id, status FROM family_profiles "
    "WHERE status IN ('creating', 'deleting') "
    "AND updated_at < now() - make_interval(mins => :grace) "
    "ORDER BY updated_at LIMIT :lim"
)
HAS_ANY_SQL = "SELECT EXISTS (SELECT 1 FROM family_profiles)"


def _row_profile(r: Any) -> ProfileRow:
    return ProfileRow(
        id=int(r[0]),
        owner_user_id=int(r[1]),
        profile_user_id=int(r[2]) if r[2] is not None else None,
        request_id=str(r[3]),
        label=str(r[4]),
        status=str(r[5]),
        suspend_reason=r[6],
        device_limit=int(r[7]),
        panel_username=r[8],
        panel_uuid=r[9],
        suspended_at=r[10],
        last_reconciled_at=r[11],
        fail_count=int(r[12] or 0),
        created_at=r[13],
        updated_at=r[14],
        sub_id=int(r[15]) if r[15] is not None else None,
        sub_status=r[16],
        sub_expire_at=r[17],
        sub_device_limit=int(r[18]) if r[18] is not None else None,
        sub_traffic_limit=int(r[19]) if r[19] is not None else None,
        sub_strategy=r[20],
        sub_squads=tuple(str(s) for s in (r[21] or ())),
        sub_external_squad=r[22],
        sub_url=r[23],
        sub_remna_id=r[24],
        sub_device_reset_at=r[25],
        traffic_reset_at=r[26],
    )


def _plan_dict(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except ValueError:
            return {}
    return {}


class FamilyBusy(RuntimeError):
    """Очередь семьи занята дольше LOCK_WAIT_MS (крон или соседняя вкладка в панели)."""


def _lock_not_available(exc: BaseException) -> bool:
    """SQLSTATE 55P03 (lock_timeout) — ищем в обёртках SQLAlchemy и asyncpg."""
    seen: set[int] = set()
    stack: list[Any] = [exc]
    while stack:
        err = stack.pop()
        if err is None or id(err) in seen or len(seen) > 20:
            continue
        seen.add(id(err))
        if type(err).__name__ == "LockNotAvailableError":
            return True
        if "55P03" in (getattr(err, "sqlstate", None), getattr(err, "pgcode", None)):
            return True
        stack.extend(
            (getattr(err, "orig", None), getattr(err, "__cause__", None), getattr(err, "__context__", None))
        )
    return False


async def family_lock(
    session: "AsyncSession", owner_id: int, *, wait_ms: Optional[int] = None
) -> None:
    """Встать в очередь семьи владельца до конца транзакции. Занято — FamilyBusy.

    `wait_ms=0` — не ждать вовсе (хук оплаты). `lock_timeout` остаётся на всю
    семейную транзакцию: ни одна наша запись не повиснет на чужом замке строки
    дольше того же срока. Замок отпускает commit или rollback.
    """
    params = {"ns": FAMILY_LOCK_NS, "owner": int(owner_id)}
    if wait_ms is None:
        wait_ms = LOCK_WAIT_MS
    timeout_ms = int(wait_ms) if wait_ms > 0 else LOCK_WAIT_MS
    await session.execute(text(f"SET LOCAL lock_timeout = '{timeout_ms}ms'"))
    if wait_ms <= 0:
        got = (await session.execute(text(FAMILY_TRY_LOCK_SQL), params)).scalar()
        if not got:
            await session.rollback()
            raise FamilyBusy(f"семья владельца {owner_id} сейчас обновляется")
        return
    try:
        await session.execute(text(FAMILY_LOCK_SQL), params)
    except Exception as exc:  # noqa: BLE001 — узнаём по SQLSTATE, остальное — наверх
        if not _lock_not_available(exc):
            raise
        await session.rollback()
        raise FamilyBusy(f"семья владельца {owner_id} сейчас обновляется") from exc


async def load_owner(session: "AsyncSession", owner_id: int) -> OwnerState:
    """Всё состояние владельца сырым SQL — без замка его строки (см. OWNER_READ_SQL).

    Сырым, а не через ORM: сессия живёт с `expire_on_commit=False`, и объект из
    identity map оказался бы устаревшим снимком.
    """
    row = (await session.execute(text(OWNER_READ_SQL), {"uid": owner_id})).first()
    if row is None:
        return OwnerState(user_id=owner_id, exists=False)
    plan = _plan_dict(row[8])
    terms = None
    plan_id = _as_int(plan.get("id"))
    if row[4] is not None and plan_id is not None and plan_id > 0:
        t = (await session.execute(text(TERMS_SQL), {"pid": plan_id})).first()
        if t is not None:
            terms = Terms(max_profiles=int(t[0]), devices_per_profile=int(t[1]))
    pause = (await session.execute(text(PAUSE_RESERVE_SQL), {"uid": owner_id})).first()
    return OwnerState(
        user_id=int(row[0]),
        telegram_id=int(row[1]) if row[1] is not None else None,
        is_blocked=bool(row[2]),
        language=row[3],
        sub_id=int(row[4]) if row[4] is not None else None,
        sub_status=row[5],
        is_trial=bool(row[6]),
        expire_at=row[7],
        plan=plan,
        sub_updated_at=row[9],
        frozen=bool(pause[0]) if pause else False,
        reserve_expire_at=pause[1] if pause else None,
        terms=terms,
    )


async def load_profiles(session: "AsyncSession", owner_id: int) -> list[ProfileRow]:
    rows = (await session.execute(text(PROFILES_SQL), {"uid": owner_id})).all()
    return [_row_profile(r) for r in rows]


async def load_profile(session: "AsyncSession", profile_id: int) -> Optional[ProfileRow]:
    row = (await session.execute(text(PROFILE_BY_ID_SQL), {"pid": int(profile_id)})).first()
    return _row_profile(row) if row is not None else None


async def profile_by_request(session: "AsyncSession", request_id: Any) -> Optional[dict]:
    row = (await session.execute(text(PROFILE_BY_REQUEST_SQL), {"rid": str(request_id)})).first()
    if row is None:
        return None
    return {"id": int(row[0]), "owner_user_id": int(row[1]), "status": row[2], "label": row[3]}


async def last_purchase_at(session: "AsyncSession", owner_id: int) -> Optional[datetime]:
    return (await session.execute(text(LAST_PURCHASE_SQL), {"uid": owner_id})).scalar()


async def has_any_profiles(session: "AsyncSession") -> bool:
    return bool((await session.execute(text(HAS_ANY_SQL))).scalar())


async def _event(
    session: "AsyncSession",
    owner_id: int,
    profile_id: Optional[int],
    kind: str,
    actor: str,
    details: Optional[dict] = None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO family_events (owner_user_id, profile_id, kind, actor, details) "
            "VALUES (:o, :p, :k, :a, CAST(:d AS jsonb))"
        ),
        {
            "o": owner_id,
            "p": profile_id,
            "k": kind[:24],
            "a": actor[:16],
            "d": json.dumps(details or {}, ensure_ascii=False, default=str),
        },
    )


# ── панель ──────────────────────────────────────────────────────────────────


class PanelError(RuntimeError):
    """Панель ответила ошибкой или не тем, что просили."""


class PanelGone(PanelError):
    """Пользователя панели нет (удалили руками или он так и не был создан)."""


def _is_not_found(exc: BaseException) -> bool:
    return type(exc).__name__ == "NotFoundError" or getattr(exc, "status_code", None) == 404


def _is_rejection(exc: BaseException) -> bool:
    """Панель ТОЧНО не создала пользователя: ответила отказом или запрос не ушёл.

    4xx — панель ответила и отказала (кроме 408: это «не дождалась»). Ошибка разбора
    тела у нас — запрос не отправлялся вовсе. Таймаут, обрыв и 5xx — не знаем:
    панель могла создать пользователя уже после нашего вопроса.
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return 400 <= status < 500 and status != 408
    return type(exc).__name__ == "ValidationError"


def _is_conflict(exc: BaseException) -> bool:
    return type(exc).__name__ == "ConflictError" or getattr(exc, "status_code", None) == 409


def _status_text(value: Any) -> str:
    return str(getattr(value, "value", value) or "").upper()


def _uuid(value: Any) -> uuid_lib.UUID:
    return value if isinstance(value, uuid_lib.UUID) else uuid_lib.UUID(str(value))


def _squad_uuids(values: Iterable[str]) -> list[uuid_lib.UUID]:
    return [_uuid(v) for v in values]


def _need(sdk: Any) -> Any:
    """Клиент панели или PanelError: без панели семью не изменить, но и падать нечем."""
    if sdk is None:
        raise PanelError("панель недоступна")
    return sdk


def _gb_to_bytes(gb: int) -> int:
    from src.core.utils.converters import gb_to_bytes

    return int(gb_to_bytes(int(gb)))


async def panel_find(sdk: Any, username: str) -> Optional[Any]:
    """Пользователь панели по имени. None — его там нет; иная ошибка — PanelError.

    Имя `rs_fam_<id>` — единственное, что у нас есть, когда ответ на создание
    потерялся: по нему и выясняем, создала ли панель профиль.
    """
    _need(sdk)
    try:
        return await sdk.users.get_user_by_username(username)
    except Exception as exc:  # noqa: BLE001 — наверх одна понятная ошибка
        if _is_not_found(exc):
            return None
        raise PanelError(f"{type(exc).__name__}: {exc}") from exc


async def panel_presence(sdk: Any, p: "ProfileRow") -> Optional[Any]:
    """Есть ли пользователь профиля в панели. None — ТОЧНО нет; PanelError — не знаем.

    «Не найден» от панели ещё не значит «удалён»: слой панели 3.x отвечает им же на
    uuid, который не удалось сопоставить с числовым id, и на любой 404 пути. Поэтому
    «нет» — только когда панель не знает профиль ни по имени `rs_fam_<id>`, ни по uuid.
    """
    if p.panel_username:
        found = await panel_find(sdk, p.panel_username)
        if found is not None:
            return found
    if p.remna_uuid:
        try:
            return await _need(sdk).users.get_user_by_uuid(_uuid(p.remna_uuid))
        except Exception as exc:  # noqa: BLE001 — наверх одна понятная ошибка
            if not _is_not_found(exc):
                raise PanelError(f"{type(exc).__name__}: {exc}") from exc
    return None


async def panel_create(
    sdk: Any, *, username: str, target: Target, description: str
) -> Any:
    from remnapy.enums import TrafficLimitStrategy
    from remnapy.models import CreateUserRequestDto

    body: dict[str, Any] = dict(
        username=username,
        expire_at=target.expire_at,
        traffic_limit_strategy=TrafficLimitStrategy(target.strategy),
        traffic_limit_bytes=_gb_to_bytes(target.traffic_limit_gb),
        hwid_device_limit=int(target.device_limit),
        description=description,
        active_internal_squads=_squad_uuids(target.internal_squads),
    )
    if target.external_squad:
        body["external_squad_uuid"] = _uuid(target.external_squad)
    if target.tag:
        body["tag"] = target.tag
    return await _need(sdk).users.create_user(CreateUserRequestDto(**body))


async def panel_sync(sdk: Any, remna_uuid: str, target: Target, *, status_active: bool) -> Any:
    """Срок, лимиты и сквады профиля УЗКИМ телом PATCH. Ответ сверяем.

    Только поля, которые семья наследует от владельца: имя, описание, телеграм,
    почту и метку не шлём — `model_dump(exclude_unset=True)` remnapy не добавит их
    сам. Молчаливое «принял, но не применил» оставило бы семью со старым сроком.
    `status_active` — вернуть ACTIVE истёкшему профилю при продлении (как база
    возвращает его владельцу); отключённый руками профиль этим не включаем.
    """
    from remnapy.enums import TrafficLimitStrategy
    from remnapy.models import UpdateUserRequestDto

    body: dict[str, Any] = dict(
        uuid=_uuid(remna_uuid),
        expire_at=target.expire_at,
        hwid_device_limit=int(target.device_limit),
        traffic_limit_bytes=_gb_to_bytes(target.traffic_limit_gb),
        traffic_limit_strategy=TrafficLimitStrategy(target.strategy),
        active_internal_squads=_squad_uuids(target.internal_squads),
        external_squad_uuid=_uuid(target.external_squad) if target.external_squad else None,
    )
    if status_active:
        from remnapy.enums.users import UserStatus

        body["status"] = UserStatus.ACTIVE
    _need(sdk)
    try:
        updated = await sdk.users.update_user(UpdateUserRequestDto(**body))
    except Exception as exc:  # noqa: BLE001
        if _is_not_found(exc):
            raise PanelGone(str(exc)) from exc
        raise PanelError(f"{type(exc).__name__}: {exc}") from exc
    got_limit = getattr(updated, "hwid_device_limit", None)
    if got_limit is None or int(got_limit) != int(target.device_limit):
        raise PanelError(f"панель вернула лимит устройств {got_limit}, ожидали {target.device_limit}")
    got_bytes = getattr(updated, "traffic_limit_bytes", None)
    if got_bytes is not None and int(got_bytes) != _gb_to_bytes(target.traffic_limit_gb):
        raise PanelError(f"панель вернула трафик {got_bytes}, ожидали {target.traffic_limit_gb} ГБ")
    got_expire = getattr(updated, "expire_at", None)
    if not isinstance(got_expire, datetime) or abs(got_expire - target.expire_at) > EXPIRE_TOLERANCE:
        raise PanelError(f"панель вернула срок {got_expire}, ожидали {target.expire_at}")
    return updated


async def panel_set_enabled(sdk: Any, remna_uuid: str, enabled: bool) -> Any:
    _need(sdk)
    try:
        if enabled:
            resp = await sdk.users.enable_user(_uuid(remna_uuid))
        else:
            resp = await sdk.users.disable_user(_uuid(remna_uuid))
    except Exception as exc:  # noqa: BLE001
        if _is_not_found(exc):
            raise PanelGone(str(exc)) from exc
        raise PanelError(f"{type(exc).__name__}: {exc}") from exc
    got = _status_text(getattr(resp, "status", None))
    if got and (got == "DISABLED") != (not enabled):
        raise PanelError(f"панель вернула статус {got}")
    return resp


async def panel_reset_traffic(sdk: Any, remna_uuid: str) -> None:
    _need(sdk)
    try:
        await sdk.users.reset_user_traffic(_uuid(remna_uuid))
    except Exception as exc:  # noqa: BLE001
        if _is_not_found(exc):
            raise PanelGone(str(exc)) from exc
        raise PanelError(f"{type(exc).__name__}: {exc}") from exc


async def panel_delete(sdk: Any, remna_uuid: str) -> None:
    """Удалить пользователя панели. «Его уже нет» — это успех."""
    _need(sdk)
    try:
        resp = await sdk.users.delete_user(_uuid(remna_uuid))
    except Exception as exc:  # noqa: BLE001
        if _is_not_found(exc):
            return
        raise PanelError(f"{type(exc).__name__}: {exc}") from exc
    deleted = getattr(resp, "is_deleted", True)
    if deleted is False:
        raise PanelError("панель ответила, что не удалила")


# ── зеркало подписки профиля ────────────────────────────────────────────────


async def _store_target(
    session: "AsyncSession", p: ProfileRow, target: Target, *, status: Optional[str] = None
) -> None:
    """Записать в подписку профиля то, что панель только что приняла.

    Вебхук панели сделает то же самое, но позже: без этой записи следующий проход
    крона увидел бы старое зеркало и сходил бы в панель ещё раз.
    """
    if p.sub_id is None:
        return
    params: dict[str, Any] = {
        "sid": p.sub_id,
        "exp": target.expire_at,
        "dev": int(target.device_limit),
        "gb": int(target.traffic_limit_gb),
        "st": target.strategy,
        "sq": _squad_uuids(target.internal_squads),
        "ext": _uuid(target.external_squad) if target.external_squad else None,
    }
    status_sql = ""
    if status:
        status_sql = ", status = CAST(:status AS subscription_status)"
        params["status"] = status
    await session.execute(
        text(
            "UPDATE subscriptions SET expire_at = :exp, device_limit = :dev, traffic_limit = :gb, "
            "traffic_limit_strategy = CAST(:st AS plan_traffic_limit_strategy), "
            "internal_squads = CAST(:sq AS uuid[]), external_squad = :ext"
            f"{status_sql}, updated_at = now() WHERE id = :sid"
        ),
        params,
    )


async def _store_status(session: "AsyncSession", p: ProfileRow, status: str) -> None:
    if p.sub_id is None:
        return
    await session.execute(
        text(
            "UPDATE subscriptions SET status = CAST(:status AS subscription_status), "
            "updated_at = now() WHERE id = :sid"
        ),
        {"status": status, "sid": p.sub_id},
    )


async def _profile_ok(session: "AsyncSession", profile_id: int, **fields: Any) -> None:
    """Профиль сведён: новые поля, счётчик неудач в ноль, отметка сверки.

    `last_reconciled_at` можно передать явно (None — «сверить ближайшим проходом»).
    """
    sets = ["fail_count = 0", "last_error = NULL", "updated_at = now()"]
    if "last_reconciled_at" not in fields:
        sets.append("last_reconciled_at = now()")
    params: dict[str, Any] = {"id": profile_id}
    for key, value in fields.items():
        if value is _NOW:
            sets.append(f"{key} = now()")
        else:
            sets.append(f"{key} = :{key}")
            params[key] = value
    await session.execute(
        text(f"UPDATE family_profiles SET {', '.join(sets)} WHERE id = :id"), params
    )


class _Now:
    """Метка «поставить now() базы» для _profile_ok."""


_NOW = _Now()


async def _profile_fail(session: "AsyncSession", profile_id: int, error: str) -> None:
    await session.execute(
        text(
            "UPDATE family_profiles SET fail_count = fail_count + 1, last_error = :e, "
            "updated_at = now() WHERE id = :id"
        ),
        {"e": error[:300], "id": profile_id},
    )


# ── создание ────────────────────────────────────────────────────────────────


def _replay(saved: dict, owner_id: int) -> dict:
    """Ответ по уже существующей попытке с этим `request_id`.

    Чужой ключ — «conflict» без подробностей: ключ уникален на всю установку, и
    подбор чужого UUID не должен ничего рассказывать о чужой семье.
    """
    if saved["owner_user_id"] != owner_id:
        return {"result": "conflict"}
    status = saved["status"]
    if status in ("active", "suspended"):
        return {"result": "created", "profile_id": saved["id"], "repeat": True}
    if status == "creating":
        return {"result": "pending", "profile_id": saved["id"], "repeat": True}
    if status == "deleting":
        return {"result": "deleted", "profile_id": saved["id"], "repeat": True}
    return {"result": "failed", "repeat": True}


async def _create_shadow(session: "AsyncSession", user_dao: Any, owner: OwnerState, label: str) -> Any:
    """Теневой аккаунт профиля — БАЗОВЫМ DAO, как база заводит панельных людей.

    Без телеграма, почты и пароля: войти в него нельзя ни одним способом. Правила
    приняты (иначе мидлварь правил остановила бы любой его апдейт), пробника нет
    (профиль — не новый клиент). `auth_type` — умолчание базы, свой не вводим:
    колонку читает перечисление базы.
    """
    from sqlalchemy.exc import IntegrityError

    from src.application.dto import UserDto
    from src.core.enums import Locale, Role

    # В базе перечисление лежит ИМЕНАМИ («RU»), а значение у Locale — «ru».
    raw = (owner.language or "").strip()
    language = Locale.__members__.get(raw.upper())
    if language is None:
        try:
            language = Locale(raw.lower())
        except ValueError:
            language = Locale.EN
    name = f"Семья #{owner.user_id}: {label}"
    for attempt in range(5):
        dto = UserDto(
            telegram_id=None,
            referral_code=f"{REFERRAL_PREFIX}{secrets.token_hex(8)}",
            name=name,
            role=Role.USER,
            language=language,
            is_rules_accepted=True,
            is_trial_available=False,
        )
        try:
            async with session.begin_nested():
                return await user_dao.create(dto)
        except IntegrityError as exc:
            if attempt == 4 or "referral_code" not in str(exc):
                raise
    raise RuntimeError("не удалось подобрать уникальный код теневого аккаунта")


async def create_profile(
    session: "AsyncSession",
    sdk: Any,
    *,
    owner_id: int,
    label: Any,
    request_id: Any,
    user_dao: Any,
    subscription_dao: Any,
    actor: str,
    config: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Завести профиль. Порядок шагов — ответ на «панель упала посередине».

    1. В очереди семьи: права, лимит, имя; теневой аккаунт и строка `creating` —
       и COMMIT (очередь отпущена). Дальше любой сбой оставляет след, по которому
       крон доведёт дело.
    2. Панель — без замков: `create_user` с именем `rs_fam_<id>`. Нет ответа —
       спрашиваем панель по имени: создала → идём дальше, точно нет → `failed` и
       теневой удалён, не знаем → строка остаётся `creating`, её доведёт крон.
    3. В очереди семьи: подписка профиля и `active`.
    Очередь занята дольше LOCK_WAIT_MS — «busy»: человек нажмёт ещё раз.
    """
    config = config or load_config()
    now = now or now_utc()
    try:
        rid = str(uuid_lib.UUID(str(request_id)))
    except ValueError:
        return {"result": "bad_request"}

    saved = await profile_by_request(session, rid)
    if saved is not None:
        await session.rollback()
        return _replay(saved, owner_id)
    clean = clean_label(label)
    if clean is None:
        await session.rollback()
        return {"result": "bad_label"}

    try:
        await family_lock(session, owner_id)
    except FamilyBusy:
        return {"result": "busy"}
    owner = await load_owner(session, owner_id)
    # В очереди ищем ещё раз: двойной клик успевает дойти до этой точки дважды.
    saved = await profile_by_request(session, rid)
    if saved is not None:
        await session.rollback()
        return _replay(saved, owner_id)
    profiles = await load_profiles(session, owner_id)
    live = [p for p in profiles if p.status in LIVE_STATUSES]
    why = create_eligibility(owner, config, len(live), now)
    if why is not None:
        await session.rollback()
        return {"result": "not_available", "reason": why}
    if any(p.label.casefold() == clean.casefold() for p in profiles):
        await session.rollback()
        return {"result": "label_taken"}

    target = target_for(owner)
    shadow = await _create_shadow(session, user_dao, owner, clean)
    username = panel_username(shadow.id)
    profile_id = (
        await session.execute(
            text(
                "INSERT INTO family_profiles "
                "(owner_user_id, profile_user_id, request_id, label, status, device_limit, "
                " panel_username) "
                "VALUES (:o, :u, :rid, :label, 'creating', :dev, :name) "
                "ON CONFLICT (request_id) DO NOTHING RETURNING id"
            ),
            {
                "o": owner_id,
                "u": shadow.id,
                "rid": rid,
                "label": clean,
                "dev": target.device_limit,
                "name": username,
            },
        )
    ).scalar()
    if profile_id is None:
        # Ключ занят параллельным запросом ДРУГОГО владельца (свой ждал бы замка).
        await session.rollback()
        saved = await profile_by_request(session, rid)
        await session.rollback()
        return _replay(saved, owner_id) if saved else {"result": "conflict"}
    profile_id = int(profile_id)
    await _event(session, owner_id, profile_id, "create_started", actor, {"label": clean})
    await session.commit()

    description = f"Семья {owner.remna_name}: «{clean}»"
    try:
        created = await panel_create(sdk, username=username, target=target, description=description)
    except Exception as exc:  # noqa: BLE001 — дальше выясняем, что с панелью на самом деле
        logger.warning(f"family: create_user '{username}' не ответил как надо: {exc}")
        try:
            created = await panel_find(sdk, username)
        except PanelError as lookup_exc:
            await _profile_fail(session, profile_id, f"create: {exc}; lookup: {lookup_exc}")
            await session.commit()
            return {"result": "pending", "profile_id": profile_id}
        if created is None and not _is_rejection(exc):
            # Ответа не было (таймаут, обрыв, 5xx): панель могла создать профиль уже
            # после нашего вопроса. «Не удалось» сейчас оставило бы сироту с работающей
            # ссылкой — строка остаётся `creating`, её решит крон после отсрочки.
            await _profile_fail(session, profile_id, f"create: {type(exc).__name__}: {exc}")
            await session.commit()
            return {"result": "pending", "profile_id": profile_id}
        if created is None:
            try:
                await _mark_failed(
                    session, owner_id, profile_id, f"{type(exc).__name__}: {exc}", actor
                )
            except FamilyBusy:
                return {"result": "pending", "profile_id": profile_id}
            return {"result": "failed", "profile_id": profile_id}
    try:
        return await _adopt(
            session,
            sdk,
            owner_id=owner_id,
            profile_id=profile_id,
            panel_user=created,
            subscription_dao=subscription_dao,
            actor=actor,
        )
    except FamilyBusy:
        # Профиль в панели есть, запись доведёт крон по имени — ссылка появится.
        return {"result": "pending", "profile_id": profile_id}


async def _mark_failed(
    session: "AsyncSession", owner_id: int, profile_id: int, error: str, actor: str
) -> None:
    """Панель профиль не создала: строка — в `failed`, теневой аккаунт — долой.

    Сначала отвязываем теневой аккаунт от строки: FK с каскадом унёс бы и её, а она
    нужна, чтобы повтор с тем же `request_id` получил честное «не вышло».
    Очередь занята — строка остаётся `creating`, её доведёт крон.
    """
    await family_lock(session, owner_id)
    shadow_id = (
        await session.execute(
            text(
                "SELECT profile_user_id FROM family_profiles "
                "WHERE id = :id AND status = 'creating' FOR UPDATE"
            ),
            {"id": profile_id},
        )
    ).first()
    if shadow_id is None:
        # Строку уже довёл кто-то другой (крон или параллельный запрос).
        await session.rollback()
        return
    await session.execute(
        text(
            "UPDATE family_profiles SET status = 'failed', profile_user_id = NULL, "
            "last_error = :e, updated_at = now() WHERE id = :id"
        ),
        {"e": error[:300], "id": profile_id},
    )
    if shadow_id[0] is not None:
        await session.execute(text("DELETE FROM users WHERE id = :u"), {"u": int(shadow_id[0])})
    await _event(session, owner_id, profile_id, "create_failed", actor, {"error": error[:200]})
    await session.commit()


async def _adopt(
    session: "AsyncSession",
    sdk: Any,
    *,
    owner_id: int,
    profile_id: int,
    panel_user: Any,
    subscription_dao: Any,
    actor: str,
) -> dict:
    """Пользователь панели есть — завести профилю подписку и перевести в `active`."""
    from src.application.dto import RemnaSubscriptionDto, SubscriptionDto

    await family_lock(session, owner_id)
    owner = await load_owner(session, owner_id)
    p = await load_profile(session, profile_id)
    panel_uuid = str(getattr(panel_user, "uuid", "")).lower()
    if (
        p is not None
        and p.status in ("active", "suspended")
        and panel_uuid in {str(p.panel_uuid or "").lower(), str(p.sub_remna_id or "").lower()}
    ):
        # Этого же пользователя панели уже довёл другой процесс (веб-запрос и крон
        # сошлись на одной строке): профиль готов, удалять его нельзя.
        await session.rollback()
        return {"result": "created", "profile_id": profile_id, "repeat": True}
    if p is None or p.status != "creating" or p.profile_user_id is None:
        # Пока мы ходили в панель, строку закрыли (крон счёл попытку неудачной,
        # владелец удалил профиль или аккаунт). Созданное в панели не должно
        # остаться сиротой с работающей ссылкой.
        await session.rollback()
        try:
            await panel_delete(sdk, str(panel_user.uuid))
        except PanelError as exc:
            logger.error(f"family: сирота в панели '{getattr(panel_user, 'username', '?')}': {exc}")
        return {"result": "failed", "profile_id": profile_id}

    remna = RemnaSubscriptionDto.from_remna_user(panel_user)
    traffic_gb = int(remna.traffic_limit or 0)
    device_limit = int(remna.device_limit or p.device_limit)
    subscription = SubscriptionDto(
        user_remna_id=remna.uuid,
        status=remna.status,
        is_trial=False,
        traffic_limit=traffic_gb,
        device_limit=device_limit,
        traffic_limit_strategy=remna.traffic_limit_strategy,
        tag=remna.tag,
        internal_squads=remna.internal_squads,
        external_squad=remna.external_squad,
        expire_at=remna.expire_at,
        url=remna.url,
        plan_snapshot=family_snapshot(
            owner, device_limit, traffic_gb, _status_text(remna.traffic_limit_strategy)
        ),
    )
    await subscription_dao.create(subscription, p.profile_user_id)
    # Отметку сверки НЕ ставим: пока профиль создавался, владелец мог сменить тариф
    # или уйти на паузу. Пустая отметка — ближайший проход крона сверит профиль.
    await _profile_ok(
        session,
        profile_id,
        status="active",
        panel_uuid=str(remna.uuid),
        last_reconciled_at=None,
        traffic_reset_at=_NOW,
    )
    await _event(session, owner_id, profile_id, "created", actor, {"label": p.label})
    await session.commit()
    return {"result": "created", "profile_id": profile_id}


async def _finish_stuck_create(
    session: "AsyncSession", sdk: Any, profile: ProfileRow, *, subscription_dao: Any
) -> str:
    """Доводка `creating`, брошенной посередине: спрашиваем панель по имени."""
    try:
        found = await panel_find(sdk, profile.panel_username or "")
    except PanelError as exc:
        await _profile_fail(session, profile.id, f"lookup: {exc}")
        await session.commit()
        return "pending"
    if found is None:
        await _mark_failed(session, profile.owner_user_id, profile.id, "панель профиль не создала", "cron")
        return "failed"
    result = await _adopt(
        session,
        sdk,
        owner_id=profile.owner_user_id,
        profile_id=profile.id,
        panel_user=found,
        subscription_dao=subscription_dao,
        actor="cron",
    )
    return str(result.get("result"))


# ── удаление ────────────────────────────────────────────────────────────────


async def delete_profile(
    session: "AsyncSession", sdk: Any, *, owner_id: int, profile_id: int, actor: str
) -> dict:
    """Удалить профиль: `deleting` → панель → теневой аккаунт (FK уносит строку).

    `deleting` фиксируется ОТДЕЛЬНЫМ commit до похода в панель: сбой панели оставляет
    строку в этом состоянии, и крон повторит — ссылка не переживёт удаление.
    """
    try:
        await family_lock(session, owner_id)
    except FamilyBusy:
        return {"result": "busy"}
    row = (
        await session.execute(
            text(
                "UPDATE family_profiles SET status = 'deleting', updated_at = now() "
                "WHERE id = :id AND owner_user_id = :o AND status <> 'failed' "
                "RETURNING id, label"
            ),
            {"id": int(profile_id), "o": owner_id},
        )
    ).first()
    if row is None:
        await session.rollback()
        return {"result": "not_found"}
    await _event(session, owner_id, int(profile_id), "delete_requested", actor, {"label": row[1]})
    await session.commit()
    try:
        done = await _delete_now(session, sdk, int(profile_id), actor)
    except FamilyBusy:
        done = False  # строка уже `deleting` — крон удалит в ближайшие минуты
    return {"result": "deleted" if done else "pending"}


async def _delete_now(session: "AsyncSession", sdk: Any, profile_id: int, actor: str) -> bool:
    """Шаги 2–3 удаления. True — профиля больше нет ни в панели, ни у нас.

    Панель — в очереди семьи этого владельца: удаление не должно разминуться со
    сверкой, которая как раз включает этот профиль. Чужих строк очередь не держит.
    """
    owner_id = (
        await session.execute(
            text("SELECT owner_user_id FROM family_profiles WHERE id = :id"), {"id": profile_id}
        )
    ).scalar()
    if owner_id is None:
        await session.rollback()
        return True
    await family_lock(session, int(owner_id))
    p = await load_profile(session, profile_id)
    if p is None or p.status != "deleting":
        await session.rollback()
        return p is None
    try:
        remna_uuid = p.remna_uuid
        if remna_uuid is None and p.panel_username:
            found = await panel_find(sdk, p.panel_username)
            remna_uuid = str(found.uuid) if found is not None else None
        if remna_uuid is not None:
            await panel_delete(sdk, remna_uuid)
    except PanelError as exc:
        await _profile_fail(session, profile_id, f"delete: {exc}")
        await session.commit()
        logger.warning(f"family: профиль #{profile_id} не удалён в панели: {exc}")
        return False
    await _event(session, p.owner_user_id, profile_id, "deleted", actor, {"label": p.label})
    if p.profile_user_id is not None:
        # Каскад по FK уносит подписку теневого аккаунта и саму строку профиля;
        # журнал остаётся (profile_id → NULL).
        await session.execute(text("DELETE FROM users WHERE id = :u"), {"u": p.profile_user_id})
    await session.execute(text("DELETE FROM family_profiles WHERE id = :id"), {"id": profile_id})
    await session.commit()
    return True


# ── сброс устройств ─────────────────────────────────────────────────────────


async def reset_profile_devices(
    session: "AsyncSession",
    sdk: Any,
    *,
    owner_id: int,
    profile_id: int,
    actor: str,
    reset_enabled: bool = True,
    cooldown_hours: int = 0,
    now: Optional[datetime] = None,
) -> dict:
    """Отвязать все устройства профиля — те же правила, что у сброса самого владельца.

    Выключатель и перерыв между сбросами — из настроек бота (`device_all_reset`):
    без перерыва сброс превращается в способ пускать в один профиль сколько угодно
    устройств по очереди.
    """
    from remnapy.models import DeleteUserAllHwidDeviceRequestDto

    now = now or now_utc()
    try:
        await family_lock(session, owner_id)
    except FamilyBusy:
        return {"result": "busy"}
    p = await load_profile(session, int(profile_id))
    if p is None or p.owner_user_id != owner_id or p.status in ("failed", "deleting"):
        await session.rollback()
        return {"result": "not_found"}
    if p.status != "active" or p.remna_uuid is None:
        await session.rollback()
        return {"result": "not_available", "reason": "inactive"}
    if not reset_enabled:
        await session.rollback()
        return {"result": "not_available", "reason": "disabled"}
    if cooldown_hours > 0 and p.sub_device_reset_at is not None:
        available_at = p.sub_device_reset_at + timedelta(hours=int(cooldown_hours))
        if now < available_at:
            await session.rollback()
            return {"result": "cooldown", "available_at": available_at}
    try:
        await sdk.hwid.delete_all_hwid_user(
            DeleteUserAllHwidDeviceRequestDto(user_uuid=_uuid(p.remna_uuid))
        )
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        logger.warning(f"family: сброс устройств профиля #{profile_id} не прошёл: {exc}")
        return {"result": "panel_error"}
    await _drop_connections(sdk, p.remna_uuid)
    if p.sub_id is not None:
        await session.execute(
            text("UPDATE subscriptions SET device_all_reset_at = :at WHERE id = :sid"),
            {"at": now, "sid": p.sub_id},
        )
    await _event(session, owner_id, p.id, "devices_reset", actor, {"label": p.label})
    await session.commit()
    return {"result": "reset"}


async def _drop_connections(sdk: Any, remna_uuid: str) -> None:
    """Оборвать живые подключения: иначе отвязанное устройство досидит сессию."""
    try:
        from remnapy.models import DropByUserUuids, DropConnectionsRequestDto, TargetAllNodes

        await sdk.ip_control.drop_connections(
            body=DropConnectionsRequestDto(
                drop_by=DropByUserUuids(user_uuids=[_uuid(remna_uuid)]),
                target_nodes=TargetAllNodes(),
            )
        )
    except Exception as exc:  # noqa: BLE001 — устройства уже отвязаны, это добивка
        logger.info(f"family: подключения профиля {remna_uuid} не оборваны: {exc}")


# ── сверка ──────────────────────────────────────────────────────────────────


async def reconcile_owner(
    session: "AsyncSession",
    sdk: Any,
    owner_id: int,
    *,
    reset_traffic: bool = False,
    actor: str = "cron",
    config: Optional[dict] = None,
    now: Optional[datetime] = None,
    wait_ms: Optional[int] = None,
) -> dict:
    """Привести все профили владельца к желаемому. Повтор безопасен.

    ПО ОДНОМУ ПРОФИЛЮ ЗА ШАГ. Каждый шаг: встать в очередь семьи → прочитать
    владельца и профили заново → решить → одно действие в панели → записать итог →
    COMMIT (очередь отпущена). Так:
      * между шагами очередь свободна: вкладка человека и хук оплаты не ждут, пока
        крон пройдёт всю семью через медленную панель;
      * сделанное в панели сразу записано: сбой на следующем профиле уже не откатит
        запись о предыдущем (иначе выключенный профиль остался бы «активным» у нас,
        а следующая сверка приняла бы его за выключенный руками и не включила);
      * решение каждого шага принято по свежему состоянию, а не по снимку начала.
    Сбой панели на профиле — `fail_count+1` без отметки сверки: следующий проход
    возьмёт его снова. Очередь занята на первом шаге — FamilyBusy наверх; на
    следующих — остаток доделает следующий проход.
    """
    config = config or load_config()
    now = now or now_utc()
    grace = int(config.get("suspend_grace_days") or DEFAULT_CONFIG["suspend_grace_days"])
    out: dict[str, Any] = {
        "synced": [], "resumed": [], "suspended": [], "deleted": [], "gone": [], "errors": [],
        "busy": False,
    }
    handled: set[int] = set()
    to_delete: list[int] = []
    first = True
    while True:
        try:
            await family_lock(session, owner_id, wait_ms=wait_ms)
        except FamilyBusy:
            if first:
                raise
            out["busy"] = True
            break
        first = False
        owner = await load_owner(session, owner_id)
        profiles = await load_profiles(session, owner_id)
        if not any(p.status in ("active", "suspended") for p in profiles):
            await session.rollback()
            break
        purchase_at = None if reset_traffic else await last_purchase_at(session, owner_id)
        decisions = plan_decisions(
            owner, profiles, now, grace, reset_traffic=reset_traffic, purchase_at=purchase_at
        )
        by_id = {p.id: p for p in profiles}
        step: Optional[Decision] = None
        for d in decisions:
            if d.profile_id in handled:
                continue
            if d.action == "noop":
                handled.add(d.profile_id)
                await session.execute(
                    text("UPDATE family_profiles SET last_reconciled_at = now() WHERE id = :id"),
                    {"id": d.profile_id},
                )
            elif d.action == "relabel":
                handled.add(d.profile_id)
                await _profile_ok(session, d.profile_id, suspend_reason=d.reason, suspended_at=_NOW)
            elif step is None:
                step = d
        if step is None:
            await session.commit()
            break
        handled.add(step.profile_id)
        p = by_id[step.profile_id]
        try:
            kind = await _apply_decision(session, sdk, owner_id, p, step, actor=actor, now=now)
        except FamilyBusy:
            # Выключение записало намерение и отпустило очередь, а вернуться в неё не
            # вышло: доделает следующий проход (намерение «приостановлен» уже у нас).
            out["busy"] = True
            break
        except PanelGone as exc:
            try:
                kind = await _on_panel_gone(session, sdk, owner_id, p, str(exc), actor=actor)
            except (PanelError, ValueError) as lookup_exc:
                await _profile_fail(session, p.id, f"{exc}; проверка: {lookup_exc}")
                out["errors"].append({"profile_id": p.id, "error": str(lookup_exc)})
            else:
                if kind:
                    out[kind].append(p.id)
        except (PanelError, ValueError) as exc:
            # ValueError — битый uuid в зеркале: это тоже «не свели», а не повод
            # бросить остальных членов семьи.
            await _profile_fail(session, p.id, str(exc))
            out["errors"].append({"profile_id": p.id, "error": str(exc)})
            logger.warning(f"family: профиль #{p.id} владельца {owner_id} не сведён: {exc}")
        else:
            if kind in ("deleted", "gone"):
                to_delete.append(p.id)
            if kind:
                out[kind].append(p.id)
        await session.commit()
    for profile_id in to_delete:
        try:
            await _delete_now(session, sdk, profile_id, actor)
        except FamilyBusy:
            break  # строка уже `deleting` — доведёт крон
    return out


async def _apply_decision(
    session: "AsyncSession",
    sdk: Any,
    owner_id: int,
    p: ProfileRow,
    d: Decision,
    *,
    actor: str,
    now: datetime,
) -> Optional[str]:
    """Одно действие по одному профилю: панель, затем запись. Без commit."""
    if d.action == "sync":
        await _apply_sync(session, sdk, p, d.target, reset=d.reset, now=now)
        if d.reset:
            await _event(session, owner_id, p.id, "renewed", actor, {"expire_at": d.target.expire_at})
        return "synced"
    if d.action == "resume":
        await _apply_sync(session, sdk, p, d.target, reset=d.reset, now=now, resume=True)
        await _event(session, owner_id, p.id, "resumed", actor, {"was": p.suspend_reason})
        return "resumed"
    if d.action == "suspend":
        return "suspended" if await _suspend(session, sdk, owner_id, p, d.reason, actor) else None
    if d.action == "gone":
        # Бот считает пользователя профиля удалённым (вебхук панели). Удаляем, только
        # если панель подтверждает: иначе ошибочный вебхук унёс бы рабочую ссылку.
        found = await panel_presence(sdk, p)
        if found is not None:
            raise PanelError("панель: пользователь профиля есть, а бот считает его удалённым")
        await _mark_deleting(session, p.id)
        await _event(session, owner_id, p.id, "panel_gone", actor, {"label": p.label})
        return "gone"
    if d.action == "delete":
        await _mark_deleting(session, p.id)
        await _event(
            session, owner_id, p.id, "expired_grace", actor, {"label": p.label, "reason": d.reason}
        )
        return "deleted"
    return None


async def _on_panel_gone(
    session: "AsyncSession", sdk: Any, owner_id: int, p: ProfileRow, error: str, *, actor: str
) -> Optional[str]:
    """Панель ответила «не найден» на действие с профилем. Удалять НЕ спешим.

    * Панель знает профиль под другим uuid (сопоставление uuid сменилось) —
      перепривязываем зеркало, следующий проход сделает действие по новому uuid.
    * Знает под тем же — это не «удалён», а сбой пути: считаем неудачей, повторим.
    * Не знает ни по имени, ни по uuid — профиль приостановлен с причиной
      `panel_missing` и удалится через отсрочку, если так и не найдётся. Отсрочку
      повторная проверка не сдвигает.
    """
    found = await panel_presence(sdk, p)
    if found is not None:
        new_uuid = str(getattr(found, "uuid", "") or "")
        if new_uuid and new_uuid.lower() != str(p.remna_uuid or "").lower():
            if p.sub_id is not None:
                await session.execute(
                    text("UPDATE subscriptions SET user_remna_id = :u, updated_at = now() WHERE id = :sid"),
                    {"u": _uuid(new_uuid), "sid": p.sub_id},
                )
            await session.execute(
                text(
                    "UPDATE family_profiles SET panel_uuid = :u, last_reconciled_at = NULL, "
                    "updated_at = now() WHERE id = :id"
                ),
                {"u": _uuid(new_uuid), "id": p.id},
            )
            await _event(session, owner_id, p.id, "relinked", actor, {"from": p.remna_uuid, "to": new_uuid})
            await _profile_fail(session, p.id, f"uuid профиля в панели сменился: {error}")
            return None
        raise PanelError(f"панель не нашла профиль по uuid, но знает его по имени: {error}")
    fields: dict[str, Any] = {"status": "suspended", "suspend_reason": "panel_missing"}
    already = p.status == "suspended" and p.suspend_reason == "panel_missing"
    if not already:
        fields["suspended_at"] = _NOW
        await _event(session, owner_id, p.id, "panel_missing", actor, {"label": p.label})
    await _profile_ok(session, p.id, **fields)
    return None if already else "suspended"


async def _suspend(
    session: "AsyncSession", sdk: Any, owner_id: int, p: ProfileRow, reason: Optional[str], actor: str
) -> bool:
    """Выключить профиль: СНАЧАЛА намерение (и commit), потом панель.

    Обратный порядок терял выключение: панель профиль выключила, а запись о нём
    откатилась вместе со сбоем на следующем шаге. Профиль оставался «активным» у нас
    и выключенным в панели — следующая сверка принимала его за выключенный руками и
    не включала уже оплаченной семье. С намерением впереди любой сбой после него
    оставляет «приостановлен» у нас, а сверка доводит панель до того же (still_on) —
    или включает профиль штатно, когда владелец снова платит.

    Отметку сверки намерение сбрасывает: не дошедшее выключение крон повторит
    ближайшим проходом, а не через час. Повторное выключение уже приостановленного
    не сдвигает отсрочку — иначе профиль не удалился бы никогда.
    """
    fields: dict[str, Any] = {"status": "suspended", "suspend_reason": reason}
    fresh_reason = p.status != "suspended" or p.suspend_reason != reason
    if fresh_reason:
        fields["suspended_at"] = _NOW
    sets = ["updated_at = now()", "last_reconciled_at = NULL"]
    params: dict[str, Any] = {"id": p.id}
    for key, value in fields.items():
        if value is _NOW:
            sets.append(f"{key} = now()")
        else:
            sets.append(f"{key} = :{key}")
            params[key] = value
    await session.execute(text(f"UPDATE family_profiles SET {', '.join(sets)} WHERE id = :id"), params)
    if fresh_reason:
        await _event(session, owner_id, p.id, "suspended", actor, {"reason": reason})
    await session.commit()

    # Намерение записано и очередь отпущена — встаём в неё снова и выключаем.
    await family_lock(session, owner_id)
    current = await load_profile(session, p.id)
    if current is None or current.status != "suspended":
        await session.rollback()  # пока очередь была свободна, профиль поменяли
        return False
    if current.remna_uuid is None:
        raise PanelGone("у профиля нет пользователя панели")
    await panel_set_enabled(sdk, current.remna_uuid, False)
    await _store_status(session, current, "DISABLED")
    await session.execute(
        text(
            "UPDATE family_profiles SET last_reconciled_at = now(), fail_count = 0, "
            "last_error = NULL WHERE id = :id"
        ),
        {"id": p.id},
    )
    return True


async def _mark_deleting(session: "AsyncSession", profile_id: int) -> None:
    await session.execute(
        text("UPDATE family_profiles SET status = 'deleting', updated_at = now() WHERE id = :id"),
        {"id": profile_id},
    )


async def _apply_sync(
    session: "AsyncSession",
    sdk: Any,
    p: ProfileRow,
    target: Target,
    *,
    reset: bool,
    now: datetime,
    resume: bool = False,
) -> None:
    """Срок и лимиты в панель, потом (если надо) сброс трафика, потом включение.

    Порядок «сначала срок, потом включить» — тот же, что у снятия паузы
    (taskiq/tasks/freeze.py): включённый пользователь со старым сроком успел бы
    получить статус «истёк» раньше, чем мы поправим дату.
    """
    if p.remna_uuid is None:
        raise PanelGone("у профиля нет пользователя панели")
    # ACTIVE шлём, как база владельцу при продлении: истёкшему — да, отключённому
    # руками в панели — нет (его включает только тот, кто выключил).
    status = (p.sub_status or "").upper()
    status_active = not resume and status != "DISABLED" and (reset or status == "EXPIRED")
    await panel_sync(sdk, p.remna_uuid, target, status_active=status_active)
    if reset:
        await panel_reset_traffic(sdk, p.remna_uuid)
    new_status = None
    if resume:
        await panel_set_enabled(sdk, p.remna_uuid, True)
        new_status = "ACTIVE"
    elif status_active:
        new_status = "ACTIVE"
    await _store_target(session, p, target, status=new_status)
    fields: dict[str, Any] = {"device_limit": target.device_limit}
    if reset:
        fields["traffic_reset_at"] = _NOW
    if resume:
        fields.update(status="active", suspend_reason=None, suspended_at=None)
    await _profile_ok(session, p.id, **fields)


async def after_purchase(session: "AsyncSession", sdk: Any, owner_id: int) -> Optional[dict]:
    """Хук оплаты: владелец купил период — семья получает тот же срок и новый трафик.

    Оплата не ждёт семью НИКОГДА:
      * очередь семьи берётся без ожидания — занята (крон как раз сверяет) → выходим,
        крон увидит оплату позже последней сверки профиля и обнулит трафик сам;
      * вся работа — в общем дедлайне HOOK_DEADLINE_SECONDS: медленная панель не
        держит ответ шлюзу, недоделанное доводит крон тем же правилом.
    Дешёвая проверка «есть ли вообще семья» — первой: хук стоит на КАЖДОЙ оплате, а
    семьи у единиц. Панели нет — ничего не делаем, крон догонит за пять минут.
    """
    if sdk is None:
        return None
    has = (
        await session.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM family_profiles "
                "WHERE owner_user_id = :uid AND status IN ('active', 'suspended'))"
            ),
            {"uid": owner_id},
        )
    ).scalar()
    await session.rollback()
    if not has:
        return None
    try:
        return await asyncio.wait_for(
            reconcile_owner(session, sdk, owner_id, reset_traffic=True, actor="payment", wait_ms=0),
            timeout=HOOK_DEADLINE_SECONDS,
        )
    except FamilyBusy:
        logger.info(f"family: семья владельца {owner_id} сверяется кроном — хук оплаты уступил")
        return {"busy": True}
    except asyncio.TimeoutError:
        logger.warning(f"family: хук оплаты не уложился в {HOOK_DEADLINE_SECONDS} с — остальное доведёт крон")
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001 — сессия после отмены могла сломаться
            pass
        return {"timeout": True}


async def owners_to_reconcile(session: "AsyncSession", *, full: bool, limit: int = 500) -> list[int]:
    sql = ALL_OWNERS_SQL if full else CHANGED_OWNERS_SQL
    rows = (await session.execute(text(sql), {"lim": limit})).all()
    await session.rollback()
    return [int(r[0]) for r in rows]


async def sweep_pending(
    session: "AsyncSession", sdk: Any, *, subscription_dao: Any, limit: int = 50
) -> dict:
    """Довести брошенные «создаю» и «удаляю». Одна строка не мешает остальным."""
    rows = (
        await session.execute(text(PENDING_SQL), {"grace": PENDING_GRACE_MINUTES, "lim": limit})
    ).all()
    await session.rollback()
    out: dict[str, list] = {"created": [], "failed": [], "deleted": [], "pending": []}
    for profile_id, _owner_id, status in rows:
        try:
            if status == "deleting":
                done = await _delete_now(session, sdk, int(profile_id), "cron")
                out["deleted" if done else "pending"].append(int(profile_id))
                continue
            p = await load_profile(session, int(profile_id))
            await session.rollback()
            if p is None or p.status != "creating":
                continue
            result = await _finish_stuck_create(session, sdk, p, subscription_dao=subscription_dao)
            key = {"created": "created", "failed": "failed"}.get(result, "pending")
            out[key].append(int(profile_id))
        except Exception as exc:  # noqa: BLE001 — одна строка не мешает остальным
            await session.rollback()
            logger.warning(f"family: доводка профиля #{profile_id} не прошла: {exc}")
            out["pending"].append(int(profile_id))
    return out


async def orphan_scan(
    session: "AsyncSession", sdk: Any, *, page: int = 200, max_pages: int = 500
) -> list[str]:
    """Пользователи панели `rs_fam_*`, за которыми у нас нет живой строки.

    Два случая:
      * имя совпадает со строкой в `failed` — панель досоздала пользователя уже после
        того, как попытку признали неудачной (ответ на создание потерялся). Ссылку никто
        не видел — такого пользователя удаляем сами, владельцу бота не пишем;
      * иначе — сирота неизвестного происхождения: он работает, а мы о нём не знаем.
        Удалять молча нельзя — там могла быть чья-то рабочая ссылка; сообщаем
        владельцу бота (возвращаемый список), решает он.
    """
    rows = (
        await session.execute(
            text(
                "SELECT fp.panel_username, fp.panel_uuid::text, s.user_remna_id::text, "
                "fp.status, fp.owner_user_id, fp.id "
                "FROM family_profiles fp "
                "LEFT JOIN subscriptions s ON s.user_id = fp.profile_user_id"
            )
        )
    ).all()
    await session.rollback()
    live = [r for r in rows if r[3] != "failed"]
    names = {str(r[0]) for r in live if r[0]}
    uuids = {str(v).lower() for r in live for v in (r[1], r[2]) if v}
    failed = {str(r[0]): (int(r[4]), int(r[5])) for r in rows if r[3] == "failed" and r[0]}
    orphans: list[str] = []
    cleaned: list[tuple[str, int, int]] = []
    for index in range(max_pages):
        resp = await sdk.users.get_all_users(start=index * page, size=page)
        users = list(getattr(resp, "users", None) or [])
        for user in users:
            name = getattr(user, "username", None)
            if not is_family_username(name) or name in names:
                continue
            if str(getattr(user, "uuid", "")).lower() in uuids:
                continue
            if name in failed:
                try:
                    await panel_delete(sdk, str(user.uuid))
                except PanelError as exc:
                    logger.warning(f"family: поздний профиль '{name}' не удалён: {exc}")
                    continue
                cleaned.append((str(name), *failed[name]))
                continue
            orphans.append(str(name))
        if len(users) < page:
            break
    for name, owner_id, profile_id in cleaned:
        await _event(session, owner_id, profile_id, "late_orphan_deleted", "cron", {"name": name})
    if cleaned:
        await session.commit()
        logger.info(f"family: удалены поздно созданные профили неудачных попыток: {[c[0] for c in cleaned]}")
    return sorted(set(orphans))


def orphans_to_report(found: Sequence[str]) -> list[str]:
    """Каких сирот владельцу бота ещё не называли. Одно сообщение на новую сироту."""
    try:
        state = json.loads(ORPHANS_STATE_PATH.read_text("utf-8"))
        reported = set(state.get("reported") or []) if isinstance(state, dict) else set()
    except Exception:  # noqa: BLE001 — нет файла или он битый: считаем, что не называли
        reported = set()
    fresh = [name for name in found if name not in reported]
    try:
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        ORPHANS_STATE_PATH.write_text(
            json.dumps({"reported": sorted(set(found))}, ensure_ascii=False), "utf-8"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"family: не сохранил список сирот ({exc})")
    return fresh


def orphans_text(names: Sequence[str]) -> str:
    shown = ", ".join(f"<code>{n}</code>" for n in names[:10])
    more = f" и ещё {len(names) - 10}" if len(names) > 10 else ""
    return (
        "👨‍👩‍👧 <b>Семейные профили: в панели есть профили без владельца</b>\n\n"
        f"{shown}{more}\n\n"
        "У бота нет записи о них: их никто не продлит и не приостановит, а ссылки "
        "работают до срока в панели. Если профили не нужны — удалите их в панели "
        "Remnawave. Больше об этих именах напоминать не буду."
    )


# ── удаление владельца ──────────────────────────────────────────────────────


async def purge_targets(session: "AsyncSession", sdk: Any, owner_id: int) -> tuple[list[str], list[int]]:
    """Что снести вместе с человеком: пользователей панели его семьи и теневые аккаунты.

    Зовётся в очереди семьи этого владельца (см. overlay_user_purge): иначе профиль,
    заведённый между этим списком и удалением, остался бы в панели сиротой.

    Таблицы может ещё не быть (свежая установка до миграции) — тогда семьи нет; это
    проверяется ЯВНО, а не глотанием любой ошибки: сбой базы обязан прервать удаление
    человека, а не превратиться в «семьи нет» и оставить его профили работать. Имя
    без известного uuid (профиль застрял в «создаю») спрашиваем у панели; панель не
    ответила — PanelError, и удаление человека прерывается целиком, ничего не меняя.
    """
    exists = (await session.execute(text("SELECT to_regclass('family_profiles')"))).scalar()
    if exists is None:
        return [], []
    rows = (
        await session.execute(
            text(
                "SELECT fp.profile_user_id, fp.panel_username, fp.panel_uuid::text, "
                "  s.user_remna_id::text "
                "FROM family_profiles fp "
                "LEFT JOIN subscriptions s ON s.user_id = fp.profile_user_id "
                "WHERE fp.owner_user_id = :u AND fp.status <> 'failed'"
            ),
            {"u": owner_id},
        )
    ).all()
    uuids: set[str] = set()
    shadows: set[int] = set()
    lookups: set[str] = set()
    for profile_user_id, name, panel_uuid, sub_uuid in rows:
        if profile_user_id is not None:
            shadows.add(int(profile_user_id))
        known = [v for v in (panel_uuid, sub_uuid) if v]
        uuids.update(str(v).lower() for v in known)
        if not known and name:
            lookups.add(str(name))
    for name in sorted(lookups):
        if sdk is None:
            raise PanelError("панель недоступна — не проверить профиль в процессе создания")
        found = await panel_find(sdk, name)
        if found is not None:
            uuids.add(str(found.uuid).lower())
    return sorted(uuids), sorted(shadows)


# ── витрина ─────────────────────────────────────────────────────────────────


async def _panel_numbers(sdk: Any, remna_uuid: str) -> dict[str, Any]:
    """Устройства и расход профиля — для показа. Любая беда — просто пусто."""
    out: dict[str, Any] = {"devices": None, "used_bytes": None}
    if sdk is None:
        return out

    async def devices() -> None:
        resp = await sdk.hwid.get_hwid_user(_uuid(remna_uuid))
        total = getattr(resp, "total", None)
        if total is None:
            total = len(getattr(resp, "devices", None) or [])
        out["devices"] = int(total)

    async def traffic() -> None:
        user = await sdk.users.get_user_by_uuid(_uuid(remna_uuid))
        used = getattr(user, "used_traffic_bytes", None)
        if used is None:
            used = getattr(getattr(user, "user_traffic", None), "used_traffic_bytes", None)
        out["used_bytes"] = int(used) if used is not None else None

    for step in (devices, traffic):
        try:
            await asyncio.wait_for(step(), timeout=VIEW_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 — показать без числа лучше, чем не показать
            logger.debug(f"family: {step.__name__} профиля {remna_uuid} не получены: {exc}")
    return out


async def family_view(
    session: "AsyncSession",
    sdk: Any,
    owner_id: int,
    *,
    config: Optional[dict] = None,
    now: Optional[datetime] = None,
    with_panel: bool = True,
) -> dict:
    """Всё, что видит владелец семьи: право, условия, профили с цифрами панели."""
    config = config or load_config()
    now = now or now_utc()
    owner = await load_owner(session, owner_id)
    profiles = await load_profiles(session, owner_id)
    await session.rollback()
    visible = [p for p in profiles if p.status in ("creating", "active", "suspended", "deleting")]
    live = [p for p in profiles if p.status in LIVE_STATUSES]
    why = create_eligibility(owner, config, len(live), now)
    numbers: list[dict] = [{} for _ in visible]
    if with_panel and sdk is not None:
        jobs = [
            _panel_numbers(sdk, p.remna_uuid) if p.status == "active" and p.remna_uuid else None
            for p in visible
        ]
        idx = [i for i, job in enumerate(jobs) if job is not None]
        results = await asyncio.gather(*(jobs[i] for i in idx), return_exceptions=True)
        for i, res in zip(idx, results):
            numbers[i] = res if isinstance(res, dict) else {}
    items = []
    for p, extra in zip(visible, numbers):
        expired = p.sub_expire_at is not None and p.sub_expire_at <= now
        items.append(
            {
                "id": p.id,
                "label": p.label,
                "status": p.status,
                "suspend_reason": p.suspend_reason,
                "expired": expired,
                "expire_at": p.sub_expire_at,
                "url": p.sub_url if p.status in ("active", "suspended") else None,
                "device_limit": p.sub_device_limit or p.device_limit,
                "devices": extra.get("devices"),
                "traffic_limit_bytes": _gb_to_bytes(p.sub_traffic_limit or 0) if p.sub_id else None,
                "traffic_used_bytes": extra.get("used_bytes"),
                "created_at": p.created_at,
                "device_reset_at": p.sub_device_reset_at,
            }
        )
    terms = owner.terms
    return {
        "enabled": bool(config.get("enabled")),
        "available": why is None,
        "reason": why,
        "plan_name": owner.plan_name or None,
        "terms": (
            {"max_profiles": terms.max_profiles, "devices_per_profile": terms.devices_per_profile}
            if terms
            else None
        ),
        "used": len(live),
        "profiles": items,
    }


async def menu_visible(session: "AsyncSession", owner_id: int, now: Optional[datetime] = None) -> bool:
    """Показывать ли вход «Семья» (кнопка бота, пункт кабинета).

    Функция включена И (тариф семейный ИЛИ профили уже есть — у человека, сменившего
    тариф, должна оставаться возможность увидеть и удалить своих).
    """
    if not load_config().get("enabled"):
        return False
    owner = await load_owner(session, owner_id)
    if owner.terms is not None and not owner.is_trial:
        return True
    has = (
        await session.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM family_profiles "
                "WHERE owner_user_id = :uid AND status IN ('creating', 'active', 'suspended'))"
            ),
            {"uid": owner_id},
        )
    ).scalar()
    return bool(has)


# ── тексты ──────────────────────────────────────────────────────────────────

_REASON_RU = {
    "disabled": "функция выключена",
    "no_subscription": "нет подписки",
    "blocked": "аккаунт заблокирован",
    "trial": "на пробной подписке семьи нет",
    "not_family": "тариф не семейный",
    "frozen": "подписка на паузе",
    "reserve": "подписка закончилась",
    "not_active": "подписка не активна",
    "max_reached": "профилей уже максимум",
    "plan": "тариф больше не семейный",
    "owner_expired": "подписка закончилась",
    "owner_frozen": "подписка на паузе",
    "owner_gone": "подписки больше нет",
    "owner_blocked": "аккаунт заблокирован",
    "panel_missing": "профиль не найден на сервере подписок",
    "inactive": "профиль приостановлен",
}


def reason_ru(code: Optional[str]) -> str:
    return _REASON_RU.get(code or "", code or "")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "DEFAULT_CONFIG",
    "FAMILY_PLAN_ID",
    "Decision",
    "FamilyBusy",
    "OwnerState",
    "PanelError",
    "PanelGone",
    "ProfileRow",
    "Target",
    "Terms",
    "after_purchase",
    "clean_label",
    "create_eligibility",
    "create_profile",
    "delete_profile",
    "family_lock",
    "family_view",
    "load_config",
    "menu_visible",
    "normalize_terms",
    "orphan_scan",
    "owner_condition",
    "plan_decisions",
    "purge_targets",
    "reconcile_owner",
    "reset_profile_devices",
    "save_config",
    "suspension_order",
    "sweep_pending",
    "target_for",
]
