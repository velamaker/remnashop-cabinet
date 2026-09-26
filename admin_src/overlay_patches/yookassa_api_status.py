"""ЮKassa: оплату засчитываем по слову самой ЮKassa, а не по тексту вебхука.

ЧТО БЫЛО. `YookassaGateway.handle_webhook` берёт статус прямо из тела уведомления,
а достоверность его проверяет только по адресу отправителя. Подписи у вебхуков
ЮKassa нет, а адрес база читает из заголовков запроса (`base._get_ip`), которым
нельзя доверять без настроенного прокси. Значит, одного этого рубежа мало: тело
уведомления нельзя считать доказательством оплаты.

ЧТО ДЕЛАЕМ. Ровно то, что советует сама ЮKassa: уведомлению не верить, а
переспросить платёж через API — GET /v3/payments/{id} с Basic-авторизацией магазина
(shop_id:секретный ключ). Подделать ответ, не зная ключа, нельзя, а платёж чужого
магазина API просто не отдаст (404). Статус берём ТОЛЬКО из ответа: тело вебхука
теперь лишь подсказывает, какой платёж проверить. Идентификатор в ответе обязан
совпасть с тем, о котором спрашивали.

СНАЧАЛА НАША БАЗА, ПОТОМ API. Раз тело недостоверно, до нас может дойти любой POST,
и каждый стоил бы запроса к ЮKassa. Поэтому до API спрашиваем свою базу: есть ли
счёт ЮKassa с этим id, который ещё может стать оплаченным — PENDING, CANCELED
(опоздавшая оплата после отмены по таймауту законна, её поднимает
gateway_payment.py) или FAILED. Нет такого счёта или он уже проведён — проводить
нечего: тихо отвечаем «принято», в API не ходим, владельца не зовём. Сумма и валюта
для сверки берутся из того же чтения.

ЧЕМ ЧИТАЕМ БАЗУ И КАК БЫСТРО ОТПУСКАЕМ. Сессией САМОГО запроса (REQUEST-scope из
контейнера dishka): её соединение база и так держит весь запрос — ещё до
handle_webhook она читает шлюз (`GetPaymentGatewayInstance` →
`PaymentGatewayDao.get_by_type`) этой же сессией. Второе соединение из общего пула
означало бы ДВА соединения на каждый запрос, а новый engine на каждый вебхук (как у
сверки суммы ЮMoney) — по новому подключению к Postgres мимо пула. Сразу после
чтения делаем rollback: дальше мы уходим в API на секунды, и держать соединение всё
это время нельзя — под нагрузкой пул кончится, и вместе с вебхуками встанет кабинет.

ЧЕМ СПРАШИВАЕМ API И СКОЛЬКО ПРОВЕРОК РАЗОМ. Отдельным клиентом, а не общим
`self._client`, которым база создаёт платежи: создание платежей не должно зависеть
от проверок. Одновременных проверок не больше CHECK_CONNECTIONS, и считаем их САМИ
(семафор), а не ожиданием места в пуле httpx: там таймер ожидания начинается заново
на каждой раздаче соединения, очередь растёт молча, и каждый ждущий держит
соединение базы. Лишний вебхук не ждёт вовсе — сразу просим ЮKassa повторить.
Адрес API, авторизацию и заголовки берём у базового клиента, собранного её же
конструктором: собирать ключ второй раз — значит однажды разойтись с базой.

СБОЙ ПРОВЕРКИ — НЕ ОПЛАТА, А ПОВТОР. Не прочиталась наша база, не достучались до
API, оно ответило 5xx/401, прислало не JSON, платёж ещё не в финальном статусе —
платёж НЕ проводим ни в каком виде. Но и потерять честную оплату нельзя: ЮKassa
повторяет уведомление, пока не получит 200. Здесь ловушка базы: на ЛЮБОЕ
исключение из шлюза её эндпоинт отвечает 200 (через `build_webhook_response`), и
ЮKassa сочла бы уведомление доставленным, а счёт навсегда остался бы в PENDING.
Поэтому такой сбой метим в `request.scope`, и наш `build_webhook_response` отвечает
503 — ЮKassa придёт снова. Исключение при этом всё равно летит наверх: база пишет
его в лог и шлёт владельцу ошибку — сбой виден.

Окончательный отказ (ЮKassa не знает платежа, который есть у нас; в ответе другой
платёж; сумма или валюта не та) — без повтора: второй раз ответ будет тем же. Но
владелец узнаёт о нём ошибкой: за таким отказом может стоять оплата.

СУММА И ВАЛЮТА. Счёт в ЮKassa создаёт сама база на `pricing.final_amount` в валюте
шлюза, и эти же значения лежат в нашей транзакции. Расхождение означает, что платёж
не тот, за который мы его принимаем, — не проводим. Как и у ЮMoney, отказываем
только на недоплату: переплата — не повод оставлять человека без подписки (у ЮKassa
её и не бывает — сумму ставим мы).

ПОЧЕМУ ОБЁРТКА, А НЕ ЗАМЕНА. Проверку адреса и разбор тела оставляем базовыми —
это второй рубеж, и переносить его к себе значит однажды разойтись с апстримом.
Зовём базу как есть и лишь подменяем её вывод о статусе.

ЕСЛИ ПРАВКА НЕ ВСТАЛА — ВЕБХУКИ ВЫКЛЮЧЕНЫ. Обычная правка при изменившемся
исходнике отступает, и бот работает как база. Здесь «как база» = снова верить телу
уведомления. Поэтому при любом несовпадении приём вебхуков ЮKassa отключается:
каждый вебхук отклоняется с 503 (ЮKassa повторяет уведомления — после обновления
правки повтор пройдёт), оплата не засчитывается, владелец получает ошибку на каждый
вебхук, а сборка — отказ в failures(), на котором валится check-update.sh.
"""

