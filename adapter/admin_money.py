"""Деньги в админке поверх «Бедолаги»: платёжные шлюзы и пополнение баланса.

Зачем отдельный модуль. Админка кабинета — это тридцать разделов и под сорок
ручек, и один файл на всех означал бы конфликты при параллельной правке.
`admin.py` — люди и лента платежей, `admin_catalog.py` — витрина, здесь —
приём денег: список шлюзов, их включение, ключи и условия пополнения.

ОТКУДА БЕРЁМ ДАННЫЕ. Только кабинетное API «Бедолаги» и только по сессии САМОГО
админа (`ctx.call`/`ctx.json` подставляют его Bearer): решение «пускать или нет»
принимает их RBAC. Служебный токен адаптера (`ctx.admin`, X-API-Key) здесь не
используется НИ РАЗУ и не должен: он принадлежит адаптеру, а не человеку, и сходи
мы за ключами шлюзов с ним — читать и переписывать их смог бы ЛЮБОЙ вошедший в
кабинет. Здесь это уже не «лишняя колонка в списке», а доступ к деньгам.

  • `/cabinet/admin/payment-methods` (+ PUT `/{method_id}`) — сам список шлюзов,
    их порядок, имя, лимиты и признак готовности;
  • `/cabinet/admin/settings` — их общий реестр настроек, где лежат ключи
    провайдеров (`YOOKASSA_SECRET_KEY`, `PLATEGA_SECRET`, …). Отдельного «API
    ключей шлюза» у них нет вовсе, ключи — это обычные настройки бота.

СЕКРЕТЫ НАРУЖУ НЕ ОТДАЁМ. Их `/cabinet/admin/settings` возвращает значения В
ОТКРЫТУЮ — там лежит и `YOOKASSA_SECRET_KEY`, и `CABINET_JWT_SECRET`. Проксировать
такое в кабинет нельзя: наш собственный бэкенд по секретным полям отдаёт только
«задано/не задано» (src/web/endpoints/admin/gateways.py:_gateway_fields), и здесь
правило то же — для секретов уходит `is_set`, а `hint` (хвост из 4 символов)
считается ТОЛЬКО для несекретных полей вроде `shop_id`. Из их ответа мы берём
ровно те ключи, что относятся к учётным данным конкретного шлюза, остальные 700
настроек не пересекают границу адаптера.

ГЛАВНАЯ ЗАСАДА: НАСТРОЙКА, КОТОРАЯ НЕ ПРИМЕНЯЕТСЯ. Их `PUT /settings/{key}`
отвечает 200 даже тогда, когда значение приехало из окружения бота: запись
ложится в БД, но игнорируется, а в лог уходит «Настройка сохранена в БД, но не
применена: значение задаётся через окружение». В типовой установке «Бедолаги»
ключи шлюзов как раз в `.env`, то есть форма «Настроить ключи» молча делала бы
вид, что сохранила. Поэтому после каждой записи мы перечитываем их же ответ и,
если значение не стало нашим, отвечаем кабинету ошибкой с причиной — молча
проглоченная правка хуже честного отказа.

ЧЕГО ЗДЕСЬ НЕТ И ПОЧЕМУ.
  • `POST /gateways/{id}/test` (тестовый платёж на 2 ₽) — по HTTP у них его нет.
    «Тест» живёт только кнопкой в их ТЕЛЕГРАМ-админке (колбэк aiogram
    `botcfg_test_payment`, app/handlers/admin/bot_configuration.py:1877), в
    кабинетное API он не вынесен и позвать его извне нечем. Собрать «тест» из
    обычного пополнения нельзя: это настоящий платёж настоящего человека, и
    «проверка» списала бы деньги. Путь не регистрируется — честный 501.
  • `PUT /api/admin/topup` — сохранять нечего: бонуса за пополнение, быстрых сумм
    и общего переключателя у них не существует (подробности у `topup_settings`).
    Лимиты живут у КАЖДОГО шлюза свои, и записать в них одно общее число значило
    бы стереть настройку каждого метода — это не перевод, а порча данных.
"""

from __future__ import annotations

import re
import time
import zlib
from typing import Any
from urllib.parse import quote

import httpx

from compose import Ctx, NotAvailable, UpstreamError, handler

# --- мелочи -----------------------------------------------------------------


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _not_found(message: str) -> UpstreamError:
    return UpstreamError(httpx.Response(404, json={"detail": message}))


