"""Напоминание о незавершённой оплате: правила, выборка и тексты (overlay).

ЧТО ЭТО. Человек дошёл до создания счёта и не заплатил. Через ~10 минут бот пишет ОДНО
сообщение: «оплата не завершилась» и кнопку, которая ведёт в кабинет — начать оплату
заново. Замер по боевой базе: из 70 оплат за полгода 65 проходят в первые 10 минут
(медиана 1.6 мин, p90 5.8), а базовый крон гасит неоплаченный счёт на 30-й минуте.
Значит десятая минута — это «уже точно бросил, но счёт ещё жив».

ПОЧЕМУ БЕЗ ССЫЛКИ НА СТАРЫЙ СЧЁТ. Прислать ту же ссылку — первое, что приходит в
голову, и это ловушка (подробности в миграции 0013). Коротко: счёт «двойника» без
метки заказа провёл бы платёж как покупку тарифа с нулевой длительностью; оплата
одного счёта не гасит остальные, и напоминание по соседнему уводит платить второй раз;
повторная оплата уже проведённого счёта съедается молча, а ссылка ЮMoney живёт вечно;
в счёте заморожены цена и скидка. Поэтому ссылок мы не храним и не шлём — и по той же
причине в денежный путь НЕ врезаемся вовсе: этот модуль читает базу постфактум.

ГЛАВНОЕ ПРАВИЛО ОТБОРА: «оплатил ли ЧЕЛОВЕК», а не «оплачен ли счёт». PENDING-строка
живёт до получаса и остаётся висеть, даже когда человек заплатил другим шлюзом, с
баланса или автоплатежом.

Конфиг — assets/payment_reminder.json (админка правит на лету). Файла нет — ВЫКЛЮЧЕНО.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from loguru import logger

CONFIG_PATH = Path(os.environ.get("RS_ASSETS_DIR", "/opt/remnashop/assets")) / "payment_reminder.json"

# Вид счёта по снимку тарифа. Синтетические id заняты: −1 проверочный платёж владельца
# (CreateTestPayment), −2 пополнение баланса, −3 подарок, −4 место под устройство,
# −5 трафик. Проверочные счета в рассылку не попадают никогда.
KIND_BY_PLAN_ID: dict[int, str] = {-2: "topup", -3: "gift", -4: "device", -5: "traffic"}
TEST_PLAN_ID = -1

# Куда ведёт кнопка. Новый счёт создаётся там обычным путём — с текущей ценой и своей
# меткой заказа.
PATH_BY_KIND: dict[str, str] = {
    "plan": "/billing",
    "topup": "/balance",
    "gift": "/billing",
    "device": "/devices",
    "traffic": "/billing?extra_traffic=1",
}

OPTOUT_KIND = "payment_reminder"

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,          # по умолчанию выключено
    "delay_minutes": 10,       # через сколько после счёта пишем
    "max_age_minutes": 45,     # старше — молчим совсем, досылки нет
    "cooldown_hours": 24,      # не чаще раза в сутки на человека
    "max_per_30d": 3,          # и не больше трёх за месяц
    "notify_admins": False,    # сводка владельцу после прохода
}

_LIMITS = {
    "delay_minutes": (5, 25),
    "max_age_minutes": (15, 180),
    "cooldown_hours": (1, 168),
    "max_per_30d": (1, 30),
}


def _flag(value: Any, default: bool) -> bool:
    # Строгое `is True`: строка "false" из руками правленого файла не должна включать
    # рассылку (грабля выключателя докупки).
    return value is True if isinstance(value, bool) else default


def _clamp(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, number))


def normalize(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return dict(DEFAULT_CONFIG)
    cfg = dict(DEFAULT_CONFIG)
    cfg["enabled"] = _flag(data.get("enabled"), DEFAULT_CONFIG["enabled"])
    cfg["notify_admins"] = _flag(data.get("notify_admins"), DEFAULT_CONFIG["notify_admins"])
    for key, (lo, hi) in _LIMITS.items():
        cfg[key] = _clamp(data.get(key), DEFAULT_CONFIG[key], lo, hi)
    # Окно обязано быть окном: потолок ниже порога означал бы «никогда».
    if cfg["max_age_minutes"] <= cfg["delay_minutes"]:
        cfg["max_age_minutes"] = cfg["delay_minutes"] + 5
    return cfg


def load_config() -> dict[str, Any]:
    try:
        return normalize(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return dict(DEFAULT_CONFIG)
    except Exception as exc:  # noqa: BLE001 — сломанный файл не включает рассылку
        logger.warning(f"payment_reminder: конфиг не прочитан ({exc}) — считаю выключенным")
        return dict(DEFAULT_CONFIG)


def save_config(data: dict[str, Any]) -> dict[str, Any]:
    cfg = normalize(data)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def effective_enabled(cfg: Optional[dict[str, Any]] = None) -> bool:
    return bool((cfg or load_config())["enabled"])


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def kind_of(plan_id: Optional[int]) -> str:
    """Вид счёта. Неизвестный синтетический id — «other»: продавать его не наше дело."""
    if plan_id is None:
        return "other"
    if plan_id > 0:
        return "plan"
    return KIND_BY_PLAN_ID.get(int(plan_id), "other")


@dataclass(frozen=True)
class Candidate:
    """Строка выборки: счёт плюс всё, что нужно знать о ЧЕЛОВЕКЕ на этот момент."""

    payment_id: str
    user_id: int
    telegram_id: Optional[int]
    lang: Optional[str]
    is_blocked: bool
    is_bot_blocked: bool
    is_test: bool
    role: str
    plan_id: Optional[int]
    plan_name: Optional[str]
    amount: Optional[Decimal]
    currency: Optional[str]
    created_at: datetime
    paid_after: bool          # есть COMPLETED с updated_at >= created_at счёта
    newer_txn: bool           # у человека есть транзакция новее этой
    sub_touched: bool         # подписка менялась после создания счёта
    opted_out: bool
    reminded_24h: bool
    reminded_30d: int
    already_row: bool         # по этому счёту строка уже есть


def decide(c: Candidate, cfg: dict[str, Any], now: datetime) -> Optional[str]:
    """Почему НЕ пишем. None — пишем.

    Порядок проверок — от самых дешёвых и бесспорных к спорным, чтобы причина в сводке
    была честной: «уже заплатил» важнее, чем «кулдаун».
    """
    if c.already_row:
        return "already_handled"
    if c.is_test or c.plan_id == TEST_PLAN_ID:
        return "test_payment"
    if (c.role or "USER").upper() != "USER":
        return "staff"
    if c.telegram_id is None:
        return "no_telegram"
    if c.is_blocked or c.is_bot_blocked:
        return "blocked"
    if c.opted_out:
        return "opted_out"
    if kind_of(c.plan_id) == "other":
        return "unknown_kind"
    # Человек, а не счёт: оплата соседнего счёта, списание с баланса и автоплатёж
    # оставляют этот PENDING висеть до получасового крона базы.
    if c.paid_after:
        return "paid"
    if c.newer_txn:
        return "newer_attempt"
    if c.sub_touched:
        return "subscription_changed"
    age_minutes = (now - c.created_at).total_seconds() / 60
    if age_minutes < int(cfg["delay_minutes"]):
        return "too_early"
    if age_minutes > int(cfg["max_age_minutes"]):
        # Досылки нет намеренно: сообщение «оплата не завершилась» через два часа
        # человек читает как спам, а не как помощь.
        return "too_late"
    if c.reminded_24h:
        return "cooldown"
    if c.reminded_30d >= int(cfg["max_per_30d"]):
        return "month_cap"
    return None


# ── тексты ───────────────────────────────────────────────────────────────────

_SUBJECT_RU = {
    "plan": "Подписка",
    "topup": "Пополнение баланса",
    "gift": "Подарочная подписка",
    "device": "Место под устройство",
    "traffic": "Докупка трафика",
}
_SUBJECT_EN = {
    "plan": "Subscription",
    "topup": "Balance top-up",
    "gift": "Gift subscription",
    "device": "Extra device slot",
    "traffic": "Extra traffic",
}


def _fmt_amount(amount: Optional[Decimal], currency: Optional[str]) -> str:
    if amount is None:
        return ""
    text = f"{Decimal(str(amount)).normalize():f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    symbol = {"RUB": "₽", "XTR": "★", "USD": "$", "EUR": "€"}.get((currency or "").upper(), currency or "")
    return f"{text} {symbol}".strip()


def _escape(text: str) -> str:
    """Название тарифа приходит из админки и едет в HTML Telegram."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_message(kind: str, amount: Optional[Decimal], currency: Optional[str], lang: Optional[str]) -> str:
    """Текст сообщения целиком, без плейсхолдеров (push прогоняет строку через .format).

    Ничего не утверждаем про причину: мы не знаем, закрылась ли страница, передумал ли
    человек или у него не прошла карта. Говорим только то, что видим: оплата не дошла.
    """
    price = _fmt_amount(amount, currency)
    if (lang or "ru").lower().startswith("en"):
        subject = _SUBJECT_EN.get(kind, "Payment")
        head = "💳 Payment didn't go through"
        line = f"{subject}{f' — {price}' if price else ''}."
        body = (
            "We didn't receive the payment. If you still want it, open the checkout "
            "again — it takes a few seconds."
        )
        return f"<b>{head}</b>\n\n{_escape(line)}\n\n{body}"
    subject = _SUBJECT_RU.get(kind, "Оплата")
    head = "💳 Оплата не завершилась"
    line = f"{subject}{f' — {price}' if price else ''}."
    body = "Деньги к нам не пришли. Если всё ещё нужно — откройте оплату заново, это пара секунд."
    return f"<b>{head}</b>\n\n{_escape(line)}\n\n{body}"


