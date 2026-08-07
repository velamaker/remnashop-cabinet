"""Админка: раздел «Письмо» (/admin/email) поверх «Бедолаги» — ПУЛЬТ к её почте.

ГЛАВНОЕ РЕШЕНИЕ ЭТОГО ФАЙЛА: второго отправителя в установке не заводим.
  На нашем бэкенде экран «Письмо» — это самостоятельный почтовый клиент: свои
  настройки в `assets/email.json`, свои пресеты Gmail/Yandex/Mail.ru, отдельный
  ключ Brevo и своя отправка (`SmtpEmailSender`). У «Бедолаги» почта СВОЯ и
  рабочая: письма шлёт её `EmailService` (app/cabinet/services/email_service.py)
  по её же ключам `SMTP_*`, а тексты берёт из её редактора шаблонов. Поднять
  рядом наш sender значило бы, что из одной установки уходят письма с ДВУХ
  адресов: подтверждение почты и сброс пароля — от бота, «тест» из админки — от
  нас. Человек видит два разных отправителя, а админ правит настройки, которые
  ни на что не влияют. Поэтому по правилу владельца экран становится пультом к
  ЕЁ настройкам, а собственного хранилища почты у адаптера нет вовсе.

ОТКУДА БЕРЁМ. Из их же справочника настроек, категория `SMTP`: чтение
`GET /cabinet/admin/settings?category_key=SMTP` (право `settings:read`), запись
`PUT /cabinet/admin/settings/{key}` (право `settings:edit`). Ходим сессией
САМОГО админа — решение «пускать или нет» принимает их RBAC, его 403 доезжает до
кабинета как есть. Служебный токен адаптера (`ctx.admin`) здесь не используется
ни разу: с ним пароль от почты сервиса менял бы любой вошедший в кабинет.

ЧТО ЛОЖИТСЯ ОДИН В ОДИН (поле экрана → их ключ)
    host        → SMTP_HOST          username   → SMTP_USER
    port        → SMTP_PORT          password   → SMTP_PASSWORD (секрет)
    use_tls     → SMTP_USE_TLS       from_email → SMTP_FROM_EMAIL
    use_ssl     → SMTP_USE_SSL       from_name  → SMTP_FROM_NAME
  Записанное применяется СРАЗУ, без перезапуска бота: их `set_value` кладёт
  значение в базу и тут же правит живой объект настроек (`_apply_to_settings`),
  а `EmailService` читает его при каждой отправке.

ЧЕГО У НЕЁ НЕТ ВОВСЕ (помечено `supported: false` — кабинет такое не рисует)
  • BREVO. Отправка через HTTP API Brevo — наша фича; в её коде нет ни Brevo, ни
    любого другого API-отправителя, только SMTP (проверено поиском по всему
    `/app/app`). Ключ Brevo принять «на будущее» нельзя: он лёг бы в никуда, а
    экран показал бы почту настроенной. Отказ на сохранение объясняет, что через
    Brevo надо ходить его же SMTP-релеем (smtp-relay.brevo.com), выбрав «Свой
    SMTP», — это работает и остаётся одним отправителем.
  • ТУМБЛЕР «ПОЧТА ВКЛЮЧЕНА». У нас это отдельный флаг, у неё почта считается
    настроенной по факту заполненных полей: `is_smtp_configured()` = задан
    SMTP_HOST И (SMTP_FROM_EMAIL ИЛИ SMTP_USER). Своего тумблера рядом не
    заводим: выключатель, который ничего не выключает, хуже его отсутствия.
    Поэтому `enabled` мы ОТДАЁМ (равным этой их формуле), но на запись
    принимаем только «включено» как no-op — экран шлёт его при каждом
    сохранении, и падать на нём нельзя. «Выключить» просят объяснением: почта
    гаснет очисткой SMTP-хоста.
  • ПРОВАЙДЕР. Отдельной настройки «провайдер» у неё нет — есть host/port/TLS.
    Поэтому провайдер не хранится, а ВЫЧИСЛЯЕТСЯ из хоста, а выбор пресета в
    форме просто записывает их host/port/TLS. Побочный эффект осознанный: если
    хост совпал с известным (smtp.gmail.com), экран покажет «Gmail», даже если
    админ выбирал «Свой SMTP», — врать про несуществующую настройку хуже.

ПРО SSL И ПОРТ 465. Их `EmailService.use_ssl` = SMTP_USE_SSL ИЛИ порт 465
(RFC 8314). То есть на 465 шифрование включится само, даже с выключенной
галкой. Отдаём их ключ КАК ЕСТЬ, а про эту особенность пишем в `warnings` —
и только когда экран действительно расходится с делом (порт 465 при снятой
галке). Подмени мы значение на «вычисленное», экран прислал бы его обратно, и
каждое сохранение писало бы боту настройку, которую админ не трогал.

ПРОВЕРКА ОТПРАВКИ У НЕЁ ЕСТЬ — переводим, а не отказываем.
  `POST /cabinet/admin/email-templates/{тип}/test` (право `email_templates:edit`)
  шлёт настоящее письмо её же отправителем и её же шаблоном, с пометкой [TEST] в
  теме. Берём тип «подтверждение почты» — это ровно то письмо, которое наш экран
  и настраивает. Своего текста в тест НЕ подставляем: пришло бы письмо, не
  похожее на настоящие. Адрес проверяем сами до запроса — их ручка формат не
  смотрит и сразу зовёт SMTP.

ТЕКСТ ПИСЬМА (вторая карточка экрана) — ЧЕСТНО НЕПРИМЕНИМ, И ПОЧЕМУ ОН ЗДЕСЬ.
  Экран `/admin/email` собран из двух карточек: настройки SMTP и редактор текста
  письма с кодом (`/api/admin/email-template`). Вторая — не «ещё один экран», а
  часть этого же: страница СНАЧАЛА грузит шаблон и при ошибке не рисует ничего
  вовсе (AdminEmailPage.tsx: `if (!form) return null`). Ответь мы на шаблон 501 —
  и рабочий пульт SMTP стал бы белым экраном. Поэтому ручка шаблона живёт тут.
  Перевести её нечем, и подделывать нельзя:
    • у нас один текст, разобранный на пять кусочков ({brand}, {code},
      {minutes}); у неё 29 типов писем × 5 языков, и каждое — ЦЕЛЫЙ HTML-документ
      с её подстановками ({service_name}, {verification_url}, {expire_hours}…);
    • подтверждение адреса она шлёт СО ССЫЛКОЙ, а не с кодом (её
      `email_verification`: verification_url + expire_hours) — наши «текст перед
      кодом» и «про срок действия» описывают письмо, которого в этой связке нет.
      Код у неё только в письме о смене адреса (`email_change_code`);
    • тему записать формально можно, но её API хранит тему ВМЕСТЕ с телом: любая
      правка создаёт у бота полную копию письма, и будущие обновления его HTML
      эту копию больше не догонят. Тихо заморозить человеку шаблон ради одной
      строки — плохая сделка.
  Поэтому карточка работает как лампочка: показываем НАСТОЯЩУЮ тему письма,
  которое реально уходит людям, всё помечаем `supported: false` / `locked` с
  причиной, а сохранение отвечает 501 и говорит, где эти письма правятся.
  Кнопка «Отправить тест» на этой карточке живая — она про отправку, а не про
  текст.

ПАРОЛЬ ОТ ПОЧТЫ УТЕЧЁТ В ИХ ЖУРНАЛ — говорим об этом вслух.
  Их зависимость `require_permission` (app/cabinet/dependencies.py) для любого
  POST/PUT/PATCH/DELETE кладёт РАЗОБРАННОЕ ТЕЛО запроса в
  `admin_audit_log.details.request_body` — до того, как обработчик настроек
  подменит значение маской для своей записи. Значит, сохранённый отсюда пароль
  SMTP осядет в их базе и в нашем разделе «Аудит» открытым текстом, хотя сама
  настройка наружу отдаётся под маской и заметить это неоткуда. Адаптером не
  чинится (тело пишется ДО нашего запроса по существу), поэтому предупреждение
  живёт в `warnings` и печатается рядом с полем ВСЕГДА. Тот же чужой дефект
  описан в admin_auth.py — лечится только у них.

ГРАБЛЯ, ИЗ-ЗА КОТОРОЙ НА ЭТОМ СТЕНДЕ ЭКРАН БУДЕТ ТОЛЬКО ДЛЯ ЧТЕНИЯ. Все восемь
ключей SMTP заданы в .env бота, и их справочник честно помечает это
`env_locked`. Такой ключ их API принимает в базу, но работает всё равно значение
из окружения (на свежих сборках — сразу 409). Поэтому запертые поля мы а) отдаём
в `locked` с причиной и б) отбиваем ДО первой записи, целиком: записать половину
настроек почты и упереться на второй половине — это письма, уходящие с чужого
адреса. Это состояние стенда, а не дефект: уберут строки из .env — поля
откроются сами.

СЕКРЕТ ПОД МАСКОЙ. Значение пароля их API наружу не отдаёт — вместо него
приходит `••••••••`. Экрану нужен только признак «задан» (`has_password`), а
пустое поле формы означает «не менять» (так же ведёт себя наш бэкенд). Маску,
если она приедет обратно из формы, тоже считаем «не менять» — их обработчик
поступает точно так же.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import quote

import httpx

from compose import HANDLERS, Ctx, NotAvailable, UpstreamError, handler

# --- их ключи и наши поля -----------------------------------------------------

_SETTINGS = "/cabinet/admin/settings"
# Категория, в которой лежат все восемь ключей разом: один запрос вместо восьми
# и, что важнее, ответ 200 с пустым списком, если у бота другой версии таких
# настроек нет. Поштучный GET в этом случае отвечает 404 и уронил бы экран
# вместо честного «почты у этого бота нет».
_CATEGORY = "SMTP"

_KEY_HOST = "SMTP_HOST"
_KEY_PORT = "SMTP_PORT"
_KEY_USER = "SMTP_USER"
_KEY_PASSWORD = "SMTP_PASSWORD"
_KEY_FROM_EMAIL = "SMTP_FROM_EMAIL"
_KEY_FROM_NAME = "SMTP_FROM_NAME"
_KEY_USE_TLS = "SMTP_USE_TLS"
_KEY_USE_SSL = "SMTP_USE_SSL"

# Поле экрана (cabinet/src/api/emailSettings.ts) → их ключ. По этой же карте
# считаются `supported` (ключа нет в справочнике — элемент прятать) и `locked`
# (ключ есть, но задан в .env или отдаётся только на чтение).
_FIELD_KEYS: dict[str, str] = {
    "host": _KEY_HOST,
    "port": _KEY_PORT,
    "use_tls": _KEY_USE_TLS,
    "use_ssl": _KEY_USE_SSL,
    "username": _KEY_USER,
    "password": _KEY_PASSWORD,
    "from_email": _KEY_FROM_EMAIL,
    "from_name": _KEY_FROM_NAME,
}
_KEYS: tuple[str, ...] = tuple(_FIELD_KEYS.values())

# Заглушка вместо значения секрета в их ответах (их же SECRET_MASK). Приходит и
# на чтение, и в ответе на запись — сверять её со значением бессмысленно.
_SECRET_MASK = "••••••••"

# Их редактор писем: список типов, тело письма и проверка отправки.
_TEMPLATES = "/cabinet/admin/email-templates"
# Тип письма для «отправить тестовое» и для показа настоящей темы: подтверждение
# адреса — ровно то письмо, ради которого наш экран и существует.
_TEST_TYPE = "email_verification"
# Запасные типы, если у сборки этого письма нет: тоже служебные, тоже про вход.
_TEST_TYPE_ORDER = (_TEST_TYPE, "email_change_code", "password_reset")
# Язык письма: у них шаблоны на пять языков, у нашего экрана языка нет вовсе.
# Русский — язык этой установки; догадываться по заголовкам браузера нельзя,
# админ проверяет то письмо, которое получают люди.
_TEMPLATE_LANG = "ru"

# Пресеты — те же, что у нашего бэкенда (email_settings.PRESETS). Это НЕ вторая
# настройка: выбор пресета просто записывает их host/port/TLS.
_PRESETS: dict[str, dict[str, Any]] = {
    "gmail": {"host": "smtp.gmail.com", "port": 587, "use_tls": True, "use_ssl": False},
    "yandex": {"host": "smtp.yandex.ru", "port": 465, "use_tls": False, "use_ssl": True},
    "mailru": {"host": "smtp.mail.ru", "port": 465, "use_tls": False, "use_ssl": True},
}

# Те же две причины «показываем, но править не отсюда», что в admin_system.py и
# admin_reserve.py — экран подписывает ими выключенные поля.
_ENV_LOCKED = "Задано в .env бота — отсюда не изменить: правьте .env и перезапускайте бота"
_READ_ONLY = "Этот параметр бот отдаёт только на чтение — из кабинета не изменить"

_NO_BREVO = (
    "Отправки через API Brevo у «Бедолаги» нет — она умеет только SMTP. Чтобы слать через "
    "Brevo, выберите «Свой SMTP» и укажите его релей: smtp-relay.brevo.com, порт 587, "
    "STARTTLS, логин и SMTP-ключ из кабинета Brevo"
)

_NO_ENABLED_TOGGLE = (
    "Тумблера «почта включена» у «Бедолаги» нет: она считает почту настроенной, когда "
    "заданы SMTP-хост и адрес отправителя (или логин). Чтобы она перестала слать письма, "
    "очистите SMTP-хост"
)

# Число типов писем сознательно не называем: у другой её сборки оно другое, а
# устаревшая цифра в тексте на экране читается как враньё.
_TEMPLATE_LOCKED = (
    "Тексты писем «Бедолага» хранит своим редактором шаблонов: несколько десятков типов "
    "писем на пяти языках, и каждое — целый HTML-документ с её подстановками. Наши пять "
    "полей в это не переводятся, а правка одной темы заморозила бы у бота копию письма — "
    "обновления бота её больше не догонят. Письма правятся у неё"
)

_TEMPLATE_NOT_A_CODE = (
    "Важно: подтверждение адреса «Бедолага» шлёт СО ССЫЛКОЙ, а не с кодом, — поля про код "
    "и его срок к этому письму не относятся. Код у неё только в письме о смене адреса"
)

_PASSWORD_AUDIT_LEAK = (
    "Осторожно: что бы сюда ни вписали, «Бедолага» положит это в свой журнал действий "
    "(наш раздел «Аудит») ОТКРЫТЫМ ТЕКСТОМ — её проверка прав пишет тело запроса до "
    "маскировки. Её дефект, адаптером не чинится: сам пароль она хранит под маской, "
    "а запись о нём — нет"
)

_SSL_465_NOTE = (
    "На порту 465 «Бедолага» включает шифрование сама, даже с выключенной галкой "
    "(SMTP_USE_SSL или порт 465). Значение показано так, как оно записано у бота"
)

# Проверка адреса — та же, что у нашего бэкенда (TestEmailRequest.to). Их ручка
# формат не смотрит и сразу зовёт SMTP: без этой проверки опечатка в адресе
# превращалась бы в 30 секунд ожидания и невнятную ошибку сервера.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Отправка письма идёт синхронным smtplib с их же таймаутом 30 секунд, а общий
# предел адаптера — 25 секунд на чтение и 30 на всю ручку. Своего запаса тут
# мало не бывает: оборванный запрос выглядит как «кабинет сломался», хотя письмо
# в этот момент уходит.
_SEND_TIMEOUT = httpx.Timeout(connect=5.0, read=75.0, write=10.0, pool=5.0)
_SEND_DEADLINE = 90.0


# --- мелочи -------------------------------------------------------------------


def _bad_request(message: str) -> UpstreamError:
    """Ошибка В ЗАПРОСЕ (кривой порт, кривой адрес) — 400, а не 501.

    501 админ читает как «раздел не переведён» и перестаёт пытаться там, где всё
    работает.
    """
    return UpstreamError(httpx.Response(400, json={"detail": message}))


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_flag(value: Any) -> bool:
    """Булево и у них, и у нас.

    Тип у ключа bool, но старые сборки отдают строку — «true»/«1» тоже понимаем,
    иначе включённый TLS выглядел бы выключенным. Этим же разбором читаем флаги
    ИЗ ТЕЛА ЗАПРОСА: `bool("false")` — это True, и клиент, приславший строку,
    включал бы SSL словом «false».
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on", "да"}
    if isinstance(value, (int, float)):
        return value != 0
    return False


