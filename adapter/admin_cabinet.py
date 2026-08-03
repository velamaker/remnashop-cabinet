"""Админка, группа «Кабинет»: оформление, доступ и язык, информация, меню,
приложения — плюс отдельный экран «Вход через Telegram».

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ — то же правило, что у `admin_users.py`, `admin_catalog.py`
и `admin_marketing.py`: админских ручек в нашем контракте под сорок, пишутся они
несколькими руками сразу, и один файл на всех — гарантированный конфликт.
Регистрация общая, тем же декоратором `handler`; сам себя модуль не импортирует
— его подхватывает `compose.py` по маске `admin_*.py` (и строка COPY в
`adapter/Dockerfile`, её добавляет тот, кто собирает образ).

ОТКУДА ДАННЫЕ И ПОЧЕМУ ИМЕННО ОТТУДА.

Почти всё здесь — их КАБИНЕТНОЕ API по сессии самого админа (`ctx.call`), и
решение «пускать или нет» принимает их RBAC: оформление и настройки закрыты
правом `settings:read`/`settings:edit`, информационные страницы — `info_pages:*`.
Служебный токен адаптера (`ctx.admin`, X-API-Key) используется РОВНО в двух
местах и только там, где их кабинетное API не отдаёт нужного вовсе:

  • тексты FAQ / правил / политики / оферты — редактируются только их машинным
    `/pages/*` (в кабинетном API их можно лишь прочитать);
  • их авторские кнопки главного меню — только машинный `/main-menu/buttons`.

И даже там токен включается ТОЛЬКО после успешного ответа их же админской ручки
(`/cabinet/admin/info-pages` для текстов, `/cabinet/admin/button-styles` для
кнопок), а на запись дополнительно спрашиваем их RBAC, есть ли у человека право
`info_pages:edit` (`/cabinet/auth/me/permissions`). Иначе вышло бы, что правит
правила сервиса любой, кто вошёл в кабинет: токен принадлежит адаптеру, а не
человеку.

ЧТО ПЕРЕВЕДЕНО ЧЕСТНО.
  • «Оформление» — целиком: имя бренда (`/cabinet/branding/name`), акцент и фоны
    тёмной/светлой темы (`/cabinet/branding/colors`), загрузка и удаление
    логотипа (`/cabinet/branding/logo`). Один в один, включая «Из темы» —
    это возврат к их же цвету по умолчанию.
  • «Информация» — FAQ (их страницы вопрос-ответ), правила, политика, оферта.
    Правится ТОТ ЖЕ текст, который показывает бот, а не подменная копия.
  • «Меню» — цвет, подпись и премиум-эмодзи тех кнопок меню бота, которые ведут
    в кабинет (`/cabinet/admin/button-styles`), и подписи их авторских кнопок.
  • «Вход через Telegram» — их настройки `TELEGRAM_OIDC_*` (экран живёт в группе
    «Система», она пока не включена, но ручка ей понадобится).

ЧЕГО У «БЕДОЛАГИ» НЕТ — И ЧТО МЫ С ЭТИМ ДЕЛАЕМ. Ничего не выдумываем: поле,
которого у них нет, отдаётся в том значении, в каком кабинет РЕАЛЬНО работает
поверх их бота, а попытка его изменить получает 400 с причиной. Молча проглотить
такую правку нельзя — админ был бы уверен, что настроил, а не настроил:
  • тех-работы включает только их бот (живой флаг лежит у них в кэше и
    переключается из их же телеграм-админки — `maintenance_service`), поэтому
    тумблер отдаётся выключенным, «следовать за ботом» — включённым, а текст
    заглушки правится по-настоящему (их `MAINTENANCE_MESSAGE`);
  • «прямая ссылка подписки», «крипто-ссылки» и список языков КАБИНЕТА у них не
    хранятся вовсе (их `AVAILABLE_LANGUAGES` — про языки бота, а не про переводы
    нашего интерфейса, и compose их осознанно не сужает);
  • кнопки доступа в меню («Кабинет в браузере», «Подписка (резерв)», подарки) —
    это кнопки НАШЕГО бота, у их меню другой набор;
  • «Приложения» — выбор приоритетного приложения и свои ссылки установки хранит
    наш бэкенд (assets/apps.json), у бота такого места нет. Чтение отдаём
    честное — «оверрайдов нет, показывается весь каталог» (ровно это же
    отвечает публичная `compose.apps`), а PUT и «Обновить ссылки» отвечают 501 с
    объяснением. Сохранить «в никуда» было бы хуже отказа: настройка не доехала
    бы и до самого кабинета — публичную `/api/apps` собирает compose.

ФОРМАТ ТЕКСТОВ. Их тексты писаны для Telegram (HTML), наши вкладки «Информации»
кабинет рисует мини-markdown'ом. Публичная `compose.info` переводит их HTML в
markdown ДЛЯ ПОКАЗА, но админу мы отдаём и принимаем СЫРОЙ текст, как он лежит у
бота. Иначе первое же «Сохранить» переписало бы их разметку нашим переводом — и
в самом боте правила поехали бы.
"""

from __future__ import annotations

import asyncio
import os
import re
import string
import time
from fnmatch import fnmatchcase
from typing import Any

import httpx

import compose
from compose import Ctx, NotAvailable, UpstreamError, handler


# --- разговор с их API ------------------------------------------------------


def _fail(status: int, detail: str) -> UpstreamError:
    """Отказ самого адаптера в той же форме, что и ошибка бота."""
    return UpstreamError(httpx.Response(status, json={"detail": detail}))


def _bad(message: str) -> UpstreamError:
    """400 «так нельзя», а не 501 «не умеем».

    Разница важна для человека: 501 читается как «раздел не переведён, больше не
    пробуй», а тут дело в конкретном поле — остальное на экране работает.
    """
    return _fail(400, message)


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
    "Name too long (max 50 characters)": "Название длиннее 50 символов",
    "At least one theme must be enabled": "Нельзя выключить обе темы",
    "Invalid file type. Allowed: PNG, JPEG, WebP, SVG":
        "Не тот тип файла: подойдут PNG, JPEG, WebP или SVG",
    "No custom logo set": "Свой логотип не загружен",
    "Setting not found": "Такой настройки у бота нет",
    "Info page not found": "Страница не найдена",
    "Service rules not found": "Правила ещё не заданы",
    "Privacy policy not found": "Политика конфиденциальности ещё не задана",
    "Public offer not found": "Оферта ещё не задана",
    "FAQ page not found": "Вопрос не найден",
    "Main menu button not found": "Кнопка не найдена",
}


