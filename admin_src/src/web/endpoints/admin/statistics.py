from datetime import date, timedelta
from typing import Any, Optional

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.dao import SubscriptionDao, TransactionDao, UserDao
from src.application.dto.statistics import GatewayStatsDto, SubscriptionStatsDto

from ._common import AdminUser

router = APIRouter(prefix="/statistics", tags=["Admin - Statistics"])


def _gateway_stats_to_dict(g: GatewayStatsDto) -> dict[str, Any]:
    return {
        "gateway_type": g.gateway_type,
        "total_income": float(g.total_income),
        "daily_income": float(g.daily_income),
        "weekly_income": float(g.weekly_income),
        "monthly_income": float(g.monthly_income),
        "last_month_income": float(g.last_month_income),
        "paid_count": g.paid_count,
        "total_transactions": g.total_transactions,
        "completed_transactions": g.completed_transactions,
        "free_transactions": g.free_transactions,
        "total_discounts": float(g.total_discounts),
    }


@router.get("/overview")
@inject
async def get_overview(
    _admin: AdminUser,
    user_dao: FromDishka[UserDao],
    transaction_dao: FromDishka[TransactionDao],
    subscription_dao: FromDishka[SubscriptionDao],
) -> dict[str, Any]:
    total_users = await user_dao.count()
    active_users = await user_dao.count_active_non_blocked()
    blocked_users = await user_dao.count_blocked()
    new_users_today = await user_dao.count_new(days=1)
    new_users_week = await user_dao.count_new(days=7)
    new_users_month = await user_dao.count_new(days=30)
    users_with_subscription = await user_dao.count_with_active_subscription()
    users_with_expired = await user_dao.count_with_expired_subscription()
    users_without_subscription = await user_dao.count_without_subscription()
    users_with_trial = await user_dao.count_with_trial_subscription()

    total_transactions = await transaction_dao.count_total()
    completed_transactions = await transaction_dao.count_completed()
    paying_users = await transaction_dao.count_paying_users()
    gateway_stats = await transaction_dao.get_gateway_stats()

    sub_stats = await subscription_dao.get_stats()

    return {
        "users": {
            "total": total_users,
            "active": active_users,
            "blocked": blocked_users,
            "new_today": new_users_today,
            "new_week": new_users_week,
            "new_month": new_users_month,
            "with_active_subscription": users_with_subscription,
            "with_expired_subscription": users_with_expired,
            "without_subscription": users_without_subscription,
            "with_trial": users_with_trial,
            "paying": paying_users,
        },
        "transactions": {
            "total": total_transactions,
            "completed": completed_transactions,
            "gateways": [_gateway_stats_to_dict(g) for g in gateway_stats],
        },
        "subscriptions": {
            "total": sub_stats.total,
            "active": sub_stats.total_active,
            "expired": sub_stats.total_expired,
            "disabled": sub_stats.total_disabled,
            "limited": sub_stats.total_limited,
            "trial": sub_stats.active_trial,
            "expiring_soon": sub_stats.expiring_soon,
            "unlimited": sub_stats.total_unlimited,
        },
    }


