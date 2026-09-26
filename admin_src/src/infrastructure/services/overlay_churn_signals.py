"""Сигналы до ухода: правила, тексты, кнопки и SQL (overlay).

ЗАЧЕМ. Люди уходят молча. Из 200 пробных 169 не подключились ни разу и ничего не
написали; платящие перестают ПОЛЬЗОВАТЬСЯ раньше, чем перестают платить, — и мы узнаём
об этом по непродлённой подписке, когда спрашивать уже некого. Два сигнала, пока
человек ещё с нами:

  1. «Всё работает?» (`check`) — один раз на человека, примерно через сутки после
     ПЕРВОГО подключения. Ответ одной кнопкой: «✅ Всё работает» — спасибо;
     «❌ Не работает» — самодиагностика кабинета (/support, там мастер с паспортом
     обращения) и поддержка. Ответ сохраняется: владелец видит долю «не работает».
  2. «Давно не подключался» (`idle`) — тому, у кого действующая НЕ пробная подписка
     (не обязательно оплаченная: выданные вручную, подарочные и импортированные —
     тоже), последнее подключение старше N дней, а до конца подписки ещё есть время.
     Одно мягкое сообщение, не чаще раза в K дней. «Никогда не подключался» — это
     другая история: её ведёт панель событием not_connected (выпуск 1.4.6), и сюда
     такие люди не попадают.

ДАННЫЕ О ПОДКЛЮЧЕНИИ — ТОЛЬКО ИЗ ПАНЕЛИ, и молчание панели ≠ «не подключался». Время
первого подключения и последнего онлайна живут в Remnawave. Панель не ответила (или
человека в её ответе нет) — пропускаем, а не записываем в «давно не был». Эту ошибку
уже ловили в кабинете: моргнувшая панель превращала платящего в «подписки нет».

НЕ ПАЧКОЙ ПО ИСТОРИИ. Оба сигнала срабатывают только в ОКНЕ после события: вопрос —
между сутками и двумя после первого подключения, «давно не был» — в первые дни после
того, как простой перевалил за N. Включили тумблер — написали только тем, кто в окно
попал сейчас, а не всем, кто подключался когда-то или пропал полгода назад.

Конфиг — assets/churn_signals.json (админка правит на лету). Файла нет — ВЫКЛЮЧЕНО,
оба сигнала.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger

ASSETS_DIR = Path(os.environ.get("RS_ASSETS_DIR", "/opt/remnashop/assets"))
CONFIG_PATH = ASSETS_DIR / "churn_signals.json"
# Итог последнего прогона: крон пишет, админка читает. Нужен ради одной вещи —
# владелец должен ВИДЕТЬ, что панель молчала и поэтому никому не писали, а не гадать,
# почему фича «ничего не делает».
LAST_RUN_PATH = ASSETS_DIR / "churn_signals_last_run.json"

KIND_CHECK = "check"
KIND_IDLE = "idle"

# Свои виды отказа в notification_optouts. Два вида — чтобы в сводке было видно, из
# какого сообщения человек сказал «не надо». Действует отказ на ОБА сигнала: кто
# попросил не присылать вопрос «Всё работает?», тот не ждёт и «всё в порядке?».
OPTOUT_CHECK = "churn_check"
OPTOUT_IDLE = "churn_idle"
OPTOUT_BY_KIND = {KIND_CHECK: OPTOUT_CHECK, KIND_IDLE: OPTOUT_IDLE}

# Окна после события. Не в настройках намеренно: это не вкус владельца, а защита от
# рассылки по истории, и крутить её незачем. Сутки у вопроса и трое у простоя —
# запас на перезапуск воркера или выключенный на выходные бот.
CHECK_WINDOW_HOURS = 24
IDLE_WINDOW_DAYS = 3

DEFAULT_CONFIG: dict[str, Any] = {
    "check_enabled": False,       # «Всё работает?» — по умолчанию выключено
    "check_delay_hours": 24,      # через сколько после первого подключения спрашивать
    "idle_enabled": False,        # «Давно не подключался» — по умолчанию выключено
    "idle_days": 7,               # N: сколько дней без подключения считать простоем
    "idle_cooldown_days": 30,     # K: не чаще раза в K дней на человека
    "idle_min_days_left": 3,      # до конца подписки должно оставаться хотя бы столько
}

_LIMITS = {
    "check_delay_hours": (2, 168),
    "idle_days": (3, 60),
    "idle_cooldown_days": (7, 180),
    "idle_min_days_left": (1, 30),
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
    for key in ("check_enabled", "idle_enabled"):
        cfg[key] = _flag(data.get(key), DEFAULT_CONFIG[key])
    for key, (lo, hi) in _LIMITS.items():
        cfg[key] = _clamp(data.get(key), DEFAULT_CONFIG[key], lo, hi)
    return cfg


def load_config() -> dict[str, Any]:
    try:
        return normalize(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return dict(DEFAULT_CONFIG)
    except Exception as exc:  # noqa: BLE001 — сломанный файл не включает рассылку
        logger.warning(f"churn_signals: конфиг не прочитан ({exc}) — считаю выключенным")
        return dict(DEFAULT_CONFIG)


def save_config(data: dict[str, Any]) -> dict[str, Any]:
    cfg = normalize(data)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def any_enabled(cfg: Optional[dict[str, Any]] = None) -> bool:
    cfg = cfg or load_config()
    return bool(cfg["check_enabled"] or cfg["idle_enabled"])


def save_last_run(report: dict[str, Any]) -> None:
    try:
        LAST_RUN_PATH.parent.mkdir(parents=True, exist_ok=True)
        LAST_RUN_PATH.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 — журнал не важнее прогона
        logger.warning(f"churn_signals: итог прогона не сохранён: {exc}")


def clear_last_run() -> None:
    """Сигналы выключили — итог прохода устарел. Иначе при следующем включении
    админка до первого нового прохода показывала бы старый итог (и красное «панель
    молчала» недельной давности) как текущий."""
    try:
        LAST_RUN_PATH.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001 — журнал не важнее настроек
        logger.warning(f"churn_signals: старый итог прогона не удалён: {exc}")


def load_last_run() -> Optional[dict[str, Any]]:
    try:
        data = json.loads(LAST_RUN_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — файла нет до первого прогона
        return None
    return data if isinstance(data, dict) else None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: Any) -> Optional[datetime]:
    """Время из панели или базы. Без зоны — UTC: панель отдаёт `Z`, база — timestamptz."""
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# Статус пользователя в панели, при котором подключение вообще возможно. DISABLED
# (в том числе заморозка — она выключает пользователя в панели), LIMITED (кончился
# трафик) и EXPIRED — это «не может», а не «не хочет»: вопрос «всё работает?» и «давно
# не видели вас» им звучат издёвкой, а причину уже объясняют другие сообщения.
PANEL_ACTIVE = "ACTIVE"


def panel_status(value: Any) -> Optional[str]:
    """Статус из ответа панели: enum remnapy или строка → «ACTIVE»; нет — None."""
    value = getattr(value, "value", value)
    if value is None:
        return None
    text_value = str(value).strip().upper()
    return text_value or None


@dataclass(frozen=True)
class PanelSeen:
    """Что панель знает о подключениях человека. Времена могут быть пустыми.

    `status` без значения — «не знаем», и тогда не пишем: то же правило, что для
    молчания панели. На бою статус есть всегда (поле обязательное в ответе панели).
    """

    first_connected_at: Optional[datetime]
    online_at: Optional[datetime]
    status: Optional[str] = None


@dataclass(frozen=True)
class Candidate:
    """Человек с активной подпиской и всё, что о нём знает НАША база."""

    user_id: int
    telegram_id: Optional[int]
    lang: Optional[str]
    is_blocked: bool
    is_bot_blocked: bool
    role: str
    remna_uuid: str
    is_trial: bool
    expire_at: datetime
    opted_out: bool
    check_done: bool                    # вопрос «Всё работает?» уже был (любой исход)
    idle_last_sent: Optional[datetime]  # когда последний раз ДОШЛО «давно не был»
    idle_open_id: Optional[int] = None  # отправленное «давно не был» без возвращения
    idle_open_sent_at: Optional[datetime] = None
    frozen: bool = False                # активная заморозка (subscription_freezes.active)


def _common_reason(c: Candidate) -> Optional[str]:
    """Кому нельзя писать ничего из этого — одинаково для обоих сигналов."""
    if (c.role or "USER").upper() != "USER":
        return "staff"
    if c.telegram_id is None:
        return "no_telegram"
    if c.is_blocked or c.is_bot_blocked:
        return "blocked"
    if c.opted_out:
        return "opted_out"
    if c.frozen:
        # Сам поставил подписку на паузу: не подключается, потому что так решил, и
        # «давно не видели вас» ему — ровно то давление, которого фича избегает.
        return "frozen"
    return None


def _panel_reason(seen: PanelSeen) -> Optional[str]:
    """Панель ответила, но пользователь там не ACTIVE — подключиться он не может."""
    if panel_status(seen.status) != PANEL_ACTIVE:
        return "panel_inactive"
    return None


def decide_check(
    c: Candidate, seen: Optional[PanelSeen], cfg: dict[str, Any], now: datetime
) -> Optional[str]:
    """Почему НЕ спрашиваем «Всё работает?». None — спрашиваем."""
    if c.check_done:
        return "already_asked"
    reason = _common_reason(c)
    if reason:
        return reason
    if seen is None:
        # Панель молчала или человека в её ответе нет: НЕ знаем — значит, не пишем.
        return "no_panel_data"
    reason = _panel_reason(seen)
    if reason:
        return reason
    first = as_utc(seen.first_connected_at)
    if first is None:
        # Ещё не подключался — это событие not_connected панели, а не наш вопрос.
        return "not_connected_yet"
    age_hours = (now - first).total_seconds() / 3600
    delay = int(cfg["check_delay_hours"])
    if age_hours < delay:
        return "too_early"
    if age_hours > delay + CHECK_WINDOW_HOURS:
        # Подключился давно: «всё работает?» через неделю звучит как опрос ради опроса,
        # а включённый тумблер не должен спросить всех, кто когда-либо подключался.
        return "too_late"
    return None


def decide_idle(
    c: Candidate, seen: Optional[PanelSeen], cfg: dict[str, Any], now: datetime
) -> Optional[str]:
    """Почему НЕ пишем «давно не подключался». None — пишем."""
    reason = _common_reason(c)
    if reason:
        return reason
    if c.is_trial:
        # Пробным свой сигнал — вопрос после первого подключения. Простой пробника за
        # неделю — это конец пробного периода, а не тревожный знак.
        return "trial"
    expire_at = as_utc(c.expire_at)
    if expire_at is None or expire_at - now < timedelta(days=int(cfg["idle_min_days_left"])):
        # Подписка вот-вот кончится: об этом пишут напоминания об окончании, и два
        # сообщения подряд про одно и то же — уже давление.
        return "ends_soon"
    if seen is None:
        return "no_panel_data"
    reason = _panel_reason(seen)
    if reason:
        return reason
    online = as_utc(seen.online_at)
    if online is None:
        # Не подключался НИ РАЗУ — не наш случай (событие not_connected панели).
        return "never_connected"
    idle = now - online
    days = int(cfg["idle_days"])
    if idle < timedelta(days=days):
        return "recently_online"
    if idle > timedelta(days=days + IDLE_WINDOW_DAYS):
        # Простой начался давно: ровно та «пачка по истории», которую включение
        # тумблера разослать не должно.
        return "idle_too_long"
    last = as_utc(c.idle_last_sent)
    if last is not None and now - last < timedelta(days=int(cfg["idle_cooldown_days"])):
        return "cooldown"
    return None


# ── тексты ───────────────────────────────────────────────────────────────────


def is_en(lang: Optional[str]) -> bool:
    return (lang or "ru").lower().startswith("en")


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


def days_ru(n: int) -> str:
    return f"{n} {_plural_ru(n, 'день', 'дня', 'дней')}"


# Тексты — целиком, без фигурных скобок: push прогоняет строку через .format.
def check_message(lang: Optional[str]) -> str:
    if is_en(lang):
        return (
            "<b>👋 Is everything working?</b>\n\n"
            "You connected recently — let us know with one tap whether everything is fine. "
            "If something is off, we'll help sort it out."
        )
    return (
        "<b>👋 Всё работает?</b>\n\n"
        "Вы недавно подключились — подскажите одной кнопкой, всё ли в порядке. "
        "Если что-то не так, поможем разобраться."
    )


def idle_message(days: int, lang: Optional[str]) -> str:
    """Мягко: мы не знаем, сломалось ли что-то или человек просто в отпуске."""
    if is_en(lang):
        return (
            "<b>🔌 We haven't seen you online for a while</b>\n\n"
            f"Your last connection was {days} days ago. Is everything OK? If it stopped "
            "working, the self-check in your account finds the cause in a minute."
        )
    return (
        "<b>🔌 Давно не видели вас в сети</b>\n\n"
        f"Последнее подключение — {days_ru(days)} назад. Всё в порядке? Если перестало "
        "работать, самопроверка в кабинете за минуту найдёт причину."
    )


def broken_message(lang: Optional[str]) -> str:
    """Ответ на «❌ Не работает»: не «напишите нам», а куда нажать прямо сейчас."""
    if is_en(lang):
        return (
            "<b>Let's sort it out</b>\n\n"
            "The self-check in your account finds the usual cause in a minute and tells you "
            "what to do. If it doesn't help, the support request goes out with all the "
            "details attached."
        )
    return (
        "<b>Давайте разберёмся</b>\n\n"
        "Самопроверка в кабинете за минуту найдёт частую причину и подскажет, что сделать. "
        "Не поможет — обращение уйдёт в поддержку сразу со всеми подробностями."
    )


WORDS = {
    "ru": {
        "works": "✅ Всё работает",
        "broken": "❌ Не работает",
        "off": "🔕 Не присылать такое",
        "selfcheck": "🩺 Самопроверка",
        "check_now": "🩺 Проверить подключение",
        "support": "💬 Поддержка",
        "write_support": "💬 Написать в поддержку",
        "thanks": "Спасибо! Рады, что всё работает.",
        "sorry": "Сейчас поможем разобраться.",
        "off_done": "Больше не пришлём такие сообщения.",
        "stale": "Этот вопрос уже неактуален.",
        "retry": "Не получилось, попробуйте ещё раз",
    },
    "en": {
        "works": "✅ All good",
        "broken": "❌ Not working",
        "off": "🔕 Don't send these",
        "selfcheck": "🩺 Self-check",
        "check_now": "🩺 Check my connection",
        "support": "💬 Support",
        "write_support": "💬 Contact support",
        "thanks": "Thanks! Glad it works.",
        "sorry": "Let's sort it out.",
        "off_done": "We won't send these again.",
        "stale": "This question is no longer relevant.",
        "retry": "Something went wrong, please try again",
    },
}


def words_for(lang: Optional[str]) -> dict[str, str]:
    return WORDS["en"] if is_en(lang) else WORDS["ru"]


def cabinet_link(base_url: Optional[str], path: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    return f"{base}{path}" if base else ""


def support_link(config: Any) -> str:
    """Та же ссылка, что у кнопки поддержки базы: t.me/<support_username>."""
    try:
        name = config.bot.support_username.get_secret_value()
    except Exception:  # noqa: BLE001 — нет имени поддержки: кнопки просто не будет
        return ""
    name = (name or "").strip().lstrip("@")
    return f"https://t.me/{name}" if name else ""


# ── кнопки ───────────────────────────────────────────────────────────────────

# Префикс колбэков. Своё пространство имён: наши роутеры встают ПЕРЕД базовыми, и
# широкий фильтр перехватил бы чужие кнопки (правило «не перехватывать чужие ручки»).
CALLBACK_PREFIX = "rs_sig:"
ANSWER_BY_CODE = {"ok": "works", "bad": "broken"}


def answer_callback(code: str, signal_id: int) -> str:
    return f"{CALLBACK_PREFIX}{code}:{int(signal_id)}"


def off_callback(kind: str) -> str:
    return f"{CALLBACK_PREFIX}off:{kind}"


def parse_callback(data: Optional[str]) -> Optional[tuple[str, str]]:
    """`rs_sig:ok:17` → ("works", "17"), `rs_sig:off:idle` → ("off", "idle").

    Всё, что не наше или испорчено, — None: кнопка молча гасит «часики», в базу
    ничего не пишется.
    """
    if not data or not data.startswith(CALLBACK_PREFIX):
        return None
    parts = data[len(CALLBACK_PREFIX):].split(":")
    if len(parts) != 2:
        return None
    code, arg = parts
    if code == "off":
        return ("off", arg) if arg in OPTOUT_BY_KIND else None
    if code in ANSWER_BY_CODE and arg.isdigit():
        return (ANSWER_BY_CODE[code], arg)
    return None


def check_keyboard(signal_id: int, lang: Optional[str] = None) -> Any:
    from aiogram.types import InlineKeyboardButton
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    w = words_for(lang)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=w["works"], callback_data=answer_callback("ok", signal_id)),
        InlineKeyboardButton(text=w["broken"], callback_data=answer_callback("bad", signal_id)),
    )
    builder.row(InlineKeyboardButton(text=w["off"], callback_data=off_callback(KIND_CHECK)))
    return builder.as_markup()


def idle_keyboard(cabinet_url: str, support_url: str, lang: Optional[str] = None) -> Any:
    """Пустой url Telegram не принимает и отверг бы сообщение целиком — без адреса
    кнопки нет вовсе."""
    from aiogram.types import InlineKeyboardButton
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    w = words_for(lang)
    builder = InlineKeyboardBuilder()
    selfcheck = cabinet_link(cabinet_url, "/support")
    if selfcheck:
        builder.row(InlineKeyboardButton(text=w["selfcheck"], url=selfcheck))
    if support_url:
        builder.row(InlineKeyboardButton(text=w["support"], url=support_url))
    builder.row(InlineKeyboardButton(text=w["off"], callback_data=off_callback(KIND_IDLE)))
    return builder.as_markup()


def broken_keyboard(cabinet_url: str, support_url: str, lang: Optional[str] = None) -> Any:
    from aiogram.types import InlineKeyboardButton
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    w = words_for(lang)
    builder = InlineKeyboardBuilder()
    selfcheck = cabinet_link(cabinet_url, "/support")
    if selfcheck:
        builder.row(InlineKeyboardButton(text=w["check_now"], url=selfcheck))
    if support_url:
        builder.row(InlineKeyboardButton(text=w["write_support"], url=support_url))
    return builder.as_markup() if (selfcheck or support_url) else None


# ── SQL ──────────────────────────────────────────────────────────────────────

# Кандидаты — все с действующей подпиской. Решение принимают decide_*, чтобы каждая
# причина молчания была видна в итоге прогона, а не терялась в WHERE. Данных о
# подключениях здесь нет: они живут в панели и приходят отдельно.
CANDIDATES_SQL = """
SELECT u.id                                    AS user_id,
       u.telegram_id                           AS telegram_id,
       lower(u.language::text)                 AS lang,
       u.is_blocked                            AS is_blocked,
       u.is_bot_blocked                        AS is_bot_blocked,
       coalesce(u.role::text, 'USER')          AS role,
       s.user_remna_id::text                   AS remna_uuid,
       s.is_trial                              AS is_trial,
       s.expire_at                             AS expire_at,
       EXISTS (SELECT 1 FROM notification_optouts o
                WHERE o.user_id = u.id
                  AND o.kind IN (:optout_check, :optout_idle))           AS opted_out,
       EXISTS (SELECT 1 FROM churn_signals c
                WHERE c.user_id = u.id AND c.kind = 'check')             AS check_done,
       EXISTS (SELECT 1 FROM subscription_freezes f
                WHERE f.user_id = u.id AND f.active)                     AS frozen,
       (SELECT max(c.sent_at) FROM churn_signals c
         WHERE c.user_id = u.id AND c.kind = 'idle' AND c.status = 'sent') AS idle_last_sent,
       io.id                                   AS idle_open_id,
       io.sent_at                              AS idle_open_sent_at
  FROM users u
  JOIN subscriptions s ON s.id = u.current_subscription_id
  LEFT JOIN LATERAL (
        SELECT c.id, c.sent_at
          FROM churn_signals c
         WHERE c.user_id = u.id AND c.kind = 'idle' AND c.status = 'sent'
           AND c.returned_at IS NULL AND c.sent_at > :returned_since
         ORDER BY c.sent_at DESC
         LIMIT 1) io ON true
 WHERE s.status::text = 'ACTIVE'
   AND s.expire_at > :now
 ORDER BY u.id DESC
 LIMIT :limit
