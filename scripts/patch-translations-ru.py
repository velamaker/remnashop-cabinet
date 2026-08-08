"""Патчи русских переводов базового образа (assets.default/translations/ru).

Одна и та же беда — Fluent по умолчанию печатает числа С РАЗДЕЛИТЕЛЯМИ ТЫСЯЧ — плюс
пустая почта. Три места, все видны админу в уведомлениях и в карточках админки:

1. Ссылка на профиль. В базе стоит href="tg://user?id={ $telegram_id }", и разделители
   попадают прямо внутрь href: получается tg://user?id=123 456 789 — тап по @нику
   профиль не открывает. Рядом, где ID показывается человеку, NUMBER(useGrouping: 0)
   уже стоит — в ссылке его забыли. Места: utils.ftl (frg-user-info, frg-user-details),
   events.ftl (блок «🤝 Пригласитель»), messages.ftl (топ-реферрер и «Приглашен»).

2. Строка «Почта: 0». Ветка [0] у $telegram_id означает «телеграма нет» и печатает
   почту, но у пользователей, заведённых прямо в панели, почты тоже нет: транслятор
   превращает None в False, и Fluent выводит его как «0». Ставим вложенный селектор
   по $email (тот же приём, что уже используется в messages.ftl:514) — пустая почта
   просто не печатается. Места: utils.ftl (frg-user, frg-user-info, frg-user-details).

3. Порт узла. Те же разделители, но в адресе ноды: frg-node-info печатает { $port }
   без NUMBER, а port — int, поэтому у любого порта >= 1000 в алертах «связь с узлом
   потеряна/восстановлена» и «узел достиг лимита трафика» адрес выглядит как
   «example.com:2 222» (порт 443 не задет). Место: utils.ftl (frg-node-info).
   Те же { $port } без NUMBER остались в messages.ftl (msg-remnawave-host-details,
   msg-remnawave-node-details, msg-remnawave-inbound-details) — это карточки в меню
   бота, не уведомления; сюда НЕ включены, чинить отдельным решением.

ПОЧЕМУ ЗДЕСЬ, А НЕ В assets/translations/ru/custom.ftl: custom.ftl компилируется в
ОТДЕЛЬНЫЙ бандл (см. src/infrastructure/services/i18n.py::LayeredFileStorage), а
fluent_compiler резолвит ссылки вида { frg-user-info } на этапе компиляции ВНУТРИ
бандла ссылающегося сообщения. Все event-* объявлены в базовом events.ftl, поэтому
подставляется базовый фрагмент, а оверрайд выигрывает только при ПРЯМОМ запросе
ключа frg-user-info — а такого вызова в коде нет. Оверрайдом фрагмент не чинится.

Запускается на СБОРКЕ образа (Dockerfile) — правки живут в образе и переживают
пересборку. Идемпотентно (повторный запуск — no-op); fail-closed: если исходный
блок не найден (база сменила тексты) — билд падает, чтобы правка не отвалилась молча.

Локальная проверка на копиях файлов: python3 scripts/patch-translations-ru.py <каталог>
"""

import sys
from pathlib import Path

DEFAULT_ROOT = Path("/opt/remnashop/assets.default/translations/ru")

# --- utils.ftl -------------------------------------------------------------

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
]


def apply(root: Path, filename: str, old: str, new: str, title: str) -> None:
    path = root / filename
    src = path.read_text(encoding="utf-8")

    if new in src:
        print(f"  ~ {filename} [{title}]: уже применён — пропускаю")
        return

    count = src.count(old)
    if count != 1:
        sys.exit(
            f"ftl patch: в {path} ожидался ровно 1 исходный блок [{title}], найдено {count} — "
            f"базовые переводы изменились, проверь патч"
        )

    path.write_text(src.replace(old, new, 1), encoding="utf-8")
    print(f"  ✓ {filename} [{title}]: применён")


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ROOT
    if not root.is_dir():
        sys.exit(f"ftl patch: каталог переводов не найден: {root}")

    for filename, old, new, title in PATCHES:
        apply(root, filename, old, new, title)


if __name__ == "__main__":
    main()
