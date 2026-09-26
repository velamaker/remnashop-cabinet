"""ЮKassa: оплата засчитывается только по ответу API, а не по тексту вебхука.

ЧТО ЗАКРЫВАЕТ overlay_patches/yookassa_api_status.py. База брала статус прямо из
тела уведомления, а достоверность проверяла только по адресу отправителя — подписи
у вебхуков ЮKassa нет, а адрес читается из заголовков запроса. Одного этого рубежа
мало, поэтому статус подтверждается запросом к API ЮKassa.

КАК ПРОВЕРЯЕМ. Вебхук идёт через НАСТОЯЩИЙ эндпоинт базы (`_process_payment_webhook`)
в НАСТОЯЩИЙ шлюз, собранный её же конструктором из настроек магазина, с НАСТОЯЩИМ
контейнером dishka в запросе (как его кладёт middleware базы). Подменены только:
  * сеть — транспорт httpx (MockTransport вместо API ЮKassa, настоящих запросов нет).
    Подменяется именно ТРАНСПОРТ, который httpx строит внутри клиента: таймаут и
    размер пула клиента остаются нашими и проверяются;
  * очередь выдачи — записываем, что в неё поставили;
  * в большинстве тестов — сам SQL чтения счёта (`_read_invoice`). Настоящий SQL
    идёт на одноразовом Postgres (RS_PG_DSN) в конце файла, а сбой базы — и там,
    и здесь через engine на закрытый порт.
«Оплата засчитана» = платёж поставлен в очередь выдачи со статусом COMPLETED; ничего
больше ниже по течению про статус не спрашивает.

ЧТО ЗАПЕРТО:
  * поддельный «succeeded», а API говорит pending → НЕ засчитано;
  * API говорит succeeded → засчитано, запрос ушёл GET /v3/payments/{id} с
    Basic-авторизацией магазина, СВОИМ клиентом (пул CHECK_CONNECTIONS, таймаут
    API_TIMEOUT), а не общим клиентом создания платежей;
  * до API — наша база: счёта ЮKassa с таким id нет или он уже проведён → в API не
    ходим, владельца не зовём; CANCELED/FAILED (опоздавшая оплата) — проверяем;
  * база не ответила → не засчитано и 503 (а не тихое «счёта нет»);
  * в ответе API другой id → отказ;
  * API недоступно / 500 / 401 / не JSON / таймаут → отказ И 503, чтобы ЮKassa
    повторила уведомление (не fail-open и не потеря честной оплаты);
  * canceled из API → CANCELED; статус из тела не решает ничего в обе стороны;
  * 404 по НАШЕМУ счёту (магазин сменили) → отказ с ошибкой владельцу;
  * сумма/валюта не сходятся со счётом → отказ;
  * IP-фильтр базы остался вторым рубежом: чужой адрес до базы и API не доходит;
  * исходник базы изменился → приём вебхуков ЮKassa ВЫКЛЮЧЕН (503, не засчитано),
    а не возврат к базовому обработчику, засчитывающему подделку.

Данные синтетические. Запуск — внутри образа бота, как остальные тесты (см. ci.yml).
"""

import base64
import importlib
import json
import time
import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, AsyncIterator, Optional

import httpx
import pytest
from dishka import Provider, Scope, make_async_container
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import overlay_patches

yk = importlib.import_module("src.infrastructure.payment_gateways.yookassa")
payments = importlib.import_module("src.web.endpoints.payments")
patch = importlib.import_module("overlay_patches.yookassa_api_status")
dto = importlib.import_module("src.application.dto.payment_gateway")
httpx_client = importlib.import_module("httpx._client")

from src.core.enums import Currency, PaymentGatewayType, TransactionStatus  # noqa: E402

from _pg_dsn import sqlalchemy_dsn  # noqa: E402 — соседний модуль тестов

COMPLETED = TransactionStatus.COMPLETED
CANCELED = TransactionStatus.CANCELED
YOOKASSA = PaymentGatewayType.YOOKASSA

SHOP_ID = "506751"
API_KEY = "test_ci_secret_key"
YOOKASSA_IP = "185.71.76.10"  # из NETWORKS базы: 185.71.76.0/27
PAYMENT_ID = uuid.UUID("2419a771-000f-5000-9000-1edaf29243f2")
OPEN_INVOICE = patch.Invoice(status="PENDING", amount=Decimal("929"), currency="RUB")
# Настоящий SQL — для тестов, которые его и проверяют (фикстура его подменяет).
REAL_READ_INVOICE = patch._read_invoice
# Закрытый порт: соединение с «базой» отвергается сразу, без сети наружу.
UNREACHABLE_DSN = "postgresql+asyncpg://nobody:nothing@127.0.0.1:9/nothing"


# ── окружение ──────────────────────────────────────────────────────────────────


def api_payment(status: str = "succeeded", pid: Any = PAYMENT_ID, value: str = "929.00",
                currency: str = "RUB") -> dict:
    """Объект платежа в том виде, в каком его отдаёт GET /v3/payments/{id}."""
    return {
        "id": str(pid),
        "status": status,
        "paid": status == "succeeded",
        "amount": {"value": value, "currency": currency},
        "income_amount": {"value": "896.49", "currency": currency},
        "test": False,
    }


