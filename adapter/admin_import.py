"""Админка: раздел «Импорт» — синхронизация пользователей с панелью Remnawave.

ПОЧЕМУ РАЗДЕЛ ВООБЩЕ ВОЗМОЖЕН. Главный вопрос к чужому боту был один: умеет ли
его API СОЗДАВАТЬ пользователей — без этого «импорт» превратился бы в кнопку,
которая ничего не импортирует. Умеет, причём в обе стороны:
  • `POST /cabinet/admin/remnawave/sync/from-panel` — забирает пользователей из
    панели и заводит недостающих в базе бота (их `sync_users_from_panel`,
    статистика `created/updated/errors/deleted`);
  • `POST /cabinet/admin/remnawave/sync/to-panel` — идёт по подпискам бота и
    создаёт/обновляет пользователей в панели (их `sync_users_to_panel`).
Проверено живьём на стенде: from-panel вернул `{"created":0,"updated":3}`, и
отдельно проверено создание одного пользователя их машинной ручкой
`POST /users` (201, пользователь потом удалён) — то есть создание у них не
декларация в схеме, а рабочий код.

ЧТО ЗАКРЫТО И ЧЕМ (два экрана из трёх на странице «Импорт»):
  • «Из панели Remnawave → в бота»  → их sync/from-panel (режим `all`);
  • «Из бота → в панель Remnawave»  → их sync/to-panel;
  • справочник сквадов для третьего блока → их `/cabinet/admin/remnawave/squads`.

ЧЕГО НЕТ И ПОЧЕМУ — импорт из файла БД x-ui/3x-ui (`POST /api/admin/import/xui`
отвечает 501 с причиной, см. `import_xui` в конце файла). Коротко: у «Бедолаги»
такой ручки нет вообще (в их openapi нет ни одного пути с x-ui, в исходниках
контейнера — ни одного упоминания), а делать её самим значило бы писать
пользователей прямо в панель служебным ключом с полными правами. Ровно этого
запрещает доктрина `admin_panel.py`: изменяющие действия идут ТОЛЬКО через их
API, чтобы правами распоряжался их RBAC, а не всякий, кто вошёл в кабинет.
Запасного пути к мутации с полными правами быть не должно, поэтому кнопка
честно отказывает, а не делает вид.

ОТКУДА ДАННЫЕ. Всё — их кабинетное `/cabinet/admin/*` СЕССИЕЙ САМОГО АДМИНА
(`ctx.call` внутри `_send`), то же правило, что в `admin_users.py` и
`admin_panel.py`. Их RBAC режет доступ правами `remnawave:read` (чтение статуса
и сквадов) и `remnawave:sync` (обе синхронизации) — человек без права получит их
же 403. Служебный ключ адаптера (`ctx.admin`) здесь не используется вовсе:
синхронизация меняет и базу бота, и панель, такое нельзя запускать в обход их
проверки прав. Панель напрямую (`ctx.panel_json`) тоже не трогаем — тут нет ни
одного поля, которого не отдаёт их API.

ЧТО ВАЖНО ЗНАТЬ ОПЕРАТОРУ ПРО ИХ СИНХРОНИЗАЦИЮ:
  • обе ручки СИНХРОННЫЕ: ответ приходит, когда синхронизация закончилась, а не
    «поставлена в очередь». Наш бэкенд ведёт себя так же (ждёт результат
    таски), поэтому экран не переделывается — но пределы времени здесь свои,
    см. `SYNC_DEADLINE`;
  • их ответ ВСЕГДА `success: true`, даже если ни один пользователь не прошёл:
    провал виден только в `data.errors`. Молча показать «Синхронизировано: 0»
    нельзя — 0 читается как «всё уже совпадает», а не «всё сломалось», поэтому
    полный провал мы превращаем в честную ошибку (см. `_synced`);
  • на стенде с панелью Remnawave 2.8.1 их sync/to-panel падает на каждом
    пользователе: они шлют `PATCH /api/users` с числовым `id`, а панель 2.8
    требует `uuid` или `username` («Either uuid or username must be provided»).
    Это баг их бота против новой панели, не адаптера, и чинить его не наше дело
    — наше дело показать админу, что синхронизация не прошла, а не ноль.
"""

from __future__ import annotations

from typing import Any

import httpx

from compose import Ctx, NotAvailable, UpstreamError, handler

# Обе синхронизации идут по ВСЕМ пользователям сразу и держат запрос открытым до
# конца работы. Общий предел адаптера (30 с) обрывал бы их на пустом месте: на
# тысяче учёток из панели это минуты. Обрыв не отменяет саму синхронизацию — она
# доработает у них, — но админ увидел бы «слишком долго» и жал бы кнопку ещё раз,
# запуская вторую такую же поверх первой (блокировки у них нет, см. `status`).
SYNC_DEADLINE = 600.0
SYNC_TIMEOUT = httpx.Timeout(connect=5.0, read=590.0, write=10.0, pool=5.0)


