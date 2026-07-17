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


async def compute_metrics(session: AsyncSession) -> dict[str, Any]:
    """Продуктовые KPI (вынесено из эндпоинта ради тестируемости).

    Денежные метрики считаем в RUB (доминирующая валюта; XTR-звёзды и USD в
    MRR/ARPU не подмешиваем — валюты не суммируются). «Возвратов» как статуса в
    БД нет (только COMPLETED/CANCELED), поэтому вместо refund показываем success
    rate платежей = COMPLETED / (COMPLETED + CANCELED).
    """
    RUB = "AND currency::text = 'RUB' "

    # ── MRR (RUB, оценка) ─────────────────────────────────────────────────────
    # Нет цены в plan_snapshot подписки → берём последний реальный RUB-платёж
    # юзера и нормируем на длительность тарифа к 30 дням. Lifetime (duration=0)
    # не рекуррентен — исключаем.
    mrr_row = (
        await session.execute(
            text(
                """
                WITH active AS (
                    SELECT DISTINCT ON (sub.user_id) sub.user_id,
                           NULLIF(sub.plan_snapshot->>'duration','')::numeric AS dur
                    FROM subscriptions sub
                    WHERE sub.expire_at > now() AND sub.is_trial = false
                    ORDER BY sub.user_id, sub.expire_at DESC
                ), lastpay AS (
                    SELECT DISTINCT ON (t.user_id) t.user_id,
                           (t.pricing->>'final_amount')::numeric AS amt
                    FROM transactions t
                    WHERE t.status::text = 'COMPLETED' AND t.is_test = false
                      AND t.currency::text = 'RUB'
                      AND (t.pricing->>'final_amount')::numeric > 0
                    ORDER BY t.user_id, t.created_at DESC
                )
                SELECT coalesce(sum(l.amt * 30.0 / a.dur), 0) AS mrr, count(*) AS n
                FROM active a JOIN lastpay l ON l.user_id = a.user_id
                WHERE a.dur IS NOT NULL AND a.dur > 0
                """
            )
        )
    ).first()
    mrr = float(mrr_row.mrr or 0) if mrr_row else 0.0
    mrr_subs = int(mrr_row.n or 0) if mrr_row else 0

    # ── Выручка/платежи за 30 дней (RUB) + ARPU/ARPPU ─────────────────────────
    rev30 = (
        await session.execute(
            text(
                "SELECT coalesce(sum((pricing->>'final_amount')::numeric),0) AS rev, "
                "count(*) AS cnt, count(DISTINCT user_id) AS payers "
                "FROM transactions WHERE status::text='COMPLETED' AND is_test=false "
                "AND (pricing->>'final_amount')::numeric>0 " + RUB +
                "AND created_at >= now() - interval '30 days'"
            )
        )
    ).first()
    revenue_30d = float(rev30.rev or 0)
    payers_30d = int(rev30.payers or 0)
    active_users = (
        await session.execute(
            text("SELECT count(*) FROM subscriptions WHERE expire_at > now()")
        )
    ).scalar() or 0
    arpu = round(revenue_30d / active_users, 2) if active_users else 0.0
    arppu = round(revenue_30d / payers_30d, 2) if payers_30d else 0.0

    # ── Конверсия trial → платящий ────────────────────────────────────────────
    conv = (
        await session.execute(
            text(
                """
                WITH trial_users AS (SELECT DISTINCT user_id FROM subscriptions WHERE is_trial = true),
                     paid AS (SELECT DISTINCT user_id FROM transactions
                              WHERE status::text='COMPLETED' AND is_test=false
                                AND (pricing->>'final_amount')::numeric>0)
                SELECT (SELECT count(*) FROM trial_users) AS trials,
                       (SELECT count(*) FROM trial_users t WHERE t.user_id IN (SELECT user_id FROM paid)) AS converted
                """
            )
        )
    ).first()
    trials = int(conv.trials or 0)
    converted = int(conv.converted or 0)
    conversion_pct = round(converted * 100 / trials, 1) if trials else 0.0

    # ── Отток (churn) за 30 дней ──────────────────────────────────────────────
    churn = (
        await session.execute(
            text(
                """
                WITH u AS (
                    SELECT user_id, max(expire_at) AS exp
                    FROM subscriptions WHERE is_trial = false GROUP BY user_id
                )
                SELECT count(*) FILTER (WHERE exp > now()) AS active_now,
                       count(*) FILTER (WHERE exp <= now() AND exp > now() - interval '30 days') AS churned
                FROM u
                """
            )
        )
    ).first()
    active_now = int(churn.active_now or 0)
    churned = int(churn.churned or 0)
    churn_base = active_now + churned
    churn_pct = round(churned * 100 / churn_base, 1) if churn_base else 0.0

    # ── Success rate платежей за 30 дней (вместо «возвратов») ──────────────────
    pay = (
        await session.execute(
            text(
                "SELECT count(*) FILTER (WHERE status::text='COMPLETED') AS ok, "
                "count(*) FILTER (WHERE status::text='CANCELED') AS canceled "
                "FROM transactions WHERE is_test=false "
                "AND created_at >= now() - interval '30 days'"
            )
        )
    ).first()
    completed_30 = int(pay.ok or 0)
    canceled_30 = int(pay.canceled or 0)
    pay_base = completed_30 + canceled_30
    success_pct = round(completed_30 * 100 / pay_base, 1) if pay_base else 0.0

    # ── Топ-тарифы по выручке (RUB, всё время) ────────────────────────────────
    top_plans = [
        {"name": r.name, "revenue": float(r.rev or 0), "count": int(r.cnt or 0)}
        for r in (
            await session.execute(
                text(
                    "SELECT plan_snapshot->>'name' AS name, "
                    "sum((pricing->>'final_amount')::numeric) AS rev, count(*) AS cnt "
                    "FROM transactions WHERE status::text='COMPLETED' AND is_test=false "
                    "AND (pricing->>'final_amount')::numeric>0 " + RUB +
                    "AND plan_snapshot->>'name' IS NOT NULL "
                    "AND coalesce((plan_snapshot->>'id')::int, 0) >= 0 "
                    "GROUP BY 1 ORDER BY rev DESC LIMIT 5"
                )
            )
        ).all()
    ]

    # ── Топ-шлюзы по выручке (RUB, всё время) ─────────────────────────────────
    top_gateways = [
        {"gateway_type": r.gw, "revenue": float(r.rev or 0), "count": int(r.cnt or 0)}
        for r in (
            await session.execute(
                text(
                    "SELECT gateway_type::text AS gw, "
                    "sum((pricing->>'final_amount')::numeric) AS rev, count(*) AS cnt "
                    "FROM transactions WHERE status::text='COMPLETED' AND is_test=false "
                    "AND (pricing->>'final_amount')::numeric>0 " + RUB +
                    "GROUP BY 1 ORDER BY rev DESC LIMIT 5"
                )
            )
        ).all()
    ]

    return {
        "currency": "RUB",
        "mrr": round(mrr, 2),
        "mrr_subs": mrr_subs,
        "arpu": arpu,
        "arppu": arppu,
        "revenue_30d": round(revenue_30d, 2),
        "active_users": int(active_users),
        "payers_30d": payers_30d,
        "conversion": {"trials": trials, "converted": converted, "pct": conversion_pct},
        "churn": {"active_now": active_now, "churned_30d": churned, "pct": churn_pct},
        "payments": {
            "completed_30d": completed_30,
            "canceled_30d": canceled_30,
            "success_pct": success_pct,
        },
        "top_plans": top_plans,
        "top_gateways": top_gateways,
    }


