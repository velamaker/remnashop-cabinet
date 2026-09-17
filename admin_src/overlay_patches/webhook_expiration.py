"""Напоминания «подписка заканчивается» на панели 3.x.

ЧТО СЛОМАЛОСЬ. Панель 2.x сама слала `user.expires_in_72/48/24_hours` и
`user.expired_24_hours_ago`, как только был включён вебхук. В 3.x этих событий
нет: вместо них ОДНО `user.expiration` с `meta.expiration` в часах (−72 — за трое
суток до конца, +24 — через сутки после), и шлёт его планировщик панели только при
`EXPIRATION_NOTIFICATIONS_ENABLED`. Бот ждёт старые имена, событие нового имени
уходит в «Unhandled user event» — и с 24.08 напоминаний в Telegram не получал
никто, без единой ошибки в логе.

ПОЧЕМУ ПЕРЕВОД В РАЗБОРЕ, А НЕ В ОБРАБОТЧИКЕ. remnapy при разборе ВЫБРАСЫВАЕТ
`meta` (у `WebhookPayloadDto` такого поля нет), и до обработчика интервал уже не
доходит. Узнать его можно только в `parse_webhook`. Переводим имя ПОСЛЕ того, как
оригинал проверил подпись по исходной строке тела: тело мы не меняем, поддельное
событие оригинал отбросит раньше, чем мы на него посмотрим. Дальше работает код
базы без единой правки — проверка «уже продлил», день в тексте, клавиатура
«Продлить»/«Купить», тумблер уведомления.

КОМУ НЕ СЛАТЬ. Панель выбирает получателей по одному `expireAt`, статус не
смотрит (и в 2.x не смотрела). А у нас срок двигают две вещи, которые подпиской
не являются:
  • резерв — у человека на резерве `expireAt` означает конец бесплатной страховки,
    и «продлите, осталось 3 дня» про неё только сбивает с толку (то же правило, что
    с 09.09 у web-push и писем); о конце резерва предупреждает сам резерв;
  • пауза — подписка отключена в панели, а срок в ней продолжает тикать.
Плюс защита от дублей: панель повторяет вебхук после 503, а её минутная задача
при занятой очереди может отработать одно окно дважды.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger
from sqlalchemy import text

from . import PatchTargetChanged, expect_source
from .webhook_v3 import _fill_uuid

PANEL_EVENT = "user.expiration"

# Часы из meta.expiration → старое имя события, которое понимает база. Набор ровно
# тот, что панель 2.x слала сама; другие часы базе нечем озвучить (текстов нет).
LEGACY_EVENT_BY_OFFSET: dict[int, str] = {
    -72: "user.expires_in_72_hours",
    -48: "user.expires_in_48_hours",
    -24: "user.expires_in_24_hours",
    24: "user.expired_24_hours_ago",
}

# Что должно стоять в .env панели. Панель требует массив строго по возрастанию и
# без дублей — иначе не стартует, поэтому строку собираем из карты, а не пишем руками.
PANEL_ENV_HINT = (
    "EXPIRATION_NOTIFICATIONS_ENABLED=true и EXPIRATION_NOTIFICATIONS="
    + json.dumps(sorted(LEGACY_EVENT_BY_OFFSET), separators=(",", ":"))
)

BEFORE = frozenset(name for hours, name in LEGACY_EVENT_BY_OFFSET.items() if hours < 0)
AFTER = frozenset(name for hours, name in LEGACY_EVENT_BY_OFFSET.items() if hours > 0)

# sha256 кода базы v0.8.2 (remnapy 2.7.x), за который мы держимся. Сверяем даже то,
# что не заменяем: перевод имён опирается на то, что подпись проверяется ДО разбора,
# а `payload.event` уходит в `handle_user_event` как есть.
SHA_PARSE = "d67ba6037c49acae747600437274932a4a74f0f449d35d93c2647800f66b7cf5"
SHA_ENDPOINT = "cad3566744a64eb95655609c533f8610d34aba3bfd1f04921b3c4a8bd56233bc"
SHA_HANDLER = {
    "RemnaWebhookService.handle_user_event": (
        "da2fcfb749ff3de797b664b8e3e460c1a443bea9019e46eef61340e691df047d"
    ),
    "RemnaWebhookService._process_expiring": (
        "4fc7d588e3f69407381dabb18ab72384e4aa937bf63816e67a04aa97045e55f5"
    ),
}

# Повтор вебхука панель делает через секунды, дубль минутной задачи — через минуты.
# Шесть часов с запасом накрывают оба и не мешают следующему окну (оно через сутки).
DEDUPE_TTL = 6 * 3600

# Резерв ставит в панель ровно тот срок, что пишет в reserve_grants; допуск нужен
# только на округление панелью и на то, что запись в базу идёт вторым шагом.
RESERVE_ENDED_TOLERANCE_H = 2

GUARD_SQL = text(
    "SELECT "
    " EXISTS (SELECT 1 FROM reserve_grants r WHERE r.user_id = :u AND r.ended = false)"
    "   AS on_reserve, "
    " EXISTS (SELECT 1 FROM reserve_grants r WHERE r.user_id = :u "
    "   AND r.reserve_expire_at BETWEEN "
    f"     CAST(:exp AS timestamptz) - interval '{RESERVE_ENDED_TOLERANCE_H} hours' "
    f" AND CAST(:exp AS timestamptz) + interval '{RESERVE_ENDED_TOLERANCE_H} hours')"
    "   AS reserve_ran_out, "
    " EXISTS (SELECT 1 FROM subscription_freezes f WHERE f.user_id = :u AND f.active = true)"
    "   AS frozen"
)

# Статусы панели, при которых напоминание имеет смысл. DISABLED — это пауза, ручное
# отключение админом или выход из канала: «продлите» там не по делу. LIMITED со
# сроком впереди — по делу: продление заодно сбросит трафик.
_STATUS_BEFORE = frozenset({"ACTIVE", "LIMITED"})
_STATUS_AFTER = frozenset({"EXPIRED"})


def legacy_event(body: Any) -> tuple[str | None, Any]:
    """(старое имя или None, сырое meta.expiration) — по телу вебхука."""
    try:
        data = json.loads(body) if isinstance(body, (str, bytes)) else body
        meta = (data or {}).get("meta") or {}
        raw = meta.get("expiration")
    except Exception:  # noqa: BLE001 — тело уже прошло подпись; кривое meta не повод ронять вебхук
        return None, None
    # bool — подкласс int: `true` в meta не должно превратиться в «через час».
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None, raw
    return LEGACY_EVENT_BY_OFFSET.get(raw), raw


def apply_parse() -> str:
    """Перевести `user.expiration` в старое имя — после проверки подписи оригиналом."""
    import remnapy.controllers.webhooks as target

    expect_source(target, "WebhookUtility.parse_webhook", SHA_PARSE, "разбор вебхука панели")
    cls = target.WebhookUtility
    current = cls.__dict__.get("parse_webhook")
    if not isinstance(current, staticmethod):
        raise PatchTargetChanged(
            "WebhookUtility.parse_webhook больше не staticmethod — перевод "
            "user.expiration не встанет, напоминания об окончании не придут"
        )
    if getattr(current.__func__, "_overlay_expiration", False):
        return "уже применено"
    original = current.__func__

    def parse_webhook(body, headers, webhook_secret, validate=True):  # noqa: ANN001, ANN202
        # Подпись проверяет ОРИГИНАЛ по нетронутому телу; None — подделка или сбой.
        payload = original(body, headers, webhook_secret, validate)
        if payload is None or getattr(payload, "event", None) != PANEL_EVENT:
            return payload
        name, raw = legacy_event(body)
        if name is None:
            logger.warning(
                f"Overlay: user.expiration с meta.expiration={raw!r} — бот умеет только "
                f"{sorted(LEGACY_EVENT_BY_OFFSET)}; в панели должно быть {PANEL_ENV_HINT}"
            )
            return payload
        payload.event = name
        logger.info(f"Overlay: user.expiration ({raw:+d} ч) → {name}")
        return payload

    parse_webhook._overlay_expiration = True  # type: ignore[attr-defined]
    cls.parse_webhook = staticmethod(parse_webhook)
    return "user.expiration → user.expires_in_72/48/24_hours, user.expired_24_hours_ago"


def check_endpoint() -> str:
    """Эндпоинт не меняем, но перевод имён верен, только пока он устроен как сейчас."""
    import src.web.endpoints.remnawave as target

    expect_source(target, "_process_remnawave_webhook", SHA_ENDPOINT, "эндпоинт вебхука панели")
    return "эндпоинт сверен: подпись до разбора, payload.event → handle_user_event"


def apply_guard() -> str:
    """Не слать напоминание тем, у кого срок означает не подписку, и не слать дважды."""
    import src.application.services.remnawave as target

    for qualname, sha in SHA_HANDLER.items():
        expect_source(target, qualname, sha, qualname)

    events = target.RemnaUserEvent
    base = {
        events.EXPIRES_IN_72_HOURS.value,
        events.EXPIRES_IN_48_HOURS.value,
        events.EXPIRES_IN_24_HOURS.value,
        events.EXPIRED_24_HOURS_AGO.value,
    }
    if base != set(LEGACY_EVENT_BY_OFFSET.values()):
        raise PatchTargetChanged(f"база переименовала события истечения: {sorted(base)}")

    cls = target.RemnaWebhookService
    # Флаг на КЛАССЕ и со своим именем: обработчик оборачивает и webhook_v3 (флаг
    # `_overlay_wrapped` на функции), и кто окажется снаружи — зависит от порядка
    # хуков. По флагу на функции повторный вызов не узнал бы нас под чужой обёрткой.
    if cls.__dict__.get("_overlay_expiry_guard", False):
        return "уже применено"
    original = cls.handle_user_event

    async def handle_user_event(self, event, remna_user, *args, **kwargs):  # noqa: ANN001, ANN202
        if event not in BEFORE and event not in AFTER:
            return await original(self, event, remna_user, *args, **kwargs)
        # Фильтру нужен наш пользователь, а его ищут по uuid. Если webhook_v3 стоит
        # внутри нас, он ещё не успел его дописать — дописываем сами (он не повторит).
        if getattr(remna_user, "uuid", None) is None:
            await _fill_uuid(remna_user, "handle_user_event")
        reason, claim = await _decide(self, str(event), remna_user)
        if reason:
            logger.info(
                f"Overlay: напоминание {event} для panel_id={getattr(remna_user, 'id', '?')} "
                f"не шлём — {reason}"
            )
            return None
        try:
            return await original(self, event, remna_user, *args, **kwargs)
        except BaseException:
            # Обработчик упал — эндпоинт ответит 503 и панель повторит. Снимаем метку,
            # иначе повтор сочли бы дублем и человек не получил бы напоминание вовсе.
            if claim:
                try:
                    await self.redis.delete(claim)
                except Exception:  # noqa: BLE001
                    pass
            raise

    cls.handle_user_event = handle_user_event
    cls._overlay_expiry_guard = True
    return "без резерва, паузы и отключённых, без дублей"


def _status(remna_user: Any) -> str:
    status = getattr(remna_user, "status", None)
    return str(getattr(status, "value", status) or "")


async def _decide(self: Any, event: str, remna_user: Any) -> tuple[str | None, str | None]:
    """(причина не слать или None, ключ метки «уже отправляли» или None)."""
    status = _status(remna_user)
    if event in BEFORE and status not in _STATUS_BEFORE:
        return f"статус в панели {status}", None
    if event in AFTER and status not in _STATUS_AFTER:
        return f"статус в панели {status}", None

    try:
        user = await self.user_dao.get_by_remna_uuid(remna_user.uuid)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Overlay: фильтр напоминаний не нашёл пользователя ({exc}) — решает база")
        return None, None
    if user is None:
        return None, None  # база сама напишет «Local user not found»

    expire_at = getattr(remna_user, "expire_at", None)
    row = None
    try:
        session = self.user_dao.session
        # Точка сохранения: ошибка запроса (например, таблицы ещё нет) иначе отравила
        # бы транзакцию, в которой база дальше читает подписку.
        async with session.begin_nested():
            result = await session.execute(GUARD_SQL, {"u": user.id, "exp": expire_at})
            row = result.one()
    except Exception as exc:  # noqa: BLE001
        # Шлём без фильтра: молчание — та самая поломка, которую мы чиним, а резервистов
        # единицы, и паузу всё равно отсекает статус панели.
        logger.error(f"Overlay: фильтр напоминаний (резерв/пауза) не сработал: {exc} — отправляем без него")
    if row is not None:
        if row.frozen:
            return "подписка на паузе", None
        if event in BEFORE and row.on_reserve:
            return "на резервном доступе: срок — это срок резерва, о нём предупреждает резерв", None
        if event in AFTER and row.reserve_ran_out:
            return "закончился резерв, а не подписка («истекла 1 день назад» было бы неправдой)", None

    stamp = int(expire_at.timestamp()) if expire_at is not None else 0
    key = f"overlay:expiry_reminder:{user.id}:{event}:{stamp}"
    try:
        if not await self.redis.set(key, "1", ex=DEDUPE_TTL, nx=True):
            return "уже отправляли (повтор вебхука или дубль задачи панели)", None
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Overlay: защита от дублей напоминаний недоступна ({exc}) — отправляем")
        return None, None
    return None, key