def _int(value: Any, default: int = 0) -> int:
    """Их числа приходят и строкой, и None. Свой хелпер, а не общий из соседнего
    файла: модули правятся порознь, связывать их приватными функциями нельзя."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --- разговор с их API ------------------------------------------------------

_ERRORS = {
    "RemnaWave API is not configured":
        "Панель Remnawave не настроена в боте — синхронизировать не с чем",
    "Auto sync service is not available":
        "Служба фоновой синхронизации у бота не запущена",
}


def _ru(text: str, need: str) -> str:
    """Их отказы по-русски. `need` — право, которого требует ЭТА ручка.

    Переводим только то, что реально встречается на этих четырёх ручках;
    незнакомый текст уходит наверх как есть — английская правда полезнее нашей
    догадки о том, что бот имел в виду.

    Про отказ их RBAC. Он отвечает `Permission denied: <причина>`, где причина —
    «No active roles assigned» или «Permission not granted by any role»
    (их app/services/permission_service.py). ИМЕНИ права в тексте нет вовсе,
    поэтому подставлять причину вместо него нельзя: получилось бы «нужно право
    „Permission not granted by any role“». Имя знаем мы — оно зашито в вызывающей
    ручке, и без него владелец бота не поймёт, что именно выдать в своей админке.
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


def _detail(resp: httpx.Response) -> str:
    """Текст ошибки из их ответа. `detail` у них бывает строкой, объектом (режим
    техработ — `{code,message,reason}`) и списком (валидация pydantic), а кабинет
    умеет показывать только строку."""
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
        return detail
    return "Не удалось выполнить действие"


def _fail(status: int, detail: str) -> UpstreamError:
    """Отказ самого адаптера в той же форме, что и ошибка бота."""
    return UpstreamError(httpx.Response(status, json={"detail": detail}))


async def _send(ctx: Ctx, method: str, path: str, need: str, **kw: Any) -> dict[str, Any]:
    """Запрос к их кабинетному API от имени самого админа. `need` — право,
    которого требует эта ручка у них (для текста отказа, см. `_ru`).

    Отдельно от `ctx.json` из-за 503. Общее правило «5xx схлопываем в 502» здесь
    вредит: их 503 на этих путях — это не внутренний сбой, а осмысленная причина
    («панель не настроена», «служба синхронизации не запущена», режим техработ),
    и админ должен прочитать именно её, а не «попробуйте позже». Остальные 5xx
    по-прежнему схлопываем: их трейсы показывать нечего.
    """
    try:
        resp = await ctx.call(method, path, **kw)
    except httpx.HTTPError as exc:
        raise _fail(502, f"Бэкенд недоступен: {exc}") from exc
    if resp.status_code == 503:
        raise _fail(503, _ru(_detail(resp), need))
    if resp.status_code >= 500:
        raise _fail(502, "Бот ответил ошибкой — синхронизация не выполнена, попробуйте позже")
    if resp.status_code >= 400:
        raise _fail(resp.status_code, _ru(_detail(resp), need))
    try:
        body = resp.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _synced(body: dict[str, Any], direction: str) -> int:
    """Сколько пользователей реально прошло синхронизацию.

    Их статистика — `{created, updated, errors, deleted}`, а наш экран показывает
    одно число, поэтому считаем созданных плюс обновлённых: и то и другое значит
    «учётка на той стороне теперь соответствует этой».

    Почему тут же проверка на провал. Их ручка отвечает 200 и `success: true`
    даже когда НИ ОДИН пользователь не прошёл (так на стенде ведёт себя
    to-panel против панели 2.8: `{"created":0,"updated":0,"errors":3}`). Отдать
    в таком случае «Синхронизировано: 0» — соврать: ноль читается как «всё и так
    совпадало». Отдаём ошибку с числом провалов; при частичном успехе (что-то
    прошло, что-то нет) ошибку не поднимаем — работа сделана, и обнулять её
    отчётом об ошибке было бы враньём в другую сторону.
    """
    data = body.get("data")
    stats = data if isinstance(data, dict) else {}
    done = _int(stats.get("created")) + _int(stats.get("updated"))
    errors = _int(stats.get("errors"))
    if done == 0 and errors > 0:
        raise _fail(
            502,
            f"Бот не смог синхронизировать {direction}: "
            f"ошибок — {errors}, успешно — ни одного. Причина видна в логах бота.",
        )
    return done


# --- ручки экрана «Импорт» --------------------------------------------------