def button_url(base_url: Optional[str], kind: str) -> str:
    """Кнопка ведёт в кабинет, а не на старый счёт. Пусто — кнопки не будет."""
    base = (base_url or "").strip().rstrip("/")
    return f"{base}{PATH_BY_KIND.get(kind, '/billing')}" if base else ""


# ── SQL ──────────────────────────────────────────────────────────────────────

# Кандидаты: неоплаченные счета в окне + всё, что нужно знать о ЧЕЛОВЕКЕ. Решение
# принимает decide(), чтобы каждая причина отказа была видна в сводке, а не терялась
# в WHERE (тот же приём, что в скидке на продление).
CANDIDATES_SQL = """
SELECT t.payment_id::text                              AS payment_id,
       t.user_id                                       AS user_id,
       u.telegram_id                                   AS telegram_id,
       lower(u.language::text)                         AS lang,
       u.is_blocked                                    AS is_blocked,
       u.is_bot_blocked                                AS is_bot_blocked,
       coalesce(t.is_test, false)                      AS is_test,
       coalesce(u.role::text, 'USER')                  AS role,
       (t.plan_snapshot->>'id')::int                   AS plan_id,
       t.plan_snapshot->>'name'                        AS plan_name,
       (t.pricing->>'final_amount')::numeric           AS amount,
       t.currency::text                                AS currency,
       t.created_at                                    AS created_at,
       EXISTS (SELECT 1 FROM transactions p
                WHERE p.user_id = t.user_id AND p.status::text = 'COMPLETED'
                  AND p.updated_at >= t.created_at)    AS paid_after,
       EXISTS (SELECT 1 FROM transactions n
                WHERE n.user_id = t.user_id AND n.created_at > t.created_at) AS newer_txn,
       EXISTS (SELECT 1 FROM subscriptions s
                WHERE s.user_id = t.user_id AND s.updated_at > t.created_at) AS sub_touched,
       EXISTS (SELECT 1 FROM notification_optouts o
                WHERE o.user_id = t.user_id AND o.kind = :optout_kind) AS opted_out,
       EXISTS (SELECT 1 FROM payment_reminders r
                WHERE r.user_id = t.user_id AND r.sent_at > :cooldown_since) AS reminded_24h,
       (SELECT count(*) FROM payment_reminders r
         WHERE r.user_id = t.user_id AND r.sent_at > :month_since)          AS reminded_30d,
       EXISTS (SELECT 1 FROM payment_reminders r
                WHERE r.payment_id = t.payment_id)                          AS already_row
  FROM transactions t
  JOIN users u ON u.id = t.user_id
 WHERE t.status::text = 'PENDING'
   AND t.created_at BETWEEN :window_start AND :window_end
 ORDER BY t.created_at
 LIMIT :limit
"""

