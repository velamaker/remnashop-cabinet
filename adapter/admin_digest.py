"""Месячный дайджест: раз в месяц человеку уходит его сводка — сколько трафика
израсходовал за 30 дней и какой сервер оказался любимым.

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ — общее правило адаптера: раздел = файл `admin_*.py`,
загрузчик в `compose.py` подхватывает их по маске, а в образ файл попадает
строкой `COPY *.py` в Dockerfile. Ни compose.py, ни main.py трогать не нужно:
страница `/admin/digest` уже описана в `ADMIN_PAGE_REQUIREMENTS` и появляется в
меню ровно тогда, когда здесь зарегистрирован GET `/api/admin/digest`. Форма
конфига (`enabled`, `day_of_month`, `hour`) взята с экрана кабинета — `DigestCard`
в AdminSettingsPage.tsx шлёт ровно эти три поля и ждёт их же в ответе.

ЧЬЯ ЭТО ФУНКЦИЯ. Настройка — свойство КАБИНЕТА: у «Бедолаги» месячной сводки нет,
хранить её негде, кроме своей памяти (`state.py`). Цифры — данные ПАНЕЛИ
Remnawave: разбивка по нодам живёт только там (`/api/bandwidth-stats/users/{uuid}`),
у бота её нет ни в каком виде. Подход к панели тот же, что у обработчиков
`/api/subscription/server-stats` и `/api/subscription/traffic-history` в compose.py:
одно окно в 30 дней, `topNodes` для «любимого», `sparklineData` для итога.

ПОЧЕМУ НЕ sum(topNodes). Соблазн взять итог суммой `topNodes` (так делает наш
бэкенд) — занижает: список урезан лимитом `topNodesLimit`, и у человека с шестью
нодами шестая в сумму не попадёт. `sparklineData` — расход по дням по ВСЕМ нодам,
и он поэлементно сходится с суммой `series` (сверено на живой панели, см.
комментарий в compose.traffic_history). Поэтому итог берём оттуда, а `topNodes` —
только ради имени любимого сервера.

ПОЧЕМУ ПЕРСОНАЛЬНОЕ СООБЩЕНИЕ, А НЕ РАССЫЛКА. У них есть оба входа:
  • `POST /broadcasts` (машинное API, X-API-Key) — рассылка по СЕГМЕНТУ
    (`all`/`active`/`trial`/`expiring`…, список закрытый — проверено по
    `app/webapi/schemas/broadcasts.py`). Списка людей она не принимает, текст у
    всех один. Для дайджеста это бессмысленно: вся его суть — ЛИЧНЫЕ цифры,
    «вы использовали N ГБ, любимый сервер — такой-то»;
  • `POST /cabinet/admin/users/{user_id}/send-message` — ровно один человек и наш
    текст целиком (HTML, до 4096 символов, `parse_mode='HTML'` — проверено в их
    `app/cabinet/routes/admin_users.py`). Это и берём.
Цена выбора: их кабинетные ручки закрыты СЕССИЕЙ администратора (JWT), машинный
ключ они не принимают. За фоновой задачей администратор не стоит, поэтому адаптеру
нужен свой вход: `BEDOLAGA_CABINET_EMAIL` / `BEDOLAGA_CABINET_PASSWORD` (служебный
админ бота с правом `users:send_message`). Не заданы — доставки нет, и это видно
честно: включить дайджест нельзя (501 с причиной), а если его включили раньше,
задача раз в месяц пишет в лог, кому написала БЫ, и никому не пишет.

КАНАЛ ТОЛЬКО ОДИН. Наш бэкенд шлёт сводку и в Telegram, и в Web Push. Здесь push
нет и взяться ему неоткуда — подписками браузера владеет наш бэкенд, а не бот.
Поэтому человек без телеграма (в кабинете регистрируются почтой) для дайджеста
недостижим: считаем его отдельно и не тратим на него запрос к панели.

ДЕДУПЛИКАЦИЯ — ДВА УРОВНЯ, и оба обязательны:
  • по месяцу: «в этом месяце уже разослали» (`done`) — иначе каждый тик в окне
    рассылки начинал бы её заново;
  • по человеку внутри месяца: id пишется в память СРАЗУ после успешной отправки.
    Перезапуск контейнера посреди рассылки (а его перезапускают при каждом
    обновлении) не должен обернуться вторым письмом тем, кому уже написали, — и
    при этом не должен лишить сводки тех, до кого дойти не успели.
"""