def _row(defs: dict[str, dict[str, Any]], key: str) -> dict[str, Any]:
    row = defs.get(key)
    return row if isinstance(row, dict) else {}


def _text(defs: dict[str, dict[str, Any]], key: str, default: str = "") -> str:
    value = _row(defs, key).get("current")
    return value.strip() if isinstance(value, str) else default


def _flag(defs: dict[str, dict[str, Any]], key: str, default: bool = False) -> bool:
    row = _row(defs, key)
    return _as_flag(row.get("current")) if "current" in row else default


def _num(defs: dict[str, dict[str, Any]], key: str, default: int = 0) -> int:
    row = _row(defs, key)
    return _int(row.get("current"), default) if "current" in row else default


def _lock_reason(defs: dict[str, dict[str, Any]], key: str) -> str:
    """Почему ключ не пишется: задан в окружении бота или помечен read-only.

    `env_locked` их справочник считает сам; у сборок, которые про него не знают,
    поля просто нет — тогда ловушку «сохранилось, но не применилось» ловит
    сверка после записи (см. `email_settings_put`).
    """
    row = _row(defs, key)
    if row.get("env_locked"):
        return _ENV_LOCKED
    return _READ_ONLY if row.get("read_only") else ""


def _provider(host: str) -> str:
    """Провайдер, вычисленный по хосту: своей настройки «провайдер» у них нет.

    Хранить его у себя (в памяти адаптера) было бы хуже: значение разъехалось бы
    с их host'ом при первой же правке .env, и экран показывал бы Gmail на почте
    Яндекса.
    """
    normalized = host.strip().lower()
    for name, preset in _PRESETS.items():
        if preset["host"] == normalized:
            return name
    return "custom"