def _bad_request(message: str) -> UpstreamError:
    """Ошибка В ЗАПРОСЕ КАБИНЕТА, а не «не умеем»: 400 с русским текстом.

    501 (`NotAvailable`) читается админом как «функция не переведена» — вешать
    его на неизвестное поле или на выключенный в конфиге бота шлюз нельзя, иначе
    человек перестанет пытаться там, где всё работает.
    """
    return UpstreamError(httpx.Response(400, json={"detail": message}))


# --- шлюзы: перевод ---------------------------------------------------------
#
# У них ключ метода — СТРОКА (`yookassa`, `telegram_stars`), у нас в контракте
# `id: number` (cabinet/src/api/admin.ts:925), и кабинет подставляет его в путь
# `/gateways/{id}/toggle`. Нужен стабильный числовой идентификатор.
#
# Порядковый номер и их `sort_order` не годятся: и то и другое меняется при
# перестановке методов в их админке, а значит вчерашняя вкладка кабинета
# выключила бы сегодня ЧУЖОЙ шлюз. Берём crc32 от имени метода: значение зависит
# только от самого имени, одинаково после любого перезапуска и влезает в
# безопасное целое JS. Обратно (id → метод) не «расшифровываем», а ищем в их же
# списке — то есть работаем только с методами, которые бот действительно отдал.
def _gateway_id(method_id: str) -> int:
    return zlib.crc32(str(method_id).encode()) & 0x7FFFFFFF


# Валюта. У «Бедолаги» сумма пополнения ВСЕГДА задаётся в копейках рубля
# (`TopUpRequest.amount_kopeks`, все `*_MIN_AMOUNT_KOPEKS`) — выбора валюты в их
# API нет, и крипто-провайдеры пересчитывают рубли в свой актив сами (у Heleket
# на это есть даже `HELEKET_MARKUP_PERCENT`). Для владельца цена рублёвая, её и
# показываем. Единственное исключение — звёзды: счёт выставляется в XTR по курсу
# `TELEGRAM_STARS_RATE_RUB`, поэтому у них своя валюта, а не рубли.
_RUB, _STARS = "RUB", "XTR"
_STARS_METHOD = "telegram_stars"


def _gateway(method: dict[str, Any]) -> dict[str, Any]:
    """Их метод оплаты → наш `AdminGateway` (cabinet/src/api/admin.ts:924)."""
    method_id = str(method.get("method_id") or "")
    return {
        "id": _gateway_id(method_id),
        # Кабинет подписывает шлюзы по ВЕРХНЕМУ регистру (AdminGatewaysPage.tsx:6),
        # и их имена методов ложатся на эти ключи один в один: yookassa →
        # YOOKASSA, telegram_stars → TELEGRAM_STARS.
        "type": method_id.upper(),
        "currency": _STARS if method_id == _STARS_METHOD else _RUB,
        "is_active": bool(method.get("is_enabled")),
        # Их `is_provider_configured` — это не «поля заполнены», а готовность
        # провайдера целиком: `<X>_ENABLED and <ключи> is not None`
        # (app/services/payment_method_config_service.py:_get_method_defaults →
        # app/config.py:is_yookassa_enabled и соседи). Смысл тот же, что у нашего
        # `is_configured`: бот примет платёж этим шлюзом или нет.
        "is_configured": bool(method.get("is_provider_configured")),
        "order_index": _int(method.get("sort_order")),
        # Имя: сначала переопределение из их админки, иначе их же имя по
        # умолчанию. Оба — то, что бот реально показывает человеку, поэтому
        # `null` отдавать незачем: без него кабинет напечатал бы «PAL24» как
        # «pal24» (в его словаре имён такого ключа нет).
        "display_name": method.get("display_name") or method.get("default_display_name") or None,
    }


async def _methods(ctx: Ctx) -> list[dict[str, Any]]:
    """Список методов оплаты «Бедолаги». Строго: их ошибка — наша ошибка.

    Мягкая деградация тут вредна: пустой список читается как «шлюзов нет», и
    владелец пошёл бы искать пропавшие платежи вместо того, чтобы увидеть 403.
    """
    rows = await ctx.json("/cabinet/admin/payment-methods", [])
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


async def _method_by_id(ctx: Ctx, gateway_id: int) -> dict[str, Any]:
    """Наш числовой id → их метод. Заодно это и проверка прав на чтение."""
    for row in await _methods(ctx):
        if _gateway_id(row.get("method_id") or "") == gateway_id:
            return row
    raise _not_found("Шлюз не найден")