def webhook(status: str = "succeeded", pid: Any = PAYMENT_ID, event: Optional[str] = None) -> dict:
    """Тело уведомления — то, что может прислать кто угодно."""
    return {
        "type": "notification",
        "event": event or f"payment.{status}",
        "object": {"id": str(pid), "status": status, "paid": status == "succeeded"},
    }


class Api:
    """Поддельная ЮKassa: отвечает заданным и записывает, о чём её спросили."""

    def __init__(self, respond) -> None:  # noqa: ANN001
        self.respond = respond
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        result = self.respond(request)
        if isinstance(result, Exception):
            raise result
        if isinstance(result, httpx.Response):
            return result
        return httpx.Response(200, json=result)


def container_with(sessions) -> Any:  # noqa: ANN001
    """Настоящий контейнер dishka, как у DatabaseProvider базы: фабрика сессий —
    APP-уровня, сама сессия — REQUEST-уровня (`async with pool() as session`)."""
    provider = Provider(scope=Scope.APP)
    provider.provide(lambda: sessions, provides=async_sessionmaker[AsyncSession])

    async def request_session(pool: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
        async with pool() as session:
            yield session

    provider.provide(request_session, scope=Scope.REQUEST, provides=AsyncSession)
    return make_async_container(provider)


@pytest.fixture
def world(monkeypatch):
    """Шлюз базы + её эндпоинт, без сети, без очереди; SQL счёта подменён."""
    state = SimpleNamespace(
        api=None, queued=[], errors=[], invoice=OPEN_INVOICE, reads=0, transports=[], shared=[],
        base_reads_gateway=False, current_container=None,
        sessions=async_sessionmaker(create_async_engine(UNREACHABLE_DSN), expire_on_commit=False),
    )

    def shared(request: httpx.Request) -> httpx.Response:
        state.shared.append(request)
        return httpx.Response(500, text="общий клиент базы — не для проверки вебхуков")

    # Базовый клиент собирает КОНСТРУКТОР базы (адрес API и авторизация — её). Мы
    # лишь подставляем транспорт, чтобы запрос не ушёл в сеть; таймаут — как у базы.
    # Любой запрос через него — ошибка: проверка обязана идти своим клиентом.
    def make_client(self, base_url, auth=None, headers=None, timeout=30.0):  # noqa: ANN001
        return httpx.AsyncClient(
            base_url=base_url, auth=auth, headers=headers, timeout=httpx.Timeout(timeout),
            transport=httpx.MockTransport(shared),
        )

    monkeypatch.setattr(yk.YookassaGateway, "_make_client", make_client)

    # Транспорт, который httpx строит сам внутри клиента проверки. Клиент при этом
    # собирается нашим кодом целиком — с его таймаутом и пулом; здесь их записываем.
    def transport(**kwargs):  # noqa: ANN003, ANN202
        state.transports.append(kwargs)
        return httpx.MockTransport(lambda r: state.api(r))

    monkeypatch.setattr(httpx_client, "AsyncHTTPTransport", transport)

    async def read_invoice(session, payment_id):  # noqa: ANN001, ANN202
        assert isinstance(session, AsyncSession), "счёт читается сессией из контейнера"
        state.reads += 1
        if isinstance(state.invoice, Exception):
            raise state.invoice
        return state.invoice

    monkeypatch.setattr(patch, "_read_invoice", read_invoice)

    class Task:
        @staticmethod
        async def kiq(payment_id, status, gateway):  # noqa: ANN001, ANN205
            state.queued.append((payment_id, status, gateway))

    monkeypatch.setattr(payments, "handle_payment_transaction_task", Task)
    monkeypatch.setattr(payments, "ErrorEvent", lambda **kw: kw)

    gateway = yk.YookassaGateway(
        gateway=dto.PaymentGatewayDto(
            type=YOOKASSA,
            currency=Currency.RUB,
            is_active=True,
            settings=dto.YooKassaGatewaySettingsDto(
                shop_id=SHOP_ID, api_key=SecretStr(API_KEY), customer="shop@example.com", vat_code=1
            ),
        ),
        bot=None,
        config=None,
    )

    class Publisher:
        @staticmethod
        async def publish(event):  # noqa: ANN001, ANN205
            state.errors.append(event["exception"])

    class Gateways:
        @staticmethod
        async def system(gateway_type):  # noqa: ANN001, ANN205
            assert gateway_type == YOOKASSA
            if state.base_reads_gateway:
                # Как база: PaymentGatewayDao.get_by_type сессией запроса — её
                # соединение занято до конца запроса.
                session = await state.current_container.get(AsyncSession)
                await session.execute(text("SELECT 1"))
            return gateway

    async def deliver(body: dict, ip: str = YOOKASSA_IP) -> int:
        """Прогнать вебхук через эндпоинт базы; вернуть код ответа ЮKassa."""
        from starlette.requests import Request

        raw = json.dumps(body).encode()

        async def receive():  # noqa: ANN202
            return {"type": "http.request", "body": raw, "more_body": False}

        container = container_with(state.sessions)
        try:
            # Как ContainerMiddleware базы: контейнер запроса лежит в request.state.
            async with container(scope=Scope.REQUEST) as request_container:
                state.current_container = request_container
                scope = {
                    "type": "http",
                    "method": "POST",
                    "path": "/api/v1/payments/yookassa",
                    "query_string": b"",
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"cf-connecting-ip", ip.encode()),
                    ],
                    "state": {"dishka_container": request_container},
                }
                response = await payments._process_payment_webhook(
                    gateway_type="yookassa",
                    request=Request(scope, receive),
                    config=SimpleNamespace(build=SimpleNamespace(data={})),
                    event_publisher=Publisher,
                    get_payment_gateway_instance=Gateways,
                    transaction_dao=None,
                    uow=None,
                )
        finally:
            await container.close()
        return response.status_code

    state.deliver = deliver
    state.gateway = gateway
    return state


