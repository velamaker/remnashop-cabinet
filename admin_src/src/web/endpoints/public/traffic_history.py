"""История трафика пользователя по дням (за 30 дней) — для графика в кабинете."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter

from src.application.common import Remnawave
from src.application.common.dao import SubscriptionDao

from ._common import CurrentUser

router = APIRouter(prefix="/subscription", tags=["Public - Subscription"])


@router.get("/traffic-history")
@inject
async def traffic_history(
    user: CurrentUser,
    subscription_dao: FromDishka[SubscriptionDao],
    remnawave: FromDishka[Remnawave],
) -> dict:
    """Возвращает {"days": [{"date": "YYYY-MM-DD", "total": bytes}, ...]} за 30 дней."""
    empty: dict = {"days": []}

    current = await subscription_dao.get_current(user.id)
    if not current:
        return empty

    sdk = getattr(remnawave, "sdk", None)
    if sdk is None:
        return empty

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)

    try:
        # Поюзерная разбивка по диапазону отдаёт элементы {date, node_name, total}.
        result = await sdk.bandwidthstats.get_user_usage_legacy_old(
            user_uuid=str(current.user_remna_id),
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
        )
    except Exception:
        return empty

    items = getattr(result, "root", result) or []

    # Суммируем по дате (по всем нодам за день).
    by_date: dict[str, int] = defaultdict(int)
    for it in items:
        date = getattr(it, "date", None)
        if not date:
            continue
        day = str(date)[:10]  # на случай, если придёт ISO-datetime
        by_date[day] += int(getattr(it, "total", 0) or 0)

    days = [{"date": d, "total": by_date[d]} for d in sorted(by_date)]
    return {"days": days}
