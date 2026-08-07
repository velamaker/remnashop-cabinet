"""Уведомление «к вашей подписке подключилось новое устройство».

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ — общее правило адаптера: раздел = файл `admin_*.py`,
загрузчик в `compose.py` подхватывает их по маске, а в образ они попадают строкой
`COPY *.py` в `adapter/Dockerfile`. Ни compose.py, ни main.py править не нужно:
страница `/admin/new-device` уже описана там в списках разделов и появляется в
меню ровно тогда, когда здесь зарегистрирован GET `/api/admin/new-device`.

ЧЬЯ ЭТО ФУНКЦИЯ. Тумблер — свойство КАБИНЕТА: у «Бедолаги» такого уведомления
нет, хранить настройку негде, кроме своей памяти (`state.py`, как «Статус
сервиса» и пауза подписки). А сами устройства — данные ПАНЕЛИ Remnawave
(`/api/hwid/devices`), и это единственный источник: у бота список устройств есть
только личный (`/cabinet/subscription/devices`, под сессией самого человека), а
за фоновой задачей никто не стоит. Поэтому раздел не гасим — берём из панели,
ровно как это делает наш собственный бэкенд
(`admin_src/src/infrastructure/taskiq/tasks/new_device.py`).

ПОЧЕМУ ПЕРСОНАЛЬНОЕ СООБЩЕНИЕ, А НЕ РАССЫЛКА. У них есть оба входа:
  • `POST /broadcasts` (машинное API, X-API-Key) — рассылка по СЕГМЕНТУ:
    all / active / trial / expiring и несколько `custom_*` (today, week,
    inactive_month и т.п. — список закрытый, см. `get_target_users` и
    `get_custom_users` в `app/handlers/admin/messages.py`). Списка людей она не
    принимает: поле `recipient_ids` в их `BroadcastConfig` есть, но наружу, в
    схему `BroadcastCreateRequest`, не выведено. То есть «сказать одному» через
    рассылку нельзя в принципе — можно только написать ВСЕМ активным, что у них
    появилось новое устройство. Это не уведомление, а рассылка неправды;
  • `POST /cabinet/admin/users/{user_id}/send-message` — ровно один человек и наш
    текст целиком (HTML, до 4096 символов; проверено по их схеме
    `SendUserMessageRequest` и коду `app/cabinet/routes/admin_users.py`). Это и
    берём. Заодно их код честно отвечает `no_telegram_id` для тех, кто завёлся
    почтой, — нам это нужно, чтобы не считать таких «недоставленными навсегда».
Отдельно рассмотрен и отброшен `POST /ban-notifications/send` (машинный ключ,
адресуется одному человеку): их шаблон `BAN_MSG_WARNING` оборачивает наш текст в
«⚠️ ПРЕДУПРЕЖДЕНИЕ … При повторном нарушении аккаунт будет заблокирован». Про
подключённое устройство это прямая ложь и угроза баном ни за что.

ЦЕНА ВЫБОРА — та же, что у соседнего раздела `admin_traffic_alert.py`: кабинетные
ручки «Бедолаги» закрыты сессией администратора (JWT), машинный ключ они не
принимают (проверено: `X-API-Key` → 401 «Authentication required», ключ в
`Authorization: Bearer` → 401 «Invalid or expired token»). Поэтому адаптеру нужен
свой служебный вход: `BEDOLAGA_CABINET_EMAIL` / `BEDOLAGA_CABINET_PASSWORD` —
админ бота с правом `users:send_message`. Не задан — доставки нет, и это видно
честно: включить уведомление нельзя (501 с причиной), а включённое раньше
работает вхолостую и пишет в лог, чего ему не хватает. Код служебного входа здесь
повторяет соседний раздел сознательно: общего модуля доставки в адаптере пока
нет, а тянуть приватные функции из чужого файла — это поломка на первом же его
переименовании. Появится третий такой раздел — их пора свести в один helper.

ПЕРВЫЙ ПРОГОН — ЗАСЕВ. Снимок пишется, сообщения НЕ уходят. Без этого в момент
включения каждый человек получил бы письмо про каждое своё давно подключённое
устройство: у нашего бэкенда ровно та же защита (`_baselined()`), и она там не
из осторожности, а по опыту раскатки.

ДЕДУПЛИКАЦИЯ. В своей памяти лежит снимок «какие HWID мы уже видели» по каждому
человеку. Отметка ставится сразу после отправки (а не в конце прогона):
контейнер перезапускают при каждом обновлении, и «сохраню в конце» означало бы
второе такое же сообщение всем, кому уже написали. Не доставленное по временной
причине (бот недоступен, 5xx) в снимок НЕ попадает — повторим в следующий заход.
Отказ окончательный (нет телеграма, бот заблокирован человеком) записываем как
известное: иначе задача вечно долбилась бы в закрытую дверь каждые два часа.

ЧТО СОЗНАТЕЛЬНО НЕ ДЕЛАЕМ: не шлём по сообщению на каждое устройство. Человек,
переустановивший приложение на трёх устройствах, получил бы три подряд — за
прогон уходит одно сообщение на человека, с числом устройств внутри.
"""