def answer(world, respond) -> Api:  # noqa: ANN001
    world.api = Api(respond)
    return world.api


def by_url(status: str = "succeeded", **kw):  # noqa: ANN003, ANN201
    """API отвечает о том платеже, о котором спросили."""
    return lambda r: api_payment(status, pid=r.url.path.rsplit("/", 1)[1], **kw)


# ── правка стоит ───────────────────────────────────────────────────────────────


def test_patch_is_applied() -> None:
    assert getattr(yk.YookassaGateway.handle_webhook, "_overlay_yookassa_api", False)
    assert not getattr(yk.YookassaGateway.handle_webhook, "_overlay_yookassa_disabled", False)
    assert "build_webhook_response" in vars(yk.YookassaGateway)
    mine = [f for f in overlay_patches.failures() if "ЮKassa" in f[0]]
    assert mine == [], mine


def test_endpoint_check_covers_the_response_builder() -> None:
    """Повтор (503) держится на _build_response базы — его исходник тоже сверяем."""
    assert set(patch.ENDPOINT_METHODS) >= {"_process_payment_webhook", "_build_response"}
    patch.check_endpoint()  # хэши совпадают с базой, на которой идут тесты


def test_base_provides_the_request_session_we_read_with() -> None:
    """Счёт читаем сессией ЗАПРОСА (`AsyncSession`, REQUEST) — база обязана её давать."""
    from src.infrastructure.di.providers.database import DatabaseProvider

    provided = {
        f.provides.type_hint: f.scope for f in DatabaseProvider().factories
    }
    assert provided.get(AsyncSession) == Scope.REQUEST


# ── главное: подделка ──────────────────────────────────────────────────────────


async def test_forged_succeeded_while_api_says_pending_is_not_credited(world) -> None:
    """Сама атака: создал счёт, прислал «succeeded» с IP ЮKassa в заголовке."""
    answer(world, lambda r: api_payment("pending"))
    code = await world.deliver(webhook("succeeded"))
    assert world.queued == [], "поддельный succeeded провёл неоплаченный счёт"
    # Не 200: если вебхук вдруг настоящий, а API не успело, ЮKassa повторит.
    assert code == 503


async def test_forged_canceled_does_not_cancel_someone_elses_pending_invoice(world) -> None:
    """Обратная сторона той же дыры: чужой неоплаченный счёт гасили подделкой."""
    answer(world, lambda r: api_payment("pending"))
    await world.deliver(webhook("canceled"))
    assert world.queued == []


# ── до API — наша база ─────────────────────────────────────────────────────────


async def test_unknown_payment_is_quiet_and_costs_no_api_request(world) -> None:
    """Случайный id подделки или уведомление о возврате: такого счёта у нас нет.

    В API не ходим (иначе каждый POST с поддельным заголовком тратил бы запрос),
    владельца не зовём (иначе любой прохожий заваливал бы его уведомлениями),
    повтор не просим — его ответ будет тем же.
    """
    world.invoice = None
    api = answer(world, lambda r: api_payment("succeeded"))
    code = await world.deliver(webhook("succeeded", event="refund.succeeded"))
    assert world.queued == []
    assert code == 200
    assert world.errors == []
    assert api.requests == [], "по чужому id проверка не должна доходить до API"
    assert world.reads == 1


@pytest.mark.parametrize("status", ["COMPLETED", "REFUNDED"])
async def test_closed_invoice_costs_no_api_request(world, status) -> None:
    world.invoice = patch.Invoice(status=status, amount=Decimal("929"), currency="RUB")
    api = answer(world, lambda r: api_payment("succeeded"))
    code = await world.deliver(webhook("succeeded"))
    assert (world.queued, code, world.errors, api.requests) == ([], 200, [], [])


@pytest.mark.parametrize("status", ["CANCELED", "FAILED"])
async def test_late_payment_on_closed_by_timeout_invoice_is_checked(world, status) -> None:
    """Счёт отменён кроном через 30 минут, а человек заплатил по старой ссылке.

    Такую оплату поднимает gateway_payment.py — отсечь её здесь значило бы вернуть
    потерю денег, которую та правка чинила.
    """
    world.invoice = patch.Invoice(status=status, amount=Decimal("929"), currency="RUB")
    answer(world, lambda r: api_payment("succeeded"))
    code = await world.deliver(webhook("succeeded"))
    assert world.queued == [(PAYMENT_ID, COMPLETED, YOOKASSA)]
    assert code == 200


