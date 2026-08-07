"""Бэкап настроек: экспорт и импорт настроек САМОГО адаптера одним файлом.

ЧТО ИМЕННО БЭКАПИМ. Только то, что хранит адаптер: строки его собственной
таблицы `settings` (state.py) — пауза подписки, «Статус сервиса», промо-баннер,
дайджест, win-back, вход с нового устройства, утренняя сводка. Это настройки
КАБИНЕТА: у «Бедолаги» такого хранилища нет и быть не может, поэтому если их не
сохранит этот раздел, их не сохранит никто.

ЧЕГО ЗДЕСЬ НЕТ И ПОЧЕМУ. Настроек самого бота. Их у него семьсот с лишним ключей
(среди них токены платёжек, пароли SMTP, секрет JWT), у него для них свои
средства, и складывать их в файл, который админ скачивает в браузер, — прямая
утечка. Ровно этими словами отказывается их отдавать `admin_system.py`; тот отказ
остаётся в силе — он про НАШ комплект `assets/*.json` и про чужие 750 ключей, а
не про семь строк собственной памяти адаптера, которых нет больше нигде.

ФОРМА ФАЙЛА — та же, что у нашего бэкенда (`admin_src/.../settings_io.py`) и та,
которую ждёт экран (`AdminBackupPage.tsx`, `SettingsBundle` в `api/admin.ts`):
`{version, exported_at, assets: {"имя.json": {...}}}`. Имена разделов внутри
`assets` намеренно совпадают с именами файлов нашего бэкенда: у семи разделов
формы настроек совпадают слово в слово (так они и писались), поэтому бандл с
установки «поверх нашего бэкенда» здесь применится, а лишние разделы честно
уедут в `skipped`, а не молча пропадут.

ПОЧЕМУ ИМПОРТ НЕ ПИШЕТ В БАЗУ НАПРЯМУЮ. Потому что настройка — это не «строка
JSON», а обещание поведения, и проверяет это обещание каждый раздел сам:
  • `promo-banner` не пускает `javascript:` в ссылку кнопки — а баннер видит
    КАЖДЫЙ пользователь кабинета, то есть прямая запись файла в базу означала бы
    XSS у всех сразу;
  • `digest`, `winback`, `new-device`, `morning-summary` не дают включить
    рассылку, когда доставлять нечем или некому (тумблер «включено» при молчащей
    рассылке — ложь в админке);
  • `freeze`, `server-status`, `digest`, `winback` держат границы значений
    (`max_days`, проценты, часы) — файл, набранный руками, их не знает.
Поэтому импорт зовёт ШТАТНЫЕ обработчики сохранения тех же разделов
(`PUT /api/admin/...` из `HANDLERS`), подсовывая им настройку из файла как
обычное тело запроса. Заодно это снимает вечную беду таких модулей: список
полей не надо повторять и потом не забывать обновлять — что раздел умеет
сохранять, то и восстановится.

ВСЁ ИЛИ НИЧЕГО. Если хоть один раздел отказался (нет прав, нечем доставлять,
испорченное значение), уже применённые разделы возвращаются как были, и импорт
отвечает отказом с названием раздела и причиной. «Часть настроек применилась,
часть нет» — худший из исходов: потом гадать, что же в итоге записано.

СЕКРЕТОВ В ФАЙЛЕ НЕТ. Их и не хранит ни один из этих разделов, но защита стоит
на входе и на выходе: поля с именами вроде `*secret*`/`*token*`/`*password*` не
попадают в бандл и не применяются из него. Плюс адресат утренней сводки
(`recipient_telegram_id`) — это ЛИЧНЫЙ телеграм живого человека (на стенде там
владелец), и ему в скачиваемом файле не место; в довесок такой файл, применённый
на другой установке, направил бы сводки постороннему. Раздел восстановится без
адресата, а он определится сам при включении — ровно так, как это делает экран.

ЧТО ЗДЕСЬ СОЗНАТЕЛЬНО НЕ БЭКАПИТСЯ, хотя лежит в той же таблице: рабочие следы
задач (`digest_sent`, `winback_sent`, `new_device_seen`, `morning_summary_state`)
и лента уведомлений админки (`admin_notifications`). Это не настройки, а память
о сделанном: восстановление чужих отметок «этому уже слали» — либо пропущенная
рассылка, либо повторная. Плюс `traffic_alert` — строка от снятой фичи, за ней
не стоит ни одного раздела (алерт о трафике у «Бедолаги» свой).

ПРАВА. Своих ролей у адаптера нет, поэтому спрашиваем RBAC бота, как соседние
разделы: админ бота плюс право `settings:edit`. Наш бэкенд пускает сюда только
владельца, потому что его бандл содержит секреты; здесь секретов нет, но и
скачивание всей конфигурации, и тем более её замена — не то, что делает админ
«только для просмотра».
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from typing import Any, NamedTuple

import httpx

from compose import HANDLERS, Ctx, NotAvailable, UpstreamError, handler

# Версия формата. Та же единица, что у нашего бэкенда: формы разделов совпадают,
# и придумывать свою нумерацию значило бы объявить чужие файлы несовместимыми.
_VERSION = 1

# Пометка происхождения. Кабинету она не нужна (лишние поля он игнорирует), а
# человеку, открывшему файл, сразу видно, чья это установка и что внутри.
_SOURCE = "adapter"

# Потолок на размер бандла. Настройки семи разделов — это единицы килобайт;
# всё, что на два порядка больше, — не бэкап, а чужой файл или мусор. Без
# потолка такой файл молча уехал бы в общую строку настроек, которую соседние
# разделы читают и переписывают целиком на каждое событие.
_MAX_BUNDLE_BYTES = 512 * 1024

# Право их RBAC на правку настроек — то же, которым закрыты соседние разделы
# группы «Настройки» (промо-баннер, оформление).
_EDIT_RIGHT = "settings:edit"


class _Section(NamedTuple):
    """Раздел бэкапа: строка нашей памяти + чья это настройка."""

    name: str          # имя внутри `assets` (как файл у нашего бэкенда)
    key: str           # ключ в state.settings
    path: str          # путь раздела: у него же берём PUT для восстановления
    title: str         # человеческое имя — для причины отказа
    private: tuple[str, ...] = ()  # поля, которые не выгружаем и не применяем


# Разделы, которые бэкапим. Список ручной, и это осознанно: в таблице `settings`
# лежат не только настройки (см. шапку), а «выгрузить всё, что нашли» означало
# бы однажды выложить в файл рабочие отметки рассылок или ленту уведомлений.
# Новый раздел с настройкой в нашей памяти = новая строка здесь.
_SECTIONS: tuple[_Section, ...] = (
    _Section("freeze.json", "freeze", "/api/admin/freeze", "Пауза подписки"),
    _Section(
        "server_status.json", "server_status", "/api/admin/server-status",
        "Статус сервиса",
    ),
    _Section("promo_banner.json", "promo_banner", "/api/admin/promo-banner", "Промо-баннер"),
    _Section(
        "trial_discount.json", "trial_discount", "/api/admin/trial-discount",
        "Скидка триальщикам",
    ),
    _Section("digest.json", "digest", "/api/admin/digest", "Ежемесячный дайджест"),
    _Section("winback.json", "winback", "/api/admin/winback", "Возврат ушедших"),
    _Section("new_device.json", "new_device", "/api/admin/new-device", "Новое устройство"),
    _Section(
        "morning_summary.json", "morning_summary", "/api/admin/morning-summary",
        "Утренняя сводка",
        # Адресат — личный телеграм живого человека и одновременно адрес
        # доставки: см. шапку файла.
        private=("recipient_telegram_id", "recipient_source"),
    ),
)

# Приметы секрета в имени поля. Ни один сегодняшний раздел таких полей не
# хранит — это страховка на будущее: настройка с токеном, добавленная соседом
# через полгода, не должна уехать в скачиваемый файл сама собой.
# Отдельно про «key»: такой приметы здесь НЕТ намеренно, иначе под неё попал бы
# `service_keywords` из «Статуса сервиса» — обычный список слов.
_SECRET_HINTS = ("secret", "token", "password", "passwd", "api_key", "private_key")


def _fail(status: int, detail: str) -> UpstreamError:
    """Отказ самого адаптера в той же форме, что и ошибка бота."""
    return UpstreamError(httpx.Response(status, json={"detail": detail}))


# --- права --------------------------------------------------------------------


async def _require_admin(ctx: Ctx) -> None:
    """Пускать только администратора бота.

    Ручки этого раздела не ходят в их API за данными, а читают и пишут СВОЮ
    память, поэтому права обязаны спросить явно: иначе конфигурацию кабинета
    скачал бы любой, кто знает путь.
    """
    if not ctx.token:
        raise _fail(401, "Требуется вход")
    me = await ctx.json("/cabinet/auth/me/is-admin", {}, soft=True) or {}
    if not me.get("is_admin"):
        raise _fail(403, "Недостаточно прав")


async def _require_edit(ctx: Ctx) -> None:
    """Право на правку настроек — по их RBAC. Сравнение по их правилу: право
    пользователя это ШАБЛОН (`*:*`, `settings:*`), требуемое — строка.
    Не смогли спросить — считаем, что права нет (fail-closed)."""
    data = await ctx.json("/cabinet/auth/me/permissions", {}, soft=True) or {}
    granted = data.get("permissions")
    ok = isinstance(granted, list) and any(
        isinstance(p, str) and fnmatchcase(_EDIT_RIGHT, p) for p in granted
    )
    if not ok:
        raise _fail(403, f"Недостаточно прав в боте: нужно право «{_EDIT_RIGHT}»")


# --- разделы ------------------------------------------------------------------


def _sections() -> list[_Section]:
    """Разделы, которые адаптер реально умеет восстановить.

    Мерка — наличие ШТАТНОГО сохранения (`PUT`): модуль раздела может не попасть
    в образ (`COPY *.py` в adapter/Dockerfile) или собираться прямо сейчас.
    Выгружать настройку, которую потом нечем применить, нельзя: бэкап,
    восстанавливающийся наполовину, хуже отсутствия бэкапа.
    """
    return [s for s in _SECTIONS if HANDLERS.get(("PUT", s.path)) is not None]


def _kind(value: Any) -> str:
    """Что за значение пришло — словами. В отказе про испорченный файл английское
    `str` из питона ничего не объясняет тому, кто этот файл открыл в блокноте."""
    if value is None:
        return "пусто"
    if isinstance(value, bool):
        return "да/нет"
    if isinstance(value, (int, float)):
        return "число"
    if isinstance(value, str):
        return "строка"
    if isinstance(value, list):
        return "список"
    return type(value).__name__


def _looks_secret(field: str) -> bool:
    lowered = field.lower()
    return any(hint in lowered for hint in _SECRET_HINTS)


def _public(section: _Section, stored: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Поля раздела, которым место в файле. Вторым — что выкинули и почему.

    Одна и та же мерка работает в обе стороны: чего мы не выгружаем, то и не
    применяем из чужого файла — иначе бандл, поправленный руками, вернул бы
    адресата сводки, которого мы только что отказались выгружать.
    """
    fields: dict[str, Any] = {}
    dropped: list[str] = []
    for name, value in stored.items():
        field = str(name)
        if field in section.private or _looks_secret(field):
            dropped.append(f"{section.name}:{field}")
            continue
        fields[field] = value
    return fields, dropped


