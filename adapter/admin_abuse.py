"""Детект абьюза триала поверх «Бедолаги» (раздел «Детект абьюза», /admin/abuse).

ЧТО ДЕЛАЕТ РАЗДЕЛ. Показывает группы аккаунтов с совпадающими идентифицирующими
признаками, которые успели попользоваться пробником: похоже на мультиаккаунт ради
нескольких бесплатных триалов. Раздел ТОЛЬКО ЧИТАЕТ: ни блокировок, ни снятия
триала он сам не делает, автодействий нет вовсе (кнопки в строках зовут ручки
раздела «Пользователи», и решение принимает человек).

ЧЬЯ ЭТО ФУНКЦИЯ. Наша: у «Бедолаги» детекта мультиаккаунта нет вовсе. Проверено
по их `/openapi.json` (676 путей — ни одной ручки про abuse/fraud/multi-account)
и по их коду. Их «Ban System» (`/cabinet/admin/ban-system/*`) — это ДРУГОЕ и не
замена: отдельный внешний сервер (BAN_SYSTEM_API_URL, по умолчанию выключен,
на стенде `{"enabled": false, "configured": false}`), и считает он IP-адреса
ПОДКЛЮЧЕНИЙ к нодам ради борьбы с раздачей чужой подписки, а не регистрации ради
повторных пробников. Так что правило владельца «не подменять своей копией то,
что у бота уже есть» здесь не нарушается — подменять нечего.

ОТКУДА ДАННЫЕ.
  • Устройства (HWID) — из ПАНЕЛИ Remnawave (`/api/hwid/devices` + `/api/users`
    для карты владельцев). Это данные панели, а не бота, и по правилу владельца
    такое не гасится из-за чужого бота: те же два обхода делает соседний раздел
    `admin_new_device.py` и наш собственный бэкенд.
  • Люди (почта, телеграм, дата, статус, кто кого привёл, был ли пробник) — из
    машинного API бота (`GET /users`, X-API-Key). Их КАБИНЕТНЫЙ список
    (`/cabinet/admin/users`) для этого не годится: он не отдаёт ни почты, ни
    `referred_by_id`, ни ссылки подписки — а без ссылки устройство панели не
    привязать к человеку бота.

ЧЕГО ЗДЕСЬ НЕТ И ПОЧЕМУ — ПОЛОВИНА ПО IP. Её не будет, и это не лень: журнала
входов с адресами у «Бедолаги» нет, а те адреса, что до неё всё-таки доходят, к
обычному человеку отношения не имеют. Всё ниже проверено живьём 7 августа, а не
взято из документации.
  • Их ручка `GET /cabinet/admin/users/{user_id}/activity` (появилась в v4.0.0) —
    вызвали и посмотрели ответ: запись входа приходит как
    `{"type":"cabinet_login","source":"cabinet","title":null,"meta":null,
    "timestamp":…}` — ни адреса, ни устройства. Иначе и быть не могло: собирает
    она их из таблицы `cabinet_refresh_tokens`, а там из «про устройство» одна
    колонка `device_info` и НИ ОДНОЙ колонки под адрес (сверено со схемой их
    базы). Плюс ручка на ОДНОГО человека за запрос (limit ≤ 200) — то есть даже
    будь там адрес, обход стоил бы по запросу на душу.
  • Адреса в их базе лежат в четырёх таблицах, и ни одна не про обычного
    пользователя: `admin_audit_log` (действия администраторов — на стенде 2150
    записей и всего 6 разных людей, это сами админы), `web_api_tokens
    .last_used_ip` (машинные ключи), `legal_consents` (адрес в момент согласия с
    правилами, на стенде пустая) и `apple_iap_abuse_events` (их собственный
    антифрод покупок Apple, тоже пустая). Наружу их API отдаёт из этого только
    админский журнал (`AuditLogEntry`) — про админов, а не про тех, кто крутит
    пробники. Ещё `last_ip` показывает их мини-аппа (`MiniAppDevice`), но это
    тот же адрес панели и только про СЕБЯ — карты «кто с кем совпал» из него не
    собрать.
  • ГЛАВНОЕ, И ЭТО СОБЛАЗН, А НЕ ПРОБЕЛ: адрес регистрации устройства ЗНАЕТ
    ПАНЕЛЬ — поле `requestIp` в `/api/hwid/devices`, оно у нас уже под рукой.
    Сигнал «общий IP» из него мы намеренно НЕ строим, и вот измерение на живой
    панели (170 устройств): у 97 из них (57 %) поля нет вовсе, а из четырёх
    адресов, на которых сошлись РАЗНЫЕ владельцы, ТРИ оказались exit-адресами
    наших же узлов — девять человек в трёх ложных группах, которые просто
    обновляли подписку, сидя под нашим VPN (та самая ловушка «адрес входа = адрес
    узла»). Вычесть адреса узлов надёжно нечем: в панели у узла записано ИМЯ, его
    надо резолвить, и молчаливый промах резолва свалил бы В ОДНУ «банду» всех
    пользователей VPN разом. Ошибка в этом разделе — повод заблокировать
    невиновного, поэтому сигнала нет. Захочется вернуться к этой мысли — сначала
    измерьте те же две цифры на своей панели.
Поэтому сигналов «общий IP» здесь не появляется, и вместо тихой пустоты раздел
говорит об этом словами — необязательным полем `note` (см. ниже). Подделывать
адреса (например, выдавать за них страну ноды или device_info) нельзя: ложная
группа в этом разделе — это повод заблокировать невиновного.

СИГНАЛЫ, КОТОРЫЕ РАБОТАЮТ (форма и смысл — как у нашего бэкенда,
`overlay_abuse_engine.py`, чтобы экран читался одинаково на любом бэкенде):
  • `hwid`     — один физический девайс у разных аккаунтов (снимок панели);
  • `email`    — «одинаковая» почта с учётом gmail-трюков (точки, +алиасы);
  • `referral` — само-реферал: пригласивший и приглашённый оказались одним
    человеком. У нашего бэкенда это доказывается общим IP; здесь IP нет, поэтому
    доказательством служит общий девайс или общая (нормализованная) почта —
    факт из данных, а не догадка.

ТОЛЬКО ЧТЕНИЕ, И ЭТО ВАЖНО ФИЗИЧЕСКИ. К панели ходим исключительно GET'ами и
НИКОГДА не шлём заголовок `x-hwid`: он не «проверяет» устройство, а РЕГИСТРИРУЕТ
его на аккаунте и шлёт владельцу событие #UserDeviceAddedEvent. Раздел, который
ищет лишние устройства, не имеет права сам их создавать.

ПРАВА. Гейт — их же RBAC: первым делом дёргаем их кабинетную ручку
`/cabinet/admin/users` сессией САМОГО админа (право `users:read`), и её отказ
уходит кабинету как есть. Машинный ключ адаптера (X-API-Key) идёт в дело только
ПОСЛЕ этого — иначе почту всех пользователей читал бы любой вошедший в кабинет.
Гейт проверяется на КАЖДЫЙ запрос и никогда не кэшируется, кэшируется только
снимок данных (он одинаков для всех, кто гейт прошёл).

ПОЧЕМУ КЭШ. Наш бэкенд собирает такие группы одним SQL за миллисекунды, а тут
это три обхода по страницам (люди бота, пользователи панели, устройства панели).
На каждое открытие экрана и на каждое нажатие «Обновить» гонять их заново —
значит на большой базе не уложиться в общий предел ответа (30 с) и получить
крутилку вместо ответа. Держим снимок в памяти процесса 5 минут, а фильтры
(`min_accounts`, `only_trial`) считаются по нему заново каждый раз — переключение
галочки мгновенное и всегда по свежему для этого снимка.

ФОРМА ОТВЕТА. `{clusters, total}` — ровно то, что ждёт экран (`AbuseCluster` в
`cabinet/src/api/admin.ts`). Поля `note` и `warning` НЕОБЯЗАТЕЛЬНЫЕ: наш
собственный бэкенд их не шлёт вовсе, и обычная установка RemnaShop выглядит и
работает как раньше — их шлёт только адаптер, которому есть что честно сказать
про чужой бот.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from compose import HANDLERS, Ctx, NotAvailable, UpstreamError, handler

# --- пределы обходов ----------------------------------------------------------

_BOT_PAGE = 200          # у их `/users` limit ≤ 200 (жёстко, по их схеме)
_PANEL_PAGE = 500        # столько пользователей панели берём за запрос
_DEVICE_PAGE = 1000      # столько устройств панель отдаёт за запрос
_PAGES_LIMIT = 500       # предохранитель от бесконечного обхода страниц
_PANEL_TIMEOUT = 30.0

# X-Forwarded-* к панели БОЛЬШЕ НЕ СТАВИМ ЗДЕСЬ: их шлёт общий клиент панели
# (`main._panel_headers`) — и только когда идём по http, как и положено. Беда
# была настоящая: панель за своим прокси рвёт соединение без этой пары, и раздел
# показывал бы «совпадений нет» там, где их не искали. Чинилось это сначала тут,
# а потом нашлось, что молча пустуют ещё три раздела на той же панели, — поэтому
# лечение переехало в одно место на всех.

# Сколько живёт снимок. Пять минут — компромисс: экран открывают подряд по
# нескольку раз (перешёл, вернулся, нажал «Обновить»), а данные меняются
# медленно — устройство подключается не чаще, чем человек ставит приложение.
_SNAPSHOT_TTL = 300.0

# Свой предел на сбор снимка — меньше общего предела ответа (main.OWN_DEADLINE
# = 30 с). Упереться в общий значит отдать 504 и не показать НИЧЕГО; упершись в
# свой, мы отдаём то, что успели собрать, и честно пишем в `warning`, что обход
# неполный. Половина правды с пометкой лучше, чем крутилка.
_BUDGET = 22.0

# Телеграм-id растёт со временем: грубый порог «аккаунт свежий» — тот же, что у
# нашего бэкенда, чтобы значок «свежий TG» означал на обоих одно и то же.
_YOUNG_TG_ID = 7_500_000_000

_GMAIL_DOMAINS = {"gmail.com", "googlemail.com"}

# Длина названия устройства в ключе группы — как у нашего бэкенда.
_MODEL_MAX = 40


def _log(message: str) -> None:
    # Тот же префикс, что у остального адаптера: лог контейнера общий.
    print(f"[adapter] детект абьюза: {message}", flush=True)


def _fail(status: int, detail: str) -> UpstreamError:
    """Отказ самого адаптера в той же форме, что и ошибка бота."""
    return UpstreamError(httpx.Response(status, json={"detail": detail}))


# --- мелкие переводчики -------------------------------------------------------


def _int(value: Any, default: int = 0) -> int:
    """Их числа приходят и строкой, и None — своя обёртка вместо int()."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _flag(value: Any, default: bool) -> bool:
    """Галочка из query-строки. Кабинет шлёт `true`/`false` словами."""
    if value is None:
        return default
    return str(value).strip().lower() not in ("false", "0", "no", "off", "")


