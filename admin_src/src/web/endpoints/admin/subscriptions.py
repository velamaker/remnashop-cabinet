from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Notifier, Remnawave
from src.application.common.dao import PlanDao, SubscriptionDao, TransactionDao, UserDao
from src.application.dto import (
    MessagePayloadDto,
    PlanSnapshotDto,
    PriceDetailsDto,
    SubscriptionDto,
    TransactionDto,
)
from src.application.use_cases.remnawave import ReissueUserSubscription, ResetUserTraffic
from src.application.use_cases.subscription import (
    SyncSubscriptionFromRemnashop,
    SyncSubscriptionFromRemnawave,
    ToggleExternalSquad,
    ToggleInternalSquad,
    UpdateDeviceLimit,
    UpdateTrafficLimit,
)
from src.application.use_cases.subscription.commands.management import (
    ToggleExternalSquadDto,
    ToggleInternalSquadDto,
    UpdateDeviceLimitDto,
    UpdateTrafficLimitDto,
)
from src.application.use_cases.user import ResetUserReferralCode, SendMessageToUser
from src.application.use_cases.user.commands.messaging import SendMessageToUserDto
from src.core.enums import (
    Currency,
    PaymentGatewayType,
    PurchaseType,
    SubscriptionStatus,
    TransactionStatus,
)
from src.core.exceptions import PermissionDeniedError
from src.infrastructure.services.overlay_extend import compute_new_expire, push_subscription_expire
from remnapy.enums.users import TrafficLimitStrategy

from ._common import AdminUser
from ._redact import is_readonly_admin

router = APIRouter(prefix="/subscriptions", tags=["Admin - Subscriptions"])

UNLIMITED_YEAR = 2099


async def _sync_remnawave(awaitable: Any) -> Any:
    """Выполняет вызов к Remnawave, превращая ошибку панели в понятный 502.

    Вызывается ДО session.commit(), чтобы при сбое синхронизации локальные
    изменения откатились и админ увидел ошибку, а не «тихое» расхождение.
    """
    try:
        return await awaitable
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Ошибка синхронизации с Remnawave: {exc}",
        )


def _sub_to_dict(s: SubscriptionDto) -> dict[str, Any]:
    return {
        "id": s.id,
        "user_id": s.user_id,
        "status": s.current_status.value,
        "is_trial": s.is_trial,
        "plan_name": s.plan_snapshot.name if s.plan_snapshot else None,
        "expire_at": s.expire_at.isoformat() if s.expire_at else None,
        "traffic_limit": s.traffic_limit,
        "device_limit": s.device_limit,
        "internal_squads": [str(u) for u in (getattr(s, "internal_squads", None) or [])],
        "external_squad": str(s.external_squad) if getattr(s, "external_squad", None) else None,
        "url": s.url,
        "created_at": s.created_at.isoformat() if hasattr(s, "created_at") and s.created_at else None,
    }


# ─── Get user subscription ──────────────────────────────────────────────────

