"""Админка: «Поддержка» (тикеты) и «Уведомления» (лента админам).

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ — то же правило, что у `admin_users.py` и `admin_catalog.py`:
админских ручек в нашем контракте под сорок, переводят их параллельно, и один
файл на всех — гарантированный конфликт. Регистрация общая, тем же декоратором
`handler`; сам себя модуль не импортирует — его подхватывает `compose.py` по
маске `admin_*.py` (и строка COPY в `adapter/Dockerfile`, её добавляет тот, кто
собирает образ). Нет файла в образе — пути честно отвечают 501, раздел
«Поддержка» не появляется в меню.

ОТКУДА ДАННЫЕ. Всё — из их КАБИНЕТНОГО API `/cabinet/admin/tickets*` сессией
самого админа (`ctx.call` внутри `_send`), а не служебным токеном адаптера.
Это вопрос безопасности, а не удобства: права здесь режет их RBAC отдельными
правами `tickets:read` / `tickets:reply` / `tickets:close`, и человек без них
получает их же 403. Со служебным токеном (X-API-Key) отвечать в чужие тикеты
смог бы любой, кто вошёл в кабинет. Токен здесь не используется вообще: всё,
что нужно этим двум экранам, их кабинетное API отдаёт само.

ЧТО ПЕРЕВЕДЕНО:
  • список обращений с фильтром по статусу, карточка с перепиской,
    ОТВЕТ админа и смена статуса — то есть раздел рабочий целиком, а не витрина;
  • лента уведомлений админам (колокольчик в шапке админки + экран
    «Уведомления»).

ЧЕГО ЗДЕСЬ НЕТ И ПОЧЕМУ (ручки не регистрируются вовсе → main.py отвечает 501):
  • `DELETE /api/admin/notifications` («Очистить историю»). Удаления этих
    записей у них нет ни в кабинетном, ни в машинном API — есть только
    «отметить прочитанным» (`/notifications/read-all`), а это другое действие:
    кнопка сказала бы «очищено», список бы опустел на экране и вернулся при
    следующем заходе. Пусть лучше кнопка честно откажет.
  • `GET|PUT /api/admin/notifications/settings` (тумблер «дублировать на
    телефон»). У них есть похожий на вид `cabinet_admin_notifications_enabled`
    в настройках тикетов, но это НЕ web-push: их флаг решает, СОЗДАВАТЬ ли
    записи ленты вообще (app/database/crud/ticket_notification.py:201,245).
    Свести их вместе значит сделать так, что выключение «пуша на телефон»
    молча выключит саму историю — ровно то, что подпись под тумблером обещает
    не делать. Плюс web-push у адаптера нет вовсе (пути `/api/push/*` в todo),
    то есть управлять нечем. Экран это переживает штатно: он читает настройку с
    `.catch(() => {})`, тумблер остаётся неактивным (AdminNotificationsPage.tsx:36,111).

ПРО РАЗДЕЛ «Уведомления» В МЕНЮ. Пункт живёт в группе прав `settings`
(AdminLayout.tsx:129), а группа включается только целиком — там два десятка
других экранов. Пока они не переведены, пункта в меню не будет, но сама ручка
не бесполезна: колокольчик в шапке админки показывается всем админам без
оглядки на разделы (AdminLayout.tsx:251,318) и ведёт на этот же экран.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from compose import Ctx, UpstreamError, handler


def _int(value: Any, default: int = 0) -> int:
    """Их числа приходят и строкой, и None. Своя копия вместо общего хелпера
    намеренно: тащить приватную функцию из соседнего файла значит связать
    модули, которые правятся порознь."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --- разговор с их API ------------------------------------------------------


def _fail(status: int, detail: str) -> UpstreamError:
    """Отказ самого адаптера в той же форме, что и ошибка бота."""
    return UpstreamError(httpx.Response(status, json={"detail": detail}))


