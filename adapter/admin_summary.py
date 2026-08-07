"""Утренняя сводка ВЛАДЕЛЬЦУ: выручка за вчера, регистрации, активные, истекающие.

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ — общее правило адаптера: раздел = файл `admin_*.py`,
загрузчик в `compose.py` подхватывает их по маске, а в образ файл попадает
строкой `COPY *.py`. Ни compose.py, ни main.py править не нужно: страница
`/admin/summary` уже описана в `ADMIN_PAGE_REQUIREMENTS` и появляется в меню
ровно тогда, когда здесь зарегистрирован GET `/api/admin/morning-summary`.

ЧЬЯ ЭТО ФУНКЦИЯ. Сводка — свойство КАБИНЕТА: у «Бедолаги» такой настройки нет,
хранить её негде, кроме своей памяти (`state.py`). А вот ЦИФРЫ — их: считаем
ровно по тем же правилам, что их собственная статистика, иначе владелец увидит
в сводке одно, а в их админке другое и перестанет верить обеим.

КОМУ ОТПРАВЛЯТЬ (главный вопрос задачи). Разбирались так:
  • `ADMIN_IDS` (телеграмы админов) у них живёт в конфиге процесса и НЕ отдаётся
    ни одной ручкой — проверено: `GET /settings/ADMIN_IDS` → 404 «Setting not
    found», в списке `GET /settings` (813 ключей) его тоже нет;
  • RBAC (`/cabinet/admin/rbac/users`) знает «суперадминов», но их может быть
    несколько, и он не отвечает на вопрос «кто ВЛАДЕЛЕЦ»; к тому же ручка
    закрыта сессией администратора, а за фоновой задачей администратор не стоит;
  • зато через их же настройки читаются `ADMIN_REPORTS_CHAT_ID` и
    `ADMIN_NOTIFICATIONS_CHAT_ID` — это чат, куда САМ бот шлёт свой ежедневный
    отчёт и админские уведомления. Это лучшее машинно-читаемое приближение к
    «владельцу», но именно приближение: там может стоять группа/форум, а не
    человек, — и написать в группу личным сообщением нельзя.
Поэтому адресат — НАСТРОЙКА (`recipient_telegram_id`), а не догадка во время
отправки. Определяем его один раз, в момент включения сводки в админке, и
записываем в настройку явно (в GET видно кого и откуда взяли):
    1) что владелец задал руками;
    2) их чат отчётов/уведомлений — если он и правда сходится с пользователем
       бота (проверяем `GET /users/by-telegram-id/...`, группа отсеется сама);
    3) тот, кто включает сводку в админке (у него есть телеграм) — «включает
       владелец» это и есть правило проекта;
    4) ничего из этого не сошлось — честный отказ 501 с причиной, а не рассылка
       «наугад кому-нибудь из админов».
Фоновая задача НИЧЕГО не определяет: берёт записанный телеграм и всё. Пустой
адресат = холостой прогон с записью в лог.

ПОЧЕМУ ПЕРСОНАЛЬНОЕ СООБЩЕНИЕ, А НЕ РАССЫЛКА. `POST /broadcasts` шлёт по
СЕГМЕНТУ (all / active / trial / expiring…), список людей не принимает: сводкой
с выручкой ушла бы вся база. Нужен ровно один человек и наш текст целиком —
это `POST /cabinet/admin/users/{user_id}/send-message` (HTML, до 4096 символов).
Цена выбора: их кабинетные ручки закрыты СЕССИЕЙ администратора, машинный ключ
они не принимают (проверено: `X-API-Key` → 401 «Authentication required»).
Поэтому адаптеру нужен свой вход — `BEDOLAGA_CABINET_EMAIL` /
`BEDOLAGA_CABINET_PASSWORD` (служебный админ бота с правом `users:send_message`).
Не заданы — включить сводку нельзя (501 с причиной), а если её включили раньше,
задача считает вхолостую и пишет в лог, что отправила БЫ.

ЗАСАДА, О КОТОРОЙ НАДО ЗНАТЬ ВЛАДЕЛЬЦУ. У «Бедолаги» есть СВОЙ ежедневный отчёт
владельцу (`ADMIN_REPORTS_ENABLED`, время `ADMIN_REPORTS_SEND_TIME`, чат
`ADMIN_REPORTS_CHAT_ID`, см. их `reporting_service.py`). Включены оба — придут
два сообщения. Молча гасить чужую функцию мы не вправе, поэтому раз в прогон
пишем предупреждение в лог.

ЧАСОВОЙ ПОЯС. «Вчера» и «час отправки» считаем в поясе БОТА (`GET
/settings/TIMEZONE`, у стенда Europe/Moscow), а не в своём: контейнер адаптера
живёт по UTC, и сводка «за вчера» по UTC не сошлась бы ни с их админкой, ни с их
собственным отчётом — владелец получил бы два разных «вчера» в одно утро.

ДЕДУПЛИКАЦИЯ. В своей памяти храним дату последней отправки (в поясе бота).
Задача просыпается каждые 15 минут, но за день отправляет один раз: перезапуск
контейнера (а он случается при каждом обновлении) не должен давать второе
письмо. Отметка ставится сразу после успешной отправки — не в конце прогона.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, time as dt_time, timedelta, timezone
from fnmatch import fnmatchcase
from typing import Any
from zoneinfo import ZoneInfo

import httpx

import bot_session
from compose import Ctx, NotAvailable, UpstreamError, handler
from scheduler import task

# --- что и где храним ---------------------------------------------------------

# Ключ настройки и ключ отметок в своей памяти (`state.settings`). Новых таблиц
# не заводим: файл базы общий, его правят соседние разделы.
_KEY = "morning_summary"
_STATE_KEY = "morning_summary_state"

# Дефолт совпадает с нашим бэкендом (`overlay_morning_summary.py`), кроме одного:
# у нас сводка по умолчанию ВЫКЛЮЧЕНА. Рассылка, включившаяся сама собой при
# обновлении, — это сообщения, которых никто не заказывал.
_DEFAULT: dict[str, Any] = {
    "enabled": False,
    "hour": 9,
    "expiring_days": 3,
    # Телеграм адресата. None = ещё не определён (см. шапку файла).
    "recipient_telegram_id": None,
    # Откуда он взялся — фразой для человека, а не кодом: «почему сводка уходит
    # вот этому» первый вопрос при разборе, и отвечать на него должна админка.
    "recipient_source": "",
}

# Права их RBAC. Сводка — это выручка, поэтому мало «быть админом»: смотреть
# цифры пускаем по `stats:read`, а включать доставку — ещё и по праву на личное
# сообщение (`app/cabinet/routes/admin_users.py`).
_STATS_RIGHT = "stats:read"
_SEND_RIGHT = "users:send_message"

# --- как считаем деньги (правило ИХ статистики) -------------------------------

# Доход у них = поступления через ШЛЮЗЫ: типы deposit + subscription_payment,
# завершённые, с платёжным методом из `REAL_PAYMENT_METHODS`. Из enum методов
# они исключают ровно два значения (`app/database/crud/transaction.py`):
#   manual  — админские пополнения (это не выручка шлюза),
#   balance — оплата с баланса (иначе двойной счёт: деньги учтены при пополнении).
# Повторяем их правило один в один, чтобы цифра сводки сходилась с их админкой.
_INCOME_TYPES = {"deposit", "subscription_payment"}
_NON_GATEWAY_METHODS = {"manual", "balance"}

# --- пределы обхода -----------------------------------------------------------

_BOT_PAGE = 200            # у их списков limit ≤ 200 (жёстко, по их схеме)
_BOT_TIMEOUT = 30.0
_PAGES_LIMIT = 200         # предохранитель: 200 страниц по 200 = 40 000 записей

# Их access живёт минуты (~15), поэтому держим его в памяти процесса и обновляем
# по сроку. На диск не кладём: это ключ от админки, а перелогин стоит один запрос.
_SERVICE_EMAIL = os.environ.get("BEDOLAGA_CABINET_EMAIL", "").strip()
_SERVICE_PASSWORD = os.environ.get("BEDOLAGA_CABINET_PASSWORD", "")
_session: dict[str, Any] = {"token": "", "until": 0.0}

# Вторая защита от повтора, кроме записи в базе: если запись почему-то не легла
# (база отвалилась ровно в этот момент), процесс всё равно не отправит сводку
# второй раз за свою жизнь. Сутки одного сообщения владельцу дороже, чем
# идеальная чистота состояния.
_sent_in_process: dict[str, str] = {}

_NO_DELIVERY = (
    "Сводку некому доставить: личное сообщение у «Бедолаги» открывается только "
    "сессией администратора (машинный ключ X-API-Key их кабинетное API не "
    "принимает), а за фоновой задачей администратор не стоит. Заведите в боте "
    "служебного админа с правом «users:send_message» и передайте адаптеру "
    "BEDOLAGA_CABINET_EMAIL и BEDOLAGA_CABINET_PASSWORD."
)

# Разные причины молчания, и путать их нельзя: «ещё не включали» чинится
# тумблером в админке, а «не нашли кому» — только руками владельца.
_RECIPIENT_NOT_SET = (
    "Адресат ещё не записан: он определяется и сохраняется в момент включения "
    "сводки в админке."
)

_NO_RECIPIENT = (
    "Адресат не определён: у «Бедолаги» список админов (ADMIN_IDS) не отдаётся "
    "ни одной ручкой, а её чат отчётов не сходится ни с одним пользователем "
    "бота (обычно это группа, а в группу личное сообщение не уходит). Укажите "
    "телеграм получателя полем recipient_telegram_id."
)


def _log(message: str) -> None:
    # Тот же префикс, что у остального адаптера: лог контейнера общий.
    print(f"[adapter] утренняя сводка: {message}", flush=True)


def _fail(status: int, detail: str) -> UpstreamError:
    """Отказ самого адаптера в той же форме, что и ошибка бота."""
    return UpstreamError(httpx.Response(status, json={"detail": detail}))


def _detail(resp: httpx.Response) -> str:
    """Причина отказа словами. У них detail бывает и строкой, и объектом с
    `code`/`message` (например, `no_telegram_id` — человек без телеграма)."""
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


# --- настройка ----------------------------------------------------------------


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _telegram_id(value: Any) -> int | None:
    """Телеграм адресата. Ноль и мусор — это «не задан», а не «пользователь 0»."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number or None


