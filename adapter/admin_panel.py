"""Админка: раздел «RemnaWave» — ноды, система, хосты, инбаунды.

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ — то же правило, что у `admin_users.py`, `admin_catalog.py`
и `admin_support.py`: админских ручек в контракте под сорок, переводят их
параллельно, и один файл на всех — гарантированный конфликт. Регистрация общая,
тем же декоратором `handler`; сам себя модуль не импортирует — его подхватывает
`compose.py` по маске `admin_*.py`. В образе файл появляется строкой COPY в
`adapter/Dockerfile` (её добавляет тот, кто собирает образ); нет файла — пути
честно отвечают 501 и раздел «RemnaWave» в меню не появляется вовсе.

ОТКУДА ДАННЫЕ И ПОЧЕМУ ИМЕННО ОТТУДА. Источника здесь два, и выбор между ними —
не вкусовщина, а вопрос того, кто решает про права.

  1. ИХ кабинетное API `/cabinet/admin/remnawave/*` сессией самого админа
     (`ctx.call` внутри `_send`). Это ОСНОВНОЙ источник: их RBAC режет доступ
     правами `remnawave:read` (чтение) и `remnawave:manage` (действия над
     нодами), и человек без них получает их же 403. Панель ходит под одним
     ключом с полными правами — если бы список нод и тем более рестарт шли
     через него, «право перезапускать ноды» в их админке превратилось бы в
     украшение: перезапустить смог бы любой, кто вошёл в кабинет.

  2. Панель Remnawave напрямую (`ctx.panel_json`) — только ЧТЕНИЕ и только там,
     где их кабинетного API не существует вовсе:
       • хосты — ручки `/cabinet/admin/remnawave/hosts` у них нет ни в каком
         виде (проверено по их openapi: есть nodes/inbounds/squads/system,
         хостов нет);
       • версия панели для шапки экрана — их API её не отдаёт ни одной ручкой;
       • поля ноды, которых нет в их списке (порт, «подключается»).
     И даже в этих трёх случаях панель дёргается ТОЛЬКО ПОСЛЕ успешного ответа
     их же админской ручки: сперва их RBAC говорит «этому админу читать
     RemnaWave можно», и лишь потом адаптер добирает недостающее.

  ИЗМЕНЯЮЩИЕ действия (включить/выключить/перезапустить ноду) идут ИСКЛЮЧИТЕЛЬНО
  через их API. Панель для них не используется ни как основной путь, ни как
  запасной — запасного пути к мутации с полными правами быть не должно.

ЧТО ПЕРЕВЕДЕНО: все четыре вкладки экрана «RemnaWave» (Ноды, Система, Хосты,
Инбаунды) и все кнопки управления нодами, включая «Рестарт всех нод». То есть
раздел рабочий целиком, а не витрина.

ЧЕГО ЗДЕСЬ НЕТ И ПОЧЕМУ (пути не регистрируются → main.py отвечает 501):
  • `GET|PUT /api/admin/server-status` — экран «Статус сервиса». Это НЕ данные
    панели и не данные бота, а настройка НАШЕГО кабинета: какие ноды показывать
    в публичном блоке статуса, показывать ли его гостю, какие хосты считать
    заглушками. У «Бедолаги» такой настройки нет вообще, так что хранить её
    пришлось бы самому адаптеру. Проблема не в хранении, а в том, что применять
    её негде: публичный статус адаптер собирает из ИХ списка стран/сквадов
    (`/cabinet/subscription/countries`, см. `_nodes_from_countries` в
    compose.py), а не из нод панели — у сквадов нет UUID нод, по которым
    работает `visible_nodes`, и нет хостов, к которым относятся
    `service_keywords`. Сохранённые тумблеры не поменяли бы ровным счётом
    ничего: экран говорил бы «Сохранено», а публичный статус вёл бы себя
    по-прежнему. Настройка, которая ничего не настраивает, хуже честного
    отказа, поэтому ручки нет.
    Отдельно стоит знать: даже будь она написана, пункт меню не появился бы —
    «Статус сервиса» лежит в группе прав `settings` (AdminLayout.tsx:119), а
    группа включается только целиком, там два десятка других экранов.
  • `GET /api/admin/server-status/nodes` — список нод для того же экрана.
    Данные настоящие (те же ноды панели), но без настройки выше экран до них не
    доходит: он первым делом читает конфиг и на ошибке рисует «Ошибка» вместо
    формы. Отдавать ручку, которую некому вызвать, смысла нет.
  • `cert_days` / `cert_checked_at` в карточке ноды — срок сертификата. У нас
    его пишет наш собственный мониторинг (taskiq node_health.json), у
    «Бедолаги» такого мониторинга нет, а панель срок серта не отдаёт. Поле
    просто не заполняем — карточка рисует плитку «Сертификат» только когда
    значение пришло, так что экран выглядит штатно.

ПРО РОЛЬ «ТОЛЬКО ПРОСМОТР». Наш бэкенд затирает адреса нод и хостов для
PREVIEW-админа (`_redact.py`). Здесь этого нет намеренно: у «Бедолаги» своя
система прав, и «можно ли этому человеку видеть инфраструктуру» решает она —
кто получил `remnawave:read`, тот и видит. Своя маскировка поверх чужого RBAC
означала бы, что адаптер придумывает права, которых в боте не объявлено.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from compose import Ctx, NotAvailable, UpstreamError, handler

# Права их RBAC. Держим именами, потому что в отказ их бот имя права не кладёт —
# см. `_ru` ниже.
_READ = "remnawave:read"
_MANAGE = "remnawave:manage"


def _int(value: Any, default: int = 0) -> int:
    """Их числа приходят и строкой, и None (`totalBytesLifetime` — строка).
    Своя копия вместо общего хелпера намеренно: тащить приватную функцию из
    соседнего файла значит связать модули, которые правятся порознь."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _opt_int(value: Any) -> int | None:
    """То же, но «не пришло» остаётся «не пришло».

    Ноль и отсутствие на этом экране — разные вещи: 0 байт трафика карточка
    рисует, а неизвестный трафик прячет (`node.traffic_used_bytes != null`).
    Подставив здесь ноль, мы бы утверждали, что нода не прокачала ничего.
    """
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    """Строка или None. Пустую строку схлопываем: у панели «нет значения»
    выражается и null, и «», а экран проверяет только на null."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _pick(*values: Any) -> Any:
    """Первое непустое из нескольких форм одного и того же поля.

    Панель за версии переезжала (см. `_hardware`), и одно значение приходится
    искать в двух-трёх местах. Перебор вместо `or` — потому что `or` считает
    пустыми ноль и False, а «0 ядер» и «не пришло» здесь разные вещи.
    """
    for value in values:
        if value is not None and value != "":
            return value
    return None


# Сколько ждём панель. Их API — основной источник, панель на подхвате, и
# зависшая панель не должна утаскивать за собой весь экран: общий дедлайн
# обработчика 30 секунд, и без своего предела запрос к боту (уже успешный)
# пропал бы вместе с ним. Там, где панель — единственный источник (хосты),
# предел больше: ждать данные осмысленно, ждать «довесок» — нет.
_PANEL_EXTRA_TIMEOUT = 8.0
_PANEL_DATA_TIMEOUT = 15.0


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
    "RemnaWave API is not configured": "Панель Remnawave в боте не настроена",
    "RemnaWave service is not available": "Модуль Remnawave в боте недоступен",
    "Failed to get RemnaWave statistics": "Панель не отдала статистику",
    "Node not found": "Нода не найдена в панели",
    "Failed to enable node": "Панель не приняла команду «включить»",
    "Failed to disable node": "Панель не приняла команду «выключить»",
    "Failed to restart node": "Панель не приняла команду «перезапустить»",
    "Failed to restart all nodes": "Панель не приняла команду «перезапустить всё»",
}


def _ru(text: str, need: str) -> str:
    """Их отказ по-русски. `need` — право, которого требует ЭТА ручка.

    Их RBAC отвечает `Permission denied: <причина>`, где причина — «Permission
    not granted by any role» или «No active roles assigned»
    (app/services/permission_service.py). Имя недостающего права в тексте
    отсутствует, а знаем его мы — оно зашито в вызывающей ручке. Без имени
    владелец бота не поймёт, что именно выдать в своей админке.
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

    Отдельно от `ctx.json`, потому что здесь нужны три вещи, которых там нет:
    перевод их отказов (`_ru`); 5xx НЕ показывается админу как есть (их
    внутренняя ошибка — это «не получилось, попробуйте позже», а не текст для
    человека); и отдельная обработка 503.

    Про 503. У них это не поломка, а осмысленный отказ «панель не настроена»
    (`_ensure_configured` в их admin_remnawave.py). Схлопнув его в общее «бот
    ответил ошибкой», мы спрятали бы единственную подсказку, что чинить, —
    поэтому текст 503 доезжает до админа переведённым.

    Коды 4xx доезжают до кабинета своими: по 401 он отправляет на вход, 403
    показывает как причину отказа.
    """
    try:
        resp = await ctx.call(method, path, **kw)
    except httpx.HTTPError as exc:
        raise _fail(502, f"Бэкенд недоступен: {exc}") from exc
    if resp.status_code == 503:
        raise _fail(502, _ru(_detail(resp), need))
    if resp.status_code >= 500:
        raise _fail(502, "Бот ответил ошибкой — попробуйте позже")
    if resp.status_code >= 400:
        raise _fail(resp.status_code, _ru(_detail(resp), need))
    try:
        body = resp.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _items(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Их списки всегда `{items: [...], total: N}`."""
    return [row for row in (data.get("items") or []) if isinstance(row, dict)]


