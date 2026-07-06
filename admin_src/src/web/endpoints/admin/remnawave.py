import json
from typing import Any

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException

from src.application.common import Remnawave
from src.web.endpoints.public.appearance import ASSETS_DIR

from ._common import AdminUser
from ._redact import is_readonly_admin, redact_host, redact_inbound, redact_node

router = APIRouter(prefix="/remnawave", tags=["Admin - RemnaWave"])

# Мониторинг серта (taskiq node_health.py) пишет cert_days на ноду в этот файл.
NODE_HEALTH_PATH = ASSETS_DIR / "node_health.json"


def _load_cert_health() -> dict[str, dict[str, Any]]:
    """name → {cert_days, checked_at} из node_health.json. Нет файла — пусто."""
    try:
        data = json.loads(NODE_HEALTH_PATH.read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        return {}


def _sdk(remnawave: Remnawave):
    if hasattr(remnawave, "sdk"):
        return remnawave.sdk  # type: ignore[attr-defined]
    raise HTTPException(status_code=500, detail="RemnaWave SDK недоступен")


def _unpack(result: Any) -> list:
    """Unpack RootModel (list) or regular object."""
    if result is None:
        return []
    if isinstance(result, list):
        return result
    # RootModel — has .root attribute
    if hasattr(result, "root"):
        r = result.root
        return r if isinstance(r, list) else ([] if r is None else [r])
    # Pydantic model with .response
    if hasattr(result, "response"):
        r = result.response
        return r if isinstance(r, list) else []
    return []


def _dump(obj: Any) -> Any:
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
    return obj


# ─── System info ─────────────────────────────────────────────────────────────

@router.get("/system")
@inject
async def get_system_info(
    _admin: AdminUser,
    remnawave: FromDishka[Remnawave],
) -> dict[str, Any]:
    try:
        sdk = _sdk(remnawave)
        metadata = await sdk.system.get_metadata()
        stats = await sdk.system.get_stats()
        return {
            "metadata": _dump(metadata),
            "stats": _dump(stats),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"RemnaWave error: {e}")


# ─── Nodes ────────────────────────────────────────────────────────────────────

@router.get("/nodes")
@inject
async def get_nodes(
    admin: AdminUser,
    remnawave: FromDishka[Remnawave],
) -> dict[str, Any]:
    try:
        sdk = _sdk(remnawave)
        result = await sdk.nodes.get_all_nodes()
        nodes = [_dump(n) for n in _unpack(result)]
        if is_readonly_admin(admin):
            nodes = [redact_node(n) for n in nodes]
        # Подмешиваем срок серта (из мониторинга node_health), матчим по имени ноды.
        cert = _load_cert_health()
        for n in nodes:
            info = cert.get(n.get("name")) if isinstance(n, dict) else None
            if info:
                n["cert_days"] = info.get("cert_days")
                n["cert_checked_at"] = info.get("checked_at")
        return {"nodes": nodes}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"RemnaWave error: {e}")


@router.post("/nodes/{node_uuid}/restart")
@inject
async def restart_node(
    node_uuid: str,
    _admin: AdminUser,
    remnawave: FromDishka[Remnawave],
) -> dict[str, Any]:
    try:
        sdk = _sdk(remnawave)
        await sdk.nodes.restart_node(node_uuid=node_uuid)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"RemnaWave error: {e}")


@router.post("/nodes/restart-all")
@inject
async def restart_all_nodes(
    _admin: AdminUser,
    remnawave: FromDishka[Remnawave],
) -> dict[str, Any]:
    try:
        sdk = _sdk(remnawave)
        await sdk.nodes.restart_all_nodes()
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"RemnaWave error: {e}")


@router.post("/nodes/{node_uuid}/enable")
@inject
async def enable_node(
    node_uuid: str,
    _admin: AdminUser,
    remnawave: FromDishka[Remnawave],
) -> dict[str, Any]:
    try:
        sdk = _sdk(remnawave)
        await sdk.nodes.enable_node(node_uuid=node_uuid)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"RemnaWave error: {e}")


@router.post("/nodes/{node_uuid}/disable")
@inject
async def disable_node(
    node_uuid: str,
    _admin: AdminUser,
    remnawave: FromDishka[Remnawave],
) -> dict[str, Any]:
    try:
        sdk = _sdk(remnawave)
        await sdk.nodes.disable_node(node_uuid=node_uuid)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"RemnaWave error: {e}")


# ─── Hosts ────────────────────────────────────────────────────────────────────

@router.get("/hosts")
@inject
async def get_hosts(
    admin: AdminUser,
    remnawave: FromDishka[Remnawave],
) -> dict[str, Any]:
    try:
        sdk = _sdk(remnawave)
        result = await sdk.hosts.get_all_hosts()
        hosts = [_dump(h) for h in _unpack(result)]
        if is_readonly_admin(admin):
            hosts = [redact_host(h) for h in hosts]
        return {"hosts": hosts}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"RemnaWave error: {e}")


# ─── Inbounds ─────────────────────────────────────────────────────────────────

@router.get("/inbounds")
@inject
async def get_inbounds(
    admin: AdminUser,
    remnawave: FromDishka[Remnawave],
) -> dict[str, Any]:
    try:
        sdk = _sdk(remnawave)
        result = await sdk.inbounds.get_all_inbounds()
        d = _dump(result)
        # inbounds response: {'total': N, 'inbounds': [...]}
        if isinstance(d, dict) and "inbounds" in d:
            inbounds = d["inbounds"]
        else:
            inbounds = [_dump(i) for i in _unpack(result)]
        if is_readonly_admin(admin):
            inbounds = [redact_inbound(i) for i in inbounds]
        return {"inbounds": inbounds}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"RemnaWave error: {e}")