# Захват: строка появляется РАНЬШЕ отправки и только один раз на счёт (payment_id — PK).
# Гонку двух прогонов решает база, а не код: второй INSERT просто ничего не делает.
CLAIM_SQL = """
INSERT INTO payment_reminders (payment_id, user_id, kind, amount, currency, status,
                               invoice_at, claimed_at)
VALUES (CAST(:payment_id AS uuid), :user_id, :kind, :amount, :currency, 'claimed',
        :invoice_at, now())
ON CONFLICT (payment_id) DO NOTHING
"""

# Итог отправки. Пишем отдельной транзакцией после самой отправки: упали между —
# строка остаётся 'claimed' и больше никем не берётся (досылки нет намеренно).
#
# ФЛАГ `:sent` ОТДЕЛЬНЫМ ПАРАМЕТРОМ, а не сравнением `:status = 'sent'`: один bind,
# использованный и как VARCHAR-значение, и как текст в сравнении, asyncpg отвергает
# («inconsistent types deduced for parameter»). Поймал PG-тест; на подделке сессии
# это выглядело бы рабочим, а на бою КАЖДАЯ отправка не записала бы свой итог —
# строка осталась бы `claimed`, `sent_at` пустым, и счётчики «не чаще раза в сутки»
# перестали бы считать вообще. Тот же класс ошибки, что блокер докупки трафика.
RESULT_SQL = """
UPDATE payment_reminders
   SET status = :status,
       tg_result = :tg_result,
       sent_at = CASE WHEN :sent THEN now() ELSE sent_at END
 WHERE payment_id = CAST(:payment_id AS uuid)
"""