# --- ноды -------------------------------------------------------------------
#
# ФОРМА ОТВЕТА ПАНЕЛИ 2.7+. Раньше нода отдавала железо и версии плоскими полями
# (`cpuCount`, `cpuModel`, `totalRam`, `xrayVersion`, `nodeVersion`) — их и ждёт
# карточка ноды в кабинете. В 2.7 панель их убрала и заменила двумя объектами:
#   versions: {node, xray}
#   system:   {info: {arch, cpus, cpuModel, memoryTotal, ...}, stats: {...}}
# Проверено по схеме живой панели 2.8.1 (dist/src/modules/nodes/dtos) и по
# клиенту самой «Бедолаги» (app/external/remnawave_api.py). Поэтому раскладываем
# новую форму обратно в поля контракта — иначе плитки «ЦПУ» и «ОЗУ» в карточке
# ноды пустуют на любой современной панели. Старую плоскую форму тоже читаем:
# на панели 2.6 новых объектов не будет, а поля — будут.


def _hardware(row: dict[str, Any], system: Any, versions: Any) -> dict[str, Any]:
    """Железо и версии ноды из обеих форм ответа панели."""
    info = system.get("info") if isinstance(system, dict) else None
    info = info if isinstance(info, dict) else {}
    vers = versions if isinstance(versions, dict) else {}
    # Сначала новая форма (2.7+), потом старая плоская — на старой панели
    # объектов нет, а поля есть. Ключи в двух написаниях: панель отвечает
    # camelCase, их кабинетное API — snake_case.
    return {
        "cpu_count": _opt_int(_pick(info.get("cpus"), row.get("cpuCount"), row.get("cpu_count"))),
        "cpu_model": _text(_pick(info.get("cpuModel"), row.get("cpuModel"), row.get("cpu_model"))),
        "total_ram": _opt_int(_pick(info.get("memoryTotal"), row.get("totalRam"), row.get("total_ram"))),
        "xray_version": _text(_pick(vers.get("xray"), row.get("xrayVersion"), row.get("xray_version"))),
        "node_version": _text(_pick(vers.get("node"), row.get("nodeVersion"), row.get("node_version"))),
    }