def _name(u: dict[str, Any]) -> str:
    """Имя для показа — то же правило, что в `admin.py`: без него в списке
    появляются безымянные строки, а в этом разделе строка без подписи
    бесполезна."""
    parts = [u.get("first_name") or "", u.get("last_name") or ""]
    name = " ".join(p for p in parts if p).strip()
    return name or (u.get("username") or "") or (u.get("email") or "") or "—"


def normalize_email(email: Any) -> str | None:
    """Почта → «фактический ящик»: gmail игнорирует точки и +алиасы, у остальных
    доменов срезаем только +алиас. Ровно та же нормализация, что у нашего
    бэкенда: `a.b+x@gmail.com` и `ab@gmail.com` — один и тот же ящик, и именно
    на этом строится самый дешёвый способ завести себе второй пробник."""
    if not isinstance(email, str) or "@" not in email:
        return None
    local, _, domain = email.strip().lower().partition("@")
    local = local.split("+", 1)[0]
    if domain in _GMAIL_DOMAINS:
        local = local.replace(".", "")
        domain = "gmail.com"
    if not local or not domain:
        return None
    return f"{local}@{domain}"


def _short_uuid(url: Any) -> str:
    """Хвост ссылки подписки — он же `shortUuid` в панели. По нему связываем
    человека бота с записью панели: телеграм есть не у всех (в кабинете
    регистрируются почтой), а ссылка подписки есть у каждого, у кого она есть."""
    tail = str(url or "").split("?")[0].split("#")[0].rstrip("/")
    return tail.rsplit("/", 1)[-1] if "/" in tail else ""


