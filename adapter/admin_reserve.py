"""Резервный доступ истёкшим — пульт к grace-доступу «Бедолаги».

ЧЬЯ ЭТО ФУНКЦИЯ. У обоих своя. У нас — «спасательный круг»: подписка кончилась,
человек ещё N дней живёт с маленьким лимитом трафика и успевает продлить (наш
overlay_reserve.py + крон reserve.py). У «Бедолаги» ровно та же мысль называется
grace-access и устроена богаче: у неё ЧЕТЫРЕ режима работы, срок в ЧАСАХ и ДВА
сквада — отдельный для истёкших и отдельный для исчерпавших трафик.

Поэтому здесь НЕ копия нашей механики поверх чужого бота (своего крона адаптер
не держит и держать не должен: два разных куска кода, гоняющих одних и тех же
людей по сквадам панели, — это гарантированные качели), а ПУЛЬТ: наш экран
читает и пишет ЕЁ настройки. Решение владельца.

ОТКУДА БЕРЁМ. Из их же справочника настроек — `/cabinet/admin/settings` (чтение)
и `/cabinet/admin/settings/{key}` (запись), под их правами `settings:read` /
`settings:edit`, по сессии САМОГО админа (`ctx.json` подставляет его Bearer).
Служебный токен адаптера здесь не используется намеренно — по той же причине,
что и в admin_system.py: раздавать чужие настройки по токену адаптера значит
отдать их любому вошедшему в кабинет.

ИХ КЛЮЧИ (app/config.py, категория GRACE):
  • GRACE_ACCESS_MODE — 'false' | 'observe' | 'true' | 'drain';
  • GRACE_ACCESS_DURATION_HOURS — сколько держать резерв, ЧАСЫ;
  • GRACE_ACCESS_TRAFFIC_GB — сколько ГБ выдать сверх израсходованного;
  • GRACE_ACCESS_EXPIRED_SQUAD_UUID — сквад для истёкших;
  • GRACE_ACCESS_LIMITED_SQUAD_UUID — сквад для исчерпавших трафик;
  • GRACE_ACCESS_TRIAL_ENABLED / _DAILY_ENABLED / _FREE_ENABLED — кому положено
    (обычные платные получают всегда, эти три вида — только по галке).
Служебные GRACE_ACCESS_RECONCILE_* и _CANDIDATE_LOOKBACK_MINUTES на экран не
выносим: это тюнинг их сверяющего цикла (как часто и какими пачками обходить
сессии), к смыслу «резервного доступа» он отношения не имеет, а неудачное
значение ломает не настройку, а фоновую работу бота.

ЧТО ОТДАЁМ КАБИНЕТУ. Прежние четыре поля (`enabled`, `reserve_gb`,
`window_days`, `squad_uuid`) — чтобы страница, открытая из кэша браузера, не
рассыпалась, — ПЛЮС необязательные: `mode`, `modes`, `window_hours`,
`squad_uuid_limited`, три галки «кому положено», `mode_note` и пара карт
`supported`/`locked` (договор тот же, что у admin_system.py и admin_auth.py:
чего у бота нет — кабинет не рисует; что есть, но правится не отсюда, — рисует
выключенным с причиной). Наш собственный бэкенд ничего из этого не шлёт, и
кабинет обязан вести себя тогда ровно как раньше.

ЕДИНИЦЫ СРОКА НЕ ПОДМЕНЯЕМ. У них ЧАСЫ, у нас ДНИ. `window_hours` отдаём как
есть и правится он тоже в часах; `window_days` остаётся только ради старой
страницы (целых дней в их часах) и на ЗАПИСЬ не принимается — 501 с причиной.
Тихий пересчёт «часы↔дни» уже стоил нам подаренных дней в паузе: округление
туда-обратно съедает остаток, и человек теряет доступ раньше, чем ему обещали.

ГЛАВНАЯ ОГОВОРКА — ПЕРЕЗАПУСК. Режим бот читает ОДИН РАЗ при старте
(`grace_access_runtime.start()` кладёт его в свою переменную, дальше все проверки
идут по ней). Запись через их API значение сохраняет, но работать бот
продолжает в прежнем режиме до перезапуска. Молчать об этом нельзя — админ
переключил бы «Включено» и ушёл в уверенности, что резерв работает. Поэтому
рядом с выбором режима едет `mode_note`. Срок, трафик, сквады и галки политика
собирает заново на каждую выдачу (`_build_policy`), они применяются сразу.

ПОЧЕМУ ПРОВЕРЯЕМ ЗНАЧЕНИЯ САМИ. Их API на запись значение только приводит к типу
(строка/число/булево) — проверки самих настроек в нём нет, а pydantic-валидаторы
их конфига на присваивание не срабатывают (`validate_assignment` не включён).
То есть «0 часов» или «режим trve» их API примет, а грейс после перезапуска
просто не поднимется (их старт ловит исключение и оставляет функцию выключенной,
бот при этом работает). Настройка, которая молча выключает функцию, хуже
отказа — поэтому режим, часы, ГБ и UUID-ы сверяем ДО записи.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx

from compose import HANDLERS, Ctx, NotAvailable, UpstreamError, handler

# --- их ключи ----------------------------------------------------------------

_SETTINGS = "/cabinet/admin/settings"

_KEY_MODE = "GRACE_ACCESS_MODE"
_KEY_HOURS = "GRACE_ACCESS_DURATION_HOURS"
_KEY_GB = "GRACE_ACCESS_TRAFFIC_GB"
_KEY_SQUAD_EXPIRED = "GRACE_ACCESS_EXPIRED_SQUAD_UUID"
_KEY_SQUAD_LIMITED = "GRACE_ACCESS_LIMITED_SQUAD_UUID"
_KEY_TRIAL = "GRACE_ACCESS_TRIAL_ENABLED"
_KEY_DAILY = "GRACE_ACCESS_DAILY_ENABLED"
_KEY_FREE = "GRACE_ACCESS_FREE_ENABLED"

# Поле нашего экрана → их ключ. По этой же карте считается `supported` (ключа нет
# в справочнике — бот другой версии, элемент прятать) и `locked` (ключ есть, но
# задан в .env или отдаётся только на чтение).
_CONTROL_KEYS = {
    "mode": _KEY_MODE,
    "window_hours": _KEY_HOURS,
    "reserve_gb": _KEY_GB,
    "squad_uuid": _KEY_SQUAD_EXPIRED,
    "squad_uuid_limited": _KEY_SQUAD_LIMITED,
    "trial_enabled": _KEY_TRIAL,
    "daily_enabled": _KEY_DAILY,
    "free_enabled": _KEY_FREE,
}

# Чего на этом бэкенде нет вовсе — кабинет такие элементы не рисует.
#   • `enabled` — тумблера «вкл/выкл» у них нет: режимов четыре, и «наблюдение»
#     со «сливом» двумя положениями не выражаются;
#   • `window_days` — срок у них в часах (см. шапку).
_UNSUPPORTED_CONTROLS = ("enabled", "window_days")

_MODES = ("false", "observe", "true", "drain")

# Подписи режимов для кабинета: их значения — служебные строки, показывать
# админу «drain» нельзя. Описания — по их же коду (grace_access_runtime.py):
# при DISABLED/OBSERVE бот не трогает панель вовсе, при DRAIN новых не берёт, а
# уже открытые доводит до конца и возвращает людей на их прежние сквады.
_MODE_LABELS: tuple[tuple[str, str, str], ...] = (
    ("false", "Выключен", "Резервный доступ не выдаётся."),
    (
        "observe",
        "Наблюдение",
        "Бот только записывает, кому резерв полагался бы. Панель не трогает, доступ никому не выдаёт.",
    ),
    ("true", "Включён", "Резерв выдаётся: истёкшие и исчерпавшие трафик получают временный доступ."),
    (
        "drain",
        "Слив",
        "Новым резерв не выдаётся, уже выданный доводится до конца и люди возвращаются на свои сквады. "
        "Правильный способ выключить резерв: сначала «Слив», и только когда открытых сессий не осталось — «Выключен».",
    ),
)

_MODE_NOTE = (
    "Режим бот читает при запуске: выбранное значение сохранится, но работать он продолжит в прежнем "
    "режиме до перезапуска бота (в его каталоге: docker compose restart). Срок, трафик, сквады и «кому "
    "положено» подхватываются сразу, без перезапуска."
)

# Те же две причины «показываем, но не даём править», что и в admin_system.py.
_ENV_LOCKED = "Задано в .env бота — отсюда не изменить: правьте .env и перезапускайте бота"
_READ_ONLY = "Этот параметр бот отдаёт только на чтение — из кабинета не изменить"


# --- мелочи ------------------------------------------------------------------


def _bad_request(message: str) -> UpstreamError:
    """Ошибка В ЗАПРОСЕ (кривое значение), а не «не умеем»: 400, не 501.

    501 читается админом как «функция не переведена» — вешать его на «0 часов»
    значит соврать про возможности бота.
    """
    return UpstreamError(httpx.Response(400, json={"detail": message}))


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _defs(rows: Any) -> dict[str, dict[str, Any]]:
    """Их справочник настроек → только интересные нам ключи."""
    wanted = set(_CONTROL_KEYS.values())
    if not isinstance(rows, list):
        return {}
    return {
        row["key"]: row
        for row in rows
        if isinstance(row, dict) and row.get("key") in wanted
    }


def _flag(defs: dict[str, dict[str, Any]], key: str, default: bool = False) -> bool:
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


def _text(defs: dict[str, dict[str, Any]], key: str, default: str = "") -> str:
    row = defs.get(key)
    if not isinstance(row, dict):
        return default
    value = row.get("current")
    return value.strip() if isinstance(value, str) else default


def _lock_reason(defs: dict[str, dict[str, Any]], key: str) -> str:
    row = defs.get(key)
    if not isinstance(row, dict):
        return ""
    if row.get("env_locked"):
        return _ENV_LOCKED
    return _READ_ONLY if row.get("read_only") else ""


def _mode(defs: dict[str, dict[str, Any]]) -> str:
    """Их режим. Незнакомое значение не подменяем «выключено»: админ должен
    увидеть то, что реально лежит у бота, иначе он «починит» настройку, которой
    не касался."""
    return _text(defs, _KEY_MODE, "false") or "false"


def _uuid_or_empty(value: Any, what: str) -> str:
    """UUID сквада или пусто. Кривой UUID их API примет как строку, а грейс
    после перезапуска просто не поднимется — проверяем сами."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        UUID(text)
    except ValueError:
        raise _bad_request(f"{what}: нужен UUID сквада (например 03542796-2d7d-…) или пусто") from None
    return text