def _normalize(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    return {
        "enabled": bool(data.get("enabled", _DEFAULT["enabled"])),
        "hour": _int(data.get("hour"), int(_DEFAULT["hour"]), 0, 23),
        # Верхняя граница окна — 90 дней: дальше «истекает в ближайшие N дней»
        # перестаёт быть предупреждением и становится всей базой сразу.
        "expiring_days": _int(data.get("expiring_days"), int(_DEFAULT["expiring_days"]), 1, 90),
        "recipient_telegram_id": _telegram_id(data.get("recipient_telegram_id")),
        "recipient_source": str(data.get("recipient_source") or ""),
    }


def _config(state: Any) -> dict[str, Any]:
    if state is None or not getattr(state, "available", False):
        # Хранить настройку негде — значит её нет, а «нет» здесь равно
        # «выключено»: врать тумблером про работающую сводку нельзя.
        return dict(_DEFAULT)
    return _normalize(state.get_setting(_KEY, {}))


def _delivery_configured() -> bool:
    # Общий служебный вход: см. bot_session.py — там же и причина, почему он нужен.
    return bot_session.configured()


# --- права --------------------------------------------------------------------


async def _require_admin(ctx: Ctx) -> None:
    """Смотреть настройку может администратор бота.

    Эта ручка не ходит в их API за данными, а читает своё хранилище, поэтому
    права обязана спросить явно: иначе настройку видел бы любой, кто знает путь.
    """
    if not ctx.token:
        raise _fail(401, "Требуется вход")
    me = await ctx.json("/cabinet/auth/me/is-admin", {}, soft=True) or {}
    if not me.get("is_admin"):
        raise _fail(403, "Недостаточно прав")


async def _require_rights(ctx: Ctx, *needed: str) -> None:
    """Спрашиваем ИХ права, а не выдумываем свои.

    Сравнение по их правилу: право пользователя — это ШАБЛОН (`*:*`, `users:*`).
    Не смогли спросить — считаем, что права нет: сводка содержит выручку, и
    «на всякий случай пропустим» здесь недопустимо.
    """
    data = await ctx.json("/cabinet/auth/me/permissions", {}, soft=True) or {}
    granted = data.get("permissions")
    granted = granted if isinstance(granted, list) else []
    for right in needed:
        if not any(isinstance(p, str) and fnmatchcase(right, p) for p in granted):
            raise _fail(403, f"Недостаточно прав в боте: нужно право «{right}»")


# --- доставка -----------------------------------------------------------------


async def _service_token(ctx: Ctx, renew: bool = False) -> str:
    return await bot_session.token(ctx, renew)


async def _send_message(ctx: Ctx, user_id: int, text: str) -> tuple[bool, str]:
    """Личное сообщение адресату. (успех, причина отказа человеческими словами).

    Отказ бота НЕ глотаем и не превращаем в успех: на неудаче нельзя ставить
    отметку «за сегодня отправлено», иначе владелец останется без сводки и даже
    не узнает об этом.
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
            return False, f"бот недоступен: {exc}"
        # Сессия протухла (их access живёт минуты) — второй заход уже с новым
        # токеном; повторять бесконечно нельзя, это была бы петля на 401.
        if resp.status_code == 401 and attempt == 1:
            continue
        if resp.status_code < 400:
            return True, ""
        return False, _detail(resp)
    return False, "неизвестная ошибка"


# --- чтение у бота ------------------------------------------------------------


async def _admin_json(ctx: Ctx, path: str, params: dict[str, Any] | None = None, what: str = "") -> Any:
    """Чтение машинным ключом. Любая беда — исключение с человеческой причиной.

    Мягкой деградации здесь быть не должно: пустой ответ вместо ошибки означал
    бы сводку «выручка 0, регистраций 0» — то есть тихую ложь владельцу в самом
    чувствительном месте.
    """
    label = what or path
    if not ctx.can_admin:
        raise RuntimeError(
            "адаптеру не выдан машинный ключ бота (BEDOLAGA_ADMIN_TOKEN) — "
            "цифры для сводки брать неоткуда"
        )
    resp = await ctx.admin("GET", path, params=params, timeout=_BOT_TIMEOUT)
    if resp is None:
        raise RuntimeError(f"бот недоступен ({label})")
    if resp.status_code >= 400:
        raise RuntimeError(f"бот отказал ({label}): {_detail(resp)}")
    try:
        return resp.json()
    except ValueError as exc:
        raise RuntimeError(f"бот ответил не-JSON ({label})") from exc


async def _setting(ctx: Ctx, key: str) -> Any:
    """Их настройка по ключу. Мягко: это подсказки (пояс, чат отчётов), без них
    сводка живёт — просто с честной оговоркой, откуда взяты значения."""
    resp = await ctx.admin("GET", f"/settings/{key}")
    if resp is None or resp.status_code >= 400:
        return None
    try:
        return (resp.json() or {}).get("current")
    except ValueError:
        return None


async def _bot_user(ctx: Ctx, telegram_id: int) -> dict[str, Any] | None:
    """Пользователь бота по телеграму. None — такого нет (например, в настройке
    бота указана группа, а не человек: личное сообщение туда не уходит)."""
    resp = await ctx.admin("GET", f"/users/by-telegram-id/{int(telegram_id)}")
    if resp is None or resp.status_code >= 400:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) and data.get("id") else None


# --- время --------------------------------------------------------------------


async def _timezone(ctx: Ctx) -> tuple[Any, str, str]:
    """(пояс, его имя, откуда взят). Пояс бота — то, по чему живёт владелец."""
    name = str(await _setting(ctx, "TIMEZONE") or "").strip()
    if name:
        try:
            return ZoneInfo(name), name, "бот"
        except Exception:  # noqa: BLE001 — незнакомое имя пояса не повод падать
            _log(f"часовой пояс бота «{name}» не распознан — считаю по своему времени")
    local = datetime.now().astimezone().tzinfo or timezone.utc
    return local, str(datetime.now(local).tzname() or "локальный"), "адаптер"


def _yesterday(tz: Any, now: datetime) -> tuple[datetime, datetime]:
    """Границы вчерашних календарных суток в поясе бота.

    Полночь собираем из даты, а не вычитаем сутки из «сегодня 00:00»: в день
    перевода часов сутки не равны 24 часам, и вычитание уехало бы на час.
    """
    end = datetime.combine(now.date(), dt_time.min, tzinfo=tz)
    start = datetime.combine(now.date() - timedelta(days=1), dt_time.min, tzinfo=tz)
    return start, end


def _moment(value: Any) -> datetime | None:
    """Их метка времени в datetime. Голую (без пояса) считаем UTC — они и отдают
    UTC, просто иногда без суффикса."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


# --- цифры --------------------------------------------------------------------


async def _revenue(ctx: Ctx, start: datetime, end: datetime) -> dict[str, Any]:
    """Выручка за окно: по правилу их же статистики + отдельной строкой ручные
    пополнения.

    Ручные показываем отдельно, а не прячем: их бот в доход шлюзов не считает, и
    если владелец начислил кому-то 5 000 ₽ руками, а сводка про них смолчала,
    первый же вопрос будет «куда делись деньги».
    """
    out: dict[str, Any] = {
        "income_kopeks": 0, "payments": 0, "manual_kopeks": 0, "manual_payments": 0,
        "incomplete": False,
    }
    offset = 0
    for _page in range(_PAGES_LIMIT):
        data = await _admin_json(
            ctx,
            "/transactions",
            {
                "limit": _BOT_PAGE,
                "offset": offset,
                "is_completed": True,
                # Фильтр по датам — на их стороне, чтобы не тащить всю историю.
                # Границы всё равно проверяем сами: их условие по `date_to`
                # включающее, и платёж ровно в полночь попал бы в оба дня.
                "date_from": start.astimezone(timezone.utc).isoformat(),
                "date_to": end.astimezone(timezone.utc).isoformat(),
            },
            "список платежей",
        )
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list) or not items:
            break
        for row in items:
            if not isinstance(row, dict):
                continue
            created = _moment(row.get("created_at"))
            if created is None or not (start <= created < end):
                continue
            kind = str(row.get("type") or "").strip().lower()
            method = str(row.get("payment_method") or "").strip().lower()
            try:
                amount = abs(int(row.get("amount_kopeks") or 0))
            except (TypeError, ValueError):
                continue
            if kind in _INCOME_TYPES and method and method not in _NON_GATEWAY_METHODS:
                out["income_kopeks"] += amount
                out["payments"] += 1
            elif kind == "deposit" and method == "manual":
                out["manual_kopeks"] += amount
                out["manual_payments"] += 1
        offset += len(items)
        if len(items) < _BOT_PAGE:
            break
    else:
        # Обход упёрся в предохранитель: столько платежей за сутки — это уже не
        # наш случай, но занизить выручку молча нельзя.
        out["incomplete"] = True
    return out


