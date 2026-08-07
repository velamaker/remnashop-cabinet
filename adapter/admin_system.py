"""Системная часть админки поверх «Бедолаги»: настройки бота, журнал действий,
обновления.

Зачем отдельный модуль. Админских ручек в нашем контракте под сорок штук, и
писать их одним файлом нельзя — адаптер собирается несколькими руками сразу.
`admin.py` отвечает за людей и деньги, `admin_catalog.py` — за витрину, здесь
живёт «системная» группа: общие настройки сервиса (`/api/admin/settings`),
журнал админских действий (`/api/admin/audit`) и лента обновлений
(`/api/admin/updates`).

ОТКУДА БЕРЁМ ДАННЫЕ. Только их кабинетное API и только по сессии САМОГО админа
(`ctx.json` подставляет его Bearer): `/cabinet/admin/settings*` под их правом
`settings:read`/`settings:edit`, `/cabinet/admin/rbac/audit-log` под
`audit_log:read`, `/cabinet/admin/updates/releases` под `updates:read`. Решение
«пускать или нет» принимает их RBAC, наружу уходит их же отказ. Служебный токен
адаптера (`ctx.admin`, X-API-Key) здесь не используется НИ РАЗУ и не должен: он
принадлежит адаптеру, а не человеку. Сходи мы за настройками с ним — режим
техработ, бэкапы и проценты рефералки смог бы менять любой вошедший в кабинет,
а журнал действий (кто, когда, с какого IP) читался бы вообще кем угодно.

ЧТО ИЗ ЭТОЙ ГРУППЫ ПЕРЕВОДИТСЯ, А ЧТО НЕТ.

  • Настройки. У них это ПЛОСКИЙ справочник из ~800 ключей (`KEY: value` с типом
    и категорией), у нас — семь осмысленных блоков. Полного соответствия нет и
    быть не может, поэтому переведено ровно то, что ложится один-в-один
    (бэкапы, требование правил, обязательный канал, охрана триала, сброс ссылки,
    рефералка, переключатели уведомлений). Всё остальное отдаётся
    пустым/нейтральным, а на ПОПЫТКУ это поменять сохранение отвечает 501 с
    причиной — молча проглотить правку хуже, чем отказать: админ уверен, что
    сохранил.
    Чего на экране быть НЕ должно, кабинет узнаёт из двух карт в ответе (так же,
    как в разделе «Меню бота»):
      – `supported` — чего у бота нет вовсе; кабинет прячет такой переключатель
        целиком. Показывать поле, которое на сохранении отвечает отказом, —
        ловушка: человек правит его, жмёт «Сохранить» и теряет заодно все
        соседние правки, потому что 501 отменяет сохранение целиком.
      – `locked` — параметр у бота ЕСТЬ, значение настоящее, но правится не
        отсюда (задан в его .env либо живёт отдельным списком, как каналы).
        Кабинет рисует его выключенным и подписывает причиной.
    Поля нет (наш бэкенд их не шлёт) — кабинет показывает всё, как раньше.
  • Двухфакторка админа (`/api/admin/2fa/*`) и ограничение админки по IP
    (`/api/admin/admin-ip`) — механика НАШЕГО бэкенда: секрет TOTP и список
    адресов хранит он же, и он же проверяет их на каждом запросе. У «Бедолаги»
    ни хранилища, ни проверки нет, а адаптеру заводить «настройку», которая
    никого ни от чего не защищает, нельзя: выключатель безопасности, который
    ничего не выключает, опаснее его отсутствия. Путей нет — честный 501.
  • Импорт/экспорт настроек (`/api/admin/settings-io/*`) — это наш комплект
    `assets/*.json` конкретной установки. У чужого бота такого комплекта нет,
    подсовывать вместо него их 750 ключей (среди которых JWT-секрет кабинета,
    токены платёжек и пароли SMTP) — прямая утечка. Не переведено намеренно.
  • Обновления. Обновлять на этой установке надо ДВЕ разные вещи — кабинет и
    бота, — и делается это разными командами в разных каталогах. Поэтому ручка
    отдаёт два блока: `cabinet` (наша лента и наша команда) и `bot` (их лента
    `/cabinet/admin/updates/releases` и их процедура). Подробности и почему
    версия кабинета видна не всегда — в шапке раздела «ОБНОВЛЕНИЯ» ниже.

  • Параметры, заданные в файле окружения бота (.env), их API принимает и кладёт
    в базу, но применяет всё равно значение из окружения — ответ 200, значение
    прежнее. Такие ключи их справочник помечает сам (`env_locked`), поэтому мы
    их а) отдаём кабинету в `locked` (переключатель нарисован выключенным) и
    б) отбиваем ДО записи: писать в их базу значение, которое не работает, —
    мина под будущее (когда строку из .env уберут, настройка «вдруг» изменится).
    Проверка ПОСЛЕ записи всё равно осталась — на случай бота постарше, чей
    справочник про `env_locked` ещё не знает.

ПРО РАЗДЕЛ «НАСТРОЙКИ». Он включится не от этого файла: группа `settings` в
`ADMIN_SECTION_REQUIREMENTS` объявляет два десятка путей (кэшбэк, пополнение,
резерв, заморозка, промо-баннер, win-back, дайджест, алерты, почта, 2FA…), и
раздел честен только целиком. Пока их нет, `/api/admin/settings` просто лежит
готовым. Разделы «Аудит» и «Обновления» — по одному пути, они включаются сразу.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

from compose import Ctx, NotAvailable, UpstreamError, handler

# --- мелочи -----------------------------------------------------------------


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bad_request(message: str) -> UpstreamError:
    """Ошибка В ЗАПРОСЕ КАБИНЕТА (а не «не умеем») — 400 с русским текстом.

    501 (`NotAvailable`) значит «у бота такого нет»; вешать его на «интервал
    бэкапа = 0» неправильно — админ решит, что функция не переведена.
    """
    return UpstreamError(httpx.Response(400, json={"detail": message}))


async def _soft(ctx: Ctx, path: str, **kw: Any) -> Any:
    """Необязательный кусочек экрана: любая беда — просто «данных нет».

    Права в их RBAC нарезаны по разделам: админ с `settings:read`, но без
    `channels:read` получит 403 на список каналов — и вместе с ним пустой экран
    настроек, который ему как раз доступен. Главный запрос каждой ручки остаётся
    строгим (`ctx.json`), он же и решает, пускать ли человека.
    """
    try:
        resp = await ctx.call("GET", path, **kw)
    except httpx.HTTPError:
        return None
    if resp.status_code >= 400:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


# ============================================================================
# НАСТРОЙКИ
# ============================================================================
#
# Их справочник — список записей вида
#   {"key": "BACKUP_MAX_KEEP", "type": "int", "current": 7, "read_only": false,
#    "category": {"key": "BACKUP", ...}, "choices": [...]}
# Читаем его целиком ОДНИМ запросом (у них есть и фильтр по категории, но тогда
# на каждое открытие экрана в ИХ журнал падало бы по восемь записей `settings:read`
# вместо одной — а этот журнал мы сами же и показываем в «Аудите»).
#
# Из ответа берём только перечисленные ниже ключи и булевы переключатели
# уведомлений. Это не лень, а осознанное ограничение: в том же списке лежат
# CABINET_JWT_SECRET, WEB_API_DEFAULT_TOKEN, пароли SMTP и ключи платёжек —
# ничего из этого не должно уехать в браузер даже случайно.

_SETTINGS = "/cabinet/admin/settings"

# Наше поле → их ключ. Всё, что здесь есть, читается И пишется.
_KEY_BACKUP_ENABLED = "BACKUP_AUTO_ENABLED"
_KEY_BACKUP_INTERVAL = "BACKUP_INTERVAL_HOURS"
_KEY_BACKUP_MAX = "BACKUP_MAX_KEEP"
_KEY_BACKUP_TO_CHAT = "BACKUP_SEND_ENABLED"
# У нас «правила обязательны», у них «пропустить принятие правил» — переворот.
_KEY_RULES_SKIP = "SKIP_RULES_ACCEPT"
_KEY_CHANNEL_REQUIRED = "CHANNEL_IS_REQUIRED_SUB"
# Наша «охрана канала для триала» — их «отключать триал при отписке от канала».
_KEY_TRIAL_CHANNEL_GUARD = "CHANNEL_DISABLE_TRIAL_ON_UNSUBSCRIBE"
# Наш «сброс ссылки» — их «отзыв подписки»: в их коде это буквально
# «revoke + regenerate link» (handlers/subscription/revoke.py), то же действие.
_KEY_LINK_RESET = "SUBSCRIPTION_REVOKE_ENABLED"
_KEY_LINK_RESET_COOLDOWN = "SUBSCRIPTION_REVOKE_COOLDOWN_SECONDS"
_KEY_REFERRAL_ENABLED = "REFERRAL_PROGRAM_ENABLED"
_KEY_REFERRAL_PERCENT = "REFERRAL_COMMISSION_PERCENT"
# Сколько платежей реферала приносят комиссию: 0 — все, 1 — только первый.
_KEY_REFERRAL_MAX_PAYMENTS = "REFERRAL_MAX_COMMISSION_PAYMENTS"

# Категории их справочника, из которых собирается наш блок «Уведомления».
# Список не перечисляем ключами: у каждой версии бота их свой набор, а правило
# «категория про уведомления» переживёт обновление бота само.
_NOTIFY_CATEGORIES = ("NOTIFICATIONS", "ADMIN_NOTIFICATIONS", "WEBHOOK_NOTIFICATIONS")

# --- что на этом бэкенде применимо (карты `supported` и `locked`) ------------
#
# Элемент экрана «Настроек» → ключ бота. Карта нужна не только для записи: по
# ней же считается, ЕСТЬ ли такой параметр у подключённого бота (ключа нет в его
# справочнике — значит бот другой версии, и переключатель надо спрятать, а не
# показывать неработающим) и НЕ ЗАДАН ли он в .env (тогда он показан, но выключен).
_CONTROL_KEYS = {
    "rules_required": _KEY_RULES_SKIP,
    "channel_required": _KEY_CHANNEL_REQUIRED,
    "backup_enabled": _KEY_BACKUP_ENABLED,
    "backup_send_to_chat": _KEY_BACKUP_TO_CHAT,
    "backup_interval_hours": _KEY_BACKUP_INTERVAL,
    "backup_max_files": _KEY_BACKUP_MAX,
    "trial_channel_guard": _KEY_TRIAL_CHANNEL_GUARD,
    "link_reset": _KEY_LINK_RESET,
    # Рефералку правит отдельный экран (/admin/referral), но читает и пишет он
    # ЭТУ же ручку — значит и применимость её полей должна ехать отсюда.
    "referral_enable": _KEY_REFERRAL_ENABLED,
    "referral_reward_l1": _KEY_REFERRAL_PERCENT,
    "referral_accrual_strategy": _KEY_REFERRAL_MAX_PAYMENTS,
}

# Чего у «Бедолаги» нет вовсе. Причины расписаны в `_view` и в отказах на
# сохранение; кабинету хватает самого факта — он такие элементы не рисует.
_UNSUPPORTED_CONTROLS = (
    "access_mode",
    "registration_allowed",
    "payments_allowed",
    "rules_link",
    "mini_app_reserve",
    "device_single_reset",
    "device_all_reset",
    "link_reset_cooldown",
    "referral_level",
    "referral_reward_type",
    "referral_reward_strategy",
    "referral_reward_l2",
)

# Короткая причина под выключенным переключателем: длинную админ читать не станет,
# а действие («правьте .env и перезапускайте бота») из неё понятно.
_ENV_LOCKED = "Задано в .env бота — отсюда не изменить: правьте .env и перезапускайте бота"
# Второй вид запрета: их API этот ключ на запись не принимает вовсе. Про .env
# тут говорить нельзя — админ пойдёт искать в нём строку, которой там нет.
_READ_ONLY = "Этот параметр бот отдаёт только на чтение — из кабинета не изменить"


def _defs(rows: Any) -> dict[str, dict[str, Any]]:
    """Их список настроек → ключ: запись."""
    if not isinstance(rows, list):
        return {}
    return {
        row["key"]: row
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("key"), str)
    }


def _flag(defs: dict[str, dict[str, Any]], key: str, default: bool = False) -> bool:
    """Булев параметр бота. Нет ключа (другая версия бота) — значение по
    умолчанию, а не падение всего экрана."""
    row = defs.get(key)
    if not isinstance(row, dict):
        return default
    value = row.get("current")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on", "да"}
    return default


def _num(defs: dict[str, dict[str, Any]], key: str, default: int = 0) -> int:
    row = defs.get(key)
    if not isinstance(row, dict):
        return default
    return _int(row.get("current"), default)


def _lock_reason(defs: dict[str, dict[str, Any]], key: str) -> str:
    """Почему параметр показываем, но не даём править. Пусто — правится.

    Причины ДВЕ, и путать их нельзя: `env_locked` — значение диктует файл
    окружения бота (лечится правкой .env и перезапуском), `read_only` — их API
    вообще не принимает запись этого ключа, и .env тут ни при чём. Раньше обе
    подписывались текстом про .env, и админ шёл искать в нём строку, которой там
    нет. Ключа нет вовсе — не наше дело, это решает `supported`.
    """
    row = defs.get(key)
    if not isinstance(row, dict):
        return ""
    if row.get("env_locked"):
        return _ENV_LOCKED
    return _READ_ONLY if row.get("read_only") else ""


def _env_locked(defs: dict[str, dict[str, Any]], key: str) -> bool:
    """Правится ли параметр отсюда вообще (любая из причин выше)."""
    return bool(_lock_reason(defs, key))


def _applicability(
    defs: dict[str, dict[str, Any]], notifications: dict[str, bool]
) -> tuple[dict[str, bool], dict[str, str]]:
    """Две карты для кабинета: что прятать (`supported`) и что показать
    выключенным (`locked`, значение — причина).

    Разделение важно именно так: «у бота этого нет» и «есть, но правится не
    отсюда» — разные новости для админа. Первое он видеть не должен вовсе,
    второе обязан: значение настоящее и на работу сервиса влияет.
    """
    supported: dict[str, bool] = {key: False for key in _UNSUPPORTED_CONTROLS}
    locked: dict[str, str] = {}
    for control, key in _CONTROL_KEYS.items():
        supported[control] = key in defs
        reason = _lock_reason(defs, key)
        if reason:
            locked[control] = reason
    # Канал читается из их списка каналов, а не из справочника: у бота их может
    # быть несколько, одним нашим полем такой список не правится.
    supported["channel_link"] = True
    locked["channel_link"] = (
        "Обязательных каналов у этого бота может быть несколько — список правится в нём самом"
    )
    supported["notifications"] = bool(notifications)
    for key in notifications:
        reason = _lock_reason(defs, key)
        if reason:
            locked[f"notifications.{key}"] = reason
    return supported, locked


def _notifications(defs: dict[str, dict[str, Any]]) -> dict[str, bool]:
    """Переключатели уведомлений: их булевы ключи из «уведомительных» категорий.

    Ключи отдаём ИХ, без перевода в наши имена. Кабинет нарисует их как
    «Admin notifications purchases enabled» — некрасиво, зато админ видит ровно
    тот параметр, который правит в боте, и сохранение попадает туда же.
    Придумывать соответствие «их ключ ≈ наш ключ» опаснее: пара похожих по
    названию событий значит в двух ботах разное, и админ выключил бы не то.

    Незаписываемые ключи (задан в .env, read-only) НЕ выбрасываем: их значение
    настоящее и на уведомления влияет — админ должен видеть, что включено. Чтобы
    он не принял их за редактируемые, каждый такой ключ едет в `locked`, и
    кабинет рисует его выключенным с причиной.
    """
    result: dict[str, bool] = {}
    for key, row in defs.items():
        category = row.get("category")
        category_key = category.get("key") if isinstance(category, dict) else None
        if category_key not in _NOTIFY_CATEGORIES:
            continue
        if row.get("type") != "bool":
            continue
        result[key] = _flag(defs, key)
    return result


async def _channels(ctx: Ctx) -> list[dict[str, Any]]:
    """Каналы обязательной подписки. У них это СПИСОК (несколько каналов),
    у нас — одно поле «Ссылка на канал», поэтому только читаем."""
    data = await _soft(ctx, "/cabinet/admin/channel-subscriptions")
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    return [c for c in items if isinstance(c, dict) and c.get("is_active", True)]


def _channel_view(channels: list[dict[str, Any]]) -> tuple[str, int | None]:
    """Список каналов → наши `channel_link` и `channel_id`.

    Один канал — показываем его как есть. Несколько — перечисляем ссылки через
    запятую: поле в кабинете одно, и молча показать первый из трёх значит
    соврать про два остальных. `channel_id` при этом пустой: числовой id одного
    из нескольких каналов ничего не значит.
    """
    links = [str(c.get("channel_link") or "").strip() for c in channels]
    links = [link for link in links if link]
    if len(channels) == 1:
        raw = str(channels[0].get("channel_id") or "").strip()
        # У них id канала — строка (может быть и `@username`), у нас число.
        channel_id = int(raw) if re.fullmatch(r"-?\d+", raw) else None
        return (links[0] if links else ""), channel_id
    return ", ".join(links), None


def _view(defs: dict[str, dict[str, Any]], channels: list[dict[str, Any]]) -> dict[str, Any]:
    """Их справочник → наша форма настроек (`AdminSettings` из admin.ts).

    Поля, которых у бота нет, заполнены НЕ выдуманными значениями, а тем, что
    про их бота правда:
      • `access.mode` — пусто: режимов доступа (PUBLIC/INVITED/RESTRICTED) у них
        нет вовсе. Ключ MAINTENANCE_MODE в их справочнике есть, но техработы
        включаются не им, а кнопкой в их админ-панели в Telegram (состояние
        живёт в кэше, `maintenance_service`), и запись в этот ключ не закрыла бы
        сервис — только сменила бы подпись в их же меню. Настройка, которая
        ничего не делает, — худший вид вранья, поэтому не мапим.
      • `registration_allowed`/`payments_allowed` — true: закрыть регистрацию
        или оплату глобально у этого бота НЕЧЕМ (такого выключателя нет), то
        есть они и правда всегда открыты. Оплата отдельными способами
        включается в разделе «Платёжные шлюзы».
      • `rules_link` — пусто: правила у них не ссылка, а страница с текстом
        (`/pages/service-rules`), нашего URL для них не существует.
      • `mini_app_reserve` — false: резервного mini app у них нет.
      • `device_single_reset`/`device_all_reset` — включены и без задержки:
        сброс устройств у них есть всегда (`DELETE /cabinet/subscription/devices`
        и `.../{hwid}`) и не настраивается ни выключателем, ни кулдауном.
    Все они перечислены в `supported: false` — кабинет их не рисует. Раньше
    рисовал: три тумблера «Сброс одного устройства / всех устройств / ссылки»
    экран показывал, но в сохранение не клал вообще, и они возвращались в
    прежнее положение при перезагрузке страницы — «сохранил» в никуда.
    """
    channel_link, channel_id = _channel_view(channels)
    notifications = _notifications(defs)
    supported, locked = _applicability(defs, notifications)
    # Их деньги — целые копейки во всей базе, отдельной настройки валюты нет:
    # весь адаптер (и цены, и платежи) считает их рублями.
    return {
        "default_currency": "RUB",
        "access": {
            "mode": "",
            "registration_allowed": True,
            "payments_allowed": True,
        },
        "requirements": {
            "rules_required": not _flag(defs, _KEY_RULES_SKIP),
            "channel_required": _flag(defs, _KEY_CHANNEL_REQUIRED),
            "rules_link": "",
            "channel_link": channel_link,
            "channel_id": channel_id,
        },
        "referral": {
            "enable": _flag(defs, _KEY_REFERRAL_ENABLED),
            # Уровень у их программы один: наград «за реферала реферала» нет.
            "level": 1,
            "accrual_strategy": _accrual(defs),
            "reward": {
                # Ни баллов, ни дней: у них награда — деньги на реферальный
                # баланс. Пусто честнее, чем «POINTS» (кабинет подписал бы
                # «1 балл = 7 ₽» под рублёвой комиссией).
                "type": "",
                "strategy": "PERCENT",
                "config": {"1": _num(defs, _KEY_REFERRAL_PERCENT), "2": 0},
            },
        },
        "backup": {
            "enabled": _flag(defs, _KEY_BACKUP_ENABLED),
            "interval_hours": _num(defs, _KEY_BACKUP_INTERVAL),
            "max_files": _num(defs, _KEY_BACKUP_MAX),
            "send_to_chat": _flag(defs, _KEY_BACKUP_TO_CHAT),
        },
        "extra": {
            "device_single_reset": {"enabled": True, "cooldown_hours": 0},
            "device_all_reset": {"enabled": True, "cooldown_hours": 0},
            "link_reset": {
                "enabled": _flag(defs, _KEY_LINK_RESET),
                # У них пауза между перевыпусками в СЕКУНДАХ, у нас — в целых
                # часах, и их обычные 15 минут в наших часах не выражаются: поле
                # покажет 0. Кабинет его не рисует (в форме только выключатель),
                # а округлять вверх нельзя — «1 час» был бы вчетверо длиннее
                # правды.
                "cooldown_hours": _num(defs, _KEY_LINK_RESET_COOLDOWN) // 3600,
            },
            "trial_channel_guard": _flag(defs, _KEY_TRIAL_CHANNEL_GUARD),
            "mini_app_reserve": False,
        },
        "notifications": notifications,
        # Что на этом бэкенде применимо и что показать выключенным. Наш бэкенд
        # этих полей не шлёт — кабинет тогда рисует экран по-старому.
        "supported": supported,
        "locked": locked,
    }


def _accrual(defs: dict[str, dict[str, Any]]) -> str:
    """«Когда начислять» ← их лимит платежей с комиссией.

    0 — комиссия с каждого платежа реферала, 1 — только с первого: это ровно
    наши ON_EACH_PAYMENT и ON_FIRST_PAYMENT. Другие значения («первые три
    платежа») в наши два варианта не ложатся — отдаём пусто, чтобы кабинет
    показал незаполненный выбор, а не соседний вариант.
    """
    limit = _num(defs, _KEY_REFERRAL_MAX_PAYMENTS)
    if limit == 0:
        return "ON_EACH_PAYMENT"
    if limit == 1:
        return "ON_FIRST_PAYMENT"
    return ""


async def _load(ctx: Ctx) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    rows = await ctx.json(_SETTINGS, [])
    return _defs(rows), await _channels(ctx)


@handler("GET", "/api/admin/settings")
async def settings_get(ctx: Ctx) -> dict[str, Any]:
    defs, channels = await _load(ctx)
    return _view(defs, channels)


# --- сохранение --------------------------------------------------------------
#
# Кабинет присылает ВЕСЬ экран целиком, а не изменённые поля, и их API умеет
# писать по одному ключу. Поэтому сначала сравниваем присланное с текущим и
# собираем список реальных изменений:
#   • не трогаем то, что админ не менял (иначе каждое «Сохранить» переписывало
#     бы полтора десятка ключей бота и засоряло их журнал);
#   • отказываем 501 только на то, что админ ПОПЫТАЛСЯ изменить — иначе экран
#     нельзя было бы сохранить вообще, ведь в теле всегда лежат и поля без
#     соответствия.
# Проверяем ВСЁ до первой записи: их API атомарной записи пачкой не умеет, и
# упасть на пятом ключе из семи — значит оставить настройки полуприменёнными.


def _changed(payload: dict[str, Any], field: str, current: Any) -> Any:
    """Присланное значение, если оно есть И отличается от текущего; иначе None."""
    if field not in payload:
        return None
    value = payload[field]
    return None if value == current else value


def _applied(defs: dict[str, dict[str, Any]], key: str, value: Any) -> bool:
    """Перечитанное значение параметра совпало с тем, что мы записали."""
    if isinstance(value, bool):
        return _flag(defs, key) is value
    return _num(defs, key, -1) == value


def _positive(value: Any, what: str) -> int:
    number = _int(value, -1)
    if number < 1:
        raise _bad_request(f"{what}: нужно целое число не меньше 1")
    return number


def _plan_backup(payload: dict[str, Any], view: dict[str, Any], out: dict[str, Any]) -> None:
    backup = payload.get("backup")
    if not isinstance(backup, dict):
        return
    now = view["backup"]
    if (value := _changed(backup, "enabled", now["enabled"])) is not None:
        out[_KEY_BACKUP_ENABLED] = bool(value)
    if (value := _changed(backup, "send_to_chat", now["send_to_chat"])) is not None:
        out[_KEY_BACKUP_TO_CHAT] = bool(value)
    if (value := _changed(backup, "interval_hours", now["interval_hours"])) is not None:
        out[_KEY_BACKUP_INTERVAL] = _positive(value, "Интервал бэкапа, часов")
    if (value := _changed(backup, "max_files", now["max_files"])) is not None:
        out[_KEY_BACKUP_MAX] = _positive(value, "Сколько копий хранить")


def _plan_access(payload: dict[str, Any], view: dict[str, Any]) -> None:
    """Доступ: менять нечего, поэтому только проверяем, что не пытаются."""
    access = payload.get("access")
    access = access if isinstance(access, dict) else {}
    if _changed(access, "mode", view["access"]["mode"]) is not None:
        raise NotAvailable(
            "Режимы доступа недоступны: у этого бота их нет вовсе, а техработы "
            "включаются кнопкой в его админ-панели в Telegram, а не настройкой"
        )
    # Флаги кабинет шлёт и внутри `access`, и рядом с ним — проверяем оба места.
    for source in (access, payload):
        if _changed(source, "registration_allowed", True) is not None:
            raise NotAvailable(
                "Закрыть регистрацию недоступно: глобального выключателя "
                "регистрации у этого бота нет"
            )
        if _changed(source, "payments_allowed", True) is not None:
            raise NotAvailable(
                "Запретить оплату недоступно: общего выключателя оплат у этого "
                "бота нет — способы оплаты включаются по одному в «Платёжных шлюзах»"
            )


def _plan_requirements(payload: dict[str, Any], view: dict[str, Any], out: dict[str, Any]) -> None:
    now = view["requirements"]
    if (value := _changed(payload, "rules_required", now["rules_required"])) is not None:
        # У них ключ обратный по смыслу: «пропустить принятие правил».
        out[_KEY_RULES_SKIP] = not bool(value)
    if (value := _changed(payload, "channel_required", now["channel_required"])) is not None:
        out[_KEY_CHANNEL_REQUIRED] = bool(value)
    if _changed(payload, "rules_link", now["rules_link"]) is not None:
        raise NotAvailable(
            "Ссылка на правила недоступна: правила у этого бота — страница с "
            "текстом, а не ссылка"
        )
    if _changed(payload, "channel_link", now["channel_link"]) is not None:
        raise NotAvailable(
            "Ссылка на канал правится не здесь: у этого бота обязательных "
            "каналов может быть несколько, и живут они отдельным списком"
        )


def _block(payload: dict[str, Any], field: str) -> dict[str, Any]:
    """Вложенный блок «Дополнительно» — из корня тела или из `extra`.

    Читаем оба места нарочно: наш контракт держит эти поля в `extra` (так их
    отдаёт GET), а сохранение исторически шлёт плоско, рядом с
    `trial_channel_guard`. Разбираться, кто прав, дороже, чем принять оба вида.
    """
    for source in (payload, payload.get("extra")):
        if isinstance(source, dict) and isinstance(source.get(field), dict):
            return source[field]
    return {}


def _plan_extra(payload: dict[str, Any], view: dict[str, Any], out: dict[str, Any]) -> None:
    extra = view["extra"]
    guard = extra["trial_channel_guard"]
    if (value := _changed(payload, "trial_channel_guard", guard)) is not None:
        out[_KEY_TRIAL_CHANNEL_GUARD] = bool(value)
    if _changed(payload, "mini_app_reserve", False) is not None:
        raise NotAvailable("Резервного mini app у этого бота нет")

    # Сброс ссылки на подписку у них есть и настраивается — переводим на их ключ.
    # Раньше экран этот тумблер показывал, но в сохранение не клал: человек его
    # переключал, видел «Сохранено!» и получал прежнее значение при перезагрузке.
    link = _block(payload, "link_reset")
    if (value := _changed(link, "enabled", extra["link_reset"]["enabled"])) is not None:
        out[_KEY_LINK_RESET] = bool(value)
    if _changed(link, "cooldown_hours", extra["link_reset"]["cooldown_hours"]) is not None:
        raise NotAvailable(
            "Паузу между сбросами ссылки отсюда не задать: у этого бота она считается "
            "в секундах (обычно 900), а наше поле — целые часы, и его 15 минут в них "
            "не выражаются"
        )

    # Сброс устройств у них не настраивается вовсе — он есть всегда.
    for field, what in (
        ("device_single_reset", "одного устройства"),
        ("device_all_reset", "всех устройств"),
    ):
        block = _block(payload, field)
        now = extra[field]
        if _changed(block, "enabled", now["enabled"]) is not None or (
            _changed(block, "cooldown_hours", now["cooldown_hours"]) is not None
        ):
            raise NotAvailable(
                f"Сброс {what} у этого бота не настраивается: он доступен всегда и "
                "ни выключателя, ни задержки у него нет"
            )


def _plan_referral(payload: dict[str, Any], view: dict[str, Any], out: dict[str, Any]) -> None:
    """Рефералка. Форма сохранения у неё своя (`reward_l1`, `reward_type`…) —
    см. AdminReferralPage.tsx и наш `SettingsUpdateRequest`."""
    referral = payload.get("referral")
    if not isinstance(referral, dict):
        return
    now = view["referral"]
    if (value := _changed(referral, "enable", now["enable"])) is not None:
        out[_KEY_REFERRAL_ENABLED] = bool(value)
    if _changed(referral, "level", now["level"]) is not None:
        raise NotAvailable(
            "Второй уровень рефералки недоступен: этот бот платит только за "
            "прямых приглашённых"
        )
    if _changed(referral, "reward_type", now["reward"]["type"]) is not None:
        raise NotAvailable(
            "Тип награды сменить нельзя: этот бот начисляет рефереру деньги на "
            "реферальный баланс, ни баллов, ни дней подписки у него нет"
        )
    if _changed(referral, "reward_strategy", now["reward"]["strategy"]) is not None:
        raise NotAvailable(
            "Фиксированная награда недоступна: этот бот считает её процентом от "
            "платежа приглашённого"
        )
    if _changed(referral, "reward_l2", 0) is not None:
        raise NotAvailable("Награда за второй уровень недоступна: у этого бота один уровень")
    if (value := _changed(referral, "reward_l1", now["reward"]["config"]["1"])) is not None:
        percent = _int(value, -1)
        if not 0 <= percent <= 100:
            raise _bad_request("Награда реферера: процент от 0 до 100")
        out[_KEY_REFERRAL_PERCENT] = percent
    accrual = _changed(referral, "accrual_strategy", now["accrual_strategy"])
    if accrual is not None:
        # Обратная сторона `_accrual`: «за каждый платёж» — без лимита,
        # «за первый» — лимит в один платёж.
        limits = {"ON_EACH_PAYMENT": 0, "ON_FIRST_PAYMENT": 1}
        if accrual not in limits:
            raise NotAvailable(f"Начисление «{accrual}» этому боту неизвестно")
        out[_KEY_REFERRAL_MAX_PAYMENTS] = limits[accrual]


def _plan_notifications(payload: dict[str, Any], view: dict[str, Any], out: dict[str, Any]) -> None:
    notifications = payload.get("notifications")
    if not isinstance(notifications, dict):
        return
    known = view["notifications"]
    for key, value in notifications.items():
        if key not in known:
            # Ключи кабинет берёт из нашего же ответа; чужой — либо опечатка,
            # либо настройка не из «уведомительных» категорий (а то и вовсе
            # чей-то секрет), и писать в неё вслепую нельзя.
            raise NotAvailable(f"Уведомление «{key}» этому боту неизвестно")
        if bool(value) != known[key]:
            out[key] = bool(value)


@handler("PUT", "/api/admin/settings")
async def settings_save(ctx: Ctx) -> dict[str, Any]:
    payload = ctx.payload()
    defs, channels = await _load(ctx)
    view = _view(defs, channels)

    changes: dict[str, Any] = {}
    _plan_access(payload, view)
    _plan_requirements(payload, view, changes)
    _plan_backup(payload, view, changes)
    _plan_extra(payload, view, changes)
    _plan_referral(payload, view, changes)
    _plan_notifications(payload, view, changes)

    for key in changes:
        if key not in defs:
            # Другая версия бота: ключа нет — писать наугад нельзя, их API
            # ответит 404, но сказать причину лучше самим.
            raise NotAvailable(f"Параметр «{key}» этот бот не знает")

    # Запертые параметры отбиваем ДО записи — своими словами и целиком. Их v4.0.0
    # такую правку и сама не примет (409 по-английски, про один ключ), но ответить
    # раньше стоит по двум причинам: сообщение получается на русском и сразу про
    # ВСЕ запертые ключи разом, а сохранение экрана не успевает применить половину
    # правок и упасть на второй.
    locked = {key: _lock_reason(defs, key) for key in changes if _env_locked(defs, key)}
    if locked:
        env_keys = sorted(k for k, reason in locked.items() if reason == _ENV_LOCKED)
        ro_keys = sorted(k for k, reason in locked.items() if reason != _ENV_LOCKED)
        parts = []
        if env_keys:
            parts.append(
                "заданы в файле окружения бота (.env), правьте .env и перезапускайте бота: "
                + ", ".join(env_keys)
            )
        if ro_keys:
            parts.append(
                "бот отдаёт только на чтение, из кабинета не меняются: " + ", ".join(ro_keys)
            )
        raise NotAvailable("Не сохранено — " + "; ".join(parts) + ". Ничего не записано.")

    if not changes:
        return view

    for key, value in changes.items():
        # Их ошибку (403 read-only, 400 «invalid value») отдаём кабинету как
        # есть — она про этот конкретный параметр и человеку понятна.
        await ctx.json(f"{_SETTINGS}/{key}", method="PUT", json={"value": value})

    defs, channels = await _load(ctx)
    # ПРОВЕРЯЕМ, ЧТО ПРАВКА ПРИМЕНИЛАСЬ. Их сохранение бывает молча
    # бесполезным: параметры, заданные в файле окружения бота (.env), их API
    # принимает и кладёт в базу, но работает всё равно значение из окружения
    # (`system_settings_service._is_env_override`) — ответ 200, значение
    # прежнее. Без проверки кабинет показал бы «Сохранено!» там, где не
    # изменилось ничего, и админ ушёл бы с уверенностью, что настроил.
    stuck = [key for key, value in changes.items() if not _applied(defs, key, value)]
    if stuck:
        saved = len(changes) - len(stuck)
        raise NotAvailable(
            "Не применилось: " + ", ".join(stuck) + ". Так бывает с параметрами, "
            "заданными в файле окружения бота (.env): правку их API принимает, а "
            "работает всё равно значение из окружения — правьте .env и "
            "перезапускайте бота."
            + (f" Остальные изменения ({saved}) сохранены." if saved else "")
        )
    return _view(defs, channels)


# ============================================================================
# ЖУРНАЛ ДЕЙСТВИЙ
# ============================================================================
#
# У них журнал шире нашего: наш пишет только изменяющие админ-запросы, их —
# ЛЮБОЙ запрос к админ-API, включая чтения (`settings:read`). Показываем как
# есть: лишние строки видны и подписаны своим методом, а вот выбросить их
# «чтобы было похоже на наш» нельзя — это уже редактирование журнала.

_AUDIT = "/cabinet/admin/rbac/audit-log"

# Их исход проверки прав → наш HTTP-код. Кодов ответа их журнал не хранит вовсе,
# но `denied` — это буквально отказ RBAC, то есть наш 403, а `success` — «пустили
# дальше». Больше кодов не выдумываем.
_AUDIT_STATUS = {"success": 200, "denied": 403}


def _actor(row: dict[str, Any]) -> str:
    """Кто действовал. У них почта есть не у всех (админ из Telegram живёт без
    неё) — тогда имя, а в крайнем случае id: строка без автора бесполезна."""
    email = (row.get("user_email") or "").strip()
    if email:
        return email
    name = (row.get("user_first_name") or "").strip()
    user_id = row.get("user_id")
    if name:
        return f"{name} (id {user_id})" if user_id is not None else name
    return f"id {user_id}" if user_id is not None else "—"


async def _actor_id(ctx: Ctx, actor: str) -> int | None:
    """Фильтр «кто» → id пользователя: их журнал фильтрует только по id.

    Ищем по их же списку людей. Ровно один — фильтруем по нему; несколько —
    честно отказываем (их API принимает один id, а показать выборку по первому
    подошедшему значит подсунуть чужие действия); ни одного — None, и ручка
    вернёт пустой список, что здесь правда: такого автора нет.
    """
    if actor.isdigit():
        return int(actor)
    needle = actor.lstrip("@")
    # Их поиск разрезан надвое: `search` — имя/username/telegram_id, почта живёт
    # в отдельном параметре `email`, и одного запроса «имя ИЛИ почта» у них нет
    # (см. admin.py). Адрес со «собакой» сразу ищем как почту, остальное —
    # сначала по имени, а если пусто, повторяем как почту: в журнале автор
    # подписан именно адресом, и его кусок («example.org») админ вбивает чаще
    # всего.
    #
    # Ходим мягко (`_soft`): права в их RBAC нарезаны по разделам, и админ с
    # `audit_log:read`, но без `users:read` получил бы на журнале чужой 403 —
    # непонятный на этом экране. Не смогли посмотреть — говорим, что делать.
    params: dict[str, Any] = {"limit": 5, "offset": 0}
    params["email" if "@" in needle else "search"] = needle
    data = await _soft(ctx, "/cabinet/admin/users", params=params)
    if isinstance(data, dict) and "@" not in needle and not (data.get("users") or []):
        data = await _soft(
            ctx, "/cabinet/admin/users", params={"limit": 5, "offset": 0, "email": needle}
        )
    if not isinstance(data, dict):
        raise NotAvailable(
            "Поиск автора по имени и почте недоступен (нет доступа к списку "
            "пользователей) — укажите в фильтре id пользователя"
        )
    users = [u for u in (data.get("users") or []) if isinstance(u, dict)]
    if not users:
        return None
    if len(users) > 1 or _int(data.get("total")) > 1:
        raise NotAvailable(
            f"Под «{actor}» подходит несколько пользователей — их журнал ищет по "
            "одному автору: укажите почту целиком или id"
        )
    return _int(users[0].get("id"), 0) or None


@handler("GET", "/api/admin/audit")
async def audit(ctx: Ctx) -> dict[str, Any]:
    limit = max(1, min(500, _int(ctx.query.get("limit"), 100)))
    offset = max(0, _int(ctx.query.get("offset"), 0))

    # Фильтров по методу и пути их журнал не умеет: он индексирует «действие»
    # (`users:block`, `settings:edit`) и «тип объекта», а метод и путь лежат в
    # строке записи. Отфильтровать их у себя по одной странице нельзя —
    # получилось бы «за последние 200 записей ни одного DELETE» вместо «в
    # журнале ни одного DELETE», а это разные утверждения.
    if ctx.query.get("method"):
        raise NotAvailable(
            "Фильтр по методу недоступен: журнал этого бота фильтруется по "
            "действию, а не по HTTP-методу"
        )
    if ctx.query.get("path"):
        raise NotAvailable(
            "Фильтр по пути недоступен: журнал этого бота фильтруется по "
            "действию и типу объекта, а не по адресу запроса"
        )

    params: dict[str, Any] = {"limit": limit, "offset": offset}
    actor = (ctx.query.get("actor") or "").strip()
    if actor:
        user_id = await _actor_id(ctx, actor)
        if user_id is None:
            return {"items": []}
        params["user_id"] = user_id
    # Даты у нас приходят днём (YYYY-MM-DD), у них параметр — момент времени;
    # «по дату» включительно, поэтому конец дня, а не его начало.
    date_from = (ctx.query.get("date_from") or "").strip()
    date_to = (ctx.query.get("date_to") or "").strip()
    if date_from:
        params["date_from"] = f"{date_from}T00:00:00" if len(date_from) == 10 else date_from
    if date_to:
        params["date_to"] = f"{date_to}T23:59:59.999999" if len(date_to) == 10 else date_to

    data = await ctx.json(_AUDIT, {}, params=params) or {}
    items = []
    for row in data.get("items") or []:
        if not isinstance(row, dict):
            continue
        items.append(
            {
                "id": row.get("id"),
                "actor": _actor(row),
                "method": row.get("request_method") or "",
                # Путь у них свой (`/cabinet/admin/...`) — так и показываем:
                # переписать его в наши адреса значит соврать, куда ходили.
                # Записи без пути (не из HTTP) подписываем их же действием.
                "path": row.get("request_path") or row.get("action") or "",
                "status": _AUDIT_STATUS.get(str(row.get("status") or "").lower(), 0),
                "created_at": row.get("created_at"),
            }
        )
    return {"items": items}


# ============================================================================
# ОБНОВЛЕНИЯ
# ============================================================================
#
# На этой установке обновлять надо ДВЕ РАЗНЫЕ ВЕЩИ, и делается это разными
# командами, в разных каталогах, а в режиме site — и на разных серверах:
#   • КАБИНЕТ — наш код (и этот адаптер вместе с ним): своя лента релизов
#     (CHANGELOG.md кабинета) и своя команда обновления;
#   • БОТ «Бедолага» — чужой продукт: своя лента и своя процедура (отдельного
#     update.sh у него нет — пересборка его же compose'ом).
# Поэтому ручка отдаёт ДВА блока, `cabinet` и `bot`. Раньше она отдавала только
# релизы бота, а экран подписывал их как релизы кабинета и предлагал НАШУ
# команду обновления — то есть врал и про то, чьи это релизы, и про то, что
# именно обновится.
#
# ПОЛЯ В КОРНЕ (`current/latest/update_available/repo/items`) — прежние, для
# кабинета, который про блоки ещё не знает (страница из кэша браузера). В них,
# как и раньше, лежит БОТ: менять смысл поля на ходу нельзя — старый экран
# нарисовал бы чужие цифры под своими подписями.
#
# Из их ответа берём ТОЛЬКО `bot`:
#   • это и есть бэкенд, который владельцу над «Бедолагой» надо обновлять;
#   • их `cabinet` — их собственный веб-кабинет, к нашему отношения не имеет, и
#     блок там к тому же липовый (`current_version` = последний релиз с гитхаба,
#     `has_updates` всегда false), то есть «обновление доступно» он не покажет
#     никогда.
#
# ОТКУДА БЕРЁТСЯ ВЕРСИЯ КАБИНЕТА. Из файла VERSION — если каталог кабинета
# адаптеру виден. По умолчанию он НЕ виден: образ адаптера собирается из каталога
# `adapter/`, а VERSION и CHANGELOG.md лежат выше, и томом каталог кабинета внутрь
# не пробрасывается. Тогда блок так и говорит — «версия отсюда не видна» — и НЕ
# показывает «доступно обновление»: сравнивать не с чем, а выдуманная версия хуже
# отсутствующей. Чтобы версия появилась, установке хватит одного из двух:
#   • переменная окружения `CABINET_VERSION=1.0.8` у адаптера;
#   • каталог кабинета томом только на чтение (`-v /opt/remnashop:/cabinet:ro`
#     или свой путь в `CABINET_ROOT`) — тогда читаются и VERSION, и CHANGELOG.md,
#     то есть лента кабинета работает даже без интернета, как на нашем бэкенде.
# Лента при этом показывается всегда: её основа — CHANGELOG.md из репозитория
# кабинета (best-effort, кэш на 15 минут). GitHub не ответил — говорим и это.

_RELEASES = "/cabinet/admin/updates/releases"

_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_BOLD = re.compile(r"\*\*([^*]+)\*\*")
# Хвост вида «(3b0306f)» — ссылка на коммит, в ленте обновлений она лишняя.
_COMMIT_TAIL = re.compile(r"\s*\(\s*[0-9a-f]{7,40}\s*\)\s*$")
# Верхний заголовок релиза: «## [3.67.0](…) (2026-07-29)» — версия и дата и так
# нарисованы карточкой.
_RELEASE_HEAD = re.compile(r"^#{1,2}\s+\[?v?\d+(\.\d+)*")

_NOTES_LIMIT = 4000


def _notes(body: str, url: str) -> str:
    """Их markdown-описание релиза → простой текст, как в нашей ленте.

    Кабинет рисует `notes` как есть, без markdown, поэтому ссылки и звёздочки
    надо развернуть: иначе вместо строки читается «* **cabinet:** текст ([abc](…))».
    Ничего не переписываем по смыслу — только снимаем разметку.
    """
    lines: list[str] = []
    for raw in (body or "").splitlines():
        line = raw.strip()
        if not line or _RELEASE_HEAD.match(line):
            continue
        bullet = line[:2] in ("* ", "- ") or line[:2] == "• "
        if bullet:
            line = line[2:].strip()
        line = _MD_LINK.sub(r"\1", line)
        line = _MD_BOLD.sub(r"\1", line)
        if line.startswith("#"):
            # Раздел релиза («### Bug Fixes») — оставляем как подзаголовок.
            heading = line.lstrip("#").strip()
            if heading:
                lines.append(f"{heading}:")
            continue
        line = _COMMIT_TAIL.sub("", line).strip()
        if line:
            lines.append(f"• {line}" if bullet else line)
    text = "\n".join(lines)
    if len(text) > _NOTES_LIMIT:
        # Обрезаем по строке, а не по символу: половина слова выглядит как сбой.
        cut = text[:_NOTES_LIMIT].rsplit("\n", 1)[0]
        text = f"{cut}\n… полный список изменений: {url}" if url else cut
    return text


def _version(value: Any) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", str(value or ""))
    return tuple(int(n) for n in numbers) or (0,)


# --- блок «Бот» --------------------------------------------------------------

_BOT_TITLE = "Бот «Бедолага»"
_CABINET_TITLE = "Кабинет"


def _bot_block(data: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Их ответ → блок «Бот» и та же лента для полей в корне (старый экран).

    Список один, а подпись карточек нужна разная: в блоке заголовок и так
    говорит, чьи это релизы, а старому экрану («История релизов кабинета»)
    подпись обязательна — без неё релизы бота читаются как наши.
    """
    bot = data.get("bot") if isinstance(data, dict) and isinstance(data.get("bot"), dict) else {}
    current = str(bot.get("current_version") or "")
    repo_url = str(bot.get("repo_url") or "").rstrip("/")
    installed = _version(current)

    items: list[dict[str, Any]] = []
    for release in bot.get("releases") or []:
        if not isinstance(release, dict):
            continue
        tag = str(release.get("tag_name") or "").strip()
        if not tag:
            continue
        # Ссылки на сам релиз их ручка не отдаёт, но адрес релиза на гитхабе
        # однозначно собирается из репозитория и тега.
        url = f"{repo_url}/releases/tag/{tag}" if repo_url else ""
        name = str(release.get("name") or "").strip()
        items.append(
            {
                "version": tag,
                "name": name if name and name != tag else "",
                "date": release.get("published_at") or None,
                "notes": _notes(str(release.get("body") or ""), url),
                "url": url,
                "installed": _version(tag) <= installed,
                # Черновиков и бет в ленту не тащим как «доступное обновление»,
                # но и прятать не станем — пометка ниже считается по этому полю.
                "_prerelease": bool(release.get("prerelease")),
            }
        )
    items.sort(key=lambda item: _version(item["version"]), reverse=True)

    # «Последняя» — старшая СТАБИЛЬНАЯ версия: звать владельца обновиться на
    # бету нельзя. Их собственный флаг `has_updates` не берём: он посчитан по
    # другому списку, и разойтись с тем, что нарисовано на экране, ему нечего
    # стоит — «доступно обновление» без единой новой карточки хуже, чем без
    # надписи вовсе.
    stable = [item["version"] for item in items if not item["_prerelease"]]
    for item in items:
        del item["_prerelease"]
    latest = stable[0] if stable else None

    legacy = [
        dict(item, name=f"{_BOT_TITLE} · {item['name']}" if item["name"] else _BOT_TITLE)
        for item in items
    ]
    block = {
        "title": _BOT_TITLE,
        "current": current or None,
        "latest": latest,
        "update_available": bool(latest and _version(latest) > installed),
        # Наш контракт ждёт «владелец/репозиторий», а не полный адрес.
        "repo": repo_url.replace("https://github.com/", ""),
        "note": (
            None
            if items
            else "Лента релизов бота сейчас пуста: его API не отдал ни одного релиза "
            "(он тянет их с GitHub и держит свой кэш на час)."
        ),
        "commands": [
            {
                "cmd": "cd <каталог бота> && docker compose up -d --build",
                "when": (
                    "На сервере БОТА, в его каталоге — там, где лежит его docker-compose.yml. "
                    "Перед этим забрать свежий код там же (git pull): образ бота собирается "
                    "из его исходников. Отдельного update.sh, как у кабинета, у этого бота нет, "
                    "поэтому команда кабинета для него не подходит."
                ),
            }
        ],
        "items": items,
    }
    return block, legacy


