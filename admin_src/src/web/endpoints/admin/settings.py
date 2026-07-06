from typing import Any, Optional

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.dao import SettingsDao
from src.core.enums import AccessMode, ReferralAccrualStrategy, ReferralLevel, ReferralRewardStrategy, ReferralRewardType

from ._common import AdminUser

router = APIRouter(prefix="/settings", tags=["Admin - Settings"])


def _settings_to_dict(s: Any) -> dict[str, Any]:
    def _enum_val(v: Any) -> Any:
        return v.value if hasattr(v, "value") else str(v)

    return {
        "default_currency": _enum_val(s.default_currency),
        "access": {
            "mode": _enum_val(s.access.mode),
            "registration_allowed": s.access.registration_allowed,
            "payments_allowed": s.access.payments_allowed,
        },
        "requirements": {
            "rules_required": s.requirements.rules_required,
            "channel_required": s.requirements.channel_required,
            "rules_link": s.requirements.rules_url,
            "channel_link": s.requirements.channel_link.get_secret_value(),
            "channel_id": s.requirements.channel_id,
        },
        "referral": {
            "enable": s.referral.enable,
            "level": _enum_val(s.referral.level),
            "accrual_strategy": _enum_val(s.referral.accrual_strategy),
            "reward": {
                "type": _enum_val(s.referral.reward.type),
                "strategy": _enum_val(s.referral.reward.strategy),
                "config": {_enum_val(k): v for k, v in s.referral.reward.config.items()},
            },
        },
        "backup": {
            "enabled": s.backup.enabled,
            "interval_hours": s.backup.interval_hours,
            "max_files": s.backup.max_files,
            "send_to_chat": s.backup.send_to_chat,
        },
        "extra": {
            "device_single_reset": {
                "enabled": s.extra.device_single_reset.enabled,
                "cooldown_hours": s.extra.device_single_reset.cooldown_hours,
            },
            "device_all_reset": {
                "enabled": s.extra.device_all_reset.enabled,
                "cooldown_hours": s.extra.device_all_reset.cooldown_hours,
            },
            "link_reset": {
                "enabled": s.extra.link_reset.enabled,
                "cooldown_hours": s.extra.link_reset.cooldown_hours,
            },
            "trial_channel_guard": s.extra.trial_channel_guard,
            "mini_app_reserve": s.extra.mini_app_reserve,
        },
        "notifications": {
            k: v for k, v in s.notifications.settings.items()
        },
    }


@router.get("")
@inject
async def get_settings(
    _admin: AdminUser,
    settings_dao: FromDishka[SettingsDao],
) -> dict[str, Any]:
    s = await settings_dao.get()
    return _settings_to_dict(s)


class AccessSettingsUpdate(BaseModel):
    mode: Optional[str] = None
    registration_allowed: Optional[bool] = None
    payments_allowed: Optional[bool] = None


class ReferralUpdate(BaseModel):
    enable: Optional[bool] = None
    level: Optional[int] = None
    accrual_strategy: Optional[str] = None
    reward_type: Optional[str] = None
    reward_strategy: Optional[str] = None  # AMOUNT | PERCENT
    reward_value: Optional[int] = None  # legacy: значение L1
    reward_l1: Optional[int] = None  # награда 1-го уровня (% или сумма)
    reward_l2: Optional[int] = None  # награда 2-го уровня


class BackupUpdate(BaseModel):
    enabled: Optional[bool] = None
    interval_hours: Optional[int] = None
    max_files: Optional[int] = None
    send_to_chat: Optional[bool] = None


class SettingsUpdateRequest(BaseModel):
    access: Optional[AccessSettingsUpdate] = None
    referral: Optional[ReferralUpdate] = None
    backup: Optional[BackupUpdate] = None
    registration_allowed: Optional[bool] = None
    payments_allowed: Optional[bool] = None
    rules_required: Optional[bool] = None
    channel_required: Optional[bool] = None
    channel_link: Optional[str] = None
    rules_link: Optional[str] = None
    trial_channel_guard: Optional[bool] = None
    mini_app_reserve: Optional[bool] = None
    notifications: Optional[dict[str, bool]] = None