@router.get("/metrics")
@inject
async def get_metrics(
    _admin: AdminUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    """Продуктовые KPI: MRR, ARPU/ARPPU, конверсия trial→оплата, отток, топы."""
    return await compute_metrics(session)


@router.get("/cohorts")
@inject
async def get_cohorts(
    _admin: AdminUser,
    session: FromDishka[AsyncSession],
    months: int = Query(12, ge=3, le=24, description="сколько последних когорт-месяцев показать"),
) -> dict[str, Any]:
    """Когортное удержание по платежам: когорта = месяц ПЕРВОГО платежа юзера,
    удержание[k] = сколько из когорты платили в месяц (когорта+k). Только реальные
    завершённые платежи (COMPLETED, не тест, final_amount>0)."""
    rows = (
        await session.execute(
            text(
                "WITH pays AS ("
                "  SELECT user_id, date_trunc('month', created_at) AS m "
                "  FROM transactions "
                "  WHERE status = 'COMPLETED' AND is_test = false "
                "    AND (pricing->>'final_amount')::numeric > 0 "
                "  GROUP BY user_id, date_trunc('month', created_at)"
                "), cohort AS ("
                "  SELECT user_id, MIN(m) AS cohort_m FROM pays GROUP BY user_id"
                ") "
                "SELECT to_char(c.cohort_m, 'YYYY-MM') AS cohort, "
                "  ((EXTRACT(YEAR FROM p.m) - EXTRACT(YEAR FROM c.cohort_m)) * 12 "
                "   + (EXTRACT(MONTH FROM p.m) - EXTRACT(MONTH FROM c.cohort_m)))::int AS moff, "
                "  count(DISTINCT p.user_id) AS users "
                "FROM cohort c JOIN pays p ON p.user_id = c.user_id "
                "WHERE c.cohort_m >= date_trunc('month', now()) - make_interval(months => :m) "
                "GROUP BY c.cohort_m, moff "
                "ORDER BY c.cohort_m, moff"
            ),
            {"m": months},
        )
    ).all()

    matrix: dict[str, dict[int, int]] = {}
    max_off = 0
    for cohort, moff, users in rows:
        matrix.setdefault(cohort, {})[int(moff)] = int(users)
        max_off = max(max_off, int(moff))

    cohorts = []
    for cohort in sorted(matrix.keys()):
        offs = matrix[cohort]
        size = offs.get(0, 0)
        retention = []
        for k in range(0, max_off + 1):
            u = offs.get(k, 0)
            retention.append({
                "offset": k,
                "users": u,
                "pct": round(u * 100 / size, 1) if size else 0.0,
            })
        cohorts.append({"cohort": cohort, "size": size, "retention": retention})

    return {"cohorts": cohorts, "max_offset": max_off}