# --- экспорт ------------------------------------------------------------------


@handler("GET", "/api/admin/settings-io/export")
async def settings_export(ctx: Ctx) -> dict[str, Any]:
    """Бандл настроек адаптера. Ровно то, что лежит в его памяти.

    Раздела, которого админ ни разу не сохранял, в файле не будет: в памяти его
    нет, а выдумать «значения по умолчанию» за раздел мы не можем — они его, и
    он вправе их менять. Для восстановления это то же самое: отсутствующий
    раздел импорт не трогает, и он остаётся на умолчаниях.
    """
    await _require_admin(ctx)
    await _require_edit(ctx)
    state = ctx.state
    if state is None or not state.available:
        raise NotAvailable(
            "Экспортировать нечего: у адаптера нет доступа к своему хранилищу, "
            "поэтому все настройки кабинета сейчас на значениях по умолчанию"
        )

    assets: dict[str, Any] = {}
    for section in _sections():
        stored = state.get_setting(section.key, None)
        if not isinstance(stored, dict) or not stored:
            continue
        fields, _dropped = _public(section, stored)
        if fields:
            assets[section.name] = fields
    return {
        "version": _VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source": _SOURCE,
        "assets": assets,
    }


# --- импорт -------------------------------------------------------------------


def _known_names() -> str:
    return ", ".join(s.name for s in _sections()) or "—"