def _device_label(device: dict[str, Any]) -> str:
    """Как назвать устройство в ключе группы. Модель приходит от клиента, поэтому
    режем по длине; экранировать не нужно — это JSON для React, а не HTML."""
    model = str(device.get("deviceModel") or "").strip()
    platform = str(device.get("platform") or "").strip()
    if model and platform and platform != model:
        label = f"{model} ({platform})"
    else:
        label = model or platform
    return label[:_MODEL_MAX]


def _trial_used(row: dict[str, Any]) -> bool:
    """Пользовался ли человек пробником.

    Готового флага «право на триал» на их пользователе нет (см. `admin.py`), но
    ФАКТ пробной подписки есть: их `/users` отдаёт и текущую подписку, и список
    всех, а у каждой — `is_trial`. Это и есть то, ради чего заводят вторые
    аккаунты, поэтому считаем именно по факту, а не по праву.
    """
    rows: list[Any] = []
    subs = row.get("subscriptions")
    if isinstance(subs, list):
        rows.extend(subs)
    current = row.get("subscription")
    if isinstance(current, dict):
        rows.append(current)
    return any(isinstance(s, dict) and bool(s.get("is_trial")) for s in rows)


def _account_view(row: dict[str, Any]) -> dict[str, Any]:
    """Аккаунт в форме, которую ждёт экран (`AbuseAccount`).

    Про `is_trial_available`. У «Бедолаги» такого поля на человеке нет вовсе:
    право на пробник она решает на лету (пробник включён, не запрещён для этого
    способа входа, не было платной подписки и нет активной). Повторять их расчёт
    здесь значило бы врать при первом же изменении их настроек, поэтому держим
    договор нашего экрана — `is_trial_available = not trial_used`: значок
    «триал использован» и подпись кнопки читаются одинаково на обоих бэкендах.
    Сама кнопка у этого бота всё равно упрётся в честный 501 — менять право на
    пробник ему нечем, и про это сказано в `note`.
    """
    tg = row.get("telegram_id")
    used = _trial_used(row)
    return {
        "id": _int(row.get("id")),
        "name": _name(row),
        "email": row.get("email"),
        "telegram_id": tg,
        "username": row.get("username"),
        "created_at": row.get("created_at"),
        "is_blocked": str(row.get("status") or "").lower() == "blocked",
        "is_trial_available": not used,
        "trial_used": used,
        "young_tg": bool(tg and _int(tg) >= _YOUNG_TG_ID),
    }


