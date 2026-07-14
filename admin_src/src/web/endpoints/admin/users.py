import io
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Remnawave
from src.application.common.dao import SubscriptionDao, TransactionDao, UserDao
from src.application.dto import UserDto
from src.core.enums import Role

from ._common import AdminUser
from ._redact import is_readonly_admin, redact_user

router = APIRouter(prefix="/users", tags=["Admin - Users"])


def _user_to_dict(user: UserDto) -> dict[str, Any]:
    return {
        "id": user.id,
        "telegram_id": user.telegram_id,
        "auth_type": user.auth_type,
        "email": user.email,
        "is_email_verified": user.is_email_verified,
        "name": user.name,
        "username": user.username,
        "role": user.role,
        "language": user.language,
        "is_blocked": user.is_blocked,
        "is_bot_blocked": user.is_bot_blocked,
        "is_trial_available": user.is_trial_available,
        "personal_discount": user.personal_discount,
        "purchase_discount": user.purchase_discount,
        "points": user.points,
        "referral_code": user.referral_code,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def _build_user_where(
    search: Optional[str], blocked: Optional[bool], role: Optional[int]
) -> tuple[str, dict[str, Any]]:
    """Одни и те же фильтры для списка и экспорта (чтобы не разъезжались)."""
    where: list[str] = []
    params: dict[str, Any] = {}
    if search:
        where.append("(u.name ILIKE :s OR u.email ILIKE :s OR u.username ILIKE :s)")
        params["s"] = f"%{search.strip()}%"
    if blocked is not None:
        where.append("u.is_blocked = :bl")
        params["bl"] = blocked
    if role is not None:
        try:
            params["r"] = Role(role).name
            where.append("u.role = :r")
        except ValueError:
            pass
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    return where_sql, params


@router.get("")
@inject
async def list_users(
    admin: AdminUser,
    user_dao: FromDishka[UserDao],
    session: FromDishka[AsyncSession],
    limit: int = Query(default=25, le=100),
    offset: int = Query(default=0, ge=0),
    search: Optional[str] = Query(default=None),
    blocked: Optional[bool] = Query(default=None),
    role: Optional[int] = Query(default=None),
    sort: str = Query(default="created_at"),
    order: str = Query(default="desc"),
    expiring_days: Optional[int] = Query(default=None, ge=1, le=365),
) -> dict[str, Any]:
    # Гибкий фильтр+сортировка. last_login берём из login_events (LEFT JOIN),
    # чтобы можно было сортировать по дате последнего входа.
    where_sql, params = _build_user_where(search, blocked, role)

    # Фильтр «истекают в N дней» (ретеншн): JOIN на подписки, у которых срок ещё
    # НЕ вышел, но истекает в окне [сейчас; сейчас+N дней]. Берём ближайшую дату.
    expiring = expiring_days is not None
    join_sql = ""
    if expiring:
        join_sql = (
            "JOIN (SELECT user_id, min(expire_at) AS expire_at FROM subscriptions "
            "WHERE expire_at > now() AND expire_at <= now() + make_interval(days => :exp_days) "
            "GROUP BY user_id) ex ON ex.user_id = u.id"
        )
        params["exp_days"] = int(expiring_days)

    # Колонка сортировки — строго из белого списка (без SQL-инъекций).
    sort_col = {
        "created_at": "u.created_at",
        "last_login": "ll.last_login",
        "name": "lower(u.name)",
    }.get(sort, "u.created_at")
    direction = "ASC" if str(order).lower() == "asc" else "DESC"
    nulls = "NULLS LAST" if direction == "DESC" else "NULLS FIRST"
    # При фильтре «истекают» сортируем по срочности (кто раньше истекает — выше).
    order_sql = "ex.expire_at ASC" if expiring else f"{sort_col} {direction} {nulls}"
    expire_col = ", ex.expire_at AS expire_at" if expiring else ""

    total = (
        await session.execute(
            text(f"SELECT count(*) FROM users u {join_sql} {where_sql}"), params
        )
    ).scalar_one()

    rows = (
        await session.execute(
            text(
                f"""
                SELECT u.id AS id, ll.last_login AS last_login{expire_col}
                FROM users u
                LEFT JOIN (
                    SELECT user_id, max(created_at) AS last_login
                    FROM login_events GROUP BY user_id
                ) ll ON ll.user_id = u.id
                {join_sql}
                {where_sql}
                ORDER BY {order_sql}, u.id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {**params, "limit": limit, "offset": offset},
        )
    ).all()

    ids = [r.id for r in rows]
    last_map = {r.id: r.last_login for r in rows}
    expire_map = {r.id: getattr(r, "expire_at", None) for r in rows} if expiring else {}
    by_id: dict[int, UserDto] = {}
    if ids:
        for u in await user_dao.get_by_ids(ids):
            by_id[u.id] = u

    items = []
    for uid in ids:  # сохраняем порядок сортировки
        u = by_id.get(uid)
        if u is None:
            continue
        d = _user_to_dict(u)
        m = last_map.get(uid)
        d["last_login_at"] = m.isoformat() if m else None
        exp = expire_map.get(uid)
        d["expire_at"] = exp.isoformat() if exp else None
        items.append(d)

    if is_readonly_admin(admin):
        items = [redact_user(it) for it in items]

    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.get("/export.xlsx")
@inject
async def export_users_xlsx(
    admin: AdminUser,
    user_dao: FromDishka[UserDao],
    session: FromDishka[AsyncSession],
    search: Optional[str] = Query(default=None),
    blocked: Optional[bool] = Query(default=None),
    role: Optional[int] = Query(default=None),
    sort: str = Query(default="created_at"),
    order: str = Query(default="desc"),
) -> Response:
    """Экспорт пользователей в Excel (.xlsx) с колонками, автофильтром и
    закреплённой шапкой. Те же фильтры/сортировка, что и в списке. Кап 50000
    строк. Для readonly-админа email/username/telegram_id/реф-код маскируются."""
    where_sql, params = _build_user_where(search, blocked, role)

    sort_col = {
        "created_at": "u.created_at",
        "last_login": "ll.last_login",
        "name": "lower(u.name)",
    }.get(sort, "u.created_at")
    direction = "ASC" if str(order).lower() == "asc" else "DESC"
    nulls = "NULLS LAST" if direction == "DESC" else "NULLS FIRST"

    rows = (
        await session.execute(
            text(
                f"""
                SELECT u.id AS id, ll.last_login AS last_login
                FROM users u
                LEFT JOIN (
                    SELECT user_id, max(created_at) AS last_login
                    FROM login_events GROUP BY user_id
                ) ll ON ll.user_id = u.id
                {where_sql}
                ORDER BY {sort_col} {direction} {nulls}, u.id DESC
                LIMIT 50000
                """
            ),
            params,
        )
    ).all()

    ids = [r.id for r in rows]
    last_map = {r.id: r.last_login for r in rows}
    by_id: dict[int, UserDto] = {}
    if ids:
        for u in await user_dao.get_by_ids(ids):
            by_id[u.id] = u
    readonly = is_readonly_admin(admin)

    import xlsxwriter  # локальный импорт — тяжёлое только при экспорте

    headers = ["Дата рег.", "ID", "Имя", "Username", "Email", "Telegram ID",
               "Роль", "Язык", "Баллы", "Скидка %", "Заблокирован",
               "Пробник доступен", "Реф-код", "Последний вход"]
    widths = [17, 8, 22, 18, 26, 15, 10, 7, 8, 9, 13, 16, 12, 17]

    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True, "default_date_format": "yyyy-mm-dd hh:mm"})
    ws = wb.add_worksheet("Пользователи")
    f_header = wb.add_format({"bold": True, "bg_color": "#EEF0F2", "border": 1, "align": "left"})
    f_date = wb.add_format({"num_format": "yyyy-mm-dd hh:mm"})

    for c, (h, wdt) in enumerate(zip(headers, widths)):
        ws.set_column(c, c, wdt)
        ws.write(0, c, h, f_header)

    for i, uid in enumerate(ids, start=1):
        u = by_id.get(uid)
        if u is None:
            continue
        d = _user_to_dict(u)
        if readonly:
            d = redact_user(d)
        try:
            role_name = Role(u.role).name
        except (ValueError, TypeError):
            role_name = getattr(u.role, "name", str(u.role))
        # Дата — datetime, чтобы Excel сортировал/фильтровал по-настоящему.
        if u.created_at is not None:
            ws.write_datetime(i, 0, u.created_at.replace(tzinfo=None), f_date)
        ws.write(i, 1, "" if d.get("id") is None else d["id"])
        ws.write(i, 2, u.name or "")
        ws.write(i, 3, d.get("username") or "")
        ws.write(i, 4, d.get("email") or "")
        ws.write(i, 5, "" if d.get("telegram_id") is None else str(d["telegram_id"]))
        ws.write(i, 6, role_name)
        ws.write(i, 7, u.language or "")
        ws.write_number(i, 8, int(u.points or 0))
        ws.write_number(i, 9, int(u.personal_discount or 0))
        ws.write(i, 10, "да" if u.is_blocked else "")
        ws.write(i, 11, "да" if u.is_trial_available else "")
        ws.write(i, 12, d.get("referral_code") or "")
        m = last_map.get(uid)
        if m is not None:
            ws.write_datetime(i, 13, m.replace(tzinfo=None), f_date)

    ws.autofilter(0, 0, len(ids), len(headers) - 1)  # фильтр по всем колонкам
    ws.freeze_panes(1, 0)  # закрепить шапку
    wb.close()

    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="users.xlsx"'},
    )


@router.get("/{user_id}")
@inject
async def get_user(
    user_id: int,
    admin: AdminUser,
    user_dao: FromDishka[UserDao],
    subscription_dao: FromDishka[SubscriptionDao],
    transaction_dao: FromDishka[TransactionDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    user = await user_dao.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")

    current_sub = await subscription_dao.get_current(user_id)
    all_subs = await subscription_dao.get_all_by_user(user_id)
    login_stats = (
        await session.execute(
            text(
                "SELECT count(*) AS total, count(DISTINCT ip) AS ips, max(created_at) AS last "
                "FROM login_events WHERE user_id = :u"
            ),
            {"u": user_id},
        )
    ).first()
    # Транзакции в карточке — платёжные детали; для read-only их не отдаём.
    readonly = is_readonly_admin(admin)
    transactions = [] if readonly else await transaction_dao.get_by_user(user_id)

    user_dict = _user_to_dict(user)
    if readonly:
        user_dict = redact_user(user_dict)
    # Рублёвый баланс-кошелёк (overlay-колонка, в UserDto его нет).
    bal = (
        await session.execute(
            text("SELECT cabinet_balance FROM users WHERE id = :id"), {"id": user_id}
        )
    ).scalar_one_or_none()
    user_dict["cabinet_balance"] = float(bal) if bal is not None else 0.0

    return {
        "user": user_dict,
        "current_subscription": {
            "status": current_sub.current_status.value,
            "is_trial": current_sub.is_trial,
            "plan_name": current_sub.plan_snapshot.name if current_sub.plan_snapshot else None,
            "expire_at": current_sub.expire_at.isoformat() if current_sub.expire_at else None,
            "traffic_limit": current_sub.traffic_limit,
            "device_limit": current_sub.device_limit,
        }
        if current_sub
        else None,
        "subscriptions_count": len(all_subs),
        "logins": {
            "total": login_stats.total if login_stats else 0,
            "distinct_ips": login_stats.ips if login_stats else 0,
            "last_login_at": login_stats.last.isoformat()
            if login_stats and login_stats.last
            else None,
        },
        "transactions": [
            {
                "payment_id": str(t.payment_id),
                "status": t.status,
                "gateway_type": t.gateway_type,
                "purchase_type": t.purchase_type,
                "final_amount": str(t.final_amount) if hasattr(t, "final_amount") else None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in transactions[:20]
        ],
    }


@router.get("/{user_id}/referrals")
@inject
async def user_referrals(
    user_id: int,
    admin: AdminUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    """Реф-связи пользователя: кто пригласил (referrer, level FIRST) + кого пригласил
    (прямые FIRST и второго уровня SECOND). Для read-only админа username маскируется."""
    readonly = is_readonly_admin(admin)

    referrer_row = (
        await session.execute(
            text(
                "SELECT u.id, u.name, u.username, r.created_at "
                "FROM referrals r JOIN users u ON u.id = r.referrer_id "
                "WHERE r.referred_id = :uid AND r.level = 'FIRST' LIMIT 1"
            ),
            {"uid": user_id},
        )
    ).first()

    rows = (
        await session.execute(
            text(
                "SELECT u.id, u.name, u.username, r.level::text AS level, r.created_at "
                "FROM referrals r JOIN users u ON u.id = r.referred_id "
                "WHERE r.referrer_id = :uid ORDER BY r.created_at DESC LIMIT 200"
            ),
            {"uid": user_id},
        )
    ).all()

    def uname(v: Any) -> Optional[str]:
        return None if readonly else v

    def member(r: Any) -> dict[str, Any]:
        return {
            "id": r.id,
            "name": r.name,
            "username": uname(r.username),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }

    first = [member(r) for r in rows if r.level == "FIRST"]
    second = [member(r) for r in rows if r.level == "SECOND"]
    return {
        "referrer": ({**member(referrer_row)}) if referrer_row else None,
        "referrals": first,
        "second_level": second,
        "counts": {"first": len(first), "second": len(second)},
    }


@router.get("/{user_id}/logins")
@inject
async def user_logins(
    user_id: int,
    admin: AdminUser,
    session: FromDishka[AsyncSession],
    limit: int = Query(default=50, le=200),
) -> dict[str, Any]:
    """История входов: всего, уникальных IP, последние события (время/IP/способ).

    Для read-only админа сами IP-адреса маскируются (видны только счётчики)."""
    readonly = is_readonly_admin(admin)
    summary = (
        await session.execute(
            text(
                "SELECT count(*) AS total, count(DISTINCT ip) AS ips, max(created_at) AS last "
                "FROM login_events WHERE user_id = :u"
            ),
            {"u": user_id},
        )
    ).first()
    rows = (
        await session.execute(
            text(
                "SELECT ip, user_agent, method, created_at FROM login_events "
                "WHERE user_id = :u ORDER BY created_at DESC LIMIT :l"
            ),
            {"u": user_id, "l": limit},
        )
    ).all()
    return {
        "total": summary.total if summary else 0,
        "distinct_ips": summary.ips if summary else 0,
        "last_login_at": summary.last.isoformat() if summary and summary.last else None,
        "items": [
            {
                "ip": None if readonly else r.ip,
                "user_agent": r.user_agent,
                "method": r.method,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


@router.get("/{user_id}/traffic-by-node")
@inject
async def user_traffic_by_node(
    user_id: int,
    _admin: AdminUser,
    subscription_dao: FromDishka[SubscriptionDao],
    remnawave: FromDishka[Remnawave],
    days: int = Query(default=30, ge=1, le=365),
) -> dict[str, Any]:
    """Расход трафика пользователя по нодам за последние N дней.

    Данные — живьём из API панели Remnawave (bandwidthstats), в БД не хранятся.
    Возвращаем список нод с трафиком (по убыванию) и суммарный объём."""
    empty = {"available": False, "days": days, "total": 0, "nodes": []}

    current = await subscription_dao.get_current(user_id)
    if not current or not getattr(current, "user_remna_id", None):
        return empty

    sdk = getattr(remnawave, "sdk", None)
    if sdk is None:
        return empty

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    try:
        result = await sdk.bandwidthstats.get_stats_user_usage(
            uuid=str(current.user_remna_id),
            top_nodes_limit=50,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"RemnaWave error: {e}")

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

    return {
        "available": True,
        "days": days,
        "total": sum(n["total"] for n in nodes),
        "nodes": nodes,
    }


class ToggleBlockRequest(BaseModel):
    is_blocked: bool


@router.put("/{user_id}/block")
@inject
async def toggle_block_user(
    user_id: int,
    body: ToggleBlockRequest,
    admin: AdminUser,
    user_dao: FromDishka[UserDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    user = await user_dao.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
    if user.id == admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Нельзя заблокировать себя")
    if user.role >= Role.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Нельзя заблокировать владельца"
        )

    user.is_blocked = body.is_blocked
    updated = await user_dao.update(user)
    if not updated:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Не удалось обновить")
    await session.commit()
    return {"success": True, "is_blocked": updated.is_blocked}


# ─── Массовые действия над сегментом (тот же фильтр, что и список) ─────────────

_BULK_CAP = 2000  # предохранитель: за раз обрабатываем не больше


class BulkActionRequest(BaseModel):
    action: str  # points | discount | block | unblock
    value: int = 0
    # Сегмент задаётся теми же фильтрами, что и список пользователей.
    search: Optional[str] = None
    blocked: Optional[bool] = None
    role: Optional[int] = None
    expiring_days: Optional[int] = None


@router.post("/bulk-action")
@inject
async def bulk_action(
    body: BulkActionRequest,
    admin: AdminUser,
    user_dao: FromDishka[UserDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    action = body.action
    value = int(body.value)
    if action not in ("points", "discount", "block", "unblock"):
        raise HTTPException(status_code=400, detail="Неизвестное действие")
    if action == "discount" and not (0 <= value <= 100):
        raise HTTPException(status_code=400, detail="Скидка должна быть 0..100%")
    if action == "points" and value == 0:
        raise HTTPException(status_code=400, detail="Укажите ненулевое число баллов")

    # Сегмент. Массово трогаем ТОЛЬКО обычных пользователей (не персонал).
    where_sql, params = _build_user_where(body.search, body.blocked, body.role)
    where_sql = (where_sql + " AND u.role = 'USER'") if where_sql else "WHERE u.role = 'USER'"
    join_sql = ""
    if body.expiring_days:
        join_sql = (
            "JOIN (SELECT user_id, min(expire_at) AS expire_at FROM subscriptions "
            "WHERE expire_at > now() AND expire_at <= now() + make_interval(days => :exp_days) "
            "GROUP BY user_id) ex ON ex.user_id = u.id"
        )
        params["exp_days"] = int(body.expiring_days)

    ids = [
        r.id
        for r in (
            await session.execute(
                text(f"SELECT u.id FROM users u {join_sql} {where_sql} ORDER BY u.id LIMIT {_BULK_CAP}"),
                params,
            )
        ).all()
    ]
    if not ids:
        return {"matched": 0, "applied": 0}

    applied = 0
    for uid in ids:
        if uid == admin.id:
            continue
        u = await user_dao.get_by_id(uid)
        if not u:
            continue
        if action == "points":
            u.points = max(0, (u.points or 0) + value)
        elif action == "discount":
            u.personal_discount = value
        elif action == "block":
            u.is_blocked = True
        else:  # unblock
            u.is_blocked = False
        if await user_dao.update(u):
            applied += 1
    await session.commit()
    return {"matched": len(ids), "applied": applied}


class SetTrialRequest(BaseModel):
    is_trial_available: bool


@router.put("/{user_id}/trial")
@inject
async def set_trial_available(
    user_id: int,
    body: SetTrialRequest,
    _admin: AdminUser,
    user_dao: FromDishka[UserDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    """Снять/вернуть право на пробник. Основное применение — детект абьюза:
    отобрать триал у мультиаккаунта (is_trial_available=false) без блокировки."""
    user = await user_dao.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")

    user.is_trial_available = body.is_trial_available
    updated = await user_dao.update(user)
    if not updated:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Не удалось обновить")
    await session.commit()
    return {"success": True, "is_trial_available": updated.is_trial_available}


class ChangeRoleRequest(BaseModel):
    role: int


@router.put("/{user_id}/role")
@inject
async def change_user_role(
    user_id: int,
    body: ChangeRoleRequest,
    admin: AdminUser,
    user_dao: FromDishka[UserDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    if admin.role < Role.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Менять роли может только владелец",
        )
    user = await user_dao.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
    if user.id == admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Нельзя менять свою роль")

    try:
        new_role = Role(body.role)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Недопустимое значение роли")

    user.role = new_role
    updated = await user_dao.update(user)
    if not updated:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Не удалось обновить")
    await session.commit()
    return {"success": True, "role": updated.role}


class SetDiscountRequest(BaseModel):
    personal_discount: int
    purchase_discount: int


@router.put("/{user_id}/discount")
@inject
async def set_user_discount(
    user_id: int,
    body: SetDiscountRequest,
    admin: AdminUser,
    user_dao: FromDishka[UserDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    user = await user_dao.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")

    user.personal_discount = max(0, min(100, body.personal_discount))
    user.purchase_discount = max(0, min(100, body.purchase_discount))
    updated = await user_dao.update(user)
    if not updated:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Не удалось обновить")
    await session.commit()
    return {
        "success": True,
        "personal_discount": updated.personal_discount,
        "purchase_discount": updated.purchase_discount,
    }