async def _registrations(ctx: Ctx, start: datetime, end: datetime) -> tuple[int, bool]:
    """Сколько человек зарегистрировалось за окно. (сколько, полный ли обход).

    Фильтра по дате у их `/users` нет, зато список отсортирован по дате
    регистрации вниз (`order_by(User.created_at.desc())`), поэтому дойдя до
    записей старее окна можно останавливаться — обход стоит страницу-другую, а
    не всю базу.

    Но верить чужой сортировке на слово нельзя: изменят её у себя — и наша цифра
    молча занизится, а «примерно верно» в сводке про деньги хуже, чем медленно.
    Поэтому решение об остановке принимаем не по отдельной записи, а по ЦЕЛОЙ
    странице: страница пришла по убыванию и кончилась записью старее окна —
    дальше только старее. Хоть одна запись не на своём месте — сортировки нет,
    и дальше идём до конца списка.
    """
    count = 0
    offset = 0
    ordered = True
    previous: datetime | None = None
    for _page in range(_PAGES_LIMIT):
        data = await _admin_json(
            ctx, "/users", {"limit": _BOT_PAGE, "offset": offset}, "список пользователей"
        )
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list) or not items:
            return count, True
        for row in items:
            if not isinstance(row, dict):
                continue
            created = _moment(row.get("created_at"))
            if created is None:
                continue
            if previous is not None and created > previous:
                ordered = False
            previous = created
            if start <= created < end:
                count += 1
        offset += len(items)
        if len(items) < _BOT_PAGE:
            return count, True
        if ordered and previous is not None and previous < start:
            return count, True
    return count, False


