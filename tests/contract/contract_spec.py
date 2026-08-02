"""Замороженный контракт кабинета в машинно-проверяемом виде.

Источник истины — `docs/CABINET-API-CONTRACT.md`. Здесь тот же контракт, но так,
чтобы его можно было прогнать против живого бэкенда: какие поля обязательны,
какого они типа и — главное — В КАКИХ ЕДИНИЦАХ.

ПОЧЕМУ единицы вынесены в отдельное свойство поля. Половина расхождений между
нашим бэкендом и адаптером поверх чужого бота — это не «нет поля», а «поле есть,
но значит другое»: у «Бедолаги» трафик в гигабайтах и деньги в копейках, у нас
трафик лимита в ГИГАБАЙТАХ, расход в БАЙТАХ, деньги в рублях. Такой ответ проходит
любую проверку «200 ли» и любую проверку типов, а кабинет показывает пользователю
лимит 100 ГБ как 100 миллиардов или цену 1249 ₽ как 124 900 ₽.

ЧТО СЧИТАТЬ ОБЯЗАТЕЛЬНЫМ. Обязательные (`req=True`) — только те поля, без которых
экран кабинета ломается или врёт. Остальные перечислены как необязательные, но
типы у них проверяются: бэкенд волен не отдавать `uptime_30d`, но если отдал —
это число, а не строка.

Правки при развитии кабинета: контракт заморожен, поэтому добавлять сюда поля
можно только вслед за `docs/CABINET-API-CONTRACT.md`, а не «потому что бэкенд
такое шлёт».
"""

from __future__ import annotations

# --- описание поля -----------------------------------------------------------

# Единицы измерения. Проверяются не «на глаз», а порогами, которые отличают
# ошибку в 1000/100 раз от нормального значения (см. checks в run_contract.py).
GB = "гигабайты"          # лимит трафика тарифа: 0 = безлимит
BYTES = "байты"           # расход трафика: целое число байт
RUB = "рубли"             # деньги числом
RUB_STR = "рубли строкой"  # деньги строкой ("1249", "104.08") — так их ждёт кабинет
ISO = "дата-время ISO"
DAY = "дата ГГГГ-ММ-ДД"
PCT = "проценты 0..100"


class F:
    """Ожидание по одному полю ответа."""

    __slots__ = ("kind", "req", "null", "unit", "enum", "items", "why")

    def __init__(
        self,
        kind: str,
        *,
        req: bool = True,
        null: bool = False,
        unit: str = "",
        enum: tuple[str, ...] = (),
        items: "dict[str, F] | F | None" = None,
        why: str = "",
    ) -> None:
        self.kind = kind      # str | int | num | bool | list | obj | any
        self.req = req        # обязательно ли присутствие ключа
        self.null = null      # допустим ли None
        self.unit = unit      # единица измерения (см. константы выше)
        self.enum = enum      # допустимый набор значений
        self.items = items    # описание элементов списка / полей объекта
        self.why = why        # зачем поле кабинету — попадает в текст ошибки


class Case:
    """Одна ручка контракта.

    `auth`:
        public — обязана работать без сессии (лендинг, страница цен, статус);
        user   — требует сессию, а без неё обязана отвечать 401.

    `nullable_body` — тело целиком может быть `null` (у `/subscription/current`
    это законное «подписки нет»), но ТОЛЬКО у вошедшего: гостю там 401.

    `feature` — ключ из `appearance.features`, к которому относится путь.
    Нужен, чтобы отличить «бэкенд честно не умеет раздел» от «умеет, но врёт»:
    признак `true` при ответе 501 — провал, потому что кабинет покажет раздел,
    который не работает.
    """

    __slots__ = ("path", "auth", "fields", "nullable_body", "feature", "why", "query")

    def __init__(
        self,
        path: str,
        auth: str,
        fields: dict[str, F],
        *,
        nullable_body: bool = False,
        feature: str = "",
        why: str = "",
        query: str = "",
    ) -> None:
        self.path = path
        self.auth = auth
        self.fields = fields
        self.nullable_body = nullable_body
        self.feature = feature
        self.why = why
        self.query = query

    @property
    def url(self) -> str:
        return self.path + (("?" + self.query) if self.query else "")