def _path_id(ctx: Ctx) -> int:
    """Номер из пути. Не число — значит такого шлюза нет: 404, а не 500 из их
    роутера (их FastAPI ответил бы на строку простынёй pydantic-ошибок)."""
    try:
        return int(str(ctx.params.get("id") or ""))
    except ValueError:
        raise _not_found("Шлюз не найден") from None


@handler("GET", "/api/admin/gateways")
async def gateways(ctx: Ctx) -> dict[str, Any]:
    """Список платёжных шлюзов.

    Порядок их: список приходит отсортированным их же `sort_order`, и
    перетасовывать его нельзя — в этом порядке кнопки видит покупатель.
    """
    items = [_gateway(m) for m in await _methods(ctx)]
    return {"items": items, "total": len(items)}


@handler("PUT", "/api/admin/gateways/{id}/toggle")
async def gateway_toggle(ctx: Ctx) -> dict[str, Any]:
    """Включить/выключить шлюз.

    Включать ненастроенный не даём — ровно как наш бэкенд
    (src/web/endpoints/admin/gateways.py:toggle_gateway). Причина не в
    аккуратности: их `is_enabled` — это только «показывать кнопку», и метод без
    готового провайдера они всё равно отфильтруют у пользователя
    (`get_enabled_methods_for_user`). Кнопка бы «включилась», а в кабинете
    ничего не изменилось — худший вид молчаливого отказа.
    """
    method = await _method_by_id(ctx, _path_id(ctx))
    desired = bool(ctx.payload().get("is_active"))

    if desired and not method.get("is_provider_configured"):
        raise _bad_request(
            "Шлюз не готов в боте. У «Бедолаги» провайдер считается настроенным, "
            "только когда заполнены его ключи И включён флаг "
            f"{str(method.get('method_id') or '').upper()}_ENABLED в конфигурации бота."
        )

    data = await ctx.json(
        # Экранируем: имя метода приехало из их ответа, а не из нашего кода, и
        # «/» внутри увёл бы запрос по чужому адресу.
        f"/cabinet/admin/payment-methods/{quote(str(method['method_id']), safe='')}", {},
        method="PUT", json={"is_enabled": desired},
    ) or {}
    return {
        "id": _gateway_id(method["method_id"]),
        # Отвечаем тем, что вернул бот, а не тем, что просили: если он состояние
        # не поменял, кабинет обязан увидеть это, а не наш оптимизм.
        "is_active": bool(data.get("is_enabled", desired)),
    }


# --- ключи шлюзов -----------------------------------------------------------
#
# Отдельного «API настроек шлюза» у «Бедолаги» нет: ключи лежат в общем реестре
# настроек бота (`/cabinet/admin/settings`) вместе с ещё семью сотнями значений —
# от SMTP до секрета JWT. Значит, мало прочитать категорию, надо ещё и решить,
# какие её ключи вообще являются учётными данными шлюза.
#
# Список ниже — не «на глаз»: имена сверены с их конфигом, готовность провайдера
# там проверяется именно этими полями (`is_yookassa_enabled`, `is_overpay_enabled`
# и соседи в app/config.py), плюс их очевидные пары — терминал к токену, путь к
# клиентскому сертификату. Всё остальное — таймауты, URL возврата, тексты кнопок,
# лимиты сумм — в форму ключей не попадает: наш бэкенд там тоже показывает только
# учётные данные (`_NON_CRED_FIELDS` и поля `settings`-дата-класса).
_CRED_SUFFIXES = (
    "API_KEY", "API_TOKEN", "API_SECRET", "ACCESS_TOKEN", "TOKEN",
    "SECRET", "SECRET_KEY", "SECRET_ID", "SECRET_WORD_1", "SECRET_WORD_2",
    "SIGNATURE_TOKEN", "SIGNING_SECRET", "WEBHOOK_SECRET",
    "PRIVATE_KEY", "PUBLIC_KEY", "SHOP_ID", "MERCHANT_ID", "PROJECT_ID",
    "TERMINAL_PUBLIC_ID", "PUBLIC_ID", "PAYMENT_SYSTEM_ID",
    "USERNAME", "PASSWORD", "PASSPHRASE", "MID", "P12_PATH",
)
# Совпадение только КОНЦОМ имени: иначе `WATA_PUBLIC_KEY_URL` (адрес, откуда
# скачивается ключ подписи) и `WATA_PUBLIC_KEY_CACHE_SECONDS` уехали бы в форму
# как «ключи», а `TELEGRAM_API_URL` — как токен.
_CRED_RE = re.compile(r"(?:^|_)(?:" + "|".join(_CRED_SUFFIXES) + r")$")