@router.get("/user/{user_id}")
@inject
async def get_user_subscription(
    user_id: int,
    _admin: AdminUser,
    subscription_dao: FromDishka[SubscriptionDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    sub = await subscription_dao.get_current(user_id)
    all_subs = await subscription_dao.get_all_by_user(user_id)
    current = _sub_to_dict(sub) if sub else None
    if current and sub is not None:
        # Откуда взялся лимит устройств: сколько даёт тариф и сколько докуплено.
        # Без этого админ видит «Устройств: 4» и не понимает, что одно из них временное.
        current.update(await _extra_device_summary(session, user_id, sub))
    return {"current": current, "history": [_sub_to_dict(s) for s in all_subs[:20]]}


# ─── Extend subscription ─────────────────────────────────────────────────────

class ExtendRequest(BaseModel):
    days: int


@router.post("/user/{user_id}/extend")
@inject
async def extend_subscription(
    user_id: int,
    body: ExtendRequest,
    _admin: AdminUser,
    user_dao: FromDishka[UserDao],
    subscription_dao: FromDishka[SubscriptionDao],
    remnawave: FromDishka[Remnawave],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    if body.days == 0 or abs(body.days) > 3650:
        raise HTTPException(status_code=400, detail="Дней должно быть от -3650 до 3650 (не 0)")

    sub = await subscription_dao.get_current(user_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Активная подписка не найдена")

    user = await user_dao.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    now = datetime.now(timezone.utc)
    # Расчёт срока и порядок записи (панель → наша база) — общие с массовым
    # «Добавить N дней» (services/overlay_extend.py): правило, поправленное в одном
    # месте, не должно разъехаться с другим. Сбой панели — 502, локально не коммитим.
    updated = await push_subscription_expire(
        user=user,
        sub=sub,
        target=compute_new_expire(sub.expire_at, body.days, now),
        remnawave=remnawave,
        subscription_dao=subscription_dao,
        guard=_sync_remnawave,
    )
    if not updated:
        raise HTTPException(status_code=500, detail="Не удалось обновить подписку")
    await session.commit()
    return {"success": True, "subscription": _sub_to_dict(updated)}


# ─── Disable subscription ─────────────────────────────────────────────────────

@router.post("/user/{user_id}/disable")
@inject
async def disable_subscription(
    user_id: int,
    _admin: AdminUser,
    subscription_dao: FromDishka[SubscriptionDao],
    remnawave: FromDishka[Remnawave],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    sub = await subscription_dao.get_current(user_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Подписка не найдена")

    # Отключаем доступ в панели Remnawave.
    await _sync_remnawave(remnawave.disable_user(sub.user_remna_id))

    updated = await subscription_dao.update_status(sub.id, SubscriptionStatus.DISABLED)
    if not updated:
        raise HTTPException(status_code=500, detail="Не удалось обновить подписку")
    await session.commit()
    return {"success": True, "subscription": _sub_to_dict(updated)}


# ─── Delete subscription ─────────────────────────────────────────────────────

@router.post("/user/{user_id}/delete")
@inject
async def delete_subscription(
    user_id: int,
    _admin: AdminUser,
    subscription_dao: FromDishka[SubscriptionDao],
    remnawave: FromDishka[Remnawave],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    sub = await subscription_dao.get_current(user_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Подписка не найдена")

    # Удаляем пользователя из панели Remnawave (доступ к VPN прекращается).
    await _sync_remnawave(remnawave.delete_user(sub.user_remna_id))

    updated = await subscription_dao.update_status(sub.id, SubscriptionStatus.DELETED)
    if not updated:
        raise HTTPException(status_code=500, detail="Не удалось удалить подписку")
    await session.commit()
    return {"success": True}


# ─── Grant subscription ───────────────────────────────────────────────────────

class GrantRequest(BaseModel):
    plan_id: int
    days: int
    is_trial: bool = False


@router.post("/user/{user_id}/grant")
@inject
async def grant_subscription(
    user_id: int,
    body: GrantRequest,
    _admin: AdminUser,
    user_dao: FromDishka[UserDao],
    plan_dao: FromDishka[PlanDao],
    subscription_dao: FromDishka[SubscriptionDao],
    remnawave: FromDishka[Remnawave],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    if body.days <= 0 or body.days > 3650:
        raise HTTPException(status_code=400, detail="Дней должно быть от 1 до 3650")

    user = await user_dao.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    plan = await plan_dao.get_by_id(body.plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Тариф не найден")

    snapshot = PlanSnapshotDto.from_plan(plan, duration=body.days)
    snapshot.is_trial = body.is_trial

    now = datetime.now(timezone.utc)

    # If user already has a subscription, extend it; otherwise create new
    existing = await subscription_dao.get_current(user_id)
    if existing and existing.is_active:
        base = existing.expire_at if existing.expire_at > now else now
        existing.expire_at = base + timedelta(days=body.days)
        existing.traffic_limit = plan.traffic_limit
        existing.device_limit = plan.device_limit
        existing.traffic_limit_strategy = plan.traffic_limit_strategy or TrafficLimitStrategy.NO_RESET
        existing.tag = plan.tag
        existing.plan_snapshot = snapshot
        # Синхронизируем срок/план в панели.
        await _sync_remnawave(
            remnawave.update_user(user=user, uuid=existing.user_remna_id, subscription=existing)
        )
        updated = await subscription_dao.update(existing)
        if not updated:
            raise HTTPException(status_code=500, detail="Не удалось обновить подписку")
        await session.commit()
        return {"success": True, "subscription": _sub_to_dict(updated), "action": "extended"}

    # Нет активной подписки — создаём пользователя в панели Remnawave.
    remna_user = None
    try:
        remna_user = await remnawave.create_user(user, plan=snapshot)
    except Exception:  # noqa: BLE001
        # Возможно, пользователь уже есть в панели (была удалённая подписка) —
        # находим его и обновляем под новый план.
        try:
            candidates = []
            if user.telegram_id:
                candidates = await remnawave.get_users_by_telegram_id(user.telegram_id)
            if not candidates and user.email:
                # ВНИМАНИЕ: в службе метод называется get_user_by_email — в
                # ЕДИНСТВЕННОМ числе, хотя в протоколе объявлен во множественном.
                # Здесь звали множественный, получали AttributeError, и вся ветка
                # спасения обрывалась: у веб-пользователя без телеграма это
                # единственный способ найти его в панели, поэтому выдача подписки
                # падала с «Не удалось создать пользователя в Remnawave», хотя
                # пользователь в панели был.
                by_email = getattr(remnawave, "get_user_by_email", None) or getattr(
                    remnawave, "get_users_by_email", None
                )
                if by_email is not None:
                    candidates = await by_email(user.email) or []
            if not candidates:
                # Последняя и самая надёжная попытка — по имени. Оно у нас
                # детерминированное (rs_web_<id> для веб-пользователей), поэтому
                # находит даже тех, у кого ни телеграма, ни совпадения по почте.
                try:
                    found = await remnawave.sdk.users.get_user_by_username(
                        f"rs_web_{user.id}"
                    )
                    if found is not None:
                        candidates = [found]
                except Exception:  # noqa: BLE001 — нет такого имени, идём дальше
                    candidates = []
            if candidates:
                remna_user = await remnawave.update_user(
                    user=user, uuid=candidates[0].uuid, plan=snapshot, reset_traffic=True,
                )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Ошибка синхронизации с Remnawave: {exc}",
            )
    if remna_user is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Не удалось создать пользователя в Remnawave",
        )

    new_sub = SubscriptionDto(
        user_id=user_id,
        user_remna_id=remna_user.uuid,
        status=SubscriptionStatus(remna_user.status),
        is_trial=body.is_trial,
        traffic_limit=plan.traffic_limit,
        device_limit=plan.device_limit,
        traffic_limit_strategy=plan.traffic_limit_strategy or TrafficLimitStrategy.NO_RESET,
        expire_at=remna_user.expire_at,
        url=remna_user.subscription_url,
        plan_snapshot=snapshot,
    )

    created = await subscription_dao.create(new_sub, user_id)
    if not created:
        raise HTTPException(status_code=500, detail="Не удалось создать подписку")
    await session.commit()
    return {"success": True, "subscription": _sub_to_dict(created), "action": "created"}


# ─── Reset trial ─────────────────────────────────────────────────────────────

@router.post("/user/{user_id}/reset-trial")
@inject
async def reset_trial(
    user_id: int,
    _admin: AdminUser,
    user_dao: FromDishka[UserDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    user = await user_dao.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    user.is_trial_available = True
    updated = await user_dao.update(user)
    if not updated:
        raise HTTPException(status_code=500, detail="Не удалось обновить пользователя")
    await session.commit()
    return {"success": True, "is_trial_available": True}


# ─── Действия над подпиской юзера (паритет с ботом) ───────────────────────────
# Переиспользуем базовые интеракторы (они сами синхронят Remnawave + коммитят
# свой uow). Зовём `._execute(admin, ...)` НАПРЯМУЮ — в обход enum-права
# (USER_EDITOR): доступ уже проверен в _common (раздел subscriptions + can_write),
# как сделано для теста шлюзов. См. память проекта.


async def _run_user_action(coro: Any) -> None:
    """Гоняет интерактор, превращая его ошибки в понятные HTTP-коды."""
    try:
        await coro
    except HTTPException:
        raise
    except PermissionDeniedError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для действия над этим пользователем",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Ошибка синхронизации с Remnawave: {exc}",
        )


@router.post("/user/{user_id}/reset-traffic")
@inject
async def reset_traffic(
    user_id: int,
    admin: AdminUser,
    reset_user_traffic: FromDishka[ResetUserTraffic],
) -> dict[str, Any]:
    await _run_user_action(reset_user_traffic._execute(admin, user_id))
    return {"success": True}


@router.post("/user/{user_id}/reissue")
@inject
async def reissue_subscription(
    user_id: int,
    admin: AdminUser,
    reissue_user_subscription: FromDishka[ReissueUserSubscription],
) -> dict[str, Any]:
    await _run_user_action(reissue_user_subscription._execute(admin, user_id))
    return {"success": True}


@router.post("/user/{user_id}/referral-reset")
@inject
async def referral_reset(
    user_id: int,
    admin: AdminUser,
    reset_user_referral_code: FromDishka[ResetUserReferralCode],
) -> dict[str, Any]:
    await _run_user_action(reset_user_referral_code._execute(admin, user_id))
    return {"success": True}


# ─── Устройства пользователя (список + удаление) ──────────────────────────────


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if value else None


@router.get("/user/{user_id}/devices")
@inject
async def user_devices(
    user_id: int,
    _admin: AdminUser,
    subscription_dao: FromDishka[SubscriptionDao],
    remnawave: FromDishka[Remnawave],
) -> dict[str, Any]:
    sub = await subscription_dao.get_current(user_id)
    if not sub:
        return {"devices": [], "count": 0}
    devices = await _sync_remnawave(remnawave.get_devices(sub.user_remna_id))
    return {
        "devices": [
            {
                "hwid": d.hwid,
                "platform": getattr(d, "platform", None),
                "device_model": getattr(d, "device_model", None),
                "os_version": getattr(d, "os_version", None),
                "user_agent": getattr(d, "user_agent", None),
                "created_at": _iso(getattr(d, "created_at", None)),
                "updated_at": _iso(getattr(d, "updated_at", None)),
            }
            for d in (devices or [])
        ],
        "count": len(devices or []),
    }


class DeleteDeviceRequest(BaseModel):
    hwid: str


@router.post("/user/{user_id}/devices/delete")
@inject
async def user_device_delete(
    user_id: int,
    body: DeleteDeviceRequest,
    _admin: AdminUser,
    subscription_dao: FromDishka[SubscriptionDao],
    remnawave: FromDishka[Remnawave],
) -> dict[str, Any]:
    sub = await subscription_dao.get_current(user_id)
    if not sub:
        raise HTTPException(status_code=404, detail="У пользователя нет активной подписки")
    # Админ удаляет устройство напрямую (без юзер-настройки device_single_reset и
    # её кулдауна) + сбрасываем активные соединения, чтобы устройство отвалилось.
    await _sync_remnawave(remnawave.delete_device(sub.user_remna_id, body.hwid))
    await _sync_remnawave(remnawave.drop_connections(sub.user_remna_id))
    return {"success": True}


# ─── Транзакции пользователя (в карточке) ─────────────────────────────────────


@router.get("/user/{user_id}/transactions")
@inject
async def user_transactions(
    user_id: int,
    admin: AdminUser,
    session: FromDishka[AsyncSession],
    limit: int = 50,
) -> dict[str, Any]:
    limit = max(1, min(limit, 200))
    redact = is_readonly_admin(admin)
    rows = (
        await session.execute(
            text(
                """
                SELECT payment_id, status::text AS status, is_test,
                       purchase_type::text AS purchase_type,
                       gateway_type::text AS gateway_type,
                       created_at, updated_at,
                       pricing->>'final_amount' AS final_amount,
                       currency::text AS currency,
                       plan_snapshot->>'name' AS plan_name,
                       plan_snapshot->>'duration' AS plan_duration
                FROM transactions
                WHERE user_id = :uid
                ORDER BY created_at DESC NULLS LAST
                LIMIT :limit
                """
            ),
            {"uid": user_id, "limit": limit},
        )
    ).all()
    return {
        "items": [
            {
                "payment_id": None if redact else str(r.payment_id),
                "status": r.status,
                "gateway_type": r.gateway_type,
                "purchase_type": r.purchase_type,
                "is_test": r.is_test,
                "amount": r.final_amount,
                "currency": r.currency,
                "plan_name": r.plan_name,
                "plan_duration": int(r.plan_duration) if r.plan_duration else None,
                "created_at": _iso(r.created_at),
                "updated_at": _iso(r.updated_at),
            }
            for r in rows
        ]
    }


# ─── Лимиты трафика/устройств ─────────────────────────────────────────────────


class TrafficLimitRequest(BaseModel):
    traffic_limit: int  # ГБ, 0 = безлимит


class DeviceLimitRequest(BaseModel):
    device_limit: int  # 0 = безлимит
    # Ставим значение ниже «тариф + докупленные» только с явного согласия админа:
    # иначе правка в карточке молча отняла бы у человека оплаченное место.
    revoke_extras: bool = False


@router.post("/user/{user_id}/traffic-limit")
@inject
async def set_traffic_limit(
    user_id: int,
    body: TrafficLimitRequest,
    admin: AdminUser,
    update_traffic_limit: FromDishka[UpdateTrafficLimit],
) -> dict[str, Any]:
    await _run_user_action(
        update_traffic_limit._execute(
            admin, UpdateTrafficLimitDto(user_id=user_id, traffic_limit=max(0, body.traffic_limit))
        )
    )
    return {"success": True}


@router.post("/user/{user_id}/device-limit")
@inject
async def set_device_limit(
    user_id: int,
    body: DeviceLimitRequest,
    admin: AdminUser,
    update_device_limit: FromDishka[UpdateDeviceLimit],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    value = max(0, body.device_limit)
    floor, slots = await _extras_floor(session, user_id)
    if slots and value > 0 and value < floor and not body.revoke_extras:
        until = min(s["ends_at"] for s in slots)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Докуплено +{len(slots)} устр. до {until.strftime('%d.%m.%Y')}. "
                f"Отменить докупку и поставить {value}?"
            ),
        )
    if slots and body.revoke_extras and value < floor:
        await session.execute(
            text(
                "UPDATE extra_device_slots SET status = 'revoked', end_reason = 'admin_limit', "
                "ended_at = now() WHERE id = ANY(:ids)"
            ),
            {"ids": [s["id"] for s in slots]},
        )
        # Ручной commit: overlay-ручки живут вне UoW базы (память admin-endpoints-commit).
        await session.commit()
    await _run_user_action(
        update_device_limit._execute(
            admin, UpdateDeviceLimitDto(user_id=user_id, device_limit=value)
        )
    )
    return {"success": True}


# ─── Докупленные места под устройства (карточка пользователя) ─────────────────


def _first_rub_gateway_type(_st: Any) -> Any:
    """Чем подписать строку возврата: колонка gateway_type NOT NULL.

    Возврат делает админ руками, шлюза у него нет — берём тот же тип, каким помечены
    покупки с баланса, чтобы строка не выпала из отчётов по валюте.
    """
    return PaymentGatewayType.YOOMONEY


async def _extras_floor(session: AsyncSession, user_id: int) -> tuple[int, list[dict[str, Any]]]:
    """Ниже какого лимита нельзя опускать, и какие места этому мешают.

    Пол — «тарифный лимит + действующие докупки»: ровно то, что считает крон. Сбой
    чтения (таблиц ещё нет) — пол 0 и пустой список: правка лимита не должна падать.
    """
    try:
        rows = (
            await session.execute(
                text(
                    "SELECT e.id, e.ends_at, (s.plan_snapshot->>'device_limit')::int "
                    "FROM extra_device_slots e "
                    "JOIN subscriptions s ON s.id = e.subscription_id "
                    "JOIN users u ON u.current_subscription_id = s.id "
                    "WHERE e.user_id = :uid AND u.id = :uid AND e.status = 'active' "
                    "ORDER BY e.ends_at"
                ),
                {"uid": user_id},
            )
        ).all()
    except Exception:  # noqa: BLE001
        await session.rollback()
        return 0, []
    if not rows:
        return 0, []
    plan_limit = int(rows[0][2] or 0)
    slots = [{"id": int(r[0]), "ends_at": r[1]} for r in rows]
    return plan_limit + len(slots), slots


async def _extra_device_summary(
    session: AsyncSession, user_id: int, sub: SubscriptionDto
) -> dict[str, Any]:
    plan_limit = getattr(sub.plan_snapshot, "device_limit", None) if sub.plan_snapshot else None
    try:
        row = (
            await session.execute(
                text(
                    "SELECT count(*), min(ends_at) FROM extra_device_slots "
                    "WHERE user_id = :uid AND subscription_id = :sid AND status = 'active'"
                ),
                {"uid": user_id, "sid": sub.id},
            )
        ).first()
    except Exception:  # noqa: BLE001 — карточка важнее подписи под числом
        await session.rollback()
        return {"plan_device_limit": plan_limit}
    return {
        "plan_device_limit": plan_limit,
        "extra_devices_active": int(row[0] or 0) if row else 0,
        "extra_until": row[1].isoformat() if row and row[1] is not None else None,
    }


@router.get("/user/{user_id}/extra-devices")
@inject
async def user_extra_devices(
    user_id: int,
    admin: AdminUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    """Журнал докупок человека: места и заказы. PREVIEW-админу суммы не показываем."""
    hide_money = is_readonly_admin(admin)
    slots = [
        {
            "id": int(r[0]),
            "subscription_id": int(r[1]),
            "status": r[2],
            "starts_at": r[3].isoformat() if r[3] else None,
            "ends_at": r[4].isoformat() if r[4] else None,
            "end_reason": r[5],
            "devices_removed": int(r[6] or 0),
        }
        for r in (
            await session.execute(
                text(
                    "SELECT id, subscription_id, status, starts_at, ends_at, end_reason, "
                    "devices_removed FROM extra_device_slots WHERE user_id = :uid "
                    "ORDER BY created_at DESC LIMIT 50"
                ),
                {"uid": user_id},
            )
        ).all()
    ]
    orders = [
        {
            "id": int(r[0]),
            "status": r[1],
            "kind": r[2],
            "source": r[3],
            "amount": None if hide_money else float(r[4]),
            "created_at": r[5].isoformat() if r[5] else None,
            "reason": r[6],
            "slot_id": r[7],
        }
        for r in (
            await session.execute(
                text(
                    "SELECT id, status, kind, source, amount, created_at, reason, slot_id "
                    "FROM extra_device_orders WHERE user_id = :uid ORDER BY created_at DESC LIMIT 50"
                ),
                {"uid": user_id},
            )
        ).all()
    ]
    return {"slots": slots, "orders": orders}


class RevokeExtraRequest(BaseModel):
    refund_unused: bool = False


@router.post("/user/{user_id}/extra-devices/{slot_id}/revoke")
@inject
async def revoke_extra_device(
    user_id: int,
    slot_id: int,
    body: RevokeExtraRequest,
    admin: AdminUser,
    session: FromDishka[AsyncSession],
    remnawave: FromDishka[Remnawave],
    transaction_dao: FromDishka[TransactionDao],
) -> dict[str, Any]:
    """Отменить докупку: снять место и (по желанию) вернуть неиспользованную стоимость.

    Лимит считаем тем же правилом, что и крон, — «тариф + оставшиеся места», чтобы
    ручная щедрость владельца (лимит выше тарифного) не пропала вместе с докупкой.
    """
    from src.infrastructure.services import overlay_extra_device as extra

    st = await extra.lock_state(session, user_id)
    slot = st.slot(slot_id)
    if slot is None:
        await session.rollback()
        raise HTTPException(status_code=404, detail="Действующая докупка не найдена")

    orders = [
        o
        for o in await extra.load_carry_orders(session, slot.subscription_id)
        if o.get("slot_id") == slot_id
    ]
    # Возврат — вниз до копейки: округление в пользу магазина здесь неуместно, но и
    # дарить лишнее незачем. Считается той же функцией, что и перенос при смене тарифа.
    refunded = extra.unused_value(orders, datetime.now(timezone.utc)) if body.refund_unused else None

    await session.execute(
        text(
            "UPDATE extra_device_slots SET status = 'revoked', end_reason = 'admin_revoke', "
            "ended_at = now() WHERE id = :id AND status = 'active'"
        ),
        {"id": slot_id},
    )
    if refunded is not None and refunded > 0:
        await session.execute(
            text("UPDATE users SET cabinet_balance = cabinet_balance + :a WHERE id = :u"),
            {"a": refunded, "u": user_id},
        )
        # Возврат — отдельной строкой в transactions, а не только прибавкой к балансу.
        # Без неё деньги «возвращены», а в выручке и плитке возвратов покупка стоит
        # целиком: отчёты показывали бы доход, которого уже нет. Строка ЧАСТИЧНАЯ
        # (возвращаем только непрожитый остаток), поэтому исходный счёт не трогаем —
        # пометить его REFUNDED целиком было бы неправдой.
        await transaction_dao.create(
            TransactionDto(
                payment_id=uuid4(),
                user_id=user_id,
                status=TransactionStatus.REFUNDED,
                purchase_type=PurchaseType.NEW,
                gateway_type=_first_rub_gateway_type(st),
                gateway_display_name="Возврат · устройство",
                pricing=PriceDetailsDto(
                    original_amount=refunded, discount_percent=0, final_amount=refunded
                ),
                currency=Currency.RUB,
                plan_snapshot=extra.synthetic_snapshot(1),
            )
        )
    active_after = len([s for s in st.slots if s.subscription_id == st.sub_id and s.id != slot_id])
    target = extra.target_limit(st.device_limit, st.plan_device_limit, active_after, 1, False)
    try:
        if target != st.device_limit:
            await extra.set_limit(session, getattr(remnawave, "sdk", None), st, target)
    except extra.PanelRejected as exc:
        await session.rollback()
        raise HTTPException(status_code=502, detail=f"Панель не приняла лимит: {exc}") from exc
    # Ручной commit: overlay-ручки живут вне UoW базы (память admin-endpoints-commit).
    await session.commit()
    return {
        "success": True,
        "device_limit": target,
        "refunded": float(refunded) if refunded is not None else None,
    }


# ─── Докупленный трафик (карточка пользователя) ───────────────────────────────


@router.get("/user/{user_id}/extra-traffic")
@inject
async def user_extra_traffic(
    user_id: int,
    admin: AdminUser,
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    """Журнал докупок трафика: прибавки и заказы. PREVIEW-админу суммы не показываем."""
    hide_money = is_readonly_admin(admin)
    grants = [
        {
            "id": int(r[0]),
            "subscription_id": int(r[1]),
            "status": r[2],
            "gb": int(r[3] or 0),
            "strategy": r[4],
            "granted_at": r[5].isoformat() if r[5] else None,
            "ends_at": r[6].isoformat() if r[6] else None,
            "end_reason": r[7],
        }
        for r in (
            await session.execute(
                text(
                    "SELECT id, subscription_id, status, gb, strategy, granted_at, ends_at, "
                    "end_reason FROM extra_traffic_grants WHERE user_id = :uid "
                    "ORDER BY created_at DESC LIMIT 50"
                ),
                {"uid": user_id},
            )
        ).all()
    ]
    orders = [
        {
            "id": int(r[0]),
            "status": r[1],
            "source": r[2],
            "gb": int(r[3] or 0),
            "amount": None if hide_money else float(r[4]),
            "created_at": r[5].isoformat() if r[5] else None,
            "reason": r[6],
            "grant_id": r[7],
        }
        for r in (
            await session.execute(
                text(
                    "SELECT id, status, source, gb, amount, created_at, reason, grant_id "
                    "FROM extra_traffic_orders WHERE user_id = :uid ORDER BY created_at DESC LIMIT 50"
                ),
                {"uid": user_id},
            )
        ).all()
    ]
    return {"grants": grants, "orders": orders}


class RevokeExtraTrafficRequest(BaseModel):
    # Решение владельца Р-4: по умолчанию НЕ возвращаем, кнопка спрашивает каждый раз.
    refund: bool = False


@router.post("/user/{user_id}/extra-traffic/{grant_id}/revoke")
@inject
async def revoke_extra_traffic(
    user_id: int,
    grant_id: int,
    body: RevokeExtraTrafficRequest,
    admin: AdminUser,
    session: FromDishka[AsyncSession],
    remnawave: FromDishka[Remnawave],
    transaction_dao: FromDishka[TransactionDao],
) -> dict[str, Any]:
    """Отозвать докупленный трафик: снять объём и (по желанию) вернуть ₽ на баланс.

    Лимит считаем тем же правилом, что и крон, — «тариф + оставшиеся прибавки», чтобы
    ручная щедрость владельца (лимит выше тарифного) не пропала вместе с докупкой.

    Возврат — ПОЛНОЙ суммой заказа, а не «непрожитой частью»: у трафика нет времени,
    за которое можно посчитать долю, — есть только объём. Делить его по остатку
    расхода значило бы лезть в панель за расходом посреди операции отзыва.
    """
    from src.infrastructure.services import overlay_extra_traffic as extra

    st = await extra.lock_state(session, user_id)
    grant = next((g for g in st.grants if g.id == grant_id), None)
    if grant is None:
        await session.rollback()
        raise HTTPException(status_code=404, detail="Действующая докупка трафика не найдена")

    from decimal import Decimal

    refunded = None
    paid_ids: list[Any] = []
    if body.refund:
        rows = (
            await session.execute(
                text(
                    "SELECT payment_id, amount FROM extra_traffic_orders "
                    "WHERE grant_id = :gid AND status = 'applied'"
                ),
                {"gid": grant_id},
            )
        ).all()
        refunded = sum((Decimal(str(r[1])) for r in rows), Decimal(0))
        paid_ids = [r[0] for r in rows if r[0] is not None]

    await session.execute(
        text(
            "UPDATE extra_traffic_grants SET status = 'revoked', end_reason = 'admin_revoke', "
            "ended_at = now() WHERE id = :id AND status = 'active'"
        ),
        {"id": grant_id},
    )
    if refunded is not None and refunded > 0:
        await session.execute(
            text("UPDATE users SET cabinet_balance = cabinet_balance + :a WHERE id = :u"),
            {"a": refunded, "u": user_id},
        )
        # ИСХОДНЫЕ СЧЕТА ПЕРЕВОДИМ В REFUNDED, а не заводим новую строку рядом.
        # Возврат здесь всегда ПОЛНЫЙ (у объёма нет доли «непрожитого»), поэтому
        # честно именно это: выручка считается суммой COMPLETED, и отдельная строка
        # REFUNDED рядом с живой COMPLETED показывала бы доход, которого уже нет, —
        # ровно то, что комментарий обещал исправить, но не исправлял.
        # У докупки УСТРОЙСТВА возврат частичный, и там правило обратное — отдельная
        # строка; не перепутайте.
        # ЧЕРЕЗ DAO, А НЕ СЫРЫМ SQL. Плитка «Возвраты» берёт дату возврата из
        # `updated_at`, а его проставляет ORM на переходе. Сырой UPDATE оставил бы
        # там дату ПОКУПКИ — возврат показался бы сделанным месяц назад.
        for paid in paid_ids:
            await transaction_dao.update_status(paid, TransactionStatus.REFUNDED)
    active_after = sum(
        g.gb for g in st.grants if g.subscription_id == st.sub_id and g.id != grant_id
    )
    target = extra.target_limit(
        st.traffic_limit, st.plan_traffic_limit, active_after, grant.gb
    )
    try:
        if target != st.traffic_limit:
            from src.core.utils.converters import gb_to_bytes

            await extra.set_traffic_limit(
                session, getattr(remnawave, "sdk", None), st, gb_to_bytes(target)
            )
    except extra.PanelRejected as exc:
        await session.rollback()
        raise HTTPException(status_code=502, detail=f"Панель не приняла лимит: {exc}") from exc
    # Ручной commit: overlay-ручки живут вне UoW базы (память admin-endpoints-commit).
    await session.commit()
    return {
        "success": True,
        "traffic_limit_gb": target,
        "refunded": float(refunded) if refunded is not None else None,
    }


# ─── Смена сквада (internal/external) — тумблер членства ──────────────────────


class SquadToggleRequest(BaseModel):
    squad_id: str
    external: bool = False


@router.post("/user/{user_id}/squad-toggle")
@inject
async def squad_toggle(
    user_id: int,
    body: SquadToggleRequest,
    admin: AdminUser,
    toggle_internal: FromDishka[ToggleInternalSquad],
    toggle_external: FromDishka[ToggleExternalSquad],
) -> dict[str, Any]:
    try:
        squad_uuid = UUID(body.squad_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Некорректный UUID сквада")
    if body.external:
        await _run_user_action(
            toggle_external._execute(admin, ToggleExternalSquadDto(user_id=user_id, squad_id=squad_uuid))
        )
    else:
        await _run_user_action(
            toggle_internal._execute(admin, ToggleInternalSquadDto(user_id=user_id, squad_id=squad_uuid))
        )
    return {"success": True}


# ─── Синхронизация с Remnawave ────────────────────────────────────────────────


class SyncRequest(BaseModel):
    direction: str = "from_remnawave"  # "from_remnawave" (панель→бот) | "from_remnashop"


@router.post("/user/{user_id}/sync")
@inject
async def sync_subscription(
    user_id: int,
    body: SyncRequest,
    admin: AdminUser,
    from_remnawave: FromDishka[SyncSubscriptionFromRemnawave],
    from_remnashop: FromDishka[SyncSubscriptionFromRemnashop],
) -> dict[str, Any]:
    if body.direction == "from_remnashop":
        await _run_user_action(from_remnashop._execute(admin, user_id))
    else:
        await _run_user_action(from_remnawave._execute(admin, user_id))
    return {"success": True}


# ─── Сообщение пользователю (в его Telegram) ─────────────────────────────────


class MessageRequest(BaseModel):
    text: str


@router.post("/user/{user_id}/message")
@inject
async def send_message(
    user_id: int,
    body: MessageRequest,
    admin: AdminUser,
    send_message_to_user: FromDishka[SendMessageToUser],
    user_dao: FromDishka[UserDao],
    notifier: FromDishka[Notifier],
) -> dict[str, Any]:
    text_msg = (body.text or "").strip()
    if not text_msg:
        raise HTTPException(status_code=400, detail="Пустое сообщение")
    payload = MessagePayloadDto(
        i18n_key="raw-message",
        i18n_kwargs={"content": text_msg},
        delete_after=None,  # не самоудалять (см. фикс рассылок delete_after)
    )

    # Раньше любая неудача превращалась в «у пользователя нет Telegram» — хотя
    # причины разные: профиль действительно без привязки (импортированные из
    # панели подписчики, веб-регистрация по email) или отправка сорвалась
    # (пользователь заблокировал бота). Отвечаем конкретно.
    target = await user_dao.get_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if target.telegram_id is None:
        return {"success": True, "delivered": False, "reason": "no_telegram"}

    # Сообщение самому себе: use-case требует СТРОГО старшую роль актора, поэтому
    # владелец, отправляя в свой же профиль, получал 403. Себе — шлём напрямую.
    if target.id == admin.id:
        if is_readonly_admin(admin):
            raise HTTPException(status_code=403, detail="Роль «только просмотр»: отправка недоступна")
        sent = await notifier.notify_user(user=target, payload=payload)
        return {
            "success": True,
            "delivered": bool(sent),
            "reason": None if sent else "send_failed",
        }

    delivered = False
    try:
        delivered = bool(
            await send_message_to_user._execute(
                admin, SendMessageToUserDto(user_id=user_id, payload=payload)
            )
        )
    except HTTPException:
        raise
    except PermissionDeniedError:
        raise HTTPException(status_code=403, detail="Недостаточно прав для действия над этим пользователем")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Ошибка отправки: {exc}")
    return {
        "success": True,
        "delivered": delivered,
        "reason": None if delivered else "send_failed",
    }


# ─── Add points ──────────────────────────────────────────────────────────────

class AddPointsRequest(BaseModel):
    points: int


@router.post("/user/{user_id}/points")
@inject
async def add_points(
    user_id: int,
    body: AddPointsRequest,
    _admin: AdminUser,
    user_dao: FromDishka[UserDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    user = await user_dao.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    user.points = max(0, (user.points or 0) + body.points)
    updated = await user_dao.update(user)
    if not updated:
        raise HTTPException(status_code=500, detail="Не удалось обновить пользователя")
    await session.commit()
    return {"success": True, "points": updated.points}


# ─── Рублёвый баланс-кошелёк (отдельно от баллов) ─────────────────────────────

class AdjustBalanceRequest(BaseModel):
    amount: float  # ₽: положительное — начислить, отрицательное — списать


@router.post("/user/{user_id}/balance")
@inject
async def adjust_balance(
    user_id: int,
    body: AdjustBalanceRequest,
    _admin: AdminUser,
    user_dao: FromDishka[UserDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    from decimal import Decimal

    user = await user_dao.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    amount = Decimal(str(body.amount))
    # GREATEST(0, …) — не уходим в минус при списании.
    new_balance = (
        await session.execute(
            text(
                "UPDATE users SET cabinet_balance = GREATEST(0, cabinet_balance + :amt) "
                "WHERE id = :id RETURNING cabinet_balance"
            ),
            {"amt": amount, "id": user_id},
        )
    ).scalar_one()
    await session.commit()
    return {"success": True, "cabinet_balance": float(new_balance)}