def _detail(resp: httpx.Response) -> str:
    """Текст ошибки из их ответа: `detail` бывает строкой, объектом и списком
    (валидация pydantic), а кабинет умеет печатать только строку."""
    try:
        body = resp.json()
    except ValueError:
        return "Не удалось выполнить действие"
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("detail") or "Запрос отклонён")
    if isinstance(detail, list):
        return "; ".join(
            str(d.get("msg") if isinstance(d, dict) else d) for d in detail
        ) or "Запрос отклонён"
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    return "Не удалось выполнить действие"


# Их отказы по-английски и техническим языком, а кабинет печатает `detail` как
# есть. Переводим только то, что реально встречается на этих ручках; незнакомое
# уходит наверх как было — английская правда лучше нашей догадки.
_ERRORS = {
    "Ticket not found": "Обращение не найдено",
    "Notification not found": "Уведомление не найдено",
}


def _ru(text: str, need: str) -> str:
    """Их отказ по-русски. `need` — право, которого требует ЭТА ручка.

    Их RBAC отвечает `Permission denied: <причина>`, где причина — «Permission
    not granted by any role» или «No active roles assigned»
    (app/services/permission_service.py:240,252). Имя недостающего права в
    тексте отсутствует, а знаем его мы — оно зашито в вызывающей ручке. Без
    имени владелец бота не поймёт, что именно выдать в своей админке.
    """
    text = text.strip()
    known = _ERRORS.get(text)
    if known:
        return known
    if text.startswith("Permission denied"):
        reason = text.split(":", 1)[1].strip() if ":" in text else ""
        if reason == "No active roles assigned":
            return "Недостаточно прав: в боте этой учётке не выдано ни одной роли"
        return f"Недостаточно прав в боте: нужно право «{need}»"
    return text


async def _send(ctx: Ctx, method: str, path: str, need: str, **kw: Any) -> dict[str, Any]:
    """Запрос к их кабинетному API от имени самого админа (его сессией).

    Отдельно от `ctx.json`, потому что здесь нужны две вещи, которых там нет:
    перевод их отказов (`_ru`) и то, что 5xx НЕ показывается админу как есть —
    их внутренняя ошибка это «не получилось, попробуйте позже», а не текст для
    человека. Коды 4xx доезжают до кабинета своими: по 401 он отправляет на
    вход, 403 показывает как причину отказа.
    """
    try:
        resp = await ctx.call(method, path, **kw)
    except httpx.HTTPError as exc:
        raise _fail(502, f"Бэкенд недоступен: {exc}") from exc
    if resp.status_code >= 500:
        raise _fail(502, "Бот ответил ошибкой — попробуйте позже")
    if resp.status_code >= 400:
        raise _fail(resp.status_code, _ru(_detail(resp), need))
    try:
        body = resp.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


# --- статусы ----------------------------------------------------------------
#
# У них четыре статуса в нижнем регистре (open/pending/answered/closed), у нас
# три (cabinet/src/api/support.ts:4). `pending` — «пользователь ответил, ждём
# поддержку»: для экрана это тот же «открыт», и это ровно те обращения, которые
# админу нужнее всего. Карта закрытая: незнакомое значение схлопывается в
# «open», иначе StatusPill ищет его в словаре и падает на undefined
# (AdminSupportPage.tsx:17-20) — экран поддержки белеет.
_STATUS_IN = {"open": "open", "pending": "open", "answered": "answered", "closed": "closed"}

# Обратно (наш статус → их) — для смены статуса. `pending` мы не ставим никогда:
# нашего эквивалента у него нет, и админ через кабинет такой статус не выбирает.
_STATUS_OUT = {"open": "open", "answered": "answered", "closed": "closed"}

# Фильтр «Открытые» обязан захватывать и `pending`: их API фильтрует по одному
# значению, поэтому спрашиваем двумя запросами и склеиваем.
_STATUS_QUERY = {"open": ("open", "pending"), "answered": ("answered",), "closed": ("closed",)}

# Порядок в списке — как у нашего бэкенда (web/endpoints/admin/support.py):
# сперва то, что ждёт ответа, потом отвеченное, в конце закрытое; внутри — по
# свежести. Их API отдаёт просто «по updated_at desc», так что раскладываем сами:
# иначе на одинаковых данных разные бэкенды дают разный экран.
_ORDER = {"open": 0, "answered": 1, "closed": 2}

