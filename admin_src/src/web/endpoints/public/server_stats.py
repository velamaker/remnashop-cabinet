"""Статистика по серверам пользователя — «любимый сервер» (топ-нода по трафику)."""

from datetime import datetime, timedelta, timezone

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter

from src.application.common import Remnawave
from src.application.common.dao import SubscriptionDao

from ._common import CurrentUser

router = APIRouter(prefix="/subscription", tags=["Public - Subscription"])


@router.get("/server-stats")
@inject
async def server_stats(
    user: CurrentUser,
    subscription_dao: FromDishka[SubscriptionDao],
    remnawave: FromDishka[Remnawave],
) -> dict:
    empty = {"favorite": None, "nodes": []}

    current = await subscription_dao.get_current(user.id)
    if not current:
        return empty

    sdk = getattr(remnawave, "sdk", None)
    if sdk is None:
        return empty

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)

    try:
        result = await sdk.bandwidthstats.get_stats_user_usage(
            uuid=str(current.user_remna_id),
            top_nodes_limit=5,
            # RemnaWave принимает дату в формате YYYY-MM-DD.
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
        )
    except Exception:
        return empty

    data = getattr(result, "root", result)
    top_nodes = getattr(data, "top_nodes", None) or []

    nodes = [
        {
            "name": n.name,
            "country_code": getattr(n, "country_code", "") or "",
            "total": int(getattr(n, "total", 0) or 0),
        }
        for n in top_nodes
    ]
    nodes.sort(key=lambda x: x["total"], reverse=True)

    return {"favorite": nodes[0] if nodes else None, "nodes": nodes}
