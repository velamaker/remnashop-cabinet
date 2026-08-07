"""[OVERLAY] Блок статистики для стартового уведомления бота.

Владелец захотел, чтобы при старте приходила сводка, как у бота «Бедолага»:
сколько пользователей, подписок, открытых тикетов и жива ли панель. Базовое
событие BotStartupEvent отдаёт только сборку и режим доступности, а трогать
`lifespan.py` ради этого не хочется — поэтому строки дописываются к уже
отрендеренному тексту в notification.py.

Всё максимально безопасно для доставки: свой короткий коннект к БД, жёсткие
таймауты, любая ошибка → просто нет блока, уведомление уходит как раньше.
Результат кэшируется на минуту — при старте уведомление уходит каждому админу,
и одни и те же счётчики незачем считать по разу на каждого.
"""

import asyncio
import os
import time
from typing import Any, Optional

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

_CACHE_TTL_SEC = 60
_DB_TIMEOUT_SEC = 5
_PANEL_TIMEOUT_SEC = 4

_cache: dict[str, object] = {"at": 0.0, "block": None}

_STATS_SQL = """
SELECT
    (SELECT count(*) FROM users)                                          AS users,
    (SELECT count(*) FROM subscriptions
      WHERE status::text = 'ACTIVE' AND is_trial = false)                 AS paid,
    (SELECT count(*) FROM subscriptions
      WHERE status::text = 'ACTIVE' AND is_trial = true)                  AS trial,
    (SELECT count(*) FROM support_tickets
      WHERE status::text <> 'closed')                                     AS tickets,
    (SELECT coalesce(sum(cabinet_balance), 0) FROM users)                 AS balance
"""


def _db_url() -> Optional[str]:
    """DSN как у приложения: те же DATABASE_*-переменные и те же дефолты."""
    from urllib.parse import quote_plus  # noqa: PLC0415

    password = os.environ.get("DATABASE_PASSWORD")
    if not password:
        return None

    host = os.environ.get("DATABASE_HOST") or "remnashop-db"
    port = os.environ.get("DATABASE_PORT") or "5432"
    user = os.environ.get("DATABASE_USER") or "remnashop"
    name = os.environ.get("DATABASE_NAME") or "remnashop"
    return (
        f"postgresql+asyncpg://{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{port}/{name}"
    )


async def _panel_alive(config: Any) -> Optional[bool]:
    """Жива ли панель Remnawave. None — проверить не смогли (строку не покажем).

    Клиент строим как в node_health.py: без заголовков x-forwarded-* панель
    просто рвёт соединение, «голый» HTTP к ней не работает.
    """
    if config is None:
        return None

    from httpx import AsyncClient, Timeout  # noqa: PLC0415

    try:
        c = config.remnawave
        headers = {
            "Authorization": f"Bearer {c.token.get_secret_value()}",
            "X-Api-Key": c.caddy_token.get_secret_value(),
            "CF-Access-Client-Id": c.cf_client_id.get_secret_value(),
            "CF-Access-Client-Secret": c.cf_client_secret.get_secret_value(),
        }
        if not c.is_external:
            headers["x-forwarded-proto"] = "https"
            headers["x-forwarded-for"] = "127.0.0.1"

        async with AsyncClient(
            base_url=f"{c.url.get_secret_value()}/api",
            headers=headers,
            cookies=c.cookies,
            timeout=Timeout(connect=3, read=_PANEL_TIMEOUT_SEC, write=3, pool=3),
        ) as client:
            response = await client.get("/nodes")
            # Ответ есть — панель жива; 401/403 тоже ответ (значит, поднята).
            return response.status_code < 500
    except Exception:  # noqa: BLE001
        return False


async def _build(config: Any) -> Optional[str]:
    url = _db_url()
    if not url:
        return None

    engine = create_async_engine(url, pool_pre_ping=False, poolclass=None)
    try:
        async with engine.connect() as conn:
            row = (await asyncio.wait_for(conn.execute(text(_STATS_SQL)), _DB_TIMEOUT_SEC)).one()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[OVERLAY] Не собрал статистику для старта: {exc}")
        return None
    finally:
        await engine.dispose()

    balance = float(row.balance or 0)
    balance_str = f"{balance:,.0f}".replace(",", " ") if balance.is_integer() else f"{balance:,.2f}".replace(",", " ")

    lines = [
        "<b>📈 Сейчас в системе</b>:",
        "<blockquote>",
        f"• <b>👥 Пользователей</b>: {int(row.users)}",
        f"• <b>💳 Платных подписок</b>: {int(row.paid)}",
        f"• <b>🎁 Триальных подписок</b>: {int(row.trial)}",
        f"• <b>🎫 Открытых тикетов</b>: {int(row.tickets)}",
        f"• <b>💰 Сумма балансов</b>: {balance_str} ₽",
    ]

    alive = await _panel_alive(config)
    if alive is not None:
        lines.append(f"• <b>🖥 Remnawave</b>: {'🟢 доступна' if alive else '🔴 недоступна'}")

    lines.append("</blockquote>")
    return "\n".join(lines)


async def startup_stats_block(config: Any = None) -> Optional[str]:
    """Готовый HTML-блок для дописывания в стартовое уведомление (или None)."""
    now = time.monotonic()
    if _cache["block"] is not None and now - float(_cache["at"]) < _CACHE_TTL_SEC:  # type: ignore[arg-type]
        return _cache["block"]  # type: ignore[return-value]

    try:
        block = await asyncio.wait_for(_build(config), _DB_TIMEOUT_SEC + _PANEL_TIMEOUT_SEC + 2)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[OVERLAY] Статистика старта пропущена: {exc}")
        return None

    if block:
        _cache["block"] = block
        _cache["at"] = now
    return block