# --- переиспользуемые куски --------------------------------------------------

# Нода в списках серверов/статуса. `host` отдаётся только вошедшему (для замера
# пинга из браузера), в публичном `/status` его быть не должно — это утечка адреса.
NODE = {
    "name": F("str", why="подпись сервера в карточке"),
    "country_code": F("str", why="флаг страны"),
    "online": F("bool", why="зелёная/красная точка"),
    "uptime_30d": F("num", req=False, null=True, unit=PCT),
    "history": F("list", req=False, null=True, items={
        "date": F("str", unit=DAY),
        "uptime": F("num", unit=PCT),
    }),
}

# Цена одного срока у одного шлюза. Деньги СТРОКОЙ — так объявлено в
# cabinet/src/types/api.ts (PlanPrice), кабинет их не считает, а печатает.
PRICE = {
    "gateway_type": F("str"),
    "currency": F("str"),
    "currency_symbol": F("str", req=False, null=True),
    "original_amount": F("str", unit=RUB_STR),
    "discount_percent": F("num", null=True, unit=PCT),
    "final_amount": F("str", unit=RUB_STR, why="итоговая цена кнопки покупки"),
    "is_free": F("bool", req=False, null=True),
}


# --- сам контракт ------------------------------------------------------------

CASES: list[Case] = [
    # ----- без входа ---------------------------------------------------------
    Case(
        "/appearance", "public",
        why="грузится ДО первой отрисовки: без него кабинет не откроется вовсе",
        fields={
            "brand_name": F("str", why="имя сервиса во всех заголовках"),
            "accent": F("str", req=False, null=True),
            "background": F("str", req=False, null=True),
            "background_dark": F("str", req=False, null=True),
            "background_light": F("str", req=False, null=True),
            "support_username": F("str", req=False, null=True),
            "logo_url": F("str", req=False, null=True),
            "telegram_oidc_enabled": F("bool", why="показывать ли кнопку входа через Telegram"),
            "sub_link_enabled": F("bool", why="показывать ли ссылку подписки"),
            "maintenance": F("bool", why="заглушка тех-работ"),
            "maintenance_message": F("str", req=False, null=True),
            "maintenance_block_login": F("bool", req=False, null=True),
            "maintenance_block_registration": F("bool", req=False, null=True),
            "maintenance_block_payments": F("bool", req=False, null=True),
            "enabled_languages": F("list", req=False, null=True),
            # Списка возможностей у нашего бэкенда нет (он умеет всё) — поле
            # необязательное, но если есть, то это словарь флагов.
            "features": F("obj", req=False, null=True),
        },
    ),
    Case(
        "/plans/public", "public",
        why="витрина лендинга и страницы «Тарифы» для гостя",
        feature="plans_public",
        fields={
            "plans": F("list", items={
                "public_code": F("str", why="код тарифа для ссылки на покупку"),
                "name": F("str"),
                "description": F("str", req=False, null=True),
                "traffic_limit": F("num", unit=GB, why="печатается как «N ГБ»"),
                "device_limit": F("int"),
                "monthly_from_rub": F("str", unit=RUB_STR, why="«от N ₽ в месяц»"),
                "max_duration_days": F("int"),
                "max_duration_price_rub": F("str", unit=RUB_STR),
            }),
        },
    ),
    Case(
        "/status", "public",
        why="публичная страница «Статус» и блок на лендинге",
        fields={
            "nodes": F("list", items=NODE),
            "all_operational": F("bool"),
            "total": F("int"),
            "online": F("int"),
        },
    ),
    Case(
        "/info", "public",
        why="страница «Информация»: FAQ, правила, оферта, политика",
        feature="info_pages",
        fields={
            "faq": F("list", null=True, items={"q": F("str"), "a": F("str")}),
            "rules": F("str", req=False, null=True),
            "privacy": F("str", req=False, null=True),
            "offer": F("str", req=False, null=True),
            "statuses": F("str", req=False, null=True),
        },
    ),
    Case(
        "/apps", "public",
        why="мастер подключения: какие приложения показывать",
        fields={
            "priority": F("str", req=False, null=True),
            "enabled": F("list", req=False, null=True),
            "custom": F("list", req=False, null=True),
        },
    ),

    # ----- требуют сессии ----------------------------------------------------
    Case(
        "/auth/me", "user",
        why="AuthContext на старте; 401 = гость, любой другой ответ ломает вход",
        fields={
            "telegram_id": F("int", null=True),
            "auth_type": F("str", enum=("telegram", "email", "google", "yandex", "vk")),
            "email": F("str", null=True),
            "is_email_verified": F("bool"),
            "pending_email": F("str", req=False, null=True),
            "name": F("str", null=True),
            "username": F("str", req=False, null=True),
            "language": F("str", req=False, null=True),
            "role": F("int", req=False, null=True),
        },
    ),
    Case(
        "/auth/whoami", "user",
        why="права админки; при ошибке кабинет гасит всё (fail-closed)",
        fields={
            "role": F("int", null=True),
            "is_admin": F("bool"),
            "is_readonly_admin": F("bool", req=False, null=True),
            "can_access_admin": F("bool"),
            "is_owner": F("bool", req=False, null=True),
            "full_access": F("bool", req=False, null=True),
            "can_write": F("bool", req=False, null=True),
            "sections": F("list", req=False, null=True),
            "grant_expires_at": F("str", req=False, null=True, unit=ISO),
            "has_password": F("bool", req=False, null=True),
        },
    ),
    Case(
        "/subscription/current", "user", nullable_body=True, feature="subscription",
        why="центральная сущность кабинета: статус, срок, лимиты, ссылка подписки",
        fields={
            "user_remna_id": F("str", null=True),
            "status": F("str", enum=("ACTIVE", "EXPIRED", "DISABLED", "LIMITED", "EXPIRED_GRACE")),
            "is_trial": F("bool"),
            # ГИГАБАЙТЫ. Кабинет сам переводит в байты (cabinet/src/lib/format.ts):
            # если бэкенд пришлёт байты, пользователь увидит терабайты вместо
            # гигабайт. Это ровно та грабля, из-за которой правился адаптер.
            "traffic_limit": F("num", unit=GB, why="лимит тарифа, 0 = безлимит"),
            "device_limit": F("int"),
            "traffic_limit_strategy": F("str", req=False, null=True),
            "expire_at": F("str", null=True, unit=ISO),
            "url": F("str", null=True, why="ссылка подписки для QR и приложений"),
            "plan_name": F("str", null=True),
            "plan_duration_days": F("int", req=False, null=True),
            # А вот расход — В БАЙТАХ. Единицы у соседних полей РАЗНЫЕ, это не
            # опечатка в контракте: так исторически отдаёт наш бэкенд.
            "used_traffic_bytes": F("int", null=True, unit=BYTES),
            "lifetime_used_traffic_bytes": F("int", req=False, null=True, unit=BYTES),
            "online_at": F("str", req=False, null=True, unit=ISO),
            "frozen": F("bool", req=False, null=True, why="пауза, поставленная пользователем"),
        },
    ),
    Case(
        "/subscription/trial-info", "user", feature="trial",
        why="кнопка «попробовать бесплатно» на главной",
        fields={
            "available": F("bool"),
            "days": F("int", null=True),
            "traffic_gb": F("num", null=True, unit=GB),
            "devices": F("int", null=True),
        },
    ),
    Case(
        "/subscription/devices", "user",
        why="страница «Устройства» и мастер диагностики",
        fields={
            "devices": F("list", items={
                "hwid": F("str", why="ключ для отключения устройства"),
                "platform": F("str", req=False, null=True),
                "device_model": F("str", req=False, null=True),
                "os_version": F("str", req=False, null=True),
                "user_agent": F("str", req=False, null=True),
                "created_at": F("str", req=False, null=True, unit=ISO),
                "updated_at": F("str", req=False, null=True, unit=ISO),
            }),
            "current_count": F("int"),
            "max_count": F("int"),
        },
    ),
    Case(
        "/subscription/servers", "user",
        why="карточка «Серверы»: список нод пользователя с адресом для пинга",
        fields={
            "nodes": F("list", items=dict(NODE, host=F("str", req=False, null=True))),
            "all_operational": F("bool"),
            "total": F("int"),
            "online": F("int"),
            "enabled": F("bool", req=False, null=True),
        },
    ),
    Case(
        "/subscription/service-status", "user",
        why="шаг «всё ли работает» в мастере диагностики",
        fields={
            "nodes": F("list", items={
                "name": F("str"),
                "country_code": F("str", req=False, null=True),
                "online": F("bool"),
            }),
            "all_operational": F("bool"),
        },
    ),
    Case(
        "/subscription/server-stats", "user",
        why="блок «любимый сервер» на главной",
        fields={
            "favorite": F("obj", null=True, items={
                "name": F("str"),
                "country_code": F("str", req=False, null=True),
                "total": F("num", unit=BYTES),
            }),
            "nodes": F("list", items={
                "name": F("str"),
                "country_code": F("str", req=False, null=True),
                "total": F("num", unit=BYTES),
            }),
        },
    ),
    Case(
        "/subscription/traffic-history", "user",
        why="график расхода трафика по дням",
        fields={
            "days": F("list", items={
                "date": F("str", unit=DAY),
                "total": F("num", unit=BYTES),
            }),
        },
    ),
    Case(
        "/subscription/freeze-status", "user", feature="freeze",
        why="карточка паузы подписки",
        fields={
            "enabled": F("bool"),
            "frozen": F("bool"),
            "can_freeze": F("bool", req=False, null=True),
            # Осталось дней паузы — осмысленно только когда пауза уже стоит,
            # поэтому необязательное (в кабинете тип тоже опциональный).
            "remaining_days": F("int", req=False, null=True),
            "max_days": F("int", req=False, null=True),
            "days_left": F("int", req=False, null=True),
        },
    ),
    Case(
        "/subscription/offers", "user", feature="purchase",
        why="витрина покупки и продления — без неё нельзя купить подписку",
        fields={
            "gateways": F("list", items={
                "gateway_type": F("str"),
                "currency": F("str"),
                "currency_symbol": F("str", req=False, null=True),
            }),
            "plans": F("list", items={
                "id": F("any", req=False, null=True),
                "public_code": F("str"),
                "name": F("str"),
                "description": F("str", req=False, null=True),
                "traffic_limit": F("num", unit=GB),
                "device_limit": F("int"),
                "type": F("str", req=False, null=True),
                "recommended_purchase_type": F("str", req=False, null=True),
                "durations": F("list", items={
                    "days": F("int"),
                    "prices": F("list", items=PRICE),
                }),
            }),
        },
    ),
    Case(
        "/balance", "user",
        why="кошелёк и баллы в кабинете",
        fields={
            "balance": F("num", unit=RUB, why="остаток кошелька"),
            "points": F("num", req=False, null=True),
            "point_value_rub": F("num", req=False, null=True, unit=RUB),
            "total_spent": F("num", req=False, null=True, unit=RUB),
            "total_purchases": F("int", req=False, null=True),
            "autopay_enabled": F("bool", req=False, null=True),
        },
    ),
    Case(
        "/balance/transactions", "user", query="limit=5", feature="transactions",
        why="история платежей",
        fields={
            "total": F("int"),
            "limit": F("int", req=False, null=True),
            "offset": F("int", req=False, null=True),
            "items": F("list", items={
                "payment_id": F("any"),
                "status": F("str"),
                "gateway_type": F("str", req=False, null=True),
                "gateway_display_name": F("str", req=False, null=True),
                "purchase_type": F("str", req=False, null=True),
                "plan_name": F("str", req=False, null=True),
                "original_amount": F("any", req=False, null=True, unit=RUB),
                "discount_percent": F("num", req=False, null=True, unit=PCT),
                "final_amount": F("any", unit=RUB, why="сумма в списке платежей"),
                "currency": F("str", req=False, null=True),
                "created_at": F("str", unit=ISO),
            }),
        },
    ),
    Case(
        "/balance/topup/config", "user", feature="topup",
        why="форма пополнения баланса: лимиты, бонус, шлюзы",
        fields={
            "enabled": F("bool"),
            "bonus_percent": F("num", req=False, null=True, unit=PCT),
            "min_amount": F("num", req=False, null=True, unit=RUB),
            "max_amount": F("num", req=False, null=True, unit=RUB),
            "presets": F("list", req=False, null=True),
            "gateways": F("list", req=False, null=True, items={
                "gateway_type": F("str"),
                "name": F("str", req=False, null=True),
                "currency_symbol": F("str", req=False, null=True),
            }),
        },
    ),
    Case(
        "/referral/program", "user", feature="referral",
        why="реферальная программа: код ссылки и счётчики",
        fields={
            "enabled": F("bool"),
            "referral_code": F("str", null=True, why="из него собирается ссылка приглашения"),
            "invited_count": F("int", req=False, null=True),
            "invited_with_payment_count": F("int", req=False, null=True),
            "reward_type": F("str", req=False, null=True),
            "reward_strategy": F("str", req=False, null=True),
            "accrual_strategy": F("str", req=False, null=True),
            "max_level": F("int", req=False, null=True),
            "reward_levels": F("list", req=False, null=True, items={
                "level": F("int"),
                "value": F("num"),
            }),
        },
    ),
    Case(
        "/referral/earnings", "user", feature="referral",
        why="«заработано по рефералке» на главной",
        fields={
            "earned": F("num", unit=RUB),
            "rewards_count": F("int", req=False, null=True),
        },
    ),
    Case(
        "/promo-banner", "user",
        why="промо-баннер в кабинете",
        fields={
            "active": F("bool"),
            "title": F("str", req=False, null=True),
            "text": F("str", req=False, null=True),
            "cta_text": F("str", req=False, null=True),
            "cta_url": F("str", req=False, null=True),
            "dismissible": F("bool", req=False, null=True),
        },
    ),
    Case(
        "/trial-discount", "user",
        why="баннер персональной скидки с таймером",
        fields={
            "active": F("bool"),
            "percent": F("num", req=False, null=True, unit=PCT),
            "expires_at": F("str", req=False, null=True, unit=ISO),
        },
    ),
    Case(
        "/notifications", "user", query="limit=20", feature="notifications",
        why="колокольчик: список уведомлений",
        fields={
            "unread": F("int"),
            "items": F("list", items={
                "id": F("any"),
                "title": F("str", null=True),
                "body": F("str", req=False, null=True),
                "url": F("str", req=False, null=True),
                "is_read": F("bool", req=False, null=True),
                "created_at": F("str", unit=ISO),
            }),
        },
    ),
    Case(
        "/notifications/unread-count", "user", feature="notifications",
        why="бейдж на колокольчике",
        fields={"unread": F("int")},
    ),
    Case(
        "/support/tickets", "user", feature="tickets",
        why="список обращений в поддержку",
        fields={
            "items": F("list", items={
                "id": F("any"),
                "subject": F("str", null=True),
                "status": F("str", enum=("open", "answered", "closed")),
                "created_at": F("str", unit=ISO),
                "updated_at": F("str", req=False, null=True, unit=ISO),
                "messages_count": F("int", req=False, null=True),
            }),
        },
    ),
    Case(
        "/sessions", "user", feature="sessions",
        why="карточка безопасности: история входов",
        fields={
            "items": F("list", items={
                "ip": F("str", req=False, null=True),
                "user_agent": F("str", req=False, null=True),
                "method": F("str", req=False, null=True),
                "created_at": F("str", unit=ISO),
            }),
        },
    ),
    Case(
        "/push/status", "user", feature="push",
        why="раздел «Уведомления»: включён ли web-push",
        fields={
            "enabled": F("bool"),
            "devices": F("int", req=False, null=True),
        },
    ),
    Case(
        "/gift/my", "user", feature="gift",
        why="история купленных подарков",
        fields={
            "items": F("list", items={
                "payment_id": F("any", req=False, null=True),
                "plan_name": F("str", req=False, null=True),
                "duration_days": F("int", req=False, null=True),
                "price": F("any", req=False, null=True, unit=RUB),
                "code": F("str", req=False, null=True),
                "issued": F("bool", req=False, null=True),
                "created_at": F("str", req=False, null=True, unit=ISO),
            }),
        },
    ),
]
