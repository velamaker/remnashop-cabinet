"""Админка: раздел «Вход через Telegram» (/admin/auth) поверх «Бедолаги».

ПОЧЕМУ ОТДЕЛЬНЫЙ ФАЙЛ И ОТКУДА ПРИЕХАЛ ПУТЬ. Раздел жил куском внутри
`admin_cabinet.py` (там же оформление, «Информация», меню и приложения). Держать
его там нельзя было и дальше, но и просто объявить обработчик здесь — мало:
модули `admin_*.py` подхватываются по алфавиту, `admin_cabinet` идёт ПОСЛЕ
`admin_auth`, и его регистрация молча затёрла бы нашу (реестр `HANDLERS` —
обычный словарь, последний записавший выигрывает). Поэтому кусок перенесён сюда
целиком, а на его месте в `admin_cabinet.py` оставлена ссылка на этот файл.
Других желающих на `/api/admin/auth-settings` нет — проверено `HANDLERS.get` до
объявления: путь наш собственный, у «Бедолаги» такой ручки не существует.

ЧТО НА ЭТОМ БЭКЕНДЕ ЛОЖИТСЯ ОДИН В ОДИН
  Тумблер, Client ID и Client Secret — это её обычные настройки бота
  (`TELEGRAM_OIDC_ENABLED`, `TELEGRAM_OIDC_CLIENT_ID`,
  `TELEGRAM_OIDC_CLIENT_SECRET`), которые читаются и пишутся её же ручкой
  `/cabinet/admin/settings/{key}` под правами `settings:read` / `settings:edit`.
  Отдельной админской ручки «настройки входа» у неё нет вовсе: в её
  `/openapi.json` из всего входа через Telegram публично торчит только
  `GET /cabinet/branding/telegram-widget`, и он на ЧТЕНИЕ (проверено на стенде).
  Права спрашивать самим не нужно и нельзя: ходим сессией САМОГО админа
  (`ctx.json` подставляет его Bearer), решение «пускать или нет» принимает их
  RBAC, а его 403 доезжает до кабинета как есть. Служебный токен адаптера
  (`ctx.admin`) здесь не используется ни разу: с ним креды входа правил бы любой,
  кто вошёл в кабинет.

ЧТО НЕ ЛОЖИТСЯ (и помечено `supported`, как в «Меню бота»)
  • НАСТРОЙКИ ВИДЖЕТА (`TELEGRAM_WIDGET_SIZE/RADIUS/USERPIC/REQUEST_ACCESS`).
    Они у «Бедолаги» есть и настоящие, но красят виджет в ЕЁ кабинете. Наш
    кабинет рисует Telegram Login Widget сам и параметры зашивает в код
    (`cabinet/src/components/TelegramLoginButton.tsx`: size=large, radius=12,
    request-access=write, аватар не спрашивается), а имя бота попадает в бандл на
    сборке (`VITE_TELEGRAM_BOT_USERNAME`). То есть их значения на наш экран входа
    не влияют НИКАК, и завести у себя такие поля значило бы: админ крутит размер
    кнопки, а кнопка не меняется. Своей копии этих настроек в памяти адаптера мы
    тоже не заводим — функция бота, подменять её нечем и незачем.
  • САМ ВХОД ПО OIDC. Кнопка «Войти через Telegram» в кабинете уходит браузером
    на `/api/auth/telegram/oidc/start`, а возврат от Telegram ждут на
    `/api/auth/telegram/oidc/callback` — это ручки НАШЕГО бэкенда (Authorization
    Code + PKCE, обмен кода на id_token). В связке с «Бедолагой» их обслуживать
    некому: адаптер таких путей не переводит (сейчас отвечает 501), а у бота
    браузерного флоу нет вовсе — её `POST /cabinet/auth/telegram/oidc` принимает
    УЖЕ ГОТОВЫЙ id_token, который добывает её собственный фронт (проверяет она
    его подписью через JWKS, client_secret в её коде не используется нигде).

ПОЭТОМУ ТУМБЛЕР ВКЛЮЧАЕТСЯ НЕ ВСЕГДА — и это главное решение этого файла.
  Включённый `TELEGRAM_OIDC_ENABLED` виден кабинету через их же
  `/cabinet/branding/telegram-widget` → `appearance.telegram_oidc_enabled`, а
  наша страница входа по этому признаку ПРЯЧЕТ классический виджет и показывает
  только кнопку OIDC (LoginPage.tsx, SettingsPage.tsx). В связке с «Бедолагой»
  это меняет рабочий вход на неработающий: виджет-то переведён
  (`/api/auth/telegram` → `/cabinet/auth/telegram/widget`), а кнопка OIDC ведёт
  в 501. Поэтому:
    • включение отклоняем с причиной (400), пока адаптер не обслуживает обе
      ручки входа — проверка живая, по реестру `HANDLERS`, так что появится
      перевод входа, и тумблер разблокируется сам;
    • ВЫКЛЮЧЕНИЕ разрешаем всегда: если OIDC уже включили в самом боте и вход в
      кабинет сломался, этот экран — то место, где это чинят;
    • Redirect URI не подсказываем, пока принимать возврат некому: адрес, по
      которому ничего не отвечает, хуже пустого поля.

СТЕРЕЖЁМ РЕЗУЛЬТАТ ФОРМУЛЫ, А НЕ ТУМБЛЕР (находка ревизии 6 августа).
  У «Бедолаги» вход включён, когда `TELEGRAM_OIDC_ENABLED` И заполнен
  `TELEGRAM_OIDC_CLIENT_ID` (её `branding.py`). Прежний вариант сторожил только
  ПЕРЕХОД тумблера, и защита обходилась штатным путём: тумблер уже включён (или
  включается при пустом client_id — переход разрешён, вход ещё не активен), а
  следующим сохранением вписывается Client ID — формула сходится, кабинет прячет
  рабочий виджет. Теперь считается ИТОГ обоих полей после сохранения: запрещён
  ровно переход «было выключено → станет включено», а любое движение в сторону
  выключения проходит всегда. Тем же итогом считается и `locked`, поэтому экран
  гасит оба поля разом, а не одно.

СЕКРЕТ ЗДЕСЬ НЕПРИМЕНИМ — поэтому заперт.
  Сама «Бедолага» `TELEGRAM_OIDC_CLIENT_SECRET` не использует НИГДЕ: её
  `POST /cabinet/auth/telegram/oidc` принимает готовый id_token и проверяет его
  подписью через JWKS (`validate_telegram_oidc_token(id_token, client_id)` —
  секрет туда не передаётся). Секрет нужен только обмену кода на токен — то есть
  тому браузерному входу, которого в связке с ней нет. Поэтому поле помечено
  `locked` с этой причиной, пока перевода входа нет; появится перевод — поле
  откроется само (та же проверка `_login_flow_ready`).

ЧУЖОЙ ДЕФЕКТ, О КОТОРОМ ОБЯЗАНЫ СКАЗАТЬ ВСЛУХ: СЕКРЕТ ЛОЖИТСЯ В ИХ ЖУРНАЛ
ОТКРЫТЫМ ТЕКСТОМ. Их зависимость `require_permission`
(`app/cabinet/dependencies.py`) для любого POST/PUT/PATCH/DELETE кладёт РАЗОБРАННОЕ
ТЕЛО запроса в `admin_audit_log.details.request_body` — до того, как обработчик
`PUT /cabinet/admin/settings/{key}` подменит значение маской для своей записи
(`log_value = SECRET_MASK if is_secret_key(key)`). Проверено на стенде: строки
журнала действительно содержат `request_body: {"value": ...}` как есть. Значит,
сохранённый через этот экран Client Secret оседает в их базе и в нашем разделе
«Аудит» в открытом виде — а сама настройка при этом отдаётся наружу под маской,
и заметить утечку неоткуда. Адаптером это не чинится: тело пишется ДО нашего
запроса по существу, чужой код мы не правим. Поэтому предупреждение живёт
отдельным полем `warnings` и печатается на экране рядом с полем секрета ВСЕГДА,
а не только пока поле заперто, — иначе оно пропало бы ровно тогда, когда секрет
впервые можно записать. Чинится это только у них: маскировать `request_body` по
именам ключей в `require_permission`.

ГРАБЛЯ, НА КОТОРУЮ НАСТУПИЛ ПРЕДЫДУЩИЙ ВАРИАНТ: СЕКРЕТ ПОД МАСКОЙ. Их API
секреты наружу не отдаёт — вместо значения приходит `••••••••` (маскируется всё,
в чьём имени есть TOKEN/SECRET/PASSWORD/KEY). Прежний код сверял «что попросили»
с «что вернулось» и на секрете всегда видел расхождение, поэтому отвечал
«задаётся переменной окружения бота» — хотя секрет в этот момент УЖЕ сохранялся.
Админ видел ошибку и правил .env, которого никто не читает. Здесь маска
учитывается явно, а «правится не отсюда» берётся из их же признака `env_locked`
ДО записи (и их 409 на этот случай переводится на русский).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any
from urllib.parse import quote

import httpx

from compose import HANDLERS, Ctx, UpstreamError, handler

# --- их ключи и наши поля -----------------------------------------------------

_SETTINGS = "/cabinet/admin/settings"
# Категория их справочника, в которой лежат все три ключа разом: один запрос
# вместо трёх — и, что важнее, ответ 200 с пустым списком, если у бота другой
# версии таких настроек нет. Поштучный GET в этом случае отвечает 404 и уронил бы
# весь экран вместо честного «этой настройки у бота нет».
_CATEGORY = "TELEGRAM_OIDC"

_KEY_ENABLED = "TELEGRAM_OIDC_ENABLED"
_KEY_CLIENT_ID = "TELEGRAM_OIDC_CLIENT_ID"
_KEY_SECRET = "TELEGRAM_OIDC_CLIENT_SECRET"
_KEYS = (_KEY_ENABLED, _KEY_CLIENT_ID, _KEY_SECRET)

# Поле экрана (cabinet/src/api/authSettings.ts) → их ключ.
_FIELDS = {
    "telegram_oidc_enabled": _KEY_ENABLED,
    "telegram_oidc_client_id": _KEY_CLIENT_ID,
    "telegram_oidc_client_secret": _KEY_SECRET,
}

# Заглушка вместо значения секрета в их ответах (SECRET_MASK у них же). Приходит
# и на чтение, и в ответе на запись — сверять её со значением бессмысленно.
_SECRET_MASK = "••••••••"

# Ручки браузерного входа по OIDC, которых ждёт кабинет. Спрашиваем реестр
# собственных обработчиков, а не карту маршрутов: у «Бедолаги» соответствия им
# нет и быть не может (её флоу начинается на фронте и заканчивается POST'ом
# готового id_token), так что карта тут ничего не решит — только свой перевод.
_LOGIN_ROUTES = (
    ("GET", "/api/auth/telegram/oidc/start"),
    ("GET", "/api/auth/telegram/oidc/callback"),
)

_ENV_LOCKED = "Задано в .env бота — отсюда не изменить: правьте .env и перезапускайте бота"

# Короткая причина под выключенным тумблером (кабинет рисует её как подпись).
_NO_LOGIN_FLOW = (
    "Включать нечем: браузерный вход по OIDC в связке с «Бедолагой» не переведён — "
    "кабинет спрятал бы рабочую кнопку Telegram-виджета и показал вместо неё ссылку в никуда"
)

# Секрет в связке с «Бедолагой» не нужен — подробности в шапке файла.
_SECRET_UNUSED = (
    "«Бедолага» client_secret не использует вовсе: id_token она проверяет подписью через "
    "JWKS, а секрет нужен только браузерному обмену кода на токен — этого входа здесь нет. "
    "Поле откроется, если появится перевод входа по OIDC"
)

# Предупреждение о ЧУЖОМ дефекте: печатается рядом с полем ВСЕГДА, а не только
# пока поле заперто. Запрет и утечка — разные вещи: запрет снимется сам, когда
# появится перевод входа (`_login_flow_ready`), а журнал «Бедолаги» как писал
# тело запроса до маскировки, так и будет писать. Если бы этот текст жил в
# `locked`, он исчез бы ровно в тот момент, когда секрет впервые можно записать,
# — то есть когда он единственный раз и нужен.
_SECRET_AUDIT_LEAK = (
    "Осторожно: что бы сюда ни вписали, «Бедолага» положит это в свой журнал действий "
    "(наш раздел «Аудит») ОТКРЫТЫМ ТЕКСТОМ — её проверка прав пишет тело запроса до "
    "маскировки. Её дефект, адаптером не чинится: сам ключ она хранит под маской, "
    "а запись о нём — нет"
)


def _fail(status: int, detail: str) -> UpstreamError:
    """Отказ самого адаптера в той же форме, что и ошибка бота."""
    return UpstreamError(httpx.Response(status, json={"detail": detail}))


def _bad(detail: str) -> UpstreamError:
    """400, а не 501: экран рабочий, отказ касается одного поля. 501 админ читает
    как «раздел не переведён» и перестаёт пытаться там, где всё работает."""
    return _fail(400, detail)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _flag(value: Any) -> bool:
    """Булев признак и у них, и у нас.

    Тип у ключа bool, но старые версии отдавали строку — «true»/«1» тоже
    понимаем, иначе включённый OIDC выглядел бы выключенным. Этим же разбором
    читаем тумблер ИЗ ТЕЛА ЗАПРОСА: `bool("false")` — это True, и клиент,
    приславший строку, включал бы вход словом «false».
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on", "да"}
    if isinstance(value, (int, float)):
        return value != 0
    return False


