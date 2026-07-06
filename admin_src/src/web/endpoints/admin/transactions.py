import io
import json
from datetime import date as _date, timedelta
from typing import Any, Optional
from uuid import UUID

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.dao import UserDao

from ._common import AdminUser
from ._redact import is_readonly_admin, redact_transaction

router = APIRouter(prefix="/transactions", tags=["Admin - Transactions"])


def _build_where(
    status_filter: Optional[str], gateway: Optional[str],
    date_from: Optional[str], date_to: Optional[str],
) -> tuple[str, dict[str, Any]]:
    """Общий WHERE для списка и экспорта (фильтры status/gateway/период)."""
    where: list[str] = []
    params: dict[str, Any] = {}
    if status_filter:
        where.append("upper(status::text) = :st")
        params["st"] = status_filter.upper()
    if gateway:
        where.append("upper(gateway_type::text) = :gw")
        params["gw"] = gateway.upper()
    if date_from:
        try:
            params["df"] = _date.fromisoformat(date_from)
            where.append("created_at >= :df")
        except ValueError:
            pass
    if date_to:
        try:
            params["dt"] = _date.fromisoformat(date_to) + timedelta(days=1)
            where.append("created_at < :dt")
        except ValueError:
            pass
    return (("WHERE " + " AND ".join(where)) if where else ""), params


@router.get("")
@inject
async def list_transactions(
    admin: AdminUser,
    user_dao: FromDishka[UserDao],
    session: FromDishka[AsyncSession],
    limit: int = Query(default=25, le=100),
    offset: int = Query(default=0, ge=0),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    gateway: Optional[str] = Query(default=None),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    # Фильтры: статус, шлюз, период (date_from..date_to включительно) + пагинация.
    where: list[str] = []
    params: dict[str, Any] = {}
    if status_filter:
        where.append("upper(status::text) = :st")
        params["st"] = status_filter.upper()
    if gateway:
        where.append("upper(gateway_type::text) = :gw")
        params["gw"] = gateway.upper()
    if date_from:
        try:
            params["df"] = _date.fromisoformat(date_from)
            where.append("created_at >= :df")
        except ValueError:
            pass
    if date_to:
        try:
            # включительно: < (дата_до + 1 день)
            params["dt"] = _date.fromisoformat(date_to) + timedelta(days=1)
            where.append("created_at < :dt")
        except ValueError:
            pass
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    total = (
        await session.execute(text(f"SELECT count(*) FROM transactions {where_sql}"), params)
    ).scalar_one()
    rows = (
        await session.execute(
            text(
                f"""
                SELECT payment_id, status, is_test, purchase_type, gateway_type,
                       created_at, updated_at, user_id,
                       pricing->>'final_amount' AS final_amount,
                       currency::text AS currency,
                       plan_snapshot->>'name' AS plan_name,
                       plan_snapshot->>'duration' AS plan_duration
                FROM transactions {where_sql}
                ORDER BY created_at DESC NULLS LAST
                LIMIT :limit OFFSET :offset
                """
            ),
            {**params, "limit": limit, "offset": offset},
        )
    ).all()

    user_ids = list({r.user_id for r in rows if r.user_id is not None})
    users_map = {u.id: u for u in (await user_dao.get_by_ids(user_ids) if user_ids else [])}

    items = []
    for r in rows:
        u = users_map.get(r.user_id)
        items.append(
            {
                "payment_id": str(r.payment_id),
                "user_id": r.user_id,
                "user_name": u.name if u else None,
                "user_email": u.email if u else None,
                "status": r.status,
                "gateway_type": r.gateway_type,
                "purchase_type": r.purchase_type,
                "is_test": r.is_test,
                "amount": r.final_amount,
                "currency": r.currency,
                "plan_name": r.plan_name,
                "plan_duration": int(r.plan_duration) if r.plan_duration else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
        )

    if is_readonly_admin(admin):
        items = [redact_transaction(it) for it in items]

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
    }


def _enum_val(v: Any) -> str:
    return v.value if hasattr(v, "value") else ("" if v is None else str(v))


@router.get("/export.xlsx")
@inject
async def export_transactions_xlsx(
    admin: AdminUser,
    user_dao: FromDishka[UserDao],
    session: FromDishka[AsyncSession],
    status_filter: Optional[str] = Query(default=None, alias="status"),
    gateway: Optional[str] = Query(default=None),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
) -> Response:
    """Экспорт транзакций в Excel (.xlsx) с колонками, автофильтром и закреплённой
    шапкой. Те же фильтры, что и в списке. Кап 50000 строк."""
    where_sql, params = _build_where(status_filter, gateway, date_from, date_to)
    rows = (
        await session.execute(
            text(
                f"""
                SELECT payment_id, status, is_test, purchase_type, gateway_type,
                       created_at, user_id,
                       pricing->>'final_amount' AS final_amount,
                       currency::text AS currency,
                       plan_snapshot->>'name' AS plan_name,
                       plan_snapshot->>'duration' AS plan_duration
                FROM transactions {where_sql}
                ORDER BY created_at DESC NULLS LAST
                LIMIT 50000
                """
            ),
            params,
        )
    ).all()

    user_ids = list({r.user_id for r in rows if r.user_id is not None})
    users_map = {u.id: u for u in (await user_dao.get_by_ids(user_ids) if user_ids else [])}
    readonly = is_readonly_admin(admin)

    import xlsxwriter  # локальный импорт — тяжёлое только при экспорте

    headers = ["Дата", "Пользователь", "Email", "Статус", "Шлюз", "Тип",
               "Сумма", "Валюта", "Тариф", "Дней", "Тест", "ID платежа"]
    widths = [17, 20, 26, 12, 14, 9, 10, 8, 22, 7, 6, 38]

    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True, "default_date_format": "yyyy-mm-dd hh:mm"})
    ws = wb.add_worksheet("Транзакции")
    f_header = wb.add_format({"bold": True, "bg_color": "#EEF0F2", "border": 1, "align": "left"})
    f_date = wb.add_format({"num_format": "yyyy-mm-dd hh:mm"})
    f_money = wb.add_format({"num_format": "#,##0"})

    for c, (h, wdt) in enumerate(zip(headers, widths)):
        ws.set_column(c, c, wdt)
        ws.write(0, c, h, f_header)

    for i, r in enumerate(rows, start=1):
        u = users_map.get(r.user_id)
        rec = {"user_email": u.email if u else None, "payment_id": str(r.payment_id), "user_id": r.user_id}
        if readonly:
            rec = redact_transaction(rec)
        created = r.created_at
        # Дата — как datetime (Excel отсортирует/отфильтрует по-настоящему).
        if created is not None:
            ws.write_datetime(i, 0, created.replace(tzinfo=None), f_date)
        ws.write(i, 1, (u.name if u else None) or (f"#{r.user_id}" if r.user_id is not None else ""))
        ws.write(i, 2, rec.get("user_email") or "")
        ws.write(i, 3, _enum_val(r.status))
        ws.write(i, 4, _enum_val(r.gateway_type))
        ws.write(i, 5, _enum_val(r.purchase_type))
        # Сумма — числом (фильтр/сортировка по значению).
        try:
            ws.write_number(i, 6, float(r.final_amount), f_money)
        except (TypeError, ValueError):
            ws.write(i, 6, r.final_amount or "")
        ws.write(i, 7, r.currency or "")
        ws.write(i, 8, r.plan_name or "")
        if r.plan_duration:
            try:
                ws.write_number(i, 9, int(r.plan_duration))
            except (TypeError, ValueError):
                ws.write(i, 9, r.plan_duration)
        ws.write(i, 10, "да" if r.is_test else "")
        ws.write(i, 11, rec.get("payment_id") or "")

    ws.autofilter(0, 0, len(rows), len(headers) - 1)  # фильтр по всем колонкам
    ws.freeze_panes(1, 0)  # закрепить шапку
    wb.close()

    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="transactions.xlsx"'},
    )