# Что прятать. У них признака «секрет» в настройках нет вовсе (тип у всех просто
# `str`), поэтому решаем по имени — и в спорных случаях в пользу маскировки.
# Публичными остаются только идентификаторы мерчанта: их и наш бэкенд показывает
# открыто (`shop_id` — обычная строка, а не SecretStr), они печатаются в чеках и
# сами по себе платёж не проводят.
_SECRET_RE = re.compile(r"(?:^|_)(?:SECRET|TOKEN|PASSWORD|PASSPHRASE|API_KEY|PRIVATE_KEY)(?:$|_)")

# Категории их настроек меняются только при обновлении бота, а спрашивают их на
# каждое открытие формы ключей — держим короткий общий кэш (как `_roles` в
# admin.py). Внутри только имена категорий: ни одного значения настройки.
_CATEGORIES_TTL = 300.0
_categories_cache: tuple[float, list[str]] | None = None


async def _categories(ctx: Ctx) -> list[str]:
    global _categories_cache
    now = time.monotonic()
    if _categories_cache and now - _categories_cache[0] < _CATEGORIES_TTL:
        return _categories_cache[1]
    rows = await ctx.json("/cabinet/admin/settings/categories", [])
    keys = [
        str(r.get("key") or "")
        for r in (rows if isinstance(rows, list) else [])
        if isinstance(r, dict) and r.get("key")
    ]
    _categories_cache = (now, keys)
    return keys


async def _category_of(ctx: Ctx, method_id: str) -> str:
    """Категория настроек, где лежат ключи этого шлюза.

    Ищем самое длинное имя категории, с которого начинается имя метода, а не
    сравниваем напрямую: `telegram_stars` живёт в категории `TELEGRAM`, а
    `freekassa_sbp` и `freekassa_card` — в `FREEKASSA` (это подметоды одного
    мерчанта, ключи у них общие). Жёсткой таблицы «метод → категория» специально
    нет: их бот регулярно добавляет провайдеров, и таблица устаревала бы молча.
    """
    upper = method_id.upper()
    matches = [
        key for key in await _categories(ctx)
        if upper == key or upper.startswith(key + "_")
    ]
    if not matches:
        raise NotAvailable(
            f"У «Бедолаги» нет настроек для шлюза «{method_id}» — вводить нечего"
        )
    return max(matches, key=len)


async def _cred_settings(ctx: Ctx, method_id: str) -> tuple[str, list[dict[str, Any]]]:
    """Категория и её настройки-ключи (сырые записи их реестра)."""
    category = await _category_of(ctx, method_id)
    rows = await ctx.json(
        "/cabinet/admin/settings", [], params={"category_key": category}
    )
    creds = [
        r for r in (rows if isinstance(rows, list) else [])
        if isinstance(r, dict) and _CRED_RE.search(str(r.get("key") or ""))
    ]
    return category, creds


def _short_name(category: str, key: str) -> str:
    """Их `YOOKASSA_SECRET_KEY` → наше `secret_key`.

    Не косметика: по коротким именам кабинет подписывает поля по-русски
    (AdminGatewaysPage.tsx:FIELD_LABELS), и «Секретный ключ» человеку понятнее,
    чем ИМЯ_ПЕРЕМЕННОЙ_БОТА. Обратный разбор при записи идёт по этому же
    правилу и только по списку полей, который мы сами и отдали.
    """
    return (key[len(category) + 1:] if key.startswith(category + "_") else key).lower()


def _is_set(value: Any) -> bool:
    """Заполнено ли поле.

    Пустая строка и None у них равнозначны «не задано» — именно так их конфиг и
    считает готовность провайдера. А вот подставные значения-заглушки (`0` у
    `shop_id`) мы «незаполненными» не объявляем: это реальное содержимое
    настройки, и врать про него нельзя. Готовность шлюза целиком показывает
    `is_configured` на карточке, а не отдельное поле.
    """
    return value is not None and str(value).strip() != ""