# --- что умеет сам адаптер ----------------------------------------------------


def _login_flow_ready() -> bool:
    """Обслуживает ли адаптер вход по OIDC целиком (старт И возврат).

    Проверка живая, при каждом запросе: модули грузятся все разом, и к моменту
    первого обращения реестр уже полный. Появится перевод входа отдельным
    модулем — тумблер и подсказка с Redirect URI включатся сами, без правок здесь.
    """
    return all(HANDLERS.get(route) is not None for route in _LOGIN_ROUTES)


def _redirect_uri() -> str:
    """Готовый Redirect URI для @BotFather → Web Login — или пусто.

    Публичный адрес кабинета адаптеру известен из `ADAPTER_ALLOWED_ORIGINS`
    (установщик кладёт туда WEB_CABINET_URL); принимаем и сам `WEB_CABINET_URL`,
    если его прокинули. Но адрес мало знать: пока возврат от Telegram принимать
    некому, подсказка зовёт админа прописать в BotFather ссылку, по которой
    ничего не ответит, — молчим.
    """
    if not _login_flow_ready():
        return ""
    raw = os.environ.get("ADAPTER_ALLOWED_ORIGINS") or os.environ.get("WEB_CABINET_URL") or ""
    base = next((part.strip().rstrip("/") for part in raw.split(",") if part.strip()), "")
    return f"{base}/api/auth/telegram/oidc/callback" if base else ""