async def _active_subscriptions(ctx: Ctx) -> int:
    """Активные подписки — их собственная цифра из `/stats/overview`.

    Считать самим по списку было бы лишним обходом базы и, главное, другой
    цифрой: у них «активная» — это статус в их таблице, и владелец сверяет
    сводку именно с их админкой.
    """
    data = await _admin_json(ctx, "/stats/overview", None, "общая статистика")
    section = data.get("subscriptions") if isinstance(data, dict) else None
    try:
        return int((section or {}).get("active") or 0)
    except (TypeError, ValueError):
        return 0


async def _expiring(ctx: Ctx, now: datetime, days: int) -> tuple[int, bool]:
    """Сколько активных подписок кончится в ближайшие N дней. (сколько, полный ли).

    Готовой цифры у них нет, а по дате окончания их список не отсортирован —
    приходится обходить активные подписки целиком. Раз в сутки это допустимо.
    """
    edge = now + timedelta(days=days)
    count = 0
    offset = 0
    for _page in range(_PAGES_LIMIT):
        data = await _admin_json(
            ctx,
            "/subscriptions",
            {"limit": _BOT_PAGE, "offset": offset, "status": "active"},
            "список подписок",
        )
        rows = data if isinstance(data, list) else []
        if not rows:
            return count, True
        for row in rows:
            if not isinstance(row, dict):
                continue
            ends = _moment(row.get("end_date"))
            if ends is not None and now <= ends < edge:
                count += 1
        offset += len(rows)
        if len(rows) < _BOT_PAGE:
            return count, True
    return count, False