from __future__ import annotations

import asyncio

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from uuid import UUID

import httpx
import orjson
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from src.core.enums import PaymentGatewayType, TransactionStatus

from . import PatchTargetChanged, expect_source

# sha256 кода базы v0.8.2, на который опирается правка.
BASE_METHODS = {
    # Оборачиваем: из него берём IP-фильтр, разбор тела и id платежа.
    "YookassaGateway.handle_webhook": (
        "88838c876d9834113420160021e87c90ca14b1ddc8446c78d67b5ea391c5b0de"
    ),
    # IP-фильтр — теперь второй рубеж. Тронут — надо посмотреть глазами.
    "YookassaGateway._verify_webhook": (
        "83ec8a6fc3b1618c4c5dab89dadba252e587bded6341ef734b8e0d81ef153912"
    ),
    # Здесь собирается клиент с адресом API и авторизацией магазина, у которого мы
    # их берём. Поменяется способ — наш запрос может уйти не туда.
    "YookassaGateway.__init__": (
        "aaf311ad42616a5dc6b31854c16a9bf7d162d4ceb17c85ae56e9e616244c34e9"
    ),
}
BASE_CLASS_METHODS = {
    # Сам клиент: base_url + auth → httpx.AsyncClient.
    "BasePaymentGateway._make_client": (
        "bd4b0602d23cba83d7b91cba2ac7c1bd438e7827df17b0cc316dc92b250bc7d4"
    ),
    # Наш build_webhook_response отдаёт ему всё, кроме просьбы о повторе.
    "BasePaymentGateway.build_webhook_response": (
        "f5eabcf25039f88ba0fa7f1704390049c6ec8ef23832cb7e8a80ffeed000f36a"
    ),
}
# Эндпоинт вебхуков: повтор держится на том, как он зовёт шлюз.
ENDPOINT_METHODS = {
    # При исключении из шлюза зовёт _build_response, при None — не ставит в очередь.
    "_process_payment_webhook": (
        "cc1df7296360029cee78f51da33e958a6fae2b57c48f0f9efdfcb9705f6ebcfe"
    ),
    # Ответ шлюзу = его build_webhook_response (наш 503). Начнёт отвечать сам —
    # просьба о повторе потеряется, и ЮKassa сочтёт сбой проверки доставкой.
    "_build_response": (
        "61c35e28c927c60495e0216d4fd0ee4c72467aed138370c97eeefd7ced062960"
    ),
}