from __future__ import annotations

import asyncio
import html
import os
import time
from fnmatch import fnmatchcase
from typing import Any

import httpx

import bot_session
from compose import Ctx, NotAvailable, UpstreamError, handler
from scheduler import task

# --- что и где храним ---------------------------------------------------------

# Ключи в своей памяти (`state.settings`). Новых таблиц не заводим: файл базы
# общий, его правят соседние разделы.
_KEY = "new_device"
_SEEN_KEY = "new_device_seen"

# Дефолт совпадает с нашим бэкендом (`overlay_new_device.py`): выключено.
# Уведомление, включившееся само при обновлении, — это письма людям, которых
# никто не заказывал.
_DEFAULT: dict[str, Any] = {"enabled": False}

# Их право на личное сообщение (`app/cabinet/routes/admin_users.py`:
# `require_permission('users:send_message')`).
_SEND_RIGHT = "users:send_message"

# --- пределы прогона ----------------------------------------------------------

# Пауза между сообщениями. Телеграм пропускает у бота около 30 сообщений в
# секунду и на превышении отвечает 429 всему боту сразу — то есть спешка нашей
# рассылки ударила бы по чужим ответам бота, а не только по ней.
_SEND_PAUSE = 0.1
# Потолок на один прогон: остаток уйдёт в следующий заход (те, кому уже написали,
# в снимке, повторов не будет). Без потолка первый же прогон на большой базе не
# уложился бы в предел времени задачи и был бы брошен посередине.
_MAX_PER_RUN = 300

_DEVICE_PAGE = 1000        # столько устройств панель отдаёт за запрос
_PANEL_PAGE = 500          # столько пользователей панели берём за запрос
_BOT_PAGE = 200            # у их `/users` limit ≤ 200 (жёстко, по их схеме)
_PANEL_TIMEOUT = 30.0
_PAGES_LIMIT = 500         # предохранитель от бесконечного обхода страниц

# Длина названия устройства в тексте. Приходит оно от клиента (это он сообщает
# панели свою модель), поэтому и режем, и экранируем.
_MODEL_MAX = 40

# Служебный вход адаптера — те же переменные, что у соседних разделов с
# доставкой. Читаем окружение здесь: main.py — общий файл, а переменные нужны
# только разделам, которые пишут людям.
_SERVICE_EMAIL = os.environ.get("BEDOLAGA_CABINET_EMAIL", "").strip()
_SERVICE_PASSWORD = os.environ.get("BEDOLAGA_CABINET_PASSWORD", "")

# Их access живёт минуты (у «Бедолаги» ~15), поэтому держим его в памяти процесса
# и обновляем по сроку. На диск не кладём сознательно: это ключ от админки, а
# перелогин стоит один запрос.
_session: dict[str, Any] = {"token": "", "until": 0.0}

# Текст — тот же, что у нашего бэкенда (`tasks/new_device.py`), чтобы человек в
# любой из двух установок читал одно и то же. Множественная форма добавлена
# здесь: за два часа устройств может появиться несколько, и три сообщения подряд
# выглядят как сбой, а не как забота.
_MSG = {
    "ru": {
        "one": (
            "🔔 <b>Новое устройство</b>\n\n"
            "К вашей подписке подключилось новое устройство{model}. "
            "Если это не вы — смените ссылку подписки в кабинете."
        ),
        "many": (
            "🔔 <b>Новые устройства</b>\n\n"
            "К вашей подписке подключились новые устройства ({count}){model}. "
            "Если это не вы — смените ссылку подписки в кабинете."
        ),
    },
    "en": {
        "one": (
            "🔔 <b>New device</b>\n\n"
            "A new device{model} connected to your subscription. "
            "If it wasn't you, reissue your subscription link in the cabinet."
        ),
        "many": (
            "🔔 <b>New devices</b>\n\n"
            "New devices ({count}) connected to your subscription{model}. "
            "If it wasn't you, reissue your subscription link in the cabinet."
        ),
    },
}