async def test_database_failure_is_not_credited_and_asks_for_retry(world) -> None:
    world.invoice = ConnectionError("db down")
    api = answer(world, lambda r: api_payment("succeeded"))
    code = await world.deliver(webhook("succeeded"))
    assert world.queued == []
    assert code == 503
    assert world.errors, "сбой базы обязан дойти до владельца"
    assert api.requests == []


async def test_real_read_on_unreachable_database_asks_for_retry(world, monkeypatch) -> None:
    """Настоящее чтение счёта, база не отвечает: «не знаем», а не «счёта нет».

    Здесь SQL не подменён — соединение отвергает закрытый порт. Проглоти чтение
    ошибку и верни None, вебхук получил бы тихие 200 — и честная оплата пропала бы.
    """
    monkeypatch.setattr(patch, "_read_invoice", REAL_READ_INVOICE)
    api = answer(world, lambda r: api_payment("succeeded"))
    code = await world.deliver(webhook("succeeded"))
    assert world.queued == []
    assert code == 503, "сбой базы проглочен: ЮKassa не повторит, оплата потеряется"
    assert world.errors
    assert api.requests == []


async def test_request_without_container_asks_for_retry(world, monkeypatch) -> None:
    """Контейнера у запроса нет (переделали middleware базы) — громко, с повтором."""
    real_invoice = patch._invoice

    async def no_container(request, payment_id):  # noqa: ANN001, ANN202
        del request.state.dishka_container
        return await real_invoice(request, payment_id)

    monkeypatch.setattr(patch, "_invoice", no_container)
    answer(world, lambda r: api_payment("succeeded"))
    code = await world.deliver(webhook("succeeded"))
    assert (world.queued, code) == ([], 503)
    assert any(isinstance(e, patch.NotConfirmed) for e in world.errors)


# ── честный путь ───────────────────────────────────────────────────────────────


async def test_api_succeeded_is_credited_and_asked_the_right_way(world) -> None:
    api = answer(world, lambda r: api_payment("succeeded"))
    code = await world.deliver(webhook("succeeded"))

    assert world.queued == [(PAYMENT_ID, COMPLETED, YOOKASSA)]
    assert code == 200
    assert world.errors == []

    [request] = api.requests
    assert request.method == "GET"
    assert str(request.url) == f"https://api.yookassa.ru/v3/payments/{PAYMENT_ID}"
    expected = base64.b64encode(f"{SHOP_ID}:{API_KEY}".encode()).decode()
    assert request.headers["authorization"] == f"Basic {expected}", "не авторизация магазина"


async def test_check_uses_own_small_pool_and_short_timeout(world) -> None:
    """Проверка не может занять соединения, которыми база создаёт платежи.

    Поток подделок по id своего PENDING-счёта доходит до API; на своём пуле он
    упрётся в CHECK_CONNECTIONS соединений, а создание платежей не заметит ничего.
    Таймаут — действующий, с запроса: убрать его из клиента = получить чужой.
    """
    api = answer(world, lambda r: api_payment("succeeded"))
    assert await world.deliver(webhook("succeeded")) == 200
    assert await world.deliver(webhook("succeeded")) == 200

    assert world.shared == [], "проверка ушла общим клиентом создания платежей"
    [built] = world.transports  # клиент собран один раз на экземпляр шлюза
    limits = built["limits"]
    assert limits.max_connections == patch.CHECK_CONNECTIONS
    assert 1 <= patch.CHECK_CONNECTIONS <= 8, "пул проверки должен быть маленьким"

    assert patch.API_TIMEOUT <= 10
    want = {"connect": patch.API_TIMEOUT, "read": patch.API_TIMEOUT,
            "write": patch.API_TIMEOUT, "pool": patch.API_TIMEOUT}
    for request in api.requests:
        assert request.extensions["timeout"] == want


async def test_api_canceled_gives_canceled(world) -> None:
    answer(world, lambda r: api_payment("canceled"))
    code = await world.deliver(webhook("canceled"))
    assert world.queued == [(PAYMENT_ID, CANCELED, YOOKASSA)]
    assert code == 200


async def test_status_comes_only_from_api_even_against_the_body(world) -> None:
    """Тело «canceled», API «succeeded» — платёж оплачен, значит COMPLETED.

    Тело больше ничего не решает: ни в пользу покупателя, ни против него.
    Двойной выдачи здесь нет — повторный COMPLETED база гасит CAS-переходом.
    """
    answer(world, lambda r: api_payment("succeeded"))
    await world.deliver(webhook("canceled"))
    assert world.queued == [(PAYMENT_ID, COMPLETED, YOOKASSA)]

    world.queued.clear()
    answer(world, lambda r: api_payment("canceled"))
    await world.deliver(webhook("succeeded"))
    assert world.queued == [(PAYMENT_ID, CANCELED, YOOKASSA)]


