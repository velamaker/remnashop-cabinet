from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Remnawave
from src.application.common.dao import PlanDao, SubscriptionDao, UserDao
from src.application.dto import MessagePayloadDto, PlanSnapshotDto, SubscriptionDto
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
from src.core.enums import SubscriptionStatus
from src.core.exceptions import PermissionDeniedError
from remnapy.enums.users import TrafficLimitStrategy

from ._common import AdminUser

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
) -> dict[str, Any]:
    sub = await subscription_dao.get_current(user_id)
    all_subs = await subscription_dao.get_all_by_user(user_id)
    return {
        "current": _sub_to_dict(sub) if sub else None,
        "history": [_sub_to_dict(s) for s in all_subs[:20]],
    }


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
    # Продление (+): считаем от текущего срока или от now, если уже истёк.
    # Убавление (−): считаем строго от текущего срока, не опускаем ниже now.
    if body.days >= 0:
        base = sub.expire_at if sub.expire_at > now else now
        sub.expire_at = base + timedelta(days=body.days)
    else:
        new_expire = sub.expire_at + timedelta(days=body.days)
        sub.expire_at = new_expire if new_expire > now else now

    # Сначала синхронизируем срок в панели Remnawave (если упадёт — локально не коммитим).
    await _sync_remnawave(remnawave.update_user(user=user, uuid=sub.user_remna_id, subscription=sub))

    updated = await subscription_dao.update(sub)
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
                candidates = await remnawave.get_users_by_email(user.email)
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
    _admin: AdminUser,
    session: FromDishka[AsyncSession],
    limit: int = 50,
) -> dict[str, Any]:
    limit = max(1, min(limit, 200))
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
                "payment_id": str(r.payment_id),
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
) -> dict[str, Any]:
    await _run_user_action(
        update_device_limit._execute(
            admin, UpdateDeviceLimitDto(user_id=user_id, device_limit=max(0, body.device_limit))
        )
    )
    return {"success": True}


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
) -> dict[str, Any]:
    text_msg = (body.text or "").strip()
    if not text_msg:
        raise HTTPException(status_code=400, detail="Пустое сообщение")
    payload = MessagePayloadDto(
        i18n_key="raw-message",
        i18n_kwargs={"content": text_msg},
        delete_after=None,  # не самоудалять (см. фикс рассылок delete_after)
    )
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
    # delivered=False обычно значит, что у пользователя нет привязанного Telegram.
    return {"success": True, "delivered": delivered}


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