def _severity(accounts: list[dict[str, Any]], *, deliberate: bool = False) -> str:
    """high — явный абьюз; medium — стоит посмотреть; low — слабый сигнал.
    Пороги один-в-один как у нашего бэкенда: раздел один, и «высокий» на разных
    бэкендах обязан означать одно и то же."""
    trials = sum(1 for a in accounts if a["trial_used"])
    if deliberate or trials >= 3 or (trials >= 2 and len(accounts) >= 3):
        return "high"
    if trials >= 2:
        return "medium"
    return "low"


# --- сбор исходников ----------------------------------------------------------


async def _bot_users(ctx: Ctx, deadline: float) -> tuple[list[dict[str, Any]], bool]:
    """Все люди бота машинным ключом → (строки, обход оборвался по времени).

    Ошибку НЕ проглатываем: пустой список молча означал бы «подозрительных нет»,
    то есть самую опасную ложь, какую этот раздел может сказать.

    По страницам идём ТОЛЬКО по признаку «страница пришла неполной»: их `total`
    в `/users` считается неверно (на стенде отдаёт 1 при семи пользователях), и
    доверие к нему обрезало бы обход на первой же странице.
    """
    rows: list[dict[str, Any]] = []
    offset = 0
    for _ in range(_PAGES_LIMIT):
        if time.monotonic() > deadline:
            return rows, True
        resp = await ctx.admin("GET", "/users", params={"limit": _BOT_PAGE, "offset": offset})
        if resp is None:
            raise RuntimeError("бот недоступен: список пользователей не получен")
        if resp.status_code >= 400:
            raise RuntimeError(f"бот отказал в списке пользователей (код {resp.status_code})")
        try:
            body = resp.json() or {}
        except ValueError as exc:
            raise RuntimeError("бот ответил не-JSON на список пользователей") from exc
        batch = body.get("items") if isinstance(body, dict) else None
        if not isinstance(batch, list) or not batch:
            break
        rows.extend(r for r in batch if isinstance(r, dict) and r.get("id") is not None)
        offset += len(batch)
        if len(batch) < _BOT_PAGE:
            break
    return rows, False