def _ru(text: str, need: str | None = None) -> str:
    """Их отказ по-русски. `need` — право, которого требует ЭТА ручка.

    Их RBAC отвечает `Permission denied: <причина>`, где причина — «Permission
    not granted by any role» или «No active roles assigned»
    (app/services/permission_service.py:240,252). Имени недостающего права в
    тексте нет, а знаем его мы — оно зашито в вызывающей ручке. Без имени
    владелец бота не поймёт, что именно выдать в своей админке.

    `need=None` — для машинного API: там прав нет вовсе (пускает токен), и
    подставлять туда имя права значило бы отправить владельца настраивать роль,
    которая на этот отказ никак не влияет.
    """
    text = (text or "").strip()
    known = _ERRORS.get(text)
    if known:
        return known
    # Длина файла и цвета приходят с подставленными значениями — по префиксу.
    if text.startswith("File too large"):
        return "Файл слишком большой: логотип принимается до 5 МБ"
    if text.startswith("Invalid hex color"):
        return f"Некорректный цвет ({text.split(':', 1)[-1].strip()})"
    if need and text.startswith("Permission denied"):
        reason = text.split(":", 1)[1].strip() if ":" in text else ""
        if reason == "No active roles assigned":
            return "Недостаточно прав: в боте этой учётке не выдано ни одной роли"
        return f"Недостаточно прав в боте: нужно право «{need}»"
    return text


async def _send(
    ctx: Ctx, method: str, path: str, need: str, *, missing: Any = ..., **kw: Any
) -> Any:
    """Запрос к их кабинетному API от имени самого админа (его сессией).

    Отдельно от `ctx.json`, потому что здесь нужны три вещи, которых там нет:
    перевод их отказов (`_ru`), сворачивание 5xx в «попробуйте позже» (их
    внутренние трейсы человеку показывать нечего) и `missing` — значение, которым
    подменяется 404. Последнее не «глотание ошибки»: у половины этих ручек 404
    означает «ещё не заполнено» (правил нет, логотипа нет), и это нормальное
    состояние экрана, а не поломка.

    Коды 4xx доезжают до кабинета своими: по 401 он отправляет на вход, 403
    показывает как причину отказа.
    """
    try:
        resp = await ctx.call(method, path, **kw)
    except httpx.HTTPError as exc:
        raise _fail(502, f"Бэкенд недоступен: {exc}") from exc
    if resp.status_code == 404 and missing is not ...:
        return missing
    if resp.status_code >= 500:
        raise _fail(502, "Бот ответил ошибкой — попробуйте позже")
    if resp.status_code >= 400:
        raise _fail(resp.status_code, _ru(_detail(resp), need))
    try:
        return resp.json()
    except ValueError:
        return None


async def _machine(
    ctx: Ctx, method: str, path: str, *, missing: Any = ..., **kw: Any
) -> Any:
    """Их машинное API (X-API-Key) — ТОЛЬКО после проверки прав их же ручкой.

    Сам по себе этот вызов никаких прав не спрашивает: токен выдан адаптеру, и
    бот пустит его куда угодно. Поэтому каждая ручка ниже сначала получает 200 от
    их кабинетного API (то есть их RBAC уже признал этого человека админом) и
    только потом зовёт `_machine`. Порядок именно такой, а не наоборот.
    """
    if not ctx.can_admin:
        raise NotAvailable(
            "Адаптеру не выдан админский токен «Бедолаги» — этот раздел без него "
            "работать не может (BEDOLAGA_ADMIN_TOKEN)"
        )
    resp = await ctx.admin(method, path, **kw)
    if resp is None:
        raise _fail(502, "Бот недоступен — попробуйте позже")
    if resp.status_code == 404 and missing is not ...:
        return missing
    if resp.status_code >= 500:
        raise _fail(502, "Бот ответил ошибкой — попробуйте позже")
    if resp.status_code >= 400:
        raise _fail(resp.status_code, _ru(_detail(resp)))
    if resp.status_code == 204 or not resp.content:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


async def _may(ctx: Ctx, permission: str) -> bool:
    """Есть ли у этого человека право в ИХ RBAC.

    Спрашиваем их же ручку `/cabinet/auth/me/permissions` и сравниваем ровно по
    их правилу: право пользователя — это ШАБЛОН (`*:*`, `info_pages:*`), а
    требуемое право — строка (`permission_service.permission_matches`). Берём
    `fnmatchcase`, чтобы результат не зависел от регистра файловой системы.

    Зачем это вообще нужно: писать тексты приходится их машинным API, у которого
    прав нет вовсе. Значит право обязан подтвердить кто-то другой — и это их
    RBAC, а не мы. Оговорка, о которой стоит знать: их `check_permission` кроме
    ролей учитывает ещё ABAC-политики (точечный `deny` на конкретное право), а
    список прав их не показывает. Точечный запрет именно на `info_pages:edit`
    при разрешённом `info_pages:read` мы не увидим — редкий случай, но честнее
    сказать о нём здесь, чем делать вид, что проверка полная.

    Не смогли спросить — считаем, что права нет (fail-closed).
    """
    data = await ctx.json("/cabinet/auth/me/permissions", {}, soft=True) or {}
    granted = data.get("permissions")
    if not isinstance(granted, list):
        return False
    return any(
        isinstance(p, str) and fnmatchcase(permission, p) for p in granted
    )


async def _require(ctx: Ctx, permission: str) -> None:
    if not await _may(ctx, permission):
        raise _fail(
            403, f"Недостаточно прав в боте: нужно право «{permission}»"
        )


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