async def _collect(ctx: Ctx, config: dict[str, Any], tz: Any, now: datetime) -> dict[str, Any]:
    """Все цифры сводки за вчера. Чистый сбор: ничего не шлёт и не записывает."""
    start, end = _yesterday(tz, now)
    money = await _revenue(ctx, start, end)
    registrations, users_full = await _registrations(ctx, start, end)
    active = await _active_subscriptions(ctx)
    expiring, subs_full = await _expiring(ctx, now, int(config["expiring_days"]))
    return {
        "date": start.strftime("%d.%m"),
        "date_iso": start.date().isoformat(),
        "income_kopeks": money["income_kopeks"],
        "payments": money["payments"],
        "manual_kopeks": money["manual_kopeks"],
        "manual_payments": money["manual_payments"],
        "registrations": registrations,
        "active_subscriptions": active,
        "expiring": expiring,
        "expiring_days": int(config["expiring_days"]),
        # Обход упёрся в предохранитель — цифры занижены, и владелец должен это
        # видеть прямо в сообщении, а не гадать, почему сводка разошлась.
        "incomplete": bool(money["incomplete"]) or not users_full or not subs_full,
        "window": {"from": start.isoformat(), "to": end.isoformat()},
    }


# --- текст --------------------------------------------------------------------


def _money(kopeks: int) -> str:
    """Копейки в рубли: целые — без хвоста, тысячи — с пробелом."""
    rubles = kopeks / 100
    body = f"{rubles:,.0f}" if float(rubles).is_integer() else f"{rubles:,.2f}"
    return body.replace(",", " ")