_Plan = tuple[list[tuple[_Section, dict[str, Any]]], list[str], list[str]]


def _plan(assets: dict[str, Any]) -> _Plan:
    """Разбор файла ДО первой записи: что применим, что пропустим, что выкинем.

    Разбираем всё сразу и целиком, потому что отказ на полпути означал бы
    полуприменённый файл. Незнакомый раздел — не ошибка, а `skipped`: в бандле
    нашего бэкенда двадцать разделов, тринадцать из них принадлежат ему самому
    или боту (оформление, меню, почта, тарифы), и ронять из-за них
    восстановление остальных семи незачем.
    """
    known = {s.name: s for s in _sections()}
    plan: list[tuple[_Section, dict[str, Any]]] = []
    skipped: list[str] = []
    ignored: list[str] = []
    for name, content in assets.items():
        section = known.get(str(name))
        if section is None:
            skipped.append(str(name))
            continue
        if not isinstance(content, dict):
            raise _fail(
                400,
                f"Раздел «{name}» в файле испорчен: ожидались настройки объектом, "
                f"а там {_kind(content)}. Ничего не изменено.",
            )
        fields, dropped = _public(section, content)
        ignored += dropped
        if not fields:
            # Пустой раздел — не отказ: применять нечего, и молчать об этом
            # нельзя, поэтому он уходит в «пропущено», а не растворяется.
            skipped.append(str(name))
            continue
        plan.append((section, fields))
    return plan, skipped, ignored


