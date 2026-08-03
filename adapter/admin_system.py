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

  • Настройки. У них это ПЛОСКИЙ справочник из ~750 ключей (`KEY: value` с типом
    и категорией), у нас — семь осмысленных блоков. Полного соответствия нет и
    быть не может, поэтому переведено ровно то, что ложится один-в-один
    (бэкапы, требование правил, обязательный канал, охрана триала, рефералка,
    переключатели уведомлений). Всё остальное отдаётся пустым/нейтральным, а на
    ПОПЫТКУ это поменять сохранение отвечает 501 с причиной — молча проглотить
    правку хуже, чем отказать: админ уверен, что сохранил.
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
  • Обновления. Своей ленты (VERSION + CHANGELOG.md кабинета) в образе адаптера
    нет — и подсмотреть, какая версия кабинета стоит, ему тоже негде. Зато у
    бота есть СВОЯ лента — `/cabinet/admin/updates/releases`. Показываем её:
    владельцу над «Бедолагой» обновлять надо именно бота, и это настоящие
    релизы, а не выдумка. Каждая карточка подписана «Бот «Бедолага»», чтобы
    ленту нельзя было принять за релизы кабинета.

  • Сохранение настроек проверяем ПОСЛЕ записи. Параметры, заданные в файле
    окружения бота (.env), их API принимает и кладёт в базу, но применяет
    всё равно значение из окружения — ответ 200, значение прежнее. Без
    проверки кабинет рисовал бы «Сохранено!» там, где не изменилось ничего.

ПРО РАЗДЕЛ «НАСТРОЙКИ». Он включится не от этого файла: группа `settings` в
`ADMIN_SECTION_REQUIREMENTS` объявляет два десятка путей (кэшбэк, пополнение,
резерв, заморозка, промо-баннер, win-back, дайджест, алерты, почта, 2FA…), и
раздел честен только целиком. Пока их нет, `/api/admin/settings` просто лежит
готовым. Разделы «Аудит» и «Обновления» — по одному пути, они включаются сразу.
"""

from __future__ import annotations

import re
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


def _notifications(defs: dict[str, dict[str, Any]]) -> dict[str, bool]:
    """Переключатели уведомлений: их булевы ключи из «уведомительных» категорий.

    Ключи отдаём ИХ, без перевода в наши имена. Кабинет нарисует их как
    «Admin notifications purchases enabled» — некрасиво, зато админ видит ровно
    тот параметр, который правит в боте, и сохранение попадает туда же.
    Придумывать соответствие «их ключ ≈ наш ключ» опаснее: пара похожих по
    названию событий значит в двух ботах разное, и админ выключил бы не то.

    Read-only ключи пропускаем: рисовать переключатель, который их же API
    отобьёт с 403 при сохранении, — обман.
    """
    result: dict[str, bool] = {}
    for key, row in defs.items():
        category = row.get("category")
        category_key = category.get("key") if isinstance(category, dict) else None
        if category_key not in _NOTIFY_CATEGORIES:
            continue
        if row.get("type") != "bool" or row.get("read_only"):
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
    """
    channel_link, channel_id = _channel_view(channels)
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
        "notifications": _notifications(defs),
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


def _plan_extra(payload: dict[str, Any], view: dict[str, Any], out: dict[str, Any]) -> None:
    guard = view["extra"]["trial_channel_guard"]
    if (value := _changed(payload, "trial_channel_guard", guard)) is not None:
        out[_KEY_TRIAL_CHANNEL_GUARD] = bool(value)
    if _changed(payload, "mini_app_reserve", False) is not None:
        raise NotAvailable("Резервного mini app у этого бота нет")


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
# Их ручка отдаёт два блока — `bot` и `cabinet`. Берём ТОЛЬКО `bot`:
#   • это и есть бэкенд, который владельцу над «Бедолагой» надо обновлять;
#   • их `cabinet` — их собственный веб-кабинет, к нашему отношения не имеет, и
#     блок там к тому же липовый (`current_version` = последний релиз с гитхаба,
#     `has_updates` всегда false), то есть «обновление доступно» он не покажет
#     никогда.

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


@handler("GET", "/api/admin/updates")
async def updates(ctx: Ctx) -> dict[str, Any]:
    # `force=1` («Проверить») их ручка не принимает: у неё свой часовой кэш
    # ответа гитхаба. Запрос всё равно уходит заново, поэтому кнопка не врёт,
    # но мгновенной перепроверки у этого бота нет.
    data = await ctx.json(_RELEASES, {}) or {}
    bot = data.get("bot") if isinstance(data.get("bot"), dict) else {}
    current = str(bot.get("current_version") or "")
    repo_url = str(bot.get("repo_url") or "").rstrip("/")
    installed = _version(current)

    items = []
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
                # Подпись обязательна: экран называется «Обновления» и в нашей
                # установке показывает релизы кабинета, а здесь это релизы БОТА.
                "name": f"Бот «Бедолага» · {name}" if name and name != tag else "Бот «Бедолага»",
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
    return {
        "current": current,
        "latest": latest,
        "update_available": bool(latest and _version(latest) > installed),
        # Наш контракт ждёт «владелец/репозиторий», а не полный адрес.
        "repo": repo_url.replace("https://github.com/", ""),
        "items": items,
    }
