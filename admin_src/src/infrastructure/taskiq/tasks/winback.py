"""Win-back истёкших: «вернись, вот скидка» через N дней после окончания (overlay).

Крон (почасовой):
  • ВЫДАЧА: находит USER, у кого подписка истекла ~days_after дней назад (окно поимки)
    и кому win-back ещё не выдавали, ставит одноразовую `users.purchase_discount = %`
    (база гасит её после покупки), пишет строку в winback_grants (дедуп: один win-back
    на юзера) и шлёт напоминание (Web Push + Telegram) «вернись, скидка N%».
  • ПОГАШЕНИЕ: истёкшие неиспользованные промо помечает used=true и снимает скидку
    (если она == выданной).

Конфиг assets/winback.json (админка). Дефолт ВЫКЛ. Ядро не трогаем. Механика скидки —
как у скидки триальщикам (см. trial_discount.py). Best-effort.

ПРЕДЛОЖЕНИЕ собирает offer_for() — чистая функция: «90 дней по цене двух месяцев» по
ценам тарифа человека (services/overlay_winback.py, term_percent) или прежняя «скидка
N %», если посчитать нельзя. Сообщение в Telegram — с кнопкой прямо в оплату нужного
тарифа и срока (кабинет выбирает их по ?plan=&days=).

ВАЛЮТЫ. Скидка одна (`purchase_discount`) и действует на покупку в ЛЮБОЙ валюте, а
соотношение цен сроков в валютах разное. Процент считается по каждой валюте, где у
тарифа есть оба срока, и берётся наибольший: иначе в валюте с «неудобными» ценами
(например, в звёздах) 90 дней со скидкой вышли бы дороже 60, и обещание «по цене двух
месяцев» было бы ложью. Цены в тексте — в валюте магазина по умолчанию
(settings.default_currency), если у тарифа она есть, иначе в рублях, иначе в любой.
"""

import html
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any, Optional

from aiogram import Bot
from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import AppConfig
from src.infrastructure.services.overlay_push import notify_user_push
from src.infrastructure.services.overlay_winback import load_config, term_percent
from src.infrastructure.services.overlay_cron_guard import cron_guard
from src.infrastructure.taskiq.broker import broker

# Окно поимки: юзеров, истёкших от days_after до days_after+CATCH дней назад.
_CATCH_DAYS = 14

_MSG = {
    "ru": (
        "💜 Возвращайтесь — скидка {percent}%",
        "Мы соскучились! Оформите подписку со скидкой {percent}% — "
        "предложение действует ограниченное время.",
    ),
    "en": (
        "💜 Come back — {percent}% off",
        "We miss you! Resubscribe with {percent}% off — limited-time offer.",
    ),
}

# {term_words} — «90 дней» / «91 день» / «92 дня» (_days); {pay_words} — «двух месяцев»
# или «31 дня» / «45 дней» (родительный падеж: «по цене чего»).
_MSG_TERM = {
    "ru": (
        "💜 Возвращайтесь: {term_words} по цене {pay_words}",
        "{plan} на {term_words} — {pay_price} вместо {term_price}. "
        "Предложение действует ограниченное время.",
    ),
    "en": (
        "💜 Come back: {term_words} for the price of {pay_words}",
        "{plan} for {term_words} — {pay_price} instead of {term_price}. Limited-time offer.",
    ),
}

_BUTTON = {
    "ru": {"term": "Вернуться на {term_words}", "percent": "Выбрать тариф"},
    "en": {"term": "Come back for {term_words}", "percent": "Choose a plan"},
}

# «По цене двух месяцев»: слово для срока, по цене которого предлагаем.
_PAY_WORDS = {
    "ru": {30: "месяца", 60: "двух месяцев", 90: "трёх месяцев", 180: "шести месяцев"},
    "en": {30: "one month", 60: "two months", 90: "three months", 180: "six months"},
}
_SYMBOLS = {"RUB": "₽", "USD": "$", "XTR": "⭐"}


def _lang(lang: Optional[str]) -> str:
    return "en" if (lang or "ru").lower().startswith("en") else "ru"


def _money(amount: Any, currency: str) -> str:
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return str(amount)
    text_value = f"{value:.0f}" if value == int(value) else f"{value:.2f}"
    symbol = _SYMBOLS.get(currency, currency)
    return f"{text_value} {symbol}"


def _days(n: int, lang: str) -> str:
    """«1 день», «2 дня», «5 дней», «11 дней», «21 день» / «1 day», «2 days»."""
    n = int(n)
    if lang == "en":
        return f"{n} day" if n == 1 else f"{n} days"
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} день"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} дня"
    return f"{n} дней"


def _days_genitive(n: int, lang: str) -> str:
    """«По цене чего»: «31 дня», «45 дней» (у 2–4 в родительном тоже «дней»)."""
    n = int(n)
    if lang == "en":
        return _days(n, lang)
    return f"{n} дня" if n % 10 == 1 and n % 100 != 11 else f"{n} дней"