# --- как это выглядит на экране ----------------------------------------------


def _applicability(defs: dict[str, dict[str, Any]]) -> tuple[dict[str, bool], dict[str, str]]:
    supported: dict[str, bool] = {key: False for key in _UNSUPPORTED_CONTROLS}
    locked: dict[str, str] = {}
    for control, key in _CONTROL_KEYS.items():
        supported[control] = key in defs
        reason = _lock_reason(defs, key)
        if reason:
            locked[control] = reason
    return supported, locked


def _view(defs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Их настройки → форма нашего экрана (`ReserveConfig` из admin.ts).

    Первые четыре поля — прежние, для страницы из кэша браузера; остальные
    необязательные, их шлёт только адаптер.
    """
    supported, locked = _applicability(defs)
    hours = _num(defs, _KEY_HOURS, 72)
    mode = _mode(defs)
    return {
        # Резерв реально выдаётся только в режиме «Включён»: при «наблюдении» и
        # «сливе» бот новых сессий не открывает.
        "enabled": mode == "true",
        "reserve_gb": _num(defs, _KEY_GB, 1),
        # Целые дни из их часов — ровно как `link_reset.cooldown_hours` в
        # admin_system.py: показать старой странице хоть что-то осмысленное, но
        # на запись такое поле не принимать (72 ч = 3 дня, а 50 ч = «2 дня»,
        # и обратный пересчёт съел бы два часа).
        "window_days": hours // 24,
        "squad_uuid": _text(defs, _KEY_SQUAD_EXPIRED),
        # --- дальше необязательное: наш бэкенд этих полей не шлёт ------------
        "mode": mode,
        "modes": [
            {"value": value, "label": label, "desc": desc} for value, label, desc in _MODE_LABELS
        ],
        "mode_note": _MODE_NOTE,
        "window_hours": hours,
        "squad_uuid_limited": _text(defs, _KEY_SQUAD_LIMITED),
        "trial_enabled": _flag(defs, _KEY_TRIAL),
        "daily_enabled": _flag(defs, _KEY_DAILY),
        "free_enabled": _flag(defs, _KEY_FREE),
        "supported": supported,
        "locked": locked,
    }


async def _load(ctx: Ctx) -> dict[str, dict[str, Any]]:
    defs = _defs(await ctx.json(_SETTINGS, []))
    if not defs:
        # Ни одного ключа GRACE — у этого бота такой механики нет вовсе (или она
        # называется иначе). Пустой экран с нашими подписями врал бы про то,
        # чего у бота нет; отказ виден и понятен.
        raise NotAvailable(
            "Резервного доступа у этого бота нет: в его настройках нет ни одного параметра "
            "grace-доступа (GRACE_ACCESS_*)"
        )
    return defs


# --- ручки -------------------------------------------------------------------
#
# ПРОВЕРКА ПЕРЕД ОБЪЯВЛЕНИЕМ (правило адаптера): наши модули грузятся последними
# и молча перехватывают чужой слот — так промо-баннер однажды отобрал у бота его
# офферы. У «Бедолаги» этих путей нет (они числятся в `todo_admin` карты
# маршрутов), но проверяем не на слово.
#
# Отказываемся именно ImportError'ом: compose.py ловит его у каждого модуля
# отдельно и печатает в лог, адаптер при этом остаётся жив, а наш путь честно
# отвечает 501. Любое другое исключение здесь уронило бы весь кабинет из-за
# одного раздела — лекарство хуже болезни.
_taken = [m for m in ("GET", "PUT") if (m, "/api/admin/reserve") in HANDLERS]
if _taken:
    raise ImportError(
        "слот " + ", ".join(f"{m} /api/admin/reserve" for m in _taken)
        + " уже занят другим модулем — резервный доступ не подключён, чтобы не отобрать чужую ручку"
    )


@handler("GET", "/api/admin/reserve")
async def reserve_get(ctx: Ctx) -> dict[str, Any]:
    return _view(await _load(ctx))


def _changed(payload: dict[str, Any], field: str, current: Any) -> Any:
    """Присланное значение, если оно есть И отличается от текущего; иначе None."""
    if field not in payload:
        return None
    value = payload[field]
    return None if value == current else value


def _plan(payload: dict[str, Any], view: dict[str, Any]) -> dict[str, Any]:
    """Что менять у бота: их ключ → новое значение.

    Проверяем ВСЁ до первой записи: их API пишет по одному ключу и атомарной
    записи пачкой не умеет — упасть на третьем ключе из пяти значит оставить
    резерв полунастроенным (например, включённым без сквадов).
    """
    changes: dict[str, Any] = {}

    # --- режим -------------------------------------------------------------
    mode = view["mode"]
    if (value := _changed(payload, "mode", mode)) is not None:
        candidate = str(value).strip().lower()
        if candidate not in _MODES:
            raise _bad_request(
                f"Режим «{value}» этому боту неизвестен: бывают только «Выключен», "
                "«Наблюдение», «Включён» и «Слив»"
            )
        # Прямой прыжок «Включён → Выключен» не даём. Их рантайм при старте с
        # выключенным режимом и незакрытыми сессиями пишет critical («use drain
        # or restore-all») и НИЧЕГО не чинит: люди остаются висеть на резервном
        # скваде вместо своего. Правильный путь у них же — сначала «Слив», и
        # только когда открытых сессий не осталось, «Выключен». Сосчитать эти
        # сессии адаптеру нечем — ручек grace у бота нет вовсе (проверено по их
        # openapi), поэтому единственная честная защита — провести админа через
        # «Слив». Выход остаётся в один шаг: из «Слива» в «Выключен» можно.
        if mode == "true" and candidate == "false":
            raise _bad_request(
                "Из «Включён» сразу в «Выключен» нельзя: у кого резерв уже выдан, останется "
                "висеть на резервном скваде вместо своего — бот при следующем запуске это "
                "заметит, но чинить не станет. Переведите сначала в «Слив»: новым резерв "
                "выдаваться перестанет, а выданный доведётся до конца и люди вернутся на свои "
                "сквады. После этого «Выключен» примется"
            )
        mode = candidate
        changes[_KEY_MODE] = mode
    elif _changed(payload, "enabled", view["enabled"]) is not None:
        # Старая страница из кэша браузера: у неё вместо режимов тумблер. Тихо
        # перевести его нельзя ни в одну сторону — «выключить» у этого бота
        # значит сначала «Слив» (иначе выданный доступ останется висеть на
        # резервном скваде: их же рантайм пишет об этом critical и ничего не
        # чинит), а «включить» без сквадов оставит функцию мёртвой.
        raise NotAvailable(
            "У этого бота не тумблер, а четыре режима резерва (Выключен / Наблюдение / "
            "Включён / Слив) — обновите страницу кабинета, там появился выбор режима"
        )

    # --- срок --------------------------------------------------------------
    if _changed(payload, "window_days", view["window_days"]) is not None:
        raise NotAvailable(
            "Срок резерва у этого бота считается в ЧАСАХ, а не в днях — обновите страницу "
            "кабинета, там появилось поле «Срок резерва, часов»"
        )
    if (value := _changed(payload, "window_hours", view["window_hours"])) is not None:
        hours = _int(value, 0)
        if hours < 1:
            raise _bad_request("Срок резерва, часов: нужно целое число не меньше 1")
        changes[_KEY_HOURS] = hours

    # --- трафик ------------------------------------------------------------
    gb = view["reserve_gb"]
    if (value := _changed(payload, "reserve_gb", gb)) is not None:
        gb = _int(value, -1)
        if gb < 0:
            raise _bad_request("Резерв трафика, ГБ: нужно целое число не меньше 0")
        changes[_KEY_GB] = gb

    # --- сквады ------------------------------------------------------------
    squads = {"squad_uuid": view["squad_uuid"], "squad_uuid_limited": view["squad_uuid_limited"]}
    for field, key, what in (
        ("squad_uuid", _KEY_SQUAD_EXPIRED, "Сквад для истёкших"),
        ("squad_uuid_limited", _KEY_SQUAD_LIMITED, "Сквад для исчерпавших трафик"),
    ):
        if (value := _changed(payload, field, squads[field])) is not None:
            squads[field] = _uuid_or_empty(value, what)
            changes[key] = squads[field]

    # --- кому положено ------------------------------------------------------
    for field, key in (
        ("trial_enabled", _KEY_TRIAL),
        ("daily_enabled", _KEY_DAILY),
        ("free_enabled", _KEY_FREE),
    ):
        if (value := _changed(payload, field, view[field])) is not None:
            changes[key] = bool(value)

    # --- итог должен быть рабочим ------------------------------------------
    # Проверяем ИТОГОВОЕ состояние, а не только присланные поля: включить резерв
    # можно и не трогая сквады — и тогда после перезапуска бот просто не поднимет
    # грейс (их `_validate_active_configuration`), молча оставив функцию
    # выключенной. Об этом надо сказать сейчас, а не «когда-нибудь не заработает».
    if mode == "true":
        if gb < 1:
            raise _bad_request(
                "Чтобы включить резерв, задайте резерв трафика не меньше 1 ГБ — иначе бот "
                "откажется поднять его при следующем запуске"
            )
        for field, what in (
            ("squad_uuid", "истёкших"),
            ("squad_uuid_limited", "исчерпавших трафик"),
        ):
            if not squads[field]:
                raise _bad_request(
                    f"Чтобы включить резерв, укажите сквад для {what}: у этого бота резерв "
                    "выдаётся только на отдельном скваде, и без него он не запустится"
                )
    return changes


def _applied(defs: dict[str, dict[str, Any]], key: str, value: Any) -> bool:
    """Перечитанное значение совпало с записанным."""
    if isinstance(value, bool):
        return _flag(defs, key) is value
    if isinstance(value, int):
        return _num(defs, key, -1) == value
    return _text(defs, key) == value


@handler("PUT", "/api/admin/reserve")
async def reserve_put(ctx: Ctx) -> dict[str, Any]:
    payload = ctx.payload()
    defs = await _load(ctx)
    view = _view(defs)

    changes = _plan(payload, view)

    for key in changes:
        if key not in defs:
            raise NotAvailable(f"Параметр «{key}» этот бот не знает")

    # Запертые параметры отбиваем ДО записи и целиком — теми же словами, что и в
    # общих настройках: иначе половина правок применится, а вторая упрётся в их
    # 409 по-английски.
    locked = {key: _lock_reason(defs, key) for key in changes if _lock_reason(defs, key)}
    if locked:
        env_keys = sorted(k for k, reason in locked.items() if reason == _ENV_LOCKED)
        ro_keys = sorted(k for k, reason in locked.items() if reason != _ENV_LOCKED)
        parts = []
        if env_keys:
            parts.append(
                "заданы в файле окружения бота (.env), правьте .env и перезапускайте бота: "
                + ", ".join(env_keys)
            )
        if ro_keys:
            parts.append("бот отдаёт только на чтение, из кабинета не меняются: " + ", ".join(ro_keys))
        raise NotAvailable("Не сохранено — " + "; ".join(parts) + ". Ничего не записано.")

    if not changes:
        return view

    # Режим — ПОСЛЕДНИМ. Их API пишет по ключу, и порядок здесь не косметика:
    # если между «включили» и «задали сквад» бота перезапустят, он поднимется
    # включённым без сквадов и грейс останется мёртвым. Обратный порядок такой
    # дырки не оставляет.
    ordered = [(k, v) for k, v in changes.items() if k != _KEY_MODE]
    if _KEY_MODE in changes:
        ordered.append((_KEY_MODE, changes[_KEY_MODE]))
    for key, value in ordered:
        # Их ошибку (403 read-only, 400 «invalid value») отдаём как есть: она про
        # этот конкретный параметр и человеку понятна.
        await ctx.json(f"{_SETTINGS}/{key}", method="PUT", json={"value": value})

    defs = await _load(ctx)
    # Проверяем, что правка применилась: их API принимает и кладёт в базу даже то,
    # что перекрыто файлом окружения (.env), и отвечает 200 при неизменившемся
    # значении. Без проверки кабинет показал бы «Сохранено» на пустом месте.
    stuck = [key for key, value in changes.items() if not _applied(defs, key, value)]
    if stuck:
        saved = len(changes) - len(stuck)
        raise NotAvailable(
            "Не применилось: " + ", ".join(stuck) + ". Так бывает с параметрами, заданными в "
            "файле окружения бота (.env): правку их API принимает, а работает всё равно "
            "значение из окружения — правьте .env и перезапускайте бота."
            + (f" Остальные изменения ({saved}) сохранены." if saved else "")
        )
    return _view(defs)
