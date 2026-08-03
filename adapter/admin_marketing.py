"""Маркетинг админки поверх «Бедолаги»: рассылки и рекламные ссылки.

Зачем отдельный модуль. Админских ручек в нашем контракте под сорок штук, и один
файл на всех — гарантированный конфликт: адаптер пишется несколькими руками
сразу. `admin.py` отвечает за людей и деньги, `admin_catalog.py` — за витрину
(тарифы, промокоды), `admin_users.py` — за карточку пользователя, здесь —
группа «Маркетинг»: рассылки и рекламные ссылки.

ОТКУДА БЕРЁМ ДАННЫЕ. Только их кабинетное API (`/cabinet/admin/broadcasts*`,
`/cabinet/admin/campaigns*`) и только по сессии САМОГО админа: `ctx.call` /
`ctx.json` подставляют его Bearer, а решение «пускать или нет» принимает их RBAC
(`broadcasts:read/send`, `campaigns:read/create/edit/delete/stats`). Служебный
токен адаптера (`ctx.admin`, X-API-Key) здесь не используется НИ РАЗУ и не
должен: он принадлежит адаптеру, а не человеку, и сходи мы за рассылкой с ним —
разослать всей базе смог бы любой вошедший в кабинет. Тут цена ошибки выше, чем
у пустой колонки: письмо и сообщение в бот нельзя «отозвать».

ЧТО ПЕРЕВЕДЕНО.
  • «Рассылки» — их `/cabinet/admin/broadcasts`: история, статус конкретной
    задачи, счётчики аудиторий и запуск. Каналы кабинета (TG_*/EMAIL_*)
    ложатся на их фильтры (см. `_TG_TARGET` / `_EMAIL_TARGET`); те, что не
    ложатся ЧЕСТНО, отвечают 501 с причиной, а не шлют «примерно то же».
  • «Рекл. ссылки» — их рекламные кампании (`/cabinet/admin/campaigns`):
    у обеих систем это «код в ссылке + статистика регистраций и выручки по
    пришедшим». Наш `code` — их `start_parameter`.

ЧЕГО ЗДЕСЬ НЕТ И ПОЧЕМУ.
  • «Промо-баннер» кабинета (title/text/CTA/цвет/аудитория/расписание) у
    «Бедолаги» не существует: ближайшее, что есть, — персональные промо-офферы
    (их уже показывает `compose.promo_banner`) и шаблоны сообщений оффера для
    Telegram. Слепить из шаблона оффера «баннер» значило бы выдумать половину
    полей, а PUT переписал бы им текст рассылки оффера. Поэтому ручки
    `/api/admin/promo-banner` здесь нет — путь отвечает 501.
  • Остановка рассылки: у них `/broadcasts/{id}/stop` есть, а в нашем контракте
    и в экране кабинета такой кнопки нет вовсе — регистрировать путь, который
    никто не позовёт, незачем.

ЕДИНИЦЫ. У них деньги в КОПЕЙКАХ (`total_revenue_kopeks`), у нас в статистике
ссылки — числом в рублях (кабинет печатает его через `toLocaleString`), а
конверсия у них в ПРОЦЕНТАХ (0..100), у нас — долей (кабинет сам умножает на
100). Перепутать легко, а увидит владелец «конверсию 2350%».
"""

from __future__ import annotations

import asyncio
import html
import re
import time
from typing import Any

import httpx

from compose import Ctx, NotAvailable, UpstreamError, handler


# --- мелочи -----------------------------------------------------------------


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bad_request(message: str) -> UpstreamError:
    """Ошибка В ЗАПРОСЕ КАБИНЕТА (а не «не умеем») — 400 с русским текстом.

    501 (`NotAvailable`) значит «у бота такого нет», и вешать его на пустой текст
    рассылки неправильно: админ решит, что раздел не переведён, и перестанет
    пытаться.
    """
    return UpstreamError(httpx.Response(400, json={"detail": message}))


def _not_found(message: str) -> UpstreamError:
    return UpstreamError(httpx.Response(404, json={"detail": message}))


def _path_id(ctx: Ctx, name: str, missing: str) -> int:
    """Номер из пути. Не число — значит такого нет: 404, а не пятисотка изнутри
    их роутера (их FastAPI ответил бы на строку простынёй pydantic-ошибки)."""
    try:
        return int(str(ctx.params.get(name) or ""))
    except ValueError:
        raise _not_found(missing) from None


