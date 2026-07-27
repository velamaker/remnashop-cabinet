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


def _panel_client(config: AppConfig) -> AsyncClient:
    """httpx-клиент к API панели (как в node_health): токен + служебные заголовки."""
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
    return AsyncClient(
        base_url=f"{c.url.get_secret_value()}/api",
        headers=headers,
        cookies=c.cookies,
        verify=True,
        timeout=Timeout(connect=15, read=30, write=10, pool=5),
    )


async def _fetch_paginated(cl: AsyncClient, path: str, key: str) -> list[dict[str, Any]]:
    """Все элементы `key` из paginated-эндпоинта панели (?start/?size)."""
    items: list[dict[str, Any]] = []
    start = 0
    while True:
        r = await cl.get(path, params={"size": _PAGE, "start": start})
        if r.status_code != 200:
            logger.warning(f"abuse_hwid: {path} вернул {r.status_code}")
            break
        resp = r.json().get("response", {}) or {}
        batch = resp.get(key, []) or []
        if not batch:
            break
        items.extend(batch)
        total = int(resp.get("total", 0) or 0)
        start += len(batch)
        if start >= total or len(batch) < _PAGE:
            break
    return items


async def _fetch_all_devices(config: AppConfig) -> list[dict[str, Any]]:
    """Все HWID-устройства панели."""
    async with _panel_client(config) as cl:
        return await _fetch_paginated(cl, "/hwid/devices", "devices")


async def _fetch_tid_to_uuid(config: AppConfig) -> dict[int, str]:
    """Карта panel numeric id (t_id) → uuid.

    С Remnawave 2.8 в /hwid/devices вместо `userUuid` приходит числовой `userId`
    (это users.t_id — новый PK панели). Локально же подписки хранят uuid
    (user_remna_id), поэтому строим мост t_id → uuid по списку /users.
    """
    async with _panel_client(config) as cl:
        users = await _fetch_paginated(cl, "/users", "users")
    mapping: dict[int, str] = {}
    for u in users:
        uid, uu = u.get("id"), u.get("uuid")
        if uid is not None and uu:
            mapping[int(uid)] = str(uu)
    return mapping


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

    # Remnawave 2.8+: устройство несёт числовой userId (t_id), а не userUuid.
    # Строим мост t_id → uuid только если он нужен (есть device без userUuid).
    tid_to_uuid: dict[int, str] = {}
    if any(d.get("userUuid") is None and d.get("userId") is not None for d in devices):
        try:
            tid_to_uuid = await _fetch_tid_to_uuid(config)
        except Exception as e:
            logger.warning(f"abuse_hwid: не удалось получить карту t_id→uuid: {e}")
        if not tid_to_uuid:
            # Без моста все устройства новой панели «безхозные» → НЕ затираем снимок.
            logger.warning("abuse_hwid: карта t_id→uuid пуста — снимок не меняем")
            return

    def _device_uuid(dev: dict[str, Any]) -> str:
        # Старый контракт (< 2.8) — uuid прямо в устройстве; новый — через t_id.
        uu = dev.get("userUuid")
        if uu:
            return str(uu)
        tid = dev.get("userId")
        return tid_to_uuid.get(int(tid), "") if tid is not None else ""

    # Полный пере-снимок: подменяем таблицу текущим состоянием панели.
    rows: list[dict] = []
    unmapped = 0
    for d in devices:
        uid = uuid_to_uid.get(_device_uuid(d))
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
