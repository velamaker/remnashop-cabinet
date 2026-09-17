"""Починка русских переводов бота — в памяти, без правки его файлов.

ЧТО ЧИНИМ в базовых переводах:

  • ЧИСЛА С РАЗДЕЛИТЕЛЯМИ ВНУТРИ ССЫЛОК. Fluent печатает `1 748 186 781`, и такой
    номер попадал прямо в `tg://user?id=` — тап по нику не открывал профиль. То же
    с адресом узла: `example.com:2 222` вместо `example.com:2222`;

  • ПУСТАЯ ПОЧТА. У людей, заведённых прямо в панели, в уведомлении печаталось
    бессмысленное «Почта: 0»;

  • те же две беды в событиях и сообщениях про пригласителя и узлы;

  • СМЕНА ТАРИФА. База пишет «заменена … без пересчета оставшегося срока», а у нас
    остаток переносится по цене дня (plan_change_carryover.py). Окно подтверждения
    выбирает текст по `$carry_state` — числа кладёт обёртка геттеров
    (plan_change_bot.py); вариант ПО УМОЛЧАНИЮ — исходный «без пересчета»: забытая
    переменная или выключенный перенос не соврут о переносе.

ПОЧЕМУ НЕ ЧЕРЕЗ custom.ftl. Оверрайд отдельным бандлом эти строки не перебивает:
фрагменты (`frg-*`) подставляются в события и сообщения на этапе КОМПИЛЯЦИИ внутри
своего же бандла, и наш файл до них не достаёт. Это проверялось — не работает.

ПОЧЕМУ НЕ ПРАВКОЙ ФАЙЛОВ. Раньше сборка меняла сами `.ftl` в образе бота. Теперь
файлы бота не трогаются вовсе: правка встраивается в ЕДИНСТВЕННОЕ место, где эти
тексты превращаются в бандл, — `Translator._make_translator` получает уже
исправленные строки, а на диске всё остаётся базовым.

FAIL-CLOSED. Каждая замена обязана найтись РОВНО ОДИН раз по всем текстам локали.
Не нашлась или нашлась дважды — правка не применяется целиком и кричит: значит
апстрим переписал перевод, и молча подменять его нельзя.
"""

from __future__ import annotations

from loguru import logger

from . import PatchTargetChanged

UTILS_USER_OLD = """frg-user =
    <blockquote>
    { $telegram_id ->
        [0] • <b>Почта</b>: <code>{ $email }</code>
        *[HAS] • <b>ID</b>: <code>{ NUMBER($telegram_id, useGrouping: 0) }</code>
    }
"""

UTILS_USER_NEW = """frg-user =
    <blockquote>
    { $telegram_id ->
        [0] { $email ->
            [0] { empty }
            *[HAS] • <b>Почта</b>: <code>{ $email }</code>
        }
        *[HAS] • <b>ID</b>: <code>{ NUMBER($telegram_id, useGrouping: 0) }</code>
    }
"""

UTILS_INFO_OLD = """frg-user-info =
    <blockquote>
    { $telegram_id ->
        [0] • <b>Почта</b>: <code>{ $email }</code>
        *[HAS] • <b>ID</b>: <code>{ NUMBER($telegram_id, useGrouping: 0) }</code>
    }
    • <b>Имя</b>: { $name } { $username ->
        [0] { empty }
        *[HAS] (<a href="tg://user?id={ $telegram_id }">@{ $username }</a>)
    }
"""

UTILS_INFO_NEW = """frg-user-info =
    <blockquote>
    { $telegram_id ->
        [0] { $email ->
            [0] { empty }
            *[HAS] • <b>Почта</b>: <code>{ $email }</code>
        }
        *[HAS] • <b>ID</b>: <code>{ NUMBER($telegram_id, useGrouping: 0) }</code>
    }
    • <b>Имя</b>: { $name } { $username ->
        [0] { empty }
        *[HAS] (<a href="tg://user?id={ NUMBER($telegram_id, useGrouping: 0) }">@{ $username }</a>)
    }
"""