# Пропуск с причиной — чтобы владелец видел в сводке, кого и почему не трогали. Строку
# пишем только для «окончательных» причин: too_early придёт снова через пять минут.
SKIP_SQL = """
INSERT INTO payment_reminders (payment_id, user_id, kind, amount, currency, status,
                               skip_reason, invoice_at)
VALUES (CAST(:payment_id AS uuid), :user_id, :kind, :amount, :currency, 'skipped',
        :skip_reason, :invoice_at)
ON CONFLICT (payment_id) DO NOTHING
"""

OPTOUT_SQL = """
INSERT INTO notification_optouts (user_id, kind) VALUES (:user_id, :kind)
ON CONFLICT (user_id, kind) DO NOTHING
"""

# Причины, по которым строку писать НЕ надо: они временные и разрешатся сами.
TRANSIENT_REASONS = frozenset({"too_early"})

# Сводка владельцу и админке: что было за сутки.
SUMMARY_SQL = """
SELECT status, coalesce(skip_reason, tg_result, '') AS detail, count(*) AS n
  FROM payment_reminders
 WHERE created_at > now() - make_interval(hours => :hours)
 GROUP BY 1, 2
 ORDER BY n DESC
"""

# Сработало ли: счёт, по которому написали, оплачен в течение суток после сообщения.
CONVERSION_SQL = """
SELECT count(*) FILTER (WHERE r.status = 'sent') AS sent,
       count(*) FILTER (WHERE r.status = 'sent' AND EXISTS (
           SELECT 1 FROM transactions p
            WHERE p.user_id = r.user_id AND p.status::text = 'COMPLETED'
              AND p.updated_at BETWEEN r.sent_at AND r.sent_at + interval '24 hours')) AS paid_after
  FROM payment_reminders r
 WHERE r.created_at > now() - make_interval(days => :days)
"""


def window_bounds(cfg: dict[str, Any], now: datetime) -> tuple[datetime, datetime]:
    """Границы выборки: [сейчас − потолок; сейчас − задержка]."""
    return (
        now - timedelta(minutes=int(cfg["max_age_minutes"])),
        now - timedelta(minutes=int(cfg["delay_minutes"])),
    )