# --- чтение их настроек -------------------------------------------------------


async def _definitions(ctx: Ctx) -> dict[str, dict[str, Any]]:
    """Их записи о наших восьми ключах: `{ключ: запись}`; чего нет — того нет.

    Сначала одним запросом берём категорию (он же проверяет право
    `settings:read` — их 403 уходит кабинету как есть). Если бот другой версии и
    категория называется иначе, добираем недостающие ключи поштучно и мягко: 404
    «Setting not found» здесь означает «у этого бота такой настройки нет», а не
    поломку.
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
    if not defs:
        # Ни одного ключа SMTP — почты у этого бота нет вовсе (или она называется
        # иначе). Пустой экран с нашими подписями врал бы про то, чего у бота
        # нет; отказ виден и понятен.
        raise NotAvailable(
            "Почты у этого бота нет: в его настройках нет ни одного параметра SMTP_* — "
            "похоже, версия «Бедолаги» другая"
        )
    return defs


def _applicability(defs: dict[str, dict[str, Any]]) -> tuple[dict[str, bool], dict[str, str]]:
    """Две карты для кабинета: что прятать (`supported`) и что показать
    выключенным (`locked`, значение — причина). Тот же договор, что в
    admin_system.py и admin_auth.py."""
    supported: dict[str, bool] = {
        # Тумблера и Brevo у неё нет вовсе — эти элементы кабинету рисовать
        # нечем (подробности в шапке файла).
        "enabled": False,
        "brevo_api_key": False,
        # Выбор провайдера работает ровно постольку, поскольку есть куда писать
        # его хост: сам по себе он у бота не хранится (см. `_provider`).
        "provider": _KEY_HOST in defs,
        # Проверка отправки у неё есть — кнопка «Тест» живая.
        "test": True,
    }
    locked: dict[str, str] = {}
    for field, key in _FIELD_KEYS.items():
        supported[field] = key in defs
        reason = _lock_reason(defs, key)
        if reason:
            locked[field] = reason
    # Пресет пишет host/port/TLS — если хоть один из них заперт, менять
    # провайдера нечем: экран покажет выбор выключенным, а не даст нажать и
    # получить отказ.
    preset_locked = [locked[field] for field in ("host", "port", "use_tls", "use_ssl") if field in locked]
    if preset_locked:
        locked["provider"] = preset_locked[0]
    return supported, locked


def _view(defs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Их настройки → форма нашего экрана (`EmailSettings` из emailSettings.ts)."""
    supported, locked = _applicability(defs)
    host = _text(defs, _KEY_HOST)
    username = _text(defs, _KEY_USER)
    from_email = _text(defs, _KEY_FROM_EMAIL)
    # Их формула готовности: `Settings.is_smtp_configured()` — хост И
    # (адрес отправителя ИЛИ логин). Пароль в неё намеренно не входит: сервер без
    # AUTH тоже бывает, и их клиент логинится только когда есть чем.
    ready = bool(host and (from_email or username))
    port = _num(defs, _KEY_PORT, 587)
    use_ssl = _flag(defs, _KEY_USE_SSL, False)
    # Предупреждения — только по делу. Про SSL на 465 говорим ровно тогда, когда
    # экран РАСХОДИТСЯ с действительностью: галка снята, а бот всё равно шифрует.
    # Печатать эту фразу при живом 587 (галка снята — и шифрования нет, всё
    # сходится) значит приучать пропускать предупреждения мимо глаз.
    warnings = {"password": _PASSWORD_AUDIT_LEAK}
    if port == 465 and not use_ssl:
        warnings["use_ssl"] = _SSL_465_NOTE
    return {
        # Тумблера у них нет — отдаём их же признак настроенности, чтобы поле не
        # было ложью. На запись он ничего не значит (см. шапку).
        "enabled": ready,
        "provider": _provider(host),
        "host": host,
        "port": port,
        "use_tls": _flag(defs, _KEY_USE_TLS, True),
        "use_ssl": use_ssl,
        "username": username,
        "from_email": from_email,
        "from_name": _text(defs, _KEY_FROM_NAME),
        # Значение секрета наружу не отдаём — экрану нужен только признак. Их
        # маска (`••••••••`) как раз и означает «задан».
        "has_password": bool(_text(defs, _KEY_PASSWORD)),
        "has_brevo_key": False,
        "is_enabled": ready,
        "presets": _PRESETS,
        # --- дальше необязательное: наш собственный бэкенд этих полей не шлёт,
        # и обычная установка RemnaShop выглядит ровно как раньше -------------
        "supported": supported,
        "locked": locked,
        # Поля рабочие, но у них есть цена — экран печатает это подписью рядом,
        # независимо от `locked`.
        "warnings": warnings,
    }


