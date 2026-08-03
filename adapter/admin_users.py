"""Админка: действия над пользователем, которые оператор делает каждый день.

Блокировка, продление и выдача подписки, правка баланса и карточка подписки —
всё, ради чего админку вообще открывают. `admin.py` рядом только ЧИТАЕТ
(список, карточка, сводка, лента платежей); здесь появляются изменения.

ПОЧЕМУ ОТДЕЛЬНЫЙ ФАЙЛ. Админских ручек в нашем контракте под сорок, и переводят
их несколько человек параллельно: один файл на всех — гарантированный конфликт
при каждой правке. Регистрация при этом общая — тот же декоратор `handler`.

КАК ЭТОТ МОДУЛЬ ПОДКЛЮЧАЕТСЯ. Сам себя он не импортирует: обработчики попадают в
реестр, только если кто-то сделал `import admin_users` (так же, как сделано для
`admin` в самом конце `compose.py`), а в образ файл попадает строкой COPY в
`adapter/Dockerfile`. Без этих двух строк ручек ниже просто нет — `main.py`
честно ответит на них 501, а кабинет спрячет соответствующие кнопки. Это не
недосмотр, а следствие правила «каждый правит только свой файл»: обе строки
добавляет тот, кто собирает адаптер целиком.

ОТКУДА ДАННЫЕ И ПОЧЕМУ ИМЕННО ОТТУДА — то же правило, что в `admin.py`, и это
вопрос безопасности, а не удобства. Всё, что меняет данные, уходит в их
кабинетное `/cabinet/admin/*` СЕССИЕЙ САМОГО АДМИНА (`ctx.call`, см. `_send`), и
решение «можно ли» принимает их RBAC: `users:block`, `users:balance`,
`users:subscription` — отдельные права, и человек без них получит их же 403.
Служебный токен адаптера (`ctx.admin`, X-API-Key) здесь НЕ используется для
действий вообще: с ним админкой смог бы пользоваться любой вошедший в кабинет.
Единственное его применение ниже — дочитать поля подписки, которых их
кабинетное API не отдаёт вовсе (ссылка, сквады, дата создания), и только после
того, как их же кабинетная ручка ответила 200, то есть RBAC уже сказал «админ».

ЧТО ЭТИ ДЕЙСТВИЯ ДЕЛАЮТ НА ИХ СТОРОНЕ (оператору это важно знать заранее):
  • блокировка — не то же самое, что «отключить VPN». Если у человека есть
    активная ОПЛАЧЕННАЯ подписка, их `block_user` осознанно пропускает
    отключение панели и подписки и только помечает учётку `blocked` (вход в
    кабинет и бота закрыт, трафик идёт). У пробной/истёкшей — гасит и
    пользователя в Remnawave, и подписки. Разблокировка возвращает активными
    подписки, срок которых ещё не вышел;
  • продление, смена тарифа и выдача — сразу синхронизируются в Remnawave
    (создание/обновление пользователя панели), это делает их код, не мы;
  • правка баланса пишет им транзакцию — где она видна, подробно расписано
    у `balance` ниже.

ЧЕГО ЗДЕСЬ НЕТ. Ручки карточки, которым у «Бедолаги» нет источника (баллы
лояльности, персональные скидки, внешние сквады, отключение/удаление подписки),
не выдумываются: их путей в адаптере нет, и `main.py` отвечает на них 501 с
причиной. Кнопки в карточке при этом видны — фильтр кабинета работает разделами
целиком, а не отдельными кнопками; честный отказ по нажатию лучше, чем действие,
которое сделает не то.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from compose import Ctx, UpstreamError, handler

# Пределы берём их же (UpdateSubscriptionRequest/UpdateBalanceRequest): свои
# придумывать нельзя — разойдутся при первом их обновлении, а проверяем заранее
# только чтобы вместо их 422 от pydantic («Input should be less than...») админ
# видел человеческую строку.
_MAX_DAYS = 3650
_MAX_KOPEKS = 2_000_000_000


def _int(value: Any, default: int = 0) -> int:
    """Их числа приходят и строкой, и None — своя обёртка вместо int().

    Дублирует такую же в `admin.py` намеренно: тащить оттуда приватный хелпер
    значит связать два файла, которые правятся параллельно и порознь.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --- их ошибки по-русски ----------------------------------------------------