async def _staff(ctx: Ctx) -> set[int]:
    """Кто в боте персонал. Их RBAC отдаёт ТОЛЬКО людей с ролью, поэтому запрос
    дешёвый.

    Зачем. Наш бэкенд ищет группы только среди обычных пользователей: тестовые
    аккаунты персонала лежат на одном ноутбуке и дали бы «группу» из владельца и
    его же второго аккаунта на каждом открытии экрана.

    Не смогли узнать (нет права `rbac:read`, старый бот) — считаем, что персонала
    нет. Это делает раздел шумнее, но не опаснее: лишняя группа видна глазом, а
    вот спрятать настоящую из-за второстепенного отказа было бы хуже. По той же
    причине это единственное место снимка, которое зависит от прав ЗАШЕДШЕГО
    админа: он влияет только на то, скрыть ли из выдачи персонал, и никакого
    доступа к чужим данным не открывает.
    """
    try:
        resp = await ctx.call("GET", "/cabinet/admin/rbac/users")
    except httpx.HTTPError:
        return set()
    if resp.status_code >= 400:
        return set()
    try:
        body = resp.json()
    except ValueError:
        return set()
    rows = body if isinstance(body, list) else (body or {}).get("items")
    if not isinstance(rows, list):
        return set()
    staff: set[int] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        uid = row.get("user_id")
        if uid is None:
            continue
        # Роль без активных назначений — не персонал: их же список отдаёт и
        # погашенные записи, а бывший модератор снова обычный пользователь.
        names = [str(n) for n in (row.get("role_names") or []) if str(n).strip()]
        if names:
            staff.add(_int(uid))
    return staff


async def _panel_owners(ctx: Ctx, deadline: float) -> tuple[dict[str, str], bool]:
    """Карта «кто владелец устройства» со стороны панели: и `uuid`, и числовой
    `id` пользователя → его `shortUuid`.

    Оба ключа нужны из-за версий панели: до Remnawave 2.8 устройство несло
    `userUuid`, с 2.8 — числовой `userId`. Строить карту по одному из них значит
    уронить раздел на соседней версии панели.
    """
    owners: dict[str, str] = {}
    start = 0
    for page in range(_PAGES_LIMIT):
        if time.monotonic() > deadline:
            return owners, True
        data = await ctx.panel_json(
            "/api/users", None,
            params={"size": _PANEL_PAGE, "start": start}, timeout=_PANEL_TIMEOUT,
        )
        if data is None:
            # `panel_json` любую беду превращает в default, поэтому пустоту
            # разбираем отдельно: «панель не ответила» и «в панели никого нет» —
            # разные вещи, и вторую нельзя показывать как «совпадений нет».
            raise RuntimeError(f"панель не отдала список пользователей (страница {page + 1})")
        rows = data.get("users") if isinstance(data, dict) else None
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            short = str(row.get("shortUuid") or "")
            if not short:
                continue
            if row.get("uuid"):
                owners[str(row["uuid"])] = short
            if row.get("id") is not None:
                owners[str(row["id"])] = short
        start += len(rows)
        total = _int(data.get("total")) if isinstance(data, dict) else 0
        if start >= total or len(rows) < _PANEL_PAGE:
            break
    return owners, False


async def _panel_devices(ctx: Ctx, deadline: float) -> tuple[list[dict[str, Any]], bool]:
    """Все HWID-устройства панели. Только чтение: заголовок `x-hwid` здесь не
    появляется никогда (см. шапку файла)."""
    out: list[dict[str, Any]] = []
    start = 0
    for page in range(_PAGES_LIMIT):
        if time.monotonic() > deadline:
            return out, True
        data = await ctx.panel_json(
            "/api/hwid/devices", None,
            params={"size": _DEVICE_PAGE, "start": start}, timeout=_PANEL_TIMEOUT,
        )
        if data is None:
            if page == 0:
                raise RuntimeError("панель не отдала список устройств")
            # Оборвались на середине: то, что успели, — не вся картина, и
            # молчать об этом нельзя.
            return out, True
        rows = data.get("devices") if isinstance(data, dict) else None
        if not isinstance(rows, list) or not rows:
            break
        out.extend(row for row in rows if isinstance(row, dict))
        start += len(rows)
        total = _int(data.get("total")) if isinstance(data, dict) else 0
        if start >= total or len(rows) < _DEVICE_PAGE:
            break
    return out, False


# --- снимок -------------------------------------------------------------------
#
# Всё, из чего считаются группы: люди, их устройства, их почты и кто кого привёл.
# Держим отдельно от подсчёта групп нарочно — подсчёт получается чистой функцией,
# которую видно на глаз и можно проверить без единого запроса наружу.

_snapshot_cache: tuple[float, dict[str, Any]] | None = None
_snapshot_lock = asyncio.Lock()