# --- чтение их настроек -------------------------------------------------------


async def _definitions(ctx: Ctx) -> dict[str, dict[str, Any]]:
    """Их записи о наших трёх ключах: `{ключ: запись}`; чего нет — того нет.

    Сначала одним запросом берём категорию (он же проверяет право `settings:read`
    — их 403 уходит кабинету как есть). Если бот другой версии и категория
    называется иначе, добираем недостающие ключи поштучно и мягко: 404 «Setting
    not found» здесь означает «у этого бота такой настройки нет», а не поломку.
    """
    rows = await ctx.json(_SETTINGS, [], params={"category_key": _CATEGORY})
    defs = {
        row["key"]: row
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict) and row.get("key") in _KEYS
    }
    missing = [key for key in _KEYS if key not in defs]
    if missing:
        extra = await asyncio.gather(
            *(
                ctx.json(f"{_SETTINGS}/{quote(key, safe='')}", None, soft=True)
                for key in missing
            )
        )
        for key, row in zip(missing, extra):
            if isinstance(row, dict) and row.get("key") == key:
                defs[key] = row
    return defs


def _current(defs: dict[str, dict[str, Any]], key: str) -> Any:
    row = defs.get(key)
    return row.get("current") if isinstance(row, dict) else None


def _locked_reason(defs: dict[str, dict[str, Any]], key: str) -> str:
    """Почему ключ не пишется: задан в окружении бота или помечен read-only.

    `env_locked` их справочник считает сам; у версий, которые про него не знают,
    поля просто нет — тогда ловушку «сохранилось, но не применилось» ловит сверка
    после записи (см. `_write`).
    """
    row = defs.get(key)
    if not isinstance(row, dict):
        return ""
    return _ENV_LOCKED if (row.get("env_locked") or row.get("read_only")) else ""