@handler("GET", "/api/admin/gateways/{id}/fields")
async def gateway_fields(ctx: Ctx) -> dict[str, Any]:
    """Поля-ключи шлюза: имя, секрет ли, заполнено ли. БЕЗ значений."""
    method = await _method_by_id(ctx, _path_id(ctx))
    category, creds = await _cred_settings(ctx, str(method.get("method_id") or ""))

    fields = []
    for row in creds:
        key = str(row.get("key") or "")
        secret = bool(_SECRET_RE.search(key))
        value = row.get("current")
        # Хвост из 4 символов — подсказка «что там сейчас» — считается ТОЛЬКО для
        # несекретных полей: у короткого секрета он раскрыл бы значение целиком,
        # у длинного сузил бы перебор. Правило один в один с нашим бэкендом.
        hint = None
        if not secret and _is_set(value):
            text = str(value)
            hint = text if len(text) <= 4 else "…" + text[-4:]
        fields.append({
            "name": _short_name(category, key),
            "secret": secret,
            "is_set": _is_set(value),
            "hint": hint,
        })
    return {"fields": fields}


def _typed(value: str, declared: str) -> Any:
    """Строка из формы → значение того типа, который объявила их настройка.

    Их `PUT /settings/{key}` принимает JSON как есть, а на той стороне значение
    ложится в поле конфига: числовой `shop_id`, приехавший строкой, обнулился бы
    при следующем чтении. Тип известен из их же описания настройки — им и
    пользуемся, а не догадываемся по виду строки.
    """
    kind = declared.lower()
    if "int" in kind:
        try:
            return int(value)
        except ValueError:
            raise _bad_request("Недопустимое значение: здесь ожидается число") from None
    if "float" in kind:
        try:
            return float(value)
        except ValueError:
            raise _bad_request("Недопустимое значение: здесь ожидается число") from None
    return value


# Текст того самого молчаливого отказа их настроек — общий для правки и очистки.
_ENV_WINS = (
    "Бот берёт {key} из своей конфигурации (.env), и {what} из кабинета им не "
    "применяется. Измените значение в конфигурации бота и перезапустите его."
)


@handler("PUT", "/api/admin/gateways/{id}/fields/{field}")
async def gateway_set_field(ctx: Ctx) -> dict[str, Any]:
    """Записать один ключ шлюза.

    Пишем только в те настройки, которые сами же отдали в `fields` — имя из пути
    ни в какой ключ их реестра напрямую не превращается. Иначе через эту ручку
    можно было бы переписать ЛЮБУЮ настройку бота (там же и `CABINET_JWT_SECRET`,
    и адрес панели), и «шлюзы» стали бы дырой в их конфигурацию целиком.
    """
    method = await _method_by_id(ctx, _path_id(ctx))
    method_id = str(method.get("method_id") or "")
    wanted = str(ctx.params.get("field") or "").strip().lower()

    category, creds = await _cred_settings(ctx, method_id)
    target = next(
        (r for r in creds if _short_name(category, str(r.get("key") or "")) == wanted),
        None,
    )
    if target is None:
        raise _bad_request("Неизвестное поле")

    key = str(target["key"])
    value = str(ctx.payload().get("value") or "").strip()

    if value == "":
        # Очистка. Своего «стереть» у их настройки нет, ближайшее — сброс к
        # значению по умолчанию (DELETE): именно он снимает переопределение.
        result = await ctx.json(
            f"/cabinet/admin/settings/{quote(key, safe='')}", {}, method="DELETE"
        ) or {}
        if _is_set(result.get("current")):
            raise _bad_request(_ENV_WINS.format(key=key, what="очистка"))
    else:
        typed = _typed(value, str(target.get("type") or "str"))
        result = await ctx.json(
            f"/cabinet/admin/settings/{quote(key, safe='')}", {},
            method="PUT", json={"value": typed},
        ) or {}
        # Тот самый молчаливый отказ: 200 в ответ, «сохранено в БД, но не
        # применено» в их логе и старое значение в `current`. Сверяем и отвечаем
        # честно — иначе владелец уверен, что ключи введены, а платежи не идут.
        if str(result.get("current")) != str(typed):
            raise _bad_request(_ENV_WINS.format(key=key, what="правка"))

    # Готовность шлюза после записи спрашиваем у них же: она считается по всем
    # ключам сразу плюс флаг `<X>_ENABLED`, и вывести её из одного поля нельзя.
    fresh = await _method_by_id(ctx, _gateway_id(method_id))
    return {"ok": True, "is_configured": bool(fresh.get("is_provider_configured"))}


# --- пополнение баланса -----------------------------------------------------


# Их собственные границы суммы, зашитые в схему запроса: `TopUpRequest
# .amount_kopeks` — ge=1000 (10 ₽), le=2 000 000 000 (20 млн ₽). Лимиты метода
# могут быть шире (Platega — от 1 ₽), но тело запроса всё равно не пройдёт их
# валидацию, поэтому диапазон формы обязан начинаться отсюда.
_FLOOR_KOPEKS, _CEIL_KOPEKS = 1000, 2_000_000_000