#
# Кабинет печатает `detail` как есть, а их админское API отвечает по-английски и
# техническим языком. Переводим ТОЛЬКО то, что реально встречается на этих пяти
# ручках: незнакомый текст уходит наверх как есть — лучше английская правда, чем
# наша догадка о том, что бот имел в виду.
_ERRORS = {
    "User not found": "Пользователь не найден",
    "User has no subscription": "У пользователя нет подписки — сначала выдайте её",
    "Tariff not found": "Тариф не найден",
    "tariff_id parameter is required": "Не выбран тариф",
    "Days must be a positive integer": "Количество дней должно быть больше нуля",
    "User not found or block failed": "Заблокировать не вышло: бот не нашёл пользователя",
    "User not found or unblock failed": "Разблокировать не вышло: бот не нашёл пользователя",
    "Failed to update balance": "Бот не смог изменить баланс",
    "User already has a subscription. Enable multi-tariff mode to add more.":
        "У пользователя уже есть подписка: этот бот разрешает только одну",
    "User already has an active subscription for this tariff. Extend it instead.":
        "У пользователя уже есть активная подписка на этот тариф — продлите её",
    "User already has an active subscription for the target tariff":
        "У пользователя уже есть активная подписка на этот тариф",
}

# «Insufficient balance. Current: 6000, requested: 999999» — самый частый отказ
# на списании, и он же самый непонятный: числа у них в копейках.
_INSUFFICIENT = re.compile(r"^Insufficient balance\. Current: (-?\d+), requested: (-?\d+)")


def _ru(text: str) -> str:
    text = text.strip()
    known = _ERRORS.get(text)
    if known:
        return known
    money = _INSUFFICIENT.match(text)
    if money:
        return (
            f"Недостаточно средств: на балансе {_int(money.group(1)) / 100:.2f} ₽, "
            f"к списанию {_int(money.group(2)) / 100:.2f} ₽"
        )
    # Отказ их RBAC. Право не переводим и не прячем: «users:balance» — это то,
    # что владелец бота выдаёт в их же админке, и без имени права разбираться
    # придётся наугад.
    if text.startswith("Permission denied"):
        need = text.split(":", 1)[1].strip() if ":" in text else ""
        if need and need != "No active roles assigned":
            return f"Недостаточно прав в боте: нужно право «{need}»"
        return "Недостаточно прав: в боте этой учётке не выдано ни одной роли"
    return text


def _detail(resp: httpx.Response) -> str:
    """Текст ошибки из их ответа. `detail` у них бывает строкой, объектом (свои
    коды) и списком (валидация pydantic) — кабинет умеет только строку и объект
    выводит сырым JSON, поэтому разбираем здесь."""
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


async def _send(ctx: Ctx, method: str, path: str, **kw: Any) -> dict[str, Any]:
    """Запрос к их кабинетному API — от имени самого админа (его сессией).

    Отдельно от `ctx.json`, потому что здесь важны две вещи, которых там нет:
    перевод их английских отказов (см. `_ru`) и то, что 5xx НЕ показывается
    админу как есть — их внутренняя ошибка это «не получилось, попробуйте
    позже», а не текст для человека. Коды 4xx (включая 401/403 от их RBAC)
    доезжают до кабинета своими: по 401 он отправляет на вход, а 403 показывает
    как причину отказа.
    """
    try:
        resp = await ctx.call(method, path, **kw)
    except httpx.HTTPError as exc:
        raise _fail(502, f"Бэкенд недоступен: {exc}") from exc
    if resp.status_code >= 500:
        raise _fail(502, "Бот ответил ошибкой — действие не выполнено, попробуйте позже")
    if resp.status_code >= 400:
        raise _fail(resp.status_code, _ru(_detail(resp)))
    try:
        body = resp.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


async def _act(ctx: Ctx, path: str, **kw: Any) -> dict[str, Any]:
    """Изменяющее действие: у их админского API все они POST."""
    return await _send(ctx, "POST", path, **kw)


