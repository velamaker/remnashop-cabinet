"""Публичный статус сервиса — онлайн ли ноды (для страницы статуса в кабинете).

Отдаёт безопасный срез по нодам (страна, имя, онлайн, кол-во онлайн-юзеров) без
IP/секретов. Зовётся залогиненным пользователем; обращение к Remnawave идёт по
токену бота на сервере.
"""

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter

from src.application.common import Remnawave

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
