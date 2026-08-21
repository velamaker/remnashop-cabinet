"""Админ: резервный доступ истёкшим подпискам (1 ГБ на N дней).

Хранится в assets/reserve.json (см. services/overlay_reserve.py). Тумблер + ГБ
резерва + окно (дней) + опц. отдельный сквад-резерв. Выдачу/окончание делает крон
taskiq/tasks/reserve.py (через Remnawave SDK; ядро не трогаем).
"""

from typing import Any, Optional

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, status
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Remnawave
from src.infrastructure.services.overlay_reserve import load_config, save_config

from ._common import AdminUser

router = APIRouter(prefix="/reserve", tags=["Admin - Reserve"])

_GB = 1024 ** 3


async def _diagnose_squad(remnawave: object, squad_uuid: str) -> dict[str, Any]:
    """Может ли сквад-резерв реально дать человеку сервер — проверка ДО включения.

    Сам по себе выбранный сквад ничего не гарантирует: у него может не быть инбаундов,
    а его хосты могут быть выключены или спрятаны от него (host.excludedInternalSquads).
    Во всех трёх случаях резерв «выдаётся», а в приложении пусто — то есть ровно тот
    отказ, который у владельца никак не проявляется, пока не пожалуется клиент.
    Поэтому ловим его в момент настройки, а не через неделю.

    checked=False — панель недоступна: это НЕ приговор скваду, просто мы не смогли
    посмотреть. Такой ответ не должен ничего блокировать.
    """
    out: dict[str, Any] = {"checked": False, "ok": False, "name": None, "hosts": 0, "problems": []}
    if not squad_uuid:
        out["problems"].append("сквад-резерв не выбран")
        out["checked"] = True
        return out

    sdk = getattr(remnawave, "sdk", None)
    if sdk is None:
        out["problems"].append("панель недоступна — проверить сквад не смогли")
        return out

    want = squad_uuid.strip().lower()
    try:
        squads = getattr(await sdk.internal_squads.get_internal_squads(), "internal_squads", None) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"reserve/squad-check: список сквадов не получен: {exc}")
        out["problems"].append("панель недоступна — проверить сквад не смогли")
        return out

    out["checked"] = True
    squad = next((s for s in squads if str(getattr(s, "uuid", "")).lower() == want), None)
    if squad is None:
        out["problems"].append("такого сквада нет в панели — возможно, он удалён или UUID неверный")
        return out

    out["name"] = getattr(squad, "name", None)
    inbounds = {
        str(getattr(inb, "uuid", inb) or "")
        for inb in (getattr(squad, "inbounds", None) or [])
    } - {""}
    if not inbounds:
        out["problems"].append("у сквада нет ни одного инбаунда — подписка будет пустой")
        return out

    # Хосты: считаем те, что реально отдадутся этому скваду. Логика та же, что в
    # public/service_status.py — хост виден, если он включён, не скрыт, отдаёт инбаунд
    # сквада и сам сквад не в его списке исключений.
    try:
        hres = await sdk.hosts.get_all_hosts()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"reserve/squad-check: хосты не получены: {exc}")
        out["problems"].append("инбаунды у сквада есть, но список хостов панель не отдала")
        return out

    hraw = getattr(hres, "root", None)
    if hraw is None:
        hraw = getattr(hres, "response", None)
    if hraw is None:
        hraw = hres or []
    try:
        hosts = list(hraw)
    except Exception:  # noqa: BLE001
        hosts = []

    visible = 0
    for h in hosts:
        if getattr(h, "is_disabled", False) or getattr(h, "is_hidden", False):
            continue
        if str(getattr(h, "inbound_uuid", "") or "") not in inbounds:
            continue
        excluded = {str(x).lower() for x in (getattr(h, "excluded_internal_squads", None) or [])}
        if want in excluded:
            continue
        visible += 1

    out["hosts"] = visible
    if not visible:
        out["problems"].append(
            "ни один сервер не отдаётся этому скваду — хосты выключены, скрыты "
            "или сквад у них в исключениях"
        )
        return out

    out["ok"] = True
    return out


class ReserveUpdate(BaseModel):
    enabled: Optional[bool] = None
    reserve_gb: Optional[int] = None
    window_days: Optional[int] = None
    squad_uuid: Optional[str] = None


@router.get("")
async def get_reserve(_admin: AdminUser) -> dict[str, Any]:
    return load_config()


@router.get("/squad-check")
@inject
async def squad_check(
    _admin: AdminUser,
    remnawave: FromDishka[Remnawave],
    squad_uuid: str = "",
) -> dict[str, Any]:
    """Вердикт по сквад-резерву для админки: отдаст он серверы или нет.

    Без параметра проверяет тот, что сейчас в настройках, — чтобы страница показывала
    состояние сразу при открытии, а не только после попытки сохранить.
    """
    return await _diagnose_squad(remnawave, squad_uuid or load_config()["squad_uuid"])