def _node_from_bot(n: dict[str, Any]) -> dict[str, Any]:
    """Нода в нашей форме из ИХ списка (`/cabinet/admin/remnawave/nodes`).

    Порта и «подключается» в их списке нет вовсе — они остаются None и, если
    панель адаптеру доступна, добираются из неё (см. `_enrich`). Пустое значение
    экран переживает: адрес печатается без порта, статус «Подключение» просто не
    показывается.
    """
    row = {
        "uuid": str(n.get("uuid") or ""),
        "name": n.get("name") or "",
        "address": n.get("address") or "",
        "port": None,
        "is_connected": bool(n.get("is_connected")),
        "is_disabled": bool(n.get("is_disabled")),
        "is_connecting": None,
        "users_online": _int(n.get("users_online")),
        "traffic_used_bytes": _opt_int(n.get("traffic_used_bytes")),
        "traffic_limit_bytes": _opt_int(n.get("traffic_limit_bytes")),
        "xray_uptime": _opt_int(n.get("xray_uptime")),
        "country_code": _text(n.get("country_code")),
        "last_status_message": _text(n.get("last_status_message")),
        "updated_at": n.get("updated_at"),
    }
    row.update(_hardware(n, n.get("system"), n.get("versions")))
    return row


def _node_from_panel(n: dict[str, Any]) -> dict[str, Any]:
    """То же, но из ответа панели (camelCase)."""
    row = {
        "uuid": str(n.get("uuid") or ""),
        "name": n.get("name") or "",
        "address": n.get("address") or "",
        "port": _opt_int(n.get("port")),
        "is_connected": bool(n.get("isConnected")),
        "is_disabled": bool(n.get("isDisabled")),
        "is_connecting": bool(n.get("isConnecting")),
        "users_online": _int(n.get("usersOnline")),
        "traffic_used_bytes": _opt_int(n.get("trafficUsedBytes")),
        "traffic_limit_bytes": _opt_int(n.get("trafficLimitBytes")),
        "xray_uptime": _opt_int(n.get("xrayUptime")),
        "country_code": _text(n.get("countryCode")),
        "last_status_message": _text(n.get("lastStatusMessage")),
        "updated_at": n.get("updatedAt"),
    }
    row.update(_hardware(n, n.get("system"), n.get("versions")))
    return row