# --- ручки: настройки почты ---------------------------------------------------
#
# ПРОВЕРКА ПЕРЕД ОБЪЯВЛЕНИЕМ (правило адаптера): наши модули грузятся последними
# и молча перехватывают чужой слот — так промо-баннер однажды отобрал у бота его
# персональные офферы. Проверяем не на слово: если слот занят, отказываемся
# ImportError'ом — compose.py ловит его у каждого модуля отдельно и печатает в
# лог, адаптер остаётся жив, а наш путь честно отвечает 501. Любое другое
# исключение здесь уронило бы весь кабинет из-за одного раздела.
_SLOTS = (
    ("GET", "/api/admin/email-settings"),
    ("PUT", "/api/admin/email-settings"),
    ("POST", "/api/admin/email-settings/test"),
    ("GET", "/api/admin/email-template"),
    ("PUT", "/api/admin/email-template"),
    ("POST", "/api/admin/email-template/test"),
)
_taken = [f"{method} {path}" for method, path in _SLOTS if (method, path) in HANDLERS]
if _taken:
    raise ImportError(
        "слот " + ", ".join(_taken) + " уже занят другим модулем — раздел «Письмо» "
        "не подключён, чтобы не отобрать чужую ручку"
    )


@handler("GET", "/api/admin/email-settings")
async def email_settings_get(ctx: Ctx) -> dict[str, Any]:
    return _view(await _definitions(ctx))