from __future__ import annotations

import asyncio
import html
import os
import time
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatchcase
from typing import Any

import httpx

import bot_session
from compose import Ctx, NotAvailable, UpstreamError, handler
from scheduler import task

# --- что и где храним ---------------------------------------------------------

# Ключи в своей памяти (`state.settings`). Новых таблиц не заводим: файл базы
# общий, его правят соседние разделы.
_KEY = "digest"
_MARK_KEY = "digest_sent"

# Дефолт совпадает с нашим бэкендом (`overlay_digest.py`): выключено, 1-го числа
# в 10:00 UTC. Рассылка, включённая сама собой при обновлении, — это письма людям,
# которых никто не заказывал, поэтому дефолт может быть только «выключено».
_DEFAULT: dict[str, Any] = {"enabled": False, "day_of_month": 1, "hour": 10}

# Границы те же, что у формы кабинета (DigestCard: 1–28 и 0–23). Верхняя граница
# дня именно 28, а не 31: 30-го числа рассылка молча пропускала бы февраль.
_DAY_MIN, _DAY_MAX = 1, 28
_HOUR_MIN, _HOUR_MAX = 0, 23

# --- окно и пределы прогона ----------------------------------------------------

# Насколько долго после назначенного часа рассылка ещё готова состояться.
# Строгое равенство часу («ровно в 10:00») выглядит аккуратнее, но контейнер
# перезапускают при каждом обновлении: попади обновление в этот час — сводка
# пропала бы на месяц. Окно ограничено, чтобы «дайджест в 10 утра» не приходил
# вечером, а тумблер, включённый днём, не выстреливал рассылкой немедленно.
_CATCH_UP_HOURS = 3

# Период сводки. 30 дней, а не «календарный месяц»: у панели окно задаётся датами,
# и одинаковая длина периода честнее сравнивается от месяца к месяцу.
_PERIOD_DAYS = 30
# Панель отдаёт столько лучших нод; больше для «любимого сервера» и не нужно.
_TOP_NODES = 5

_GB = 1024 ** 3

# Пауза между сообщениями. Телеграм пропускает у бота около 30 сообщений в
# секунду и на превышении отвечает 429 всему боту сразу — то есть спешка нашей
# рассылки ударила бы по чужим ответам бота, а не только по ней.
_SEND_PAUSE = 0.1
# Пауза между запросами к панели: у дайджеста запрос НА КАЖДОГО человека (разбивку
# по нодам иначе не получить), и очередь из них не должна мешать самой панели
# обслуживать подключения.
_PANEL_PAUSE = 0.05
# Потолок на один прогон: остаток уйдёт следующим тиком в том же окне (отметки
# проставлены, повторов не будет). Без потолка первый же запуск на большой базе
# не уложился бы в предел времени задачи и был бы брошен посередине.
_MAX_PER_RUN = 300
# Сколько людей смотрит холостой прогон из админки, когда конкретный id не назван.
# Он идёт внутри HTTP-запроса, и человек за экраном не будет ждать полного обхода
# базы; для «посмотреть, что получится» хватает первых.
_DRY_LIMIT = 25

_PANEL_PAGE = 500          # сколько пользователей панели берём за запрос
_BOT_PAGE = 200            # у их `/users` limit ≤ 200 (жёстко, по их схеме)
_PANEL_TIMEOUT = 30.0
_PAGES_LIMIT = 200         # предохранитель от бесконечного обхода страниц

# Их право на личное сообщение (`app/cabinet/routes/admin_users.py`:
# `require_permission('users:send_message')`).
_SEND_RIGHT = "users:send_message"

# Служебный вход адаптера. Читаем окружение прямо здесь: main.py — общий файл, а
# переменная нужна только разделам, которые пишут людям.
_SERVICE_EMAIL = os.environ.get("BEDOLAGA_CABINET_EMAIL", "").strip()
_SERVICE_PASSWORD = os.environ.get("BEDOLAGA_CABINET_PASSWORD", "")