async def _extra(ctx: Ctx, path: str, **kw: Any) -> Any:
    """Необязательный кусочек экрана: любая беда — просто «данных нет».

    Почему не `ctx.json(..., soft=True)`. Мягкий режим compose.py осознанно НЕ
    глотает 401/403 — для пользовательских ручек это правильно. Здесь наоборот:
    права в их RBAC нарезаны мелко (`campaigns:read` отдельно от
    `campaigns:stats`), и админ с одним правом получал бы 403 на второстепенном
    запросе — и вместе с ним пустой экран вместо статистики, которая ему как раз
    доступна. Главный запрос каждой ручки остаётся строгим, он же и решает,
    пускать ли человека.
    """
    try:
        resp = await ctx.call("GET", path, **kw)
    except httpx.HTTPError:
        return None
    if resp.status_code >= 400:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


# ============================ РАССЫЛКИ ======================================
#
# Форма кабинета (AdminBroadcastsPage) — это текст плюс набор каналов, где канал
# = «куда» + «кому». У «Бедолаги» ровно то же самое разложено на два поля:
# `channel` (telegram/email/both) и `target` — ключ их фильтра. Ниже — сверка
# каналов кабинета с их фильтрами; она сделана не «по названию», а по их
# SQL-условиям (app/handlers/admin/messages.py::get_target_users_count и
# app/cabinet/routes/admin_broadcasts.py::_get_email_filter_count).

# Telegram-каналы ложатся один в один.
#   all      — все активные учётки (кому есть куда слать — решает их отправка);
#   active   — активная ОПЛАЧЕННАЯ подписка (пробные исключены);
#   no       — нет ни одной активной подписки;
#   trial    — есть пробная подписка;
#   expired  — истёкшая/отключённая и ни одной активной.
# Разница с подписью кабинета одна: «С подпиской» у нас описано как «активная
# (вкл. пробные)», а у них пробные вынесены в отдельный фильтр. Их разбиение
# строже нашего и БЕЗОПАСНЕЕ: выбрав «С подпиской» и «Пробный период» вместе,
# админ не пришлёт одному человеку два одинаковых сообщения.
_TG_TARGET: dict[str, str] = {
    "TG_ALL": "all",
    "TG_SUBSCRIBED": "active",
    "TG_UNSUBSCRIBED": "no",
    "TG_TRIAL": "trial",
    "TG_EXPIRED": "expired",
}

# Email-каналы кабинета означают «только у кого НЕТ Telegram» — иначе человек
# получит и сообщение в боте, и письмо об одном и том же. Этому условию у них
# отвечает ровно один фильтр — `email_only` (auth_type = email).
_EMAIL_TARGET: dict[str, str] = {
    "EMAIL_ALL": "email_only",
}

# Остальные email-сегменты кабинета не переводятся, и подменять их «похожими»
# нельзя — это не косметика, а лишние письма живым людям. Причина у каждого
# своя, и она уходит админу текстом 501: пусть видит, ЧЕГО не хватает у бота.
_EMAIL_UNAVAILABLE: dict[str, str] = {
    "EMAIL_SUBSCRIBED": (
        "у этого бота фильтр почты «с активной подпиской» не исключает тех, у кого "
        "есть Telegram (им уйдёт и сообщение в боте, и письмо) и не отделяет пробные"
    ),
    "EMAIL_TRIAL": "у этого бота нет email-фильтра «пробный период»",
    "EMAIL_EXPIRING": "у этого бота нет email-фильтра «подписка заканчивается»",
    "EMAIL_EXPIRED": (
        "у этого бота фильтр почты «с истекшей подпиской» не исключает тех, у кого "
        "есть Telegram — письмо ушло бы дублем к сообщению в боте"
    ),
}

# Обратный перевод для истории: их `target_type` → канал кабинета.
_TARGET_TO_TG = {v: k for k, v in _TG_TARGET.items()}
_TARGET_TO_EMAIL = {v: k for k, v in _EMAIL_TARGET.items()}