def _view(defs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Ответ ровно в форме экрана (cabinet/src/api/authSettings.ts) + применимость."""
    client_id = _text(_current(defs, _KEY_CLIENT_ID))
    secret = _current(defs, _KEY_SECRET)
    enabled = _flag(_current(defs, _KEY_ENABLED))
    ready = _login_flow_ready()

    active = enabled and bool(client_id)

    supported: dict[str, bool] = {
        # Ключи, которых у этого бота нет вовсе, кабинету показывать нечем.
        "telegram_oidc_enabled": _KEY_ENABLED in defs,
        "telegram_oidc_client_id": _KEY_CLIENT_ID in defs,
        "telegram_oidc_client_secret": _KEY_SECRET in defs,
        "redirect_uri": bool(_redirect_uri()),
        # Внешний вид Telegram-виджета (их TELEGRAM_WIDGET_SIZE/RADIUS/USERPIC/
        # REQUEST_ACCESS) красит кнопку в ИХ кабинете; наш рисует её своими
        # зашитыми параметрами, так что заводить у себя такие поля — обманывать.
        "widget": False,
    }
    locked: dict[str, str] = {}
    for field, key in _FIELDS.items():
        reason = _locked_reason(defs, key)
        if reason:
            locked[field] = reason
    if not ready and not active:
        # Запираем оба слагаемых их формулы, потому что включает любое из них:
        # при заполненном Client ID достаточно тумблера, при включённом тумблере
        # — Client ID. Но ровно в том состоянии, в каком поле ВКЛЮЧАЕТ: уже
        # выставленное слагаемое оставляем правимым, чтобы его можно было снять.
        # А когда вход УЖЕ включён (так бывает: включили в самом боте, и вход в
        # кабинет сломался), не запираем ничего — этот экран и есть то место,
        # где беду чинят, а любая правка отсюда ведёт только к выключению.
        if not enabled:
            locked.setdefault("telegram_oidc_enabled", _NO_LOGIN_FLOW)
        if not client_id:
            locked.setdefault("telegram_oidc_client_id", _NO_LOGIN_FLOW)
    if not ready:
        # Секрет заперт независимо от состояния тумблера: их входу он не нужен
        # вовсе (см. шапку файла).
        locked.setdefault("telegram_oidc_client_secret", _SECRET_UNUSED)

    return {
        "telegram_oidc_client_id": client_id,
        # Значение секрета наружу не отдаём — экрану нужен только признак. Их
        # маска (`••••••••`) как раз и означает «задан».
        "has_secret": bool(_text(secret)),
        "telegram_oidc_enabled_setting": enabled,
        # Их формула (branding.py): тумблер И заданный client_id. Показываем ИХ
        # правду, а не «работает ли вход у нас»: если бот отдаёт кабинету
        # `oidc_enabled`, страница входа действительно перешла на кнопку OIDC, и
        # админ должен видеть это на экране, а не гадать.
        "telegram_oidc_active": active,
        "redirect_uri": _redirect_uri(),
        # Что на этом бэкенде применимо (`supported`) и что показать выключенным
        # с причиной (`locked`) — тот же договор, что у «Настроек» и «Меню бота»
        # (AdminSettingsPage.tsx: нет поля = бэкенд наш, экран как раньше).
        "supported": supported,
        "locked": locked,
        # Поле рабочее, но у него есть цена — экран печатает это подписью рядом,
        # независимо от `locked`. Поле необязательное: наш собственный бэкенд его
        # не шлёт, и обычная установка RemnaShop выглядит ровно как раньше.
        "warnings": {"telegram_oidc_client_secret": _SECRET_AUDIT_LEAK},
    }


# --- запись -------------------------------------------------------------------


async def _write(ctx: Ctx, key: str, value: Any, what: str) -> None:
    """Запись их настройки с проверкой, что она ПРИМЕНИЛАСЬ.

    Две их особенности, из-за которых нельзя просто отправить PUT и радоваться:
      • ключ, заданный в окружении бота, сохраняется в базу, но не применяется —
        свежие версии отвечают на такую запись 409 (по-английски), старые молча
        200. Первое переводим, второе ловим сверкой `current` с отправленным.
      • секрет в ответе замаскирован — сверять его со значением нельзя, иначе
        каждая удачная запись выглядела бы неудачной (именно так и было).
    """
    try:
        data = await ctx.json(
            f"{_SETTINGS}/{quote(key, safe='')}", {}, method="PUT", json={"value": value}
        )
    except UpstreamError as exc:
        if exc.resp.status_code == 409:
            # Их 409 — это ровно «ключ прибит в .env»; текст у них английский.
            raise _bad(f"{what} задаётся в .env бота ({key}) — правьте .env и перезапускайте бота") from exc
        raise
    if not isinstance(data, dict):
        # Ответ не их формы — сверять нечего; сама запись прошла (не 4xx/5xx).
        return
    applied = data.get("current")
    if data.get("is_secret") or applied == _SECRET_MASK:
        return
    if isinstance(value, bool) and _flag(applied) == value:
        # Булев ключ старые версии отдают строкой — сравнение «в лоб» соврало бы.
        return
    if applied != value:
        raise _bad(
            f"{what} задаётся переменной окружения бота ({key}) — через API "
            "его не поменять, правьте .env бота и перезапускайте его"
        )


def _ensure_writable(
    defs: dict[str, dict[str, Any]], view: dict[str, Any], field: str, key: str, what: str
) -> None:
    """Отказ по тем же правилам, что экран показал выключенным полем.

    Причину берём из `view["locked"]`, а не считаем заново: там уже сведено и
    «заперто у бота» (`env_locked`/`read_only`), и наши собственные запреты
    (включать вход нечем, секрет здесь не нужен и утекает в их журнал). Иначе
    договор расходится с делом: экран рисует поле запертым, а запись его
    принимает — так секрет однажды и записался мимо всех проверок.
    """
    if key not in defs:
        raise _bad(
            f"У этого бота нет такой настройки ({key}): «{what}» ему настроить нечем — "
            "похоже, версия «Бедолаги» другая"
        )
    reason = view["locked"].get(field)
    if reason:
        raise _bad(f"{what}: {reason}")


# --- ручки экрана -------------------------------------------------------------
#
# Путь заведомо свободен: до этой строки `HANDLERS.get(("GET",
# "/api/admin/auth-settings"))` пуст — у «Бедолаги» такой ручки нет, а наш
# прежний вариант из `admin_cabinet.py` убран (см. шапку файла).


@handler("GET", "/api/admin/auth-settings")
async def auth_settings_get(ctx: Ctx) -> dict[str, Any]:
    return _view(await _definitions(ctx))


@handler("PUT", "/api/admin/auth-settings")
async def auth_settings_put(ctx: Ctx) -> dict[str, Any]:
    """Сохранение экрана.

    Кабинет присылает все три поля сразу, поэтому пишем только ИЗМЕНЁННОЕ: иначе
    каждое «Сохранить» переписывало бы ключи бота и засоряло его журнал (тот
    самый, что мы показываем в «Аудите»). И проверяем ВСЁ до первой записи —
    записать пачкой их API не умеет, а упасть на втором ключе из трёх значит
    оставить настройки входа полуприменёнными.
    """
    body = ctx.payload()
    defs = await _definitions(ctx)
    view = _view(defs)

    # поле экрана, их ключ, значение, человеческое имя для текста ошибки
    plan: list[tuple[str, str, Any, str]] = []

    # Итог ИХ формулы после сохранения: не присланное поле остаётся как было.
    want_enabled = (
        _flag(body.get("telegram_oidc_enabled"))
        if "telegram_oidc_enabled" in body
        else view["telegram_oidc_enabled_setting"]
    )
    want_client_id = (
        _text(body.get("telegram_oidc_client_id"))
        if "telegram_oidc_client_id" in body
        else view["telegram_oidc_client_id"]
    )
    # Сторожим переход «выключено → включено», а не переключение тумблера:
    # включает любое из двух полей, и раньше этим обходили защиту (см. шапку).
    if want_enabled and want_client_id and not view["telegram_oidc_active"] and not _login_flow_ready():
        raise _bad(
            "Включить вход по OIDC нечем: адаптер не обслуживает возврат от Telegram "
            "(/api/auth/telegram/oidc/start и /callback), а у «Бедолаги» браузерного "
            "OIDC-входа нет — она принимает уже готовый id_token от своего фронта. "
            "Она считает вход включённым, когда стоит тумблер И заполнен Client ID, "
            "поэтому запрещено и то и другое. Если включить, кабинет спрячет рабочую "
            "кнопку Telegram-виджета и покажет вместо неё кнопку, которая ответит "
            "«не умеем». Классический вход через Telegram у бота работает — он и остаётся"
        )

    if "telegram_oidc_enabled" in body:
        if want_enabled != view["telegram_oidc_enabled_setting"]:
            plan.append(
                ("telegram_oidc_enabled", _KEY_ENABLED, want_enabled, "Вход через Telegram (OIDC)")
            )

    if "telegram_oidc_client_id" in body:
        if want_client_id != view["telegram_oidc_client_id"]:
            plan.append(("telegram_oidc_client_id", _KEY_CLIENT_ID, want_client_id, "Client ID"))

    if "telegram_oidc_client_secret" in body:
        secret = _text(body.get("telegram_oidc_client_secret"))
        # Пустое поле формы = «оставить как было» (так же ведёт себя наш бэкенд):
        # иначе каждое сохранение стирало бы секрет, которого не видно. Маску
        # (`••••••••`) на всякий случай тоже считаем «не менять»: это их же
        # заглушка, и однажды она может приехать обратно из чужого клиента.
        if secret and secret != _SECRET_MASK:
            plan.append(("telegram_oidc_client_secret", _KEY_SECRET, secret, "Client Secret"))

    for field, key, _value, what in plan:
        _ensure_writable(defs, view, field, key, what)
    for _field, key, value, what in plan:
        await _write(ctx, key, value, what)

    return _view(await _definitions(ctx)) if plan else view