def _log(message: str) -> None:
    # Тот же префикс, что у остального адаптера: лог контейнера общий.
    print(f"[adapter] новое устройство: {message}", flush=True)


def _fail(status: int, detail: str) -> UpstreamError:
    """Отказ самого адаптера в той же форме, что и ошибка бота."""
    return UpstreamError(httpx.Response(status, json={"detail": detail}))


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


def _code(resp: httpx.Response) -> str:
    """Их машинный код отказа (`no_telegram_id`, `forbidden`, `bot_not_configured`).
    Нужен, чтобы отличить «этому человеку не написать никогда» от «сейчас не
    вышло»: первое записываем как известное, второе повторяем."""
    try:
        body = resp.json()
    except ValueError:
        return ""
    detail = body.get("detail") if isinstance(body, dict) else None
    return str(detail.get("code") or "") if isinstance(detail, dict) else ""


# --- настройка ----------------------------------------------------------------


def _normalize(raw: Any) -> dict[str, Any]:
    """Форма ответа ровно такая, какую ждёт экран кабинета (NewDeviceConfig):
    одно поле, ничего лишнего."""
    data = raw if isinstance(raw, dict) else {}
    return {"enabled": bool(data.get("enabled", _DEFAULT["enabled"]))}


def _config(state: Any) -> dict[str, Any]:
    if state is None or not getattr(state, "available", False):
        # Хранить настройку негде — значит её нет, а «нет» здесь равно
        # «выключено»: врать тумблером про работающее уведомление нельзя.
        return dict(_DEFAULT)
    return _normalize(state.get_setting(_KEY, {}))


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

    Сохранение здесь — не «поправить текст на экране», а включение личных
    сообщений от имени их бота. Право на них у них уже есть
    (`users:send_message`), поэтому спрашиваем его, а не выдумываем своё: админ
    «только для просмотра» не должен уметь включить рассылку всей базе.
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
    "Уведомление некому доставить: личное сообщение у «Бедолаги» открывается "
    "только сессией администратора (машинный ключ X-API-Key их кабинетное API не "
    "принимает), а за фоновой задачей администратор не стоит. Заведите в боте "
    "служебного админа с правом «users:send_message» и передайте адаптеру "
    "BEDOLAGA_CABINET_EMAIL и BEDOLAGA_CABINET_PASSWORD."
)


class _DeliveryDown(RuntimeError):
    """Служебный вход не работает. Беда общая для всего прогона, а не по одному
    человеку: перебирать остальных бессмысленно — им ответит то же самое."""


async def _service_token(ctx: Ctx, renew: bool = False) -> str:
    return await bot_session.token(ctx, renew)


