"""«Все места для устройств заняты» — сообщение в Telegram (overlay).

ЗАЧЕМ. Лимит устройств — самое частое, во что упираются платящие: у 21 из 23 активных
с лимитом 1 заняты все места. Когда место кончается, панель молча отказывает новому
устройству, а приложение показывает английское «Limit of devices reached» без ссылки.
Человек не понимает, что случилось, и не знает, что место можно докупить.

ПОЧЕМУ ПИШЕМ, КОГДА ЗАНЯТО ПОСЛЕДНЕЕ МЕСТО, А НЕ В МОМЕНТ ОТКАЗА. Сам отказ поймать
нельзя: вебхука «лимит устройств» у панели нет, а запрос, которому она отказала, в
журнал запросов подписки не попадает — на 3.4.4 запись идёт ПОСЛЕ проверки лимита
(`checkHwidDeviceLimit` возвращает раньше). Надёжный сигнал — список устройств: когда
их стало столько же, сколько мест, следующее уже не подключится. Об этом и пишем —
заранее, пока человек ещё не столкнулся с отказом.

КОГДА ПИШЕМ (decide). Переход «было свободное место → заняты все» между двумя снимками
крона. Освободил место и снова занял — напишем снова, но не чаще `cooldown_days`.
Первый проход после включения — baseline: кто уже заполнен, тем НЕ пишем, иначе
включение разослало бы сообщение всем, кто давно живёт на своём лимите и доволен.
Это же правило — для «выключили и снова включили» и для крона, который долго падал:
снимок с прошлого прохода устарел, и сравнение с ним дало бы «только что заполнил»
всем, кто заполнил места за паузу, — рассылку по истории. Поэтому включение в
админке сбрасывает baseline (save_config), а снимок старше STALE_AFTER крон сам
считает устаревшим и делает проход baseline (snapshot_stale).

КОМУ. Обычный пользователь (не персонал) с Telegram; не заблокирован магазином и не
заблокировал бота; текущая подписка ACTIVE и НЕ пробная (кнопка «докупить место» у
пробной не работает, а уговаривать триальщика платить за место — не то сообщение);
лимит больше нуля (0 в панели — безлимит); не нажимал «Не присылать такое».

СОСТОЯНИЕ — файл, а не таблица: его пишет крон воркера, а админка только снимает
отметку baseline при включении («не присылать» — строка в notification_optouts, её
пишет бот). Запись атомарная. Если включение совпадёт с проходом крона и тот
перезапишет снятую отметку, выручит snapshot_stale: выключенный крон снимок не
обновлял, и к этому моменту он уже старый.

По умолчанию выключено: тумблер в админке, раздел «Докупка устройств».
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
CONFIG_PATH = ASSETS_DIR / "device_full.json"
STATE_PATH = ASSETS_DIR / "device_full_state.json"

OPTOUT_KIND = "device_full"
# Своё пространство имён callback-данных — не пересекаемся с базой и соседями.
OPTOUT_CALLBACK = "rs_devfull_off"

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,     # по умолчанию выключено
    "cooldown_days": 7,   # не чаще раза в N дней на человека
}
_LIMITS = {"cooldown_days": (1, 90)}

# Потолок сообщений за проход: хвост заберёт следующий проход через 20 минут, а
# полсотни сообщений подряд — это флуд-лимит Telegram.
MAX_PER_RUN = 30

# Снимок старше этого — устарел: крон ходит раз в 20 минут, три пропущенных прохода
# подряд значат, что его выключали или он падал. Сравнивать с таким снимком нельзя —
# переходом «только что заполнил» оказалось бы всё, что случилось за паузу.
STALE_AFTER = timedelta(hours=1)


# ── настройки ─────────────────────────────────────────────────────────────────


def normalize(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return dict(DEFAULT_CONFIG)
    out = dict(DEFAULT_CONFIG)
    # Строгое `is True`: строка "false" из руками правленого файла не должна включать
    # рассылку (грабля выключателя докупки).
    out["enabled"] = data.get("enabled") is True
    for key, (lo, hi) in _LIMITS.items():
        try:
            out[key] = max(lo, min(hi, int(data.get(key, DEFAULT_CONFIG[key]))))
        except (TypeError, ValueError):
            out[key] = DEFAULT_CONFIG[key]
    return out


def load_config() -> dict[str, Any]:
    try:
        return normalize(json.loads(CONFIG_PATH.read_text("utf-8")))
    except FileNotFoundError:
        return dict(DEFAULT_CONFIG)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"device_full: конфиг нечитаем ({exc}) — по умолчанию (выключено)")
        return dict(DEFAULT_CONFIG)


def save_config(data: Any) -> dict[str, Any]:
    cfg = normalize(data)
    if cfg["enabled"] and not load_config()["enabled"]:
        # Включили: снимок остался с того времени, когда рассылка ещё работала, и
        # первый проход после паузы написал бы всем, кто заполнил места за неё.
        # Сбрасываем ДО записи настроек: не записались настройки — лишний baseline
        # безвреден, а наоборот вышла бы рассылка по истории.
        reset_baseline()
    _atomic_write(CONFIG_PATH, cfg)
    return cfg


# ── состояние ─────────────────────────────────────────────────────────────────
# {"baselined": bool, "full": {"<user_id>": "<когда заметили заполненным>"},
#  "sent": {"<user_id>": "<когда писали последний раз>"}, "last_run": {...}}


def _atomic_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_state() -> dict[str, Any]:
    try:
        data = json.loads(STATE_PATH.read_text("utf-8"))
    except FileNotFoundError:
        data = {}
    except Exception as exc:  # noqa: BLE001
        # Битый файл — это не «всех можно поздравить заново»: начинаем с baseline,
        # то есть первым проходом никому не пишем.
        logger.warning(f"device_full: состояние нечитаемо ({exc}) — начинаю с baseline")
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {
        "baselined": data.get("baselined") is True,
        "full": dict(data.get("full") or {}),
        "sent": dict(data.get("sent") or {}),
        "last_run": data.get("last_run") or None,
    }


def save_state(state: dict[str, Any]) -> None:
    _atomic_write(STATE_PATH, state)


def reset_baseline() -> None:
    """Следующий проход — baseline: снимок пересоберётся, писать в нём никому не будем.

    «Кому писали» не трогаем: пауза в рассылке не отменяет паузу между сообщениями
    одному человеку.
    """
    state = load_state()
    if state["baselined"]:
        state["baselined"] = False
        save_state(state)


def snapshot_stale(state: dict[str, Any], now: datetime) -> bool:
    """Снимок прошлого прохода слишком старый, чтобы сравнивать с ним. Нет отметки
    времени прохода (руками правленый файл) — тоже устарел: лишний baseline безвреден."""
    last_run = state.get("last_run")
    at = _parse(last_run.get("at")) if isinstance(last_run, dict) else None
    return at is None or now - at > STALE_AFTER


# ── решение ───────────────────────────────────────────────────────────────────


def is_full(devices: int, limit: Optional[int]) -> bool:
    """Все места заняты. Лимит 0 или пустой — безлимит, заполнить нельзя."""
    return bool(limit) and int(limit) > 0 and devices >= int(limit)


def _parse(ts: Any) -> Optional[datetime]:
    if not isinstance(ts, str):
        return None
    try:
        value = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def decide(
    *,
    was_full: bool,
    now_full: bool,
    last_sent: Any,
    now: datetime,
    cooldown_days: int,
) -> str:
    """Писать ли человеку. Возвращает причину: 'send' или почему нет. Чистая функция."""
    if not now_full:
        return "has_free_slot"
    if was_full:
        return "already_full"
    sent = _parse(last_sent)
    if sent is not None and now - sent < timedelta(days=cooldown_days):
        return "cooldown"
    return "send"


# ── сообщение ─────────────────────────────────────────────────────────────────

# Два текста: с докупкой и без. Какой — решает offers_buy(), тот же флаг, что ставит
# кнопку «Докупить»: обещать докупку, когда продажа мест выключена, нельзя.
TEXTS = {
    "ru": {
        "body_buy": (
            "📱 <b>Все места для устройств заняты</b> — {used} из {limit}.\n\n"
            "Следующее устройство подключиться не сможет. Можно докупить место или "
            "освободить: удалить устройство, которым вы больше не пользуетесь."
        ),
        "body_free": (
            "📱 <b>Все места для устройств заняты</b> — {used} из {limit}.\n\n"
            "Следующее устройство подключиться не сможет. Освободите место: удалите "
            "устройство, которым больше не пользуетесь."
        ),
        "buy": "➕ Докупить место",
        "free": "🗑 Освободить место",
        "off": "Не присылать такое",
        "done": "Больше не напишу о занятых местах.",
    },
    "en": {
        "body_buy": (
            "📱 <b>All device slots are taken</b> — {used} of {limit}.\n\n"
            "The next device won't be able to connect. You can buy an extra slot or free "
            "one up by removing a device you no longer use."
        ),
        "body_free": (
            "📱 <b>All device slots are taken</b> — {used} of {limit}.\n\n"
            "The next device won't be able to connect. Free up a slot: remove a device "
            "you no longer use."
        ),
        "buy": "➕ Buy a slot",
        "free": "🗑 Free a slot",
        "off": "Don't send this",
        "done": "No more messages about device slots.",
    },
}


def texts_for(lang: Optional[str]) -> dict[str, str]:
    return TEXTS["en"] if (lang or "ru").lower().startswith("en") else TEXTS["ru"]


def message_text(used: int, limit: int, lang: Optional[str] = None, *, buy: bool = False) -> str:
    """Текст сообщения. `buy` — предлагать ли докупку: передавайте offers_buy(), тот же
    флаг, что ставит кнопку. По умолчанию без докупки — пообещать лишнего хуже."""
    key = "body_buy" if buy else "body_free"
    return texts_for(lang)[key].format(used=int(used), limit=int(limit))


def devices_url(base_url: Optional[str], *, buy: bool) -> str:
    """Страница устройств кабинета: там и докупка места, и удаление. Пусто — без кнопки."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return ""
    return f"{base}/devices?buy=1" if buy else f"{base}/devices"


