"""Win-back истёкших: «вернитесь — вот скидка» через N дней после конца подписки.

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ — общее правило адаптера: раздел = файл `admin_*.py`,
загрузчик в `compose.py` подхватывает их по маске, а в образ файл попадает
строкой `COPY *.py` в Dockerfile. Страница `/admin/winback` уже описана в
`ADMIN_PAGE_REQUIREMENTS` и появляется в меню ровно тогда, когда здесь
зарегистрирован GET `/api/admin/winback`. Форма конфига (`enabled`, `percent`,
`days_after`, `lifetime_hours`) и её границы взяты с экрана кабинета —
`WinbackCard` в AdminSettingsPage.tsx шлёт ровно эти четыре поля и ждёт их же в
ответе.

ЧЬЯ ЭТО ФУНКЦИЯ. Сама «возвращалка» — свойство КАБИНЕТА: у «Бедолаги» такого
сценария нет, хранить настройку негде, кроме своей памяти (`state.py`). А вот
СКИДКА — целиком их: мы её не выдумываем и не считаем сами, а выдаём их
средствами (см. ниже). Правило проекта: не подменять чужую механику своей.

СКИДКА ВЫДАЁТСЯ ПО-НАСТОЯЩЕМУ. У «Бедолаги» есть ДВА механизма персональной
скидки, и они не равноценны:

  1. Промо-оффер (`POST /promo-offers`, машинный ключ) — персональная запись
     `DiscountOffer` на конкретного человека. Скидкой сама по себе НЕ становится:
     оффер надо ЗАБРАТЬ (`POST /cabinet/promo/claim`), и только клейм пишет
     процент в `users.promo_offer_discount_percent`. Кнопки клейма нет ни в нашем
     кабинете (см. `promo_banner` в compose.py — баннер честно отсылает в бота),
     ни в самом боте без их же сообщения с кнопкой, которого создание оффера
     через API не шлёт. То есть выданный так оффер человек может и не суметь
     забрать — это была бы скидка на бумаге.

  2. Промокод типа `discount` (`POST /promo-codes`, машинный ключ) — одноразовая
     ПРОЦЕНТНАЯ скидка. В их модели у этого типа поля переиспользованы
     (`app/services/promocode_service.py`, ветка `PromoCodeType.DISCOUNT`):
       • `balance_bonus_kopeks` — ПРОЦЕНТ скидки (1–100), не копейки;
       • `subscription_days`   — сколько ЧАСОВ живёт скидка после активации
                                 (0 = бессрочно, до первой покупки);
       • `max_uses = 1`        — код сгорает после первой активации;
       • `valid_until`         — до какого момента код вообще можно активировать.
     Активация доступна человеку СЕГОДНЯ и в боте, и в нашем кабинете (форма
     промокода → `POST /api/promocode/activate` в compose.py), а активированная
     скидка тут же уменьшает цены в витрине и показывается баннером
     (`GET /api/trial-discount`).

Берём второй. Проверено на живом стенде от начала до конца: код создан машинным
ключом, активирован из кабинета, цены всех периодов упали на заданный процент,
баннер скидки загорелся, код после активации сгорел.

Цена выбора — её надо знать: скидка не «появляется сама», человек делает ОДНО
действие (вводит присланный код). Тихо применить процент за него нечем: писать
в `users.promo_offer_discount_percent` может только их код, у машинного API такой
ручки нет, а лезть в их базу мимо их же логики адаптеру нельзя.

Второе следствие: их правило «одна активная скидка на человека»
(`active_discount_exists`). Если у человека уже есть действующая скидка — от
промо-оффера или другого промокода, — наш код он активировать не сможет, пока
своя не кончится. Код при этом не сгорает и живёт до `valid_until`. Ломать это
правило мы не имеем права: оно защищает владельца от сложения скидок.

КОМУ ПИШЕМ. Канал ровно один — личное сообщение бота (см. bot_session.py, там же
причина, почему нужен служебный вход). Web Push здесь нет и взяться ему неоткуда:
подписками браузера владеет наш бэкенд, а не бот. Значит человек без телеграма
(в кабинете регистрируются почтой) недостижим — и промокод ему не выпускается
вовсе: код, о котором некому рассказать, это мусор в их админке.

ПОРЯДОК ДЕЙСТВИЙ СТРОГИЙ: сначала выпускаем код, потом пишем; не дошло —
удаляем выпущенный код и НЕ ставим отметку о выдаче. Иначе первый же сбой
телеграма оставлял бы после себя выданные, но никому не известные промокоды.

ДЕДУПЛИКАЦИЯ. Один человек — одно предложение. Отметка (id → дата) пишется в
свою память СРАЗУ после успешной отправки: контейнер перезапускают при каждом
обновлении, и «сохраним в конце прогона» означало бы второе письмо тем, кому уже
написали. Своих таблиц не заводим (файл базы общий) — отметки лежат одной
записью в `settings`.

НЕУДАЧНАЯ ДОСТАВКА ТОЖЕ ЗАПОМИНАЕТСЯ. Отметку «выдано» на отказе ставить нельзя,
но и делать вид, что попытки не было, — тоже: человек, заблокировавший бота,
недостижим НАВСЕГДА, а прогон повторяется каждый час всё окно поимки. Без памяти
о неудаче это сотни циклов «выпустили код — не доставили — удалили код» на одного
такого человека (окно поимки две недели — это под четыре сотни заходов). Поэтому:
  • их машинный код отказа `forbidden` / `no_telegram_id` (сообщение блокировано,
    телеграма нет) — это приговор, отметка ставится сразу и навсегда;
  • всё остальное (бот не ответил, 500) — беда этого часа: считаем попытки и
    отступаемся после `_MAX_TRIES`, чтобы неудача была ограничена по цене, а не
    по времени;
  • отказ в ВЫПУСКЕ кода — беда не человека, а всего прогона (нет прав, ключ,
    бот лежит): прогон останавливается на первом же таком отказе с причиной в
    логе и в сводке, а не повторяет её на каждом кандидате.
Приговор от временной беды отличаем ТОЛЬКО по машинному коду отказа
(`bot_session.send` отдаёт его третьим значением): английская фраза в `detail`
у них меняется от сборки к сборке, и решение «больше не пробовать никогда»
опирать на неё нельзя.
"""

