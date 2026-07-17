"""Периодический снимок HWID-устройств пользователей (overlay).

В нашей БД устройств нет — берём их из Remnawave ОДНИМ глобальным запросом
`GET /hwid/devices?size=…` (все устройства сразу) и раскладываем в overlay-таблицу
hwid_devices. По этим данным детект абьюза (admin/abuse.py) ловит сигнал «один HWID →
разные аккаунты» — он не зависит от IP, поэтому ловит и тех, кто регистрировался/
подключался ПОД нашим VPN (их cabinet-IP = IP ноды, см. [[login-ip-is-tunnel-exit]]).

Почему прямой httpx, а не SDK: у SDK `get_hwid_users(size=…)` передаёт size как тело
(AttributeBody), панель на GET его игнорирует и отдаёт лишь дефолтную страницу (~25).
Query-параметр `?size=` работает надёжно (клиента строим как node_health._fetch_nodes).

Почему глобальный вызов, а не обход по юзерам: (1) один запрос вместо сотен —
не долбим панель; (2) охватывает ВСЕХ, у кого панель знает устройство, включая тех,
у кого уже нет ТЕКУЩЕЙ подписки (истёкшие триалы — как раз мультиаккаунтеры).

Best-effort: при ошибке/пустом ответе НЕ трогаем существующий снимок.
"""

from typing import Any

from dishka.integrations.taskiq import FromDishka, inject
from httpx import AsyncClient, Timeout
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import AppConfig
from src.infrastructure.taskiq.broker import broker

_PAGE = 1000  # устройств немного (сотни) — одной страницы обычно хватает.


async def _fetch_all_devices(config: AppConfig) -> list[dict[str, Any]]:
    """Все HWID-устройства панели (пагинация по ?start/?size). Клиент — как в node_health."""
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

    devices: list[dict[str, Any]] = []
    async with AsyncClient(
        base_url=f"{c.url.get_secret_value()}/api",
        headers=headers,
        cookies=c.cookies,
        verify=True,
        timeout=Timeout(connect=15, read=30, write=10, pool=5),
    ) as cl:
        start = 0
        while True:
            r = await cl.get("/hwid/devices", params={"size": _PAGE, "start": start})
            if r.status_code != 200:
                logger.warning(f"abuse_hwid: /hwid/devices вернул {r.status_code}")
                break
            resp = r.json().get("response", {}) or {}
            batch = resp.get("devices", []) or []
            if not batch:
                break
            devices.extend(batch)
            total = int(resp.get("total", 0) or 0)
            start += len(batch)
            if start >= total or len(batch) < _PAGE:
                break
    return devices


@broker.task(schedule=[{"cron": "17 */6 * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def snapshot_hwid_devices(
    session: FromDishka[AsyncSession],
    config: FromDishka[AppConfig],
) -> None:
    try:
        devices = await _fetch_all_devices(config)
    except Exception as e:
        logger.warning(f"abuse_hwid: не удалось получить устройства с панели: {e}")
        return

    if not devices:
        logger.info("abuse_hwid: панель вернула 0 устройств — снимок не меняем")
        return

    # Карта remna-uuid → наш user_id (только обычные пользователи).
    uuid_rows = (
        await session.execute(
            text(
                "SELECT DISTINCT s.user_remna_id, s.user_id "
                "FROM subscriptions s JOIN users u ON u.id = s.user_id "
                "WHERE u.role = 'USER' AND s.user_remna_id IS NOT NULL"
            )
        )
    ).all()
    uuid_to_uid: dict[str, int] = {str(ru): uid for ru, uid in uuid_rows}

    # Полный пере-снимок: подменяем таблицу текущим состоянием панели.
    rows: list[dict] = []
    unmapped = 0
    for d in devices:
        uid = uuid_to_uid.get(str(d.get("userUuid") or ""))
        hwid = (d.get("hwid") or "").strip()
        if not hwid:
            continue
        if uid is None:
            unmapped += 1
            continue
        model = (d.get("deviceModel") or "").strip() or None
        platform = (d.get("platform") or "").strip() or None
        rows.append(
            {
                "u": uid,
                "h": hwid[:256],
                "m": model[:128] if model else None,
                "p": platform[:64] if platform else None,
            }
        )

    await session.execute(text("DELETE FROM hwid_devices"))
    for r in rows:
        await session.execute(
            text(
                "INSERT INTO hwid_devices (user_id, hwid, device_model, platform, updated_at) "
                "VALUES (:u, :h, :m, :p, now()) ON CONFLICT (user_id, hwid) DO UPDATE "
                "SET device_model = EXCLUDED.device_model, platform = EXCLUDED.platform, updated_at = now()"
            ),
            r,
        )
    await session.commit()

    logger.info(
        f"abuse_hwid: снимок готов — устройств {len(devices)}, записано {len(rows)}, "
        f"без нашего аккаунта {unmapped}, юзеров {len({r['u'] for r in rows})}"
    )
