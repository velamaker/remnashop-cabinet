"""Промо-баннер в кабинете: объявление или акция на главной странице.

ЧЬЯ ЭТО ФУНКЦИЯ. Целиком НАША. Бот в ней не участвует ни одной ручкой: у
«Бедолаги» нет ни такого баннера, ни места, где его хранить. Правило владельца
«гасим только то, что принадлежит боту» работает здесь в обратную сторону —
раздел не прячем, а держим сами: настройка ложится в собственную память адаптера
(`state.settings`, ключ `promo_banner`), а публичная ручка отдаёт кабинету уже
готовый баннер. Своих таблиц не заводим — файл базы общий с соседними разделами.

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ — общее правило адаптера: раздел = файл `admin_*.py`,
`compose.py` подхватывает их по маске, в образ файл попадает строкой
`COPY *.py` в `adapter/Dockerfile`. Ни `compose.py`, ни `main.py` править не
пришлось: страница `/admin/promo-banner` уже описана в `ADMIN_PAGE_REQUIREMENTS`
(и в разделе `settings`), поэтому в меню она появляется ровно тогда, когда здесь
зарегистрирован GET `/api/admin/promo-banner`.

ФОРМА И ЛОГИКА — как у нашего бэкенда, экран-то один и тот же:
  • конфиг (`PromoBannerConfig` в cabinet/src/api/admin.ts) — десять полей,
    их же шлёт `PromoBannerCard` частичным обновлением;
  • публичный ответ (`PromoBannerStatus` в cabinet/src/api/promoBanner.ts) —
    `active` плюс то, что рисует компонент `PromoBanner.tsx`;
  • таргетинг тот же: тумблер → окно показа (`starts_at`/`ends_at`) → аудитория.
Образец: admin_src/src/web/endpoints/{admin,public}/promo_banner.py и
services/overlay_promo_banner.py.

ОТКУДА БЕРЁТСЯ АУДИТОРИЯ. Из подписки самого человека — ровно того ответа
(`/cabinet/subscription`), на котором стоит весь экран подписки в кабинете
(compose.subscription_current). Ничего сверх этого адаптер о человеке не знает и
не выдумывает: «на пробном» — их же `is_trial`, «истекающим» — их `end_date`.
Плюс пауза: она наша (state.freezes), и человек на паузе подписку ИМЕЕТ —
показывать ему «купите подписку» было бы враньём, тем более что тот же кабинет
на соседнем экране пишет ему «подписка на паузе».

ПО УМОЛЧАНИЮ ВЫКЛЮЧЕН. Баннер видят все пользователи сразу, поэтому включиться
сам при обновлении он не может ни при каких условиях.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from typing import Any

import httpx

from compose import HANDLERS, Ctx, NotAvailable, UpstreamError, handler

# --- что храним ---------------------------------------------------------------

# Ключ в своей памяти (`state.settings`). Новых таблиц не заводим: файл общий.
_KEY = "promo_banner"

# Дефолт слово в слово с нашим бэкендом (overlay_promo_banner.DEFAULT_CONFIG):
# выключено, без текста, акцентный цвет, всем, скрывать разрешено, без окна.
_DEFAULT: dict[str, Any] = {
    "enabled": False,
    "title": "",
    "text": "",
    "cta_text": "",
    "cta_url": "",
    "color": "accent",
    "audience": "all",
    "dismissible": True,
    "starts_at": "",  # ISO-строка или пусто (без ограничения снизу)
    "ends_at": "",    # ISO-строка или пусто (без ограничения сверху)
}

# Значения, которые понимает кабинет (те же списки, что в его `select`).
_COLORS = ("accent", "red", "green", "amber")
_AUDIENCES = ("all", "no_sub", "has_sub", "trial", "expiring")

# Пределы длины — те же, что у нашего бэкенда: экран один, и текст, который в
# одной установке влезает, а в другой обрезается, — это разное поведение одной
# и той же кнопки «Сохранить».
_TITLE_MAX = 120
_TEXT_MAX = 500
_CTA_TEXT_MAX = 40
_CTA_URL_MAX = 300
_DATE_MAX = 40

# «Истекающим» — тот же порог, что подписан на экране («≤3 дн.»).
_EXPIRING_DAYS = 3

# Право менять настройку. Своего у нас нет и выдумывать его нельзя, поэтому
# спрашиваем их RBAC — то самое право, которым у них закрыта правка настроек
# (им же пользуется `admin_cabinet.py` для оформления). Баннер видит КАЖДЫЙ
# пользователь кабинета, и админ «только для просмотра» не должен уметь его
# опубликовать. Читать настройку достаточно быть админом бота.
_EDIT_RIGHT = "settings:edit"

_INACTIVE: dict[str, Any] = {"active": False}


def _fail(status: int, detail: str) -> UpstreamError:
    """Отказ самого адаптера в той же форме, что и ошибка бота."""
    return UpstreamError(httpx.Response(status, json={"detail": detail}))


# --- права --------------------------------------------------------------------


async def _require_admin(ctx: Ctx) -> None:
    """Пускать только администратора бота.

    Ручка не ходит в их API, а читает своё хранилище, поэтому права обязана
    спросить явно: иначе черновик акции («скидка 40 % со вторника») читал бы
    любой, кто знает путь.
    """
    if not ctx.token:
        raise _fail(401, "Требуется вход")
    me = await ctx.json("/cabinet/auth/me/is-admin", {}, soft=True) or {}
    if not me.get("is_admin"):
        raise _fail(403, "Недостаточно прав")


async def _require_edit(ctx: Ctx) -> None:
    """Право на правку — по их RBAC. Сравнение по их правилу: право
    пользователя это ШАБЛОН (`*:*`, `settings:*`), требуемое — строка.
    Не смогли спросить — считаем, что права нет (fail-closed)."""
    data = await ctx.json("/cabinet/auth/me/permissions", {}, soft=True) or {}
    granted = data.get("permissions")
    ok = isinstance(granted, list) and any(
        isinstance(p, str) and fnmatchcase(_EDIT_RIGHT, p) for p in granted
    )
    if not ok:
        raise _fail(403, f"Недостаточно прав в боте: нужно право «{_EDIT_RIGHT}»")


# --- настройка ----------------------------------------------------------------


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _safe_url(url: str) -> str:
    """Ссылка, которую можно поставить в `<a href>`: http(s) или внутренний путь.

    Её видит КАЖДЫЙ пользователь, поэтому `javascript:`/`data:` здесь — готовый
    stored-XSS на всю базу. `//host` тоже не пускаем: для браузера это внешний
    адрес, а выглядит как внутренний путь. Проверка стоит и на записи (там с
    объяснением, почему не сохранили), и здесь — на случай, если в хранилище
    что-то попало мимо неё.
    """
    value = (url or "").strip()
    if not value:
        return ""
    if value.startswith("/") and not value.startswith("//"):
        return value
    return value if value.lower().startswith(("http://", "https://")) else ""


def _normalize(raw: Any) -> dict[str, Any]:
    """Хранимое → форма, которую ждёт экран (`PromoBannerConfig`), без лишнего."""
    data = raw if isinstance(raw, dict) else {}
    color = _text(data.get("color")).lower()
    audience = _text(data.get("audience")).lower()
    return {
        "enabled": bool(data.get("enabled", _DEFAULT["enabled"])),
        "title": _text(data.get("title"))[:_TITLE_MAX],
        "text": _text(data.get("text"))[:_TEXT_MAX],
        "cta_text": _text(data.get("cta_text"))[:_CTA_TEXT_MAX],
        "cta_url": _safe_url(_text(data.get("cta_url"))[:_CTA_URL_MAX]),
        "color": color if color in _COLORS else str(_DEFAULT["color"]),
        "audience": audience if audience in _AUDIENCES else str(_DEFAULT["audience"]),
        "dismissible": bool(data.get("dismissible", _DEFAULT["dismissible"])),
        "starts_at": _text(data.get("starts_at"))[:_DATE_MAX],
        "ends_at": _text(data.get("ends_at"))[:_DATE_MAX],
    }


def _config(state: Any) -> dict[str, Any]:
    if state is None or not getattr(state, "available", False):
        # Хранить негде — значит настройки нет, а «нет» здесь равно «выключено»:
        # тумблер, включённый в интерфейсе поверх пустоты, был бы обманом.
        return dict(_DEFAULT)
    return _normalize(state.get_setting(_KEY, {}))


# --- разбор того, что пришло с экрана ------------------------------------------


def _as_text(name: str, value: Any, limit: int) -> str:
    """Текстовое поле. Слишком длинное — отказ с причиной, а не тихая обрезка:
    молча укоротить заголовок значит показать «Сохранено» и подсунуть админу не
    тот текст, который он написал."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _fail(400, f"Поле «{name}» должно быть строкой")
    cleaned = value.strip()
    if len(cleaned) > limit:
        raise _fail(400, f"«{name}» — до {limit} символов (сейчас {len(cleaned)})")
    return cleaned