def _decimal(value: Any) -> Optional[Decimal]:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _pick_prices(
    prices: dict[tuple[int, str], Any], term: int, pay: int, currency: Optional[str] = None
) -> Optional[tuple[int, str, Any, Any]]:
    """Скидка и валюта для текста: (процент, валюта, цена term, цена pay) или None.

    Процент — НАИБОЛЬШИЙ из нужных по каждой валюте, где у тарифа есть оба срока:
    скидка одна на покупку в любой валюте, и «по цене двух месяцев» должно быть правдой
    в каждой. Валюта, где длинный срок и так не дороже, скидки не требует (0). Цены в
    какой-то валюте не посчитать (мусор или разница больше потолка term_percent) —
    обещание не проверить, и предложения нет: уйдёт прежняя «скидка N %».

    Текст — в `currency` (валюта магазина по умолчанию), иначе в рублях, иначе в любой
    из тех, где длинный срок дороже: «449 ₽ вместо 440 ₽» было бы бессмыслицей.
    """
    both = sorted({c for (_d, c) in prices if (term, c) in prices and (pay, c) in prices})
    needed: dict[str, int] = {}
    for cur in both:
        term_price, pay_price = prices[(term, cur)], prices[(pay, cur)]
        pct = term_percent(term_price, pay_price)
        if pct is None:
            t, p = _decimal(term_price), _decimal(pay_price)
            if t is None or p is None or t <= 0 or p <= 0 or p < t:
                return None
            pct = 0  # длинный срок в этой валюте и так не дороже
        needed[cur] = pct
    shown = [c for c in both if needed[c] > 0]
    if not shown:
        return None
    shown.sort(key=lambda c: (c != currency, c != "RUB", c))
    cur = shown[0]
    return max(needed.values()), cur, prices[(term, cur)], prices[(pay, cur)]


def offer_for(
    *,
    lang: Optional[str],
    plan_code: Optional[str],
    plan_name: Optional[str],
    prices: dict[tuple[int, str], Any],
    cfg: dict[str, Any],
    currency: Optional[str] = None,
) -> dict[str, Any]:
    """Что предлагаем человеку: вид, процент скидки, текст и путь кнопки. Чистая функция.

    `currency` — валюта магазина по умолчанию: в ней цены в тексте, если у тарифа она есть.
    """
    lg = _lang(lang)
    term, pay = int(cfg["term_days"]), int(cfg["pay_days"])
    if cfg.get("mode") == "term" and plan_code:
        picked = _pick_prices(prices, term, pay, currency)
        if picked is not None:
            pct, cur, term_price, pay_price = picked
            title, body = _MSG_TERM[lg]
            term_words = _days(term, lg)
            pay_words = _PAY_WORDS[lg].get(pay) or _days_genitive(pay, lg)
            return {
                "kind": "term",
                "percent": pct,
                "title": title.format(term_words=term_words, pay_words=pay_words),
                "body": body.format(
                    plan=html.escape(plan_name or plan_code),
                    term_words=term_words,
                    pay_price=_money(pay_price, cur),
                    term_price=_money(term_price, cur),
                ),
                "button": _BUTTON[lg]["term"].format(term_words=term_words),
                "path": f"/billing?plan={plan_code}&days={term}",
            }
    percent = int(cfg["percent"])
    title, body = _MSG.get(lg, _MSG["ru"])
    return {
        "kind": "percent",
        "percent": percent,
        "title": title.format(percent=percent),
        "body": body.format(percent=percent),
        "button": _BUTTON[lg]["percent"],
        "path": "/billing",
    }


def _keyboard(cabinet_url: str, offer: dict[str, Any]) -> Any:
    base = (cabinet_url or "").strip().rstrip("/")
    if not base:
        return None  # пустой url Telegram не принимает и отверг бы сообщение целиком
    from aiogram.types import InlineKeyboardButton
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=offer["button"], url=f"{base}{offer['path']}"))
    return builder.as_markup()


# Кому писать и цены его тарифа на оба срока — одной выборкой, строкой на цену.
CANDIDATES_SQL = """
SELECT u.id                         AS user_id,
       lower(u.language::text)      AS lang,
       u.telegram_id                AS telegram_id,
       p.public_code                AS plan_code,
       p.name                       AS plan_name,
       d.days                       AS days,
       pr.currency::text            AS currency,
       pr.price                     AS price
  FROM users u
  JOIN subscriptions s ON u.current_subscription_id = s.id
  LEFT JOIN winback_grants w ON w.user_id = u.id
  LEFT JOIN plans p
         ON p.is_active
        AND (s.plan_snapshot->>'id') ~ '^-?[0-9]+$'
        AND p.id = (s.plan_snapshot->>'id')::int
  LEFT JOIN plan_durations d ON d.plan_id = p.id AND d.days IN (:term, :pay)
  LEFT JOIN plan_prices pr ON pr.plan_duration_id = d.id
 WHERE u.role = 'USER' AND u.is_blocked = false
   AND s.expire_at < now() - make_interval(days => :d)
   AND s.expire_at > now() - make_interval(days => :d + :catch)
   AND w.user_id IS NULL
"""


# Валюта магазина по умолчанию (одна строка настроек базы) — в ней цены в тексте.
DEFAULT_CURRENCY_SQL = "SELECT default_currency::text FROM settings ORDER BY id LIMIT 1"


