"""Публичная страница статуса — доступна БЕЗ авторизации (доверие пользователей).

Отдаёт срез по нодам (страна, имя, онлайн, host). Пинг НЕ меряем на сервере —
он в Польше, поэтому «серверный» пинг не отражает реальность для пользователей.
Пинг считается в браузере пользователя (клиентский замер до host ноды), чтобы
показать честную latency с его устройства. host = адрес ноды из панели.
Результат кэшируется на ~30 с, чтобы неавторизованные запросы не долбили панель.
"""

import json
import os
import time
from pathlib import Path
from typing import Any

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter

from src.application.common import Remnawave
from src.infrastructure.services.overlay_server_status import load_config

router = APIRouter(tags=["Public - Status"])

_CACHE_TTL = 30.0
_cache: dict[str, Any] = {"at": 0.0, "data": None}

_NODE_HEALTH_PATH = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets")) / "node_health.json"


def _load_uptime() -> dict[str, dict[str, Any]]:
    """История аптайма нод из node_health.json (пишет крон node_health).

    → {node_name: {uptime_30d: float|None, history: [{date, uptime}]}}. Аптайм за
    день = up/total*100 по замерам крона (раз в 10 мин)."""
    out: dict[str, dict[str, Any]] = {}
    try:
        data = json.loads(_NODE_HEALTH_PATH.read_text(encoding="utf-8"))
    except Exception:
        return out
    if not isinstance(data, dict):
        return out
    for name, st in data.items():
        if not isinstance(st, dict):
            continue
        hist = st.get("history")
        if not isinstance(hist, dict) or not hist:
            continue
        total_sum = up_sum = 0
        series: list[dict[str, Any]] = []
        for day in sorted(hist.keys())[-30:]:
            slot = hist.get(day) or {}
            t = int(slot.get("t", 0))
            u = int(slot.get("u", 0))
            if t <= 0:
                continue
            total_sum += t
            up_sum += u
            series.append({"date": day, "uptime": round(u / t * 100, 1)})
        if total_sum > 0:
            out[name] = {"uptime_30d": round(up_sum / total_sum * 100, 1), "history": series}
    return out


async def _fetch(remnawave: Remnawave) -> dict[str, Any]:
    empty: dict[str, Any] = {"nodes": [], "all_operational": True, "total": 0, "online": 0}

    # Блок выключен админом или скрыт для невошедших — публично ничего не отдаём.
    cfg = load_config()
    if not cfg["enabled"] or not cfg["guest_visible"]:
        return empty

    sdk = getattr(remnawave, "sdk", None)
    if sdk is None:
        return empty
    try:
        result = await sdk.nodes.get_all_nodes()
    except Exception:
        return empty

    uptime = _load_uptime()
    raw = getattr(result, "root", result) or []
    nodes = []
    for n in raw:
        if getattr(n, "is_disabled", False):
            continue
        name = getattr(n, "name", "") or ""
        u = uptime.get(name) or {}
        nodes.append(
            {
                "name": name,
                "country_code": getattr(n, "country_code", "") or "",
                "online": bool(getattr(n, "is_connected", False)),
                "uptime_30d": u.get("uptime_30d"),
                "history": u.get("history") or [],
                # ВНИМАНИЕ: host (адрес ноды) публично НЕ отдаём — иначе адрес/через
                # DNS и IP утекал бы любому без входа. Публичный статус — без пинга;
                # host для клиентского пинга отдаётся только владельцу на
                # /subscription/servers.
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