async def _send_message(ctx: Ctx, user_id: int, text: str) -> tuple[bool, str, bool]:
    """Личное сообщение одному человеку.

    Возвращает (успех, причина словами, стоит ли повторить). Третий флаг — не
    украшение: от него зависит, попадёт ли устройство в снимок. «Бот сейчас
    недоступен» надо повторить, а «у человека нет телеграма» или «он заблокировал
    бота» — записать как известное, иначе задача будет ломиться в закрытую дверь
    каждые два часа до скончания века.

    Неработающий служебный вход — не отказ по этому человеку, а конец прогона:
    поднимается `_DeliveryDown`, вызывающий перебор прекращает.
    """
    for attempt in (1, 2):
        try:
            token = await _service_token(ctx, renew=attempt == 2)
        except RuntimeError as exc:
            raise _DeliveryDown(str(exc)) from exc
        try:
            resp = await ctx.call(
                "POST",
                f"/cabinet/admin/users/{int(user_id)}/send-message",
                json={"text": text},
                headers={"authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            # Сетевая беда с ОДНИМ сообщением не должна ронять весь прогон:
            # остальным написать можно, а этот попадёт в следующий заход.
            return False, f"бот недоступен: {exc}", True
        # Сессия протухла (их access живёт минуты) — второй заход уже с новым
        # токеном; повторять бесконечно нельзя, это была бы петля на 401.
        if resp.status_code == 401 and attempt == 1:
            continue
        if resp.status_code < 400:
            return True, "", False
        # 404 — человека у бота больше нет (удалили): в снимок, чтобы не долбиться.
        # 400 с их кодом — окончательный отказ по этому человеку.
        # 401/403 (нет сессии, нет права) и 5xx — беда настройки или бота, повторим.
        final = resp.status_code == 404 or (
            resp.status_code == 400
            and _code(resp) in ("no_telegram_id", "forbidden", "bad_request", "empty_message")
        )
        return False, _detail(resp), not final
    return False, "неизвестная ошибка", True


# --- откуда берём людей и устройства ------------------------------------------


def _short_uuid(url: Any) -> str:
    """Хвост ссылки подписки — он же `shortUuid` в панели. По нему и связываем
    человека бота с записью панели: телеграм есть не у всех (в кабинете
    регистрируются почтой), а ссылка подписки есть у каждого, у кого она куплена."""
    tail = str(url or "").split("?")[0].split("#")[0].rstrip("/")
    return tail.rsplit("/", 1)[-1] if "/" in tail else ""


async def _bot_users(ctx: Ctx) -> dict[str, dict[str, Any]]:
    """Люди бота, разложенные по `shortUuid` их подписок.

    Берём машинным ключом (`ctx.admin`) — это единственный доступ к их базе,
    который есть у фоновой задачи. Ошибку не проглатываем: пустой список молча
    означал бы «устройства ничьи», то есть тихо выключенное уведомление.

    По страницам идём ТОЛЬКО по признаку «страница пришла неполной». Их `total`
    в `/users` считается неверно (на стенде отдаёт 1 при семи пользователях), и
    доверие к нему обрезало бы обход на первой же странице.
    """
    if not ctx.can_admin:
        raise RuntimeError(
            "адаптеру не выдан машинный ключ бота (BEDOLAGA_ADMIN_TOKEN) — "
            "владельцев устройств определить нечем"
        )
    found: dict[str, dict[str, Any]] = {}
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
        batch = data.get("items") if isinstance(data, dict) else None
        if not isinstance(batch, list) or not batch:
            break
        for row in batch:
            if not isinstance(row, dict):
                continue
            try:
                uid = int(row.get("id"))
            except (TypeError, ValueError):
                continue
            person = {
                "user_id": uid,
                "telegram_id": row.get("telegram_id"),
                "language": str(row.get("language") or "ru")[:2].lower(),
                "status": str(row.get("status") or "").lower(),
            }
            # Индексируем ВСЕ подписки человека, а не только текущую: панель
            # помнит устройства и по старой ссылке, и без этого устройства
            # человека с продлённой (перевыпущенной) подпиской остались бы ничьи.
            subs = row.get("subscriptions")
            rows = subs if isinstance(subs, list) else []
            current = row.get("subscription")
            if isinstance(current, dict):
                rows = [*rows, current]
            for sub in rows:
                if not isinstance(sub, dict):
                    continue
                key = _short_uuid(sub.get("subscription_url"))
                if key:
                    found[key] = person
        offset += len(batch)
        if len(batch) < _BOT_PAGE:
            break
    return found


async def _panel_owners(ctx: Ctx) -> dict[str, str]:
    """Карта «кто владелец устройства» со стороны панели: и `uuid`, и числовой
    `id` пользователя → его `shortUuid`.

    Оба ключа нужны из-за версий панели: до Remnawave 2.8 устройство несло
    `userUuid`, с 2.8 — числовой `userId` (см. заметку о миграции 2.8). Строить
    карту по одному из них значит уронить раздел на соседней версии панели.
    """
    if not ctx.has_panel:
        raise RuntimeError(
            "панель Remnawave адаптеру не задана (REMNAWAVE_URL/REMNAWAVE_TOKEN) — "
            "устройства брать неоткуда"
        )
    owners: dict[str, str] = {}
    start = 0
    for page in range(_PAGES_LIMIT):
        data = await ctx.panel_json(
            "/api/users",
            None,
            params={"size": _PANEL_PAGE, "start": start},
            timeout=_PANEL_TIMEOUT,
        )
        if data is None:
            # `panel_json` любую беду превращает в default, поэтому пустоту
            # разбираем отдельно: «панель не ответила» и «в панели никого нет» —
            # разные вещи. Обрыв на ЛЮБОЙ странице тоже ошибка, а не конец списка:
            # снимок устройств потом прополется ровно по этим неполным данным, и
            # у половины людей «новыми» окажутся их старые устройства.
            raise RuntimeError(
                f"панель Remnawave не отдала список пользователей (страница {page + 1})"
            )
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
        total = int(data.get("total") or 0) if isinstance(data, dict) else 0
        if start >= total or len(rows) < _PANEL_PAGE:
            break
    return owners


async def _panel_devices(ctx: Ctx) -> list[dict[str, Any]]:
    """Все HWID-устройства панели. Тот же обход, что у нашего бэкенда."""
    if not ctx.has_panel:
        raise RuntimeError(
            "панель Remnawave адаптеру не задана (REMNAWAVE_URL/REMNAWAVE_TOKEN) — "
            "устройства брать неоткуда"
        )
    out: list[dict[str, Any]] = []
    start = 0
    for page in range(_PAGES_LIMIT):
        data = await ctx.panel_json(
            "/api/hwid/devices",
            None,
            params={"size": _DEVICE_PAGE, "start": start},
            timeout=_PANEL_TIMEOUT,
        )
        if data is None:
            if page == 0:
                raise RuntimeError("панель Remnawave не отдала список устройств")
            break
        rows = data.get("devices") if isinstance(data, dict) else None
        if not isinstance(rows, list) or not rows:
            break
        out.extend(row for row in rows if isinstance(row, dict))
        start += len(rows)
        total = int(data.get("total") or 0) if isinstance(data, dict) else 0
        if start >= total or len(rows) < _DEVICE_PAGE:
            break
    return out


def _label(device: dict[str, Any]) -> str:
    """Как назвать устройство человеку. Модель приходит от клиента, поэтому
    экранируем: их ручка разбирает наш текст как HTML и на битой разметке
    отвечает отказом — то есть один кривой `<` лишил бы человека уведомления."""
    raw = str(device.get("deviceModel") or device.get("platform") or "").strip()
    return html.escape(raw[:_MODEL_MAX]) if raw else ""


def _current(
    devices: list[dict[str, Any]],
    owners: dict[str, str],
    people: dict[str, dict[str, Any]],
) -> dict[str, dict[str, str]]:
    """Что видно СЕЙЧАС: `{id человека бота: {hwid: как называется}}`.

    Чистая функция: сюда приходят уже готовые списки, отсюда уходит решение — её
    видно на глаз и можно проверить без единого запроса и без единой отправки.
    Устройства, чьего владельца в боте нет (пользователь заведён прямо в панели),
    отбрасываем: писать некому, а в снимке они были бы мусором.
    """
    out: dict[str, dict[str, str]] = {}
    for device in devices:
        hwid = str(device.get("hwid") or "").strip()[:256]
        if not hwid:
            continue
        owner = device.get("userUuid") or device.get("userId")
        short = owners.get(str(owner)) if owner is not None else None
        person = people.get(short or "")
        if person is None:
            continue
        out.setdefault(str(person["user_id"]), {})[hwid] = _label(device)
    return out


def _fresh(
    current: dict[str, dict[str, str]], seen: dict[str, list[str]]
) -> dict[str, list[str]]:
    """Устройства, которых в прошлом снимке не было: `{id человека: [hwid…]}`."""
    out: dict[str, list[str]] = {}
    for uid, devices in current.items():
        known = set(seen.get(uid) or [])
        new = [hwid for hwid in devices if hwid not in known]
        if new:
            out[uid] = new
    return out


def _kept(
    current: dict[str, dict[str, str]], seen: dict[str, list[str]]
) -> dict[str, list[str]]:
    """Снимок, обрезанный до того, что панель показывает сейчас.

    Отключённые устройства забываем: во-первых, снимок лежит одной строкой в
    своей базе и расти вечно ему нельзя; во-вторых, устройство, которое человек
    сам удалил из подписки, а потом подключил заново, — это и правда новое
    подключение, про него стоит сказать.
    """
    out: dict[str, list[str]] = {}
    for uid, devices in current.items():
        known = [hwid for hwid in (seen.get(uid) or []) if hwid in devices]
        if known:
            out[uid] = known
    return out


def _text(language: str, labels: list[str]) -> str:
    """Текст сообщения. Одно устройство — с моделью, несколько — с числом и
    перечислением: так человеку видно, про что речь, без трёх сообщений подряд."""
    forms = _MSG.get(language, _MSG["ru"])
    named = [name for name in labels if name]
    if len(labels) == 1:
        return forms["one"].format(model=f" ({named[0]})" if named else "")
    tail = f": {', '.join(named)}" if named else ""
    return forms["many"].format(count=len(labels), model=tail)


# --- сам прогон ---------------------------------------------------------------


async def run_once(ctx: Ctx, dry_run: bool = False) -> dict[str, Any]:
    """Один проход: снять устройства, сравнить со снимком, написать про новые.

    `dry_run=True` — холостой прогон: считаем и показываем, но никому не пишем.
    Тем же путём идёт задача, когда доставка не настроена: молча ничего не делать
    — худший вариант, а честная строка «написали бы N» показывает, что задача
    жива и чего ей не хватает.

    ВАЖНО про холостой прогон: снимок он ВСЁ РАВНО обновляет. Иначе в день, когда
    доставку наконец настроят, людям прилетело бы разом про все устройства,
    подключённые за время простоя, — то есть ровно тот залп, от которого спасает
    засев. «Новое устройство» — новость на час, а не на неделю.
    """
    state = ctx.state
    config = _config(state)
    summary: dict[str, Any] = {
        "enabled": config["enabled"],
        "dry_run": dry_run,
        "seeded": False,
        "devices": 0,
        "people": 0,
        "fresh": 0,
        "sent": 0,
        "failed": 0,
    }
    if not config["enabled"]:
        return summary
    if state is None or not state.available:
        # Без памяти дедупликации нет, а значит каждый прогон был бы новой
        # рассылкой про те же устройства. Лучше не делать ничего.
        raise RuntimeError("своя память адаптера недоступна — снимок хранить негде")

    devices = await _panel_devices(ctx)
    owners = await _panel_owners(ctx)
    people = await _bot_users(ctx)
    current = _current(devices, owners, people)
    summary["devices"] = sum(len(v) for v in current.values())
    summary["people"] = len(current)

    stored = state.get_setting(_SEEN_KEY, {})
    stored = stored if isinstance(stored, dict) else {}
    seen: dict[str, list[str]] = {}
    for uid, hwids in (stored.get("devices") or {}).items():
        if isinstance(hwids, list):
            seen[str(uid)] = [str(h) for h in hwids]

    # ЗАСЕВ. Первый прогон только запоминает: включили уведомление — и человек
    # НЕ получает письмо про каждое своё давнее устройство.
    if not stored.get("seeded"):
        state.set_setting(
            _SEEN_KEY,
            {"seeded": True, "devices": {uid: list(d) for uid, d in current.items()}},
        )
        summary["seeded"] = True
        _log(
            f"засев: записано устройств {summary['devices']} "
            f"у {summary['people']} чел. — уведомления со следующего прогона"
        )
        return summary

    fresh = _fresh(current, seen)
    summary["fresh"] = len(fresh)
    snapshot = _kept(current, seen)
    # Люди разложены по `shortUuid` (по нему мы искали владельца устройства), а
    # писать надо по id — второй индекс строим один раз, а не ищем перебором на
    # каждого получателя.
    by_id = {str(person["user_id"]): person for person in people.values()}

    def _remember(uid: str, hwids: list[str]) -> None:
        # Пишем сразу, а не в конце прогона: контейнер перезапускают при каждом
        # обновлении, и «сохраню потом» означало бы второе такое же сообщение
        # всем, кому уже написали.
        snapshot.setdefault(uid, []).extend(hwids)
        state.set_setting(_SEEN_KEY, {"seeded": True, "devices": snapshot})

    for uid, hwids in fresh.items():
        if summary["sent"] >= _MAX_PER_RUN:
            _log(
                f"за прогон отправлено {_MAX_PER_RUN} сообщений — остальные уйдут "
                "в следующий заход (кому написали, те уже в снимке)"
            )
            break
        person = by_id.get(uid)
        if person is None:
            continue  # человека уже нет в списке — писать некому
        labels = [current[uid][hwid] for hwid in hwids]

        if dry_run:
            _remember(uid, hwids)
            continue
        if not person.get("telegram_id"):
            # Человек завёлся почтой: канала для него у бота нет вовсе. Записываем
            # как известное — иначе каждые два часа мы бы выясняли это заново, а
            # привязав телеграм, он получил бы письмо про старые устройства.
            _remember(uid, hwids)
            continue
        if person.get("status") not in ("", "active"):
            # Заблокированному «у вас новое устройство» — сообщение ни о чём:
            # доступа у него нет. В снимок, чтобы не повторять решение каждый раз.
            _remember(uid, hwids)
            continue

        try:
            ok, reason, retry = await _send_message(
                ctx, int(person["user_id"]), _text(str(person["language"]), labels)
            )
        except _DeliveryDown as exc:
            # Служебный вход отвалился — остальным будет ровно то же самое.
            # Устройства этого прохода в снимок не попали, значит в следующий раз
            # человек про них узнает.
            _log(f"прогон остановлен: {exc}")
            break
        if ok:
            summary["sent"] += 1
            _remember(uid, hwids)
            await asyncio.sleep(_SEND_PAUSE)
            continue
        summary["failed"] += 1
        _log(f"user_id={uid}: не доставлено — {reason}")
        if not retry:
            # Отказ окончательный (нет телеграма, бот заблокирован) — записываем,
            # чтобы не ломиться в закрытую дверь каждые два часа.
            _remember(uid, hwids)

    # Снимок сохраняем и в конце: за прогон могли отпасть устройства, которых в
    # панели больше нет, — их место в базе освобождаем (см. _kept).
    state.set_setting(_SEEN_KEY, {"seeded": True, "devices": snapshot})
    return summary


@task("new-device", hours=2, run_at_start=False, timeout=900)
async def new_device(ctx: Ctx) -> None:
    """Фоновая проверка новых устройств.

    Раз в два часа — как у нашего бэкенда (крон `17 */2 * * *`). Чаще незачем:
    панель регистрирует HWID при подключении, но уведомление про устройство —
    это про «зайдите и проверьте», а не про секунды. `run_at_start=False`
    обязателен: иначе каждое обновление контейнера начиналось бы с рассылки.
    """
    config = _config(ctx.state)
    if not config["enabled"]:
        return

    ready = _delivery_configured()
    if not ready:
        # Включили раньше, а доступ для отправки пропал (или его и не было).
        # Считаем вхолостую и говорим прямо, чего не хватает.
        _log(f"доставка не настроена — идёт холостой прогон. {_NO_DELIVERY}")

    summary = await run_once(ctx, dry_run=not ready)
    if summary["seeded"]:
        return  # про засев уже сказано подробнее
    if summary["fresh"] or summary["failed"]:
        _log(
            "устройств {devices} у {people} чел., новых у {fresh} чел., "
            "отправлено {sent}, не доставлено {failed}{tail}".format(
                **summary, tail=" (холостой прогон)" if summary["dry_run"] else ""
            )
        )


# --- ручки --------------------------------------------------------------------


@handler("GET", "/api/admin/new-device")
async def new_device_config(ctx: Ctx) -> dict[str, Any]:
    await _require_admin(ctx)
    return _config(ctx.state)


@handler("PUT", "/api/admin/new-device")
async def new_device_save(ctx: Ctx) -> dict[str, Any]:
    """Сохранение. Меняем только присланное — экран шлёт частичное обновление.

    Включить, когда сообщение доставить нечем, нельзя: тумблер «включено» при
    молчащем уведомлении — это ложь в админке, которую замечают через месяц по
    жалобе «а почему мне не сказали, что кто-то подключился».
    """
    await _require_admin(ctx)
    await _require_send_right(ctx)
    if ctx.state is None or not ctx.state.available:
        raise NotAvailable(
            "Настройку некуда сохранить: у адаптера нет доступа к своему хранилищу"
        )

    body = ctx.payload()
    config = _config(ctx.state)
    if body.get("enabled") is not None:
        enabled = bool(body["enabled"])
        if enabled and not _delivery_configured():
            raise NotAvailable(_NO_DELIVERY)
        config["enabled"] = enabled

    ctx.state.set_setting(_KEY, config)
    return config