async def _panel_nodes(ctx: Ctx) -> dict[str, dict[str, Any]]:
    """Ноды панели по uuid. Пусто — панель не настроена или не ответила.

    Панель у адаптера и у бота по построению одна и та же (установщик берёт
    REMNAWAVE_API_URL/API_KEY из окружения бота), но проверять это адаптеру
    нечем. Поэтому склейка идёт строго по uuid: не совпало — ничего не добираем,
    а не подставляем данные соседней панели.
    """
    raw = await ctx.panel_json("/api/nodes", timeout=_PANEL_EXTRA_TIMEOUT)
    if not isinstance(raw, list):
        return {}
    return {
        str(n["uuid"]): n
        for n in raw
        if isinstance(n, dict) and n.get("uuid")
    }


def _enrich(row: dict[str, Any], panel: dict[str, Any]) -> dict[str, Any]:
    """Добираем в ноду то, чего их список не отдал (порт, «подключается», железо).

    Заполняем ТОЛЬКО пустые поля: там, где ответили оба, прав их API — оно и
    есть источник, а панель здесь на подхвате. False и 0 пустыми не считаются,
    иначе «нода не подключается» превратилось бы в значение из панели.
    """
    extra = _node_from_panel(panel)
    for key, value in extra.items():
        if row.get(key) is None and value is not None:
            row[key] = value
    return row


@handler("GET", "/api/admin/remnawave/nodes")
async def nodes(ctx: Ctx) -> dict[str, Any]:
    """Вкладка «Ноды»: состояние, онлайн, трафик, железо.

    Их список — основной источник и одновременно проверка прав (`remnawave:read`,
    не админ → их 403 уедет в кабинет). Панель дёргается после него и только
    ради полей, которых у них нет.

    ПОЧЕМУ ЕСТЬ ЗАПАСНОЙ ПУТЬ. Их сервис ловит любую ошибку панели внутри себя и
    возвращает ПУСТОЙ список (`get_all_nodes` в remnawave_service.py: `except
    Exception: return []`). То есть при заминке панели экран показал бы «Ноды не
    найдены» — то же самое, что у установки без нод. Поэтому пустой ответ
    перепроверяем панелью: если она видит ноды, показываем их. Права при этом
    уже проверены их же ручкой выше, а выдумывания нет — это те же ноды из того
    же места, куда ходит их бот.
    """
    data = await _send(ctx, "GET", "/cabinet/admin/remnawave/nodes", _READ)
    rows = [_node_from_bot(n) for n in _items(data) if n.get("uuid")]

    panel = await _panel_nodes(ctx)
    if rows:
        for row in rows:
            found = panel.get(row["uuid"])
            if found:
                _enrich(row, found)
    elif panel:
        rows = [_node_from_panel(n) for n in panel.values()]

    # Порядок задаёт сам экран (сортирует по числу онлайн), поэтому отдаём как
    # пришло — не изобретаем второй порядок поверх их.
    return {"nodes": rows}