@router.put("")
@inject
async def update_reserve(
    body: ReserveUpdate,
    _admin: AdminUser,
    remnawave: FromDishka[Remnawave],
) -> dict[str, Any]:
    current = load_config()
    for field in ("enabled", "reserve_gb", "window_days", "squad_uuid"):
        val = getattr(body, field)
        if val is not None:
            current[field] = val

    # Резерв без рабочего сквада включить нельзя. Смысл фичи — посадить истёкшего на
    # сервер, пускающий в Telegram; без сквада крон не даст ничего, а со сломанным
    # сквадом человек получит «активную» подписку, в которой пусто. Молчаливое
    # «сохранено» тут хуже отказа: тумблер стоит «вкл», а не работает ничего — именно
    # так фича и выглядела сломанной у владельца.
    if current["enabled"]:
        if not current["squad_uuid"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Укажите сквад-резерв: это сервер, который пускает только в Telegram. "
                       "Без него резерв включить нельзя.",
            )
        diagnosis = await _diagnose_squad(remnawave, current["squad_uuid"])
        # checked=False — панель просто не ответила; блокировать настройку из-за этого
        # нельзя, иначе временная недоступность панели запирает админку.
        if diagnosis["checked"] and not diagnosis["ok"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Сквад-резерв не сможет выдать сервер: "
                       + "; ".join(diagnosis["problems"])
                       + ". Поправьте сквад в панели Remnawave и попробуйте снова.",
            )

    saved = save_config(current)
    return saved


@router.get("/grants")
@inject
async def list_grants(
    _admin: AdminUser,
    session: FromDishka[AsyncSession],
    remnawave: FromDishka[Remnawave],
    limit: int = 50,
) -> dict[str, Any]:
    """Кто сейчас на резерве — и работает ли он у них на самом деле.

    Своя таблица reserve_grants говорит только «резерв выдавали». Работает он или
    нет, знает панель: подписка отдаёт серверы через сквады, поэтому ACTIVE без
    сквадов — это «выдан», но в приложении пусто. Именно так дефект и прятался,
    пока состояние не показывали. Панель спрашиваем только про АКТИВНЫЕ резервы
    (их единицы) и по каждому отдельно — недоступность панели не должна ронять
    страницу целиком, поэтому ошибка превращается в пометку у строки.
    """
    limit = max(1, min(200, limit))
    rows = (
        await session.execute(
            text(
                # У человека теперь может быть несколько выдач (резерв положен на каждое
                # истечение) — отдаём id строки, иначе список нечем различать.
                "SELECT r.id, r.user_id, r.remna_uuid, r.granted_at, r.reserve_expire_at, r.ended, "
                "       u.telegram_id, u.username "
                "FROM reserve_grants r JOIN users u ON u.id = r.user_id "
                "ORDER BY r.granted_at DESC LIMIT :n"
            ),
            {"n": limit},
        )
    ).all()

    sdk = getattr(remnawave, "sdk", None)
    items: list[dict[str, Any]] = []
    for grant_id, uid, uuid, granted_at, expire_at, ended, tg_id, username in rows:
        item: dict[str, Any] = {
            "id": grant_id,
            "user_id": uid,
            "telegram_id": tg_id,
            "username": username,
            "remna_uuid": str(uuid),
            "granted_at": granted_at.isoformat() if granted_at else None,
            "reserve_expire_at": expire_at.isoformat() if expire_at else None,
            "ended": bool(ended),
            "panel": None,
            "problem": None,
            "note": None,
        }
        if not ended and sdk is not None:
            try:
                user = await sdk.users.get_user_by_uuid(str(uuid))
            except Exception as exc:  # noqa: BLE001 — панель недоступна лишь для этой строки
                logger.warning(f"reserve/grants: user_id={uid} ({uuid}) не прочитан: {exc}")
                item["problem"] = "панель не ответила"
            else:
                squads = [
                    getattr(s, "name", None) or str(getattr(s, "uuid", ""))
                    for s in (getattr(user, "active_internal_squads", None) or [])
                ]
                limit_bytes = int(getattr(user, "traffic_limit_bytes", 0) or 0)
                used_bytes = int(getattr(user, "used_traffic_bytes", 0) or 0)
                status_now = getattr(user, "status", None)
                status_now = str(getattr(status_now, "value", status_now or ""))
                item["panel"] = {
                    "status": status_now,
                    "squads": squads,
                    "traffic_limit_gb": round(limit_bytes / _GB, 2),
                    "used_traffic_gb": round(used_bytes / _GB, 2),
                }
                # LIMITED при израсходованном лимите — не поломка, а штатный конец
                # резерва («кончился трафик, продлите»); в проблемы его не пишем,
                # иначе владелец будет чинить то, что работает как задумано.
                if not squads:
                    item["problem"] = "нет активных сквадов — в приложении будет пусто"
                elif status_now == "LIMITED" and limit_bytes and used_bytes >= limit_bytes:
                    item["note"] = "резерв израсходован"
                elif status_now != "ACTIVE":
                    item["problem"] = f"статус в панели {status_now}, а не ACTIVE"
        items.append(item)

    return {
        "items": items,
        "active": sum(1 for i in items if not i["ended"]),
        "broken": sum(1 for i in items if i["problem"]),
    }