def _plan(payload: dict[str, Any], view: dict[str, Any]) -> dict[str, Any]:
    """Что менять у бота: `{их ключ: значение}`, только ИЗМЕНИВШЕЕСЯ.

    Пишем лишь отличия по двум причинам: их API пишет по одному ключу (пачкой не
    умеет), и каждая запись — строка в их журнале действий, который мы же
    показываем в «Аудите». Проверяем ВСЁ до первой записи: упасть на пятом ключе
    из восьми значит оставить почту полунастроенной, то есть письма, уходящие с
    чужого адреса.
    """
    changes: dict[str, Any] = {}

    # --- то, чего у неё нет вовсе ------------------------------------------
    if "enabled" in payload and not _as_flag(payload["enabled"]):
        # «Включено» приходит при каждом сохранении — на нём падать нельзя, это
        # no-op. А вот осознанное «выключить» надо объяснить, а не проглотить.
        raise NotAvailable(_NO_ENABLED_TOGGLE)
    if str(payload.get("brevo_api_key") or "").strip():
        raise NotAvailable(_NO_BREVO)

    # --- провайдер: пресет = запись их host/port/TLS -------------------------
    #
    # ЗАПАДНЯ, ИЗ-ЗА КОТОРОЙ ПРЕСЕТ ПРИМЕНЯЕТСЯ ТОЛЬКО ПРИ СМЕНЕ ПРОВАЙДЕРА.
    # Провайдера у них нет, мы вычисляем его по хосту (см. `_provider`) и тем же
    # значением его получает форма. Значит, бот с почтой на smtp.gmail.com:465
    # выглядит как «Gmail», экран присылает provider=gmail при КАЖДОМ
    # сохранении, и «пресет главнее полей» молча переписал бы порт на 587 —
    # правка, которой админ не делал, зато письма перестали ходить. Поэтому
    # пресет вступает в силу, только когда провайдер ДЕЙСТВИТЕЛЬНО сменили: тогда
    # host/port/TLS обязаны приехать из пресета, а не остаться от прежней почты.
    preset: dict[str, Any] = {}
    if "provider" in payload:
        name = str(payload.get("provider") or "").strip().lower()
        if name == "brevo":
            raise NotAvailable(_NO_BREVO)
        if name not in (*_PRESETS, "custom"):
            raise _bad_request(
                f"Провайдер «{payload.get('provider')}» неизвестен: бывают Gmail, Yandex, "
                "Mail.ru и «Свой SMTP»"
            )
        if name != view["provider"]:
            preset = _PRESETS.get(name, {})

    # --- поля формы ---------------------------------------------------------
    host = str(payload.get("host", view["host"]) or "").strip()
    port = _int(payload.get("port", view["port"]), -1)
    use_tls = _as_flag(payload.get("use_tls", view["use_tls"]))
    use_ssl = _as_flag(payload.get("use_ssl", view["use_ssl"]))
    if preset:
        # Пресет главнее присланных host/port/TLS — ровно как в нашем бэкенде
        # (email_settings.load_email_settings: `if preset: eff.update(preset)`).
        # Иначе выбор «Gmail» с устаревшим хостом в форме сохранил бы чужой хост.
        host, port = preset["host"], preset["port"]
        use_tls, use_ssl = preset["use_tls"], preset["use_ssl"]

    if port < 1 or port > 65535:
        raise _bad_request("Порт SMTP: нужно целое число от 1 до 65535")

    from_email = str(payload.get("from_email", view["from_email"]) or "").strip()
    if from_email and not _EMAIL_RE.match(from_email):
        # Кривой адрес отправителя их API примет как строку, а письма перестанут
        # уходить у ВСЕХ — про это лучше сказать сейчас.
        raise _bad_request("Отправитель (From): нужен адрес вида noreply@example.com или пусто")

    for key, value, current in (
        (_KEY_HOST, host, view["host"]),
        (_KEY_PORT, port, view["port"]),
        (_KEY_USE_TLS, use_tls, view["use_tls"]),
        (_KEY_USE_SSL, use_ssl, view["use_ssl"]),
        (_KEY_USER, str(payload.get("username", view["username"]) or "").strip(), view["username"]),
        (_KEY_FROM_EMAIL, from_email, view["from_email"]),
        (_KEY_FROM_NAME, str(payload.get("from_name", view["from_name"]) or "").strip(), view["from_name"]),
    ):
        if value != current:
            changes[key] = value

    # --- пароль -------------------------------------------------------------
    password = str(payload.get("password") or "").strip()
    # Пустое поле формы = «оставить как было» (так же ведёт себя наш бэкенд):
    # иначе каждое сохранение стирало бы пароль, которого не видно. Маску
    # (`••••••••`) тоже считаем «не менять» — это их же заглушка, и их
    # обработчик поступает с ней точно так же.
    if password and password != _SECRET_MASK:
        changes[_KEY_PASSWORD] = password

    return changes