# --- блок «Кабинет» ----------------------------------------------------------

# Тот же репозиторий и та же переменная, что у нашего бэкенда (UPDATE_REPO):
# лента кабинета не зависит от того, какой бот его обслуживает.
_CABINET_REPO = (os.environ.get("UPDATE_REPO") or "velamaker/remnashop-cabinet").strip().strip("/")

# Где искать каталог кабинета, если его пробросили адаптеру (см. шапку раздела).
# Пусто в обоих местах — значит не пробросили, это норма.
_CABINET_ROOTS = tuple(
    path for path in (os.environ.get("CABINET_ROOT"), "/cabinet", "/opt/remnashop") if path
)

# Заголовок версии в нашем CHANGELOG.md: «## v1.0.8».
_CHANGELOG_HEAD = re.compile(r"^##\s+v?(\d+(?:\.\d+)+)\s*$")

# Лента меняется не чаще релизов, поэтому 15 минут; неудачу кэшируем на минуту,
# чтобы починившаяся сеть подхватилась сама, но и не долбить GitHub на каждый
# заход в раздел.
_FEED_TTL = 900.0
_FEED_FAIL_TTL = 60.0
_feed_cache: dict[str, Any] = {"at": 0.0, "ttl": 0.0, "value": None}


def _read_text(path: Path) -> str:
    """Файл кабинета, если он вообще виден адаптеру. Не виден — пустая строка:
    непроброшенный каталог это штатная установка, а не поломка."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _cabinet_local() -> tuple[str, str]:
    """(версия, текст CHANGELOG.md) из каталога кабинета, если он проброшен."""
    for root in _CABINET_ROOTS:
        version = _read_text(Path(root) / "VERSION").strip()
        if version:
            return version.lstrip("vV"), _read_text(Path(root) / "CHANGELOG.md")
    return "", ""


def _changelog(text: str) -> dict[str, str]:
    """Наш CHANGELOG.md → {«1.0.8»: «• пункт\\n• пункт»}.

    Формат курируемый (`## vX.Y` плюс пункты списком), и это уже человеческий
    текст — ровно то, что владелец должен прочитать перед обновлением. Ничего не
    переписываем: берём пункты как есть.
    """
    result: dict[str, str] = {}
    version = ""
    lines: list[str] = []
    for raw in text.splitlines():
        head = _CHANGELOG_HEAD.match(raw.strip())
        if head:
            if version and lines:
                result[version] = "\n".join(lines)
            version, lines = head.group(1), []
            continue
        if not version:
            continue
        line = raw.strip()
        if line[:1] in ("-", "*", "•"):
            lines.append("• " + line[1:].strip())
    if version and lines:
        result[version] = "\n".join(lines)
    return result


async def _github(url: str) -> httpx.Response | None:
    """Запрос к GitHub. None — СЕТЬ не дала ответа (в отличие от ответа 404).

    Отдельный клиент, а не `ctx`: тот ходит в бота с его заголовками и сессией
    админа, отправлять их наружу нельзя. Таймаут короткий и разница «не ответил»
    против «ответил 404» важна: экран админки не должен ждать чужую сеть, а
    ходить на второй адрес, когда первый упёрся в глухую сеть, бессмысленно —
    это ещё столько же ожидания с тем же итогом.
    """
    try:
        async with httpx.AsyncClient(timeout=6, follow_redirects=True) as client:
            return await client.get(url)
    except httpx.HTTPError:
        return None


async def _cabinet_remote() -> tuple[dict[str, str], bool]:
    """CHANGELOG.md кабинета из репозитория: версии, которых ещё НЕ стоит.

    Второй возвращаемый флаг — «сеть отвечала»: по нему решаем, идти ли за
    датами релизов. Без сети второй запрос только задержит экран.
    """
    for branch in ("main", "master"):
        resp = await _github(
            f"https://raw.githubusercontent.com/{_CABINET_REPO}/{branch}/CHANGELOG.md"
        )
        if resp is None:
            return {}, False
        parsed = _changelog(resp.text) if resp.status_code == 200 else {}
        if parsed:
            return parsed, True
    return {}, True


async def _cabinet_meta() -> tuple[dict[str, str], dict[str, str]]:
    """Даты и ссылки релизов с GitHub — необязательное украшение ленты.

    Релизов у репозитория может не быть вовсе (лента живёт в CHANGELOG.md), и
    тогда ни даты, ни ссылки нет. Придумывать адрес `releases/tag/vX.Y` не
    станем: ссылка на несуществующую страницу — то же враньё, только мелкое.
    """
    urls: dict[str, str] = {}
    dates: dict[str, str] = {}
    resp = await _github(f"https://api.github.com/repos/{_CABINET_REPO}/releases?per_page=50")
    if resp is not None and resp.status_code != 200:
        resp = None
    try:
        rows = resp.json() if resp is not None else []
    except ValueError:
        rows = []
    if not isinstance(rows, list):
        return urls, dates
    for row in rows:
        if not isinstance(row, dict) or row.get("draft"):
            continue
        tag = str(row.get("tag_name") or "").strip().lstrip("vV")
        if not tag:
            continue
        urls[tag] = str(row.get("html_url") or "")
        dates[tag] = str(row.get("published_at") or row.get("created_at") or "")
    return urls, dates


async def _cabinet_feed(force: bool, local_text: str) -> list[dict[str, Any]]:
    """Лента кабинета: карточки версий, старшая первой.

    Локальный CHANGELOG (когда каталог кабинета проброшен) — офлайн-основа,
    репозиторный добавляет версии, которых ещё НЕ стоит: ради них лента и нужна.
    Версия есть в обоих — берём репозиторную, она свежее.
    """
    now = time.monotonic()
    cached = _feed_cache["value"]
    if not force and cached is not None and now - _feed_cache["at"] < _feed_cache["ttl"]:
        return cached

    remote, network = await _cabinet_remote()
    notes = {**_changelog(local_text), **remote}
    # За датами идём, только если сеть вообще отвечает: иначе это лишние секунды
    # ожидания на экране ради украшения, которого всё равно не будет.
    urls, dates = await _cabinet_meta() if notes and network else ({}, {})
    items = [
        {
            "version": f"v{version}",
            # Имя = версия: карточку подписывает заголовок блока, а не строка
            # «Кабинет» в каждом релизе (кабинет такое имя прячет сам).
            "name": f"v{version}",
            "date": dates.get(version) or None,
            "notes": notes[version],
            "url": urls.get(version) or None,
        }
        for version in sorted(notes, key=_version, reverse=True)[:20]
    ]
    _feed_cache.update(at=now, ttl=_FEED_TTL if items else _FEED_FAIL_TTL, value=items)
    return items


async def _cabinet_block(force: bool) -> dict[str, Any]:
    """Блок «Кабинет»: что стоит, что вышло и чем обновлять."""
    local_version, local_text = _cabinet_local()
    installed = (os.environ.get("CABINET_VERSION") or local_version).strip().lstrip("vV")
    items = [dict(item) for item in await _cabinet_feed(force, local_text)]

    known = _version(installed) if installed else None
    if known is not None:
        for item in items:
            # Пометку «не установлено» ставим, только когда есть с чем сравнивать:
            # без версии кабинета любая такая пометка — догадка.
            item["installed"] = _version(item["version"]) <= known
    latest = items[0]["version"] if items else None

    notes = []
    if not installed:
        notes.append(
            "Какая версия кабинета стоит — отсюда не видно: у адаптера нет файла VERSION "
            "(он собирается из каталога adapter/, а VERSION лежит выше). Поэтому «доступно "
            "обновление» здесь не пишется — сравнивать не с чем. Чтобы версия появилась, "
            "задайте адаптеру переменную CABINET_VERSION или пробросьте ему каталог кабинета "
            "томом только на чтение (-v /opt/remnashop:/cabinet:ro)."
        )
    if not items:
        notes.append(
            "Лента релизов кабинета сейчас недоступна: не удалось прочитать CHANGELOG.md "
            f"репозитория {_CABINET_REPO} (нет доступа к GitHub?). Команда обновления от этого "
            "не меняется."
        )
    return {
        "title": _CABINET_TITLE,
        "current": installed or None,
        "latest": latest,
        "update_available": bool(latest and known is not None and _version(latest) > known),
        "repo": _CABINET_REPO,
        "note": " ".join(notes) or None,
        "commands": [
            {
                "cmd": "cd /opt/remnashop && ./update.sh",
                "when": (
                    "Кабинет стоит на сервере бота (обычная установка). Каталог — тот, "
                    "куда его ставили."
                ),
            },
            {
                "cmd": 'cd /opt/remnashop-cabinet && DEST="$PWD" bash site-install.sh',
                "when": (
                    "Кабинет на ОТДЕЛЬНОМ сервере (режим site): команду выполняют на сервере "
                    "КАБИНЕТА и в ЕГО каталоге — том, куда его ставили. Обновление там — "
                    "повторный запуск того же установщика; update.sh не подходит, он "
                    "пересобирает ещё и бота, которого на сервере кабинета нет."
                ),
            },
        ],
        "items": items,
    }


# --- расхождение -------------------------------------------------------------


def _label(version: Any) -> str:
    """Версия для фразы: с «v» спереди. Установленная приходит без неё
    (файл VERSION, `current_version` бота), выпущенная — с ней (тег релиза),
    и в одном предложении «1.0.6 → v1.0.8» читается как опечатка."""
    text = str(version or "").strip()
    return text if text[:1] in ("v", "V") else f"v{text}"


def _mismatch(cabinet: dict[str, Any], bot: dict[str, Any]) -> str | None:
    """Словами: что из двух отстало. Молчим, когда сравнивать нечего (версия
    кабинета не видна) или когда оба в одинаковом состоянии — про это уже
    написано в самих блоках."""
    # Пустая лента — это «неизвестно», а не «свежий»: без последней вышедшей
    # версии сказать «бот свежий» нельзя, а блок рядом честно пишет, что ленты
    # нет. Иначе экран противоречит сам себе в одном виде.
    if not cabinet.get("latest") or not bot.get("latest"):
        return None
    if not cabinet["current"] or not bot["current"]:
        return None
    cabinet_old, bot_old = cabinet["update_available"], bot["update_available"]
    if not cabinet_old and not bot_old:
        return None
    if cabinet_old and bot_old:
        return (
            f"Отстали оба: кабинет {_label(cabinet['current'])} → {_label(cabinet['latest'])}, "
            f"бот {_label(bot['current'])} → {_label(bot['latest'])}. Обновлять их надо по "
            "отдельности — каждый своей командой."
        )
    if bot_old:
        return (
            f"Кабинет свежий ({_label(cabinet['current'])}), а бот отстал: стоит "
            f"{_label(bot['current'])}, вышла {_label(bot['latest'])}. Обновлять надо бота — "
            f"командой из блока «{bot['title']}»."
        )
    return (
        f"Бот свежий ({_label(bot['current'])}), а кабинет отстал: стоит "
        f"{_label(cabinet['current'])}, вышла {_label(cabinet['latest'])}. Обновлять надо "
        f"кабинет — командой из блока «{_CABINET_TITLE}»."
    )


@handler("GET", "/api/admin/updates")
async def updates(ctx: Ctx) -> dict[str, Any]:
    force = (ctx.query.get("force") or "").strip().lower() in ("1", "true", "yes")
    # Лента бота — строгий запрос: это их ручка и их право `updates:read`, и их
    # отказ должен доехать до кабинета как есть.
    # `force=1` («Проверить») их ручка не принимает: у неё свой часовой кэш
    # ответа гитхаба. Запрос всё равно уходит заново, поэтому кнопка не врёт,
    # но мгновенной перепроверки у этого бота нет. Наша лента `force` слушается.
    data = await ctx.json(_RELEASES, {}) or {}
    bot, legacy_items = _bot_block(data)
    cabinet = await _cabinet_block(force)
    return {
        # Поля в корне — прежние, для экрана, который про блоки не знает.
        "current": bot["current"] or "",
        "latest": bot["latest"],
        "update_available": bot["update_available"],
        "repo": bot["repo"],
        "items": legacy_items,
        # Новое: два блока и расхождение между ними. Наш бэкенд этих полей не
        # шлёт, и кабинет обязан работать без них по-старому.
        "cabinet": cabinet,
        "bot": bot,
        "mismatch": _mismatch(cabinet, bot),
    }
