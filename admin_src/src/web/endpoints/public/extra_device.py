"""Public: докупка «+1 устройство до конца срока подписки» (overlay).

Две ручки: предложение с ценой (только чтение, панель не трогаем) и покупка —
с рублёвого баланса одной транзакцией базы либо счётом через обычный RUB-шлюз.

ДЕНЕЖНЫЙ ПУТЬ. Всё важное — в services/overlay_extra_device.py, здесь только
разбор запроса, замок, ответ. Правила, из-за которых код выглядит именно так:
  * бизнес-отказы — это 200 с полем `result`, а не HTTP-ошибка: `ApiError.detail`
    кабинета — строка, и коду причины неоткуда взять перевод;
  * `request_id` кабинета ищем ДО замка и ещё раз ПОД замком — иначе двойной клик
    (или повтор запроса из-за разорванного соединения) купил бы два места;
  * чужой `request_id` — 409 и ни слова о чужом заказе: ключ уникален на всю
    установку, и подбор чужого UUID не должен ничего рассказывать;
  * панель — последним шагом покупки с баланса: её ошибка откатывает транзакцию
    целиком, и человеку честно отвечаем «деньги не списаны».
"""

from __future__ import annotations

import uuid as uuid_lib
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Notifier, Remnawave
from src.application.common.dao import PaymentGatewayDao, TransactionDao
from src.application.dto import MessagePayloadDto, PriceDetailsDto
from src.application.use_cases.gateways.commands.payment import CreatePayment, CreatePaymentDto
from src.core.enums import Currency, PaymentGatewayType, PurchaseType
from src.core.utils.time import datetime_now
from src.infrastructure.services import overlay_extra_device as extra
from src.web.endpoints.public._common import CurrentUser

router = APIRouter(prefix="/subscription", tags=["Public - Extra device"])


def _fmt(value: Any) -> str:
    """Целые суммы без хвоста: цены докупки всегда в целых рублях."""
    d = Decimal(str(value))
    return str(int(d)) if d == d.to_integral() else format(d.normalize(), "f")


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if isinstance(value, datetime) else None


def _web_rub_gateways(active: list) -> list:
    """Настроенные рублёвые веб-шлюзы. Звёзды — только в боте, доллары нам не нужны."""
    return [
        g
        for g in active
        if g.type != PaymentGatewayType.TELEGRAM_STARS
        and g.currency == Currency.RUB
        and g.settings
        and g.settings.is_configured
    ]


async def _balance_gateway_type(payment_gateway_dao: PaymentGatewayDao) -> Optional[Any]:
    """Чем подписать транзакцию «Баланс · устройство»: колонка gateway_type NOT NULL.

    Берём первый АКТИВНЫЙ рублёвый шлюз, а если активных нет — любой рублёвый из
    таблицы: строки всех типов заводит установщик, и выключенные шлюзы не повод
    отказать человеку в покупке за деньги, которые уже лежат на балансе.
    """
    active = [g for g in await payment_gateway_dao.get_active() if g.currency == Currency.RUB]
    if active:
        return active[0].type
    every = [g for g in await payment_gateway_dao.get_all() if g.currency == Currency.RUB]
    return every[0].type if every else None


def _quote_payload(st: extra.UserState, cfg: dict, now: datetime) -> dict[str, Any]:
    """Предложение: что можно купить сейчас и за сколько. Без панели и без записи."""
    active = [s for s in st.slots if s.subscription_id == st.sub_id]
    payload: dict[str, Any] = {
        "enabled": True,
        "currency": "RUB",
        "currency_symbol": "₽",
        "price_per_30d": _fmt(cfg["price_rub_30d"]) if cfg.get("price_rub_30d") else None,
        "max_extra": cfg["max_extra"],
        "device_limit": st.device_limit,
        "plan_device_limit": st.plan_device_limit,
        "extra_count": len(active),
        "subscription_expire_at": _iso(st.expire_at),
        "balance": _fmt(st.balance),
        # Отключатся ли устройства по окончании места. Человек обязан знать это ДО
        # оплаты: настройка меняется владельцем, и обещание «ничего не отключим»,
        # вшитое в текст кабинета, однажды стало бы неправдой.
        "removes_excess": bool(cfg.get("remove_excess_devices")),
    }
    why = extra.eligibility(st, cfg, now, "new")
    if why is None:
        q = extra.quote(st, cfg, now, "new")
        payload["new"] = {
            "available": True,
            "reason": None,
            "amount": _fmt(q.amount),
            "until": _iso(q.period_end),
            "days": q.days,
        }
    else:
        payload["new"] = {"available": False, "reason": why}

    slots = []
    for slot in active:
        entry: dict[str, Any] = {"slot_id": slot.id, "ends_at": _iso(slot.ends_at), "extend": None}
        if extra.eligibility(st, cfg, now, "extend", slot.id) is None:
            q = extra.quote(st, cfg, now, "extend", slot.id)
            entry["extend"] = {
                "amount": _fmt(q.amount),
                "until": _iso(q.period_end),
                "days": q.days,
            }
        slots.append(entry)
    payload["slots"] = slots
    return payload