# Их предел на страницу — 100 (`per_page` le=100). Наш экран пагинации не имеет
# и хочет список целиком, поэтому страницы собираем сами.
_PER_PAGE = 100

# Сколько страниц готовы забрать за один заход. Их список отсортирован по
# свежести, так что предел режет самые старые обращения, а не нужные. Совсем
# без предела один заход в раздел на старой установке — это десятки запросов к
# боту; сколько тикетов на самом деле, видно в `total` ответа.
_MAX_PAGES = 5

# Их же предел на длину ответа (AdminReplyRequest.message max_length=4000).
# Проверяем заранее только чтобы вместо их 422 от pydantic админ увидел
# человеческую строку.
_REPLY_MAX = 4000


def _user(u: Any) -> dict[str, Any]:
    """Автор обращения в нашей форме (AdminTicketUser).

    Имя у них разложено на части и целиком может отсутствовать (почтовые
    учётки) — тогда экран подставит почту или «#id» сам (AdminSupportPage.tsx:29).
    """
    if not isinstance(u, dict):
        # Пользователь у них nullable: удалённая учётка оставляет тикет сиротой.
        return {"id": 0, "name": None, "email": None, "telegram_id": None}
    name = " ".join(p for p in (u.get("first_name") or "", u.get("last_name") or "") if p).strip()
    return {
        "id": _int(u.get("id")),
        "name": name or u.get("username") or None,
        "email": u.get("email"),
        "telegram_id": u.get("telegram_id"),
    }


def _message(m: dict[str, Any]) -> dict[str, Any]:
    """Сообщение переписки (TicketThread.tsx ждёт id/sender/body/created_at)."""
    text = (m.get("message_text") or "").strip()
    if not text:
        # Вложение у них — telegram file_id. Скачать его умеет только их
        # машинное API по X-API-Key, а наш тред показывает текст: пустой «пузырь»
        # выглядит как потерянное сообщение, поэтому подписываем, где смотреть.
        # Подпись автора, если она есть, честнее нашей.
        text = (m.get("media_caption") or "").strip()
        if not text and m.get("has_media"):
            text = "📎 Вложение — откройте в Telegram-боте"
    return {
        "id": m.get("id"),
        "sender": "admin" if m.get("is_from_admin") else "user",
        "body": text,
        "created_at": m.get("created_at"),
    }


def _subject(t: dict[str, Any]) -> str:
    """Заголовок строки списка. Пустая тема у них возможна, и пустая строка в
    списке некликабельна на вид — подписываем номером, как делает их же карточка
    (admin_tickets.py:439, `Ticket #{id}`)."""
    return (t.get("title") or "").strip() or f"Обращение #{_int(t.get('id'))}"


def _ticket(t: dict[str, Any]) -> dict[str, Any]:
    """Строка списка обращений (AdminTicketListItem)."""
    return {
        "id": _int(t.get("id")),
        "subject": _subject(t),
        "status": _STATUS_IN.get(str(t.get("status") or "").lower(), "open"),
        "created_at": t.get("created_at"),
        # У них updated_at обязателен, но у старых записей бывает пустым —
        # тогда «последнее движение» это создание.
        "updated_at": t.get("updated_at") or t.get("created_at"),
        "messages_count": _int(t.get("messages_count")),
        "user": _user(t.get("user")),
    }


async def _page(ctx: Ctx, status: str | None, page: int) -> dict[str, Any]:
    params: dict[str, Any] = {"page": page, "per_page": _PER_PAGE}
    if status:
        params["status"] = status
    return await _send(
        ctx, "GET", "/cabinet/admin/tickets", "tickets:read", params=params
    )


