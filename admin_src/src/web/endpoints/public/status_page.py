"""Публичная страница статуса — доступна БЕЗ авторизации (доверие пользователей).

Отдаёт срез по нодам (страна, имя, онлайн, host). Пинг НЕ меряем на сервере —
он в Польше, поэтому «серверный» пинг не отражает реальность для пользователей.
Пинг считается в браузере пользователя (клиентский замер до host ноды), чтобы
показать честную latency с его устройства. host = адрес ноды из панели.
Результат кэшируется на ~30 с, чтобы неавторизованные запросы не долбили панель.
"""

import time
from typing import Any

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter

from src.application.common import Remnawave

router = APIRouter(tags=["Public - Status"])

_CACHE_TTL = 30.0
_cache: dict[str, Any] = {"at": 0.0, "data": None}


async def _fetch(remnawave: Remnawave) -> dict[str, Any]:
    empty: dict[str, Any] = {"nodes": [], "all_operational": True, "total": 0, "online": 0}

    sdk = getattr(remnawave, "sdk", None)
    if sdk is None:
        return empty
    try:
        result = await sdk.nodes.get_all_nodes()
    except Exception:
        return empty

    raw = getattr(result, "root", result) or []
    nodes = []
    for n in raw:
        if getattr(n, "is_disabled", False):
            continue
        nodes.append(
            {
                "name": getattr(n, "name", "") or "",
                "country_code": getattr(n, "country_code", "") or "",
                "online": bool(getattr(n, "is_connected", False)),
                # host — для клиентского (браузерного) замера пинга с устройства юзера
                "host": getattr(n, "address", "") or "",
            }
        )

    online = sum(1 for x in nodes if x["online"])
    return {
        "nodes": nodes,
        "all_operational": (online == len(nodes)) if nodes else True,
        "total": len(nodes),
        "online": online,
    }


@router.get("/status")
@inject
async def public_status(remnawave: FromDishka[Remnawave]) -> dict[str, Any]:
    now = time.monotonic()
    if _cache["data"] is not None and (now - _cache["at"]) < _CACHE_TTL:
        return _cache["data"]

    data = await _fetch(remnawave)
    _cache["at"] = now
    _cache["data"] = data
    return data