async def _collect(ctx: Ctx) -> dict[str, Any]:
    """Снять исходники заново."""
    deadline = time.monotonic() + _BUDGET
    warnings: list[str] = []

    rows, cut = await _bot_users(ctx, deadline)
    if cut:
        warnings.append(
            f"Список людей бота дочитать не успели — показано по первым {len(rows)}; "
            "часть совпадений могла не попасть в выборку."
        )
    staff = await _staff(ctx)

    people: dict[int, dict[str, Any]] = {}
    email_of: dict[int, str] = {}
    referred_by: dict[int, int] = {}
    short_to_uid: dict[str, int] = {}
    for row in rows:
        uid = _int(row.get("id"))
        if not uid or uid in staff:
            continue
        people[uid] = _account_view(row)
        norm = normalize_email(row.get("email"))
        if norm:
            email_of[uid] = norm
        ref = row.get("referred_by_id")
        if ref is not None:
            referred_by[uid] = _int(ref)
        # Индексируем ВСЕ подписки человека, а не только текущую: панель помнит
        # устройства и по старой ссылке, и без этого устройства человека с
        # перевыпущенной подпиской остались бы ничьи.
        subs: list[Any] = []
        if isinstance(row.get("subscriptions"), list):
            subs.extend(row["subscriptions"])
        if isinstance(row.get("subscription"), dict):
            subs.append(row["subscription"])
        for sub in subs:
            if not isinstance(sub, dict):
                continue
            key = _short_uuid(sub.get("subscription_url"))
            if key:
                short_to_uid[key] = uid

    hwid_users: dict[str, set[int]] = {}
    hwid_label: dict[str, str] = {}
    if not ctx.has_panel:
        warnings.append(
            "Панель Remnawave адаптеру не задана (REMNAWAVE_URL/REMNAWAVE_TOKEN) — "
            "совпадения по устройствам не проверялись."
        )
    else:
        try:
            owners, cut_owners = await _panel_owners(ctx, deadline)
            devices, cut_devices = await _panel_devices(ctx, deadline)
        except RuntimeError as exc:
            _log(f"устройства не собраны: {exc}")
            warnings.append(
                f"Совпадения по устройствам не проверялись: {exc}. "
                "Показаны только совпадения по почте и само-рефералы."
            )
        else:
            if cut_owners or cut_devices:
                warnings.append(
                    "Панель дочитать не успели — совпадения по устройствам "
                    "показаны не полностью."
                )
            matched = 0
            for device in devices:
                hwid = str(device.get("hwid") or "").strip()[:256]
                if not hwid:
                    continue
                owner = device.get("userUuid") or device.get("userId")
                short = owners.get(str(owner)) if owner is not None else None
                uid = short_to_uid.get(short or "")
                # Устройство, чьего владельца в боте нет (человек заведён прямо в
                # панели или это персонал), отбрасываем: показать его не под кем.
                if uid is None:
                    continue
                matched += 1
                hwid_users.setdefault(hwid, set()).add(uid)
                if hwid not in hwid_label:
                    label = _device_label(device)
                    if label:
                        hwid_label[hwid] = label
            # НИ ОДНО устройство не легло на человека бота, хотя связывать было с
            # чем (и устройства есть, и подписки с ссылками есть) — это не «чисто»,
            # это порванная связка. Так выглядит, например, адаптер, подключённый к
            # чужой панели, или бот, который отдаёт ссылку подписки на другом домене.
            # Молчать здесь опаснее всего: экран скажет «подозрительных нет» ровно
            # там, где по устройствам никого и не искали.
            if devices and short_to_uid and not matched:
                _log(
                    f"устройства не связались с людьми бота: панель отдала {len(devices)}, "
                    f"ссылок подписок у бота {len(short_to_uid)}, совпало 0"
                )
                warnings.append(
                    f"Панель отдала {len(devices)} устройств, но ни одно не удалось "
                    "связать с человеком этого бота (сверяем по ссылке подписки) — "
                    "значит, совпадения по устройствам НЕ проверены. Похоже, адаптер "
                    "смотрит не в ту панель. Показаны только совпадения по почте и "
                    "само-рефералы."
                )

    email_users: dict[str, set[int]] = {}
    for uid, norm in email_of.items():
        email_users.setdefault(norm, set()).add(uid)

    user_hwids: dict[int, set[str]] = {}
    for hwid, uids in hwid_users.items():
        for uid in uids:
            user_hwids.setdefault(uid, set()).add(hwid)

    return {
        "people": people,
        "hwid_users": hwid_users,
        "hwid_label": hwid_label,
        "email_users": email_users,
        "email_of": email_of,
        "user_hwids": user_hwids,
        "referred_by": referred_by,
        "warnings": warnings,
    }