async def _card(ctx: Ctx, user_id: str) -> dict[str, Any]:
    """Их карточка пользователя — единственный источник его подписок, он же и
    проверка прав: не админ получит их 403 ещё до всякого действия."""
    return await _send(ctx, "GET", f"/cabinet/admin/users/{user_id}")


# --- подписка пользователя --------------------------------------------------


async def _subscription_extra(ctx: Ctx, user_id: int) -> dict[int, dict[str, Any]]:
    """id подписки → её запись из их «машинного» `/subscriptions` (X-API-Key).

    Зачем вообще. Их кабинетная карточка пользователя отдаёт срок, тариф и
    лимиты, но не отдаёт ни ссылку подписки, ни подключённые сквады, ни дату
    создания — а в нашей форме `AdminSubscription` эти поля есть. Машинный
    список по `user_id` отдаёт ровно их одним запросом.

    Почему это не дыра в правах: сюда попадают только после того, как их
    кабинетная ручка ответила 200, то есть RBAC уже признал этого человека
    админом. И почему это не «пришивание чужих данных»: берём только записи, у
    которых их же `user_id` совпал с запрошенным — если их фильтр когда-нибудь
    начнёт игнорироваться (так у них уже бывает с фильтром метода в платежах),
    мы получим пусто, а не чужую подписку в карточке.

    Любая беда — пустой словарь: это необязательное дополнение, из-за него
    карточка не должна падать.
    """
    if not ctx.can_admin or user_id <= 0:
        return {}
    resp = await ctx.admin("GET", "/subscriptions", params={"user_id": user_id, "limit": 200})
    if resp is None or resp.status_code >= 400:
        return {}
    try:
        items = resp.json()
    except ValueError:
        return {}
    if not isinstance(items, list):
        return {}
    return {
        _int(i.get("id")): i
        for i in items
        if isinstance(i, dict) and _int(i.get("user_id"), -1) == user_id
    }


