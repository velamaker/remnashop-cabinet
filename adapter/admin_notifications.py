"""Админка: «Уведомления» (/admin/notifications) — лента событий кабинета.

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ — общее правило адаптера (см. `admin_panel.py`,
`admin_subapp.py`): админских ручек в контракте под сорок, пишут их несколькими
руками сразу, и один файл на всех — гарантированный конфликт. Регистрация общая,
тем же декоратором `handler`; сам себя модуль не импортирует — его подхватывает
`compose.py` по маске `admin_*.py`, в образ он попадает строкой `COPY admin*.py`
в `adapter/Dockerfile`. Нет файла в образе — работает то, что было до него.

ОТКУДА ДАННЫЕ. Из СВОЕЙ памяти адаптера (`state.py`, таблица settings) — и это
не выбор из двух источников. Ленты событий кабинета у «Бедолаги» нет вовсе: всё,
что у неё есть, — уведомления по обращениям (`/cabinet/admin/tickets/
notifications`, только «новый тикет» и «ответ пользователя»), про регистрации,
оплаты и прочее её бот пишет владельцу в Telegram, а не в базу. Значит события,
которые видит САМ адаптер (заморозка, покупка через кабинет, недоставленное
уведомление), кроме нас записать некому.

ПОЧЕМУ ЭТОТ МОДУЛЬ ЗАБИРАЕТ СЕБЕ `GET /api/admin/notifications`. Эту ручку уже
отдаёт `admin_support.py` — там она собрана из тикетных уведомлений. Реестр
`HANDLERS` — обычный словарь, и «кто импортировался последним, тот и в реестре»:
модули грузятся по алфавиту, `admin_notifications` идёт РАНЬШЕ `admin_support`,
то есть наша регистрация была бы молча затёрта. Поэтому мы импортируем соседа
ЯВНО (после этого его ручки уже в реестре), запоминаем его обработчик и
регистрируем свой поверх — а внутри зовём запомненный и подмешиваем его записи к
своим. Так порядок загрузки перестаёт что-либо решать, чужой файл не тронут, и
тикетные уведомления из ленты не пропадают. Соседа в образе нет — лента просто
состоит из наших событий.

ЧТО ЗДЕСЬ ЕСТЬ (ручки, которые зовёт кабинет, см. `cabinet/src/api/admin.ts`,
`notificationsAdminApi`):
  • `GET /api/admin/notifications?limit=N` — лента для экрана и для колокольчика
    в шапке админки (`AdminNotifBell.tsx`);
  • `DELETE /api/admin/notifications` — «Очистить»;
  • `POST /api/admin/notifications/read` — отметка прочитанным (о ней ниже).

ЧЕГО ЗДЕСЬ НЕТ И ПОЧЕМУ (ручки не регистрируются вовсе → main.py отвечает 501):
  • `GET|PUT /api/admin/notifications/settings` — тумблер «дублировать на телефон
    (web-push)». Web-push у адаптера нет вообще (пути `/api/push/*` не
    переведены), то есть управлять нечем: сохранённый тумблер обещал бы пуш на
    телефон и молча ничего не делал. Похожий на вид флаг «Бедолаги»
    (`cabinet_admin_notifications_enabled`) — не про пуш: он решает, СОЗДАВАТЬ ли
    записи ленты обращений вообще, и свести их вместе значило бы, что выключение
    «пуша» гасит саму историю. Экран это переживает штатно: настройку он читает
    с `.catch(() => {})`, тумблер остаётся неактивным
    (`AdminNotificationsPage.tsx:36,111`). Регистрировать ручку ради красивого
    501 нельзя ещё и потому, что `compose.admin_can_write` считает права по
    реестру: зарегистрированный PUT включил бы кнопки сохранения по всему
    разделу «Настройки».

КАК СЮДА ПОПАДАЮТ СОБЫТИЯ. Одной функцией `record(...)` (см. ниже) из тех
модулей, которые эти события и видят. Здесь она только объявлена: подключение
вызовов — отдельная работа владельца задачи, чтобы не править чужие файлы.

ПРО ОТМЕТКУ ПРОЧИТАННЫМ. Сегодняшний кабинет её не зовёт: колокольчик считает
непрочитанное по метке в localStorage браузера (`admin_notif_seen`). Метка на
стороне адаптера всё равно нужна — она общая на все устройства и переживает
чистку браузера, поэтому лента отдаёт `read` у каждой записи и общий `unread`, а
ручка `POST .../read` их проставляет. Полей сверх контракта кабинет не замечает
(лишние ключи он игнорирует), так что старый экран работает как работал.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatchcase
from typing import Any

import httpx

from compose import HANDLERS, Ctx, NotAvailable, UpstreamError, handler

_PATH = "/api/admin/notifications"


def _log(message: str) -> None:
    # Тот же префикс, что у остального адаптера: лог контейнера общий.
    print(f"[adapter] уведомления: {message}", flush=True)


# --- сосед с тикетными уведомлениями -----------------------------------------
#
# Импорт ЯВНЫЙ и до регистрации своих ручек — почему именно так, см. шапку файла.
# Молча переживаем его отсутствие: файл может не попасть в образ или сломаться, и
# из-за этого наша лента пропадать не должна.
try:
    import admin_support  # noqa: F401  (нужен ради его регистрации в HANDLERS)
except Exception as exc:  # noqa: BLE001 — сосед необязателен
    _log(f"лента обращений недоступна: модуль admin_support не подключён: {exc!r}")

# Запоминаем ДО своей регистрации: сразу после неё по этому ключу будет уже наш
# обработчик, и вызов превратился бы в бесконечную рекурсию.
_TICKETS = HANDLERS.get(("GET", _PATH))


# --- хранилище ----------------------------------------------------------------
#
# Всё лежит в ОДНОЙ строке настроек (`state.settings`), а не в своей таблице:
# файл базы общий на весь адаптер, и заводить в нём таблицы под каждый раздел
# запрещено правилами проекта. Одна строка — ещё и атомарная запись: счётчик,
# сами записи и обе метки меняются вместе, без полусохранённого состояния.
_KEY = "admin_notifications"

# Сколько записей храним. Лента не может расти бесконечно: строка настроек
# читается и переписывается целиком на каждое событие, а экран всё равно просит
# не больше 200 (`AdminNotificationsPage.tsx:28`). Двести записей — это десятки
# килобайт JSON, для sqlite ничто; тысячи означали бы мегабайтную строку,
# которую переписывают на каждую заморозку.
_MAX_ITEMS = 200

# Границы полей. Обрезаем на записи, а не на выдаче: иначе один модуль с
# простыней текста в `body` раздул бы общую строку так, что пострадали бы все
# остальные события.
_TITLE_MAX = 120
_BODY_MAX = 500
_URL_MAX = 200
_KIND_MAX = 40

# Окно дедупликации по умолчанию. Одинаковое событие, пришедшее второй раз за
# пять минут, — это почти всегда повтор (перезапуск контейнера посреди задачи,
# повторный запрос кабинета, двойной клик), а не второе событие. Окно нарочно
# короткое: сделай его сутками — и вторая честная заморозка того же человека в
# тот же день потерялась бы. Кому нужно жёстче, передаёт свой `key` и своё окно.
_DEDUP_WINDOW = 300.0

# Их предел на запрос — 100 (`limit` le=100), наш экран просит 200. Верхняя
# граница — как у нашего бэкенда (`web/endpoints/admin/notifications.py`).
_LIMIT_DEFAULT = 100
_LIMIT_MAX = 500

# Право «Бедолаги» на правку настроек бота: раздел «Уведомления» в кабинете живёт
# в группе «Настройки», и очистка чужой истории — не то действие, которое должно
# быть доступно админу «только для просмотра».
_EDIT_RIGHT = "settings:edit"


def _fail(status: int, detail: str) -> UpstreamError:
    """Отказ самого адаптера в той же форме, что и ошибка бота."""
    return UpstreamError(httpx.Response(status, json={"detail": detail}))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: Any) -> datetime | None:
    """Их и наши метки времени → сравнимые даты.

    Свои мы пишем ISO с зоной, а «Бедолага» отдаёт `created_at` из postgres —
    сплошь и рядом БЕЗ зоны (naive UTC). Сравнивать такие с aware нельзя,
    python бросит TypeError прямо посреди сортировки ленты, поэтому naive
    считаем за UTC — это правда для их базы.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