# Их access живёт минуты (у «Бедолаги» ~15), поэтому держим его в памяти процесса
# и обновляем по сроку. На диск не кладём сознательно: это ключ от админки, а
# перелогин стоит один запрос. Свой на модуль, а не общий с соседним разделом:
# соседа правят параллельно, и лезть в его приватные имена — значит сломаться от
# переименования в чужом файле.
_session: dict[str, Any] = {"token": "", "until": 0.0}

# Слово «VPN» в тексте нарочно не звучит: адаптер работает поверх ЧУЖОГО бота, и
# как называется сервис — решает его владелец в своём оформлении, а не мы.
_MSG = {
    "ru": ("📊 Ваш месяц", "За месяц вы использовали {gb} ГБ.{fav} Спасибо, что с нами!"),
    "en": ("📊 Your month", "This month you used {gb} GB.{fav} Thanks for being with us!"),
}
_FAV = {"ru": " Любимый сервер — {name}.", "en": " Favorite server — {name}."}


def _log(message: str) -> None:
    # Тот же префикс, что у остального адаптера: лог контейнера общий.
    print(f"[adapter] дайджест: {message}", flush=True)


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
    """Форма ответа ровно такая, какую ждёт экран кабинета (DigestConfig):
    три поля, ничего лишнего."""
    data = raw if isinstance(raw, dict) else {}
    return {
        "enabled": bool(data.get("enabled", _DEFAULT["enabled"])),
        "day_of_month": _clamp(
            data.get("day_of_month"), int(_DEFAULT["day_of_month"]), _DAY_MIN, _DAY_MAX
        ),
        "hour": _clamp(data.get("hour"), int(_DEFAULT["hour"]), _HOUR_MIN, _HOUR_MAX),
    }


def _config(state: Any) -> dict[str, Any]:
    if state is None or not getattr(state, "available", False):
        # Хранить настройку негде — значит её нет, а «нет» здесь равно
        # «выключено»: врать тумблером про работающую рассылку нельзя.
        return dict(_DEFAULT)
    return _normalize(state.get_setting(_KEY, {}))


# --- память о разосланном ------------------------------------------------------


def _empty_marks(month: str) -> dict[str, Any]:
    return {"month": month, "users": [], "done": False, "dry": False}


def _marks(state: Any, month: str) -> dict[str, Any]:
    """Отметки текущего месяца. Прошлый месяц не храним: он больше ни на что не
    влияет, а список id разросся бы навсегда."""
    if state is None or not getattr(state, "available", False):
        return _empty_marks(month)
    raw = state.get_setting(_MARK_KEY, {})
    if not isinstance(raw, dict) or str(raw.get("month") or "") != month:
        return _empty_marks(month)
    users: list[int] = []
    for value in raw.get("users") or []:
        try:
            users.append(int(value))
        except (TypeError, ValueError):
            continue
    return {
        "month": month,
        "users": users,
        "done": bool(raw.get("done")),
        "dry": bool(raw.get("dry")),
    }


def _save_marks(state: Any, marks: dict[str, Any]) -> None:
    if state is None or not getattr(state, "available", False):
        return
    state.set_setting(_MARK_KEY, marks)


# --- права --------------------------------------------------------------------


async def _require_admin(ctx: Ctx) -> None:
    """Читать настройку может администратор бота.

    Эта ручка не ходит в их API, а читает своё хранилище, поэтому спросить права
    обязана явно: иначе настройку кабинета видел бы любой, кто знает путь.
    """
    if not ctx.token:
        raise _fail(401, "Требуется вход")
    me = await ctx.json("/cabinet/auth/me/is-admin", {}, soft=True) or {}
    if not me.get("is_admin"):
        raise _fail(403, "Недостаточно прав")