@router.put("")
@inject
async def update_settings(
    body: SettingsUpdateRequest,
    _admin: AdminUser,
    settings_dao: FromDishka[SettingsDao],
    session: FromDishka[AsyncSession],
) -> dict[str, Any]:
    from pydantic import SecretStr

    s = await settings_dao.get()

    if body.access:
        if body.access.mode is not None:
            try:
                s.access.mode = AccessMode(body.access.mode.upper())
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid access mode: {body.access.mode}")
        if body.access.registration_allowed is not None:
            s.access.registration_allowed = body.access.registration_allowed
        if body.access.payments_allowed is not None:
            s.access.payments_allowed = body.access.payments_allowed

    if body.registration_allowed is not None:
        s.access.registration_allowed = body.registration_allowed
    if body.payments_allowed is not None:
        s.access.payments_allowed = body.payments_allowed
    if body.rules_required is not None:
        s.requirements.rules_required = body.rules_required
    if body.channel_required is not None:
        s.requirements.channel_required = body.channel_required
    if body.channel_link is not None:
        s.requirements.channel_link = SecretStr(body.channel_link)
    if body.rules_link is not None:
        s.requirements.rules_link = SecretStr(body.rules_link)
    if body.trial_channel_guard is not None:
        s.extra.trial_channel_guard = body.trial_channel_guard
    if body.mini_app_reserve is not None:
        s.extra.mini_app_reserve = body.mini_app_reserve

    if body.referral:
        if body.referral.enable is not None:
            s.referral.enable = body.referral.enable
        if body.referral.level is not None:
            try:
                s.referral.level = ReferralLevel(body.referral.level)
            except ValueError:
                raise HTTPException(status_code=400, detail="Недопустимый уровень рефералки")
        if body.referral.reward_type is not None:
            try:
                s.referral.reward.type = ReferralRewardType(body.referral.reward_type.upper())
            except ValueError:
                raise HTTPException(status_code=400, detail="Недопустимый тип награды")
        if body.referral.reward_strategy is not None:
            try:
                s.referral.reward.strategy = ReferralRewardStrategy(body.referral.reward_strategy.upper())
            except ValueError:
                raise HTTPException(status_code=400, detail="Недопустимая стратегия награды")
        # Награды по уровням: reward_l1/reward_l2 (или legacy reward_value = L1).
        l1 = body.referral.reward_l1 if body.referral.reward_l1 is not None else body.referral.reward_value
        if l1 is not None or body.referral.reward_l2 is not None:
            cfg = dict(s.referral.reward.config)
            if l1 is not None:
                cfg[ReferralLevel.FIRST] = max(0, l1)
            if body.referral.reward_l2 is not None:
                cfg[ReferralLevel.SECOND] = max(0, body.referral.reward_l2)
            s.referral.reward.config = cfg
        if body.referral.accrual_strategy is not None:
            try:
                s.referral.accrual_strategy = ReferralAccrualStrategy(body.referral.accrual_strategy.upper())
            except ValueError:
                raise HTTPException(status_code=400, detail="Недопустимая стратегия начисления")

    if body.backup:
        if body.backup.enabled is not None:
            s.backup.enabled = body.backup.enabled
        if body.backup.interval_hours is not None:
            s.backup.interval_hours = body.backup.interval_hours
        if body.backup.max_files is not None:
            s.backup.max_files = body.backup.max_files
        if body.backup.send_to_chat is not None:
            s.backup.send_to_chat = body.backup.send_to_chat

    if body.notifications is not None:
        new_settings = dict(s.notifications.settings)
        new_settings.update(body.notifications)
        s.notifications.settings = new_settings

    updated = await settings_dao.update(s)
    if not updated:
        raise HTTPException(status_code=500, detail="Не удалось обновить")
    await session.commit()
    return _settings_to_dict(updated)