# ── ответ API не о том ─────────────────────────────────────────────────────────


async def test_api_answer_about_another_payment_is_rejected(world) -> None:
    other = uuid.UUID("2419a771-000f-5000-9000-000000000001")
    answer(world, lambda r: api_payment("succeeded", pid=other))
    await world.deliver(webhook("succeeded"))
    assert world.queued == []
    assert any(isinstance(e, patch.Rejected) for e in world.errors), "отказ должен быть виден"


@pytest.mark.parametrize("junk_id", [None, "", "not-a-uuid", 42])
async def test_api_answer_without_readable_id_is_rejected(world, junk_id) -> None:
    answer(world, lambda r: {**api_payment("succeeded"), "id": junk_id})
    await world.deliver(webhook("succeeded"))
    assert world.queued == []


async def test_404_for_our_own_invoice_alerts_owner(world) -> None:
    """Счёт у нас есть, а магазин платежа не знает: сменили магазин или ключ.

    Повтор ответа не изменит — без 503. Но за этим может стоять оплата, поэтому
    владелец получает ошибку, а не одну строку WARNING в логе.
    """
    answer(world, lambda r: httpx.Response(404, json={"type": "error", "code": "not_found"}))
    code = await world.deliver(webhook("succeeded"))
    assert world.queued == []
    assert code == 200
    [error] = world.errors
    assert isinstance(error, patch.Rejected)
    assert "404" in str(error) and str(PAYMENT_ID) in str(error)


# ── API недоступно: не fail-open ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "respond",
    [
        pytest.param(lambda r: httpx.Response(500, text="Internal Server Error"), id="500"),
        pytest.param(lambda r: httpx.Response(502, text="Bad Gateway"), id="502"),
        pytest.param(lambda r: httpx.Response(401, json={"code": "invalid_credentials"}), id="401"),
        pytest.param(lambda r: httpx.Response(429, json={"code": "too_many_requests"}), id="429"),
        pytest.param(lambda r: httpx.Response(200, text="<html>proxy</html>"), id="not-json"),
        pytest.param(lambda r: httpx.Response(200, json=["succeeded"]), id="not-object"),
        pytest.param(lambda r: httpx.ConnectError("connection refused", request=r), id="connect"),
        pytest.param(lambda r: httpx.ReadTimeout("timed out", request=r), id="timeout"),
        pytest.param(lambda r: httpx.PoolTimeout("pool is busy", request=r), id="pool-busy"),
    ],
)
async def test_api_failure_is_not_credited_and_asks_for_retry(world, respond) -> None:
    answer(world, respond)
    code = await world.deliver(webhook("succeeded"))
    assert world.queued == [], "сбой проверки провёл оплату — fail-open"
    assert code == 503, "без не-200 ЮKassa не повторит, и честная оплата повиснет"
    assert world.errors, "сбой проверки обязан дойти до владельца"


@pytest.mark.parametrize("status", ["pending", "waiting_for_capture", "", None, {"x": 1}])
async def test_non_final_or_garbage_status_is_not_credited(world, status) -> None:
    answer(world, lambda r: {**api_payment("succeeded"), "status": status})
    code = await world.deliver(webhook("succeeded"))
    assert world.queued == []
    assert code == 503


async def test_retry_mark_does_not_leak_to_next_webhook(world) -> None:
    """Метка повтора живёт в запросе, а экземпляр шлюза — один на всех."""
    answer(world, lambda r: httpx.Response(500))
    assert await world.deliver(webhook("succeeded")) == 503
    answer(world, lambda r: api_payment("succeeded"))
    assert await world.deliver(webhook("succeeded")) == 200
    assert world.queued == [(PAYMENT_ID, COMPLETED, YOOKASSA)]


# ── сумма и валюта ─────────────────────────────────────────────────────────────


async def test_underpaid_amount_is_rejected(world) -> None:
    answer(world, lambda r: api_payment("succeeded", value="1.00"))
    code = await world.deliver(webhook("succeeded"))
    assert world.queued == []
    assert code == 200, "окончательный отказ — без повтора"
    assert any(isinstance(e, patch.Rejected) for e in world.errors)


async def test_other_currency_is_rejected(world) -> None:
    answer(world, lambda r: api_payment("succeeded", value="929.00", currency="USD"))
    await world.deliver(webhook("succeeded"))
    assert world.queued == []


async def test_amount_format_does_not_reject_honest_payment(world) -> None:
    """«929.00» у ЮKassa против «929» в нашей базе — одна и та же сумма."""
    answer(world, lambda r: api_payment("succeeded", value="929.00"))
    await world.deliver(webhook("succeeded"))
    assert world.queued == [(PAYMENT_ID, COMPLETED, YOOKASSA)]