async def _require_send_right(ctx: Ctx) -> None:
    """Менять настройку может тот, кому их RBAC разрешает писать людям.

    Сохранение здесь — это не «поправить текст на экране», а включение рассылки
    личных сообщений от имени их бота. Право на неё у них уже есть
    (`users:send_message`), поэтому спрашиваем его, а не выдумываем своё: админ
    «только для просмотра» не должен уметь разослать сообщение всей базе.
    Сравнение по их правилу — право пользователя это ШАБЛОН (`*:*`, `users:*`).
    Не смогли спросить — считаем, что права нет.
    """
    data = await ctx.json("/cabinet/auth/me/permissions", {}, soft=True) or {}
    granted = data.get("permissions")
    ok = isinstance(granted, list) and any(
        isinstance(p, str) and fnmatchcase(_SEND_RIGHT, p) for p in granted
    )
    if not ok:
        raise _fail(403, f"Недостаточно прав в боте: нужно право «{_SEND_RIGHT}»")


# --- доставка -----------------------------------------------------------------


def _delivery_configured() -> bool:
    # Общий служебный вход: см. bot_session.py — там же и причина, почему он нужен.
    return bot_session.configured()


_NO_DELIVERY = (
    "Дайджест некому доставить: личное сообщение у «Бедолаги» открывается только "
    "сессией администратора (машинный ключ X-API-Key их кабинетное API не "
    "принимает), а за фоновой задачей администратор не стоит. Заведите в боте "
    "служебного админа с правом «users:send_message» и передайте адаптеру "
    "BEDOLAGA_CABINET_EMAIL и BEDOLAGA_CABINET_PASSWORD."
)


async def _service_token(ctx: Ctx, renew: bool = False) -> str:
    return await bot_session.token(ctx, renew)


def _detail(resp: httpx.Response) -> str:
    """Причина отказа словами. У них detail бывает и строкой, и объектом с
    `code`/`message` (например, `no_telegram_id` — человек зарегистрирован только
    по почте, телеграма у него нет вовсе)."""
    try:
        body = resp.json()
    except ValueError:
        body = None
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("code") or resp.status_code)
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    return f"код {resp.status_code}"