# --- ОФОРМЛЕНИЕ и ДОСТУП/ЯЗЫК ----------------------------------------------
#
# Оба экрана кабинета («Оформление» и «Доступ и язык») ходят в одну и ту же пару
# ручек `/api/admin/appearance`, только шлют разные поля. Поэтому PUT применяет
# ровно то, что пришло: экран бренда не должен трогать тех-работы, и наоборот.

# Их цвета по умолчанию (app/cabinet/routes/branding.py:323). Нужны для кнопки
# «Из темы»: у нас сброс цвета — это пустая строка, а их PATCH принимает только
# валидный hex и понятия «верни как было» не имеет. Отдельной ручки сброса
# одного цвета у них тоже нет (`/colors/reset` сбрасывает все двенадцать сразу,
# включая те, которых наш экран не показывает, — это была бы правка вслепую).
_DEFAULT_COLORS = {
    "accent": "#3b82f6",
    "darkBackground": "#0a0f1a",
    "lightBackground": "#F7E7CE",
}

# Ключ их настройки с текстом заглушки тех-работ. Читается ещё и как проверка
# прав: право `settings:read` — ровно то, без которого нечего делать на экране
# оформления (правка требует `settings:edit`). Сами `/cabinet/branding*` на
# чтение публичные, и опираться на них как на пропуск нельзя.
_MAINTENANCE_MESSAGE = "MAINTENANCE_MESSAGE"

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


async def _setting(ctx: Ctx, key: str, need: str = "settings:read") -> Any:
    """Значение их настройки (то, что реально действует сейчас)."""
    data = await _send(ctx, "GET", f"/cabinet/admin/settings/{key}", need)
    return data.get("current") if isinstance(data, dict) else None


async def _set_setting(ctx: Ctx, key: str, value: Any, what: str) -> Any:
    """Запись их настройки с проверкой, что она ПРИМЕНИЛАСЬ.

    У них есть тихая ловушка: ключ, заданный переменной окружения, сохраняется в
    базу, но не применяется — `set_value` пишет в лог «значение задаётся через
    окружение» и идёт дальше (app/services/system_settings_service.py:1692).
    Ответ при этом 200, и кабинет отрапортовал бы «Сохранено», а текст остался бы
    прежним. Поэтому сверяем `current` из их же ответа с тем, что просили.
    """
    data = await _send(
        ctx, "PUT", f"/cabinet/admin/settings/{key}", "settings:edit",
        json={"value": value},
    )
    applied = data.get("current") if isinstance(data, dict) else None
    if applied != value:
        raise _bad(
            f"{what} задаётся переменной окружения бота ({key}) — через API "
            "его не поменять, правьте .env бота и перезапускайте его"
        )
    return applied


def _logo(brand: Any) -> dict[str, Any]:
    """Адрес логотипа для кабинета: их байты отдаёт наш `/api/appearance/logo`.

    Метка времени — не украшение: их отдача логотипа стоит с
    `Cache-Control: max-age=3600` (branding.py:432), и без неё админ ещё час
    видел бы на экране старую картинку и думал, что загрузка не сработала.
    Метка только на админском экране; публичный адрес (compose.appearance)
    остаётся постоянным, чтобы логотип кабинета кэшировался у посетителей.
    """
    has = isinstance(brand, dict) and brand.get("has_custom_logo")
    if not has:
        return {"logo_url": None}
    return {"logo_url": f"/api/appearance/logo?v={int(time.time())}"}


async def _appearance_view(ctx: Ctx) -> dict[str, Any]:
    """Ответ обоих экранов. Права спрашиваем ПЕРВЫМ запросом (см. `_MAINTENANCE_MESSAGE`)."""
    message = await _setting(ctx, _MAINTENANCE_MESSAGE)
    brand, colors = await asyncio.gather(
        _send(ctx, "GET", "/cabinet/branding", "settings:read", missing={}),
        _send(ctx, "GET", "/cabinet/branding/colors", "settings:read", missing={}),
    )
    brand = brand if isinstance(brand, dict) else {}
    colors = colors if isinstance(colors, dict) else {}
    name = brand.get("name")
    name = name if isinstance(name, str) else ""
    dark = colors.get("darkBackground")
    return {
        # У них имя всегда конкретное (пусто = «только логотип», их же режим), а
        # у нас пусто означает «подставить имя бота». Отдаём как есть: пустое
        # поле в форме — это их пустое имя, а не наша выдумка.
        "brand_name": name,
        "brand_name_resolved": name or "VPN",
        "accent": colors.get("accent"),
        # legacy-поле: кабинет берёт его, когда нет пары тем.
        "background": dark,
        "background_dark": dark,
        "background_light": colors.get("lightBackground"),
        # Их логотип отдаётся байтами по своему пути — заворачиваем в наш,
        # как это уже делает публичная `compose.appearance`.
        **_logo(brand),
        # Дальше — то, чего у бота нет: значения ровно те, как кабинет работает
        # поверх него (см. шапку файла). Менять их PUT не даст.
        "sub_link_enabled": True,
        "crypto_links_enabled": False,
        "maintenance_enabled": False,
        "maintenance_follow_bot": True,
        "maintenance_message": message if isinstance(message, str) else "",
        "maintenance_block_login": True,
        "maintenance_block_registration": True,
        "maintenance_block_payments": True,
        "enabled_languages": None,
    }


@handler("GET", "/api/admin/appearance")
async def appearance_get(ctx: Ctx) -> dict[str, Any]:
    return await _appearance_view(ctx)