async def _default_currency(session: AsyncSession) -> Optional[str]:
    row = (await session.execute(text(DEFAULT_CURRENCY_SQL))).first()
    return str(row[0]) if row is not None and row[0] else None


def group_candidates(rows: list[Any]) -> list[dict[str, Any]]:
    """Строки выборки (по строке на цену) → по человеку с его ценами."""
    people: dict[int, dict[str, Any]] = {}
    for r in rows:
        person = people.setdefault(
            int(r.user_id),
            {"user_id": int(r.user_id), "lang": r.lang, "telegram_id": r.telegram_id,
             "plan_code": r.plan_code, "plan_name": r.plan_name, "prices": {}},
        )
        if r.days is not None and r.currency and r.price is not None:
            person["prices"][(int(r.days), str(r.currency))] = r.price
    return list(people.values())


async def _expire_pass(session: AsyncSession) -> int:
    expired = (
        await session.execute(
            text(
                "SELECT user_id, percent FROM winback_grants "
                "WHERE used = false AND expires_at < now()"
            )
        )
    ).all()
    for uid, percent in expired:
        await session.execute(
            text(
                "UPDATE users SET purchase_discount = 0 "
                "WHERE id = :u AND purchase_discount = :p"
            ),
            {"u": uid, "p": percent},
        )
    if expired:
        await session.execute(
            text(
                "UPDATE winback_grants SET used = true "
                "WHERE used = false AND expires_at < now()"
            )
        )
        await session.commit()
    return len(expired)


@broker.task(schedule=[{"cron": "37 * * * *"}], retry_on_error=False)
@inject(patch_module=True)
@cron_guard("winback", "Скидка «возвращайтесь»")
async def run_winback(
    session: FromDishka[AsyncSession],
    config: FromDishka[AppConfig],
) -> None:
    await _expire_pass(session)

    cfg = load_config()
    if not cfg["enabled"]:
        return

    days_after = cfg["days_after"]
    lifetime = cfg["lifetime_hours"]

    rows = (
        await session.execute(
            text(CANDIDATES_SQL),
            {"d": days_after, "catch": _CATCH_DAYS, "term": cfg["term_days"], "pay": cfg["pay_days"]},
        )
    ).all()
    if not rows:
        return
    currency = await _default_currency(session)

    bot: Bot | None = None
    try:
        bot = Bot(config.bot.token.get_secret_value())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"winback: не смог создать Bot ({e}) — TG-напоминания пропущены")

    cabinet_url = getattr(config, "web_cabinet_url", "") or ""
    granted = 0
    by_kind = {"term": 0, "percent": 0}
    for person in group_candidates(rows):
        uid, lang, tg_id = person["user_id"], person["lang"], person["telegram_id"]
        offer = offer_for(
            lang=lang,
            plan_code=person["plan_code"],
            plan_name=person["plan_name"],
            prices=person["prices"],
            cfg=cfg,
            currency=currency,
        )
        percent = offer["percent"]
        try:
            await session.execute(
                text(
                    "UPDATE users SET purchase_discount = GREATEST(purchase_discount, :p) "
                    "WHERE id = :u"
                ),
                {"p": percent, "u": uid},
            )
            await session.execute(
                text(
                    "INSERT INTO winback_grants (user_id, percent, granted_at, expires_at, used) "
                    "VALUES (:u, :p, now(), now() + make_interval(hours => :h), false) "
                    "ON CONFLICT (user_id) DO NOTHING"
                ),
                {"u": uid, "p": percent, "h": lifetime},
            )
            await session.commit()
            granted += 1
            by_kind[offer["kind"]] += 1
        except Exception as e:  # noqa: BLE001
            await session.rollback()
            logger.warning(f"winback: выдача user_id={uid} не удалась: {e}")
            continue

        # Web Push: текст собран заранее, поэтому шаблон — из готовых строк.
        await notify_user_push(
            session,
            SimpleNamespace(id=uid, language=lang),
            # Под обоими ключами: язык выбирает сам notify_user_push, а фолбэк у него — ru.
            {key: (offer["title"], html.unescape(offer["body"])) for key in ("ru", "en")},
            url=offer["path"],
            tag="winback",
        )
        if bot is not None and tg_id:
            try:
                # parse_mode обязателен: без него <b> пришёл бы человеку текстом.
                await bot.send_message(
                    int(tg_id),
                    f"<b>{html.escape(offer['title'])}</b>\n\n{offer['body']}",
                    parse_mode="HTML",
                    reply_markup=_keyboard(cabinet_url, offer),
                )
            except Exception as e:  # noqa: BLE001
                logger.debug(f"winback: TG user_id={uid} не доставлено: {e}")

    if bot is not None:
        try:
            await bot.session.close()
        except Exception:  # noqa: BLE001
            pass

    if granted:
        logger.info(
            f"winback: предложение выдано {granted} шт. "
            f"(«{cfg['term_days']} по цене {cfg['pay_days']}» — {by_kind['term']}, скидка — {by_kind['percent']})"
        )