# Звёзды исключаем: их же `POST /cabinet/balance/topup` на этот метод отвечает
# 400 «Telegram Stars payments are only available through the bot» — счёт на
# звёзды выдаёт только бот. Ровно так же поступает пользовательская форма
# пополнения в адаптере (compose._NOT_PAYABLE_IN_BROWSER) и наш собственный
# бэкенд, поэтому и настройки пополнения должны описывать тот же набор шлюзов.
_NOT_PAYABLE_IN_BROWSER = {_STARS_METHOD}


def _limits(method: dict[str, Any]) -> tuple[int, int]:
    """Действующие границы метода: переопределение из их админки, иначе умолчание."""
    low = method.get("min_amount_kopeks")
    high = method.get("max_amount_kopeks")
    return (
        max(_int(low if low is not None else method.get("default_min_amount_kopeks")), _FLOOR_KOPEKS),
        min(_int(high if high is not None else method.get("default_max_amount_kopeks")), _CEIL_KOPEKS),
    )


@handler("GET", "/api/admin/topup")
async def topup_settings(ctx: Ctx) -> dict[str, Any]:
    """Условия пополнения баланса — как они выглядят в «Бедолаге».

    Экран у нас редактируемый, а сохранять нечего: `PUT /api/admin/topup`
    намеренно не зарегистрирован (501). Все пять значений здесь настоящие —
    просто общего места, куда их записать, у «Бедолаги» нет: лимиты задаются у
    КАЖДОГО шлюза свои, а бонуса и быстрых сумм не существует вовсе (почему —
    в комментариях к полям ниже).

    Диапазон считаем ПЕРЕСЕЧЕНИЕМ, а не объединением лимитов: кабинет знает один
    диапазон на всю форму пополнения и не перепроверяет сумму при смене шлюза
    (BalancePage.tsx), поэтому админ должен видеть тот же диапазон, который
    реально пройдёт у любого доступного метода. Правило и порядок — те же, что у
    пользовательской формы (compose.topup_config), иначе админ настраивал бы одно,
    а покупатель видел другое.

    Источник — их АДМИНСКИЙ список методов, а не пользовательский: у методов есть
    фильтры «кому показывать» (тип учётки, первое пополнение, промо-группа), и
    личная выдача админа описывала бы его самого, а не установку.
    """
    methods = [
        m for m in await _methods(ctx)
        if m.get("is_enabled")
        # Метод, который бот всё равно отфильтрует (провайдер не готов), в
        # условиях пополнения не участвует — иначе диапазон сузил бы шлюз,
        # которого покупатель не увидит.
        and m.get("is_provider_configured")
        and m.get("method_id") not in _NOT_PAYABLE_IN_BROWSER
    ]

    # От самого широкого диапазона: он задаёт основу, к которой прирастают
    # совместимые методы (иначе один узкий метод отрезал бы все остальные).
    low = high = 0
    kept = False
    for method in sorted(methods, key=lambda m: _limits(m)[1] - _limits(m)[0], reverse=True):
        lo, hi = _limits(method)
        if hi < lo:
            continue
        if not kept:
            low, high, kept = lo, hi, True
            continue
        if max(low, lo) > min(high, hi):
            continue
        low, high = max(low, lo), min(high, hi)

    return {
        # Отдельного тумблера «пополнение включено» у них нет: пополнение живо
        # ровно до тех пор, пока включён хоть один пригодный шлюз.
        "enabled": kept,
        # Бонуса за пополнение у них не существует: на баланс зачисляется ровно
        # уплаченное. Единственный «бонус» в их настройках —
        # REFERRAL_FIRST_TOPUP_BONUS_KOPEKS, награда пригласившему, а не процент
        # к зачислению. Ноль здесь — не заглушка, а факт.
        "bonus_percent": 0,
        # Округляем ВНУТРЬ диапазона: минимум вверх, максимум вниз — иначе
        # граничная сумма из кабинета не пройдёт их проверку в копейках.
        "min_amount": -(-low // 100),
        "max_amount": high // 100,
        # Быстрых сумм у них нет ни в API, ни в настройках — кнопки-пресеты не
        # рисуются, сумма вводится руками. Это тоже факт, а не пустой список
        # вместо данных.
        "presets": [],
    }