# Поля, которых у «Бедолаги» нет: значение, при котором кабинет и правда так
# работает, и объяснение для админа. Проверяем ДО первой записи — иначе половина
# формы сохранилась бы, а половина отвалилась.
_FIXED: dict[str, tuple[Any, str]] = {
    "sub_link_enabled": (
        True,
        "Прямую ссылку подписки кабинет поверх «Бедолаги» показывает всегда: "
        "общего запрета их API не отдаёт",
    ),
    "crypto_links_enabled": (
        False,
        "Крипто-ссылки собирает наш бэкенд, у «Бедолаги» их нет",
    ),
    "maintenance_enabled": (
        False,
        "Тех-работы включает сам бот (его админ-панель), кабинет только "
        "закрывается вместе с ним",
    ),
    "maintenance_follow_bot": (
        True,
        "Кабинет поверх «Бедолаги» всегда следует за тех-работами бота — "
        "отключить эту связь нечем",
    ),
    "maintenance_block_login": (
        True,
        "В тех-работы их API не пускает никого, кроме админов, — выборочно "
        "разрешить вход нельзя",
    ),
    "maintenance_block_registration": (
        True,
        "В тех-работы их API закрыто целиком, включая регистрацию",
    ),
    "maintenance_block_payments": (
        True,
        "В тех-работы их API закрыто целиком, включая оплату",
    ),
}


@handler("PUT", "/api/admin/appearance")
async def appearance_put(ctx: Ctx) -> dict[str, Any]:
    body = ctx.payload()

    for key, (fixed, why) in _FIXED.items():
        if key in body and bool(body[key]) is not fixed:
            raise _bad(why)
    languages = body.get("enabled_languages")
    if isinstance(languages, list) and languages:
        raise _bad(
            "Список языков кабинета «Бедолага» не хранит: её настройка языков — "
            "про язык бота, а переводы кабинета живут в нём самом"
        )

    current = await _appearance_view(ctx)

    if "brand_name" in body:
        name = body.get("brand_name")
        name = name.strip() if isinstance(name, str) else ""
        if name != current["brand_name"]:
            await _send(
                ctx, "PUT", "/cabinet/branding/name", "settings:edit",
                json={"name": name},
            )

    # Цвета: пустая строка — это кнопка «Из темы», то есть вернуть их дефолт.
    patch: dict[str, str] = {}
    for ours, theirs in (
        ("accent", "accent"),
        ("background_dark", "darkBackground"),
        ("background_light", "lightBackground"),
    ):
        # Старый общий `background` — фолбэк для тёмной темы: так его понимает и
        # сам кабинет, когда пары тем нет.
        raw = body.get(ours)
        if raw is None and ours == "background_dark":
            raw = body.get("background")
        if raw is None:
            continue
        value = raw.strip() if isinstance(raw, str) else ""
        value = value or _DEFAULT_COLORS[theirs]
        if not _HEX.match(value):
            raise _bad(f"Некорректный цвет: {value}")
        if value.lower() != str(current[ours] or "").lower():
            patch[theirs] = value
    if patch:
        await _send(
            ctx, "PATCH", "/cabinet/branding/colors", "settings:edit", json=patch
        )

    if "maintenance_message" in body:
        message = body.get("maintenance_message")
        message = message if isinstance(message, str) else ""
        if message != current["maintenance_message"]:
            await _set_setting(
                ctx, _MAINTENANCE_MESSAGE, message, "Текст экрана тех-работ"
            )

    return await _appearance_view(ctx)


# --- логотип ----------------------------------------------------------------

# Границу multipart берём из САМОГО тела, потому что до обработчика доезжают
# только байты: `Ctx` заголовков запроса не отдаёт (main.py:730). По RFC 2046
# тело обязано начинаться со строки `--<boundary>`, так что это не догадка, а
# разбор первой строки. Всё, что на неё не похоже, отвергаем — вслепую
# проксировать чужие байты как multipart нельзя.
_BOUNDARY_CHARS = set(string.ascii_letters + string.digits + "'()+_,-./:=? ")


def _multipart_type(body: bytes) -> str | None:
    head = body[:256].split(b"\r\n", 1)[0]
    if not head.startswith(b"--") or not (3 <= len(head) <= 72):
        return None
    try:
        boundary = head[2:].decode("ascii")
    except UnicodeDecodeError:
        return None
    if not boundary or boundary.endswith(" "):
        return None
    if any(ch not in _BOUNDARY_CHARS for ch in boundary):
        return None
    return f"multipart/form-data; boundary={boundary}"


@handler("POST", "/api/admin/appearance/logo")
async def appearance_logo_upload(ctx: Ctx) -> dict[str, Any]:
    # Сырое тело запроса: multipart через `payload()` не разобрать, а публичного
    # доступа к байтам у Ctx нет. Через getattr — чтобы переименование внутри
    # compose.py стоило нам одной сломанной кнопки, а не всего модуля.
    body = getattr(ctx, "_body", b"")
    content_type = _multipart_type(body)
    if content_type is None:
        raise _bad("Файл не получен — попробуйте выбрать логотип ещё раз")
    brand = await _send(
        ctx, "POST", "/cabinet/branding/logo", "settings:edit",
        content=body, headers={"content-type": content_type},
    )
    return _logo(brand)


@handler("DELETE", "/api/admin/appearance/logo")
async def appearance_logo_delete(ctx: Ctx) -> dict[str, Any]:
    brand = await _send(ctx, "DELETE", "/cabinet/branding/logo", "settings:edit")
    return _logo(brand)


# --- ИНФОРМАЦИЯ -------------------------------------------------------------
#
# Пять вкладок нашего экрана против четырёх сущностей бота:
#   FAQ         → их страницы вопрос-ответ (`/pages/faq`, у каждой title+content);
#   Правила     → `/pages/service-rules`;
#   Политика    → `/pages/privacy-policy`;
#   Оферта      → `/pages/public-offer`;
#   Статусы     → текста нет и быть не может: это подсказка самого кабинета.
#
# Почему не через их кабинетный CMS `/cabinet/admin/info-pages`. Там можно
# завести страницу с `replaces_tab` и подменить вкладку — но это КОПИЯ поверх, а
# не тот текст, который бот показывает в Telegram: правила разъехались бы с
# кабинетом. Правим первоисточник.


async def _default_lang(ctx: Ctx) -> str:
    """Язык, на котором бот отвечает по умолчанию.

    Наш экран правит ОДИН текст без выбора языка, значит писать надо в язык по
    умолчанию — на остальные у них работает фолбэк. Их публичная ручка эту
    величину отдаёт сама, гадать не нужно.
    """
    data = await ctx.json("/cabinet/info/languages", {}, soft=True) or {}
    code = data.get("default") if isinstance(data, dict) else None
    code = (code or "ru").split("-", 1)[0].lower()
    return code or "ru"