# Их статусы (broadcast_service.py) → наши четыре, которые умеет рисовать
# кабинет. `partial` — это «дошло не всем», а не поломка: рассылка отработала до
# конца, и сколько именно не дошло, видно в счётчике ошибок рядом. Красный крест
# «Ошибка» тут врал бы. Неизвестный статус отдаём как есть: кабинет печатает
# незнакомое значение как подпись (STATUS_CONFIG[...] ?? b.status), и увидеть
# настоящее слово полезнее, чем угадать не тот значок.
_STATUS: dict[str, str] = {
    "queued": "PROCESSING",
    "in_progress": "PROCESSING",
    "cancelling": "PROCESSING",
    "completed": "COMPLETED",
    "partial": "COMPLETED",
    "cancelled": "CANCELED",
    "canceled": "CANCELED",
    "failed": "ERROR",
}

_CHANNEL_LABEL = {"telegram": "Telegram", "email": "Email", "both": "Telegram + Email"}

# Подписи их фильтров нужны только для истории (какой аудитории ушла рассылка) и
# не меняются поминутно, а тарифные фильтры называются по имени тарифа — брать
# их надо у бота, а не хранить копию их словаря у себя. Ответ общий для всех
# админов, поэтому держим короткий кэш: иначе каждый заход в «Рассылки» бьёт по
# боту двумя лишними запросами.
_LABELS_TTL = 60.0
_labels_cache: tuple[float, dict[str, str]] | None = None


def _remember_labels(*payloads: Any) -> dict[str, str]:
    """Складывает `key → подпись` из ответов их ручек с фильтрами."""
    labels: dict[str, str] = {}
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for group in ("filters", "tariff_filters", "custom_filters"):
            for item in payload.get(group) or []:
                if isinstance(item, dict) and item.get("key"):
                    labels[str(item["key"])] = str(item.get("label") or item["key"])
    if labels:
        global _labels_cache
        _labels_cache = (time.monotonic(), labels)
    return labels


async def _target_labels(ctx: Ctx) -> dict[str, str]:
    """Подписи аудиторий для истории. Не добрались — обойдёмся ключом."""
    if _labels_cache and time.monotonic() - _labels_cache[0] < _LABELS_TTL:
        return _labels_cache[1]
    filters, email_filters = await asyncio.gather(
        _extra(ctx, "/cabinet/admin/broadcasts/filters"),
        _extra(ctx, "/cabinet/admin/broadcasts/email-filters"),
    )
    return _remember_labels(filters, email_filters)


def _audience(item: dict[str, Any], labels: dict[str, str]) -> str:
    """Кому ушла рассылка — в терминах кабинета, где это возможно.

    Совпал канал кабинета — отдаём его ключ: кабинет знает его подпись
    («Telegram · все»). Не совпал (рассылку запустили из их админки по фильтру,
    которого у нас нет: по тарифу, по дате регистрации, по трафику) — отдаём
    человеческую подпись их фильтра. Кабинет печатает незнакомую аудиторию как
    есть, поэтому лучше «Telegram · Регистрация сегодня», чем «custom_today».
    """
    channel = str(item.get("channel") or "telegram").lower()
    target = str(item.get("target_type") or "")
    if channel == "telegram" and target in _TARGET_TO_TG:
        return _TARGET_TO_TG[target]
    if channel == "email" and target in _TARGET_TO_EMAIL:
        return _TARGET_TO_EMAIL[target]
    label = labels.get(target) or target or "—"
    return f"{_CHANNEL_LABEL.get(channel, channel)} · {label}"