# --- действия над нодами -----------------------------------------------------
#
# Только через их API и только с правом `remnawave:manage`. Панель здесь не
# используется даже как запасной путь: её ключ полноправный, и любой обход их
# RBAC означал бы, что кнопка «Рестарт» работает у того, кому её не давали.


async def _action(ctx: Ctx, action: str) -> dict[str, Any]:
    await _send(
        ctx,
        "POST",
        f"/cabinet/admin/remnawave/nodes/{ctx.param('uuid')}/action",
        _MANAGE,
        json={"action": action},
    )
    # Их ответ (`success`, `message`, `is_disabled`) кабинету не нужен: после
    # действия экран перезапрашивает список нод целиком.
    return {"success": True}


@handler("POST", "/api/admin/remnawave/nodes/{uuid}/restart")
async def node_restart(ctx: Ctx) -> dict[str, Any]:
    """Перезапуск xray на ноде. Разрывает соединения её пользователей —
    их клиенты переподключаются сами, но обрыв заметен."""
    return await _action(ctx, "restart")


@handler("POST", "/api/admin/remnawave/nodes/{uuid}/enable")
async def node_enable(ctx: Ctx) -> dict[str, Any]:
    return await _action(ctx, "enable")


@handler("POST", "/api/admin/remnawave/nodes/{uuid}/disable")
async def node_disable(ctx: Ctx) -> dict[str, Any]:
    """Выключение ноды в панели: она перестаёт принимать подключения и пропадает
    из статуса сервиса у пользователей."""
    return await _action(ctx, "disable")


@handler("POST", "/api/admin/remnawave/nodes/restart-all")
async def nodes_restart_all(ctx: Ctx) -> dict[str, Any]:
    """«Рестарт всех нод» — разом по всей инфраструктуре.

    Тело не шлём: у них поле `force_restart` необязательное и по умолчанию
    False, а «мягкий» перезапуск — ровно то, что делает наш бэкенд
    (RestartAllNodesRequestBodyDto без аргументов). Отправлять force, которого
    кнопка не обещает, нельзя.
    """
    await _send(ctx, "POST", "/cabinet/admin/remnawave/nodes/restart-all", _MANAGE)
    return {"success": True}


# --- система -----------------------------------------------------------------
#
# Экран ждёт форму нашего бэкенда: `metadata` от панели и `stats` — её же
# статистику, но в snake_case (наш бэкенд отдаёт разобранные модели remnapy).
# Панель по HTTP отвечает camelCase, поэтому раскладываем руками.
#
# ПОЧЕМУ ИСТОЧНИК — ИХ `/status`, А НЕ ИХ ЖЕ `/system`. У «Бедолаги» две ручки
# про то же самое. `/system` отдаёт СВОЮ переработку (`server_info`,
# `users_by_status`, `traffic_periods`), где нет двух вещей, которые экран
# показывает: физических ядер и `totalBytesLifetime` («N ТБ lifetime» на плитке
# «Хостов онлайн»). `/status` же кладёт ответ панели целиком, как получил
# (`connection.system_info` = `/api/system/stats`), — из него собирается вся
# карточка без потерь. Право у обеих одно и то же (`remnawave:read`).

# Версия панели меняется раз в месяц, а экран обновляется каждые 30 секунд —
# держим её в памяти, чтобы не бить по панели ради строчки в шапке.
_META_TTL = 300.0
_meta_cache: tuple[float, dict[str, Any]] | None = None


