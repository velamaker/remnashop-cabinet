"""Публичный статус сервиса — онлайн ли ноды (для страницы статуса в кабинете).

Отдаёт безопасный срез по нодам (страна, имя, онлайн, кол-во онлайн-юзеров) без
IP/секретов. Зовётся залогиненным пользователем; обращение к Remnawave идёт по
токену бота на сервере.
"""

from datetime import datetime, timezone

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Remnawave
from src.application.common.dao import SubscriptionDao
from src.core.enums import Role
from src.infrastructure.services.overlay_server_status import load_config, visible_node_ids

from ._common import CurrentUser

router = APIRouter(prefix="/subscription", tags=["Public - Subscription"])


def _is_service_host(remark: str, keywords: list[str]) -> bool:
    """Хост-заглушка (не реальный сервер) — если в названии есть слово из списка."""
    r = (remark or "").lower()
    return any(kw.lower() in r for kw in keywords if kw)


def _clean_remark(s: str) -> str:
    """Убираем ведущие флаги/эмодзи из remark хоста — флаг рисуется отдельно по
    country_code, иначе выходит двойной флаг. Режем всё до первой буквы/цифры."""
    s = (s or "").strip()
    for idx, ch in enumerate(s):
        if ch.isalnum():
            return s[idx:].strip() or s
    return s


def _is_staff(user) -> bool:
    """Сотрудник (видит всё): PREVIEW и выше (PREVIEW/ADMIN/DEV/OWNER/SYSTEM)."""
    role = getattr(user, "role", None)
    return getattr(role, "value", 0) >= Role.PREVIEW.value


@router.get("/service-status")
@inject
async def service_status(
    user: CurrentUser,
    remnawave: FromDishka[Remnawave],
) -> dict:
    empty: dict = {"nodes": [], "all_operational": True}

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
            continue  # отключённые админом ноды в публичный статус не показываем
        nodes.append(
            {
                "name": getattr(n, "name", "") or "",
                "country_code": getattr(n, "country_code", "") or "",
                "online": bool(getattr(n, "is_connected", False)),
            }
        )

    all_operational = all(x["online"] for x in nodes) if nodes else True
    return {"nodes": nodes, "all_operational": all_operational}


async def _current_sub_state(session: AsyncSession, user_id: int) -> tuple[bool, bool]:
    """(has_sub, active) — есть ли у юзера подписка вообще и активна ли она сейчас."""
    row = (
        await session.execute(
            text(
                "SELECT s.expire_at, s.status "
                "FROM users u JOIN subscriptions s ON u.current_subscription_id = s.id "
                "WHERE u.id = :uid"
            ),
            {"uid": user_id},
        )
    ).first()
    if not row:
        return (False, False)
    now = datetime.now(timezone.utc)
    active = bool(row[1] == "ACTIVE" and row[0] and row[0] > now)
    return (True, active)


def _finalize(items: list[dict]) -> dict:
    online = sum(1 for x in items if x["online"])
    return {
        "enabled": True,
        "nodes": items,
        "all_operational": (online == len(items)) if items else True,
        "total": len(items),
        "online": online,
    }