async def _info_gate(ctx: Ctx) -> None:
    """Пропуск на экран «Информация» — их же ручка и их же RBAC.

    `/cabinet/admin/info-pages` требует `info_pages:read`; ответ нам не нужен,
    нужен сам факт «их бот признал этого человека админом раздела». Только после
    этого включается машинный токен (см. `_machine`).
    """
    await _send(ctx, "GET", "/cabinet/admin/info-pages", "info_pages:read")


async def _rich(ctx: Ctx, path: str, public: str, lang: str) -> str:
    """Текст правил/политики/оферты «как он есть у бота».

    Если в базе пусто, их машинная ручка отвечает 404, а пользователю бот в это
    время показывает встроенный текст по умолчанию. Пустое поле в админке в
    такой ситуации — ложь наполовину: админ решит, что текста нет вовсе, и
    напишет свой с нуля. Поэтому в этом случае подставляем их же действующий
    текст из публичной ручки — ровно то, что видит человек.

    Той же публичной ручкой обходимся, когда адаптеру не выдан админский токен
    бота: показать текст всё равно можно, а вот сохранить — уже нет (см. PUT).
    """
    page = None
    if ctx.can_admin:
        page = await _machine(ctx, "GET", path, missing=None, params={"language": lang})
    if isinstance(page, dict) and isinstance(page.get("content"), str):
        return page["content"]
    fallback = await ctx.json(public, {}, soft=True, params={"language": lang}) or {}
    content = fallback.get("content") if isinstance(fallback, dict) else None
    return content if isinstance(content, str) else ""


async def _faq_pages(ctx: Ctx, lang: str) -> list[dict[str, Any]]:
    """Их вопросы-ответы в порядке показа. `include_inactive` — намеренно: в
    админке видно всё, что заведено, иначе скрытый вопрос был бы «потерян» и
    следующее сохранение его удалило бы.

    Без админского токена читаем публичный список: там только включённые
    вопросы, но это лучше пустого экрана — а править их всё равно нечем.
    """
    if not ctx.can_admin:
        rows = await ctx.json(
            "/cabinet/info/faq", [], soft=True, params={"language": lang}
        )
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    data = await _machine(
        ctx, "GET", "/pages/faq", missing={},
        params={"language": lang, "include_inactive": "true"},
    )
    items = data.get("items") if isinstance(data, dict) else None
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


async def _info_view(ctx: Ctx, lang: str) -> dict[str, Any]:
    faq = await _faq_pages(ctx, lang)
    rules, privacy, offer = await asyncio.gather(
        _rich(ctx, "/pages/service-rules", "/cabinet/info/rules", lang),
        _rich(ctx, "/pages/privacy-policy", "/cabinet/info/privacy-policy", lang),
        _rich(ctx, "/pages/public-offer", "/cabinet/info/public-offer", lang),
    )
    return {
        "faq": [
            {"q": _text(p.get("title")), "a": p.get("content") or ""} for p in faq
        ],
        "rules": rules,
        "privacy": privacy,
        "offer": offer,
        # Расшифровка статусов — текст самого кабинета: отдаём ту же константу,
        # что и публичная `compose.info`, чтобы админ видел то же, что читатель.
        # Через getattr, а не импортом приватного имени: переименуют — вкладка
        # останется пустой, но раздел не рухнет.
        "statuses": getattr(compose, "_STATUSES_MD", ""),
    }


@handler("GET", "/api/admin/info")
async def info_get(ctx: Ctx) -> dict[str, Any]:
    await _info_gate(ctx)
    return await _info_view(ctx, await _default_lang(ctx))


@handler("PUT", "/api/admin/info")
async def info_put(ctx: Ctx) -> dict[str, Any]:
    body = ctx.payload()
    await _info_gate(ctx)
    if not ctx.can_admin:
        # Читать тексты можно и без токена (публичные ручки), а править — нет:
        # в кабинетном API «Бедолаги» таких ручек нет вовсе. Говорим об этом
        # прямо, иначе «Сохранить» будет молча ничего не делать.
        raise NotAvailable(
            "Тексты «Информации» правятся машинным API «Бедолаги», а адаптеру не "
            "выдан её админский токен (BEDOLAGA_ADMIN_TOKEN)"
        )
    # Токен адаптера прав не спрашивает — право на ПРАВКУ подтверждает их RBAC.
    await _require(ctx, "info_pages:edit")
    lang = await _default_lang(ctx)
    current = await _info_view(ctx, lang)

    if "statuses" in body and body.get("statuses") != current["statuses"]:
        raise _bad(
            "«Статусы» — подсказка самого кабинета, у бота такого текста нет: "
            "менять её здесь нечем"
        )

    # Правила/политика/оферта. Пустой текст не пишем: их ручка требует content и
    # записала бы пустую страницу вместо встроенного текста бота.
    for key, path, extra in (
        ("rules", "/pages/service-rules", {}),
        ("privacy", "/pages/privacy-policy", {"is_enabled": True}),
        ("offer", "/pages/public-offer", {"is_enabled": True}),
    ):
        if key not in body:
            continue
        text = body.get(key)
        text = text if isinstance(text, str) else ""
        if text == current[key]:
            continue
        if not text.strip():
            raise _bad("Текст не может быть пустым — бот покажет его как есть")
        # is_enabled=True шлём вместе с текстом: у выключенной страницы их
        # публичная ручка отвечает 404, и написанное просто не появилось бы.
        await _machine(
            ctx, "PUT", path, json={"language": lang, "content": text, **extra}
        )

    if "faq" in body:
        await _save_faq(ctx, lang, body.get("faq"), current["faq"])

    return await _info_view(ctx, lang)