def _text(numbers: dict[str, Any]) -> str:
    """Сообщение владельцу. HTML — их ручка принимает именно его."""
    if numbers["payments"]:
        revenue = f"{_money(numbers['income_kopeks'])} ₽ ({numbers['payments']} опл.)"
    else:
        revenue = "нет оплат"
    lines = [
        f"☀️ <b>Сводка за {numbers['date']}</b>",
        "",
        f"💰 Выручка: {revenue}",
    ]
    if numbers["manual_payments"]:
        lines.append(
            f"➕ Начислено вручную: {_money(numbers['manual_kopeks'])} ₽ "
            f"({numbers['manual_payments']})"
        )
    lines += [
        f"🆕 Новых регистраций: {numbers['registrations']}",
        f"📊 Активных подписок: {numbers['active_subscriptions']}",
        f"⏳ Истекают в ближайшие {numbers['expiring_days']} дн.: {numbers['expiring']}",
    ]
    if numbers["incomplete"]:
        lines += ["", "⚠️ Данные неполные: бот отдал не весь список, цифры занижены."]
    return "\n".join(lines)


# --- сам прогон ---------------------------------------------------------------


def _sent_today(state: Any, day: str) -> bool:
    if _sent_in_process.get("date") == day:
        return True
    if state is None or not getattr(state, "available", False):
        return False
    saved = state.get_setting(_STATE_KEY, {})
    return isinstance(saved, dict) and str(saved.get("last_sent") or "") == day


def _mark_sent(state: Any, day: str, recipient: int) -> None:
    _sent_in_process["date"] = day
    if state is not None and getattr(state, "available", False):
        state.set_setting(_STATE_KEY, {"last_sent": day, "recipient_telegram_id": recipient})


async def run_once(ctx: Ctx, dry_run: bool = False) -> dict[str, Any]:
    """Один проход: собрать цифры, сложить текст и (если не вхолостую) отправить.

    `dry_run=True` — холостой прогон: считаем и показываем, но никому не пишем и
    отметку не ставим. Тем же путём идёт работа, когда доставка не настроена:
    молча ничего не делать — худший вариант, а строка в логе «отправила бы вот
    это» показывает, что задача жива и чего ей не хватает.
    """
    config = _config(ctx.state)
    tz, tz_name, tz_source = await _timezone(ctx)
    now = datetime.now(tz)
    numbers = await _collect(ctx, config, tz, now)
    recipient = config["recipient_telegram_id"]
    out: dict[str, Any] = {
        "numbers": numbers,
        "text": _text(numbers),
        "timezone": tz_name,
        "timezone_source": tz_source,
        "recipient_telegram_id": recipient,
        "dry_run": dry_run,
        "sent": False,
        "reason": "",
    }

    if not recipient:
        out["reason"] = _RECIPIENT_NOT_SET
        return out
    if dry_run:
        return out

    # Внутренний id ищем перед самой отправкой: единственная настройка — телеграм,
    # а их id пользователя мы не храним, чтобы он не устарел молча.
    user = await _bot_user(ctx, int(recipient))
    if user is None:
        out["reason"] = (
            f"в боте нет пользователя с телеграмом {recipient} — "
            "личное сообщение отправить некому"
        )
        return out

    ok, reason = await _send_message(ctx, int(user["id"]), out["text"])
    if not ok:
        out["reason"] = reason
        return out
    out["sent"] = True
    # Отметку ставим сразу после успеха: контейнер перезапускают при каждом
    # обновлении, и «отметим в конце прогона» означало бы вторую такую же сводку.
    _mark_sent(ctx.state, now.date().isoformat(), int(recipient))
    return out


async def _native_report_warning(ctx: Ctx) -> None:
    """Предупредить в лог, если у бота включён СВОЙ ежедневный отчёт владельцу.

    Любую неудачу проверки молча пропускаем: это подсказка человеку, а не
    условие работы. Гасить чужую функцию за владельца мы не вправе.
    """
    enabled = await _setting(ctx, "ADMIN_REPORTS_ENABLED")
    if not enabled:
        return
    at = await _setting(ctx, "ADMIN_REPORTS_SEND_TIME") or "?"
    _log(
        f"у бота включён свой ежедневный отчёт (ADMIN_REPORTS_ENABLED, {at}) — "
        "владелец получит два сообщения; выключите одно из двух"
    )