def offers_buy(base_url: Optional[str], can_buy: bool) -> bool:
    """Предлагаем ли докупку: продажа мест включена и кнопке есть куда вести. Одно
    решение и для кнопки, и для текста — чтобы текст не звал докупать без кнопки."""
    return bool(can_buy) and bool(devices_url(base_url, buy=True))


def keyboard(base_url: Optional[str], *, can_buy: bool, lang: Optional[str] = None) -> Any:
    """Кнопки. Без адреса кабинета остаётся только «Не присылать такое» (пустой url
    Telegram не принимает и отверг бы сообщение целиком). «Докупить» — только когда
    продажа мест включена: иначе кнопка вела бы в никуда."""
    from aiogram.types import InlineKeyboardButton
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    words = texts_for(lang)
    builder = InlineKeyboardBuilder()
    if offers_buy(base_url, can_buy):
        builder.row(InlineKeyboardButton(text=words["buy"], url=devices_url(base_url, buy=True)))
    if devices_url(base_url, buy=False):
        builder.row(InlineKeyboardButton(text=words["free"], url=devices_url(base_url, buy=False)))
    builder.row(InlineKeyboardButton(text=words["off"], callback_data=OPTOUT_CALLBACK))
    return builder.as_markup()


# ── SQL ───────────────────────────────────────────────────────────────────────