async def _metadata(ctx: Ctx) -> dict[str, Any]:
    """Версия панели для шапки экрана.

    Их API версию не отдаёт ни одной ручкой (в openapi «Бедолаги» её нет), а у
    панели она лежит в `/api/system/metadata`. Панели может не быть — тогда
    версия остаётся неизвестной и шапка её просто не печатает
    (`if (d.metadata?.version)`), а не показывает выдуманную.
    """
    global _meta_cache
    now = time.monotonic()
    if _meta_cache and now - _meta_cache[0] < _META_TTL:
        return _meta_cache[1]
    raw = await ctx.panel_json("/api/system/metadata", timeout=_PANEL_EXTRA_TIMEOUT)
    if not isinstance(raw, dict):
        return {"version": None}
    build = raw.get("build")
    if isinstance(build, dict):
        # У панели `build` — объект {time, number}, у нас в контракте строка.
        # Берём номер сборки: это единственное, что вообще похоже на строку.
        build = build.get("number")
    meta = {"version": _text(raw.get("version")), "build": _text(build)}
    _meta_cache = (now, meta)
    return meta


def _stats(info: dict[str, Any]) -> dict[str, Any]:
    """Статистика панели в форме нашего контракта.

    Блоки, которых панель не прислала, НЕ подставляем нулями, а опускаем: экран
    печатает «—» на отсутствующем блоке и «0 ядер»/«0%» на нулевом. Первое —
    правда, второе — враньё про мёртвый сервер.
    """
    out: dict[str, Any] = {}

    cpu = info.get("cpu")
    if isinstance(cpu, dict) and cpu.get("cores") is not None:
        out["cpu"] = {
            "cores": _int(cpu.get("cores")),
            "physical_cores": _opt_int(cpu.get("physicalCores")),
        }

    mem = info.get("memory")
    # Ноль в total — это не «мало памяти», а «панель не измерила»: экран делит на
    # него, считая процент занятости.
    if isinstance(mem, dict) and _int(mem.get("total")) > 0:
        out["memory"] = {
            "total": _int(mem.get("total")),
            "used": _int(mem.get("used")),
            "free": _int(mem.get("free")),
        }

    if info.get("uptime") is not None:
        out["uptime"] = _int(info.get("uptime"))

    users = info.get("users")
    if isinstance(users, dict):
        counts = users.get("statusCounts")
        out["users"] = {
            "total_users": _int(users.get("totalUsers")),
            # Ключи статусов (ACTIVE/LIMITED/…) экран печатает как есть — это
            # словарь самой панели, придумывать в нём нечего.
            "status_counts": {
                str(k): _int(v) for k, v in counts.items()
            } if isinstance(counts, dict) else {},
        }

    online = info.get("onlineStats")
    if isinstance(online, dict):
        out["online_stats"] = {
            "online_now": _int(online.get("onlineNow")),
            "last_day": _int(online.get("lastDay")),
            "last_week": _int(online.get("lastWeek")),
            "never_online": _int(online.get("neverOnline")),
        }

    panel_nodes = info.get("nodes")
    if isinstance(panel_nodes, dict):
        out["nodes"] = {
            "total_online": _int(panel_nodes.get("totalOnline")),
            # Панель отдаёт lifetime строкой (число не влезает в JS number) —
            # строкой же и передаём, экран сам приводит его к числу.
            "total_bytes_lifetime": str(panel_nodes.get("totalBytesLifetime") or "0"),
        }

    return out


@handler("GET", "/api/admin/remnawave/system")
async def system(ctx: Ctx) -> dict[str, Any]:
    """Вкладка «Система»: версия панели, её железо, аптайм и сводка по людям."""
    data = await _send(ctx, "GET", "/cabinet/admin/remnawave/status", _READ)

    # Их ручка отвечает 200 даже когда панель недоступна: «не подключились» —
    # это поле в теле, а не код ответа. Без явной проверки экран показал бы
    # пустые плитки и молчал бы о причине.
    if not data.get("is_configured"):
        raise _fail(502, _text(data.get("configuration_error")) or "Панель Remnawave в боте не настроена")
    conn = data.get("connection")
    conn = conn if isinstance(conn, dict) else {}
    if conn.get("status") != "connected":
        # `message` у них уже по-русски («Ошибка API: …»), и это ровно то, что
        # админу нужно прочитать. Адрес панели (`api_url`) не передаём: экрану
        # он не нужен, а лишний раз печатать адрес инфраструктуры незачем.
        raise _fail(502, _text(conn.get("message")) or "Панель Remnawave не отвечает")

    info = conn.get("system_info")
    info = info if isinstance(info, dict) else {}
    return {"metadata": await _metadata(ctx), "stats": _stats(info)}