UTILS_DETAILS_OLD = """frg-user-details =
    <blockquote>
    { $telegram_id ->
        [0] • <b>Почта</b>: <code>{ $email }</code>
        *[HAS] • <b>ID</b>: <code>{ NUMBER($telegram_id, useGrouping: 0) }</code>
    }
    • <b>Имя</b>: { $name } { $username ->
        [0] { space }
        *[HAS] (<a href="tg://user?id={ $telegram_id }">@{ $username }</a>)
    }
"""

UTILS_DETAILS_NEW = """frg-user-details =
    <blockquote>
    { $telegram_id ->
        [0] { $email ->
            [0] { empty }
            *[HAS] • <b>Почта</b>: <code>{ $email }</code>
        }
        *[HAS] • <b>ID</b>: <code>{ NUMBER($telegram_id, useGrouping: 0) }</code>
    }
    • <b>Имя</b>: { $name } { $username ->
        [0] { space }
        *[HAS] (<a href="tg://user?id={ NUMBER($telegram_id, useGrouping: 0) }">@{ $username }</a>)
    }
"""

UTILS_NODE_OLD = """    • <b>Адрес</b>: <code>{ $address }{ $port ->
    [0] { space }
    *[HAS] :{ $port }
    }</code>
"""

UTILS_NODE_NEW = """    • <b>Адрес</b>: <code>{ $address }{ $port ->
    [0] { space }
    *[HAS] :{ NUMBER($port, useGrouping: 0) }
    }</code>
"""

# --- events.ftl ------------------------------------------------------------

EVENTS_REFERRER_OLD = (
    '        *[HAS] (<a href="tg://user?id={ $referrer_telegram_id }">'
    "@{ $referrer_username }</a>)\n"
)

EVENTS_REFERRER_NEW = (
    '        *[HAS] (<a href="tg://user?id='
    '{ NUMBER($referrer_telegram_id, useGrouping: 0) }">@{ $referrer_username }</a>)\n'
)

# --- messages.ftl ----------------------------------------------------------

MESSAGES_TOP_REFERRER_OLD = (
    '                *[HAS] <a href="tg://user?id={ $top_referrer_telegram_id }">'
    "@{ $top_referrer_username }</a>\n"
)

MESSAGES_TOP_REFERRER_NEW = (
    '                *[HAS] <a href="tg://user?id='
    '{ NUMBER($top_referrer_telegram_id, useGrouping: 0) }">@{ $top_referrer_username }</a>\n'
)

MESSAGES_REFERRER_OLD = (
    '            *[HAS] <a href="tg://user?id={ $referrer_telegram_id }">'
    "@{ $referrer_username }</a>\n"
)

MESSAGES_REFERRER_NEW = (
    '            *[HAS] <a href="tg://user?id='
    '{ NUMBER($referrer_telegram_id, useGrouping: 0) }">@{ $referrer_username }</a>\n'
)

# Тот же дефект порта, но в карточках МЕНЮ бота (хост, узел, входящее
# подключение): int без NUMBER печатается как «8 443». В алертах это уже
# починено выше, а здесь оставалось — владелец попросил закрыть и тут.
MESSAGES_HOST_PORT_OLD = "    • <b>Адрес</b>: <code>{ $address }:{ $port }</code>\n"
MESSAGES_HOST_PORT_NEW = (
    "    • <b>Адрес</b>: <code>{ $address }:{ NUMBER($port, useGrouping: 0) }</code>\n"
)

MESSAGES_NODE_PORT_OLD = "    *[HAS]:{ $port }\n"
MESSAGES_NODE_PORT_NEW = "    *[HAS]:{ NUMBER($port, useGrouping: 0) }\n"

MESSAGES_INBOUND_PORT_OLD = "    *[HAS] • <b>Порт</b>: { $port }\n"
MESSAGES_INBOUND_PORT_NEW = (
    "    *[HAS] • <b>Порт</b>: { NUMBER($port, useGrouping: 0) }\n"
)

# --- смена тарифа: перенос остатка по цене дня ------------------------------
# «⚠️» в файлах базы — U+26A0 U+FE0F; пишем кодами, чтобы замена не разошлась с
# исходником из-за невидимого селектора варианта.
_WARN = "\u26a0\ufe0f"

PLAN_CHANGE_LINK_OLD = (
    f"    [CHANGE] <i>{_WARN} Текущая подписка будет <u>заменена</u> данным планом "
    "без пересчета оставшегося срока.</i>\n"
)