"""
# ORDER BY u.id DESC, а не по возрастанию: потолок в выборке — страховка от прохода
# по всей базе, но при сортировке от старых аккаунтов на установке крупнее потолка
# новые не попадали бы в неё НИКОГДА. А сигнал «Всё работает?» адресован именно
# новичкам — через сутки после первого подключения. С возрастающим порядком он на
# такой установке не ушёл бы ни разу, и в админке были бы честные нули.

# Захват ДО отправки. Второй вопрос тому же человеку и второе «давно не был» на тот
# же простой запрещают уникальные индексы (миграция 0015): не вставилось — RETURNING
# пуст, и мы не шлём. Именно RETURNING, а не «проверили выше»: два прогона
# параллельно видят одну и ту же пустоту.
CLAIM_SQL = """
INSERT INTO churn_signals (user_id, kind, status, seen_at, claimed_at)
VALUES (:user_id, :kind, 'claimed', :seen_at, :now)
ON CONFLICT DO NOTHING
RETURNING id
"""

# Итог отправки. `:sent` — отдельным параметром, а не сравнением `:status = 'sent'`:
# один bind и как значение, и как текст в сравнении asyncpg отвергает (урок
# напоминания об оплате — на подделке сессии это выглядело бы рабочим).
RESULT_SQL = """
UPDATE churn_signals
   SET status = :status,
       tg_result = :tg_result,
       sent_at = CASE WHEN :sent THEN CAST(:now AS timestamptz) ELSE sent_at END
 WHERE id = :id