# Записи без даты (их у «Бедолаги» теоретически может не быть) считаем самыми
# старыми: и в сортировке, и при очистке — «когда это случилось, неизвестно»
# ближе к «давно», чем к «только что».
_OLDEST = datetime.fromtimestamp(0, timezone.utc)


def _usable(state: Any) -> bool:
    return state is not None and bool(getattr(state, "available", False))


def _empty() -> dict[str, Any]:
    return {"seq": 0, "items": [], "read_until": None, "cutoff": None}


def _load(state: Any) -> dict[str, Any]:
    """Лента из своей памяти. Битое или чужое содержимое — как пустое: падать на
    испорченной строке настроек значило бы уронить и колокольчик, и экран."""
    if not _usable(state):
        return _empty()
    raw = state.get_setting(_KEY, None)
    if not isinstance(raw, dict):
        return _empty()
    items = [item for item in (raw.get("items") or []) if isinstance(item, dict)]
    try:
        seq = int(raw.get("seq") or 0)
    except (TypeError, ValueError):
        seq = 0
    return {
        "seq": max(seq, len(items)),
        "items": items[:_MAX_ITEMS],
        "read_until": raw.get("read_until") if isinstance(raw.get("read_until"), str) else None,
        "cutoff": raw.get("cutoff") if isinstance(raw.get("cutoff"), str) else None,
    }