def _broadcast(item: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
    """Их запись истории → наша карточка рассылки."""
    raw_status = str(item.get("status") or "").lower()
    # Заблокировавшие бота у них считаются отдельно, а у нас колонок две:
    # «Доставлено» и «Ошибок». Блокировка — это недоставка, поэтому она в
    # ошибках. Иначе прогресс-бар навсегда застревает не дойдя до конца:
    # кабинет считает прогресс как «доставлено + ошибок» от общего числа.
    failed = _int(item.get("failed_count")) + _int(item.get("blocked_count"))
    return {
        "task_id": str(item.get("id")),
        "status": _STATUS.get(raw_status, raw_status.upper()),
        "audience": _audience(item, labels),
        "total_count": _int(item.get("total_count")),
        "success_count": _int(item.get("sent_count")),
        "failed_count": failed,
        "created_at": item.get("created_at"),
    }


@handler("GET", "/api/admin/broadcasts")
async def broadcasts(ctx: Ctx) -> dict[str, Any]:
    """История рассылок (в т.ч. запущенных из их собственной админки)."""
    # Наш экран без пагинации, их список — с ней и с потолком 100. Берём
    # последнюю сотню (их сортировка — новые сверху) и честно отдаём ИХ total:
    # «показано 100 из 340» кабинет не рисует, но врать про общее число незачем.
    data = await ctx.json(
        "/cabinet/admin/broadcasts", {}, params={"limit": 100, "offset": 0}
    ) or {}
    labels = await _target_labels(ctx)
    items = [
        _broadcast(item, labels)
        for item in (data.get("items") or [])
        if isinstance(item, dict)
    ]
    return {"items": items, "total": _int(data.get("total"), len(items))}


@handler("GET", "/api/admin/broadcasts/{task_id}")
async def broadcast_status(ctx: Ctx) -> dict[str, Any]:
    """Обновление одной карточки: кабинет дёргает её, пока статус PROCESSING."""
    task_id = _path_id(ctx, "task_id", "Рассылка не найдена")
    data = await ctx.json(f"/cabinet/admin/broadcasts/{task_id}", {}) or {}
    return _broadcast(data, await _target_labels(ctx))


@handler("GET", "/api/admin/broadcasts/audience-counts")
async def broadcast_audience_counts(ctx: Ctx) -> dict[str, int]:
    """Сколько человек в каждой аудитории — цифры на кнопках формы.

    Отдаём ТОЛЬКО те каналы, которые реально переводятся. Ноль вместо
    непереводимого сегмента читался бы как «там никого нет», а это неправда:
    кабинет на отсутствующий ключ рисует «…», и это честнее.
    """
    filters = await ctx.json("/cabinet/admin/broadcasts/filters", {}) or {}
    # Почта у бота может быть не настроена вовсе — это не повод терять
    # telegram-счётчики, ради которых экран и открывают.
    email_filters = await ctx.json(
        "/cabinet/admin/broadcasts/email-filters", {}, soft=True
    ) or {}
    _remember_labels(filters, email_filters)

    counts: dict[str, int] = {}
    for group, mapping in (
        (filters.get("filters") or [], _TG_TARGET),
        (email_filters.get("filters") or [], _EMAIL_TARGET),
    ):
        by_key = {
            str(f.get("key")): _int(f.get("count"))
            for f in group
            if isinstance(f, dict) and f.get("key")
        }
        for channel, target in mapping.items():
            if target in by_key:
                counts[channel] = by_key[target]
    return counts


# Разметка письма. Кабинет присылает ОДИН текст на все каналы и рисует его
# предпросмотр по правилам Telegram (белый список b/i/u/s/code/pre/a). Их
# email-рассылка отправляет тело как HTML, поэтому текст надо перевести в HTML —
# ровно тем же белым списком, чтобы письмо совпало с тем, что админ видел в
# предпросмотре. Всё остальное экранируется: админ пишет для Telegram, а не для
# почты, и случайная «<» не должна съесть половину письма.
_HTML_TAGS = re.compile(r"&lt;(/?)(b|strong|i|em|u|s|code|pre)&gt;", re.IGNORECASE)
_HTML_LINK = re.compile(r'&lt;a href="([^"]*)"&gt;', re.IGNORECASE)
_SAFE_SCHEME = re.compile(r"^(https?://|tg://|mailto:)", re.IGNORECASE)


def _email_html(text: str) -> str:
    body = html.escape(text, quote=False)
    body = _HTML_TAGS.sub(lambda m: f"<{m.group(1)}{m.group(2).lower()}>", body)

    # Ссылка с неизвестной схемой (javascript:, data:) — не ссылка: тег не
    # восстанавливаем, текст остаётся. То же правило, что в предпросмотре
    # кабинета.
    opened = 0

    def _link(match: re.Match[str]) -> str:
        nonlocal opened
        href = match.group(1).strip()
        if not _SAFE_SCHEME.match(href):
            return ""
        opened += 1
        return f'<a href="{href}">'

    body = _HTML_LINK.sub(_link, body)
    # Закрывающих `</a>` возвращаем ровно столько, сколько открыли: лишний
    # закрывающий тег остаётся от выброшенной небезопасной ссылки, и в письме
    # это уже не разметка, а мусор.
    body = re.sub(r"&lt;/a&gt;", "</a>", body, count=opened, flags=re.IGNORECASE)
    body = body.replace("&lt;/a&gt;", "").replace("&lt;/A&gt;", "")
    body = body.replace("\r\n", "\n").replace("\n", "<br>")
    return (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;'
        f'font-size:15px;line-height:1.55">{body}</div>'
    )


async def _email_subject(ctx: Ctx) -> str:
    """Тема письма. Её форма кабинета не спрашивает вовсе — там одно поле
    «текст», — поэтому берём ту же тему, что ставит наш собственный бэкенд:
    «Сообщение от {бренд}». Бренд у бота свой, спрашиваем его, а не подставляем
    наш."""
    brand = await ctx.json("/cabinet/branding", {}, soft=True) or {}
    return f"Сообщение от {str(brand.get('name') or '').strip() or 'VPN'}"


@handler("POST", "/api/admin/broadcasts")
async def broadcast_create(ctx: Ctx) -> dict[str, Any]:
    """Запуск рассылки.

    У нас один запрос на все выбранные каналы, у них — одна рассылка на один
    фильтр, поэтому запускаем по одной на канал. ВСЕ проверки делаются ДО первой
    отправки: отбить непереводимый сегмент после того, как половина сообщений уже
    ушла, нельзя — отозвать их невозможно.

    Почему всё идёт через их «комбинированную» ручку `/broadcasts/send`, даже
    telegram-только. У них два входа с РАЗНЫМИ правами: `/broadcasts` требует
    `broadcasts:create`, `/broadcasts/send` — `broadcasts:send`. Смешивать их в
    одном запросе опасно: админ с одним правом из двух получил бы 403 на втором
    канале, когда первый уже разослан, увидел бы ошибку и нажал «Отправить» ещё
    раз — то есть отправил бы telegram-часть дважды. Один вход = отказ приходит
    на первом же вызове, до единого отправленного сообщения.
    """
    body = ctx.payload()
    text = str(body.get("text") or "").strip()
    if not text:
        raise _bad_request("Текст рассылки пуст")

    raw = body.get("channels")
    if not isinstance(raw, list):
        raise _bad_request("Не выбран ни один канал")
    # dict.fromkeys, а не set: порядок каналов — это порядок запуска, и в истории
    # он должен совпасть с тем, что админ отмечал сверху вниз.
    channels = list(dict.fromkeys(str(c) for c in raw if str(c).strip()))
    if not channels:
        raise _bad_request("Не выбран ни один канал")

    for channel in channels:
        if channel in _EMAIL_UNAVAILABLE:
            raise NotAvailable(
                f"Email-рассылка этому сегменту недоступна: {_EMAIL_UNAVAILABLE[channel]}. "
                "Доступен сегмент «Email · все»."
            )
        if channel not in _TG_TARGET and channel not in _EMAIL_TARGET:
            raise _bad_request(f"Неизвестный канал рассылки «{channel}»")

    # Тема и HTML нужны только если выбран email — лишний запрос за брендом ради
    # telegram-рассылки не делаем.
    subject = ""
    if any(c in _EMAIL_TARGET for c in channels):
        subject = await _email_subject(ctx)

    telegram: list[str] = []
    email: list[int] = []
    for channel in channels:
        if channel in _TG_TARGET:
            payload: dict[str, Any] = {
                "channel": "telegram",
                "target": _TG_TARGET[channel],
                "message_text": text,
                # `category` решает, кого их бот выкинет из получателей по личным
                # настройкам уведомлений (news/promo можно отключить, system —
                # нет). В нашей форме такого выбора нет вовсе, и у их API
                # умолчание — `system`. Берём его: иначе счётчик на кнопке
                # («~N получателей») показывал бы одно, а рассылка молча уходила
                # бы меньшему числу людей — админ бы этого не понял.
                "category": "system",
            }
        else:
            payload = {
                "channel": "email",
                "target": _EMAIL_TARGET[channel],
                "email_subject": subject,
                "email_html_content": _email_html(text),
            }
        created = await ctx.json(
            "/cabinet/admin/broadcasts/send", {}, method="POST", json=payload
        ) or {}
        task_id = _int(created.get("id"))
        if channel in _TG_TARGET:
            telegram.append(str(task_id))
        else:
            email.append(task_id)

    return {"telegram": telegram, "email": email}


# ========================= РЕКЛАМНЫЕ ССЫЛКИ =================================
#
# У нас «рекламная ссылка» — это имя, код и статистика по пришедшим. У них ровно
# то же самое называется рекламной кампанией: `start_parameter` — код в ссылке
# (t.me/bot?start=КОД), плюс регистрации, покупки и выручка по тем, кто пришёл.
# Разница одна и она важна для владельца: у нас код подставляется в адрес
# кабинета (?ref=), а у них — в deep-link бота, и регистрация из кабинета в их
# кампанию не попадёт, пока сам вход не научится передавать `campaign_slug`
# (это ручка авторизации, не этот модуль).


def _ad_link(campaign: dict[str, Any]) -> dict[str, Any]:
    """Их кампания → наша рекламная ссылка."""
    return {
        "id": _int(campaign.get("id")),
        "name": campaign.get("name") or "",
        "code": campaign.get("start_parameter") or "",
        "is_active": bool(campaign.get("is_active")),
        "created_at": campaign.get("created_at"),
        # Готовая ссылка от бота. У «Бедолаги» рекламный код — это не `?ref=` на
        # сайте, а deep-link в Telegram (`t.me/бот?start=код`): подпись на экране
        # обязана показывать то, что реально сработает, иначе владелец разошлёт
        # ссылку, по которой переходы не считаются.
        "url": campaign.get("deep_link") or "",
    }


def _rate(percent: Any) -> float:
    """Их проценты (0..100) → наша доля (0..1). Кабинет умножает на 100 сам."""
    try:
        return round(float(percent) / 100, 4)
    except (TypeError, ValueError):
        return 0.0


@handler("GET", "/api/admin/ad-links")
async def ad_links(ctx: Ctx) -> dict[str, Any]:
    """Список ссылок. Выключенные тоже нужны — их как раз включают обратно."""
    data = await ctx.json(
        "/cabinet/admin/campaigns", {},
        # Потолок страницы у них 100, нашему экрану пагинация не положена.
        params={"include_inactive": True, "limit": 100, "offset": 0},
    ) or {}
    items = [
        _ad_link(c) for c in (data.get("campaigns") or []) if isinstance(c, dict)
    ]
    # Готовую ссылку их СПИСОК не отдаёт (только карточка), а владельцу она нужна
    # именно здесь — чтобы скопировать и вставить в рекламу. Собираем сами.
    bot = await bot_username(ctx)
    if bot:
        for item in items:
            if not item.get("url") and item.get("code"):
                item["url"] = f"https://t.me/{bot}?start={item['code']}"
    return {"items": items, "total": _int(data.get("total"), len(items))}


@handler("GET", "/api/admin/ad-links/{id}/stats")
async def ad_link_stats(ctx: Ctx) -> dict[str, Any]:
    """Конверсия по ссылке.

    Их статистика богаче нашей (средний чек, активные пробники, дата последней
    регистрации), но лишние поля кабинету некуда положить — берём те, что он
    рисует. Дату создания их ручка статистики не отдаёт, поэтому подтягиваем
    карточку кампании отдельно и мягко: право на статистику и право на чтение у
    них РАЗНЫЕ, и из-за недостающей даты экран падать не должен.
    """
    link_id = _path_id(ctx, "id", "Рекламная ссылка не найдена")
    stats, detail = await asyncio.gather(
        ctx.json(f"/cabinet/admin/campaigns/{link_id}/stats", {}),
        _extra(ctx, f"/cabinet/admin/campaigns/{link_id}"),
    )
    stats = stats or {}
    base = _ad_link({**stats, **(detail if isinstance(detail, dict) else {})})
    return {
        **base,
        "stats": {
            "registrations": _int(stats.get("registrations")),
            # «Пробных» у нас — сколько пришедших вообще брали пробную подписку.
            "trials": _int(stats.get("trial_users_count")),
            "buyers": _int(stats.get("paid_users_count")),
            # Их conversion_count — те, у кого зафиксирован переход пробной
            # подписки в оплаченную; это ровно наши «купившие после пробной».
            "trial_buyers": _int(stats.get("conversion_count")),
            # Выручка у них — реальные пополнения пришедших, в копейках. Валюта
            # у бота одна (рубли), как и во всём остальном адаптере.
            "revenue": {"RUB": round(_int(stats.get("total_revenue_kopeks")) / 100, 2)},
            "reg_to_buy_rate": _rate(stats.get("conversion_rate")),
            "trial_to_buy_rate": _rate(stats.get("trial_conversion_rate")),
        },
    }


@handler("POST", "/api/admin/ad-links")
async def ad_link_create(ctx: Ctx) -> dict[str, Any]:
    """Создание ссылки.

    У их кампании обязателен тип бонуса — «что дать пришедшему». Наша реклама
    ничего не дарит, это чистая метка источника, поэтому `none`. Придумывать
    бонус (деньги на баланс, дни подписки) адаптер не имеет права: это деньги
    владельца, а форма кабинета про них не спрашивала.
    """
    body = ctx.payload()
    name = str(body.get("name") or "").strip()
    code = str(body.get("code") or "").strip()
    if not name:
        raise _bad_request("Название не заполнено")
    if not code:
        raise _bad_request("Код ссылки не заполнен")
    # Их код уезжает в deep-link Telegram, поэтому у них он ограничен латиницей,
    # цифрами, «_» и «-». Проверяем сами, чтобы вместо простыни pydantic на
    # английском админ увидел, что именно не так.
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", code):
        raise _bad_request(
            "Код ссылки: только латинские буквы, цифры, «_» и «-» (до 64 символов) — "
            "он подставляется в ссылку на бота"
        )
    created = await ctx.json(
        "/cabinet/admin/campaigns", {},
        method="POST",
        json={"name": name, "start_parameter": code, "bonus_type": "none", "is_active": True},
    ) or {}
    return _ad_link(created)


@handler("PUT", "/api/admin/ad-links/{id}")
async def ad_link_update(ctx: Ctx) -> dict[str, Any]:
    """Переименование и выключение ссылки.

    Кладём в тело ТОЛЬКО присланные поля: их правка различает «не передано» и
    «передано пустым» (model_fields_set), и полный набор полей затёр бы бонусы и
    партнёра, если кампанию заводили в их админке.
    """
    link_id = _path_id(ctx, "id", "Рекламная ссылка не найдена")
    body = ctx.payload()
    payload: dict[str, Any] = {}
    if "name" in body:
        name = str(body.get("name") or "").strip()
        if not name:
            raise _bad_request("Название не заполнено")
        payload["name"] = name
    if "is_active" in body:
        payload["is_active"] = bool(body.get("is_active"))
    if not payload:
        raise _bad_request("Нечего менять")
    return _ad_link(
        await ctx.json(
            f"/cabinet/admin/campaigns/{link_id}", {}, method="PUT", json=payload
        ) or {}
    )


# Их отказ удалять кампанию с регистрациями — правильный (иначе пропадёт вся
# статистика источника), но текст английский и попадёт прямо в alert кабинета.
# Переводим только этот один случай: он частый и подсказывает, что делать.
_DELETE_BLOCKED = re.compile(r"Cannot delete campaign with (\d+) registrations", re.I)


@handler("DELETE", "/api/admin/ad-links/{id}")
async def ad_link_delete(ctx: Ctx) -> dict[str, Any]:
    """Удаление ссылки. Кабинету от ответа ничего не нужно, кроме «получилось»."""
    link_id = _path_id(ctx, "id", "Рекламная ссылка не найдена")
    resp = await ctx.call("DELETE", f"/cabinet/admin/campaigns/{link_id}")
    if resp.status_code >= 400:
        try:
            detail = str((resp.json() or {}).get("detail") or "")
        except ValueError:
            detail = ""
        hit = _DELETE_BLOCKED.search(detail)
        if hit:
            raise _bad_request(
                f"Ссылку нельзя удалить: по ней уже пришли {hit.group(1)} "
                "регистраций — иначе потеряется статистика источника. Выключите её."
            )
        raise UpstreamError(resp)
    return {"success": True}


# Имя бота нужно, чтобы собрать рекламную ссылку: их список кампаний отдаёт
# только код, а готовый deep-link — лишь в карточке. Спрашиваем один раз.
_BOT_USERNAME: dict[str, Any] = {"value": None, "until": 0.0}


async def bot_username(ctx: Ctx) -> str:
    """Имя бота из их публичной настройки виджета входа. Пусто — не смогли."""
    now = time.time()
    if _BOT_USERNAME["value"] is not None and now < _BOT_USERNAME["until"]:
        return str(_BOT_USERNAME["value"])
    data = await ctx.json("/cabinet/branding/telegram-widget", {}, soft=True) or {}
    name = str(data.get("bot_username") or "").lstrip("@")
    _BOT_USERNAME.update({"value": name, "until": now + 600})
    return name
