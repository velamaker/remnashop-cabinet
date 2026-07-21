"""Публичный статус сервиса — онлайн ли ноды (для страницы статуса в кабинете).

Отдаёт безопасный срез по нодам (страна, имя, онлайн, кол-во онлайн-юзеров) без
IP/секретов. Зовётся залогиненным пользователем; обращение к Remnawave идёт по
токену бота на сервере.
"""

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter

from src.application.common import Remnawave
from src.application.common.dao import SubscriptionDao
from src.infrastructure.services.overlay_server_status import load_config, visible_node_ids

from ._common import CurrentUser

router = APIRouter(prefix="/subscription", tags=["Public - Subscription"])


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


@router.get("/servers")
@inject
async def my_servers(
    user: CurrentUser,
    subscription_dao: FromDishka[SubscriptionDao],
    remnawave: FromDishka[Remnawave],
) -> dict:
    """Серверы для блока «Статус сервиса» в кабинете вошедшего пользователя.

    В отличие от публичного /status, отдаёт host (для клиентского пинга) — но
    только владельцу. При bind_to_subscription показываем ТОЛЬКО ноды сквадов
    активной подписки пользователя; иначе — все ноды панели. Управляется из
    админки (assets/server_status.json).
    """
    cfg = load_config()
    empty: dict = {"enabled": cfg["enabled"], "nodes": [], "all_operational": True, "total": 0, "online": 0}

    if not cfg["enabled"]:
        return empty

    sdk = getattr(remnawave, "sdk", None)
    if sdk is None:
        return empty

    try:
        result = await sdk.nodes.get_all_nodes()
    except Exception:
        return empty

    visible = visible_node_ids(cfg)  # пусто = показываем все
    raw = getattr(result, "root", result) or []
    all_nodes: dict[str, dict] = {}
    for n in raw:
        if getattr(n, "is_disabled", False):
            continue
        node_uuid = str(getattr(n, "uuid", "") or "")
        if not node_uuid:
            continue
        if visible and node_uuid not in visible:
            continue  # админ ограничил список видимых нод
        all_nodes[node_uuid] = {
            "name": getattr(n, "name", "") or "",
            "country_code": getattr(n, "country_code", "") or "",
            "online": bool(getattr(n, "is_connected", False)),
            # host — для клиентского замера пинга; отдаём только владельцу
            "host": getattr(n, "address", "") or "",
        }

    # Привязка по подписке: показываем только ноды, доступные пользователю.
    # Берём их через /users/{uuid}/accessible-nodes (точная ручка Remnawave).
    #
    # ПРИВАТНОСТЬ: host (адрес ноды) отдаём ТОЛЬКО тем, чей список доступных нод
    # конкретно подтверждён этой ручкой. Если подтвердить не удалось (пустой ответ
    # у истёкшего/без активных сквадов ИЛИ сбой API) — НЕ раскрываем чужие ноды с
    # адресами: деградируем до host-less списка (как на публичном статусе). Раньше
    # тут был фолбэк «показать ВСЕ ноды», из-за чего части юзеров утекали хосты.
    if cfg["bind_to_subscription"]:
        subscription = await subscription_dao.get_current(user.id)
        if subscription is None:
            nodes = []  # нет активной подписки → серверов не показываем
        else:
            allowed: set[str] = set()
            resolved = False  # удалось ли достоверно получить список доступных нод
            try:
                acc = await sdk.users.get_user_accessible_nodes(subscription.user_remna_id)
                for n in getattr(acc, "nodes", None) or []:
                    allowed.add(str(getattr(n, "uuid", "") or ""))
                resolved = True
            except Exception:
                resolved = False

            if resolved and allowed:
                # Точный список получен — только эти ноды, с host (для пинга).
                nodes = [v for k, v in all_nodes.items() if k in allowed]
            else:
                # Не подтвердили доступ (пусто/сбой) — показываем ноды БЕЗ host,
                # чтобы блок не был пустым, но адреса чужих нод не утекали.
                nodes = [{**v, "host": ""} for v in all_nodes.values()]
    else:
        nodes = list(all_nodes.values())

    online = sum(1 for x in nodes if x["online"])
    return {
        "enabled": True,
        "nodes": nodes,
        "all_operational": (online == len(nodes)) if nodes else True,
        "total": len(nodes),
        "online": online,
    }