def _applied(defs: dict[str, dict[str, Any]], key: str, value: Any) -> bool:
    """Перечитанное значение совпало с записанным."""
    if key == _KEY_PASSWORD or _row(defs, key).get("is_secret"):
        # Секрет отдаётся маской — сверять его со значением бессмысленно, иначе
        # каждая удачная запись выглядела бы неудачной.
        return True
    if isinstance(value, bool):
        return _flag(defs, key) is value
    if isinstance(value, int):
        return _num(defs, key, -1) == value
    return _text(defs, key) == value


@handler("PUT", "/api/admin/email-settings")
async def email_settings_put(ctx: Ctx) -> dict[str, Any]:
    payload = ctx.payload()
    defs = await _definitions(ctx)
    view = _view(defs)

    changes = _plan(payload, view)

    for key in changes:
        if key not in defs:
            raise NotAvailable(f"Параметр «{key}» этот бот не знает")

    # Запертые параметры отбиваем ДО записи и целиком — теми же словами, что в
    # общих настройках: иначе половина правок применится, а вторая упрётся в их
    # 409 по-английски, и почта останется настроенной наполовину.
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

    for key, value in changes.items():
        # Их ошибку (409 «задано в .env», 400 «invalid value») отдаём как есть:
        # она про этот конкретный параметр и человеку понятна.
        await ctx.json(f"{_SETTINGS}/{quote(key, safe='')}", method="PUT", json={"value": value})

    defs = await _definitions(ctx)
    # Проверяем, что правка ПРИМЕНИЛАСЬ: их API принимает и кладёт в базу даже
    # то, что перекрыто файлом окружения, и отвечает 200 при неизменившемся
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


# --- проверка отправки --------------------------------------------------------

# Их английские отказы кабинет показал бы человеку дословно — переводим
# известные, незнакомые оставляем как есть (чужой английский текст лучше, чем
# «ошибка»).
_TEST_ERRORS = {
    "SMTP is not configured": (
        "Почта у бота не настроена: нужны SMTP-хост и адрес отправителя (или логин)"
    ),
    "No email address provided and admin has no email": "Укажите адрес, куда отправить письмо",
    "Template not found": (
        "У этого бота нет шаблона письма о подтверждении адреса — отправлять нечего"
    ),
}