"""

# Вернулся ли человек после «давно не был»: панель увидела подключение позже письма.
RETURNED_SQL = """
UPDATE churn_signals
   SET returned_at = :returned_at
 WHERE id = :id AND returned_at IS NULL
"""

# Ответ на «Всё работает?». Проверка владельца строки — в самом UPDATE: колбэк несёт
# id строки, и чужой id не должен менять чужой ответ. Повторное нажатие переписывает
# ответ: человек мог сначала ошибиться кнопкой.
ANSWER_SQL = """
UPDATE churn_signals
   SET answer = :answer,
       answered_at = now()
 WHERE id = :id AND user_id = :user_id AND kind = 'check'
RETURNING id
"""

OPTOUT_SQL = """
INSERT INTO notification_optouts (user_id, kind) VALUES (:user_id, :kind)
ON CONFLICT (user_id, kind) DO NOTHING
"""

# Сводка для админки: что было за :days дней.
STATS_SQL = """
SELECT count(*) FILTER (WHERE kind = 'check' AND status = 'sent')        AS check_sent,
       count(*) FILTER (WHERE kind = 'check' AND answer IS NOT NULL)     AS check_answered,
       count(*) FILTER (WHERE kind = 'check' AND answer = 'works')       AS check_works,
       count(*) FILTER (WHERE kind = 'check' AND answer = 'broken')      AS check_broken,
       count(*) FILTER (WHERE kind = 'check' AND status = 'failed')      AS check_failed,
       count(*) FILTER (WHERE kind = 'idle' AND status = 'sent')         AS idle_sent,
       count(*) FILTER (WHERE kind = 'idle' AND returned_at IS NOT NULL) AS idle_returned,
       count(*) FILTER (WHERE kind = 'idle' AND status = 'failed')       AS idle_failed
  FROM churn_signals
 WHERE created_at > now() - make_interval(days => :days)
"""

# Кто ответил «не работает» — владельцу есть кому написать первым.
BROKEN_SQL = """
SELECT user_id, answered_at
  FROM churn_signals
 WHERE kind = 'check' AND answer = 'broken'
 ORDER BY answered_at DESC
 LIMIT :limit
"""

OPTOUTS_SQL = """
SELECT kind, count(*) AS n
  FROM notification_optouts
 WHERE kind IN (:optout_check, :optout_idle)
 GROUP BY kind
"""


def share_percent(part: int, whole: int) -> Optional[int]:
    """Доля в процентах; из пустого — None, а не ноль: «0 % не работает» из нуля
    ответов читалось бы как хорошая новость."""
    if whole <= 0:
        return None
    return round(part * 100 / whole)