async def _save_faq(ctx: Ctx, lang: str, wanted: Any, current: list[dict[str, Any]]) -> None:
    """Список вопросов кабинета → их страницы FAQ.

    У нас список без идентификаторов (экран шлёт весь массив целиком), у них —
    строки с id. Поэтому сопоставляем по ПОРЯДКУ: i-й вопрос кабинета правит i-ю
    страницу бота, лишние вопросы создаются, лишние страницы удаляются. Это
    ровно то, что сделал админ на экране: он видел тот же порядок, в котором мы
    отдали список.
    """
    if not isinstance(wanted, list):
        return
    items = [i for i in wanted if isinstance(i, dict)]
    if [{"q": _text(i.get("q")), "a": i.get("a") or ""} for i in items] == current:
        return

    pages = await _faq_pages(ctx, lang)
    for index, item in enumerate(items):
        title = _text(item.get("q"))
        content = item.get("a") if isinstance(item.get("a"), str) else ""
        if not title:
            raise _bad(f"Вопрос {index + 1}: у бота вопрос не может быть без заголовка")
        if index < len(pages):
            page = pages[index]
            if (
                _text(page.get("title")) == title
                and (page.get("content") or "") == content
                and page.get("is_active")
            ):
                continue
            await _machine(
                ctx, "PUT", f"/pages/faq/{page.get('id')}",
                json={
                    "title": title, "content": content,
                    "display_order": index, "is_active": True,
                },
            )
        else:
            await _machine(
                ctx, "POST", "/pages/faq",
                json={
                    "language": lang, "title": title, "content": content,
                    "display_order": index, "is_active": True,
                },
            )
    for page in pages[len(items):]:
        await _machine(ctx, "DELETE", f"/pages/faq/{page.get('id')}")

    if items:
        # У них FAQ ещё и включается целиком, по языку: при выключенном разделе
        # их публичная ручка отдаёт пустой список (faq_service.py:95), и только
        # что написанные вопросы никто бы не увидел.
        status = await _machine(
            ctx, "GET", "/pages/faq/status", missing={}, params={"language": lang}
        )
        if not (isinstance(status, dict) and status.get("is_enabled")):
            await _machine(
                ctx, "PUT", "/pages/faq/status",
                json={"language": lang, "is_enabled": True},
            )


# --- МЕНЮ -------------------------------------------------------------------
#
# Экран «Меню» описывает кнопки НАШЕГО бота, и переводится из него не всё.
#
# Что у «Бедолаги» есть: `/cabinet/admin/button-styles` — цвет, своя подпись и
# премиум-эмодзи для тех кнопок её меню, которые открывают кабинет (их разделы
# home / subscription / balance / referral / support / info / admin / language,
# app/utils/button_styles_cache.py). Это ровно то же, чем управляет наш блок
# «Навигация», поэтому четыре пункта ложатся один в один.
#
# Чего у неё нет: кнопок доступа («Личный кабинет», «Кабинет в браузере»,
# «Подключиться» ссылкой, «Подписка (резерв)»), их порядка и кнопки подарков —
# у её меню другой набор и другой способ открытия. Такие пункты отдаются
# выключенными, а попытка их включить или подписать получает 400 с причиной.
# Молчать нельзя: галочка, которая «сохранилась» и ничего не сделала, хуже отказа.
_SECTION: dict[str, str] = {
    "nav_dashboard": "home",
    "nav_subscription": "subscription",
    "nav_invite": "referral",
    "nav_support": "support",
}
# «Устройства» у них отдельной кнопкой не стилизуются: их «Подключиться»
# (subscription_connect) относится к разделу subscription, то есть красится
# вместе с «Подпиской» (CALLBACK_TO_SECTION). Отдать сюда ту же секцию значило
# бы, что правка одной кнопки молча меняет вторую.
_NO_SECTION = ("nav_devices",)
_ACCESS_KEYS = (
    "cabinet_miniapp", "cabinet_url", "connect_miniapp", "connect_url", "remna_sub",
)
_PREMIUM = re.compile(r'<tg-emoji emoji-id="(\d+)">([^<]*)</tg-emoji>')


def _menu_view(styles: dict[str, Any], lang: str) -> dict[str, Any]:
    texts: dict[str, str] = {}
    colors: dict[str, str] = {}
    for key, section in _SECTION.items():
        cfg = styles.get(section)
        if not isinstance(cfg, dict):
            continue
        labels = cfg.get("labels")
        label = labels.get(lang) if isinstance(labels, dict) else ""
        label = label if isinstance(label, str) else ""
        emoji_id = _text(cfg.get("icon_custom_emoji_id"))
        if emoji_id:
            # Их премиум-эмодзи хранится отдельным полем, у нас — тегом в тексте.
            # Фолбэк («⭐») их модель не хранит вовсе: Telegram подставляет сам
            # текст кнопки, а в нашем редакторе это просто значок в поле ввода —
            # при сохранении тег снимается целиком, так что ничего не теряется.
            tag = f'<tg-emoji emoji-id="{emoji_id}">⭐</tg-emoji>'
            label = f"{tag} {label}".strip()
        texts[key] = label
        style = _text(cfg.get("style"))
        # У них «без цвета» называется default, у нас — пустая строка.
        colors[key] = "" if style in ("", "default") else style
    return {
        # Кнопок доступа у «Бедолаги» нет — отдаём выключенными, это правда: в
        # её меню таких кнопок не показывается.
        **{key: False for key in _ACCESS_KEYS},
        "gift": False,
        # Своей мини-аппы у них тоже нет: поле ссылки отдаём пустым, чтобы
        # оно было управляемым, а попытку задать адрес ниже честно отклоняем.
        "custom_url": "",
        # Порядок относится к тем же кнопкам доступа: своего порядка у нас нет.
        "order": [],
        "texts": texts,
        "colors": colors,
    }


async def _menu_styles(ctx: Ctx) -> dict[str, Any]:
    data = await _send(ctx, "GET", "/cabinet/admin/button-styles", "settings:read")
    return data if isinstance(data, dict) else {}


@handler("GET", "/api/admin/menu")
async def menu_get(ctx: Ctx) -> dict[str, Any]:
    styles, lang = await asyncio.gather(_menu_styles(ctx), _default_lang(ctx))
    return _menu_view(styles, lang)