async def _apply(ctx: Ctx, section: _Section, fields: dict[str, Any]) -> dict[str, Any]:
    """Отдать настройку штатному сохранению раздела и вернуть его ответ.

    Обработчик читает тело через `ctx.payload()`, другого входа у него нет,
    поэтому подсовываем копию контекста с ДРУГИМ телом. Именно копию: свой
    контекст нужен целым — по нему дальше проверяются права и ходят остальные
    разделы.
    """
    save = HANDLERS[("PUT", section.path)]
    sub = copy.copy(ctx)
    sub._body = json.dumps(fields, ensure_ascii=False).encode("utf-8")  # noqa: SLF001
    result = await save(sub)
    return result if isinstance(result, dict) else {}


def _rollback(state: Any, before: dict[str, Any]) -> None:
    """Вернуть настройки как были. Зовётся при любом отказе посреди импорта."""
    for key, value in before.items():
        # Строки не было вовсе — пишем пустой объект, а не `null`: все разделы
        # читают настройку как «что сохранено поверх значений по умолчанию», и
        # пустой объект для них ровно то же самое, что отсутствие строки.
        # Удаления строки в state.py нет, а заводить его ради отката — менять
        # общий файл, который в это время может править кто-то другой.
        state.set_setting(key, {} if value is None else value)


def _sentence(text: str) -> str:
    """Чужую причину дописываем точкой, если её нет: дальше идёт наша фраза, и
    без точки две причины слипаются в одну неразборчивую."""
    cleaned = " ".join(str(text).split())
    return cleaned if not cleaned or cleaned[-1] in ".!?…" else cleaned + "."


def _explain(section: _Section | None, exc: BaseException) -> BaseException:
    """Причина отказа с названием раздела. Форму отказа сохраняем: их 403 должен
    остаться 403, наш 501 — 501, иначе кабинет покажет не то."""
    where = f"Раздел «{section.title}»" if section else "Импорт"
    tail = "Настройки не изменены — файл не применён."
    if isinstance(exc, UpstreamError):
        try:
            detail = (exc.resp.json() or {}).get("detail") or ""
        except ValueError:
            detail = ""
        reason = _sentence(detail) or "отказ бота."
        return _fail(exc.resp.status_code, f"{where}: {reason} {tail}")
    if isinstance(exc, NotAvailable):
        return NotAvailable(f"{where}: {_sentence(str(exc))} {tail}")
    return exc


def _note(ctx: Ctx, restored: list[str], turned_on: list[str]) -> None:
    """Строка в ленту уведомлений админки: кто-то заменил настройки файлом.

    Действие редкое и крупное (может включить рассылки), и след о нём нужен —
    молча подменённая конфигурация потом ищется днями.

    Импорт ЛЕНИВЫЙ, внутри функции, и это не стиль. Наш файл — первый по алфавиту
    среди `admin_*`, то есть загружается раньше всех соседей, а `import` из шапки
    затащил бы ленту (и всё, что она тянет за собой) в этот ранний слот. Порядок
    регистрации соседям не безразличен: та же лента нарочно импортирует
    `admin_support` ДО объявления своих ручек, чтобы запомнить прежнего владельца
    общего пути и отдавать ему запросы. Переставлять такие цепочки из чужого
    модуля нельзя. К моменту запроса всё уже загружено, и `import` здесь — просто
    взгляд в `sys.modules`.
    """
    try:
        from admin_notifications import record
    except Exception:  # noqa: BLE001 — модуля может не быть в образе
        return
    body = "Восстановлено разделов: " + str(len(restored))
    if restored:
        body += " (" + ", ".join(restored) + ")"
    if turned_on:
        body += ". Включено: " + ", ".join(turned_on)
    try:
        record(
            ctx, "settings_import", "Настройки восстановлены из файла", body,
            # Свой ключ дедупликации: два импорта подряд — это два разных
            # события, и склеивать их в одну запись нельзя.
            key=f"settings_import:{datetime.now(timezone.utc).isoformat()}",
        )
    except Exception:  # noqa: BLE001 — запись в ленту не должна ломать импорт
        return