async def _by_status(ctx: Ctx, status: str | None) -> tuple[list[dict[str, Any]], int]:
    """Все обращения одного статуса (или все подряд) — их страницами, наш список.

    Первую страницу берём строго: она же и проверка прав (`tickets:read`, не
    админ → их 403 уедет в кабинет). Остальные — параллельно, и тоже строго:
    молча потерянная страница это пропавшие обращения, а не «данных нет».
    """
    first = await _page(ctx, status, 1)
    items = [t for t in (first.get("items") or []) if isinstance(t, dict)]
    total = _int(first.get("total"), len(items))
    pages = min(_int(first.get("pages"), 1), _MAX_PAGES)
    if pages > 1:
        rest = await asyncio.gather(*(_page(ctx, status, p) for p in range(2, pages + 1)))
        for chunk in rest:
            items.extend(t for t in (chunk.get("items") or []) if isinstance(t, dict))
    return items, total


@handler("GET", "/api/admin/support/tickets")
async def tickets(ctx: Ctx) -> dict[str, Any]:
    """Список обращений с фильтром по статусу.

    Фильтр «Открытые» разворачивается в два их запроса (`open` и `pending`):
    иначе из списка выпадают обращения, где пользователь ответил и ждёт
    поддержку, — то есть именно те, ради которых экран открывают.
    """
    want = (ctx.query.get("status") or "").strip().lower()
    if want and want not in _STATUS_QUERY:
        # Их API на незнакомый статус отвечает пустым списком, и фильтр молча
        # показывал бы «тикетов нет» вместо «такого статуса не бывает».
        raise _fail(400, f"Неизвестный статус обращения: {want}")

    statuses: tuple[str | None, ...] = _STATUS_QUERY[want] if want else (None,)
    results = await asyncio.gather(*(_by_status(ctx, s) for s in statuses))

    rows: list[dict[str, Any]] = []
    total = 0
    for items, count in results:
        rows.extend(items)
        total += count

    items = [_ticket(t) for t in rows]
    # Два прохода, а не один составной ключ: время нужно по убыванию, группа —
    # по возрастанию, а `reverse` в python общий на весь ключ. Сортировка
    # устойчивая, поэтому порядок внутри группы сохраняется от первого прохода.
    items.sort(key=lambda t: str(t["updated_at"] or ""), reverse=True)
    items.sort(key=lambda t: _ORDER.get(t["status"], 0))
    # `total` сверх контракта: экран печатает длину списка, а знать, что за
    # пределом `_MAX_PAGES` осталось ещё, полезно — иначе обрезка невидима.
    return {"items": items, "total": total}


@handler("GET", "/api/admin/support/tickets/{id}")
async def ticket(ctx: Ctx) -> dict[str, Any]:
    """Карточка обращения с перепиской (AdminTicketDetail)."""
    data = await _send(
        ctx, "GET", f"/cabinet/admin/tickets/{ctx.param('id')}", "tickets:read"
    )
    messages = [m for m in (data.get("messages") or []) if isinstance(m, dict)]
    return {
        "id": _int(data.get("id")),
        "subject": _subject(data),
        "status": _STATUS_IN.get(str(data.get("status") or "").lower(), "open"),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at") or data.get("created_at"),
        "user": _user(data.get("user")),
        # Порядок задаёт их ручка (сортирует по created_at), тред рисует как есть.
        "messages": [_message(m) for m in messages],
    }


@handler("POST", "/api/admin/support/tickets/{id}/messages")
async def reply(ctx: Ctx) -> dict[str, Any]:
    """Ответ админа в обращение — то, ради чего раздел вообще нужен.

    Что это делает на их стороне (оператору важно знать заранее): статус
    обращения становится «отвечен», пользователю уходит уведомление в Telegram
    и запись в его ленту кабинета (admin_tickets.py:494-526). Отдельной кнопки
    «ответить молча» у них нет.

    Вложения не отправляем: их поля ждут telegram `file_id`, а браузер такого
    не даёт — файл сначала нужно залить в Telegram. Наш тред файлов и не
    предлагает.
    """
    body = str(ctx.payload().get("body") or "").strip()
    if not body:
        raise _fail(400, "Пустой ответ отправить нельзя")
    if len(body) > _REPLY_MAX:
        raise _fail(400, f"Ответ длиннее {_REPLY_MAX} символов — сократите")
    await _send(
        ctx,
        "POST",
        f"/cabinet/admin/tickets/{ctx.param('id')}/reply",
        "tickets:reply",
        json={"message": body},
    )
    # Их ответ — само сообщение; экран после отправки перезапрашивает карточку
    # целиком, поэтому наверх отдаём то, что ждёт контракт.
    return {"success": True}