from __future__ import annotations

import asyncio
import html
import secrets
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatchcase
from typing import Any
from zoneinfo import ZoneInfo

import httpx

import bot_session
from compose import Ctx, NotAvailable, UpstreamError, handler
from scheduler import task

# --- что и где храним ---------------------------------------------------------

# Ключи в своей памяти (`state.settings`).
_KEY = "winback"
_MARK_KEY = "winback_sent"

# Дефолт совпадает с нашим бэкендом (`overlay_winback.py`): ВЫКЛЮЧЕНО, 20 % через
# 3 дня после окончания, промо живёт неделю. Рассылка, включившаяся сама собой при
# обновлении, — это письма людям, которых никто не заказывал.
_DEFAULT: dict[str, Any] = {
    "enabled": False,
    "percent": 20,
    "days_after": 3,
    "lifetime_hours": 168,
}

# Границы те же, что у формы кабинета (WinbackCard: 1–100, 1–90, 1–1440).
_PERCENT_MIN, _PERCENT_MAX = 1, 100
_DAYS_MIN, _DAYS_MAX = 1, 90
_HOURS_MIN, _HOURS_MAX = 1, 1440

# --- окно поимки и пределы прогона ---------------------------------------------

# Окно поимки: берём тех, у кого подписка кончилась от `days_after` до
# `days_after + _CATCH_DAYS` дней назад. Без окна задача ловила бы ровно «сегодня
# ему N-й день», и любой простой (обновление контейнера, сутки без интернета)
# означал бы, что целая когорта пропущена навсегда. Две недели — тот же запас,
# что и у нашего бэкенда.
_CATCH_DAYS = 14

# Пауза между сообщениями. Телеграм пропускает у бота около 30 сообщений в
# секунду и на превышении отвечает 429 всему боту сразу — спешка нашей рассылки
# ударила бы по чужим ответам бота, а не только по ней. Пауза и потолок ниже
# считают ПОПЫТКИ, а не удачи: неудачная попытка стоит боту ТРЁХ обращений
# (выпуск кода, отправка, отзыв кода), и пропускать её мимо счётчика значит
# долбить бота без единой паузы ровно тогда, когда ему и так плохо.
_SEND_PAUSE = 0.1
# Потолок на один прогон: остаток уйдёт следующим часом (отметки проставлены,
# повторов не будет). Без потолка первый же запуск на большой базе не уложился бы
# в предел времени задачи и был бы брошен посередине.
_MAX_PER_RUN = 200
# Сколько раз пробуем достучаться до одного человека, пока причина отказа не
# названа приговором. Пять часовых заходов — это пять выпущенных и отозванных
# кодов на человека вместо сотен, и при этом целых пять часов на то, чтобы бот
# ожил, если беда была временной.
_MAX_TRIES = 5
# Сколько людей показывает холостой прогон из админки: за ручкой стоит человек у
# экрана, ему нужен образец, а не полный список базы.
_DRY_LIMIT = 25

_BOT_PAGE = 200            # у их `/users` limit ≤ 200 (жёстко, по их схеме)
_PAGES_LIMIT = 200         # предохранитель от бесконечного обхода страниц

# Тихие часы по поясу БОТА (по нему живёт владелец, см. их настройку TIMEZONE).
# Рекламное сообщение в четыре утра — верный способ получить блокировку бота
# вместо возврата. Окно поимки шире двух недель, поэтому отложить на несколько
# часов ничего не стоит. Настройкой это не делаем сознательно: экран кабинета
# шлёт ровно четыре поля, и лишнее в ответе он всё равно не покажет.
_QUIET_FROM, _QUIET_TO = 22, 9

# Их право на личное сообщение (`app/cabinet/routes/admin_users.py`:
# `require_permission('users:send_message')`).
_SEND_RIGHT = "users:send_message"

# Их машинные коды отказа в доставке, которые не меняются от повтора: человек
# заблокировал бота или телеграма у него нет вовсе (routes/admin_users.py:2166,
# 2192). Всё прочее — беда часа, а не приговор.
_NEVER_AGAIN = ("forbidden", "no_telegram_id")

# Промокод: из чего собираем и сколько раз пробуем при совпадении.
# Алфавит без 0/O/1/I — код читают глазами из сообщения и набирают руками.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_PREFIX = "BACK"
_CODE_BODY = 6
_CODE_TRIES = 5