async def _test_type(ctx: Ctx) -> str:
    """Тип письма для теста: их «подтверждение адреса», если он есть.

    Тип зашивать намертво нельзя — у другой сборки набор писем другой, и тест
    упёрся бы в их «Unknown template type» вместо письма. Спрашиваем их же
    список, но мягко: не ответил — берём привычный тип, а отказ придёт от самой
    отправки и будет понятнее.

    ПОЧЕМУ СВОИМ ЗАПРОСОМ, А НЕ `ctx.json(soft=True)`. Тот намеренно не глотает
    401/403 (потерянная сессия не должна выглядеть как «данных нет»), а права в
    их RBAC нарезаны поштучно: `email_templates:read` и `email_templates:edit` —
    РАЗНЫЕ разрешения (их PERMISSION_REGISTRY). Админ с одним только `edit`
    отправить письмо вправе, а список получить — нет, и его 403 на списке отменял
    бы разрешённую отправку, показывая вдобавок ошибку не про то.

    Порядок предпочтения не случайный: сначала служебные письма (их и настраивает
    наш экран), и только если таких нет вовсе — первое попавшееся. Проверка
    доказывает, что почта уходит, любым письмом, но «[TEST] Подписка истекла» в
    ящике админа читается хуже, чем «[TEST] Подтверждение email».
    """
    try:
        resp = await ctx.call("GET", _TEMPLATES)
    except httpx.HTTPError:
        return _TEST_TYPE
    if resp.status_code >= 400:
        return _TEST_TYPE
    try:
        data = resp.json()
    except ValueError:
        return _TEST_TYPE
    items = data.get("items") if isinstance(data, dict) else None
    types = [
        str(item.get("type"))
        for item in (items if isinstance(items, list) else [])
        if isinstance(item, dict) and item.get("type")
    ]
    if not types:
        return _TEST_TYPE
    for preferred in _TEST_TYPE_ORDER:
        if preferred in types:
            return preferred
    return types[0]