@router.get("/{payment_id}")
@inject
async def get_transaction(
    payment_id: str,
    admin: AdminUser,
    session: FromDishka[AsyncSession],
    user_dao: FromDishka[UserDao],
) -> dict[str, Any]:
    """Полные детали транзакции: pricing, plan_snapshot, шлюз, тайминги, юзер."""
    try:
        UUID(payment_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Недопустимый payment_id")

    row = (
        await session.execute(
            text(
                "SELECT payment_id, status, is_test, purchase_type, gateway_type, "
                "gateway_display_name, payment_method, currency, "
                "pricing::text AS pricing, plan_snapshot::text AS plan_snapshot, "
                "created_at, updated_at, user_id "
                "FROM transactions WHERE payment_id = :pid"
            ),
            {"pid": payment_id},
        )
    ).first()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Транзакция не найдена")

    user = await user_dao.get_by_id(row.user_id)
    readonly = is_readonly_admin(admin)

    return {
        "payment_id": "•••" if readonly else str(row.payment_id),
        "status": _enum_val(row.status),
        "is_test": row.is_test,
        "purchase_type": _enum_val(row.purchase_type),
        "gateway_type": _enum_val(row.gateway_type),
        "gateway_display_name": row.gateway_display_name,
        "payment_method": row.payment_method,
        "currency": _enum_val(row.currency),
        "pricing": json.loads(row.pricing) if row.pricing else None,
        "plan_snapshot": json.loads(row.plan_snapshot) if row.plan_snapshot else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "user": {
            "id": user.id if user else row.user_id,
            "name": user.name if user else None,
            "email": None if readonly else (user.email if user else None),
            "username": user.username if user else None,
        },
    }