def _sub(raw: dict[str, Any], user_id: int, extra: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Их подписка → форма `AdminSubscription` нашего контракта."""
    more = extra.get(_int(raw.get("id"))) or {}
    return {
        "id": _int(raw.get("id")),
        "user_id": user_id,
        # Их статусы (active/trial/expired/disabled) кабинет красит по верхнему
        # регистру; незнакомые он показывает нейтрально, поэтому не подменяем.
        "status": str(raw.get("status") or "").upper(),
        "is_trial": bool(raw.get("is_trial")),
        "plan_name": raw.get("tariff_name"),
        "expire_at": raw.get("end_date"),
        # ГИГАБАЙТЫ: в нашем контракте у админской подписки лимит именно в ГБ
        # (в байтах его хранит только панель) — см. cabinet AdminUsersPage.
        "traffic_limit": _int(raw.get("traffic_limit_gb")),
        "device_limit": _int(raw.get("device_limit")),
        # Сквады знает только машинное API. Пустой список здесь означает «узнать
        # нечем» (нет токена адаптера) — на экране это разница видна: чипы
        # серверов рисуются лишь тогда, когда переведён и список сквадов.
        "internal_squads": [str(s) for s in (more.get("connected_squads") or [])],
        # «Внешнего сквада» (приёмника каскада) у них нет как понятия — не null,
        # а именно отсутствие механики.
        "external_squad": None,
        # Ссылка подписки: без токена адаптера её взять неоткуда, а пустая
        # строка честнее выдуманной — кабинет на этом экране её не показывает.
        "url": more.get("subscription_url") or "",
        # Дата создания есть только в машинном ответе; их `start_date` — это
        # начало текущего периода, а не создание записи, и подменять одно другим
        # значит показывать неправду в истории.
        "created_at": more.get("created_at"),
    }


def _subs_of(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Все подписки из их карточки пользователя.

    У них два поля: `subscriptions` (все) и `subscription` (одна, оставлена для
    совместимости). Берём список, а одиночную — только если списка нет вовсе:
    иначе одна и та же подписка попала бы и в «текущую», и в «историю».
    """
    subs = [s for s in (data.get("subscriptions") or []) if isinstance(s, dict)]
    if subs:
        return subs
    single = data.get("subscription")
    return [single] if isinstance(single, dict) else []


def _current(subs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Какая подписка «текущая»: активная, а если активных нет — с самым дальним
    сроком. Пустая «текущая» при живой истории читалась бы как «подписки нет», и
    админ выдал бы вторую."""
    if not subs:
        return None
    active = [s for s in subs if s.get("is_active")]
    pool = active or subs
    return max(pool, key=lambda s: str(s.get("end_date") or ""))


@handler("GET", "/api/admin/subscriptions/user/{id}")
async def user_subscription(ctx: Ctx) -> dict[str, Any]:
    """Карточка подписки пользователя — вкладка «Подписка» открывается первой.

    Отдельной ручки «подписки пользователя» у них нет: всё, что нужно, лежит в
    карточке пользователя, её и читаем — заодно это и проверка прав (их
    `users:read`, не админ → 403 уедет в кабинет).

    ВАЖНО про эту вкладку: кабинет грузит её вместе со списком тарифов
    (`GET /api/admin/plans`) одним `Promise.all`, и пока тарифов нет, вкладка
    покажет ошибку даже при рабочей ручке ниже. Это не здешняя беда, но знать
    про связку нужно.
    """
    data = await _card(ctx, ctx.param("id"))
    user_id = _int(data.get("id"))
    subs = _subs_of(data)
    # Подписок нет — и дочитывать нечего: лишний запрос к боту на каждой карточке
    # новичка ничего бы не дал.
    extra = await _subscription_extra(ctx, user_id) if subs else {}
    current = _current(subs)
    return {
        "current": _sub(current, user_id, extra) if current else None,
        # История — всё, кроме текущей. Многотарифный режим у них выключен, так
        # что обычно она пуста; но если бот включит его, лишние подписки будут
        # видны, а не потеряются.
        "history": [_sub(s, user_id, extra) for s in subs if s is not current],
    }


# --- блокировка -------------------------------------------------------------


@handler("PUT", "/api/admin/users/{id}/block")
async def block(ctx: Ctx) -> dict[str, Any]:
    """Заблокировать и разблокировать.

    Метод именно PUT: так эту ручку зовёт кабинет (`usersAdminApi.block`) и так
    она записана в контракте. У них это две разные ручки, а не флаг.

    `reason` их ручка принимает, но не спрашивать же его у админа задним числом:
    кабинет причину не собирает, и подставлять свою выдумку в их журнал нельзя —
    без параметра они пишут собственный текст «Заблокирован администратором».
    """
    want_blocked = bool(ctx.payload().get("is_blocked"))
    action = "block" if want_blocked else "unblock"
    data = await _act(ctx, f"/cabinet/admin/users/{ctx.param('id')}/{action}")
    # Что получилось, говорит их ответ (`new_status`), а не то, что мы просили:
    # иначе кабинет нарисует блокировку, которой на самом деле нет.
    new_status = str(data.get("new_status") or "").lower()
    return {
        "success": bool(data.get("success", True)),
        "is_blocked": new_status == "blocked" if new_status else want_blocked,
    }


# --- срок подписки ----------------------------------------------------------


def _days(value: Any) -> int:
    """Дни из тела запроса с их же пределами. Ноль отбиваем сами: их API на него
    отвечает 422 от pydantic, а это для админа не текст."""
    days = _int(value)
    if days == 0:
        raise _fail(400, "Укажите, на сколько дней изменить срок")
    if abs(days) > _MAX_DAYS:
        raise _fail(400, f"Срок можно менять не больше чем на {_MAX_DAYS} дней за раз")
    return days


@handler("POST", "/api/admin/subscriptions/user/{id}/extend")
async def extend(ctx: Ctx) -> dict[str, Any]:
    """Изменить срок: плюс — продлить, минус — убавить.

    Кабинет шлёт знак в тех же днях, а у них это два разных действия: `extend` и
    `shorten` (оба принимают только положительное число). Убавление ниже
    «сейчас» их код сам переводит в статус «истекла» и гасит доступ в панели.

    Истёкшую подписку `extend` не продлевает «от старой даты», а считает от
    текущего момента — то есть работает и как «вернуть доступ».

    Подписку называем явным номером, хотя лишний запрос за карточкой и стоит
    одного похода к боту: без него ИХ ручка сама выбирает «активную, иначе
    первую в списке», а карточка на экране выбирает «активную, иначе с самым
    дальним сроком». Пока многотарифный режим выключен, подписка у человека одна
    и разницы нет; со старыми записями (режим когда-то включали) админ продлил бы
    не ту подписку, которую видит.
    """
    days = _days(ctx.payload().get("days"))
    uid = ctx.param("id")
    current = _current(_subs_of(await _card(ctx, uid)))
    if current is None:
        raise _fail(404, _ERRORS["User has no subscription"])
    data = await _act(
        ctx,
        f"/cabinet/admin/users/{uid}/subscription",
        json={
            "action": "extend" if days > 0 else "shorten",
            "days": abs(days),
            "subscription_id": _int(current.get("id")),
        },
    )
    sub = data.get("subscription")
    return {
        "success": True,
        # Их ответ отдаёт подписку в том же виде, что карточка, но без ссылки и
        # сквадов (их знает только машинное API) — за ними кабинет всё равно
        # сразу перезагружает карточку целиком.
        "subscription": _sub(sub, _int(ctx.params.get("id")), {}) if isinstance(sub, dict) else None,
    }


# --- выдача подписки --------------------------------------------------------


@handler("POST", "/api/admin/subscriptions/user/{id}/grant")
async def grant(ctx: Ctx) -> dict[str, Any]:
    """Выдать подписку: тариф + срок.

    `plan_id` из кабинета — это id ТАРИФА «Бедолаги»: список тарифов кабинет
    берёт у того же адаптера (`GET /api/admin/plans`), и там id их же, так что
    пересчитывать нечего.

    Поведение повторяет наш бэкенд (`grant_subscription`): есть подписка —
    продлеваем её и переводим на выбранный тариф («extended»), нет — заводим
    новую («created»). Своей воли здесь нет: многотарифный режим у бота выключен,
    и на вторую подписку их API отвечает 400 — то есть «выдать ещё одну» не
    существует как операция, и притворяться, что мы её сделали, нельзя.

    ПОЧЕМУ ДВА ЗАПРОСА НА СМЕНЕ ТАРИФА. Одной операции «смени тариф и продли» у
    них нет: `change_tariff` меняет тариф (и пишет в их журнал транзакцию на 0 ₽
    «Смена тарифа администратором»), `extend` добавляет дни. Порядок именно
    такой — если второй запрос не пройдёт, человек останется на новом тарифе со
    старым сроком, и это видно в карточке; обратный порядок дал бы лишние дни на
    старом тарифе, что заметить куда труднее. Про частичный результат честно
    пишем в ответе.
    """
    body = ctx.payload()
    plan_id = _int(body.get("plan_id"))
    if plan_id <= 0:
        raise _fail(400, "Не выбран тариф")
    days = _int(body.get("days"))
    if not 1 <= days <= _MAX_DAYS:
        raise _fail(400, f"Срок должен быть от 1 до {_MAX_DAYS} дней")

    uid = ctx.param("id")
    user_id = _int(ctx.params.get("id"))
    # Их же карточка отвечает на два вопроса разом: есть ли права (403 от RBAC) и
    # есть ли у человека подписка.
    current = _current(_subs_of(await _card(ctx, uid)))
    path = f"/cabinet/admin/users/{uid}/subscription"

    if current is None:
        result = await _act(ctx, path, json={
            "action": "create",
            "tariff_id": plan_id,
            "days": days,
            # Пробную выдаём, только если попросили: их `create` от этого флага
            # меняет тип подписки, а кабинет по умолчанию шлёт false.
            "is_trial": bool(body.get("is_trial")),
        })
        action = "created"
    else:
        sub_id = _int(current.get("id"))
        if _int(current.get("tariff_id")) != plan_id:
            await _act(ctx, path, json={
                "action": "change_tariff", "tariff_id": plan_id, "subscription_id": sub_id,
            })
        try:
            result = await _act(ctx, path, json={
                "action": "extend", "days": days, "subscription_id": sub_id,
            })
        except UpstreamError as exc:
            if _int(current.get("tariff_id")) == plan_id:
                raise
            raise _fail(
                exc.resp.status_code,
                "Тариф сменили, а продлить не вышло: " + _detail(exc.resp),
            ) from exc
        # `is_trial` здесь не применяем: у существующей подписки их API его не
        # меняет вовсе, и молча проглотить флаг честнее, чем сделать вид, что
        # подписка стала пробной.
        action = "extended"

    sub = result.get("subscription")
    return {
        "success": True,
        "subscription": _sub(sub, user_id, {}) if isinstance(sub, dict) else None,
        "action": action,
    }


# --- баланс -----------------------------------------------------------------


@handler("POST", "/api/admin/subscriptions/user/{id}/balance")
async def balance(ctx: Ctx) -> dict[str, Any]:
    """Начислить или списать деньги. Кабинет шлёт РУБЛИ, у них — копейки.

    ЧТО ЭТО ЗАПИСЫВАЕТ В ИХ ДЕНЬГАХ (проверено на стенде). Их ручка не просто
    двигает число: с `create_transaction` она заводит транзакцию —
    `type=deposit` на плюс и `type=withdrawal` на минус, `payment_method=manual`,
    описание — то, что мы передадим. Отсюда три следствия, которые оператор
    должен знать заранее:
      • запись видна в карточке пользователя (её лента у нас собирается из их
        `recent_transactions`) и в собственной истории баланса человека в
        кабинете — то есть пользователь увидит и сумму, и текст описания;
      • в НАШЕЙ ленте платежей (`/api/admin/transactions`) её не будет: та лента
        собрана из их поиска по платежам, а это реестр СЧЕТОВ ШЛЮЗОВ. Ручное
        движение по кошельку — не платёж, и в реестре его нет. Это не потеря
        данных, а разные журналы: искать такие начисления надо в карточке;
      • описание пишем по-русски и от лица кабинета: их значение по умолчанию —
        английское «Admin balance adjustment», и именно оно уехало бы человеку в
        историю.

    Отличие от нашего бэкенда: тот при списании больше остатка прижимает баланс
    к нулю, а их API отвечает 400 и не списывает ничего. Подменять их отказ
    «успехом» нельзя — переводим текст и показываем суммы (см. `_ru`).
    """
    try:
        # Копейки округляем, а не отрезаем: 10.005 ₽ иначе превращается в 10.00 ₽,
        # и админ видит не ту сумму, которую вводил. Ошибок здесь три вида, и все
        # означают одно — «это не сумма»: не число вовсе (TypeError/ValueError),
        # «nan» (ValueError уже от int) и «inf» (OverflowError). Без общего
        # перехвата два последних доходили бы до кабинета как «внутренняя ошибка».
        kopeks = int(round(float(ctx.payload().get("amount")) * 100))
    except (TypeError, ValueError, OverflowError):
        raise _fail(400, "Сумма указана неверно") from None
    if kopeks == 0:
        raise _fail(400, "Сумма не может быть нулевой")
    if abs(kopeks) > _MAX_KOPEKS:
        raise _fail(400, "Слишком большая сумма — бот такую не принимает")

    data = await _act(ctx, f"/cabinet/admin/users/{ctx.param('id')}/balance", json={
        "amount_kopeks": kopeks,
        "description": (
            "Начисление администратором через кабинет" if kopeks > 0
            else "Списание администратором через кабинет"
        ),
        # Явно, хотя это и их значение по умолчанию: движение денег без следа в
        # журнале — то, чего в кабинете не должно случаться никогда.
        "create_transaction": True,
    })
    return {
        "success": True,
        # Отдаём НОВЫЙ баланс их же ответом, а не нашей арифметикой: между
        # чтением карточки и списанием человек мог потратить деньги сам.
        "cabinet_balance": round(_int(data.get("new_balance_kopeks")) / 100, 2),
    }