PLAN_CHANGE_LINK_NEW = (
    f"    [CHANGE] <i>{_WARN} Текущая подписка будет <u>заменена</u> данным планом. "
    "Что будет с оставшимися днями — покажем перед оплатой.</i>\n"
)

PLAN_CHANGE_CONFIRM_OLD = (
    f"    [CHANGE] <i>{_WARN} Текущая подписка будет <u>заменена</u> выбранной "
    "без пересчета оставшегося срока.</i>\n"
)

PLAN_CHANGE_CONFIRM_NEW = (
    "    [CHANGE] { $carry_state ->\n"
    f"        [CARRY] <i>{_WARN} Текущая подписка будет <u>заменена</u> выбранной. "
    "Остаток { NUMBER($carry_left, useGrouping: 0) } дн. пересчитаем по цене дня: к новому сроку "
    "добавится <b>{ NUMBER($carry_bonus, useGrouping: 0) } дн.</b> Дни считаются в момент оплаты.</i>\n"
    f"        [SMALL] <i>{_WARN} Текущая подписка будет <u>заменена</u> выбранной. "
    "Остаток { NUMBER($carry_left, useGrouping: 0) } дн. по цене дня меньше одного дня нового "
    "плана — к сроку ничего не добавится.</i>\n"
    f"        [LOST] <i>{_WARN} Текущая подписка будет <u>заменена</u> выбранной. "
    "К новому сроку добавится { NUMBER($carry_bonus, useGrouping: 0) } дн., а ещё "
    "{ NUMBER($carry_lost, useGrouping: 0) } дн. перенести нельзя — их цена неизвестна.</i>\n"
    f"        [LIFETIME] <i>{_WARN} Бессрочная подписка будет <u>заменена</u> подпиской "
    "на выбранный срок.</i>\n"
    f"        [NONE] <i>{_WARN} Текущая подписка будет <u>заменена</u> выбранной.</i>\n"
    f"       *[OFF] <i>{_WARN} Текущая подписка будет <u>заменена</u> выбранной "
    "без пересчета оставшегося срока.</i>\n"
    "    }\n"
)

# Якорь с именем ключа: `<b>{ $plan_name }</b>` в файле встречается не только здесь.
# После «=» в базе стоит пробел.
PLAN_CHANGE_SUCCESS_OLD = (
    "msg-subscription-change-success = \n"
    "    Ваша подписка была изменена.\n"
    "\n"
    "    <b>{ $plan_name }</b>\n"
)

PLAN_CHANGE_SUCCESS_NEW = (
    "msg-subscription-change-success = \n"
    "    Ваша подписка была изменена.\n"
    "\n"
    "    <b>{ $plan_name }</b>\n"
    "    { $carry_days ->\n"
    "        [0] { empty }\n"
    "       *[HAS] Остаток прежнего плана пересчитан: "
    "<b>+{ NUMBER($carry_days, useGrouping: 0) } дн.</b>\n"
    "    }\n"
)

# (файл, что заменяем, на что, короткое имя для лога)
PATCHES = [
    ("utils.ftl", UTILS_USER_OLD, UTILS_USER_NEW, "frg-user: пустая почта"),
    ("utils.ftl", UTILS_INFO_OLD, UTILS_INFO_NEW, "frg-user-info: пустая почта + ссылка"),
    ("utils.ftl", UTILS_DETAILS_OLD, UTILS_DETAILS_NEW, "frg-user-details: пустая почта + ссылка"),
    ("utils.ftl", UTILS_NODE_OLD, UTILS_NODE_NEW, "frg-node-info: порт без разделителей тысяч"),
    ("events.ftl", EVENTS_REFERRER_OLD, EVENTS_REFERRER_NEW, "event-user.registered: ссылка на пригласителя"),
    ("messages.ftl", MESSAGES_TOP_REFERRER_OLD, MESSAGES_TOP_REFERRER_NEW, "msg: ссылка на топ-реферрера"),
    ("messages.ftl", MESSAGES_REFERRER_OLD, MESSAGES_REFERRER_NEW, "msg: ссылка на пригласителя"),
    ("messages.ftl", MESSAGES_HOST_PORT_OLD, MESSAGES_HOST_PORT_NEW, "msg-remnawave-host: порт"),
    ("messages.ftl", MESSAGES_NODE_PORT_OLD, MESSAGES_NODE_PORT_NEW, "msg-remnawave-node: порт"),
    ("messages.ftl", MESSAGES_INBOUND_PORT_OLD, MESSAGES_INBOUND_PORT_NEW, "msg-remnawave-inbound: порт"),
    ("messages.ftl", PLAN_CHANGE_LINK_OLD, PLAN_CHANGE_LINK_NEW, "msg-subscription-plan: смена тарифа"),
    ("messages.ftl", PLAN_CHANGE_CONFIRM_OLD, PLAN_CHANGE_CONFIRM_NEW, "msg-subscription-confirm: перенос остатка"),
    ("messages.ftl", PLAN_CHANGE_SUCCESS_OLD, PLAN_CHANGE_SUCCESS_NEW, "msg-subscription-change-success: пересчёт"),
]