# ЮKassa ждёт ответа на уведомление недолго; базовые 30 с клиента — много. Один и
# тот же предел на соединение, чтение, запись и ожидание свободного места в пуле.
API_TIMEOUT = 10.0
# Сколько проверок разом может идти к API. Честных вебхуков — единицы в минуту;
# больше нужно только потоку подделок, а ему пусть не хватает.
CHECK_CONNECTIONS = 4
# Сколько проверок идёт одновременно — считаем САМИ, а не ожиданием места в пуле
# httpx: там таймер ожидания начинается заново на каждой раздаче соединения
# (httpcore 1.0.9), и очередь растёт молча, а каждый ждущий держит соединение базы.
# Здесь лишний запрос не ждёт вовсе: сразу просим ЮKassa повторить.
_CHECKS = asyncio.Semaphore(CHECK_CONNECTIONS)

# Где на экземпляре шлюза лежит наш клиент (вместе с базовым, из которого собран).
CLIENT_ATTR = "_overlay_yookassa_check_client"
# Метка «проверить не удалось — пусть ЮKassa повторит». В scope, а не в self:
# экземпляр шлюза один на все запросы (кэш фабрики), а scope — у каждого свой.
RETRY_KEY = "overlay.yookassa.retry"

STATUS_MAP = {
    "succeeded": TransactionStatus.COMPLETED,
    "canceled": TransactionStatus.CANCELED,
}
# Счета, которые ещё могут стать оплаченными. CANCELED — счёт, отменённый нашим
# кроном через 30 минут: оплата по старой ссылке законна (см. gateway_payment.py).
# В COMPLETED и REFUNDED вебхуку делать нечего — в API за ними не ходим.
OPEN_STATUSES = frozenset(
    s.name for s in (TransactionStatus.PENDING, TransactionStatus.CANCELED, TransactionStatus.FAILED)
)


class NotConfirmed(RuntimeError):
    """ЮKassa не подтвердила финальный статус — не проводим, ждём повтора."""


class Rejected(ValueError):
    """Окончательный отказ: повтор вебхука ничего не изменит."""


class WebhooksDisabled(RuntimeError):
    """Правка не встала — вебхуки ЮKassa не принимаем вовсе, ждём повтора."""


@dataclass(frozen=True)
class Invoice:
    """Наш счёт ЮKassa: статус и то, на что он выставлен."""

    status: str
    amount: Optional[Decimal]
    currency: Optional[str]


def _to_decimal(value: Any) -> Optional[Decimal]:
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError, AttributeError):
        return None


# ── наша база ──────────────────────────────────────────────────────────────────


async def _read_invoice(session: Any, payment_id: UUID) -> Optional[Invoice]:
    """Счёт ЮKassa с этим id. None — такого счёта ЮKassa у нас нет."""
    row = (
        await session.execute(
            text(
                "SELECT status::text AS status, pricing->>'final_amount' AS amount, "
                "currency::text AS currency FROM transactions "
                "WHERE payment_id = :pid AND gateway_type::text = :gateway"
            ),
            {"pid": str(payment_id), "gateway": PaymentGatewayType.YOOKASSA.name},
        )
    ).first()
    if row is None:
        return None
    return Invoice(status=row.status, amount=_to_decimal(row.amount), currency=row.currency or None)


async def _invoice(request: Any, payment_id: UUID) -> Optional[Invoice]:
    """Прочитать счёт сессией этого же запроса (почему так — см. шапку модуля).

    Ошибку НЕ глотаем: «база не ответила» — это «не знаем», а не «счёта нет».
    Проглоченная, она тихо отвечала бы ЮKassa 200 на честную оплату.
    """
    try:
        container = request.state.dishka_container
    except AttributeError as exc:
        raise NotConfirmed("у запроса нет контейнера зависимостей — счёт не прочитать") from exc
    # Сессия запроса: её соединение уже занято этим же вебхуком (база прочитала им
    # шлюз). Второе соединение из пула удваивало бы цену каждой подделки — см. шапку.
    session = await container.get(AsyncSession)
    invoice = await _read_invoice(session, payment_id)
    # И сразу ОТПУСКАЕМ соединение. Чтение открыло транзакцию (autobegin), а дальше
    # мы уходим в API ЮKassa на секунды — всё это время соединение из пула стояло бы
    # занятым, и поток подделок по чужому открытому счёту занял бы пул базы целиком.
    # Для ЮKassa эндпоинт после handle_webhook этой сессией не пользуется.
    await session.rollback()
    return invoice