@router.get("/servers")
@inject
async def my_servers(
    user: CurrentUser,
    subscription_dao: FromDishka[SubscriptionDao],
    remnawave: FromDishka[Remnawave],
    session: FromDishka[AsyncSession],
) -> dict:
    """Серверы для блока «Статус сервиса» в кабинете вошедшего пользователя.

    Что показываем зависит от состояния пользователя:
      • сотрудник (админ/овнер) — ВСЕ реальные хосты (Poland H/WS/X…), с адресами;
      • активная подписка — свои хосты (по сквадам), заглушки скрыты, с адресами;
      • подписка кончилась — хосты-заглушки (ПОДПИСКА ЗАКОНЧИЛАСЬ/Продлите/Резерв…);
      • нет подписки (новый) — ноды/страны (что серверы есть), без адресов.

    Хост-заглушки определяются по словам из настройки service_keywords (админка).
    Связи надёжны (accessible-nodes бывает пустым даже у активных):
      host.inbound_uuid ↔ node.config_profile.active_inbounds[].uuid ↔ squad.inbounds.
    """
    cfg = load_config()
    empty: dict = {"enabled": cfg["enabled"], "nodes": [], "all_operational": True, "total": 0, "online": 0}

    if not cfg["enabled"]:
        return empty

    sdk = getattr(remnawave, "sdk", None)
    if sdk is None:
        return empty

    is_staff = _is_staff(user)
    keywords: list[str] = cfg.get("service_keywords") or []

    # 1) Ноды → карта inbound_uuid -> [(country_code, online)] + список нод (для фолбэка).
    try:
        nres = await sdk.nodes.get_all_nodes()
    except Exception:
        return empty
    visible = visible_node_ids(cfg)  # пусто = все
    inbound_nodes: dict[str, list[tuple[str, bool]]] = {}
    node_items: list[dict] = []  # ноды/страны без адресов — для новых юзеров и фолбэка
    for n in getattr(nres, "root", nres) or []:
        if getattr(n, "is_disabled", False):
            continue
        nuuid = str(getattr(n, "uuid", "") or "")
        if visible and nuuid not in visible:
            continue  # админ ограничил список видимых нод
        cc = getattr(n, "country_code", "") or ""
        conn = bool(getattr(n, "is_connected", False))
        node_items.append({
            "name": getattr(n, "name", "") or "",
            "country_code": cc,
            "online": conn,
            "host": "",  # ноды показываем без адреса (приватно)
        })
        cp = getattr(n, "config_profile", None)
        for inb in getattr(cp, "active_inbounds", None) or []:
            iu = str(getattr(inb, "uuid", "") or "")
            if iu:
                inbound_nodes.setdefault(iu, []).append((cc, conn))

    # 2) Хосты панели → делим на реальные и сервисные (заглушки).
    try:
        hres = await sdk.hosts.get_all_hosts()
    except Exception:
        hres = None
    hraw = getattr(hres, "root", None)
    if hraw is None:
        hraw = getattr(hres, "response", None)
    if hraw is None:
        hraw = hres or []
    try:
        hlist = list(hraw)
    except Exception:
        hlist = []

    def build_host(h, reveal: bool) -> dict | None:
        if getattr(h, "is_disabled", False):
            return None
        iu = str(getattr(h, "inbound_uuid", "") or "")
        serving = inbound_nodes.get(iu)
        if not serving:
            return None  # inbound не на видимой/включённой ноде
        online = any(conn for (_cc, conn) in serving)
        cc = next((c for (c, conn) in serving if conn and c), "") or (serving[0][0] if serving else "")
        return {
            "name": _clean_remark(getattr(h, "remark", "") or ""),
            "country_code": cc,
            "online": online,
            "host": (getattr(h, "address", "") or "") if reveal else "",
            "_inbound": iu,
            "_service": _is_service_host(getattr(h, "remark", "") or "", keywords),
        }

    # --- Сотрудник: все РЕАЛЬНЫЕ хосты с адресами (заглушки не нужны). ---
    if is_staff:
        items = []
        for h in hlist:
            b = build_host(h, reveal=True)
            if b and not b["_service"]:
                items.append({k: v for k, v in b.items() if not k.startswith("_")})
        return _finalize(items)

    # --- Обычный пользователь: зависит от состояния подписки. ---
    has_sub, active = await _current_sub_state(session, user.id)

    if active:
        # Свои хосты по подписке (сквады), заглушки скрыты, адреса отдаём (это его сеть).
        allowed_inbounds: set[str] | None = None
        if cfg["bind_to_subscription"]:
            subscription = await subscription_dao.get_current(user.id)
            squad_ids = {str(s) for s in (getattr(subscription, "internal_squads", None) or [])} if subscription else set()
            allowed_inbounds = set()
            if squad_ids:
                try:
                    sres = await sdk.internal_squads.get_internal_squads()
                    for sq in getattr(sres, "internal_squads", None) or []:
                        if str(getattr(sq, "uuid", "") or "") in squad_ids:
                            for inb in getattr(sq, "inbounds", None) or []:
                                iu = str(getattr(inb, "uuid", inb) or "")
                                if iu:
                                    allowed_inbounds.add(iu)
                except Exception:
                    allowed_inbounds = set()  # не смогли — безопасный фолбэк: ноды ниже
        if allowed_inbounds is not None and not allowed_inbounds:
            # Привязка включена, но сквады/инбаунды не вычислили — безопасный фолбэк: ноды.
            return _finalize(node_items)
        items = []
        for h in hlist:
            b = build_host(h, reveal=True)
            if not b or b["_service"]:
                continue
            if allowed_inbounds is not None and b["_inbound"] not in allowed_inbounds:
                continue
            items.append({k: v for k, v in b.items() if not k.startswith("_")})
        return _finalize(items or node_items)

    if has_sub:
        # Подписка кончилась → показываем хосты-заглушки (продление/резерв и т.п.).
        items = []
        for h in hlist:
            b = build_host(h, reveal=True)
            if b and b["_service"]:
                items.append({k: v for k, v in b.items() if not k.startswith("_")})
        return _finalize(items or node_items)

    # Новый пользователь без подписки → просто ноды/страны (что серверы есть).
    return _finalize(node_items)