@handler("POST", "/api/admin/settings-io/import")
async def settings_import(ctx: Ctx) -> dict[str, Any]:
    """Восстановление настроек из бандла. Всё или ничего.

    Форма ответа — та, которую ждёт экран (`AdminBackupPage.tsx`):
    `restored`/`skipped`/`count`. Остальные поля сверх контракта кабинет
    игнорирует, а в ответе на `curl` они отвечают на первые вопросы: что
    выкинули и что включилось.
    """
    await _require_admin(ctx)
    await _require_edit(ctx)
    state = ctx.state
    if state is None or not state.available:
        raise NotAvailable(
            "Восстанавливать некуда: у адаптера нет доступа к своему хранилищу"
        )

    body = ctx.payload()
    assets = body.get("assets")
    # Три разные беды — три разные причины: «выбрали не тот файл», «файл побили»
    # и «файл пустой». Одно общее «не похож на бэкап» заставляло бы гадать.
    if assets is None:
        raise _fail(400, "Файл не похож на бэкап настроек: в нём нет раздела «assets»")
    if not isinstance(assets, dict):
        raise _fail(
            400,
            f"Файл испорчен: раздел «assets» должен быть объектом с настройками, "
            f"а там {_kind(assets)}",
        )
    if not assets:
        raise _fail(400, "В файле нет ни одного раздела настроек — восстанавливать нечего")

    version = body.get("version")
    # `True` в python — тоже int, поэтому булево отсекаем отдельно: версия
    # «истина» это мусор, а не единица.
    if isinstance(version, bool) or (version is not None and not isinstance(version, int)):
        raise _fail(400, "Файл не похож на бэкап настроек: версия не число")
    if isinstance(version, int) and version > _VERSION:
        raise _fail(
            400,
            f"Бэкап сделан более новой версией (версия {version}, эта установка "
            f"понимает {_VERSION}) — обновите кабинет",
        )

    size = len(json.dumps(assets, ensure_ascii=False).encode("utf-8"))
    if size > _MAX_BUNDLE_BYTES:
        raise _fail(
            400,
            f"Файл слишком большой ({size // 1024} КБ): настройки кабинета занимают "
            "единицы килобайт, похоже, это не бэкап настроек",
        )

    plan, skipped, ignored = _plan(assets)
    if not plan:
        raise _fail(
            400,
            "В файле нет ни одного раздела, который хранит адаптер. Он отвечает "
            f"только за настройки кабинета: {_known_names()}. Остальное "
            "(оформление, меню, почта, тарифы) хранит сам бот — у него свои "
            "средства резервного копирования.",
        )

    # Снимок ДО первой записи: нужен, чтобы вернуть всё как было, если хоть один
    # раздел откажется. Снимаем только те строки, которые собираемся трогать.
    before = {section.key: state.get_setting(section.key, None) for section, _ in plan}
    restored: list[str] = []
    turned_on: list[str] = []
    current: _Section | None = None
    try:
        for section, fields in plan:
            current = section
            was = before.get(section.key)
            was_on = bool(was.get("enabled")) if isinstance(was, dict) else False
            after = await _apply(ctx, section, fields)
            if bool(after.get("enabled")) and not was_on:
                turned_on.append(section.title)
            restored.append(section.name)
    except BaseException as exc:  # noqa: BLE001 — откат обязан пережить и обрыв
        # BaseException, а не Exception: общий предел запроса (main.OWN_DEADLINE)
        # обрывает обработчик отменой, и она не Exception. Оставить после такого
        # обрыва половину применённых разделов — ровно то, чего мы избегаем.
        _rollback(state, before)
        raise _explain(current, exc)

    _note(ctx, restored, turned_on)
    return {
        "restored": restored,
        "skipped": skipped,
        "count": len(restored),
        # Сверх контракта экрана — для честности: что из файла мы намеренно не
        # применили (секреты, личный адресат сводки) и что включили.
        "ignored_fields": ignored,
        "turned_on": turned_on,
    }