@handler("POST", "/api/admin/support/tickets/{id}/status")
async def set_status(ctx: Ctx) -> dict[str, Any]:
    """Сменить статус (экран шлёт сюда только «закрыть»)."""
    want = str(ctx.payload().get("status") or "").strip().lower()
    if want not in _STATUS_OUT:
        raise _fail(400, f"Неизвестный статус обращения: {want or '—'}")
    data = await _send(
        ctx,
        "POST",
        f"/cabinet/admin/tickets/{ctx.param('id')}/status",
        "tickets:close",
        json={"status": _STATUS_OUT[want]},
    )
    # Что получилось, говорит их ответ, а не то, что мы просили: иначе кабинет
    # покажет закрытие, которого не произошло.
    actual = _STATUS_IN.get(str(data.get("status") or "").lower())
    return {"success": True, "status": actual or want}


# --- уведомления админам -----------------------------------------------------
#
# У «Бедолаги» лента админам одна и она про обращения: `new_ticket` (создано) и
# `user_reply` (пользователь ответил), см. ticket_notification.py:196-256.
# Регистраций и оплат в ней нет и быть не может — про них их бот пишет владельцу
# в Telegram, ленты кабинета для этого у него не существует. Подпись на экране
# обещает больше («регистрации, оплаты, тикеты и т.п.»), но подмешивать туда
# выдуманные записи нельзя: показываем ровно то, что бот действительно завёл.
_NOTIF_TITLE = {
    "new_ticket": "Новое обращение",
    "user_reply": "Ответ пользователя",
    # Админской ленте не свойственно, но их таблица общая на две стороны —
    # пусть незнакомый тип не превращается в безымянную карточку.
    "admin_reply": "Ответ поддержки",
}

# Их предел на запрос — 100 (`limit` le=100), наш экран просит 200
# (AdminNotificationsPage.tsx:28). Добираем страницами через `offset`.
_NOTIF_CHUNK = 100
# Столько же, сколько отдаёт наш бэкенд (web/endpoints/admin/notifications.py).
_NOTIF_MAX = 500


@handler("GET", "/api/admin/notifications")
async def notifications(ctx: Ctx) -> dict[str, Any]:
    """Лента уведомлений админам: экран «Уведомления» и колокольчик в шапке."""
    limit = max(1, min(_NOTIF_MAX, _int(ctx.query.get("limit"), 100)))

    rows: list[dict[str, Any]] = []
    while len(rows) < limit:
        chunk = min(_NOTIF_CHUNK, limit - len(rows))
        data = await _send(
            ctx,
            "GET",
            "/cabinet/admin/tickets/notifications",
            "tickets:read",
            params={"limit": chunk, "offset": len(rows)},
        )
        page = [n for n in (data.get("items") or []) if isinstance(n, dict)]
        rows.extend(page)
        # Короткая страница — записи кончились. Это же и защита от вечного
        # цикла, если их ручка вдруг перестанет уважать offset.
        if len(page) < chunk:
            break

    return {
        "items": [
            {
                "id": _int(n.get("id")),
                "title": _NOTIF_TITLE.get(str(n.get("notification_type") or ""), "Поддержка"),
                # Готовый текст вида «Новый тикет #3: …» они собирают сами.
                "body": n.get("message") or "",
                # Маршрута на конкретное обращение в админке нет (App.tsx:475) —
                # ведём в раздел целиком. Экран это поле сейчас не рисует, но
                # контракт его требует, и подставлять чужой адрес нельзя.
                "url": "/admin/support",
                "created_at": n.get("created_at"),
            }
            for n in rows
        ]
    }