async def _send_test(ctx: Ctx) -> dict[str, Any]:
    """Тестовое письмо ИХ отправителем и ИХ шаблоном.

    Свой текст в тест не подставляем намеренно: их ручка это умеет
    (`subject`/`body_html` в теле), но тогда админ проверял бы письмо, которого
    люди никогда не получат. Тема у них помечается [TEST] — так видно, что это
    проверка, а не настоящее уведомление.
    """
    to = str(ctx.payload().get("to") or "").strip()
    if not _EMAIL_RE.match(to):
        raise _bad_request("Укажите адрес вида you@example.com — письмо отправляется сразу")

    ntype = await _test_type(ctx)
    try:
        resp = await ctx.call(
            "POST",
            f"{_TEMPLATES}/{quote(ntype, safe='')}/test",
            json={"language": _TEMPLATE_LANG, "email": to},
            timeout=_SEND_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        # Оборвалось ПОСРЕДИ отправки: письмо в этот момент могло уже уйти. Общий
        # ответ адаптера («бэкенд недоступен») читается как «не отправлено», и
        # админ жмёт кнопку ещё раз — получая второе письмо.
        raise UpstreamError(
            httpx.Response(
                502,
                json={
                    "detail": (
                        "Бот не ответил, пока отправлял письмо, — оно могло уйти. "
                        f"Загляните в ящик, прежде чем пробовать снова ({exc})"
                    )
                },
            )
        ) from exc
    if resp.status_code >= 400:
        detail = ""
        try:
            body = resp.json()
            detail = body.get("detail") if isinstance(body, dict) else ""
        except ValueError:
            detail = ""
        detail = (detail if isinstance(detail, str) else "").strip()
        translated = _TEST_ERRORS.get(detail)
        if translated:
            raise UpstreamError(httpx.Response(400, json={"detail": translated}))
        if detail.startswith("Failed to send test email"):
            # Их 500 с причиной от SMTP-сервера — самое ценное, что тут бывает
            # («authentication failed», «connection refused»). Схлопывать его в
            # «попробуйте позже» значит отнять у админа единственную подсказку.
            #
            # Причины бывает и нет: та же фраза без двоеточия приходит, когда их
            # отправщик просто вернул «не отправил» (`if not success`). Тогда
            # английский хвост «Failed to send test email» ничего не добавляет —
            # честнее сказать, где смотреть дальше.
            reason = detail.split(":", 1)[1].strip() if ":" in detail else ""
            raise UpstreamError(
                httpx.Response(
                    502,
                    json={
                        "detail": (
                            f"Не удалось отправить письмо: {reason}"
                            if reason
                            else "Бот не смог отправить письмо и не сказал почему — "
                            "причину он пишет в свой лог (docker logs)"
                        )
                    },
                )
            )
        if detail.startswith("Unknown template type"):
            # Бывает только у сборки с другим набором писем: типа, который мы
            # выбрали, у неё нет. Это «у бота такого нет», а не поломка.
            raise NotAvailable(
                f"У этого бота нет письма «{ntype}» — отправить проверочное нечем "
                "(похоже, версия «Бедолаги» другая)"
            )
        if resp.status_code >= 500:
            # Их внутренний трейс показывать нечего, но и «ошибка» без слов —
            # плохой ответ: оставляем то, что она сказала.
            raise UpstreamError(
                httpx.Response(
                    502,
                    json={"detail": "Бот не смог отправить письмо" + (f": {detail}" if detail else "")},
                )
            )
        # Всё остальное — их отказ по существу (401 «нет сессии», 403 «нет права
        # email_templates:edit»): такое доезжает до кабинета как есть.
        raise UpstreamError(resp)
    # `template` сверх контракта (наш бэкенд его не шлёт): каким именно письмом
    # проверяли — видно и в ящике, и в ответе, а не только по догадке.
    return {"success": True, "to": to, "template": ntype}


@handler("POST", "/api/admin/email-settings/test", deadline=_SEND_DEADLINE)
async def email_settings_test(ctx: Ctx) -> dict[str, Any]:
    return await _send_test(ctx)


# --- текст письма: честная лампочка вместо редактора --------------------------


async def _template_subject(ctx: Ctx) -> str:
    """Настоящая тема письма о подтверждении адреса — или пусто.

    Мягко и своим запросом (не `ctx.json`): права в их RBAC нарезаны по
    разделам, и админ с `settings:read`, но без `email_templates:read` получит
    403. Уронить из-за этого весь экран нельзя — пульт SMTP ему как раз доступен.
    """
    try:
        resp = await ctx.call("GET", f"{_TEMPLATES}/{quote(_TEST_TYPE, safe='')}")
    except httpx.HTTPError:
        return ""
    if resp.status_code >= 400:
        return ""
    try:
        data = resp.json()
    except ValueError:
        return ""
    languages = data.get("languages") if isinstance(data, dict) else None
    row = (languages or {}).get(_TEMPLATE_LANG) if isinstance(languages, dict) else None
    subject = row.get("subject") if isinstance(row, dict) else ""
    return subject.strip() if isinstance(subject, str) else ""


def _fail(status: int, detail: str) -> UpstreamError:
    """Отказ самого адаптера в той же форме, что и ошибка бота."""
    return UpstreamError(httpx.Response(status, json={"detail": detail}))


async def _require_admin(ctx: Ctx) -> None:
    """Права спрашиваем сами там, где своего запроса к боту может не быть.

    Обычно проверка выходит бесплатно: раздел всё равно ходит в их API сессией
    админа, и отказ RBAC доезжает до кабинета как есть. Но у ручки шаблона
    единственный запрос — «мягкий»: любой отказ она превращает в пустую тему,
    чтобы админ с урезанными правами не терял весь экран. Из-за этого она
    отвечала 200 ОБЫЧНОМУ пользователю (нашлось аудитом 7 августа): данных он не
    получал, но админская ручка не должна отвечать «хорошо» тому, кому в админку
    нельзя — сегодня она пустая, а завтра в неё добавят настоящее поле.
    """
    if not ctx.token:
        raise _fail(401, "Требуется вход")
    me = await ctx.json("/cabinet/auth/me/is-admin", {}, soft=True) or {}
    if not me.get("is_admin"):
        raise _fail(403, "Недостаточно прав")


@handler("GET", "/api/admin/email-template")
async def email_template_get(ctx: Ctx) -> dict[str, Any]:
    """Форма `EmailTemplate` (emailTemplate.ts), но как лампочка, а не редактор.

    Показываем НАСТОЯЩУЮ тему письма, которое реально уходит людям, остальные
    поля пустые: у «Бедолаги» письмо — цельный HTML-шаблон, наших кусочков в нём
    нет. Почему ручка вообще здесь и почему не 501 — в шапке файла (страница
    грузит шаблон первым и при ошибке не рисует ничего вовсе).
    """
    await _require_admin(ctx)
    return {
        "subject": await _template_subject(ctx),
        "heading": "",
        "intro": "",
        "expire_note": "",
        "ignore_note": "",
        # --- необязательное: наш бэкенд этих полей не шлёт --------------------
        "supported": {
            # Тема у неё есть, но правится не отсюда — показываем выключенной.
            "subject": True,
            "heading": False,
            "intro": False,
            "expire_note": False,
            "ignore_note": False,
            # Отправка проверяется — кнопка «Отправить тест» живая.
            "test": True,
        },
        "locked": {"subject": _TEMPLATE_LOCKED},
        "warnings": {"subject": _TEMPLATE_LOCKED, "intro": _TEMPLATE_NOT_A_CODE},
    }


@handler("PUT", "/api/admin/email-template")
async def email_template_put(_: Ctx) -> dict[str, Any]:
    """Сохранять нечего — и это ответ, а не заглушка.

    Молча принять правку было бы худшим из вариантов: админ уверен, что поменял
    текст письма, а люди получают прежнее. 501 с причиной — там, где эти письма
    правятся на самом деле.
    """
    raise NotAvailable(_TEMPLATE_LOCKED + ". " + _TEMPLATE_NOT_A_CODE)


@handler("POST", "/api/admin/email-template/test", deadline=_SEND_DEADLINE)
async def email_template_test(ctx: Ctx) -> dict[str, Any]:
    """Вторая кнопка «Отправить тест» на том же экране — та же отправка."""
    return await _send_test(ctx)