@task("morning-summary", minutes=15, run_at_start=False, timeout=600)
async def morning_summary(ctx: Ctx) -> None:
    """Ежедневная сводка владельцу.

    Просыпаемся каждые 15 минут, а не раз в сутки: расписание адаптера знает
    только интервалы, и «раз в 24 часа» уехало бы по кругу от заданного часа.
    Отправку разрешает не будильник, а три условия: включено, наступил
    настроенный час, сегодня ещё не отправляли.

    Час проверяем «не раньше», а не «ровно»: при точном совпадении обновление
    контейнера в 9:05 съедало бы сводку за день целиком и молча. Опоздание на
    час лучше, чем пропуск, а второго сообщения не будет — есть отметка о дате.
    """
    config = _config(ctx.state)
    if not config["enabled"]:
        return

    tz, _name, _source = await _timezone(ctx)
    now = datetime.now(tz)
    if now.hour < int(config["hour"]):
        return
    day = now.date().isoformat()
    if _sent_today(ctx.state, day):
        return

    ready = _delivery_configured()
    if not ready:
        # Включили раньше, а доступ для отправки пропал (или его и не было).
        _log(f"доставка не настроена — холостой прогон. {_NO_DELIVERY}")
    else:
        await _native_report_warning(ctx)

    result = await run_once(ctx, dry_run=not ready)
    numbers = result["numbers"]
    outcome = (
        "отправлено" if result["sent"]
        else f"не отправлено: {result['reason'] or 'холостой прогон'}"
    )
    _log(
        "за {date}: выручка {income} ₽ ({payments} опл.), регистраций "
        "{registrations}, активных {active_subscriptions}, истекают {expiring} — "
        "{outcome}".format(
            income=_money(numbers["income_kopeks"]), outcome=outcome, **numbers
        )
    )


# --- кто получатель -----------------------------------------------------------


async def _recipient_candidate(ctx: Ctx) -> tuple[int | None, str, dict[str, Any] | None]:
    """Кого их настройки предлагают в качестве владельца. (телеграм, откуда, кто).

    Берём чат, куда бот САМ шлёт свой отчёт, и чат админских уведомлений —
    ничего ближе к «владельцу» в их API не отдаётся. Значение проверяем: в этих
    настройках может стоять группа или форум, а личное сообщение уходит только
    человеку, который есть у бота в пользователях.
    """
    for key, source in (
        ("ADMIN_REPORTS_CHAT_ID", "чат отчётов бота"),
        ("ADMIN_NOTIFICATIONS_CHAT_ID", "чат уведомлений бота"),
    ):
        telegram = _telegram_id(await _setting(ctx, key))
        if telegram is None:
            continue
        user = await _bot_user(ctx, telegram)
        if user is not None:
            return telegram, source, user
    return None, "", None


async def _self_candidate(ctx: Ctx) -> tuple[int | None, dict[str, Any] | None]:
    """Тот, кто прямо сейчас включает сводку в админке.

    Последний разумный вариант: правило проекта — рассылку включает владелец,
    значит он же и адресат. Записываем это в настройку явно, чтобы в GET было
    видно, кому уходит сводка, и чтобы фоновая задача ничего не домысливала.
    """
    me = await ctx.json("/cabinet/auth/me", {}, soft=True) or {}
    telegram = _telegram_id(me.get("telegram_id"))
    if telegram is None:
        return None, None
    return telegram, await _bot_user(ctx, telegram)


def _person(user: dict[str, Any] | None) -> str:
    """Имя получателя человеческими словами — для экрана и логов."""
    if not user:
        return ""
    name = " ".join(str(user.get(k) or "").strip() for k in ("first_name", "last_name")).strip()
    login = str(user.get("username") or "").strip()
    return name or (f"@{login}" if login else f"id {user.get('id')}")


async def _recipient_view(ctx: Ctx, config: dict[str, Any]) -> dict[str, Any]:
    """Что показать в админке про адресата: кто, откуда и дойдёт ли до него.

    Когда адресат ещё не задан, показываем ПРЕДЛОЖЕНИЕ (`proposed`) — кого
    возьмём при включении. Это честнее пустого поля: владелец видит будущего
    получателя до того, как нажмёт тумблер, а не после первой отправки.
    """
    telegram = config["recipient_telegram_id"]
    if telegram:
        user = await _bot_user(ctx, int(telegram))
        return {
            "telegram_id": telegram,
            "source": config["recipient_source"] or "explicit",
            "name": _person(user),
            "deliverable": user is not None,
            "reason": "" if user else f"в боте нет пользователя с телеграмом {telegram}",
            "proposed": False,
        }
    candidate, source, user = await _recipient_candidate(ctx)
    if candidate is None:
        return {
            "telegram_id": None, "source": "", "name": "", "deliverable": False,
            "reason": _NO_RECIPIENT, "proposed": True,
        }
    return {
        "telegram_id": candidate, "source": source, "name": _person(user),
        "deliverable": True, "reason": "", "proposed": True,
    }


async def _resolve_recipient(ctx: Ctx, config: dict[str, Any]) -> tuple[int, str]:
    """Адресат на момент ВКЛЮЧЕНИЯ. Не нашли — честный отказ, а не случайный админ."""
    telegram = config["recipient_telegram_id"]
    if telegram:
        if await _bot_user(ctx, int(telegram)) is None:
            raise NotAvailable(
                f"В боте нет пользователя с телеграмом {telegram}: личное сообщение "
                "ему не уйдёт. Человек должен хотя бы раз написать боту."
            )
        return int(telegram), config["recipient_source"] or "explicit"

    candidate, source, _user = await _recipient_candidate(ctx)
    if candidate is not None:
        return candidate, source

    mine, user = await _self_candidate(ctx)
    if mine is not None and user is not None:
        return mine, "тот, кто включил сводку"
    raise NotAvailable(_NO_RECIPIENT)