def _as_choice(name: str, value: Any, allowed: tuple[str, ...]) -> str:
    """Значение из списка. Неизвестное — отказ, а не подстановка умолчания:
    молча заменить аудиторию на «всем» значит разослать баннер всей базе."""
    choice = _text(value).lower()
    if choice not in allowed:
        raise _fail(
            400, f"Недопустимое значение «{name}»: ожидалось одно из {', '.join(allowed)}"
        )
    return choice


def _moment(value: str) -> datetime | None:
    """ISO-строка → момент времени. Пусто или мусор — «ограничения нет»."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    # Без часового пояса считаем UTC: кабинет шлёт `toISOString()` (всегда с Z),
    # а вручную вписанную дату надо от чего-то отсчитывать.
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _as_moment(name: str, value: Any) -> str:
    """Дата окна показа. Храним ровно ту строку, что прислал кабинет (его же
    поле `datetime-local` читает её обратно срезом), но убеждаемся, что она
    разбирается: непонятая дата означала бы «показывать всегда», а админ был бы
    уверен, что назначил срок."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _fail(400, f"Поле «{name}» должно быть датой в формате ISO")
    cleaned = value.strip()
    if not cleaned:
        return ""
    if len(cleaned) > _DATE_MAX or _moment(cleaned) is None:
        raise _fail(400, f"Не понял дату в поле «{name}»: нужен формат ISO")
    return cleaned