@router.get("/extra-device")
@inject
async def get_extra_device(
    user: CurrentUser,
    session: FromDishka[AsyncSession],
    payment_gateway_dao: FromDishka[PaymentGatewayDao],
) -> dict[str, Any]:
    cfg = extra.load_config()
    # Выключено — не рассказываем даже цену: пока владелец не открыл продажи,
    # никакого «скоро будет за X ₽» в ответе быть не должно.
    if not extra.effective_enabled(cfg):
        return {"enabled": False}

    # Только показываем: замок строки человека здесь не нужен и мешал бы покупке,
    # которая идёт параллельно из другой вкладки.
    st = await extra.lock_state(session, user.id, lock=False)
    await session.rollback()
    payload = _quote_payload(st, cfg, datetime_now())
    payload["gateways"] = [
        {"gateway_type": g.type.value, "currency_symbol": g.currency.symbol}
        for g in _web_rub_gateways(await payment_gateway_dao.get_active())
    ]
    return payload


class BuyRequest(BaseModel):
    request_id: uuid_lib.UUID
    kind: str = Field(default="new", pattern="^(new|extend)$")
    slot_id: Optional[int] = None
    pay: str = Field(default="balance", pattern="^(balance|gateway)$")
    gateway_type: Optional[PaymentGatewayType] = None
    expected_amount: Decimal = Field(gt=0)


async def _notify_admins(notifier: Notifier, content: str) -> None:
    try:
        await notifier.notify_admins(
            MessagePayloadDto(i18n_key="raw-message", i18n_kwargs={"content": content}, delete_after=None)
        )
    except Exception:  # noqa: BLE001 — уведомление не имеет права ломать оплаченную покупку
        logger.exception("extra_device: не предупредил владельца о докупке")


@router.post("/extra-device/buy")
@inject
async def buy_extra_device(
    body: BuyRequest,
    user: CurrentUser,
    session: FromDishka[AsyncSession],
    remnawave: FromDishka[Remnawave],
    payment_gateway_dao: FromDishka[PaymentGatewayDao],
    transaction_dao: FromDishka[TransactionDao],
    create_payment: FromDishka[CreatePayment],
    notifier: FromDishka[Notifier],
) -> dict[str, Any]:
    # Тот же почтовый гейт, что у покупки подписки: это тоже покупка за деньги.
    _assert_email_verified(user)

    cfg = extra.load_config()
    now = datetime_now()

    saved = await extra.order_by_request(session, body.request_id)
    if saved is not None:
        return _replay(saved, user.id)

    st = await extra.lock_state(session, user.id)
    # Под замком ищем ещё раз: двойной клик успевает дойти до этой точки дважды.
    saved = await extra.order_by_request(session, body.request_id)
    if saved is not None:
        await session.rollback()
        return _replay(saved, user.id)

    why = extra.eligibility(st, cfg, now, body.kind, body.slot_id)
    if why is not None:
        await session.rollback()
        return {"result": "not_available", "reason": why, "quote": _safe_quote(st, cfg, now)}

    q = extra.quote(st, cfg, now, body.kind, body.slot_id)
    if q.amount > body.expected_amount:
        # Подписку успели продлить — период вырос, цена тоже. Денег не трогаем:
        # человек должен увидеть новую сумму и нажать ещё раз.
        await session.rollback()
        return {"result": "price_changed", "quote": _safe_quote(st, cfg, now)}

    if body.pay == "gateway":
        await session.rollback()
        return await _checkout(
            body=body,
            user=user,
            session=session,
            st=st,
            q=q,
            cfg=cfg,
            payment_gateway_dao=payment_gateway_dao,
            create_payment=create_payment,
        )

    gateway_type = await _balance_gateway_type(payment_gateway_dao)
    if gateway_type is None:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Оплата временно недоступна: не настроен ни один рублёвый шлюз",
        )

    limit_before = st.device_limit
    try:
        result = await extra.buy_from_balance(
            session,
            sdk=getattr(remnawave, "sdk", None),
            transaction_dao=transaction_dao,
            gateway_type=gateway_type,
            st=st,
            q=q,
            request_id=body.request_id,
            source="balance",
            price_per_30d=int(cfg["price_rub_30d"]),
        )
    except extra.SlotChanged:
        await session.rollback()
        return {"result": "not_available", "reason": "slot_changed", "quote": _safe_quote(st, cfg, now)}
    except extra.PanelRejected as exc:
        await session.rollback()
        logger.warning(f"extra_device: панель отказала user_id={user.id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Не удалось добавить устройство — деньги не списаны. Попробуйте позже.",
        ) from exc
    if result.get("result") == "insufficient_balance":
        await session.rollback()
        return {
            "result": "insufficient_balance",
            "need": _fmt(result["need"]),
            "balance": _fmt(result["balance"]),
        }

    try:
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        # Панель уже приняла новый лимит, а база — нет. Возвращаем панель обратно
        # (best-effort) и зовём владельца: молча оставить расхождение нельзя.
        logger.critical(f"extra_device: commit упал после панели, user_id={user.id}: {exc}")
        await _rollback_panel(remnawave, st, limit_before)
        await _notify_admins(notifier, extra.admin_text("commit_failed", user=user.log, limit=limit_before))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Не удалось сохранить покупку. Напишите в поддержку — мы разберёмся.",
        ) from exc

    if cfg.get("notify_admins"):
        await _notify_admins(
            notifier,
            extra.admin_text(
                "bought",
                user=user.log,
                kind=q.kind,
                until=q.period_end,
                amount=_fmt(q.amount),
                source="balance",
            ),
        )
    return {
        "result": "applied",
        "device_limit": result["device_limit"],
        "until": _iso(result["until"]),
        "spent": _fmt(result["spent"]),
        "balance": _fmt(result["balance"]),
    }