# ── API ЮKassa ─────────────────────────────────────────────────────────────────


def check_client(gateway: Any) -> httpx.AsyncClient:
    """Свой клиент к API с маленьким пулом; адрес, ключ и заголовки — базовые.

    Собирается один раз на экземпляр шлюза. Фабрика базы пересоздаёт экземпляр при
    смене настроек магазина, а вместе с ним и базовый клиент — тогда соберём заново.
    """
    source = gateway._client
    cached = gateway.__dict__.get(CLIENT_ATTR)
    if cached is not None and cached[0] is source:
        return cached[1]
    client = httpx.AsyncClient(
        base_url=source.base_url,
        auth=source.auth,
        headers=source.headers,
        timeout=httpx.Timeout(API_TIMEOUT),
        limits=httpx.Limits(
            max_connections=CHECK_CONNECTIONS, max_keepalive_connections=CHECK_CONNECTIONS
        ),
    )
    setattr(gateway, CLIENT_ATTR, (source, client))
    return client


async def fetch_payment(gateway: Any, payment_id: UUID) -> Optional[dict]:
    """Платёж глазами API. None — у магазина такого платежа нет (404).

    Всё остальное, кроме 200 с объектом, — NotConfirmed: сеть, 5xx, 401 (ключ
    сменили — владелец поправит, повтор пройдёт), 429, не-JSON, занятый пул.
    """
    try:
        response = await check_client(gateway).get(f"v3/payments/{payment_id}")
    except Exception as exc:  # noqa: BLE001 — любой сбой транспорта одинаково «не знаем»
        raise NotConfirmed(f"API ЮKassa недоступно: {type(exc).__name__}: {exc}") from exc

    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise NotConfirmed(
            f"API ЮKassa ответило {response.status_code}: {response.text[:300]}"
        )
    try:
        data = orjson.loads(response.content)
    except orjson.JSONDecodeError as exc:
        raise NotConfirmed("API ЮKassa ответило не JSON") from exc
    if not isinstance(data, dict):
        raise NotConfirmed("API ЮKassa ответило не объектом платежа")
    return data


def status_from_api(payment_id: UUID, payment: dict) -> TransactionStatus:
    """Итоговый статус — по ответу API и только по нему."""
    try:
        answered = UUID(str(payment.get("id")))
    except ValueError:
        answered = None
    if answered != payment_id:
        raise Rejected(
            f"API ЮKassa вернуло другой платёж: спрашивали '{payment_id}', "
            f"ответ про {payment.get('id')!r}"
        )

    status = payment.get("status")
    mapped = STATUS_MAP.get(status) if isinstance(status, str) else None
    if mapped is None:
        # pending / waiting_for_capture: база на такое тоже отвечала отказом
        # ("Unsupported status"). Мы вдобавок просим повтор — если вебхук настоящий
        # и API просто не успело, следующая попытка увидит финальный статус.
        raise NotConfirmed(f"Unsupported status: {status}")
    return mapped


def amount_problem(payment: dict, invoice: Optional[Invoice]) -> Optional[str]:
    """Почему оплату нельзя провести по сумме/валюте. None — можно."""
    if invoice is None:
        return None

    amount = payment.get("amount")
    if not isinstance(amount, dict):
        return "API ЮKassa не сообщило сумму платежа"
    paid = _to_decimal(amount.get("value"))
    currency = str(amount.get("currency") or "").strip().upper()
    if paid is None or not currency:
        return f"API ЮKassa прислало нечитаемую сумму: {amount!r}"

    if invoice.currency and currency != invoice.currency.strip().upper():
        return f"валюта платежа {currency}, а у счёта {invoice.currency}"
    if invoice.amount is not None and paid < invoice.amount:
        return f"оплачено {paid} {currency}, а по счёту {invoice.amount}"
    return None