def _save(state: Any, data: dict[str, Any]) -> None:
    if not _usable(state):
        return
    data["items"] = data["items"][:_MAX_ITEMS]
    state.set_setting(_KEY, data)


def _clip(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.split()) if "\n" in text or "\r" in text else text.strip()
    return text[:limit]


# --- запись событий (то, ради чего модуль существует) -------------------------


def record(
    target: Any,
    kind: str,
    title: str,
    body: str = "",
    url: str = "",
    key: str | None = None,
    dedup_seconds: float = _DEDUP_WINDOW,
) -> bool:
    """Записать событие в ленту владельца. Единственный вход для других модулей.

    Зовут так (в обработчике — `ctx`, в фоновой задаче — тоже `ctx`):

        from admin_notifications import record
        record(ctx, "freeze", "Пауза подписки", f"{email} поставил подписку на паузу")

    `target` — `Ctx` или сама `state.State`: у Ctx берём `ctx.state` сами, чтобы
    вызывающему не приходилось помнить, что именно передавать (перепутал — и
    событие тихо не записалось бы, а это ровно та тишина, от которой лента и
    заводится).

    `kind` — короткое машинное имя события (`freeze`, `purchase`, `undelivered`):
    в ленте оно не показывается, но по нему видно происхождение записи в
    диагностике и по нему же считается дедупликация.

    `key` — свой ключ дедупликации, когда «одно и то же» шире, чем совпадение
    текста: например `f"freeze:{user_id}:{day}"`. Без него ключ собирается из
    kind+title+body, а окно — `dedup_seconds` (0 выключает дедупликацию совсем).

    Возвращает True, если запись легла в ленту; False — если это повтор или
    писать некуда (своя база недоступна). ИСКЛЮЧЕНИЙ НЕ БРОСАЕТ НИКОГДА: она
    зовётся из середины заморозки и покупки, и сорвать их из-за журнала нельзя —
    беда уедет в лог контейнера.

    Функция намеренно ОБЫЧНАЯ, а не `async`: лента читается и переписывается
    целиком, и без единственного `await` внутри две записи в однопроцессном
    адаптере не могут перемешаться и затереть друг друга. Обращение к своей
    sqlite занимает микросекунды, событийный цикл этого не замечает.
    """
    try:
        state = getattr(target, "state", target)
        if not _usable(state):
            # Молчим не всегда: без своей базы не работает и заморозка, про это
            # уже сказано в других местах, а лог на каждое событие был бы шумом.
            return False

        kind_text = _clip(kind, _KIND_MAX) or "event"
        title_text = _clip(title, _TITLE_MAX)
        body_text = _clip(body, _BODY_MAX)
        if not title_text and not body_text:
            # Пустая карточка в ленте — мусор, который нечем объяснить.
            _log(f"событие «{kind_text}» без текста не записано")
            return False

        mark = _clip(key, 200) or f"{kind_text}|{title_text}|{body_text}"
        data = _load(state)
        now = _now()

        if dedup_seconds > 0:
            since = now - timedelta(seconds=float(dedup_seconds))
            for item in data["items"]:
                if item.get("key") != mark:
                    continue
                when = _parse(item.get("created_at"))
                if when is not None and when >= since:
                    return False

        data["seq"] = int(data["seq"]) + 1
        data["items"].insert(
            0,
            {
                # Отрицательный id — чтобы никогда не столкнуться с id тикетных
                # уведомлений «Бедолаги» (там обычный автоинкремент): лента
                # общая, а ключ записи на экране должен быть уникальным.
                "id": -data["seq"],
                "kind": kind_text,
                "title": title_text,
                "body": body_text,
                "url": _clip(url, _URL_MAX),
                "created_at": now.isoformat(),
                "key": mark,
                "read": False,
            },
        )
        _save(state, data)
        return True
    except Exception as exc:  # noqa: BLE001 — журнал не имеет права ломать вызвавшего
        _log(f"событие «{kind}» не записано: {exc!r}")
        return False