def test_amount_problem_edges() -> None:
    ok = api_payment("succeeded", value="929.00")
    invoice = OPEN_INVOICE
    assert patch.amount_problem(ok, invoice) is None
    assert patch.amount_problem(ok, patch.Invoice("PENDING", Decimal("929"), "rub")) is None
    assert patch.amount_problem(ok, patch.Invoice("PENDING", None, "RUB")) is None, (
        "сумма счёта нечитаема — сверяем валюту"
    )
    assert patch.amount_problem(ok, None) is None
    assert patch.amount_problem(api_payment(value="928.99"), invoice)
    assert patch.amount_problem({**ok, "amount": None}, invoice)
    assert patch.amount_problem({**ok, "amount": {"value": "abc", "currency": "RUB"}}, invoice)


# ── второй рубеж ───────────────────────────────────────────────────────────────


async def test_untrusted_ip_is_still_refused_before_database_and_api(world) -> None:
    api = answer(world, lambda r: api_payment("succeeded"))
    code = await world.deliver(webhook("succeeded"), ip="203.0.113.7")
    assert code == 403
    assert world.queued == []
    assert api.requests == [], "IP-фильтр базы должен резать раньше запроса к API"
    assert world.reads == 0


async def test_body_status_unsupported_by_base_is_refused_as_before(world) -> None:
    """Статус тела, который база не знала, она и раньше отвергала — не трогаем."""
    api = answer(world, lambda r: api_payment("succeeded"))
    await world.deliver(webhook("waiting_for_capture"))
    assert world.queued == []
    assert api.requests == []


# ── правка не встала: выключено, а не «как у базы» ─────────────────────────────


@pytest.fixture
def base_changed(monkeypatch, capsys):
    """Вернуть базовый обработчик и «изменить» исходник базы; применить правку заново.

    Всё, что трогаем, — через monkeypatch: после теста класс снова с нашей правкой,
    а список отказов overlay — прежний.
    """
    cls = yk.YookassaGateway
    monkeypatch.setattr(cls, "handle_webhook", cls.handle_webhook._overlay_original)
    monkeypatch.delattr(cls, "build_webhook_response")
    monkeypatch.setitem(patch.BASE_METHODS, "YookassaGateway._verify_webhook", "0" * 64)
    monkeypatch.setattr(overlay_patches, "_failures", [])

    overlay_patches._run("ЮKassa: статус оплаты из её API", patch.apply)
    return SimpleNamespace(failures=overlay_patches.failures(), stderr=capsys.readouterr().err)


async def test_changed_base_disables_webhooks_instead_of_trusting_the_body(world, base_changed) -> None:
    """Правка не встала → базовый обработчик засчитал бы подделку. Он не должен вернуться."""
    [(name, reason)] = base_changed.failures
    assert "ЮKassa" in name and "ОТКЛЮЧЁН" in reason, "сборка обязана увидеть отказ"
    assert "ОТКЛЮЧЁН" in base_changed.stderr
    assert "исходном поведении базы" not in base_changed.stderr, "тревога не должна врать"

    # Даже «настоящая» оплата не засчитывается: проверить её нечем.
    api = answer(world, lambda r: api_payment("succeeded"))
    code = await world.deliver(webhook("succeeded"))
    assert world.queued == [], "при чужом исходнике вернулся обработчик, верящий телу"
    assert code == 503, "ЮKassa должна повторить, когда правку обновят"
    [error] = world.errors
    assert isinstance(error, patch.WebhooksDisabled), "владелец должен узнать на каждом вебхуке"
    assert api.requests == [] and world.reads == 0


async def test_disabled_webhooks_still_ignore_foreign_addresses(world, base_changed) -> None:
    """Выключенный приём не зовёт владельца на каждый POST сканера без заголовка ЮKassa."""
    code = await world.deliver(webhook("succeeded"), ip="203.0.113.7")
    assert (code, world.queued, world.errors) == (403, [], [])


# ── SQL счёта на настоящем Postgres (одноразовый RS_PG_DSN) ───────────────────

DSN = sqlalchemy_dsn()
SCHEMA_NAME = "yookassa_api_status_test"
# Схема без таблиц: база «отвечает», но счёт прочитать нельзя.
EMPTY_SCHEMA = "yookassa_api_status_empty"

PENDING_ID = uuid.UUID("2419a771-000f-5000-9000-00000000a001")
CANCELED_ID = uuid.UUID("2419a771-000f-5000-9000-00000000a002")
FAILED_ID = uuid.UUID("2419a771-000f-5000-9000-00000000a003")
COMPLETED_ID = uuid.UUID("2419a771-000f-5000-9000-00000000a004")
REFUNDED_ID = uuid.UUID("2419a771-000f-5000-9000-00000000a005")
YOOMONEY_ID = uuid.UUID("2419a771-000f-5000-9000-00000000a006")


def _labels(enum) -> str:  # noqa: ANN001
    # SQLAlchemy Enum базы пишет в колонку ИМЯ члена перечисления.
    return ", ".join(f"'{member.name}'" for member in enum)


