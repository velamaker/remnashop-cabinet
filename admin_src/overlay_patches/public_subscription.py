"""Публичные ручки подписки кабинета.

ЧТО ДЕЛАЕМ. Базовый набор ручек рассчитан на пользователя телеграма. Кабинету
нужно больше: выдать подписку человеку, зарегистрированному почтой (у него нет
telegram_id, а панель требует имя), отдать резервный доступ истёкшему, подменить
адрес ссылки подписки на наш алиас, проверить почту перед покупкой через шлюз.

ПОЧЕМУ ЦЕЛИКОМ СВОЙ МОДУЛЬ. Здесь переписаны почти все ручки раздела — точечные
правки означали бы подмену каждой из двенадцати. Зато подключение — одна строка
в базе: `public/__init__.py` берёт `router` ПО ИМЕНИ из модуля и включает его.
Значит достаточно собрать свой роутер здесь и подставить его на место базового
до того, как выполнится включение.

Пути и модели ответов совпадают с базовыми — снаружи это тот же раздел API,
поэтому кабинету и мобильным клиентам ничего менять не нужно.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

import httpx
from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field
from remnapy.models.hwid import HwidDeviceDto
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Remnawave
from src.application.common.dao import (
    PaymentGatewayDao,
    PlanDao,
    SubscriptionDao,
    TransactionDao,
)
from src.application.common.uow import UnitOfWork
from src.application.dto import PlanDto, PlanSnapshotDto, TransactionDto, UserDto
from src.application.services import PricingService
from src.application.use_cases.gateways.commands.payment import (
    CreatePayment,
    CreatePaymentDto,
    ProcessPayment,
    ProcessPaymentDto,
)
from src.application.use_cases.plan.queries.match import MatchPlan, MatchPlanDto
from src.application.use_cases.promocode.commands.activate import (
    ActivatePromocode,
    ActivatePromocodeDto,
)
from src.application.use_cases.promocode.queries.validate import ValidatePromocode
from src.application.use_cases.remnawave.commands.management import (
    DeleteUserAllDevices,
    DeleteUserDevice,
    DeleteUserDeviceDto,
    ReissueSubscription,
)
from src.application.use_cases.subscription.commands.purchase import (
    ActivateTrialSubscription,
    ActivateTrialSubscriptionDto,
)
from src.application.use_cases.user.queries.plans import GetAvailablePlans
from src.core.enums import (
    AuthType,
    Currency,
    PaymentGatewayType,
    PurchaseType,
    TransactionStatus,
)
from src.core.exceptions import (
    CooldownError,
    PromocodeAlreadyActivatedError,
    PromocodeExpiredError,
    PromocodeNotAvailableError,
    PromocodeNotFoundError,
    TrialNotAvailableError,
)
from src.core.utils.time import datetime_now
from src.web.schemas import (
    DeviceDeleteResponse,
    DeviceResponse,
    DevicesDeleteAllResponse,
    DurationGatewayPriceResponse,
    DurationOfferResponse,
    ExtendRequest,
    GatewayOfferResponse,
    PaymentInitResponse,
    PlanOfferResponse,
    PromocodeActivateRequest,
    PromocodeActivateResponse,
    PurchaseRequest,
    ReissueResponse,
    SubscriptionInfoResponse,
    SubscriptionOffersResponse,
    TrialActivateResponse,
)

from src.web.endpoints.public._common import CurrentUser
from src.web.endpoints.public.sub_alias import maybe_alias_url

router = APIRouter(prefix="/subscription", tags=["Public - Subscription"])


# Оверлей-схемы: базовый DeviceResponse не отдаёт время активности.
# Добавляем created_at/updated_at (Remnawave отдаёт их в HwidDeviceDto),
# чтобы кабинет показывал «последнюю активность» устройства.
class DeviceActivityResponse(DeviceResponse):
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DevicesActivityResponse(BaseModel):
    devices: list[DeviceActivityResponse]
    current_count: int
    max_count: int


class PlanChangeCarryEntry(BaseModel):
    """Сколько дней добавит перенос остатка при смене на (тариф, срок, валюта).

    `mode` — про конкретную цель: `carry` (по цене дня), `same_plan` (тот же тариф,
    1:1), `unpriced` (у срока нет цены — перенести нельзя), `none` (новый тариф
    бессрочный — переносить нечего). `lost_days` уже включает упор в технический
    предел (`capped`). `extras_lost` — докупки, чья стоимость не перенесётся.
    `lost_reason` — почему часть дней не перенесётся: `cap` (упор в предел),
    `old_price` (цена старых дней неизвестна), `new_price` (у срока нет цены).
    """

    plan_code: str
    duration_days: int
    currency: str
    mode: str
    bonus_days: int
    lost_days: int
    capped: bool = False
    extras_lost: int = 0
    lost_reason: Optional[str] = None


class SubscriptionOffersOverlayResponse(SubscriptionOffersResponse):
    """Витрина плюс условия смены тарифа — ради честного текста в кабинете.

    Смена тарифа (CHANGE) у нас пересчитывает остаток текущей подписки в дни нового
    тарифа по цене дня (overlay_patches/plan_change_carryover.py). Сколько дней
    добавится, зависит от тарифа, срока и валюты, поэтому витрина отдаёт таблицу
    `plan_change_carry`, посчитанную той же чистой функцией, что и зачисление.
    Сервер всё равно пересчитает в момент оплаты — таблица только для показа.

    `plan_change_carry_active` — перенос ДЕЙСТВИТЕЛЬНО случится: правка покупки встала
    в этом процессе и выключатель `assets/plan_change.json` включён. Новый кабинет
    читает таблицу только при нём.

    `plan_change_keeps_days` — флаг для СТАРЫХ сборок кабинета (кэш вкладки, отдельный
    сайт), которые таблицу не знают: при true они не предупреждают вовсе. Поэтому он
    true, только когда перенос включён и НИЧЕГО не пропадает — у бессрочной, при
    возврате, при любой потере дней хоть на одной цели он false, и старая сборка
    предупреждает по-старому (честная сторона).

    Поля необязательные: старые клиенты их не замечают, а кабинет без флага
    `plan_change_keeps_days` (чужой бэкенд, старая сборка) не предупреждает и не
    предлагает смену вовсе.
    """

    plan_change_keeps_days: bool = False
    # Полные оставшиеся сутки текущей подписки (как считает кабинет); на паузе —
    # из сохранённого остатка. None у бессрочной и без подписки. С переносом — после
    # правил резерва (резерв — 0: бесплатная страховка не переносится).
    current_days_left: Optional[int] = None
    current_is_trial: Optional[bool] = None
    current_is_unlimited: Optional[bool] = None
    # None — «не удалось узнать», а не «паузы нет».
    current_frozen: Optional[bool] = None
    # Режим ТЕКУЩЕЙ подписки: carry | lifetime | reserve | refund | none; None — перенос
    # выключен или состояние не загрузилось.
    carry_mode: Optional[str] = None
    # Записи только при carry_mode = carry и только для тарифов со сменой (CHANGE).
    plan_change_carry: Optional[list[PlanChangeCarryEntry]] = None
    # Перенос включён и состояние загружено — новый кабинет берёт условия из таблицы.
    plan_change_carry_active: Optional[bool] = None
    # Докупленные места под устройства ТЕКУЩЕЙ подписки: сколько их, каков полный лимит
    # сейчас и до когда живёт ближайшее. Нужны, чтобы страница оплаты честно сказала,
    # что при смене тарифа места не переходят, а их стоимость идёт днями. None —
    # «не знаем» (чужой бэкенд или сбой чтения), и тогда кабинет молчит.
    current_device_limit: Optional[int] = None
    current_extra_devices: Optional[int] = None
    current_extra_until: Optional[str] = None
    # Докупленный ТРАФИК текущего окна: сколько ГБ и до какого момента. Нужен, чтобы
    # экран смены тарифа честно предупредил — докупленные гигабайты сгорят (решение
    # владельца Р-1: стоимость днями не переносим, но человек должен знать заранее).
    # None — «не знаем» (чужой бэкенд или сбой чтения), и тогда кабинет молчит.
    current_extra_traffic_gb: Optional[int] = None
    current_extra_traffic_until: Optional[str] = None


def plan_change_terms(
    *,
    expire_at: Optional[datetime],
    is_unlimited: bool,
    is_trial: bool,
    frozen_seconds: Optional[int],
    now: datetime,
) -> dict:
    """Что сгорит при смене тарифа. Чистая функция — тестируется без БД.

    Пауза важнее срока: пока подписка на паузе, `expire_at` в базе не двигается
    (и может быть уже в прошлом), а настоящий остаток лежит в `remaining_seconds`.
    """
    if frozen_seconds is not None:
        days: Optional[int] = max(0, int(frozen_seconds) // 86400)
    elif is_unlimited:
        days = None
    elif expire_at is None:
        days = 0
    else:
        days = max(0, int((expire_at - now).total_seconds() // 86400))
    return {
        "current_days_left": days,
        "current_is_trial": is_trial,
        "current_is_unlimited": is_unlimited,
        "current_frozen": frozen_seconds is not None,
    }


def _to_device_response(device: HwidDeviceDto) -> DeviceActivityResponse:
    return DeviceActivityResponse(
        hwid=device.hwid,
        platform=device.platform,
        device_model=device.device_model,
        os_version=device.os_version,
        user_agent=device.user_agent,
        created_at=getattr(device, "created_at", None),
        updated_at=getattr(device, "updated_at", None),
    )


def _assert_web_gateway(gateway_type: PaymentGatewayType) -> None:
    if gateway_type == PaymentGatewayType.TELEGRAM_STARS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Оплата через Telegram Stars недоступна в веб-кабинете",
        )


def _assert_web_purchase_email_verified(user: UserDto) -> None:
    """
    Skip email verification for Telegram / OAuth users — they never go through
    email+password registration, so requiring verification would block them.
    Only email-registered users must verify before purchasing or getting trial.

    Гейт настраивается тумблером (assets/email_gate.json, дефолт ВКЛ) — установщик
    может отключить обязательную верификацию email перед триалом/покупкой.
    """
    from src.infrastructure.services.overlay_email_gate import is_enabled

    if not is_enabled():
        return

    if user.auth_type != AuthType.EMAIL:
        return

    if user.is_email_verified:
        return

    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Подтвердите email перед покупкой или продлением подписки",
    )


async def _get_available_plan_by_code(
    user: UserDto,
    plan_code: str,
    get_available_plans: GetAvailablePlans,
) -> Optional[PlanDto]:
    plans = await get_available_plans.system(user)
    return next((plan for plan in plans if plan.public_code == plan_code), None)


async def _resolve_trial_plan(plan_dao: PlanDao) -> Optional[PlanDto]:
    trial_plans = await plan_dao.get_active_trial_plans()
    return trial_plans[0] if trial_plans else None


async def _validate_gateway_for_web(
    gateway_type: PaymentGatewayType,
    payment_gateway_dao: PaymentGatewayDao,
) -> None:
    _assert_web_gateway(gateway_type)
    gateway = await payment_gateway_dao.get_by_type(gateway_type)
    if not gateway or not gateway.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Gateway '{gateway_type}' not found or inactive",
        )

    if not gateway.settings or not gateway.settings.is_configured:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Gateway '{gateway_type}' is not configured",
        )


@router.get("/current", response_model=Optional[SubscriptionInfoResponse])
@inject
async def get_current_subscription(
    user: CurrentUser,
    subscription_dao: FromDishka[SubscriptionDao],
    remnawave: FromDishka[Remnawave],
) -> Optional[SubscriptionInfoResponse]:
    current_subscription = await subscription_dao.get_current(user.id)

    if not current_subscription:
        return None

    remna_user = await remnawave.get_user_by_uuid(current_subscription.user_remna_id)

    return SubscriptionInfoResponse(
        user_remna_id=str(current_subscription.user_remna_id),
        status=current_subscription.current_status.value,
        is_trial=current_subscription.is_trial,
        traffic_limit=current_subscription.traffic_limit,
        device_limit=current_subscription.device_limit,
        traffic_limit_strategy=current_subscription.traffic_limit_strategy.value,
        expire_at=current_subscription.expire_at,
        # Крипто-ссылка: при включённом тумблере отдаём непрозрачный алиас вместо
        # реального sub-URL. Кабинет строит из этого поля ВСЕ deep-link'и/QR/копирование,
        # поэтому подмена в одном месте прячет реальную ссылку на всех поверхностях.
        url=maybe_alias_url(current_subscription.url),
        plan_name=current_subscription.plan_snapshot.name,
        plan_duration_days=current_subscription.plan_snapshot.duration,
        used_traffic_bytes=remna_user.used_traffic_bytes if remna_user else None,
        lifetime_used_traffic_bytes=remna_user.lifetime_used_traffic_bytes if remna_user else None,
        online_at=remna_user.online_at if remna_user else None,
    )


@router.get("/devices", response_model=DevicesActivityResponse)
@inject
async def get_subscription_devices(
    user: CurrentUser,
    subscription_dao: FromDishka[SubscriptionDao],
    remnawave: FromDishka[Remnawave],
) -> DevicesActivityResponse:
    current_subscription = await subscription_dao.get_current(user.id)
    if not current_subscription:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Подписка не найдена")

    devices = await remnawave.get_devices(current_subscription.user_remna_id)
    return DevicesActivityResponse(
        devices=[_to_device_response(device) for device in devices],
        current_count=len(devices),
        max_count=current_subscription.device_limit,
    )


@router.delete("/devices/{hwid}", response_model=DeviceDeleteResponse)
@inject
async def delete_subscription_device(
    hwid: str,
    user: CurrentUser,
    delete_user_device: FromDishka[DeleteUserDevice],
) -> DeviceDeleteResponse:
    deleted = await delete_user_device(
        user,
        DeleteUserDeviceDto(user_id=user.id, hwid=hwid),
    )
    return DeviceDeleteResponse(deleted=deleted)


@router.delete("/devices", response_model=DevicesDeleteAllResponse)
@inject
async def delete_all_subscription_devices(
    user: CurrentUser,
    delete_all_devices: FromDishka[DeleteUserAllDevices],
) -> DevicesDeleteAllResponse:
    try:
        await delete_all_devices(user)
    except CooldownError as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    return DevicesDeleteAllResponse(success=True)


@router.post("/reissue", response_model=ReissueResponse)
@inject
async def reissue_current_subscription(
    user: CurrentUser,
    reissue_subscription: FromDishka[ReissueSubscription],
    subscription_dao: FromDishka[SubscriptionDao],
    remnawave: FromDishka[Remnawave],
    session: FromDishka[AsyncSession],
) -> ReissueResponse:
    try:
        await reissue_subscription(user)
    except CooldownError as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e

    # Перевыпуск ОТЗЫВАЕТ старую ссылку в панели, но базовый код НЕ обновляет
    # subscription.url в БД (пишет только link_reset_at) → до вебхука/sync кабинет и
    # крипто-алиас указывали бы на ОТОЗВАННУЮ ссылку (клиент получал бы 404/502).
    # Подтягиваем свежий url из панели и пишем сразу. Best-effort — при сбое подхватит sync.
    try:
        current = await subscription_dao.get_current(user.id)
        if current:
            remna_user = await remnawave.get_user_by_uuid(current.user_remna_id)
            fresh = getattr(remna_user, "subscription_url", None) if remna_user else None
            if fresh and fresh != current.url:
                await session.execute(
                    text("UPDATE subscriptions SET url = :url WHERE user_remna_id = :ruid"),
                    {"url": fresh, "ruid": str(current.user_remna_id)},
                )
                await session.commit()
    except Exception:  # noqa: BLE001
        logger.warning(f"reissue: не удалось обновить url в БД для user_id={user.id} (подхватит sync)")

    return ReissueResponse(success=True)


@router.post("/promocode", response_model=PromocodeActivateResponse)
@inject
async def activate_promocode_web(
    body: PromocodeActivateRequest,
    user: CurrentUser,
    activate_promocode: FromDishka[ActivatePromocode],
    session: FromDishka[AsyncSession],
    subscription_dao: FromDishka[SubscriptionDao],
    validate_promocode: FromDishka[ValidatePromocode],
) -> PromocodeActivateResponse:
    _assert_web_purchase_email_verified(user)
    code = (body.code or "").strip()
    # Подарок другого тарифа, при котором пропадут дни, веб не активирует (подтверждения
    # тут нет) — см. overlay_plan_change.promo_web_refusal. Недействительный код —
    # обычная ошибка активации ниже, а не «подарок заменит тариф».
    from src.infrastructure.services import overlay_plan_change as carry

    refusal = await carry.promo_web_refusal(
        session, user=user, code=code, validate_promocode=validate_promocode,
        subscription_dao=subscription_dao, now=datetime_now(),
    )
    if refusal:
        raise HTTPException(status_code=refusal[0], detail=refusal[1])
    try:
        promo = await activate_promocode(user, ActivatePromocodeDto(code=code, user=user))
    except PromocodeNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except (
        PromocodeExpiredError,
        PromocodeAlreadyActivatedError,
        PromocodeNotAvailableError,
    ) as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    return PromocodeActivateResponse(success=True, reward_type=promo.reward_type.value)


@router.post("/trial", response_model=TrialActivateResponse)
@inject
async def activate_trial_web(
    user: CurrentUser,
    plan_dao: FromDishka[PlanDao],
    activate_trial: FromDishka[ActivateTrialSubscription],
) -> TrialActivateResponse:
    _assert_web_purchase_email_verified(user)

    if not user.is_trial_available:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Пробный период недоступен")

    plan = await _resolve_trial_plan(plan_dao)
    if not plan or not plan.durations:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Нет активного пробного тарифа")

    duration_days = plan.durations[0].days
    plan_snapshot = PlanSnapshotDto.from_plan(plan, duration_days)
    try:
        await activate_trial.system(ActivateTrialSubscriptionDto(user=user, plan=plan_snapshot))
    except TrialNotAvailableError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    # Схема TrialActivateResponse требует is_free/activated/duration_days
    # (поля success у неё нет). Триал тут выдаётся бесплатно.
    return TrialActivateResponse(is_free=True, activated=True, duration_days=duration_days)


@router.get("/trial-info")
@inject
async def trial_info_web(
    user: CurrentUser,
    plan_dao: FromDishka[PlanDao],
) -> dict:
    """Инфо о пробном периоде для карточки в кабинете (дни/трафик/устройства)."""
    plan = await _resolve_trial_plan(plan_dao)
    if not plan or not plan.durations:
        return {"available": False, "days": 0, "traffic_gb": 0, "devices": 0}
    return {
        "available": bool(user.is_trial_available),
        "days": plan.durations[0].days,
        "traffic_gb": plan.traffic_limit,  # ГБ, 0 = безлимит
        "devices": plan.device_limit,      # 0 = безлимит
    }


@router.post("/purchase", response_model=PaymentInitResponse)
@inject
async def purchase_subscription(
    body: PurchaseRequest,
    user: CurrentUser,
    subscription_dao: FromDishka[SubscriptionDao],
    payment_gateway_dao: FromDishka[PaymentGatewayDao],
    pricing_service: FromDishka[PricingService],
    get_available_plans: FromDishka[GetAvailablePlans],
    create_payment: FromDishka[CreatePayment],
    process_payment: FromDishka[ProcessPayment],
) -> PaymentInitResponse:
    _assert_web_purchase_email_verified(user)
    await _validate_gateway_for_web(body.gateway_type, payment_gateway_dao)

    plan = await _get_available_plan_by_code(user, body.plan_code, get_available_plans)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тариф не найден")

    duration = plan.get_duration(body.duration_days)
    if not duration:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Срок тарифа не найден",
        )

    gateway = await payment_gateway_dao.get_by_type(body.gateway_type)
    if not gateway:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Платёжный шлюз не найден")

    current_subscription = await subscription_dao.get_current(user.id)
    purchase_type = PurchaseType.CHANGE if current_subscription else PurchaseType.NEW
    plan_snapshot = PlanSnapshotDto.from_plan(plan, duration.days)
    pricing = pricing_service.calculate(
        user,
        duration.get_price(gateway.currency),
        gateway.currency,
    )

    try:
        payment = await create_payment(
            user,
            CreatePaymentDto(
                plan_snapshot=plan_snapshot,
                pricing=pricing,
                purchase_type=purchase_type,
                gateway_type=body.gateway_type,
            ),
        )
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ошибка соединения с платёжной системой. Попробуйте ещё раз.",
        ) from e

    tx_status = TransactionStatus.PENDING
    if pricing.is_free:
        await process_payment.system(
            ProcessPaymentDto(
                payment_id=payment.id,
                new_transaction_status=TransactionStatus.COMPLETED,
                gateway_type=body.gateway_type,
            ),
        )
        tx_status = TransactionStatus.COMPLETED

    return PaymentInitResponse(
        payment_id=str(payment.id),
        payment_url=payment.url,
        purchase_type=purchase_type.value,
        status=tx_status.value,
        is_free=pricing.is_free,
        final_amount=str(pricing.final_amount),
        currency=gateway.currency.symbol,
    )


@router.post("/extend", response_model=PaymentInitResponse)
@inject
async def extend_subscription(
    body: ExtendRequest,
    user: CurrentUser,
    subscription_dao: FromDishka[SubscriptionDao],
    payment_gateway_dao: FromDishka[PaymentGatewayDao],
    pricing_service: FromDishka[PricingService],
    get_available_plans: FromDishka[GetAvailablePlans],
    match_plan: FromDishka[MatchPlan],
    create_payment: FromDishka[CreatePayment],
    process_payment: FromDishka[ProcessPayment],
) -> PaymentInitResponse:
    _assert_web_purchase_email_verified(user)
    await _validate_gateway_for_web(body.gateway_type, payment_gateway_dao)

    current_subscription = await subscription_dao.get_current(user.id)
    if not current_subscription:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Подписка не найдена")

    available_plans = await get_available_plans.system(user)
    matched_plan = await match_plan.system(
        MatchPlanDto(plan_snapshot=current_subscription.plan_snapshot, plans=available_plans)
    )
    if not matched_plan:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Тариф для продления недоступен",
        )

    duration = matched_plan.get_duration(body.duration_days)
    if not duration:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Срок тарифа не найден",
        )

    gateway = await payment_gateway_dao.get_by_type(body.gateway_type)
    if not gateway:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Платёжный шлюз не найден")

    pricing = pricing_service.calculate(
        user,
        duration.get_price(gateway.currency),
        gateway.currency,
    )
    plan_snapshot = PlanSnapshotDto.from_plan(matched_plan, duration.days)
    try:
        payment = await create_payment(
            user,
            CreatePaymentDto(
                plan_snapshot=plan_snapshot,
                pricing=pricing,
                purchase_type=PurchaseType.RENEW,
                gateway_type=body.gateway_type,
            ),
        )
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ошибка соединения с платёжной системой. Попробуйте ещё раз.",
        ) from e

    tx_status = TransactionStatus.PENDING
    if pricing.is_free:
        await process_payment.system(
            ProcessPaymentDto(
                payment_id=payment.id,
                new_transaction_status=TransactionStatus.COMPLETED,
                gateway_type=body.gateway_type,
            ),
        )
        tx_status = TransactionStatus.COMPLETED

    return PaymentInitResponse(
        payment_id=str(payment.id),
        payment_url=payment.url,
        purchase_type=PurchaseType.RENEW.value,
        status=tx_status.value,
        is_free=pricing.is_free,
        final_amount=str(pricing.final_amount),
        currency=gateway.currency.symbol,
    )


class PayWithBalanceRequest(BaseModel):
    plan_code: str
    duration_days: int = Field(gt=0, le=3650)
    gateway_type: PaymentGatewayType


@router.post("/pay-with-balance")
@inject
async def pay_with_balance(
    body: PayWithBalanceRequest,
    user: CurrentUser,
    session: FromDishka[AsyncSession],
    uow: FromDishka[UnitOfWork],
    subscription_dao: FromDishka[SubscriptionDao],
    payment_gateway_dao: FromDishka[PaymentGatewayDao],
    pricing_service: FromDishka[PricingService],
    get_available_plans: FromDishka[GetAvailablePlans],
    match_plan: FromDishka[MatchPlan],
    transaction_dao: FromDishka[TransactionDao],
    process_payment: FromDishka[ProcessPayment],
) -> dict:
    """Оплата тарифа с рублёвого баланса-кошелька (NEW/CHANGE/RENEW).

    Списываем ₽ атомарно, создаём завершённую транзакцию (без обращения к шлюзу,
    display_name «Баланс») и отдаём её базовому ProcessPayment — он выдаёт/меняет/
    продлевает подписку и начисляет реферальные, как при обычной оплате. При
    ошибке — возвращаем деньги на баланс.
    """
    _assert_web_purchase_email_verified(user)

    plan = await _get_available_plan_by_code(user, body.plan_code, get_available_plans)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тариф не найден")

    duration = plan.get_duration(body.duration_days)
    if not duration:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Срок тарифа не найден")

    gateway = await payment_gateway_dao.get_by_type(body.gateway_type)
    if not gateway:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Платёжный шлюз не найден")
    if gateway.currency != Currency.RUB:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Оплата с баланса доступна только в рублях",
        )

    pricing = pricing_service.calculate(
        user, duration.get_price(gateway.currency), gateway.currency
    )
    price = Decimal(str(pricing.final_amount))

    current = await subscription_dao.get_current(user.id)
    if not current:
        purchase_type = PurchaseType.NEW
    else:
        matched = await match_plan.system(
            MatchPlanDto(plan_snapshot=current.plan_snapshot, plans=[plan])
        )
        purchase_type = PurchaseType.RENEW if matched else PurchaseType.CHANGE

    # Атомарное списание баланса (спишется только если хватает).
    new_balance = (
        await session.execute(
            text(
                "UPDATE users SET cabinet_balance = cabinet_balance - :amt "
                "WHERE id = :id AND cabinet_balance >= :amt RETURNING cabinet_balance"
            ),
            {"amt": price, "id": user.id},
        )
    ).scalar_one_or_none()
    if new_balance is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Недостаточно средств на балансе: нужно {price} ₽",
        )
    await session.commit()

    transaction: Optional[TransactionDto] = None
    try:
        transaction = TransactionDto(
            payment_id=uuid.uuid4(),
            user_id=user.id,
            status=TransactionStatus.PENDING,
            purchase_type=purchase_type,
            gateway_type=gateway.type,
            gateway_display_name="Баланс",
            pricing=pricing,
            currency=gateway.currency,
            plan_snapshot=PlanSnapshotDto.from_plan(plan, duration.days),
        )
        async with uow:
            await transaction_dao.create(transaction)
            await uow.commit()

        await process_payment.system(
            ProcessPaymentDto(
                payment_id=transaction.payment_id,
                new_transaction_status=TransactionStatus.COMPLETED,
                gateway_type=body.gateway_type,
            ),
        )
    except Exception as e:  # noqa: BLE001
        # ВОЗВРАЩАТЬ ДЕНЬГИ МОЖНО, ТОЛЬКО ЕСЛИ ПОКУПКА НЕ ВЫДАНА. `try` накрывает и
        # последний шаг успешного пути — живой вызов Telegram у заблокировавшего бота.
        # Безусловный возврат там давал бесплатную покупку, а со сменой тарифа — ещё и
        # перенос остатка бонусом. Судим по подписке, а не по сроку: новая строка
        # после смены бывает с более ранним сроком (см. was_change_granted).
        from src.infrastructure.services.overlay_balance import (
            _alert_admins_balance,
            was_balance_purchase_granted,
        )

        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        # Выдано — только если счёт COMPLETED и выдача не упала (PurchaseError), и уже
        # затем — подписка действительно изменилась.
        granted = await was_balance_purchase_granted(
            error=e,
            session=session,
            payment_id=transaction.payment_id if transaction is not None else None,
            subscription_dao=subscription_dao,
            user_id=user.id,
            before=current,
        )
        if granted:
            logger.error(
                f"pay_with_balance: user_id={user.id} — покупка {purchase_type.value} ВЫДАНА, "
                f"но шаг после выдачи упал ({e}). Деньги НЕ возвращаем."
            )
            await _alert_admins_balance(
                f"Оплата с баланса: {purchase_type.value} выдана, но последний шаг упал "
                f"(user_id={user.id}, {price} ₽). Деньги не возвращены — проверьте, "
                "дошло ли уведомление.",
                title="⚠️ Оплата с баланса",
            )
            return {
                "success": True,
                "purchase_type": purchase_type.value,
                "spent": float(price),
                "balance": float(Decimal(str(new_balance))),
            }

        await session.execute(
            text("UPDATE users SET cabinet_balance = cabinet_balance + :amt WHERE id = :id"),
            {"amt": price, "id": user.id},
        )
        await session.commit()
        if granted is None:
            logger.error(
                f"pay_with_balance: user_id={user.id} упало ({e}), деньги возвращены, "
                "но выдачу подтвердить НЕ удалось — проверьте вручную"
            )
            await _alert_admins_balance(
                f"Оплата с баланса упала (user_id={user.id}, {price} ₽, {purchase_type.value}), "
                "деньги вернули, но выдана ли покупка — неизвестно. Нужна проверка.",
                title="⚠️ Оплата с баланса",
            )
        else:
            logger.warning(f"pay_with_balance: выдача user_id={user.id} упала ({e}), деньги возвращены")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Не удалось активировать тариф — деньги возвращены на баланс. Попробуйте позже.",
        )

    return {
        "success": True,
        "purchase_type": purchase_type.value,
        "spent": float(price),
        "balance": float(Decimal(str(new_balance))),
    }


# response_model — именно оверлей-схема: FastAPI режет ответ по модели, и с базовой
# новые поля молча пропали бы вместе с предупреждением о сгорающих днях.
@router.get("/offers", response_model=SubscriptionOffersOverlayResponse)
@inject
async def get_subscription_offers(
    user: CurrentUser,
    session: FromDishka[AsyncSession],
    subscription_dao: FromDishka[SubscriptionDao],
    payment_gateway_dao: FromDishka[PaymentGatewayDao],
    pricing_service: FromDishka[PricingService],
    get_available_plans: FromDishka[GetAvailablePlans],
    match_plan: FromDishka[MatchPlan],
) -> SubscriptionOffersOverlayResponse:
    active_gateways = await payment_gateway_dao.get_active()
    web_gateways = [
        gateway
        for gateway in active_gateways
        if gateway.type != PaymentGatewayType.TELEGRAM_STARS
        and gateway.settings
        and gateway.settings.is_configured
    ]

    available_plans = await get_available_plans.system(user)
    current_subscription = await subscription_dao.get_current(user.id)

    matched_plan: Optional[PlanDto] = None
    if current_subscription:
        matched_plan = await match_plan.system(
            MatchPlanDto(
                plan_snapshot=current_subscription.plan_snapshot,
                plans=available_plans,
            )
        )

    plan_offers: list[PlanOfferResponse] = []
    change_plans: list[PlanDto] = []
    for plan in available_plans:
        if not plan.public_code:
            continue

        duration_offers: list[DurationOfferResponse] = []
        for duration in plan.durations:
            prices: list[DurationGatewayPriceResponse] = []
            for gateway in web_gateways:
                pricing = pricing_service.calculate(
                    user=user,
                    price=duration.get_price(gateway.currency),
                    currency=gateway.currency,
                )
                prices.append(
                    DurationGatewayPriceResponse(
                        gateway_type=gateway.type,
                        currency=gateway.currency.value,
                        currency_symbol=gateway.currency.symbol,
                        original_amount=str(pricing.original_amount),
                        discount_percent=pricing.discount_percent,
                        final_amount=str(pricing.final_amount),
                        is_free=pricing.is_free,
                    )
                )

            duration_offers.append(DurationOfferResponse(days=duration.days, prices=prices))

        is_renew_candidate = (
            current_subscription is not None
            and matched_plan is not None
            and matched_plan.id == plan.id
            and not current_subscription.is_unlimited
        )
        recommended_purchase_type = (
            PurchaseType.RENEW.value
            if is_renew_candidate
            else (PurchaseType.CHANGE.value if current_subscription else PurchaseType.NEW.value)
        )
        if recommended_purchase_type == PurchaseType.CHANGE.value:
            change_plans.append(plan)

        plan_offers.append(
            PlanOfferResponse(
                id=plan.id,
                public_code=plan.public_code,
                name=plan.name,
                description=plan.description,
                traffic_limit=plan.traffic_limit,
                device_limit=plan.device_limit,
                type=plan.type.value,
                recommended_purchase_type=recommended_purchase_type,
                durations=duration_offers,
            )
        )

    gateway_offers = [
        GatewayOfferResponse(
            gateway_type=gateway.type,
            currency=gateway.currency.value,
            currency_symbol=gateway.currency.symbol,
        )
        for gateway in web_gateways
    ]

    terms: dict = {}
    if current_subscription:
        # Остаток на паузе. Только чтение, коммит не нужен. Витрина — единственный
        # путь к покупке, поэтому сбой вспомогательной таблицы не должен её ронять:
        # откатываем прерванную транзакцию и честно говорим «про паузу не знаем».
        frozen_seconds: Optional[int] = None
        frozen_known = True
        try:
            row = (
                await session.execute(
                    text(
                        "SELECT remaining_seconds FROM subscription_freezes "
                        "WHERE user_id = :uid AND active = true"
                    ),
                    {"uid": user.id},
                )
            ).first()
            frozen_seconds = int(row[0]) if row and row[0] is not None else None
        except Exception as e:  # noqa: BLE001
            frozen_known = False
            await session.rollback()
            logger.warning(f"offers: не удалось прочитать паузу user_id={user.id} ({e})")

        terms = plan_change_terms(
            expire_at=current_subscription.expire_at,
            is_unlimited=current_subscription.is_unlimited,
            is_trial=current_subscription.is_trial,
            frozen_seconds=frozen_seconds,
            now=datetime_now(),
        )
        if not frozen_known:
            terms["current_frozen"] = None

    if current_subscription:
        # Докупленные места — отдельным чтением в SAVEPOINT: витрина единственный путь
        # к покупке, и отсутствующая таблица (порядок выкатки) не должна её ронять.
        terms.update(await _extra_device_terms(session, user, current_subscription))
        terms.update(await _extra_traffic_terms(session, user, current_subscription))

    keeps_days = False
    if current_subscription:
        keeps_days, carry_terms = await _carry_terms(
            session=session,
            user=user,
            current_subscription=current_subscription,
            change_plans=change_plans,
            currencies=_unique_currencies(web_gateways),
        )
        terms.update(carry_terms)

    return SubscriptionOffersOverlayResponse(
        gateways=gateway_offers,
        plans=plan_offers,
        has_current_subscription=bool(current_subscription),
        current_subscription_status=(
            current_subscription.current_status.value if current_subscription else None
        ),
        plan_change_keeps_days=keeps_days,
        **terms,
    )


async def _extra_device_terms(session, user, current_subscription) -> dict:
    """Сколько мест докуплено к текущей подписке и до когда. Сбой — пустой словарь."""
    try:
        rows = (
            await session.execute(
                text(
                    "SELECT count(*), min(ends_at) FROM extra_device_slots "
                    "WHERE user_id = :uid AND subscription_id = :sid AND status = 'active'"
                ),
                {"uid": user.id, "sid": current_subscription.id},
            )
        ).first()
        count = int(rows[0] or 0) if rows else 0
        until = rows[1] if rows else None
        return {
            "current_device_limit": int(getattr(current_subscription, "device_limit", 0) or 0),
            "current_extra_devices": count,
            "current_extra_until": until.isoformat() if until is not None else None,
        }
    except Exception as e:  # noqa: BLE001
        await session.rollback()
        logger.warning(f"offers: докупленные устройства user_id={user.id} не прочитаны ({e})")
        return {}


async def _extra_traffic_terms(session, user, current_subscription) -> dict:
    """Сколько ГБ докуплено к текущему окну и до когда. Сбой — пустой словарь.

    Отдельным чтением и с откатом, как у мест: витрина — единственный путь к покупке,
    и отсутствующая таблица (порядок выкатки) не должна её ронять.
    """
    try:
        row = (
            await session.execute(
                text(
                    "SELECT coalesce(sum(gb), 0), min(ends_at) FROM extra_traffic_grants "
                    "WHERE user_id = :uid AND subscription_id = :sid AND status = 'active'"
                ),
                {"uid": user.id, "sid": current_subscription.id},
            )
        ).first()
        gb = int(row[0] or 0) if row else 0
        until = row[1] if row else None
        return {
            "current_extra_traffic_gb": gb,
            "current_extra_traffic_until": until.isoformat() if until is not None else None,
        }
    except Exception as e:  # noqa: BLE001
        await session.rollback()
        logger.warning(f"offers: докупленный трафик user_id={user.id} не прочитан ({e})")
        return {}


def _unique_currencies(gateways: list) -> list[Currency]:
    seen: list[Currency] = []
    for gateway in gateways:
        if gateway.currency not in seen:
            seen.append(gateway.currency)
    return seen


def carry_entry(plan_code: str, duration_days: int, currency: str, result) -> Optional[PlanChangeCarryEntry]:
    """Результат расчёта → запись витрины. Режим цели — carry | same_plan | unpriced | none."""
    if result.mode not in ("carry", "same_plan", "unpriced", "none"):
        return None
    return PlanChangeCarryEntry(
        plan_code=plan_code,
        duration_days=duration_days,
        currency=currency,
        mode=result.mode,
        bonus_days=int(result.added_days),
        lost_days=int(result.lost_days),
        capped=bool(result.capped),
        extras_lost=int(result.extras_lost),
        lost_reason=result.lost_reason,
    )


def legacy_keeps_days(carry_mode: Optional[str], entries: list[PlanChangeCarryEntry]) -> bool:
    """`plan_change_keeps_days` для старых сборок: true, только если ничего не пропадёт."""
    if carry_mode in ("lifetime", "refund"):
        return False
    return not any(
        e.lost_days > 0 or e.mode == "unpriced" or e.capped or e.extras_lost > 0 for e in entries
    )


async def _carry_terms(
    *,
    session: AsyncSession,
    user,
    current_subscription,
    change_plans: list[PlanDto],
    currencies: list[Currency],
) -> tuple[bool, dict]:
    """Условия переноса остатка для витрины: (plan_change_keeps_days, поля ответа).

    Обещать перенос можно, только если он случится (`overlay_active`). Сбой загрузки
    состояния — rollback и `false`: кабинет по-старому предупредит о сгорании, а это
    безопасная сторона.
    """
    from src.infrastructure.services import overlay_plan_change as carry

    if not carry.overlay_active():
        return False, {}
    if current_subscription.is_trial:
        # Пробник заменяется по правилам базы и не переносится.
        return True, {"carry_mode": "none", "plan_change_carry_active": True}

    now = datetime_now()
    try:
        state = await carry.load_carry_state(
            session,
            user_id=user.id,
            subscription=carry.sub_row_from_dto(current_subscription),
            now=now,
            exclude_payment_id=None,
            extra_plan_ids=[plan.id for plan in change_plans],
        )
    except Exception as e:  # noqa: BLE001
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        logger.warning(f"offers: состояние переноса user_id={user.id} не загрузилось ({e})")
        return False, {}

    mode, left = carry.state_mode(state, now)
    fields: dict = {
        "carry_mode": mode,
        "current_days_left": None if left is None else int(left) // carry.DAY,
    }
    entries: list[PlanChangeCarryEntry] = []
    if mode == "carry":
        for plan in change_plans:
            for duration in plan.durations:
                for currency in currencies:
                    try:
                        price = duration.get_price(currency)
                    except Exception:  # noqa: BLE001 — нет цены в этой валюте: записи нет
                        continue
                    result = carry.compute_carryover(
                        state,
                        new_plan_id=plan.id,
                        new_duration=duration.days,
                        new_list_amount=price,
                        currency=currency.value,
                        now=now,
                    )
                    entry = carry_entry(plan.public_code, duration.days, currency.value, result)
                    if entry is not None:
                        entries.append(entry)
    fields["plan_change_carry"] = entries
    fields["plan_change_carry_active"] = True
    return legacy_keeps_days(mode, entries), fields


def apply() -> str:
    """Подставить наш роутер на место базового.

    Патчим сам модуль базы: `web/endpoints/public/__init__.py` читает из него имя
    `router` и включает его в общий публичный роутер — то есть уже после нас.
    """
    from . import PatchTargetChanged

    import src.web.endpoints.public.subscription as target

    if not hasattr(target, "router"):
        raise PatchTargetChanged(
            "в public/subscription.py нет объекта router — база перестроила "
            "публичные ручки подписки, кабинет потеряет выдачу и резерв"
        )
    if getattr(target, "_overlay_wrapped", False):
        return "уже подставлен"

    target.router = router
    target._overlay_wrapped = True
    return "выдача веб-пользователю, резерв, алиасы ссылок"