# --- инбаунды ----------------------------------------------------------------


def _inbound(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "uuid": str(row.get("uuid") or ""),
        "tag": row.get("tag") or "",
        "type": row.get("type") or "",
        "network": _text(row.get("network")),
        "security": _text(row.get("security")),
        "port": _opt_int(row.get("port")),
    }


async def _inbounds(ctx: Ctx) -> list[dict[str, Any]]:
    """Инбаунды из ИХ API — их форма уже совпадает с нашей."""
    data = await _send(ctx, "GET", "/cabinet/admin/remnawave/inbounds", _READ)
    return [_inbound(row) for row in _items(data)]


@handler("GET", "/api/admin/remnawave/inbounds")
async def inbounds(ctx: Ctx) -> dict[str, Any]:
    """Вкладка «Инбаунды»."""
    return {"inbounds": await _inbounds(ctx)}


# --- хосты -------------------------------------------------------------------


@handler("GET", "/api/admin/remnawave/hosts")
async def hosts(ctx: Ctx) -> dict[str, Any]:
    """Вкладка «Хосты» — единственная, где источник только панель.

    Ручки хостов у «Бедолаги» нет ни в кабинетном API, ни в машинном: в её
    openapi есть nodes, inbounds, squads, system — хостов нет вовсе. Поэтому
    список берётся из панели, но СНАЧАЛА спрашивается их API: успешный ответ
    `/cabinet/admin/remnawave/inbounds` означает, что их RBAC разрешил этому
    админу читать раздел RemnaWave. Заодно это и есть справочник для колонки
    «Инбаунд» — иначе она пустует всегда (см. ниже).
    """
    inbound_by_uuid = {row["uuid"]: row for row in await _inbounds(ctx) if row["uuid"]}

    if not ctx.has_panel:
        raise _fail(
            502,
            "Список хостов есть только в панели Remnawave, а её адрес адаптеру "
            "не задан (REMNAWAVE_URL/REMNAWAVE_TOKEN)",
        )
    raw = await ctx.panel_json("/api/hosts", timeout=_PANEL_DATA_TIMEOUT)
    if not isinstance(raw, list):
        raise _fail(502, "Панель Remnawave не отдала список хостов")

    items = []
    for h in raw:
        if not isinstance(h, dict):
            continue
        # У хоста панель кладёт в `inbound` только два UUID
        # (configProfileUuid + configProfileInboundUuid), без тега и типа —
        # проверено по схеме живой панели 2.8.1. Экран же печатает `inbound.tag`,
        # поэтому связываем по UUID со справочником инбаундов. Не нашли —
        # оставляем null, и колонка честно показывает прочерк.
        link = h.get("inbound") if isinstance(h.get("inbound"), dict) else {}
        found = inbound_by_uuid.get(str(link.get("configProfileInboundUuid") or ""))
        # У 2.8 метки хоста — список `tags`, у прежних версий одно поле `tag`.
        tags = h.get("tags")
        items.append(
            {
                "uuid": str(h.get("uuid") or ""),
                "remark": h.get("remark") or "",
                "address": h.get("address") or "",
                "port": _int(h.get("port")),
                "is_disabled": bool(h.get("isDisabled")),
                "security_layer": _text(h.get("securityLayer")),
                "inbound": {"tag": found["tag"], "type": found["type"]} if found else None,
                "tag": _text(h.get("tag")) or (_text(tags[0]) if isinstance(tags, list) and tags else None),
            }
        )
    return {"hosts": items}


