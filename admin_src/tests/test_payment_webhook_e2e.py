"""E2E против ЖИВОГО стека: отклонение ПОДДЕЛЬНЫХ платёжных webhook (анти-форжери подписи).

Ядро идемпотентности платежей (атомарный CAS-переход статуса) покрыто юнит-тестом
`test_payment_idempotency.py`. Здесь — транспорт/подпись: webhook без валидной подписи
ОБЯЗАН быть отклонён (403) и НЕ менять состояние. Валидный путь (провижининг подписки/
начисление баланса) на проде НЕ гоняем намеренно — он меняет состояние реальных
аккаунтов; подпись подделать нельзя (нет секрета шлюза), поэтому все запросы ниже
безопасны: random payment_id + заведомо неверная подпись → шлюз режет до обработки.

Активные HTTP-шлюзы прода: YOOMONEY (HMAC-SHA256, поле `sign`), VALUTIX (HMAC-SHA512,
заголовок `X-Signature`). Telegram Stars — не HTTP-webhook, вне scope.

Запуск (opt-in, нужен доступ к web):
  RS_INTEGRATION_URL=http://remnashop-web:5000 pytest tests/test_payment_webhook_e2e.py -v
"""

import os
import uuid

import pytest

httpx = pytest.importorskip("httpx")

BASE = os.environ.get("RS_INTEGRATION_URL")
pytestmark = pytest.mark.skipif(not BASE, reason="RS_INTEGRATION_URL не задан — нет доступа к web")

WEBHOOK = "/api/v1/payments"


async def _post(path: str, content: str, headers: dict) -> httpx.Response:
    async with httpx.AsyncClient(base_url=BASE, timeout=15) as c:
        return await c.post(f"{WEBHOOK}{path}", content=content, headers=headers)


@pytest.mark.asyncio
async def test_yoomoney_forged_signature_rejected():
    """YooMoney webhook с НЕВЕРНЫМ `sign` → 403 (HMAC не сошёлся, обработки нет)."""
    pid = str(uuid.uuid4())  # несуществующий payment_id — даже при чуде обхода нечего трогать
    body = (
        f"notification_type=p2p-incoming&operation_id=op-{pid}"
        f"&amount=100.00&currency=643&datetime=2026-07-26T00:00:00Z"
        f"&sender=&codepro=false&label={pid}&sha1_hash=deadbeef"
        f"&sign=0000000000000000000000000000000000000000000000000000000000000000"
    )
    r = await _post("/yoomoney", body, {"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 403, f"поддельный YooMoney webhook должен быть 403, а не {r.status_code}"


@pytest.mark.asyncio
async def test_yoomoney_missing_signature_rejected():
    """YooMoney webhook БЕЗ `sign` → 403 (fail-closed: нет подписи = отказ)."""
    pid = str(uuid.uuid4())
    body = f"notification_type=p2p-incoming&operation_id=op-{pid}&amount=100.00&label={pid}"
    r = await _post("/yoomoney", body, {"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 403, f"YooMoney webhook без подписи должен быть 403, а не {r.status_code}"


@pytest.mark.asyncio
async def test_valutix_forged_signature_rejected():
    """Valutix webhook с НЕВЕРНЫМ X-Signature → 403 (HMAC-SHA512 не сошёлся)."""
    pid = str(uuid.uuid4())
    body = f'{{"id":"{pid}","status":"COMPLETED","amount":100}}'
    r = await _post(
        "/valutix", body,
        {"Content-Type": "application/json", "X-Signature": "deadbeef" * 16},
    )
    assert r.status_code == 403, f"поддельный Valutix webhook должен быть 403, а не {r.status_code}"


@pytest.mark.asyncio
async def test_valutix_missing_signature_rejected():
    """Valutix webhook БЕЗ X-Signature → 403 (fail-closed)."""
    pid = str(uuid.uuid4())
    body = f'{{"id":"{pid}","status":"COMPLETED"}}'
    r = await _post("/valutix", body, {"Content-Type": "application/json"})
    assert r.status_code == 403, f"Valutix webhook без подписи должен быть 403, а не {r.status_code}"


@pytest.mark.asyncio
async def test_unknown_gateway_rejected():
    """Webhook на несуществующий шлюз → 404 (не 500/200)."""
    r = await _post("/notagateway", "{}", {"Content-Type": "application/json"})
    assert r.status_code == 404, f"неизвестный шлюз должен быть 404, а не {r.status_code}"