def _schema_sql() -> list[str]:
    s = SCHEMA_NAME
    row = "('{pid}', '{status}', '{gateway}', '{pricing}', 'RUB')"
    rows = [
        row.format(pid=PENDING_ID, status="PENDING", gateway="YOOKASSA", pricing='{"final_amount": "929"}'),
        # Число, а не строка, в JSON — ->> отдаёт текст в обоих случаях.
        row.format(pid=CANCELED_ID, status="CANCELED", gateway="YOOKASSA", pricing='{"final_amount": 929.00}'),
        row.format(pid=FAILED_ID, status="FAILED", gateway="YOOKASSA", pricing='{"final_amount": "929"}'),
        row.format(pid=COMPLETED_ID, status="COMPLETED", gateway="YOOKASSA", pricing='{"final_amount": "929"}'),
        row.format(pid=REFUNDED_ID, status="REFUNDED", gateway="YOOKASSA", pricing='{"final_amount": "929"}'),
        # Счёт ДРУГОГО шлюза: подделка «ЮKassa» с его id не должна доходить до API.
        row.format(pid=YOOMONEY_ID, status="PENDING", gateway="YOOMONEY", pricing='{"final_amount": "929"}'),
    ]
    return [
        f"DROP SCHEMA IF EXISTS {s} CASCADE",
        f"DROP SCHEMA IF EXISTS {EMPTY_SCHEMA} CASCADE",
        f"CREATE SCHEMA {s}",
        f"CREATE SCHEMA {EMPTY_SCHEMA}",
        f"CREATE TYPE {s}.transaction_status AS ENUM ({_labels(TransactionStatus)})",
        f"CREATE TYPE {s}.payment_gateway_type AS ENUM ({_labels(PaymentGatewayType)})",
        f"CREATE TYPE {s}.currency AS ENUM ({_labels(Currency)})",
        # Колонки — как у модели Transaction базы (uuid, enum-статус/шлюз/валюта, JSONB).
        f"CREATE TABLE {s}.transactions (id SERIAL PRIMARY KEY, payment_id UUID UNIQUE NOT NULL, "
        f"status {s}.transaction_status NOT NULL, gateway_type {s}.payment_gateway_type NOT NULL, "
        f"pricing JSONB NOT NULL, currency {s}.currency NOT NULL)",
        f"INSERT INTO {s}.transactions (payment_id, status, gateway_type, pricing, currency) "
        f"VALUES {', '.join(rows)}",
    ]


@pytest.fixture
async def pg():
    """Одноразовая схема в audit-pg; после теста удаляется целиком."""
    if not DSN:
        pytest.skip("нужен RS_PG_DSN (одноразовый Postgres)")
    admin = create_async_engine(DSN)
    async with admin.begin() as conn:
        for statement in _schema_sql():
            await conn.execute(text(statement))

    def sessions(schema: str):  # noqa: ANN202
        engine = create_async_engine(
            DSN, connect_args={"server_settings": {"search_path": schema}}
        )
        engines.append(engine)
        return async_sessionmaker(engine, expire_on_commit=False)

    engines: list = []
    try:
        yield SimpleNamespace(good=sessions(SCHEMA_NAME), empty=sessions(EMPTY_SCHEMA))
    finally:
        for engine in engines:
            await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA_NAME} CASCADE"))
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {EMPTY_SCHEMA} CASCADE"))
        await admin.dispose()


async def test_invoice_read_on_real_schema(pg) -> None:
    """uuid-колонка против строкового параметра, enum-статус/шлюз/валюта — только живая база."""
    async with pg.good() as session:
        read = lambda pid: REAL_READ_INVOICE(session, pid)  # noqa: E731
        assert await read(PENDING_ID) == patch.Invoice("PENDING", Decimal("929"), "RUB")
        assert await read(CANCELED_ID) == patch.Invoice("CANCELED", Decimal("929.00"), "RUB")
        assert await read(COMPLETED_ID) == patch.Invoice("COMPLETED", Decimal("929"), "RUB")
        assert await read(YOOMONEY_ID) is None, "счёт другого шлюза — не счёт ЮKassa"
        assert await read(uuid.uuid4()) is None


@pytest.mark.parametrize(
    ("pid", "asked", "credited"),
    [
        pytest.param(PENDING_ID, True, True, id="pending"),
        pytest.param(CANCELED_ID, True, True, id="canceled-late-payment"),
        pytest.param(FAILED_ID, True, True, id="failed"),
        pytest.param(COMPLETED_ID, False, False, id="completed"),
        pytest.param(REFUNDED_ID, False, False, id="refunded"),
        pytest.param(YOOMONEY_ID, False, False, id="other-gateway"),
        pytest.param(uuid.UUID("2419a771-000f-5000-9000-00000000ffff"), False, False, id="unknown"),
    ],
)
async def test_real_read_decides_whether_api_is_asked(world, pg, monkeypatch, pid, asked, credited) -> None:
    """Весь путь с настоящим SQL: до API доходят только наши открытые счета ЮKassa."""
    monkeypatch.setattr(patch, "_read_invoice", REAL_READ_INVOICE)
    world.sessions = pg.good
    api = answer(world, by_url("succeeded"))

    code = await world.deliver(webhook("succeeded", pid=pid))

    assert code == 200
    assert world.errors == []
    assert len(api.requests) == (1 if asked else 0)
    assert world.queued == ([(pid, COMPLETED, YOOKASSA)] if credited else [])