@router.get("/sales")
@inject
async def get_sales_stats(
    _admin: AdminUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    """Продажи (оплаченные транзакции) за 30/60/90 дней.

    Валюты не суммируются между собой (RUB/USD/XTR-звёзды) — выручка разбита
    по валютам. Данные считаются на лету из таблицы transactions (status=COMPLETED,
    исключая тестовые), поэтому всегда актуальны.
    """
    periods = (30, 60, 90)

    # Один проход по последним 90 дням, условные агрегаты на каждое окно.
    rows = (
        await session.execute(
            text(
                """
                SELECT
                    currency::text AS currency,
                    sum((pricing->>'final_amount')::numeric)
                        FILTER (WHERE created_at >= now() - interval '30 days') AS rev30,
                    count(*) FILTER (WHERE created_at >= now() - interval '30 days') AS cnt30,
                    sum((pricing->>'final_amount')::numeric)
                        FILTER (WHERE created_at >= now() - interval '60 days') AS rev60,
                    count(*) FILTER (WHERE created_at >= now() - interval '60 days') AS cnt60,
                    sum((pricing->>'final_amount')::numeric)
                        FILTER (WHERE created_at >= now() - interval '90 days') AS rev90,
                    count(*) FILTER (WHERE created_at >= now() - interval '90 days') AS cnt90
                FROM transactions
                WHERE status::text = 'COMPLETED'
                  AND is_test = false
                  AND (pricing->>'final_amount')::numeric > 0
                  AND created_at >= now() - interval '90 days'
                GROUP BY currency
                """
            )
        )
    ).all()

    # Собираем в структуру по периодам.
    result: dict[int, dict[str, Any]] = {
        d: {"days": d, "sales_count": 0, "revenue": {}} for d in periods
    }
    for r in rows:
        for d, rev_key, cnt_key in (
            (30, "rev30", "cnt30"),
            (60, "rev60", "cnt60"),
            (90, "rev90", "cnt90"),
        ):
            cnt = getattr(r, cnt_key) or 0
            if cnt == 0:
                continue
            result[d]["sales_count"] += cnt
            rev = float(getattr(r, rev_key) or 0)
            if rev:
                result[d]["revenue"][r.currency] = rev

    return {
        "periods": [
            {
                "days": p["days"],
                "sales_count": p["sales_count"],
                "revenue": [
                    {"currency": cur, "amount": amt}
                    for cur, amt in sorted(p["revenue"].items())
                ],
            }
            for p in (result[d] for d in periods)
        ]
    }


@router.get("/daily")
@inject
async def get_daily_stats(
    _admin: AdminUser,
    session: FromDishka[AsyncSession],
    days: int = Query(30, ge=7, le=90),
) -> dict[str, Any]:
    """Ряды по дням за N дней: регистрации + выручка по валютам.

    Пропуски заполняются нулями (непрерывный ряд для графика). Выручка —
    оплаченные транзакции (COMPLETED, не тест, final_amount>0), валюты не
    суммируются между собой. Считается на лету из users/transactions.
    """
    since = date.today() - timedelta(days=days - 1)

    reg_rows = (
        await session.execute(
            text(
                """
                SELECT created_at::date AS d, count(*) AS c
                FROM users
                WHERE created_at::date >= :since
                GROUP BY 1
                """
            ),
            {"since": since},
        )
    ).all()

    rev_rows = (
        await session.execute(
            text(
                """
                SELECT created_at::date AS d,
                       currency::text AS currency,
                       sum((pricing->>'final_amount')::numeric) AS amt
                FROM transactions
                WHERE status::text = 'COMPLETED'
                  AND is_test = false
                  AND (pricing->>'final_amount')::numeric > 0
                  AND created_at::date >= :since
                GROUP BY 1, 2
                """
            ),
            {"since": since},
        )
    ).all()

    regs_by_day = {r.d: int(r.c) for r in reg_rows}
    rev_by_day: dict[date, dict[str, float]] = {}
    currency_totals: dict[str, float] = {}
    for r in rev_rows:
        amt = float(r.amt or 0)
        rev_by_day.setdefault(r.d, {})[r.currency] = amt
        currency_totals[r.currency] = currency_totals.get(r.currency, 0.0) + amt

    series = []
    for i in range(days):
        d = since + timedelta(days=i)
        series.append(
            {
                "date": d.isoformat(),
                "registrations": regs_by_day.get(d, 0),
                "revenue": rev_by_day.get(d, {}),
            }
        )

    # Валюты по убыванию суммарной выручки — фронт по умолчанию рисует первую.
    currencies = [
        c for c, _ in sorted(currency_totals.items(), key=lambda kv: kv[1], reverse=True)
    ]

    return {"days": days, "currencies": currencies, "series": series}


@router.get("/transactions")
@inject
async def get_transaction_stats(
    _admin: AdminUser,
    transaction_dao: FromDishka[TransactionDao],
) -> dict[str, Any]:
    gateway_stats = await transaction_dao.get_gateway_stats()
    plan_income = await transaction_dao.get_plan_income()
    total = await transaction_dao.count_total()
    completed = await transaction_dao.count_completed()
    free = await transaction_dao.count_free()

    return {
        "total": total,
        "completed": completed,
        "free": free,
        "paid": completed - free,
        "gateways": [_gateway_stats_to_dict(g) for g in gateway_stats],
        "plan_income": [
            {
                "plan_id": p.plan_id,
                "currency": p.currency,
                "total_income": float(p.total_income),
            }
            for p in plan_income
        ],
    }