async def _snapshot(ctx: Ctx) -> dict[str, Any]:
    """Снимок из кэша или свежий. Замок — чтобы два одновременных открытия
    экрана не устроили два одинаковых обхода панели: первый соберёт, второй
    дождётся и возьмёт готовое."""
    global _snapshot_cache

    cached = _snapshot_cache
    if cached and time.monotonic() - cached[0] < _SNAPSHOT_TTL:
        return cached[1]
    async with _snapshot_lock:
        # Пока ждали замок, снимок мог собрать сосед — проверяем ещё раз.
        cached = _snapshot_cache
        if cached and time.monotonic() - cached[0] < _SNAPSHOT_TTL:
            return cached[1]
        data = await _collect(ctx)
        _snapshot_cache = (time.monotonic(), data)
        return data


# --- сами группы --------------------------------------------------------------


def _build(snapshot: dict[str, Any], min_accounts: int, only_trial: bool) -> list[dict[str, Any]]:
    """Группы совпадений по снимку. Чистая функция: ни одного запроса наружу."""
    people: dict[int, dict[str, Any]] = snapshot["people"]

    def accounts_of(uids: Any) -> list[dict[str, Any]]:
        return sorted(
            (people[u] for u in uids if u in people), key=lambda a: a["id"]
        )

    def passes(accounts: list[dict[str, Any]], deliberate: bool = False) -> bool:
        if len(accounts) < min_accounts:
            return False
        if only_trial and not deliberate:
            # Смысл фильтра: группа интересна, когда пробником успели
            # воспользоваться хотя бы двое — иначе это просто семья на одном
            # телефоне, а не заработок на бесплатных днях.
            return sum(1 for a in accounts if a["trial_used"]) >= 2
        return True

    clusters: list[dict[str, Any]] = []

    # ── Сигнал 1: общий девайс (HWID) ─────────────────────────────────────────
    for hwid, uids in snapshot["hwid_users"].items():
        accounts = accounts_of(uids)
        if not passes(accounts):
            continue
        short = hwid[:16] + "…" if len(hwid) > 18 else hwid
        device = snapshot["hwid_label"].get(hwid)
        trials = sum(1 for a in accounts if a["trial_used"])
        clusters.append({
            "signal": "hwid",
            "key": f"{short} · {device}" if device else short,
            "device": device,
            "accounts": accounts,
            # Один физический телефон и два пробника — это уже не совпадение.
            "severity": _severity(accounts, deliberate=trials >= 2),
        })

    # ── Сигнал 2: одинаковая почта (с gmail-нормализацией) ────────────────────
    for norm, uids in snapshot["email_users"].items():
        accounts = accounts_of(uids)
        if not passes(accounts):
            continue
        clusters.append({
            "signal": "email",
            "key": norm,
            "accounts": accounts,
            "severity": _severity(accounts),
        })

    # ── Сигнал 3: само-реферал ────────────────────────────────────────────────
    #
    # У нашего бэкенда доказательство «это один человек» — общий IP. Здесь IP нет
    # (см. шапку файла), поэтому доказываем общим девайсом или общей почтой: и то
    # и другое — факт из данных. Без такого доказательства обычная рефералка
    # («привёл друга») попадала бы сюда каждым приглашением, и раздел стало бы
    # невозможно читать.
    user_hwids: dict[int, set[str]] = snapshot["user_hwids"]
    email_of: dict[int, str] = snapshot["email_of"]

    def same_person(a: int, b: int) -> bool:
        if user_hwids.get(a, set()) & user_hwids.get(b, set()):
            return True
        mail = email_of.get(a)
        return bool(mail and mail == email_of.get(b))

    groups: dict[int, list[int]] = {}
    for uid, referrer in snapshot["referred_by"].items():
        if uid not in people or referrer not in people:
            continue
        if same_person(uid, referrer):
            groups.setdefault(referrer, []).append(uid)
    for referrer, invited in groups.items():
        accounts = accounts_of([referrer, *invited])
        if not passes(accounts, deliberate=True):
            continue
        clusters.append({
            "signal": "referral",
            "key": f"referrer #{referrer}",
            "accounts": accounts,
            "severity": _severity(accounts, deliberate=True),
        })

    order = {"high": 0, "medium": 1, "low": 2}
    clusters.sort(key=lambda c: (order.get(c["severity"], 3), -len(c["accounts"])))
    return clusters