# --- права --------------------------------------------------------------------


async def _require_admin(ctx: Ctx) -> None:
    """Лента — наша память, а не ручка бота, поэтому право спрашиваем явно:
    иначе события кабинета читал бы любой, кто знает путь."""
    if not ctx.token:
        raise _fail(401, "Требуется вход")
    me = await ctx.json("/cabinet/auth/me/is-admin", {}, soft=True) or {}
    if not me.get("is_admin"):
        raise _fail(403, "Недостаточно прав")


async def _require_edit(ctx: Ctx) -> None:
    """Очистка истории — необратимое действие над общей лентой владельца.

    Спрашиваем ИХ право (`settings:edit`), а не выдумываем своё: раздел
    «Уведомления» в кабинете живёт в группе «Настройки», и админ «только для
    просмотра» стирать чужую историю не должен. Сравнение по их правилу — право
    пользователя это ШАБЛОН (`*:*`, `settings:*`). Не смогли спросить — считаем,
    что права нет.
    """
    data = await ctx.json("/cabinet/auth/me/permissions", {}, soft=True) or {}
    granted = data.get("permissions")
    ok = isinstance(granted, list) and any(
        isinstance(p, str) and fnmatchcase(_EDIT_RIGHT, p) for p in granted
    )
    if not ok:
        raise _fail(403, f"Недостаточно прав в боте: нужно право «{_EDIT_RIGHT}»")


# --- сборка ленты -------------------------------------------------------------


def _detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return f"код {resp.status_code}"
    value = body.get("detail") if isinstance(body, dict) else None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return f"код {resp.status_code}"


async def _ticket_items(ctx: Ctx) -> tuple[list[dict[str, Any]], str]:
    """Тикетные уведомления соседа → (записи, причина отказа).

    401/403 пропускаем наверх как есть: это ответ ИХ RBAC про этого админа
    (кабинет по 401 отправляет на вход, 403 показывает причиной), и поведение
    экрана остаётся ровно тем, каким было до появления нашей ленты. Остальное —
    их 5xx, оборванная сеть, поломка в самом соседе — глушим до причины строкой:
    наши собственные события к обращениям отношения не имеют, и терять их из-за
    чужого куска экрана неправильно. Причина уходит в лог и в поле
    `tickets_error` ответа (кабинет лишние поля игнорирует, а в диагностике
    curl'ом видно, почему в ленте нет тикетов).
    """
    if _TICKETS is None:
        return [], ""
    try:
        data = await _TICKETS(ctx)
    except UpstreamError as exc:
        if exc.resp.status_code in (401, 403):
            raise
        reason = _detail(exc.resp)
    except Exception as exc:  # noqa: BLE001 — см. docstring
        reason = repr(exc)
    else:
        items = (data or {}).get("items") or []
        return [item for item in items if isinstance(item, dict)], ""
    _log(f"лента обращений не прочиталась: {reason}")
    return [], reason


def _shape(item: dict[str, Any], read: bool) -> dict[str, Any]:
    """Форма записи для кабинета (`AdminNotification`) плюс наши `read`/`kind`."""
    return {
        "id": item.get("id"),
        "title": item.get("title") or "",
        "body": item.get("body") or "",
        "url": item.get("url") or "",
        "created_at": item.get("created_at"),
        "read": read,
        "kind": item.get("kind") or "ticket",
    }