# Локаль, к которой относится таблица. Остальные не трогаем вовсе.
LOCALE = "ru"


def _fix(texts: list[str]) -> list[str]:
    """Применить замены к текстам локали — по факту совпадения, без исключений.

    ВАЖНО, почему тут НЕЛЬЗЯ падать. Переводы собираются ДВУМЯ слоями: базовым
    (`assets.default/translations/ru`) и пользовательским (`assets/translations/ru`,
    куда оператор кладёт свои строки). Для каждого слоя вызывается своя сборка
    бандла, и в пользовательском наших блоков нет и быть не должно. Требование
    «каждая замена обязана найтись» роняло бы построение переводчика на втором
    слое — а вместе с ним и весь старт бота.

    Fail-closed остаётся, но переехал в apply(): там один раз проверяется, что все
    блоки на месте в БАЗОВЫХ переводах. Здесь же просто применяем, что подошло.
    """
    out = list(texts)
    for _filename, old, new, _title in PATCHES:
        for i, text in enumerate(out):
            if old in text:
                out[i] = text.replace(old, new, 1)
                break
    return out


def _missing_in_defaults() -> list[str]:
    """Какие замены не находятся в базовых переводах (значит апстрим их менял)."""
    from pathlib import Path

    from src.core.constants import ASSETS_DEFAULT_DIR

    root = Path(ASSETS_DEFAULT_DIR) / "translations" / LOCALE
    cache: dict[str, str] = {}
    missing: list[str] = []
    for filename, old, new, title in PATCHES:
        path = root / filename
        if filename not in cache:
            try:
                cache[filename] = path.read_text("utf-8")
            except OSError as exc:
                missing.append(f"{title}: {path} не прочитан ({exc})")
                cache[filename] = ""
        text = cache[filename]
        if old not in text and new not in text:
            missing.append(title)
    return missing


def apply() -> str:
    import src.infrastructure.services.i18n as i18n

    storage = getattr(i18n, "LayeredFileStorage", None)
    if storage is None or not hasattr(storage, "_make_translator"):
        raise PatchTargetChanged(
            "в src/infrastructure/services/i18n.py нет LayeredFileStorage._make_translator — "
            "база сменила сборку переводов, починка чисел в ссылках и пустой почты пропадёт"
        )

    original = storage._make_translator
    if getattr(original, "_overlay_wrapped", False):
        return "уже включена"

    def _make_translator(self, locale: str, texts: list[str]):
        if str(locale).lower().startswith(LOCALE):
            texts = _fix(texts)
            logger.debug(f"Overlay: русские переводы поправлены в памяти ({len(PATCHES)} замен)")
        return original(self, locale, texts)

    # Fail-closed ЗДЕСЬ, один раз: все блоки обязаны найтись в БАЗОВЫХ переводах.
    # Не нашлись — апстрим переписал перевод, и молча подменять его нельзя.
    missing = _missing_in_defaults()
    if missing:
        raise PatchTargetChanged(
            "переводы базы изменились, замены не легли: " + "; ".join(missing)
        )

    _make_translator._overlay_wrapped = True  # type: ignore[attr-defined]
    storage._make_translator = _make_translator  # type: ignore[method-assign]
    return f"русские переводы правятся в памяти ({len(PATCHES)} замен), файлы бота не тронуты"