async def confirm(
    gateway: Any, request: Any, payment_id: UUID, claimed: TransactionStatus
) -> Optional[tuple[UUID, TransactionStatus]]:
    """Что на самом деле с платежом. Формат ответа — как у базового handle_webhook."""
    invoice = await _invoice(request, payment_id)
    if invoice is None:
        # Так выглядит и подделка со случайным id, и настоящее уведомление не о
        # нашем платеже (refund.succeeded несёт в object.id номер ВОЗВРАТА).
        # Провести нечего, API не спрашиваем, владельца не зовём: иначе любой
        # прохожий тратил бы запросы к ЮKassa и заваливал его уведомлениями.
        logger.warning(f"ЮKassa: вебхук о '{payment_id}', такого счёта ЮKassa у нас нет — не проводим")
        return None
    if invoice.status not in OPEN_STATUSES:
        logger.info(f"ЮKassa: счёт '{payment_id}' уже {invoice.status} — вебхуку делать нечего")
        return None

    if _CHECKS.locked():
        # Все места проверки заняты. Ждать нельзя: ждущий держит соединение базы, и
        # очередь из подделок выбрала бы пул. Просим ЮKassa повторить — честный
        # вебхук вернётся через минуты, когда поток схлынет. Метку ставим САМИ и
        # выходим без исключения: владельцу это не сбой, а защита от потока, и
        # звать его на каждый запрос потока — значит завалить его уведомлениями.
        logger.warning(
            f"ЮKassa: все {CHECK_CONNECTIONS} проверок заняты, просим повторить вебхук позже"
        )
        request.scope[RETRY_KEY] = True
        return None

    async with _CHECKS:
        payment = await fetch_payment(gateway, payment_id)
    if payment is None:
        # Счёт у нас есть, а магазин его не знает: платёж создан в ДРУГОМ магазине —
        # сменили shop_id/ключ, перепутали тестовый и боевой. Повтор ответа не
        # изменит, но за этим может стоять оплата — владелец должен знать.
        raise Rejected(
            f"ЮKassa: платёж '{payment_id}' НЕ проведён — счёт у нас есть ({invoice.status}), "
            "а API магазина его не знает (404). Похоже, в настройках шлюза не тот магазин, "
            "в котором создан платёж (сменили shop_id/ключ, перепутали тестовый и боевой). "
            "Сверьте платёж в личном кабинете ЮKassa и при оплате проведите вручную"
        )

    status = status_from_api(payment_id, payment)
    if status != claimed:
        logger.warning(
            f"ЮKassa: вебхук по '{payment_id}' утверждал {claimed}, API — {status}. "
            "Верим API; расхождение похоже на подделку вебхука"
        )

    if status == TransactionStatus.COMPLETED:
        problem = amount_problem(payment, invoice)
        if problem:
            raise Rejected(f"ЮKassa: платёж '{payment_id}' НЕ проведён — {problem}")

    return payment_id, status


# ── установка ──────────────────────────────────────────────────────────────────


def _verify_base(target: Any, base: Any, cls: Any) -> None:
    """Всё, на чём держится правка, — той формы, которую мы проверяли."""
    for qualname, sha in BASE_METHODS.items():
        expect_source(target, qualname, sha, qualname)
    for qualname, sha in BASE_CLASS_METHODS.items():
        expect_source(base, qualname, sha, qualname)

    # Свой ответ на вебхук мы ДОБАВЛЯЕМ. Появится он у базы — сначала посмотреть,
    # что он делает, а не молча перекрыть.
    if "build_webhook_response" in vars(cls):
        raise PatchTargetChanged(
            "у YookassaGateway появился свой build_webhook_response — сверьте с повтором вебхука"
        )