def _as_url(value: Any) -> str:
    """Ссылка кнопки. Отказ с объяснением вместо тихого стирания: пропавшее
    после сохранения поле админ ищет глазами, а причину не узнаёт никогда."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _fail(400, "Ссылка кнопки должна быть строкой")
    cleaned = value.strip()
    if not cleaned:
        return ""
    if len(cleaned) > _CTA_URL_MAX:
        raise _fail(400, f"Ссылка кнопки — до {_CTA_URL_MAX} символов")
    safe = _safe_url(cleaned)
    if not safe:
        raise _fail(
            400,
            "Ссылка кнопки должна начинаться с http:// или https:// либо быть "
            "внутренним путём кабинета (например /billing)",
        )
    return safe


# --- кому показывать ----------------------------------------------------------


async def _frozen(ctx: Ctx) -> bool:
    """На паузе ли человек. Знает это только адаптер: пауза — наша фича, её
    список лежит в своей же памяти (state.freezes)."""
    if ctx.state is None or not ctx.state.available:
        return False
    # Мягко: своего идентификатора может не быть в ответе чужой сборки, и это не
    # повод ронять всю страницу из-за баннера — просто не считаем человека
    # поставленным на паузу. 401/403 `ctx.json` не глотает даже так.
    me = await ctx.json("/cabinet/auth/me", {}, soft=True) or {}
    user_key = str(me.get("id") or "")
    return bool(user_key and ctx.state.get_freeze(user_key))


async def _audience_matches(ctx: Ctx, audience: str) -> bool:
    """Подходит ли человек под выбранную аудиторию.

    Ошибку бота НЕ глотаем (ctx.json без `soft`): молчаливое «данных нет» здесь
    значит «подписки нет», то есть человеку с оплаченной подпиской показали бы
    «купите подписку». Пусть лучше баннер не появится вовсе — компонент кабинета
    ошибку этой ручки переживает молча, а причина видна в логе и в devtools.
    """
    if audience == "all":
        return True

    data = await ctx.json("/cabinet/subscription", {}) or {}
    sub = (data.get("subscription") or {}) if data.get("has_subscription") else {}
    end = _moment(str(sub.get("end_date") or ""))
    now = datetime.now(timezone.utc)
    active = str(sub.get("status") or "").upper() == "ACTIVE" and end is not None and end > now
    # Пауза: подписка у человека есть, просто её часы остановлены. Спрашиваем
    # только у неактивных — активной подписке пауза невозможна.
    paused = False if active else await _frozen(ctx)

    if audience == "no_sub":
        return not (active or paused)
    if audience == "has_sub":
        return active or paused
    if audience == "trial":
        return (active or paused) and bool(sub.get("is_trial"))
    if audience == "expiring":
        # На паузе срок не идёт, и «истекает через N дней» про такого человека
        # неправда — он сам решит, когда продолжить.
        if not active or end is None:
            return False
        return (end - now).days <= _EXPIRING_DAYS
    return False


# --- версия баннера (дедупликация «скрыто») -----------------------------------


def _version(config: dict[str, Any]) -> str:
    """Метка контента: по ней кабинет помнит, что баннер уже закрыли крестиком,
    и снова показывает его, только когда админ сменил текст.

    Считаем устойчивым хэшем, а не встроенным `hash()` (как наш бэкенд): у строк
    он солится случайным семенем на каждый запуск процесса, а контейнер адаптера
    перезапускают при каждом обновлении — закрытый баннер вылезал бы снова после
    каждого из них. Дедупликация обязана переживать перезапуск.
    """
    material = "\x00".join(
        (config["title"], config["text"], config["starts_at"], config["ends_at"])
    )
    return hashlib.sha1(material.encode("utf-8")).hexdigest()[:8]


# --- ручки --------------------------------------------------------------------


@handler("GET", "/api/admin/promo-banner")
async def promo_banner_config(ctx: Ctx) -> dict[str, Any]:
    await _require_admin(ctx)
    return _config(ctx.state)


@handler("PUT", "/api/admin/promo-banner")
async def promo_banner_save(ctx: Ctx) -> dict[str, Any]:
    """Сохранение. Меняем только присланное — экран шлёт частичное обновление.

    Всё, что не понято, отбиваем ДО записи и целиком: «часть полей применилась,
    часть нет» после ошибки — худший исход, потом гадать, что же в итоге видит
    пользователь.
    """
    await _require_admin(ctx)
    await _require_edit(ctx)
    if ctx.state is None or not ctx.state.available:
        raise NotAvailable(
            "Баннер некуда сохранить: у адаптера нет доступа к своему хранилищу"
        )

    body = ctx.payload()
    config = _config(ctx.state)
    if "title" in body:
        config["title"] = _as_text("Заголовок", body["title"], _TITLE_MAX)
    if "text" in body:
        config["text"] = _as_text("Текст", body["text"], _TEXT_MAX)
    if "cta_text" in body:
        config["cta_text"] = _as_text("Текст кнопки", body["cta_text"], _CTA_TEXT_MAX)
    if "cta_url" in body:
        config["cta_url"] = _as_url(body["cta_url"])
    if "color" in body:
        config["color"] = _as_choice("Цвет", body["color"], _COLORS)
    if "audience" in body:
        config["audience"] = _as_choice("Кому показывать", body["audience"], _AUDIENCES)
    if "dismissible" in body:
        config["dismissible"] = bool(body["dismissible"])
    if "starts_at" in body:
        config["starts_at"] = _as_moment("Показывать с", body["starts_at"])
    if "ends_at" in body:
        config["ends_at"] = _as_moment("Показывать до", body["ends_at"])
    if "enabled" in body:
        config["enabled"] = bool(body["enabled"])

    starts, ends = _moment(config["starts_at"]), _moment(config["ends_at"])
    if starts and ends and starts >= ends:
        raise _fail(400, "«Показывать с» должно быть раньше, чем «Показывать до»")
    # Включённый баннер без единого слова — пустая карточка у всех на главной.
    # Публичная ручка такой всё равно не отдаст (см. ниже), поэтому честнее
    # сказать об этом сразу, а не оставить админа с включённым тумблером и без
    # баннера.
    if config["enabled"] and not (config["title"] or config["text"]):
        raise _fail(400, "Нечего показывать: заполните заголовок или текст")

    ctx.state.set_setting(_KEY, config)
    return config


# Этот путь у «Бедолаги» УЖЕ занят: там кабинету отдаются её персональные
# промо-офферы («Персональная скидка 30 %»). Наши модули грузятся последними и
# молча перехватили бы слот — человек перестал бы видеть свои предложения от бота.
# Запоминаем прежнего владельца и отдаём ему запрос, когда своего баннера нет.
_BOT_BANNER = HANDLERS.get(("GET", "/api/promo-banner"))


async def _fallback(ctx: Ctx) -> dict[str, Any]:
    """Наш баннер выключен или не подходит — пусть отвечает бот, как раньше."""
    if _BOT_BANNER is None:
        return dict(_INACTIVE)
    return await _BOT_BANNER(ctx)


@handler("GET", "/api/promo-banner")
async def promo_banner_public(ctx: Ctx) -> dict[str, Any]:
    """Баннер для того, кто спрашивает. Порядок проверок как у нашего бэкенда:
    тумблер и содержимое → окно показа → аудитория."""
    if not ctx.token:
        # Гостю баннера нет: аудиторию посчитать не по кому, а отдавать текст
        # акции всем подряд (в том числе поисковым роботам на публичных
        # страницах) — не то, что админ настраивал. Отвечаем «баннера нет», а не
        # 401: кабинет на 401 уводит человека на вход, а промо-баннер такого
        # права не имеет.
        #
        # Оговорка, о которой честнее сказать вслух: наличие куки мы проверяем,
        # а её ЖИВОСТЬ при аудитории «всем» — нет (у прочих аудиторий сессию всё
        # равно проверит запрос подписки). Сходить к боту ради этого значило бы
        # добавить запрос на каждое открытие главной, а прячем мы здесь не
        # данные человека, а текст объявления, который и так видит любой вошедший.
        return dict(_INACTIVE)

    config = _config(ctx.state)
    if not config["enabled"] or not (config["title"] or config["text"]):
        return await _fallback(ctx)

    now = datetime.now(timezone.utc)
    starts, ends = _moment(config["starts_at"]), _moment(config["ends_at"])
    if starts and now < starts:
        return await _fallback(ctx)
    if ends and now > ends:
        return await _fallback(ctx)

    if not await _audience_matches(ctx, config["audience"]):
        return await _fallback(ctx)

    return {
        "active": True,
        "title": config["title"],
        "text": config["text"],
        "cta_text": config["cta_text"],
        "cta_url": config["cta_url"],
        "color": config["color"],
        "dismissible": config["dismissible"],
        "version": _version(config),
    }