@handler("PUT", "/api/admin/menu")
async def menu_put(ctx: Ctx) -> dict[str, Any]:
    body = ctx.payload()
    # custom_miniapp — наша кнопка своей мини-аппы: её рисует НАШ бот, у «Бедолаги»
    # такого меню нет вовсе. Молча «сохранить» и ничего не сделать — худший вариант.
    for key in (*_ACCESS_KEYS, "gift", "custom_miniapp"):
        if body.get(key):
            raise _bad(
                "У «Бедолаги» такой кнопки в меню нет: её главное меню собрано "
                "из своих кнопок, включить нашу нечем"
            )
    if body.get("custom_url"):
        raise _bad(
            "Своё мини-приложение кнопкой в меню «Бедолаги» не добавить: её главное "
            "меню собрано из своих кнопок. Добавьте её через раздел «Дополнительные кнопки»"
        )
    if body.get("order"):
        raise _bad(
            "Порядок кнопок доступа менять нечему: у «Бедолаги» этих кнопок нет"
        )

    styles, lang = await asyncio.gather(_menu_styles(ctx), _default_lang(ctx))
    current = _menu_view(styles, lang)

    patch: dict[str, dict[str, Any]] = {}
    for field in ("texts", "colors"):
        values = body.get(field)
        if not isinstance(values, dict):
            continue
        for key, value in values.items():
            if key in _SECTION:
                continue
            if str(value or "") == str(current[field].get(key) or ""):
                continue  # ничего не поменяли — молчим
            if key in _NO_SECTION:
                raise _bad(
                    "«Устройства» у «Бедолаги» красятся вместе с «Подпиской» — "
                    "отдельно эту кнопку не настроить"
                )
            raise _bad(
                "Эту кнопку меню настраивает наш бот, у «Бедолаги» её нет"
            )

    for key, section in _SECTION.items():
        changes: dict[str, Any] = {}
        texts = body.get("texts")
        if isinstance(texts, dict) and key in texts:
            raw = texts.get(key)
            raw = raw if isinstance(raw, str) else ""
            if raw != current["texts"].get(key, ""):
                found = _PREMIUM.search(raw)
                label = _PREMIUM.sub("", raw).strip()
                changes["icon_custom_emoji_id"] = found.group(1) if found else ""
                cfg = styles.get(section)
                labels = cfg.get("labels") if isinstance(cfg, dict) else None
                labels = dict(labels) if isinstance(labels, dict) else {}
                # Их PATCH заменяет словарь подписей целиком — доливаем свою
                # строку к уже сохранённым языкам, иначе правка русской подписи
                # стёрла бы английскую.
                if label:
                    labels[lang] = label
                else:
                    labels.pop(lang, None)
                changes["labels"] = labels
        colors = body.get("colors")
        if isinstance(colors, dict) and key in colors:
            value = _text(colors.get(key))
            if value != current["colors"].get(key, ""):
                changes["style"] = value or "default"
        if changes:
            patch[section] = changes

    if patch:
        await _send(
            ctx, "PATCH", "/cabinet/admin/button-styles", "settings:edit", json=patch
        )
        styles = await _menu_styles(ctx)
    return _menu_view(styles, lang)


# --- дополнительные кнопки бота ---------------------------------------------
#
# Наш блок «Дополнительные кнопки» — авторские кнопки главного меню (реклама,
# соглашения, свои разделы). У «Бедолаги» это `/main-menu/buttons`, и живёт оно
# только в машинном API: в кабинетном таких ручек нет вовсе. Поэтому — токен, но
# строго после проверки прав их же ручкой (`settings:read` на стилях кнопок).
#
# Цвета у их кнопок нет: модель хранит текст, действие, видимость и порядок.
# Отдаём `color: null` и пустой список доступных цветов, а попытку покрасить
# честно отклоняем — иначе выбранный цвет «сохранился» бы в никуда.


def _bot_button(row: dict[str, Any]) -> dict[str, Any]:
    return {
        # Их id вместо нашего порядкового номера: по нему же уходит и правка,
        # а порядковый у них не уникален (display_order можно повторить).
        "index": row.get("id"),
        "text": row.get("text") or "",
        "type": row.get("action_type") or "",
        "is_active": bool(row.get("is_active")),
        "color": None,
    }


async def _bot_buttons(ctx: Ctx) -> list[dict[str, Any]]:
    """Список их авторских кнопок. Права спрашивать ОБЯЗАН вызывающий: токен
    адаптера сам по себе пропуском не является (см. `_machine`)."""
    data = await _machine(ctx, "GET", "/main-menu/buttons", params={"limit": 100})
    items = data.get("items") if isinstance(data, dict) else None
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


@handler("GET", "/api/admin/menu/buttons")
async def menu_buttons_get(ctx: Ctx) -> dict[str, Any]:
    await _menu_styles(ctx)  # пропуск: их RBAC, право settings:read
    rows = await _bot_buttons(ctx)
    return {"buttons": [_bot_button(r) for r in rows], "colors": []}


@handler("PUT", "/api/admin/menu/buttons")
async def menu_buttons_put(ctx: Ctx) -> dict[str, Any]:
    body = ctx.payload()
    colors = body.get("colors")
    if isinstance(colors, dict) and any(v for v in colors.values()):
        raise _bad(
            "Цвет у кнопок меню «Бедолаги» не настраивается: её кнопка хранит "
            "текст, действие и видимость"
        )

    await _menu_styles(ctx)  # пропуск: их RBAC, право settings:read
    # Правка кнопок — мутация, и одного «может смотреть» мало. У самих
    # `/main-menu/buttons` прав нет вовсе (машинный токен), поэтому право на
    # запись подтверждает их же RBAC — тем же `settings:edit`, которым закрыты
    # соседние настройки меню (`/cabinet/admin/button-styles`).
    await _require(ctx, "settings:edit")
    rows = await _bot_buttons(ctx)
    known = {str(r.get("id")): r for r in rows}
    texts = body.get("texts")
    if isinstance(texts, dict):
        for index, value in texts.items():
            row = known.get(str(index))
            if row is None:
                raise _bad("Такой кнопки в меню бота больше нет — обновите страницу")
            text = _text(value)
            if not text:
                raise _bad("Текст кнопки не может быть пустым")
            if text == (row.get("text") or ""):
                continue
            await _machine(
                ctx, "PATCH", f"/main-menu/buttons/{row.get('id')}",
                json={"text": text},
            )

    rows = await _bot_buttons(ctx)
    return {"buttons": [_bot_button(r) for r in rows], "colors": []}