@handler("GET", _PATH)
async def notifications(ctx: Ctx) -> dict[str, Any]:
    """Лента: наши события кабинета плюс тикетные уведомления «Бедолаги»."""
    await _require_admin(ctx)

    try:
        limit = int(ctx.query.get("limit") or _LIMIT_DEFAULT)
    except (TypeError, ValueError):
        limit = _LIMIT_DEFAULT
    limit = max(1, min(_LIMIT_MAX, limit))

    data = _load(ctx.state)
    cutoff = _parse(data.get("cutoff"))
    read_until = _parse(data.get("read_until"))

    rows: list[tuple[datetime, dict[str, Any]]] = []
    for item in data["items"]:
        when = _parse(item.get("created_at")) or _OLDEST
        rows.append((when, _shape(item, bool(item.get("read")))))

    foreign, error = await _ticket_items(ctx)
    for item in foreign:
        when = _parse(item.get("created_at")) or _OLDEST
        # Очистка удаляет наши записи физически, а тикетные удалить нечем — у
        # «Бедолаги» есть только «отметить прочитанным». Поэтому вместо удаления
        # прячем всё, что было до очистки: иначе кнопка «Очистить» врала бы —
        # список пустел на экране и возвращался при следующем заходе. Сами
        # уведомления при этом целы и видны в их разделе обращений.
        if cutoff is not None and when <= cutoff:
            continue
        rows.append((when, _shape(item, read_until is not None and when <= read_until)))

    rows.sort(key=lambda row: row[0], reverse=True)
    items = [row[1] for row in rows[:limit]]

    result: dict[str, Any] = {
        "items": items,
        # Сверх контракта: экран считает непрочитанное сам по метке в браузере,
        # но общая на все устройства цифра полезнее, а лишнее поле кабинету не
        # мешает.
        "unread": sum(1 for item in items if not item["read"]),
    }
    if error:
        result["tickets_error"] = error
    return result


@handler("POST", "/api/admin/notifications/read")
async def notifications_read(ctx: Ctx) -> dict[str, Any]:
    """Отметить прочитанным: всю ленту или одну запись (`{"id": -3}`).

    Форма повторяет пользовательскую ленту кабинета (`POST /api/notifications/
    read` с необязательным id) — выдумывать для админской свою незачем.

    Тикетные записи «Бедолаги» здесь НЕ трогаем: их «прочитано» живёт в её
    разделе обращений, меняется отдельным правом (`tickets:settings`) и значит
    там другое — «оператор разобрал обращение». Для нашей ленты хватает общей
    метки времени: всё, что старше момента отметки, считается прочитанным.
    """
    await _require_admin(ctx)
    if not _usable(ctx.state):
        raise NotAvailable(
            "Отметку хранить негде: своя память адаптера недоступна "
            "(переменная ADAPTER_STATE, каталог должен быть доступен на запись)"
        )

    payload = ctx.payload()
    data = _load(ctx.state)

    if payload.get("id") is not None:
        try:
            wanted = int(payload["id"])
        except (TypeError, ValueError):
            raise _fail(400, "id уведомления должен быть числом") from None
        found = False
        for item in data["items"]:
            if item.get("id") == wanted:
                item["read"] = True
                found = True
                break
        if not found:
            # Чужую (тикетную) запись поштучно мы отметить не можем — честнее
            # сказать это, чем ответить «готово» и ничего не сделать.
            raise _fail(404, "Такого уведомления нет в ленте адаптера")
    else:
        for item in data["items"]:
            item["read"] = True
        data["read_until"] = _now().isoformat()

    _save(ctx.state, data)
    return {"ok": True}


@handler("DELETE", _PATH)
async def notifications_clear(ctx: Ctx) -> dict[str, Any]:
    """Очистить историю.

    Наши записи удаляются насовсем; тикетные — прячутся по метке (см. GET):
    удалять их у «Бедолаги» нечем, а кнопка, после которой список возвращается,
    хуже отсутствующей. Счётчик id намеренно не сбрасываем: одинаковые id у
    старой и новой записи путали бы отметку прочитанным.
    """
    await _require_admin(ctx)
    await _require_edit(ctx)
    if not _usable(ctx.state):
        raise NotAvailable(
            "Очищать нечего и негде: лента живёт в своей памяти адаптера, а она "
            "недоступна (переменная ADAPTER_STATE, каталог должен быть доступен "
            "на запись)"
        )

    data = _load(ctx.state)
    now = _now().isoformat()
    _save(
        ctx.state,
        {"seq": data["seq"], "items": [], "read_until": now, "cutoff": now},
    )
    return {"ok": True}


# Самопроверка формы хранения при импорте не нужна, а вот отдельный запуск
# `python admin_notifications.py` удобен: печатает, что модуль видит соседа и
# каким получается пустой ответ, — без контейнера и без бота.
if __name__ == "__main__":  # pragma: no cover
    print("сосед admin_support:", "есть" if _TICKETS else "нет")
    print(json.dumps(_empty(), ensure_ascii=False))