def _retry_response(base_response: Any) -> Any:
    async def build_webhook_response(self, request):  # noqa: ANN001, ANN202
        if request.scope.get(RETRY_KEY):
            return Response(status_code=503)
        return await base_response(self, request)

    return build_webhook_response


def _disable(cls: Any, reason: str) -> str:
    """Выключить приём вебхуков ЮKassa: ничего не засчитывать, просить повтор."""
    message = (
        "Приём вебхуков ЮKassa ОТКЛЮЧЁН: проверка оплаты через API не встала "
        f"({reason}). Оплата НЕ проведена, ЮKassa будет повторять уведомление: обновите "
        "overlay_patches/yookassa_api_status.py под новую базу, и повтор пройдёт "
        "(не успевшие — сверить в личном кабинете ЮKassa)"
    )
    if getattr(cls.handle_webhook, "_overlay_yookassa_disabled", False):
        return message

    async def handle_webhook(self, request):  # noqa: ANN001, ANN202
        # Адрес смотрим лишь затем, чтобы не звать владельца на каждый POST
        # сканера. Сам фильтр мог измениться вместе с базой — тогда не мешает.
        try:
            trusted = self._verify_webhook(request)
        except PermissionError:
            raise
        except Exception:  # noqa: BLE001
            trusted = True
        if not trusted:
            raise PermissionError("Webhook verification failed")
        request.scope[RETRY_KEY] = True
        logger.critical(message)
        raise WebhooksDisabled(message)

    handle_webhook._overlay_yookassa_disabled = True  # type: ignore[attr-defined]
    cls.build_webhook_response = _retry_response(cls.build_webhook_response)
    cls.handle_webhook = handle_webhook
    return message


def apply() -> str:
    import src.infrastructure.payment_gateways.base as base
    import src.infrastructure.payment_gateways.yookassa as target

    cls = target.YookassaGateway
    if getattr(cls.handle_webhook, "_overlay_yookassa_api", False):
        return "уже применено"

    try:
        _verify_base(target, base, cls)
    except Exception as exc:  # noqa: BLE001 — любая причина одинаково опасна
        # Отступить к базе нельзя: базовый обработчик засчитывает подделку.
        message = _disable(cls, f"{type(exc).__name__}: {exc}")
        logger.critical(message)
        failure = PatchTargetChanged(message)
        failure.fallback = (
            "Приём вебхуков ЮKassa ОТКЛЮЧЁН (503, оплата не засчитывается), "
            "чтобы не вернуть засчитывание подделки."
        )
        raise failure from exc

    original = cls.handle_webhook

    async def handle_webhook(self, request):  # noqa: ANN001, ANN202
        # IP-фильтр и разбор тела — базовые; статус из тела дальше НЕ используется.
        result = await original(self, request)
        if result is None:
            return None
        payment_id, claimed = result
        try:
            return await confirm(self, request, payment_id, claimed)
        except Rejected:
            raise
        except Exception:
            # Любой другой сбой — «не знаем», а не «не оплачено навсегда»: просим
            # ЮKassa повторить. Оплата при этом не проведена — исключение летит дальше.
            request.scope[RETRY_KEY] = True
            raise

    handle_webhook._overlay_yookassa_api = True  # type: ignore[attr-defined]
    # Базовый обработчик — для теста выключателя: вернуть его и увидеть, что при
    # чужом исходнике на место встаёт отказ, а не он.
    handle_webhook._overlay_original = original  # type: ignore[attr-defined]
    cls.build_webhook_response = _retry_response(cls.build_webhook_response)
    cls.handle_webhook = handle_webhook
    return (
        "статус оплаты берётся из API ЮKassa (только по нашим открытым счетам), "
        "сбой проверки — повтор вебхука"
    )


def check_endpoint() -> str:
    """Эндпоинт не меняем, но повтор вебхука держится на его нынешнем устройстве."""
    import src.web.endpoints.payments as target

    for qualname, sha in ENDPOINT_METHODS.items():
        expect_source(target, qualname, sha, f"эндпоинт платёжных вебхуков: {qualname}")
    return (
        "эндпоинт сверен: исключение шлюза → build_webhook_response, "
        "None → без очереди, ответ — от шлюза"
    )