def _assert_email_verified(user: Any) -> None:
    """Тот же гейт, что у покупки подписки: правило одно, реализация одна."""
    from overlay_patches.public_subscription import _assert_web_purchase_email_verified

    _assert_web_purchase_email_verified(user)


def _safe_quote(st: extra.UserState, cfg: dict, now: datetime) -> dict[str, Any]:
    try:
        return _quote_payload(st, cfg, now)
    except Exception:  # noqa: BLE001 — предложение для показа не должно ронять ответ
        logger.exception("extra_device: не собрал предложение для ответа")
        return {"enabled": True}


def _replay(saved: dict, user_id: int) -> dict[str, Any]:
    """Ответ по уже существующему заказу с этим `request_id`.

    Чужой ключ — 409 без подробностей: `request_id` уникален на всю установку, и
    ответ на подобранный чужой UUID не должен раскрывать ни сумму, ни ссылку.
    """
    if saved["user_id"] != user_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Повторите попытку")
    if saved["status"] == "applied":
        return {
            "result": "applied",
            "device_limit": None,
            "until": _iso(saved["period_end"]),
            "spent": _fmt(saved["amount"]),
            "balance": None,
            "repeat": True,
        }
    if saved["status"] == "pending" and saved.get("payment_url"):
        return {
            "result": "pending",
            "payment_id": str(saved["payment_id"]),
            "payment_url": saved["payment_url"],
            "amount": _fmt(saved["amount"]),
            "repeat": True,
        }
    return {"result": "not_available", "reason": saved.get("status") or "unknown", "repeat": True}


async def _checkout(
    *,
    body: BuyRequest,
    user: Any,
    session: AsyncSession,
    st: extra.UserState,
    q: extra.Quote,
    cfg: dict,
    payment_gateway_dao: PaymentGatewayDao,
    create_payment: CreatePayment,
) -> dict[str, Any]:
    """Счёт на точную сумму. Строку заказа пишем ДО отдачи ссылки — иначе 503."""
    allowed = {g.type for g in _web_rub_gateways(await payment_gateway_dao.get_active())}
    if body.gateway_type is None or body.gateway_type not in allowed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Платёжный шлюз недоступен"
        )

    try:
        payment = await create_payment(
            user,
            CreatePaymentDto(
                plan_snapshot=extra.synthetic_snapshot(q.days),
                pricing=PriceDetailsDto(
                    original_amount=q.amount, discount_percent=0, final_amount=q.amount
                ),
                purchase_type=PurchaseType.NEW,
                gateway_type=body.gateway_type,
            ),
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        # Шлюз не ответил — это «попробуйте ещё раз», а не поломка кабинета: 500
        # выглядел бы для человека как «у них всё сломалось».
        logger.warning(f"extra_device: шлюз не выставил счёт user_id={user.id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Платёжный шлюз не ответил, попробуйте ещё раз",
        ) from exc

    try:
        fresh = await extra.record_order(
            session,
            request_id=body.request_id,
            payment_id=payment.id,
            payment_url=payment.url,
            user_id=user.id,
            st=st,
            q=q,
            price_per_30d=int(cfg["price_rub_30d"]),
        )
    except Exception as exc:  # noqa: BLE001
        logger.critical(f"extra_device: заказ по счёту '{payment.id}' не записан: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Не удалось создать счёт, попробуйте ещё раз",
        ) from exc

    if not fresh:
        # Параллельный двойник успел занять ключ: отдаём ЕГО ссылку, а наш счёт
        # останется PENDING и сгорит кроном базы — платить по нему человек не пойдёт.
        saved = await extra.order_by_request(session, body.request_id)
        if saved is not None and saved.get("payment_url"):
            return {
                "result": "pending",
                "payment_id": str(saved["payment_id"]),
                "payment_url": saved["payment_url"],
                "amount": _fmt(saved["amount"]),
                "repeat": True,
            }

    return {
        "result": "pending",
        "payment_id": str(payment.id),
        "payment_url": payment.url,
        "amount": _fmt(q.amount),
    }


async def _rollback_panel(remnawave: Any, st: extra.UserState, limit: int) -> None:
    """Вернуть панели прежний лимит. Реализация одна на все точки — в сервисе."""
    await extra.compensate_limit(getattr(remnawave, "sdk", None), st, limit)