@handler("GET", "/api/admin/import/status")
async def import_status(ctx: Ctx) -> dict[str, bool]:
    """Идёт ли синхронизация прямо сейчас (экран опрашивает раз в 5 секунд).

    У нашего бэкенда все три флага — это redis-ключи запущенных фоновых задач. У
    «Бедолаги» фоновая только одна: их планировщик раз в сутки сам делает
    sync/from-panel плюс синхронизацию сквадов, и его состояние видно в
    `/sync/auto/status` (`is_running`). Его и отдаём в `panel` — пока их
    планировщик тянет пользователей из панели, запускать то же самое руками
    поверх незачем, и кнопка честно блокируется.

    `bot` и `xui` всегда false, и это не заглушка, а положение дел: sync/to-panel
    у них выполняется синхронно внутри запроса (фонового «идёт» просто не
    существует), а импорта из x-ui нет вовсе. Своего флага «занято» адаптер не
    заводит: он жил бы в его памяти и при перезапуске завис бы включённым
    навсегда, заблокировав кнопку до следующего рестарта.

    Отказ их службы (503 «Auto sync service is not available») экран не роняет:
    он опрашивает эту ручку раз в пять секунд, и «служба не поднята» значит ровно
    «ничего сейчас не идёт». А вот 401/403 уходят наверх как есть — это не
    «данных нет», а «вам сюда нельзя», и кабинет обязан это увидеть.
    """
    try:
        auto = await _send(
            ctx, "GET", "/cabinet/admin/remnawave/sync/auto/status", "remnawave:read",
        )
    except UpstreamError as exc:
        if exc.resp.status_code in (401, 403):
            raise
        auto = {}
    return {"panel": bool(auto.get("is_running")), "bot": False, "xui": False}


@handler("GET", "/api/admin/import/squads")
async def import_squads(ctx: Ctx) -> dict[str, Any]:
    """Сквады панели для блока импорта из x-ui.

    Сам импорт из файла мы не умеем (см. `import_xui`), но список отдаём
    настоящий: если бы этой ручки не было, экран написал бы «Сквады не найдены
    (проверьте связь с панелью)» — то есть соврал бы про причину. Пусть лучше
    список будет живой, а отказ придёт по нажатию кнопки и с объяснением.

    Берём их ручку, а не панель напрямую: список сквадов приходит из панели в
    обоих случаях, но через их API его закрывает право `remnawave:read`.
    """
    body = await _send(ctx, "GET", "/cabinet/admin/remnawave/squads", "remnawave:read")
    items = body.get("items")
    squads = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        uuid = str(item.get("uuid") or "")
        if not uuid:
            continue
        # `name` — имя сквада в панели (наш бэкенд показывает именно его);
        # `display_name` — их собственная подпись, годится только как запасная.
        name = str(item.get("name") or item.get("display_name") or uuid)
        squads.append({"uuid": uuid, "name": name})
    return {"squads": squads}


@handler("POST", "/api/admin/import/sync-panel", deadline=SYNC_DEADLINE)
async def sync_from_panel(ctx: Ctx) -> dict[str, Any]:
    """Панель → бот: завести в базе бота тех, кто есть в панели.

    Режим `all` жёстко: на нашем экране выбора режима нет, а наш бэкенд делает
    полную синхронизацию. Их `new_only`/`update_only` остались бы настройкой,
    которую негде выставить.
    """
    body = await _send(
        ctx, "POST", "/cabinet/admin/remnawave/sync/from-panel", "remnawave:sync",
        json={"mode": "all"}, timeout=SYNC_TIMEOUT,
    )
    return {"success": True, "synced": _synced(body, "пользователей из панели")}


@handler("POST", "/api/admin/import/sync-bot", deadline=SYNC_DEADLINE)
async def sync_to_panel(ctx: Ctx) -> dict[str, Any]:
    """Бот → панель: создать в панели тех, у кого в боте есть подписка.

    Тела их ручка не принимает вовсе, поэтому и не шлём.
    """
    body = await _send(
        ctx, "POST", "/cabinet/admin/remnawave/sync/to-panel", "remnawave:sync",
        timeout=SYNC_TIMEOUT,
    )
    return {"success": True, "synced": _synced(body, "пользователей бота в панель")}


@handler("POST", "/api/admin/import/xui")
async def import_xui(_ctx: Ctx) -> dict[str, Any]:
    """Импорт из файла БД x-ui/3x-ui — честный 501.

    Путь зарегистрирован намеренно, хотя ничего не делает: без него `main.py`
    ответил бы общим «нет соответствия в „Бедолаге“», а тут причина конкретная и
    админу понятно, что чинить нечего.

    Почему нельзя сделать. У «Бедолаги» импорта из x-ui нет ни в API, ни в коде.
    Наш бэкенд делает его сам: читает `inbounds` из sqlite-файла и создаёт
    пользователей СРАЗУ В ПАНЕЛИ через её API. Повторить это в адаптере значит
    начать писать в панель служебным ключом с полными правами — а правило
    проекта (см. `admin_panel.py`) ровно обратное: всё изменяющее идёт через их
    API, чтобы решение «можно ли» принимал их RBAC. Ключ адаптера такой проверки
    не проходит, и второго пути к созданию учёток мимо неё быть не должно.
    """
    raise NotAvailable(
        "Импорт из файла x-ui этот бот не умеет: у «Бедолаги» такой ручки нет, "
        "а создавать пользователей в панели в обход её проверки прав адаптер не "
        "будет. Перенести учётки можно так: завести их в панели Remnawave, а "
        "потом нажать «Синхронизировать из панели» на этом же экране."
    )