# --- ПРИЛОЖЕНИЯ -------------------------------------------------------------
#
# Каталог приложений — фича самого кабинета: список зашит в него, а эта ручка
# отдаёт лишь выбор админа (приоритетное приложение, что показывать, свои
# ссылки). Хранит этот выбор наш бэкенд в assets/apps.json; у «Бедолаги» такого
# места нет — её `/cabinet/admin/apps/*` про другое (какой конфиг страницы
# подписки Remnawave отдавать боту).
#
# Поэтому чтение честное и ровно то же, что у публичной `compose.apps`:
# «оверрайдов нет» — кабинет покажет весь каталог, как он и делает сейчас для
# пользователей. А запись не притворяется работающей: и PUT, и «Обновить ссылки»
# отвечают 501 со своей причиной. Сохранять было бы вдвойне неверно — даже
# записав куда-то выбор, показать его пользователю нечем: публичную `/api/apps`
# собирает compose и ничего про такое хранилище не знает.


@handler("GET", "/api/admin/apps")
async def apps_get(ctx: Ctx) -> dict[str, Any]:
    # Ответ ничего не раскрывает, но путь админский, и решать, кто админ, должен
    # их бот, а не мы: без этой проверки /api/admin/apps отвечал бы 200 любому
    # вошедшему в кабинет. Права раздела здесь ни при чём — экран ничего не
    # читает из бота, поэтому и спрашиваем только «админ ли».
    if not (await ctx.json("/cabinet/auth/me/is-admin", {}) or {}).get("is_admin"):
        raise _fail(403, "Раздел доступен только администраторам бота")
    return {"priority": None, "enabled": None, "custom": []}


@handler("PUT", "/api/admin/apps")
async def apps_put(_: Ctx) -> dict[str, Any]:
    raise NotAvailable(
        "«Бедолага» не хранит настройки каталога приложений: приоритет, состав и "
        "свои ссылки живут в нашем бэкенде. Кабинет показывает полный каталог"
    )


@handler("POST", "/api/admin/apps/refresh-links")
async def apps_refresh(_: Ctx) -> dict[str, Any]:
    raise NotAvailable(
        "Ссылки установки подтягивает наш бэкенд и хранит у себя — «Бедолаге» их "
        "негде сохранить"
    )


# --- ВХОД ЧЕРЕЗ TELEGRAM ----------------------------------------------------
#
# Экран живёт в группе «Система» (она пока не включена целиком), но ручка ей
# нужна, и переводится она полностью: у «Бедолаги» те же три настройки OIDC.
# Секрет наружу не отдаём — только признак «задан», как и наш бэкенд.
_OIDC_ENABLED = "TELEGRAM_OIDC_ENABLED"
_OIDC_ID = "TELEGRAM_OIDC_CLIENT_ID"
_OIDC_SECRET = "TELEGRAM_OIDC_CLIENT_SECRET"


def _redirect_uri() -> str:
    """Готовый Redirect URI для @BotFather → Web Login.

    Считаем как наш бэкенд: адрес кабинета + фиксированный путь. Публичный адрес
    адаптеру известен из `ADAPTER_ALLOWED_ORIGINS` (установщик кладёт туда
    WEB_CABINET_URL), и это единственное место, где он у нас есть. Не знаем —
    отдаём пусто: подсказка с неверным адресом хуже её отсутствия.
    """
    origins = (os.environ.get("ADAPTER_ALLOWED_ORIGINS") or "").split(",")
    base = next((o.strip().rstrip("/") for o in origins if o.strip()), "")
    return f"{base}/api/auth/telegram/oidc/callback" if base else ""


async def _auth_view(ctx: Ctx) -> dict[str, Any]:
    enabled, client_id, secret = await asyncio.gather(
        _setting(ctx, _OIDC_ENABLED),
        _setting(ctx, _OIDC_ID),
        _setting(ctx, _OIDC_SECRET),
    )
    client_id = _text(client_id)
    return {
        "telegram_oidc_client_id": client_id,
        # Значение секрета не показываем: экрану нужен только признак.
        "has_secret": bool(_text(secret)),
        "telegram_oidc_enabled_setting": bool(enabled),
        # Их же формула: тумблер И заданный client_id (branding.py:903).
        "telegram_oidc_active": bool(enabled) and bool(client_id),
        "redirect_uri": _redirect_uri(),
    }


@handler("GET", "/api/admin/auth-settings")
async def auth_settings_get(ctx: Ctx) -> dict[str, Any]:
    return await _auth_view(ctx)


@handler("PUT", "/api/admin/auth-settings")
async def auth_settings_put(ctx: Ctx) -> dict[str, Any]:
    body = ctx.payload()
    current = await _auth_view(ctx)

    if "telegram_oidc_client_id" in body:
        client_id = _text(body.get("telegram_oidc_client_id"))
        if client_id != current["telegram_oidc_client_id"]:
            await _set_setting(ctx, _OIDC_ID, client_id, "Client ID")
    if "telegram_oidc_client_secret" in body:
        secret = _text(body.get("telegram_oidc_client_secret"))
        # Пустое поле в форме = «оставить как было» (так же ведёт себя наш
        # бэкенд): иначе каждое сохранение стирало бы секрет, которого не видно.
        if secret:
            await _set_setting(ctx, _OIDC_SECRET, secret, "Client Secret")
    if "telegram_oidc_enabled" in body:
        enabled = bool(body.get("telegram_oidc_enabled"))
        if enabled != current["telegram_oidc_enabled_setting"]:
            await _set_setting(ctx, _OIDC_ENABLED, enabled, "Вход через Telegram")

    return await _auth_view(ctx)