# Все, кто вообще может получить сообщение. Кому НЕ писать, решает крон по полям —
# чтобы причина отказа была видна в итоге прохода, а не терялась в WHERE.
CANDIDATES_SQL = """
SELECT u.id                                   AS user_id,
       u.telegram_id                          AS telegram_id,
       lower(u.language::text)                AS lang,
       u.is_blocked                           AS is_blocked,
       u.is_bot_blocked                       AS is_bot_blocked,
       coalesce(u.role::text, 'USER')         AS role,
       s.user_remna_id::text                  AS remna_uuid,
       s.device_limit                         AS device_limit,
       s.is_trial                             AS is_trial,
       s.status::text                         AS status,
       EXISTS (SELECT 1 FROM notification_optouts o
                WHERE o.user_id = u.id AND o.kind = :optout_kind) AS opted_out
  FROM users u
  JOIN subscriptions s ON s.id = u.current_subscription_id
 WHERE s.user_remna_id IS NOT NULL
"""

OPTOUT_SQL = """
INSERT INTO notification_optouts (user_id, kind) VALUES (:user_id, :kind)
ON CONFLICT (user_id, kind) DO NOTHING
"""


def skip_reason(row: Any) -> Optional[str]:
    """Почему человеку не писать вообще (до сравнения со снимком) или None."""
    if (getattr(row, "role", None) or "USER") != "USER":
        return "staff"
    if not getattr(row, "telegram_id", None):
        return "no_telegram"
    if getattr(row, "is_blocked", False) or getattr(row, "is_bot_blocked", False):
        return "blocked"
    if (getattr(row, "status", None) or "") != "ACTIVE":
        return "not_active"
    if getattr(row, "is_trial", False):
        return "trial"
    limit = getattr(row, "device_limit", None)
    if not limit or int(limit) <= 0:
        return "unlimited"
    if getattr(row, "opted_out", False):
        return "opted_out"
    return None