# --- «Статус сервиса»: настройка САМОГО кабинета ------------------------------
#
# Это не данные бота: тумблер показа блока, видимость гостям, привязка к подписке
# и список нод — свойства кабинета, а у нашего бэкенда они лежат в assets/. У
# чужого бота такого хранилища нет и быть не может, но и гасить раздел нельзя:
# по правилу владельца прячем только то, что боту ПРИНАДЛЕЖИТ. Поэтому настройку
# держит сам адаптер, в своей базе (той же, где паузы подписок).

_STATUS_KEY = "server_status"
_STATUS_DEFAULT = {
    # Дефолт совпадает с нашим бэкендом: блок включён, привязан к подписке,
    # гостям не показывается, ноды и слова-заглушки не заданы.
    "enabled": True,
    "bind_to_subscription": True,
    "guest_visible": False,
    "visible_nodes": [],
    "service_keywords": [],
}


async def _require_admin(ctx: Ctx) -> None:
    """Пускать только администратора бота.

    Остальные админские ручки защищены сами собой: они ходят в их API, и права
    проверяет их RBAC. Эта — единственная, что читает и пишет СВОЁ хранилище и
    ни к кому не обращается, поэтому спрашивать права обязана явно. Без этого
    настройку кабинета мог бы прочитать и переписать любой (проверено: без
    сессии отдавала 200).
    """
    if not ctx.token:
        raise UpstreamError(httpx.Response(401, json={"detail": "Требуется вход"}))
    me = await ctx.json("/cabinet/auth/me/is-admin", {}, soft=True) or {}
    if not me.get("is_admin"):
        raise UpstreamError(httpx.Response(403, json={"detail": "Недостаточно прав"}))


def _status_config(ctx: Ctx) -> dict[str, Any]:
    stored = {}
    if ctx.state is not None and ctx.state.available:
        stored = ctx.state.get_setting(_STATUS_KEY, {}) or {}
    return {**_STATUS_DEFAULT, **(stored if isinstance(stored, dict) else {})}


@handler("GET", "/api/admin/server-status")
async def server_status_config(ctx: Ctx) -> dict[str, Any]:
    await _require_admin(ctx)
    return _status_config(ctx)


@handler("PUT", "/api/admin/server-status")
async def server_status_update(ctx: Ctx) -> dict[str, Any]:
    await _require_admin(ctx)
    if ctx.state is None or not ctx.state.available:
        raise NotAvailable(
            "Настройку некуда сохранить: у адаптера нет доступа к своему хранилищу"
        )
    body = ctx.payload()
    config = _status_config(ctx)
    # Меняем только присланное: экран шлёт частичное обновление (Partial<>),
    # и затирать остальное значениями по умолчанию нельзя.
    for key in _STATUS_DEFAULT:
        if key not in body:
            continue
        value = body[key]
        if key in ("visible_nodes", "service_keywords"):
            config[key] = [str(v) for v in value] if isinstance(value, list) else []
        else:
            config[key] = bool(value)
    ctx.state.set_setting(_STATUS_KEY, config)
    return config


@handler("GET", "/api/admin/server-status/nodes")
async def server_status_nodes(ctx: Ctx) -> dict[str, Any]:
    """Ноды для выбора в настройке. Берём из их же списка — он у админа есть
    всегда, а панель у адаптера может быть не настроена."""
    data = await ctx.json("/cabinet/admin/remnawave/nodes", {}) or {}
    # Их ответ — {"items": [...], "total": N}; на всякий случай понимаем и голый
    # список, и ключ "nodes": молча показать пустоту вместо нод хуже всего.
    rows = data
    if isinstance(data, dict):
        rows = data.get("items") or data.get("nodes") or []
    items = []
    for n in rows or []:
        if not isinstance(n, dict):
            continue
        items.append({
            "uuid": str(n.get("uuid") or ""),
            "name": str(n.get("name") or ""),
            # Страна у них не всегда заполнена — пустая строка честнее выдумки.
            "country_code": str(n.get("country_code") or "").lower(),
            "online": bool(n.get("is_connected") or n.get("online")),
            "disabled": bool(n.get("is_disabled") or n.get("disabled")),
        })
    return {"nodes": items}