async def test_real_invoice_amount_is_what_the_payment_is_checked_against(world, pg, monkeypatch) -> None:
    monkeypatch.setattr(patch, "_read_invoice", REAL_READ_INVOICE)
    world.sessions = pg.good
    answer(world, by_url("succeeded", value="928.99"))
    code = await world.deliver(webhook("succeeded", pid=PENDING_ID))
    assert (world.queued, code) == ([], 200)
    assert any(isinstance(e, patch.Rejected) and "928.99" in str(e) for e in world.errors)


async def test_real_database_error_is_not_swallowed(world, pg, monkeypatch) -> None:
    """База отвечает, но чтение падает (нет таблицы): 503 и ошибка, а не «счёта нет»."""
    monkeypatch.setattr(patch, "_read_invoice", REAL_READ_INVOICE)
    world.sessions = pg.empty
    api = answer(world, by_url("succeeded"))
    code = await world.deliver(webhook("succeeded", pid=PENDING_ID))
    assert world.queued == []
    assert code == 503, "ошибка базы превратилась в тихое «счёта нет»"
    assert world.errors
    assert api.requests == []


async def test_forged_webhook_costs_no_second_database_connection(world, pg, monkeypatch) -> None:
    """Вебхук не должен требовать ВТОРОГО соединения с базой.

    База ещё до handle_webhook читает шлюз сессией запроса и держит её соединение до
    конца запроса. Первая версия правки брала для счёта второе соединение из пула,
    то есть каждому запросу их нужно было два одновременно: под нагрузкой пул
    кончается, вебхуки получают 503, а кабинет встаёт в очередь к базе. На пуле из
    ОДНОГО соединения это видно сразу: второе не выдать — ждём pool_timeout и падаем.
    """
    monkeypatch.setattr(patch, "_read_invoice", REAL_READ_INVOICE)
    engine = create_async_engine(
        DSN,
        pool_size=1,
        max_overflow=0,
        pool_timeout=2,
        connect_args={"server_settings": {"search_path": SCHEMA_NAME}},
    )
    try:
        world.sessions = async_sessionmaker(engine, expire_on_commit=False)
        world.base_reads_gateway = True
        answer(world, by_url("succeeded"))

        started = time.monotonic()
        code = await world.deliver(webhook("succeeded", pid=PENDING_ID))
        spent = time.monotonic() - started

        assert (code, world.errors) == (200, []), "второе соединение не выдалось — пул исчерпан"
        assert world.queued == [(PENDING_ID, COMPLETED, YOOKASSA)]
        assert spent < 1.5, f"вебхук ждал соединение {spent:.1f} с — значит просил второе"
    finally:
        await engine.dispose()


async def test_when_all_checks_are_busy_we_ask_for_retry_instead_of_queueing(world, monkeypatch) -> None:
    """Очередь к API не копится: лишний вебхук сразу просит повторить.

    Ждать места нельзя: ждущий держит соединение базы (счёт читается сессией
    запроса), и очередь из подделок по чужому открытому счёту заняла бы пул базы
    целиком — вместе с кабинетом. Ожидание места в пуле httpx для этого не годится:
    там таймер начинается заново на каждой раздаче соединения, и очередь растёт молча.
    """
    # Занимаем все места проверки — как поток, который уже идёт к API.
    held = [patch._CHECKS for _ in range(patch.CHECK_CONNECTIONS)]
    for _ in held:
        await patch._CHECKS.acquire()
    try:
        api = answer(world, by_url("succeeded"))
        code = await world.deliver(webhook("succeeded"))
    finally:
        for _ in held:
            patch._CHECKS.release()

    assert code == 503, "вебхук должен уйти на повтор, а не встать в очередь"
    assert api.requests == [], "занятая проверка не должна ходить в API"
    assert world.queued == [], "оплата не проводится"
    assert world.errors == [], "это защита от потока, а не сбой — владельца не зовём"


async def test_normal_webhook_passes_when_checks_are_free(world) -> None:
    """Контроль к предыдущему тесту: со свободными местами всё работает как обычно."""
    answer(world, by_url("succeeded"))
    assert await world.deliver(webhook("succeeded")) == 200
    assert world.queued and world.errors == []


async def test_invoice_read_releases_the_connection_before_going_to_api(world, monkeypatch) -> None:
    """Соединение базы отпускается ДО похода в API.

    Иначе оно стоит занятым все секунды запроса к ЮKassa, и поток подделок по
    чужому открытому счёту выбирает пул базы.
    """
    released: list[str] = []

    real_read = patch._read_invoice

    async def read(session, payment_id):  # noqa: ANN001, ANN202
        original_rollback = session.rollback

        async def spy() -> None:
            released.append("rollback")
            await original_rollback()

        monkeypatch.setattr(session, "rollback", spy, raising=False)
        return await real_read(session, payment_id)

    monkeypatch.setattr(patch, "_read_invoice", read)

    def api_check(request: httpx.Request) -> httpx.Response:
        assert released, "к API пошли, не отпустив соединение базы"
        return api_payment("succeeded", pid=request.url.path.rsplit("/", 1)[1])

    answer(world, api_check)
    assert await world.deliver(webhook("succeeded")) == 200