# --- ручки --------------------------------------------------------------------


@handler("GET", "/api/admin/morning-summary")
async def morning_summary_config(ctx: Ctx) -> dict[str, Any]:
    """Настройка + диагностика: кому уйдёт, по каким часам и почему молчит.

    Экран кабинета читает три поля (тумблер, час, окно), остальное — ответы на
    вопросы, которые владелец задаёт первыми, когда сводка не приходит.
    """
    await _require_admin(ctx)
    config = _config(ctx.state)
    _tz, tz_name, tz_source = await _timezone(ctx)
    saved = ctx.state.get_setting(_STATE_KEY, {}) if ctx.state is not None else {}
    return {
        **config,
        "timezone": tz_name,
        "timezone_source": tz_source,
        "delivery_ready": _delivery_configured(),
        "delivery_reason": "" if _delivery_configured() else _NO_DELIVERY,
        "recipient": await _recipient_view(ctx, config),
        "last_sent": (saved or {}).get("last_sent") if isinstance(saved, dict) else None,
    }


@handler("PUT", "/api/admin/morning-summary")
async def morning_summary_save(ctx: Ctx) -> dict[str, Any]:
    """Сохранение. Меняем только присланное — экран шлёт частичное обновление.

    Включить, когда сводку доставить нечем или некому, нельзя: тумблер
    «включено» при молчащей рассылке — это ложь в админке, которую замечают
    через месяц по вопросу «а где сводки?». Отказ приходит ДО записи и
    сохранение не проходит целиком: «часть настроек применилась, часть нет» —
    худший исход, потом ищи, что же в итоге записано. Час и окно правятся
    обычным сохранением с выключенным тумблером.

    Чего ждать после включения: если настроенный час сегодня уже прошёл, первая
    сводка придёт в ближайшие четверть часа (задача не «ждёт ровно девяти», а
    «не раньше девяти»). Это заодно подтверждение, что доставка настроена —
    лучше, чем сутки гадать, придёт ли завтра.
    """
    await _require_admin(ctx)
    await _require_rights(ctx, _STATS_RIGHT, _SEND_RIGHT)
    if ctx.state is None or not ctx.state.available:
        raise NotAvailable(
            "Настройку некуда сохранить: у адаптера нет доступа к своему хранилищу"
        )

    body = ctx.payload()
    config = _config(ctx.state)
    if body.get("hour") is not None:
        config["hour"] = _int(body["hour"], int(config["hour"]), 0, 23)
    if body.get("expiring_days") is not None:
        config["expiring_days"] = _int(body["expiring_days"], int(config["expiring_days"]), 1, 90)
    if "recipient_telegram_id" in body:
        # Явно заданный адресат — источник «explicit»: при следующем включении
        # его не подменит ни чат отчётов, ни тот, кто нажал тумблер.
        config["recipient_telegram_id"] = _telegram_id(body["recipient_telegram_id"])
        config["recipient_source"] = "explicit" if config["recipient_telegram_id"] else ""

    enabled = body.get("enabled")
    if enabled is not None and bool(enabled):
        if not _delivery_configured():
            raise NotAvailable(_NO_DELIVERY)
        telegram, source = await _resolve_recipient(ctx, config)
        config["recipient_telegram_id"] = telegram
        config["recipient_source"] = source
    if enabled is not None:
        config["enabled"] = bool(enabled)

    ctx.state.set_setting(_KEY, config)
    return {
        **config,
        "delivery_ready": _delivery_configured(),
        "recipient": await _recipient_view(ctx, config),
    }


@handler("POST", "/api/admin/morning-summary/preview", deadline=120.0)
async def morning_summary_preview(ctx: Ctx) -> dict[str, Any]:
    """Холостой прогон: те же цифры и тот же текст, что ушли бы владельцу.

    Нужен ровно затем, зачем существует сама сводка: убедиться, что цифры
    сходятся с их админкой, НЕ отправляя никому сообщение. Обход списков долгий,
    поэтому у ручки свой предел времени — общий (30 с) обрывал бы её на большой
    базе, и владелец видел бы «ошибку» вместо цифр.
    """
    await _require_admin(ctx)
    await _require_rights(ctx, _STATS_RIGHT)
    try:
        return await run_once(ctx, dry_run=True)
    except RuntimeError as exc:
        # Беда чужого API — это 502 с причиной, а не 500 «что-то пошло не так»:
        # по тексту сразу видно, чего именно не хватило.
        raise _fail(502, f"Не удалось собрать сводку: {exc}") from exc