# --- что раздел говорит о себе словами ----------------------------------------

# Постоянное объяснение: чем этот раздел ищет поверх «Бедолаги» и чего у неё нет.
# Кабинет показывает его вместо своей подсказки про IP — иначе экран обещал бы
# поиск по адресам входа, которого здесь не будет никогда.
_NOTE = (
    "Группы аккаунтов с совпадающими признаками: общий девайс (HWID из панели "
    "Remnawave), «одинаковая» почта с учётом gmail-точек и +алиасов, само-рефералы "
    "(пригласивший и приглашённый на одном девайсе или с одной почтой). "
    "Групп по общему IP здесь нет: этот бот адресов входа не хранит вовсе — в его "
    "журнале активности запись «вход в кабинет» это выданный refresh-токен без "
    "адреса и без устройства. Кнопки «Снять/Вернуть триал» этот бот тоже не "
    "поддерживает: права на пробник у него на человеке нет, доступность он решает "
    "на лету (не было платной подписки и нет активной). "
    "Данные снимаются с бота и панели не чаще раза в 5 минут. "
    "Автодействий нет — решение за вами."
)


# --- ручка --------------------------------------------------------------------
#
# ПРОВЕРКА ПЕРЕД ОБЪЯВЛЕНИЕМ (правило адаптера): наши модули грузятся последними
# и молча перехватывают чужой слот — так промо-баннер однажды отобрал у бота его
# персональные офферы. У «Бедолаги» этого пути нет (он числится в `todo_admin`
# карты маршрутов), но проверяем не на слово.
#
# Отказываемся именно ImportError'ом: compose.py ловит его у каждого модуля
# отдельно и печатает в лог, адаптер при этом остаётся жив, а наш путь честно
# отвечает 501. Любое другое исключение здесь уронило бы всю админку из-за одного
# раздела.
if ("GET", "/api/admin/abuse/trials") in HANDLERS:
    raise ImportError(
        "слот GET /api/admin/abuse/trials уже занят другим модулем — детект абьюза "
        "не подключён, чтобы не отобрать чужую ручку"
    )


@handler("GET", "/api/admin/abuse/trials")
async def abuse_trials(ctx: Ctx) -> dict[str, Any]:
    """Группы подозрительных совпадений. Только чтение."""
    min_accounts = max(2, min(20, _int(ctx.query.get("min_accounts"), 2)))
    only_trial = _flag(ctx.query.get("only_trial"), True)

    # ГЕЙТ ПРАВ — первым делом и на каждый запрос. Их кабинетная ручка под правом
    # `users:read`: не админ или админ без права — получит их же 401/403, и мы
    # даже не пойдём за данными. Дальше в дело идёт машинный ключ адаптера,
    # который сам по себе прав не проверяет вовсе.
    await ctx.json("/cabinet/admin/users", {}, params={"limit": 1, "offset": 0})

    if not ctx.can_admin:
        # Без машинного ключа не собрать ни почту, ни ссылки подписки — то есть
        # ни одного сигнала. Пустой список тут означал бы «всё чисто», а это
        # ровно та тихая ложь, которой в этом разделе быть не должно.
        raise NotAvailable(
            "Детект абьюза недоступен: адаптеру не выдан машинный ключ бота "
            "(BEDOLAGA_ADMIN_TOKEN). Без него не получить ни почты людей, ни ссылок "
            "их подписок — а значит не с чем сверять устройства из панели."
        )

    try:
        snapshot = await _snapshot(ctx)
    except RuntimeError as exc:
        # Люди — основа раздела: без них показывать нечего, и «пусто» соврало бы.
        # Это беда сегодняшнего часа (бот молчит), а не «бэкенд так не умеет»,
        # поэтому 502 с причиной, а не 501.
        raise _fail(502, f"Детект абьюза не собрался: {exc}") from exc

    clusters = _build(snapshot, min_accounts, only_trial)
    result: dict[str, Any] = {
        "clusters": clusters,
        "total": len(clusters),
        "note": _NOTE,
    }
    if snapshot["warnings"]:
        result["warning"] = " ".join(snapshot["warnings"])
    return result