# Слово «VPN» в тексте нарочно не звучит: адаптер работает поверх ЧУЖОГО бота, и
# как называется сервис — решает его владелец в своём оформлении, а не мы.
_MSG = {
    "ru": (
        "💜 Возвращайтесь — скидка {percent}%",
        "Мы соскучились! Промокод <code>{code}</code> даёт скидку {percent}% "
        "на возврат.\n\nВведите его в кабинете или в боте до {until} — "
        "предложение одноразовое и действует ограниченное время.",
    ),
    "en": (
        "💜 Come back — {percent}% off",
        "We miss you! Promo code <code>{code}</code> gives you {percent}% off.\n\n"
        "Enter it in the cabinet or in the bot before {until} — "
        "it is single-use and time-limited.",
    ),
}


def _log(message: str) -> None:
    # Тот же префикс, что у остального адаптера: лог контейнера общий.
    print(f"[adapter] win-back: {message}", flush=True)


def _fail(status: int, detail: str) -> UpstreamError:
    """Отказ самого адаптера в той же форме, что и ошибка бота."""
    return UpstreamError(httpx.Response(status, json={"detail": detail}))


# --- настройка ----------------------------------------------------------------


def _clamp(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _normalize(raw: Any) -> dict[str, Any]:
    """Форма ответа ровно такая, какую ждёт экран кабинета (WinbackConfig)."""
    data = raw if isinstance(raw, dict) else {}
    return {
        "enabled": bool(data.get("enabled", _DEFAULT["enabled"])),
        "percent": _clamp(
            data.get("percent"), int(_DEFAULT["percent"]), _PERCENT_MIN, _PERCENT_MAX
        ),
        "days_after": _clamp(
            data.get("days_after"), int(_DEFAULT["days_after"]), _DAYS_MIN, _DAYS_MAX
        ),
        "lifetime_hours": _clamp(
            data.get("lifetime_hours"),
            int(_DEFAULT["lifetime_hours"]),
            _HOURS_MIN,
            _HOURS_MAX,
        ),
    }


def _config(state: Any) -> dict[str, Any]:
    if state is None or not getattr(state, "available", False):
        # Хранить настройку негде — значит её нет, а «нет» здесь равно
        # «выключено»: врать тумблером про работающую рассылку нельзя.
        return dict(_DEFAULT)
    return _normalize(state.get_setting(_KEY, {}))


# --- память о выданных предложениях --------------------------------------------

# Сколько храним отметку. Внутри окна поимки повтор невозможен и без срока —
# человек выходит из окна через `days_after + _CATCH_DAYS` дней. Срок нужен от
# другого: отметки лежат ОДНОЙ записью JSON в общей памяти (своих таблиц нам
# заводить нельзя), и вечный список рано или поздно превратит каждую отправку в
# перезапись мегабайта. Год с лишним — заведомо больше любого окна: тот, кто ушёл
# и вернулся спустя год, это уже другой случай, и предложить ему снова честно.
_MARK_TTL_DAYS = 400


# Хвост отметки. Без хвоста «YYYY-MM-DD» — предложение ВЫДАНО (так писали
# раньше, старые записи читаются как были). С хвостом это память о НЕУДАЧЕ:
#   «YYYY-MM-DD!x» — доставить нельзя никогда (бот заблокирован, телеграма нет);
#   «YYYY-MM-DD!3» — три неудачные попытки, четвёртая ещё будет.
# Одна строка вместо второй записи в хранилище: файл базы общий, и заводить ради
# счётчика попыток вторую таблицу нам нельзя.
_MARK_FAIL = "!"
_MARK_NEVER = "!x"


def _mark_state(value: Any) -> tuple[str, int]:
    """Отметка → («sent» | «never» | «tries» | «», сколько попыток было)."""
    text = str(value or "").strip()
    if not text:
        return "", 0
    if _MARK_FAIL not in text:
        return "sent", 0
    tail = text.split(_MARK_FAIL, 1)[1]
    if tail == "x":
        return "never", 0
    try:
        return "tries", int(tail)
    except ValueError:
        # Непонятный хвост — считаем выданным: лишний раз промолчать дешевле,
        # чем прислать человеку второе предложение.
        return "sent", 0


def _marks(state: Any) -> dict[str, str]:
    """Кому уже выдавали: id → дата выдачи (YYYY-MM-DD) или та же дата с хвостом
    неудачи. Протухшее отбрасываем при чтении, чтобы запись не росла бесконечно."""
    if state is None or not getattr(state, "available", False):
        return {}
    raw = state.get_setting(_MARK_KEY, {})
    if not isinstance(raw, dict):
        return {}
    edge = (datetime.now(timezone.utc) - timedelta(days=_MARK_TTL_DAYS)).strftime("%Y-%m-%d")
    out: dict[str, str] = {}
    for key, value in raw.items():
        # Значение храним ЦЕЛИКОМ (раньше резалось до десяти символов — и хвост
        # неудачи терялся бы при первом же чтении), а для срока смотрим на дату в
        # его начале. Даты пишем одним форматом (ISO-дата), поэтому сравнение
        # строк здесь равносильно сравнению дат. Непонятную запись держим:
        # потерять отметку хуже, чем хранить лишнюю — потеря означает второе
        # письмо человеку.
        text = str(value or "").strip()
        if len(text) >= 10 and text[:10] < edge:
            continue
        try:
            name = str(int(key))
        except (TypeError, ValueError):
            continue
        if text:
            out[name] = text
    return out


def _save_marks(state: Any, marks: dict[str, str]) -> None:
    if state is None or not getattr(state, "available", False):
        return
    state.set_setting(_MARK_KEY, marks)


def _mark_failure(
    state: Any, marks: dict[str, str], key: str, now: datetime, forever: bool
) -> str:
    """Запомнить неудачу доставки. Возвращает новое значение отметки.

    Пишем сразу, как и отметку об удаче: контейнер перезапускают при каждом
    обновлении, а «сохраним в конце прогона» здесь означало бы, что счётчик
    попыток обнуляется чаще, чем растёт, и заблокировавший бота человек снова
    получает свои сотни выпущенных-и-отозванных кодов.
    """
    _kind, tries = _mark_state(marks.get(key))
    tail = _MARK_NEVER if forever else f"{_MARK_FAIL}{min(tries + 1, _MAX_TRIES)}"
    marks[key] = now.strftime("%Y-%m-%d") + tail
    _save_marks(state, marks)
    return marks[key]


# --- права --------------------------------------------------------------------


async def _require_admin(ctx: Ctx) -> None:
    """Читать настройку может администратор бота.

    Эта ручка не ходит в их API за данными, а читает своё хранилище, поэтому
    права обязана спросить явно: иначе настройку кабинета видел бы любой, кто
    знает путь.
    """
    if not ctx.token:
        raise _fail(401, "Требуется вход")
    me = await ctx.json("/cabinet/auth/me/is-admin", {}, soft=True) or {}
    if not me.get("is_admin"):
        raise _fail(403, "Недостаточно прав")


async def _require_send_right(ctx: Ctx) -> None:
    """Менять настройку может тот, кому их RBAC разрешает писать людям.

    Сохранение здесь — не «поправить текст на экране», а включение рассылки
    личных сообщений от имени их бота, да ещё и с выдачей скидок. Право на
    рассылку у них уже есть (`users:send_message`), поэтому спрашиваем его, а не
    выдумываем своё: админ «только для просмотра» не должен уметь раздать скидку
    всей истёкшей базе. Сравнение по их правилу — право пользователя это ШАБЛОН
    (`*:*`, `users:*`). Не смогли спросить — считаем, что права нет.
    """
    data = await ctx.json("/cabinet/auth/me/permissions", {}, soft=True) or {}
    granted = data.get("permissions")
    ok = isinstance(granted, list) and any(
        isinstance(p, str) and fnmatchcase(_SEND_RIGHT, p) for p in granted
    )
    if not ok:
        raise _fail(403, f"Недостаточно прав в боте: нужно право «{_SEND_RIGHT}»")


# --- доставка ------------------------------------------------------------------


def _delivery_configured() -> bool:
    # Общий служебный вход: см. bot_session.py — там же и причина, почему он нужен.
    return bot_session.configured()


# --- время ---------------------------------------------------------------------


async def _bot_timezone(ctx: Ctx) -> tuple[Any, str]:
    """(пояс, его имя). Пояс бота — то, по чему живёт владелец. Не спросили или
    имя незнакомое — считаем по своему времени и говорим об этом вслух."""
    resp = await ctx.admin("GET", "/settings/TIMEZONE")
    name = ""
    if resp is not None and resp.status_code < 400:
        try:
            name = str((resp.json() or {}).get("current") or "").strip()
        except ValueError:
            name = ""
    if name:
        try:
            return ZoneInfo(name), name
        except Exception:  # noqa: BLE001 — незнакомое имя пояса не повод падать
            _log(f"часовой пояс бота «{name}» не распознан — считаю по своему времени")
    local = datetime.now().astimezone().tzinfo or timezone.utc
    return local, str(datetime.now(local).tzname() or "локальный")


def _is_quiet(now: datetime) -> bool:
    """Тихие часы. Окно перешагивает полночь, поэтому не диапазон, а два куска."""
    return now.hour >= _QUIET_FROM or now.hour < _QUIET_TO


def _stamp(value: Any) -> datetime | None:
    """Их метка времени → datetime в UTC. None — поля нет или оно битое."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        # Их ответы приходят с «Z», который fromisoformat до 3.11 не понимал.
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# --- откуда берём людей ---------------------------------------------------------


def _detail(resp: httpx.Response) -> str:
    """Причина отказа словами. У них `detail` бывает и строкой, и объектом с
    `code`/`message`."""
    return bot_session.detail(resp)


async def _bot_users(ctx: Ctx) -> list[dict[str, Any]]:
    """Люди бота: кому писать, под каким id и когда у них кончилась подписка.

    Берём машинным ключом (`ctx.admin`) — это единственный доступ, который есть у
    фоновой задачи. Ошибку не проглатываем: пустой список молча означал бы «никто
    не истёк», а это неправда.

    Конец списка определяем ТОЛЬКО по короткой странице. Поле `total` в их ответе
    доверия не заслуживает: на живом стенде оно равно 1 при семи пользователях
    (проверено запросом), и остановка по нему обрезала бы рассылку после первой
    страницы.
    """
    if not ctx.can_admin:
        raise RuntimeError(
            "адаптеру не выдан машинный ключ бота (BEDOLAGA_ADMIN_TOKEN) — "
            "ни список истёкших, ни выдача промокода без него невозможны"
        )
    out: list[dict[str, Any]] = []
    offset = 0
    for _ in range(_PAGES_LIMIT):
        resp = await ctx.admin(
            "GET", "/users", params={"limit": _BOT_PAGE, "offset": offset}
        )
        if resp is None:
            raise RuntimeError("бот недоступен: список пользователей не получен")
        if resp.status_code >= 400:
            raise RuntimeError(f"бот отказал в списке пользователей: {_detail(resp)}")
        try:
            data = resp.json() or {}
        except ValueError as exc:
            raise RuntimeError("бот ответил не-JSON на список пользователей") from exc
        batch = data.get("items") or []
        if not isinstance(batch, list) or not batch:
            break
        out.extend(x for x in batch if isinstance(x, dict))
        offset += len(batch)
        if len(batch) < _BOT_PAGE:
            break
    return out


def _last_end(user: dict[str, Any]) -> datetime | None:
    """Когда у человека кончается (или кончилась) подписка — самая поздняя из всех.

    Смотрим и текущую (`subscription`), и весь список (`subscriptions`): в
    мульти-тарифном режиме их у человека несколько, и если хоть одна ещё живёт,
    он никуда не уходил. Брать только «текущую» значило бы позвать назад того, кто
    и не уходил, — и подарить ему скидку ни за что.
    """
    latest: datetime | None = None
    rows = list(user.get("subscriptions") or [])
    current = user.get("subscription")
    if isinstance(current, dict):
        rows.append(current)
    for row in rows:
        if not isinstance(row, dict):
            continue
        end = _stamp(row.get("end_date"))
        if end and (latest is None or end > latest):
            latest = end
    return latest


def _examine(
    users: list[dict[str, Any]],
    marks: dict[str, str],
    config: dict[str, Any],
    now: datetime,
) -> list[dict[str, Any]]:
    """Вердикт по каждому человеку — до единого обращения наружу.

    Чистая функция: сюда приходят готовые списки, отсюда уходит решение. Так его
    видно на глаз и можно проверить без единой отправки и без единого выданного
    промокода. Причина отказа не выбрасывается, а едет дальше: холостой прогон
    показывает владельцу, почему конкретный человек остался без предложения.
    """
    days_after = int(config["days_after"])
    # Кончилась РАНЬШЕ, чем `days_after` назад, но не раньше края окна поимки.
    newest = now - timedelta(days=days_after)
    oldest = now - timedelta(days=days_after + _CATCH_DAYS)

    out: list[dict[str, Any]] = []
    for user in users:
        try:
            uid = int(user.get("id"))
        except (TypeError, ValueError):
            continue
        end = _last_end(user)
        record: dict[str, Any] = {
            "user_id": uid,
            "telegram_id": user.get("telegram_id"),
            "language": str(user.get("language") or "ru")[:2].lower(),
            "expired_at": end.isoformat() if end else None,
            # Ключ дедупликации прежний — сам человек: «один человек — одно
            # предложение». Носим его в записи, чтобы прогон не собирал строку
            # заново там, где ставит отметку.
            "mark_key": str(uid),
            "skip": "",
        }
        out.append(record)

        if str(user.get("status") or "").lower() != "active":
            # Заблокированным и удалённым «возвращайтесь, вот скидка» —
            # издевательство: вернуться им всё равно не дадут.
            record["skip"] = "аккаунт не активен"
        elif end is None:
            # Подписки не было вовсе — это не «ушёл», это «не приходил».
            # Возвращать такого нечем и незачем: win-back про истёкших.
            record["skip"] = "подписки не было"
        elif end > newest:
            record["skip"] = (
                "подписка ещё живёт" if end > now else f"истекла меньше {days_after} дн. назад"
            )
        elif end < oldest:
            record["skip"] = f"истекла больше {days_after + _CATCH_DAYS} дн. назад"
        elif not user.get("telegram_id"):
            # Канал один — личное сообщение бота. Промокод, о котором некому
            # рассказать, — мусор в их админке, поэтому и не выпускаем.
            record["skip"] = "нет телеграма"
        elif record["mark_key"] in marks:
            kind, tries = _mark_state(marks[record["mark_key"]])
            day = str(marks[record["mark_key"]])[:10]
            if kind == "never":
                # Их приговор, а не наша догадка: бот заблокирован или телеграма
                # нет вовсе. Повторять — это выпускать и тут же удалять код
                # каждый час до конца окна поимки.
                record["skip"] = "доставить нельзя: бот заблокирован (проверено)"
            elif kind == "tries" and tries >= _MAX_TRIES:
                record["skip"] = f"не доставлено {tries} раз подряд — больше не пробуем"
            elif kind != "tries":
                record["skip"] = f"предложение уже выдавали {day}"
            # kind == "tries" и попытки не исчерпаны — человек остаётся
            # кандидатом: беда могла быть часовой.
    return out


# --- скидка их средствами -------------------------------------------------------


def _new_code() -> str:
    """Одноразовый код для одного человека. Случайный, а не «WINBACK123»:
    предсказуемый код увели бы у того, кому он выдан."""
    body = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_BODY))
    return f"{_CODE_PREFIX}{body}"


async def _issue_code(ctx: Ctx, percent: int, hours: int) -> dict[str, Any]:
    """Выпускает одноразовый промокод на скидку. Возвращает их запись о нём.

    Тип `discount` у них переиспользует поля: `balance_bonus_kopeks` — это
    ПРОЦЕНТ, а `subscription_days` — ЧАСЫ жизни скидки после активации (см.
    `promocode_service._apply_promocode_effects`). Не копейки и не дни; передать
    сюда рубли значило бы выдать скидку в тысячи процентов.

    Про два срока. `valid_until` — докуда можно активировать код, `hours` —
    сколько живёт уже активированная скидка. Ставим оба равными `lifetime_hours`:
    человек в сообщении видит дату, до которой надо успеть, а бот при активации
    сам скажет, сколько часов действует скидка. Плата за простоту — активировав
    код в последний час, человек получит скидку ещё на такой же срок; растянуть
    её ровно до общего дедлайна их модель не умеет (часы фиксируются при выпуске).

    Отказ бота не глотаем: выданная «наполовину» скидка хуже отсутствующей.
    """
    deadline = datetime.now(timezone.utc) + timedelta(hours=hours)
    last = ""
    for _ in range(_CODE_TRIES):
        code = _new_code()
        resp = await ctx.admin(
            "POST",
            "/promo-codes",
            json={
                "code": code,
                "type": "discount",
                "balance_bonus_kopeks": int(percent),
                "subscription_days": int(hours),
                "max_uses": 1,
                "valid_until": deadline.isoformat().replace("+00:00", "Z"),
                "is_active": True,
            },
        )
        if resp is None:
            raise RuntimeError("бот недоступен: промокод не выпущен")
        if resp.status_code < 400:
            try:
                data = resp.json() or {}
            except ValueError as exc:
                raise RuntimeError("бот ответил не-JSON на выпуск промокода") from exc
            return {
                "id": data.get("id"),
                "code": str(data.get("code") or code),
                "valid_until": data.get("valid_until") or deadline.isoformat(),
            }
        last = _detail(resp)
        # Единственная беда, которую имеет смысл пережить повтором, — совпадение
        # случайного кода с уже существующим. Всё остальное (нет прав, кривые
        # поля) повторится точно так же.
        if "already exists" not in last.lower():
            break
    raise RuntimeError(f"бот отказал в выпуске промокода: {last or 'причина неизвестна'}")


async def _revoke_code(ctx: Ctx, code_id: Any) -> None:
    """Убирает выпущенный код, о котором так и не удалось рассказать.

    Не критично для человека, поэтому беда здесь только пишется в лог: важнее не
    уронить прогон, чем убрать одну лишнюю строку в их админке.
    """
    if code_id is None:
        return
    resp = await ctx.admin("DELETE", f"/promo-codes/{int(code_id)}")
    if resp is None or resp.status_code >= 400:
        reason = "бот недоступен" if resp is None else _detail(resp)
        _log(f"не удалось убрать невручённый промокод id={code_id}: {reason}")


# --- текст ----------------------------------------------------------------------


def _text(language: str, percent: int, code: str, until: datetime | None, tz: Any) -> str:
    """Готовое сообщение. Бот шлёт его с parse_mode='HTML', поэтому всё, что
    пришло не от нас, экранируем: код из их ответа попадает в разметку."""
    title, body = _MSG.get(language, _MSG["ru"])
    when = until.astimezone(tz).strftime("%d.%m.%Y %H:%M") if until else "—"
    return f"<b>{title.format(percent=percent)}</b>\n\n" + body.format(
        percent=percent, code=html.escape(code), until=when
    )


def _group(skip: str) -> str:
    """Причина отказа без её переменной части — чтобы одинаковые складывались.

    «предложение уже выдавали 2026-08-06» у каждого своё число, и без этого
    сводка превратилась бы в список дат вместо счётчика. То же с числом попыток.
    """
    for head in ("предложение уже выдавали", "не доставлено"):
        if skip.startswith(head):
            return head
    return skip


def _preview(record: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """Строка холостого прогона про одного человека."""
    reasons = [r for r in (record["skip"], extra.pop("skip", "")) if r]
    return {
        "user_id": record["user_id"],
        "telegram_id": record["telegram_id"],
        "language": record["language"],
        "expired_at": record["expired_at"],
        "would_send": not reasons,
        "skip": "; ".join(reasons) or None,
        **extra,
    }


# --- сам прогон -----------------------------------------------------------------


async def run_once(
    ctx: Ctx,
    config: dict[str, Any],
    dry_run: bool = False,
    only_user_id: int | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Один проход. Возвращает сводку для лога и для экрана админки.

    `dry_run=True` — холостой прогон: смотрим, кого нашли, и показываем образец
    сообщения. Ни промокода, ни отправки, ни отметки. Код в образце — заведомо
    ненастоящий (`XXXXXX`): показать живой значило бы выдать скидку ради превью.

    `only_user_id` — посмотреть одного человека вместе с причиной, по которой он
    в рассылку не попадает.
    """
    now = datetime.now(timezone.utc)
    tz, tz_name = await _bot_timezone(ctx)
    summary: dict[str, Any] = {
        "dry_run": dry_run,
        "timezone": tz_name,
        "examined": 0,
        "candidates": 0,
        "sent": 0,
        "unreachable": 0,
        "already_offered": 0,
        "failed": 0,
        # Сколько отказов оказались приговором (бот заблокирован): такие люди
        # больше не в выборке, и владелец должен видеть это отдельно от «не
        # дошло, попробуем ещё».
        "undeliverable": 0,
        "truncated": False,
        # Пусто, пока прогон не остановился на общей беде (нет прав на выпуск
        # кода, бот лёг). Строка здесь — причина, по которой остальные не
        # рассмотрены вовсе.
        "stopped": "",
        "previews": [],
    }

    users = await _bot_users(ctx)
    marks = _marks(ctx.state)
    records = _examine(users, marks, config, now)
    if only_user_id is not None:
        records = [r for r in records if r["user_id"] == only_user_id]
        if not records:
            raise _fail(404, f"Пользователь id={only_user_id} у бота не найден")

    summary["examined"] = len(records)
    summary["unreachable"] = sum(1 for r in records if r["skip"] == "нет телеграма")
    summary["already_offered"] = sum(
        1 for r in records if r["skip"].startswith("предложение уже выдавали")
    )
    summary["candidates"] = sum(1 for r in records if not r["skip"])
    # Почему остальные не попали. Без этого холостой прогон при нуле кандидатов
    # отвечает «осмотрено 7, к возврату 0» и молчит о причине: образцы
    # показываются только тем, кому бы ушло, а два счётчика выше покрывают две
    # причины из семи. Новые причины («доставить нельзя», «не доставлено N раз»)
    # иначе исчезали бы вовсе, и владелец видел бы, что человек просто пропал.
    skipped: dict[str, int] = {}
    for record in records:
        if record["skip"]:
            group = _group(record["skip"])
            skipped[group] = skipped.get(group, 0) + 1
    summary["skipped"] = dict(sorted(skipped.items(), key=lambda pair: -pair[1]))

    percent = int(config["percent"])
    hours = int(config["lifetime_hours"])
    seen = 0       # сколько строк показали в холостом прогоне
    attempts = 0   # сколько РАЗ обращались к боту ради выдачи (не сколько удалось)
    for record in records:
        forced = only_user_id is not None
        if record["skip"] and not forced:
            continue
        if limit is not None and seen >= limit:
            summary["truncated"] = True
            break
        seen += 1

        if dry_run:
            summary["previews"].append(
                _preview(
                    record,
                    percent=percent,
                    # Настоящий код здесь не выпускается сознательно: превью не
                    # должно раздавать скидки.
                    text=_text(
                        record["language"],
                        percent,
                        "X" * _CODE_BODY,
                        now + timedelta(hours=hours),
                        tz,
                    ),
                )
            )
            continue
        if record["skip"]:
            continue
        # Считаем ПОПЫТКИ, а не удачи. Неудачная попытка — это три обращения к
        # боту (выпуск, отправка, отзыв), и пропускать её мимо потолка значило бы
        # пройти всю базу разом, без единой паузы, ровно в тот час, когда боту
        # плохо.
        if attempts >= _MAX_PER_RUN:
            summary["truncated"] = True
            _log(
                f"за прогон сделано {_MAX_PER_RUN} попыток — остальные уйдут "
                "следующим заходом (отметки проставлены, повторов не будет)"
            )
            break
        attempts += 1

        # Порядок строгий: сначала код, потом письмо, и не дошло — код убираем.
        try:
            issued = await _issue_code(ctx, percent, hours)
        except RuntimeError as exc:
            # Отказ в выпуске — это про ВЕСЬ прогон, а не про одного человека
            # (нет прав, кривой ключ, бот лежит). Раньше здесь стоял `continue`,
            # и одна общая беда превращалась в столько же одинаковых строк в
            # логе, сколько нашлось кандидатов. Останавливаемся и говорим причину
            # один раз; людям при этом ничего не записываем — их вины в этом нет,
            # в следующий заход они снова кандидаты.
            summary["stopped"] = str(exc)
            _log(f"прогон остановлен: {exc}")
            break

        text = _text(
            record["language"], percent, issued["code"], _stamp(issued["valid_until"]), tz
        )
        try:
            ok, reason, code = await bot_session.send(ctx, record["user_id"], text)
        except Exception:  # noqa: BLE001 — вернём код и понесём беду дальше
            # Отправка умеет не только «не дошло», но и упасть: служебный вход
            # перестал пускать (пароль сменили) — это RuntimeError, а не ответ
            # бота. Прогон на этом кончится, и без отзыва здесь только что
            # выпущенный код остался бы висеть у них навсегда — никому не
            # вручённый и ни на что не годный. Отмену прогона (остановка
            # адаптера) сюда сознательно не ловим: сеть в этот момент уже
            # обрывают, отозвать всё равно не выйдет — а такой код сам погаснет
            # по `valid_until`.
            await _revoke_code(ctx, issued["id"])
            raise
        if not ok:
            summary["failed"] += 1
            # Код без адресата не нужен никому, кроме сборщиков чужих промокодов.
            await _revoke_code(ctx, issued["id"])
            # Отметку «выдано» на неудаче ставить нельзя — человек не получил
            # ничего. Но и забывать попытку нельзя: заход повторяется каждый час
            # всё окно поимки, и без памяти о неудаче на одного заблокировавшего
            # бота уходят сотни циклов «выпустили — не дошло — удалили».
            forever = code in _NEVER_AGAIN
            _mark_failure(ctx.state, marks, record["mark_key"], now, forever)
            if forever:
                summary["undeliverable"] += 1
            _log(
                f"user_id={record['user_id']}: не доставлено — {reason}"
                + (" (больше не пробуем)" if forever else "")
            )
            # Пауза нужна и здесь: боту от неудачной попытки не легче, чем от
            # удачной, — обращений даже больше.
            await asyncio.sleep(_SEND_PAUSE)
            continue

        summary["sent"] += 1
        # Отметку пишем сразу: контейнер перезапускают при каждом обновлении, и
        # сохранение «в конце прогона» означало бы второе предложение тем, кому
        # уже написали. Перечитывать marks не нужно — этот словарь наш на прогон,
        # а параллельного прогона у задачи не бывает (scheduler не накладывает
        # запуск на запуск).
        marks[record["mark_key"]] = now.strftime("%Y-%m-%d")
        _save_marks(ctx.state, marks)
        await asyncio.sleep(_SEND_PAUSE)

    summary["attempts"] = attempts
    return summary


@task("winback", hours=1, run_at_start=False, timeout=1800)
async def winback(ctx: Ctx) -> None:
    """Фоновая выдача win-back.

    Раз в час, как и у нашего бэкенда: окно поимки две недели, спешить некуда, а
    редкий заход дешевле для бота. Почти всегда задача уходит сразу — проверка
    тумблера стоит одного чтения своей SQLite, без единого запроса наружу.
    `run_at_start=False` обязателен: иначе каждое обновление контейнера начиналось
    бы с раздачи скидок.
    """
    config = _config(ctx.state)
    if not config["enabled"]:
        return

    if not _delivery_configured():
        # Включили раньше, а доступ для отправки пропал (или его и не было).
        # Молча ничего не делать нельзя: раз в час пишем, чего не хватает.
        _log(f"выдача остановлена. {bot_session.NO_DELIVERY}")
        return

    tz, tz_name = await _bot_timezone(ctx)
    now_local = datetime.now(tz)
    if _is_quiet(now_local):
        # Не пропуск, а перенос: человек останется в окне поимки ещё много часов.
        return

    summary = await run_once(ctx, config)
    if summary["candidates"] or summary["failed"]:
        # Молчим, когда возвращать некого: строка «нашли 0» раз в час — это шум,
        # в котором потом не найти настоящую беду.
        _log(
            "осмотрено {examined}, к возврату {candidates}, выдано {sent}, "
            "без телеграма {unreachable}, уже предлагали {already_offered}, "
            "не удалось {failed} (из них навсегда {undeliverable}) "
            "(пояс {tz})".format(**summary, tz=tz_name)
        )


# --- ручки ----------------------------------------------------------------------


@handler("GET", "/api/admin/winback")
async def winback_config(ctx: Ctx) -> dict[str, Any]:
    await _require_admin(ctx)
    return _config(ctx.state)


@handler("PUT", "/api/admin/winback")
async def winback_save(ctx: Ctx) -> dict[str, Any]:
    """Сохранение. Меняем только присланное — экран шлёт частичное обновление.

    Включить, когда выдать или доставить нечем, нельзя: тумблер «включено» при
    молчащей рассылке — ложь в админке, которую замечают через месяц по вопросу
    «а где обещанные возвраты». Отказ приходит ДО записи, и сохранение не проходит
    целиком: «часть настроек применилась, часть нет» после ошибки — худший из
    возможных исходов. Проценты и сроки настраиваются обычным сохранением с
    выключенным тумблером.
    """
    await _require_admin(ctx)
    await _require_send_right(ctx)
    if ctx.state is None or not ctx.state.available:
        raise NotAvailable(
            "Настройку некуда сохранить: у адаптера нет доступа к своему хранилищу"
        )

    body = ctx.payload()
    enabled = body.get("enabled")
    if enabled is not None and bool(enabled):
        if not _delivery_configured():
            raise NotAvailable(bot_session.NO_DELIVERY)
        if not ctx.can_admin:
            raise NotAvailable(
                "Win-back включить нечем: без машинного ключа бота "
                "(BEDOLAGA_ADMIN_TOKEN) адаптер не видит истёкших и не может "
                "выпустить промокод на скидку."
            )

    config = _config(ctx.state)
    for field, low, high in (
        ("percent", _PERCENT_MIN, _PERCENT_MAX),
        ("days_after", _DAYS_MIN, _DAYS_MAX),
        ("lifetime_hours", _HOURS_MIN, _HOURS_MAX),
    ):
        if body.get(field) is not None:
            config[field] = _clamp(body[field], int(config[field]), low, high)
    if enabled is not None:
        config["enabled"] = bool(enabled)

    ctx.state.set_setting(_KEY, config)
    return config


@handler("POST", "/api/admin/winback/dry-run", deadline=120.0)
async def winback_dry_run(ctx: Ctx) -> dict[str, Any]:
    """Холостой прогон: кого позовём назад и каким текстом — без выдачи и отправки.

    Нужен потому, что проверить такую рассылку иначе можно единственным способом —
    раздать живым людям настоящие скидки. Здесь не выпускается ни одного
    промокода, не отправляется ни одного сообщения и не ставится ни одной
    отметки, поэтому звать её безопасно хоть подряд. Выключенный тумблер её не
    блокирует: посмотреть, кого зацепит, до включения — ровно то, ради чего она и
    нужна.

    `user_id` (в теле или в query) — посмотреть одного человека вместе с причиной
    отказа. Без него берётся первая горстка (_DRY_LIMIT).
    """
    await _require_admin(ctx)
    raw = ctx.payload().get("user_id", ctx.query.get("user_id"))
    only: int | None = None
    if raw not in (None, ""):
        try:
            only = int(raw)
        except (TypeError, ValueError):
            raise _fail(400, "user_id должен быть числом") from None

    config = _config(ctx.state)
    summary = await run_once(
        ctx, config, dry_run=True, only_user_id=only, limit=None if only else _DRY_LIMIT
    )
    summary["config"] = config
    summary["catch_days"] = _CATCH_DAYS
    summary["quiet_hours"] = f"{_QUIET_FROM}:00–{_QUIET_TO}:00"
    summary["delivery_configured"] = _delivery_configured()
    summary["can_issue_discount"] = ctx.can_admin
    if not summary["delivery_configured"]:
        summary["delivery_note"] = bot_session.NO_DELIVERY
    if not summary["can_issue_discount"]:
        summary["discount_note"] = (
            "Без машинного ключа бота (BEDOLAGA_ADMIN_TOKEN) промокод на скидку "
            "выпустить нечем."
        )
    return summary