async def _send_message(ctx: Ctx, user_id: int, text: str) -> tuple[bool, str]:
    """Личное сообщение одному человеку. (успех, причина отказа человеческими словами).

    Отказ бота НЕ глотаем и не превращаем в успех: «дошло» и «не дошло» решают,
    ставить ли отметку. Поставить её на неудаче — значит лишить человека сводки
    до следующего месяца.
    """
    for attempt in (1, 2):
        token = await _service_token(ctx, renew=attempt == 2)
        try:
            resp = await ctx.call(
                "POST",
                f"/cabinet/admin/users/{int(user_id)}/send-message",
                json={"text": text},
                headers={"authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            # Сетевая беда с ОДНИМ сообщением не должна ронять весь прогон:
            # остальным написать можно, а этот попадёт в следующий заход
            # (отметку на неудаче не ставим).
            return False, f"бот недоступен: {exc}"
        # Сессия протухла (их access живёт минуты) — второй заход уже с новым
        # токеном; повторять бесконечно нельзя, это была бы петля на 401.
        if resp.status_code == 401 and attempt == 1:
            continue
        if resp.status_code < 400:
            return True, ""
        return False, _detail(resp)
    return False, "неизвестная ошибка"


# --- откуда берём людей и цифры ------------------------------------------------


def _short_uuid(url: Any) -> str:
    """Хвост ссылки подписки — он же `shortUuid` в панели. По нему и связываем
    человека бота с записью панели: своего `remnawave_uuid` «Бедолага» наружу не
    отдаёт ни одной ручкой, а ссылку подписки отдаёт всегда."""
    tail = str(url or "").split("?")[0].split("#")[0].rstrip("/")
    return tail.rsplit("/", 1)[-1] if "/" in tail else ""


async def _bot_users(ctx: Ctx) -> list[dict[str, Any]]:
    """Люди бота: кому вообще можно писать и под каким id.

    Берём машинным ключом (`ctx.admin`) — это единственный доступ, который есть у
    фоновой задачи. Ошибку не проглатываем: пустой список молча означал бы «никому
    сводка не нужна», а это неправда.

    Конец списка определяем ТОЛЬКО по короткой странице. Поле `total` в их ответе
    доверия не заслуживает: на живом стенде оно равно 1 при семи пользователях
    (проверено запросом), и остановка по нему обрезала бы рассылку после первой
    страницы.
    """
    if not ctx.can_admin:
        raise RuntimeError(
            "адаптеру не выдан машинный ключ бота (BEDOLAGA_ADMIN_TOKEN) — "
            "список пользователей взять неоткуда"
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


async def _panel_index(ctx: Ctx) -> dict[str, dict[str, Any]]:
    """Записи панели, разложенные по `shortUuid`: uuid (по нему берётся расход) и
    статус подписки.

    `ctx.panel_json` любую беду превращает в default, поэтому пустой ответ на
    ПЕРВОЙ странице разбираем отдельно: «панель не ответила» и «в панели нет
    пользователей» — разные вещи, и первое обязано быть видимой ошибкой прогона,
    а не тихим «никому слать нечего».
    """
    if not ctx.has_panel:
        raise RuntimeError(
            "панель Remnawave адаптеру не задана (REMNAWAVE_URL/REMNAWAVE_TOKEN) — "
            "расход трафика брать неоткуда"
        )
    found: dict[str, dict[str, Any]] = {}
    start = 0
    for page in range(_PAGES_LIMIT):
        data = await ctx.panel_json(
            "/api/users",
            None,
            params={"size": _PANEL_PAGE, "start": start},
            timeout=_PANEL_TIMEOUT,
        )
        if data is None:
            if page == 0:
                raise RuntimeError("панель Remnawave не отдала список пользователей")
            break
        rows = data.get("users") if isinstance(data, dict) else None
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = str(row.get("shortUuid") or "")
            if key:
                found[key] = {
                    "uuid": str(row.get("uuid") or ""),
                    "status": str(row.get("status") or "").upper(),
                }
        start += len(rows)
        if len(rows) < _PANEL_PAGE:
            break
    return found


def _total_bytes(data: dict[str, Any]) -> int:
    """Итог за период по ВСЕМ нодам.

    Не сумма `topNodes`: этот список урезан `topNodesLimit`, и у человека с шестью
    нодами шестая в итог не попала бы. `sparklineData` — расход по дням, сложённый
    панелью по всем нодам сразу; ниже по списку идут запасные варианты на случай,
    если версия панели отдаст не всё.
    """
    spark = data.get("sparklineData")
    if isinstance(spark, list) and spark:
        return sum(int(value or 0) for value in spark)
    series = data.get("series")
    if isinstance(series, list) and series:
        return sum(
            int(value or 0)
            for row in series
            if isinstance(row, dict)
            for value in (row.get("data") or [])
        )
    top = data.get("topNodes")
    if isinstance(top, list):
        return sum(int(row.get("total") or 0) for row in top if isinstance(row, dict))
    return 0


def _favorite(data: dict[str, Any]) -> str:
    """Имя ноды с наибольшим расходом. Пусто — про любимый сервер молчим, а не
    выдумываем «нет данных» в тексте человеку."""
    rows = data.get("topNodes") or data.get("series") or []
    best, best_total = "", 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            total = int(row.get("total") or 0)
        except (TypeError, ValueError):
            continue
        if total > best_total:
            best, best_total = str(row.get("name") or ""), total
    return best


async def _usage(ctx: Ctx, uuid: str) -> tuple[int, str] | None:
    """Расход за период и любимый сервер. None — панель не ответила.

    Отличать «панель промолчала» от «трафика ноль» обязательно: во втором случае
    человека надо пропустить, а в первом — попробовать ещё раз следующим тиком.
    Списать сбой панели на «трафика нет» значит потерять сводку на месяц.
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=_PERIOD_DAYS)
    data = await ctx.panel_json(
        f"/api/bandwidth-stats/users/{uuid}",
        None,
        # Панель принимает только дату, без времени.
        params={
            "topNodesLimit": _TOP_NODES,
            "start": start.strftime("%Y-%m-%d"),
            "end": end.strftime("%Y-%m-%d"),
        },
        timeout=_PANEL_TIMEOUT,
    )
    if not isinstance(data, dict):
        return None
    return _total_bytes(data), _favorite(data)


def _examine(
    users: list[dict[str, Any]], panel: dict[str, dict[str, Any]], sent: set[int]
) -> list[dict[str, Any]]:
    """Вердикт по каждому человеку ДО обращения к панели за его расходом.

    Чистая функция: сюда приходят готовые списки, отсюда уходит решение — так его
    видно на глаз и можно проверить без единой отправки и без единого запроса.
    Причина отказа не выбрасывается, а едет дальше: холостой прогон показывает
    владельцу, почему конкретный человек остался без сводки.
    """
    out: list[dict[str, Any]] = []
    for user in users:
        try:
            uid = int(user.get("id"))
        except (TypeError, ValueError):
            continue
        record: dict[str, Any] = {
            "user_id": uid,
            "telegram_id": user.get("telegram_id"),
            "language": str(user.get("language") or "ru")[:2].lower(),
            "uuid": "",
            "skip": "",
        }
        out.append(record)

        subscription = user.get("subscription")
        entry = (
            panel.get(_short_uuid(subscription.get("subscription_url")))
            if isinstance(subscription, dict)
            else None
        )
        if str(user.get("status") or "").lower() != "active":
            # Заблокированным и удалённым тёплая сводка «спасибо, что с нами» —
            # издевательство, а не забота.
            record["skip"] = "аккаунт не активен"
        elif not isinstance(subscription, dict):
            record["skip"] = "нет подписки"
        elif entry is None:
            record["skip"] = "нет записи в панели"
        elif entry["status"] != "ACTIVE":
            record["skip"] = "подписка в панели не активна"
        elif not entry["uuid"]:
            record["skip"] = "в панели нет uuid"
        elif not user.get("telegram_id"):
            # Канал у дайджеста один — телеграм бота. Web Push тут нет и взяться
            # ему неоткуда, поэтому человек с одной лишь почтой недостижим.
            record["skip"] = "нет телеграма"
        elif uid in sent:
            record["skip"] = "уже отправляли в этом месяце"
        if entry is not None:
            record["uuid"] = entry["uuid"]
    return out


def _preview(record: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """Строка холостого прогона про одного человека.

    Причины отказа складываем, а не заменяем: у человека их бывает сразу две
    («нет телеграма» и «нет трафика»), и показать только последнюю значит
    отправить владельца чинить не то.
    """
    reasons = [r for r in (record["skip"], extra.pop("skip", "")) if r]
    return {
        "user_id": record["user_id"],
        "telegram_id": record["telegram_id"],
        "language": record["language"],
        "would_send": not reasons,
        "skip": "; ".join(reasons) or None,
        **extra,
    }


def _text(language: str, gb: float, favorite: str) -> str:
    """Готовое сообщение. Имя ноды экранируем: бот шлёт текст с parse_mode='HTML',
    и нода, названная «<FIN>», обрушила бы отправку на разборе разметки."""
    title, body = _MSG.get(language, _MSG["ru"])
    tail = ""
    if favorite:
        tail = _FAV.get(language, _FAV["ru"]).format(name=html.escape(favorite))
    return f"<b>{title}</b>\n\n" + body.format(gb=gb, fav=tail)


# --- сам прогон ---------------------------------------------------------------


async def run_once(
    ctx: Ctx,
    month: str,
    dry_run: bool = False,
    only_user_id: int | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Один проход рассылки. Возвращает сводку для лога и для экрана админки.

    `dry_run=True` — холостой прогон: считаем, собираем текст, показываем его и не
    пишем ни людям, ни отметок. Тем же путём идёт задача, когда доставка не
    настроена: молча ничего не делать — худший вариант, а честная строка в логе
    «отправили бы N сводок» показывает, что задача жива и чего ей не хватает.

    `only_user_id` — посмотреть одного человека. Для него расход спрашиваем даже
    при отказе (нет телеграма, уже отправляли): владелец просит показать текст, а
    не объяснение, почему текста не будет.
    """
    state = ctx.state
    summary: dict[str, Any] = {
        "month": month,
        "dry_run": dry_run,
        "examined": 0,
        "candidates": 0,
        "sent": 0,
        "no_traffic": 0,
        "unreachable": 0,
        "panel_silent": 0,
        "failed": 0,
        "truncated": False,
        "previews": [],
    }

    users = await _bot_users(ctx)
    panel = await _panel_index(ctx)
    marks = _marks(state, month)
    sent_ids = set(marks["users"])

    records = _examine(users, panel, sent_ids)
    if only_user_id is not None:
        records = [r for r in records if r["user_id"] == only_user_id]
        if not records:
            raise _fail(404, f"Пользователь id={only_user_id} у бота не найден")
    summary["examined"] = len(records)
    summary["unreachable"] = sum(1 for r in records if r["skip"] == "нет телеграма")
    summary["candidates"] = sum(1 for r in records if not r["skip"])

    seen = 0
    for record in records:
        forced = only_user_id is not None
        if record["skip"] and not forced:
            continue
        if not record["uuid"]:
            # Без записи в панели расход спрашивать не у кого — показываем причину.
            if forced:
                summary["previews"].append(_preview(record, skip="нет записи в панели"))
            continue
        if not dry_run and summary["sent"] >= _MAX_PER_RUN:
            summary["truncated"] = True
            _log(
                f"за прогон отправлено {_MAX_PER_RUN} сводок — остальные уйдут "
                "следующим заходом (отметки проставлены, повторов не будет)"
            )
            break
        if limit is not None and seen >= limit:
            summary["truncated"] = True
            break
        seen += 1

        usage = await _usage(ctx, record["uuid"])
        await asyncio.sleep(_PANEL_PAUSE)
        if usage is None:
            # Панель промолчала: отметку не ставим, человек попадёт в следующий
            # заход этого же окна.
            summary["panel_silent"] += 1
            continue
        total, favorite = usage
        gb = round(total / _GB, 1)
        text = _text(record["language"], gb, favorite)

        if total <= 0:
            # Пустая сводка «вы использовали 0 ГБ» — не забота, а спам. Отметку не
            # ставим: сегодня трафика нет, но следующий месяц считается заново.
            summary["no_traffic"] += 1
            if forced and dry_run:
                summary["previews"].append(
                    _preview(
                        record,
                        skip="нет трафика за период",
                        gb=gb,
                        favorite=favorite or None,
                        text=text,
                    )
                )
            continue

        if dry_run:
            summary["previews"].append(
                _preview(record, gb=gb, favorite=favorite or None, text=text)
            )
            continue
        if record["skip"]:
            continue

        ok, reason = await _send_message(ctx, record["user_id"], text)
        if not ok:
            summary["failed"] += 1
            _log(f"user_id={record['user_id']}: не доставлено — {reason}")
            continue
        summary["sent"] += 1
        marks["users"].append(record["user_id"])
        # Отметку пишем сразу: контейнер перезапускают при каждом обновлении, и
        # сохранение «в конце прогона» означало бы вторую сводку всем, кому уже
        # написали.
        _save_marks(state, marks)
        await asyncio.sleep(_SEND_PAUSE)

    if not dry_run and not summary["truncated"] and not summary["panel_silent"]:
        # Месяц закрыт только когда обход дошёл до конца и никого не отложил:
        # иначе следующий тик в том же окне доберёт оставшихся.
        marks["done"] = True
        _save_marks(state, marks)
    return summary


@task("digest", minutes=15, run_at_start=False, timeout=1800)
async def digest(ctx: Ctx) -> None:
    """Фоновая рассылка месячной сводки.

    Просыпается каждые 15 минут и почти всегда сразу уходит: проверка дня, часа и
    отметки месяца стоит одного чтения своей SQLite, без единого запроса наружу.
    Четыре подхода в час, а не один, — чтобы пропущенный тик (перезапуск,
    затянувшийся предыдущий прогон) не стоил людям сводки. `run_at_start=False`
    обязателен: иначе свежая установка (или любое обновление контейнера в нужный
    час) начиналась бы с рассылки.
    """
    config = _config(ctx.state)
    if not config["enabled"]:
        return

    now = datetime.now(timezone.utc)
    if now.day != int(config["day_of_month"]):
        return
    if not int(config["hour"]) <= now.hour < int(config["hour"]) + _CATCH_UP_HOURS:
        return

    month = now.strftime("%Y-%m")
    marks = _marks(ctx.state, month)
    ready = _delivery_configured()
    if marks["done"] or (marks["dry"] and not ready):
        return

    if not ready:
        # Включили раньше, а доступ для отправки пропал (или его и не было).
        # Считаем вхолостую ОДИН раз за месяц и говорим прямо, чего не хватает.
        _log(f"доставка не настроена — идёт холостой прогон. {_NO_DELIVERY}")

    summary = await run_once(ctx, month, dry_run=not ready)
    _log(
        "{month}: осмотрено {examined}, к отправке {candidates}, отправлено {sent}, "
        "без трафика {no_traffic}, без телеграма {unreachable}, "
        "панель промолчала {panel_silent}, не доставлено {failed}{tail}".format(
            **summary, tail=" (холостой прогон)" if summary["dry_run"] else ""
        )
    )
    if not ready:
        # Отмечаем сам факт холостого отчёта, иначе он повторялся бы каждые 15
        # минут все три часа окна. Отметки перечитываем: за время прогона их мог
        # поправить он сам, и запись по устаревшей копии стёрла бы его работу.
        marks = _marks(ctx.state, month)
        marks["dry"] = True
        _save_marks(ctx.state, marks)


# --- ручки --------------------------------------------------------------------


@handler("GET", "/api/admin/digest")
async def digest_config(ctx: Ctx) -> dict[str, Any]:
    await _require_admin(ctx)
    return _config(ctx.state)


@handler("PUT", "/api/admin/digest")
async def digest_save(ctx: Ctx) -> dict[str, Any]:
    """Сохранение. Меняем только присланное — экран шлёт частичное обновление.

    Включить, когда сообщение доставить нечем, нельзя: тумблер «включено» при
    молчащей рассылке — это ложь в админке, которую замечают через месяц по
    жалобам «а где обещанная сводка». Отказ приходит ДО записи, и сохранение не
    проходит целиком: «часть настроек применилась, часть нет» после ошибки — худший
    из возможных исходов, потом ищи, что же в итоге записано. День и час
    настраиваются обычным сохранением с выключенным тумблером.
    """
    await _require_admin(ctx)
    await _require_send_right(ctx)
    if ctx.state is None or not ctx.state.available:
        raise NotAvailable(
            "Настройку некуда сохранить: у адаптера нет доступа к своему хранилищу"
        )

    body = ctx.payload()
    enabled = body.get("enabled")
    if enabled is not None and bool(enabled) and not _delivery_configured():
        raise NotAvailable(_NO_DELIVERY)

    config = _config(ctx.state)
    if body.get("day_of_month") is not None:
        config["day_of_month"] = _clamp(
            body["day_of_month"], int(config["day_of_month"]), _DAY_MIN, _DAY_MAX
        )
    if body.get("hour") is not None:
        config["hour"] = _clamp(body["hour"], int(config["hour"]), _HOUR_MIN, _HOUR_MAX)
    if enabled is not None:
        config["enabled"] = bool(enabled)

    ctx.state.set_setting(_KEY, config)
    return config


@handler("POST", "/api/admin/digest/dry-run", deadline=120.0)
async def digest_dry_run(ctx: Ctx) -> dict[str, Any]:
    """Холостой прогон: показать, что и кому ушло бы, ничего не отправляя.

    Нужен потому, что проверить рассылку иначе можно только одним способом —
    разослать её живым людям. Здесь не отправляется ни одного сообщения и не
    ставится ни одной отметки, поэтому вызывать её безопасно хоть подряд, а
    выключенный тумблер её не блокирует: посмотреть текст до включения — ровно
    то, ради чего она и нужна.

    `user_id` (в теле или в query) — посмотреть одного человека. Без него берётся
    первая горстка (_DRY_LIMIT): за ручкой стоит человек у экрана, а полный обход
    базы — это запрос к панели на каждого.
    """
    await _require_admin(ctx)
    raw = ctx.payload().get("user_id", ctx.query.get("user_id"))
    only: int | None = None
    if raw not in (None, ""):
        try:
            only = int(raw)
        except (TypeError, ValueError):
            raise _fail(400, "user_id должен быть числом") from None

    month = datetime.now(timezone.utc).strftime("%Y-%m")
    summary = await run_once(
        ctx, month, dry_run=True, only_user_id=only, limit=None if only else _DRY_LIMIT
    )
    summary["config"] = _config(ctx.state)
    summary["delivery_configured"] = _delivery_configured()
    if not summary["delivery_configured"]:
        summary["delivery_note"] = _NO_DELIVERY
    return summary
