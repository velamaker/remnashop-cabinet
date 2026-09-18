"""Public: докупка «+N ГБ к текущему окну трафика» (overlay).

Две ручки: предложение с ценой и сроком (только чтение) и покупка — с рублёвого
баланса одной транзакцией базы либо счётом через обычный RUB-шлюз.

ДЕНЕЖНЫЙ ПУТЬ. Всё важное — в services/overlay_extra_traffic.py, здесь только разбор
запроса, замок, ответ. Правила, из-за которых код выглядит именно так:
  * бизнес-отказы — это 200 с полем `result`, а не HTTP-ошибка: `ApiError.detail`
    кабинета — строка, и коду причины неоткуда взять перевод;
  * `request_id` кабинета ищем ДО замка и ещё раз ПОД замком — иначе двойной клик
    (или повтор запроса из-за разорванного соединения) купил бы два раза;
  * чужой `request_id` — 409 и ни слова о чужом заказе: ключ уникален на всю
    установку, и подбор чужого UUID не должен ничего рассказывать;
  * текущий лимит берём ИЗ ПАНЕЛИ, а не из нашей строки: `subscriptions.traffic_limit`
    хранит округлённые ГБ, и «наше число + 50» незаметно сдвигало бы человеку лимит,
    если в панели он не кратен гигабайту;
  * просроченные прибавки закрываем ПРЯМО В ТРАНЗАКЦИИ покупки: между обнулением
    расхода панелью и проходом крона проходит до 17 минут, и покупка в это окно
    иначе дала бы двойной объём;
  * панель — последним шагом покупки с баланса: её ошибка откатывает транзакцию
    целиком, и человеку честно отвечаем «деньги не списаны».
"""

from __future__ import annotations

import uuid as uuid_lib
from dataclasses import replace as dc_replace
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
from src.core.utils.converters import bytes_to_gb
from src.core.utils.time import datetime_now
from src.infrastructure.services import overlay_extra_traffic as extra
from src.web.endpoints.public._common import CurrentUser

router = APIRouter(prefix="/subscription", tags=["Public - Extra traffic"])


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
    """Чем подписать транзакцию «Баланс · трафик»: колонка gateway_type NOT NULL.

    Берём первый АКТИВНЫЙ рублёвый шлюз, а если активных нет — любой рублёвый из
    таблицы: строки всех типов заводит установщик, и выключенные шлюзы не повод
    отказать человеку в покупке за деньги, которые уже лежат на балансе.
    """
    active = [g for g in await payment_gateway_dao.get_active() if g.currency == Currency.RUB]
    if active:
        return active[0].type
    every = [g for g in await payment_gateway_dao.get_all() if g.currency == Currency.RUB]
    return every[0].type if every else None


def _window(st: extra.UserState, panel: Optional[extra.PanelView], now: datetime):
    """(момент обнуления, известен ли он, якорь). Якорь — дата из панели или из кеша."""
    anchor = None
    if panel is not None:
        anchor = panel.created_at
    if anchor is None:
        anchor = extra.cached_created_at(st.remna_uuid)
    if anchor is None:
        anchor = next((g.panel_created_at for g in st.grants if g.panel_created_at), None)
    known = not (extra.needs_anchor(st.strategy) and anchor is None)
    return extra.next_traffic_reset(st.strategy, anchor, now), known, anchor


def _payload(
    st: extra.UserState, panel: Optional[extra.PanelView], cfg: dict, now: datetime
) -> dict[str, Any]:
    """Предложение: сколько и за сколько можно докупить сейчас. Без записи."""
    window_end, known, _anchor = _window(st, panel, now)
    limit_gb = bytes_to_gb(panel.limit_bytes) if panel is not None else st.traffic_limit
    payload: dict[str, Any] = {
        "enabled": True,
        "currency": "RUB",
        "currency_symbol": "₽",
        "gb": int(cfg["gb_per_purchase"]),
        "price": _fmt(cfg["price_rub"]) if cfg.get("price_rub") else None,
        "balance": _fmt(st.balance),
        "traffic_limit_gb": limit_gb,
        "plan_traffic_limit_gb": st.plan_traffic_limit,
        "used_bytes": panel.used_bytes if panel is not None else None,
        "extra_gb_active": st.active_gb,
        "strategy": extra.strategy_name(st.strategy),
        "resets_at": _iso(window_end),
        "show_from_percent": int(cfg["show_from_percent"]),
        "subscription_expire_at": _iso(st.expire_at),
    }
    if panel is None:
        # Панель молчит: показываем, что знаем из базы, но кнопку прячем — считать
        # прибавку от неизвестного лимита нельзя.
        payload["offer"] = {"available": False, "reason": "panel_unavailable", "until": None}
        return payload
    why = extra.eligibility(st, cfg, now, window_end=window_end, window_known=known)
    hours_left = None
    if window_end is not None:
        hours_left = max(0, int((window_end - now).total_seconds() // 3600))
    payload["offer"] = {
        "available": why is None,
        "reason": why,
        "until": _iso(window_end),
        "hours_left": hours_left,
    }
    return payload


@router.get("/extra-traffic")
@inject
async def get_extra_traffic(
    user: CurrentUser,
    session: FromDishka[AsyncSession],
    remnawave: FromDishka[Remnawave],
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
    now = datetime_now()
    # В панель ходим ТОЛЬКО за тех, кому вообще можно продать. Безлимитному,
    # пробному и истёкшему предложение не положено, а лишний запрос на каждую
    # отрисовку Главной — положен ещё меньше.
    panel = None
    if extra.eligibility(st, cfg, now, window_end=None, window_known=True) in (None, "reset_too_soon", "window_cap"):
        panel = await extra.read_panel(remnawave, st)
    payload = _payload(st, panel, cfg, now)
    payload["gateways"] = [
        {"gateway_type": g.type.value, "currency_symbol": g.currency.symbol}
        for g in _web_rub_gateways(await payment_gateway_dao.get_active())
    ]
    return payload


class BuyRequest(BaseModel):
    request_id: uuid_lib.UUID
    pay: str = Field(default="balance", pattern="^(balance|gateway)$")
    gateway_type: Optional[PaymentGatewayType] = None
    expected_amount: Decimal = Field(gt=0)
    expected_gb: int = Field(gt=0)


async def _notify_admins(notifier: Notifier, content: str) -> None:
    try:
        await notifier.notify_admins(
            MessagePayloadDto(i18n_key="raw-message", i18n_kwargs={"content": content}, delete_after=None)
        )
    except Exception:  # noqa: BLE001 — уведомление не имеет права ломать оплаченную покупку
        logger.exception("extra_traffic: не предупредил владельца о докупке")


@router.post("/extra-traffic/buy")
@inject
async def buy_extra_traffic(
    body: BuyRequest,
    user: CurrentUser,
    session: FromDishka[AsyncSession],
    remnawave: FromDishka[Remnawave],
    payment_gateway_dao: FromDishka[PaymentGatewayDao],
    transaction_dao: FromDishka[TransactionDao],
    create_payment: FromDishka[CreatePayment],
    notifier: FromDishka[Notifier],
) -> dict[str, Any]:
    cfg = extra.load_config()
    now = datetime_now()
    # ПЕРВОЙ СТРОКОЙ, ДО ВСЕГО. Выключенные продажи — это состояние по умолчанию на
    # каждой установке, и запрос по закрытой ручке не должен ни брать замок строки
    # человека, ни ходить в панель: так чужой POST превращался в нагрузку на панель.
    if not extra.effective_enabled(cfg):
        return {"result": "not_available", "reason": "disabled"}

    # Тот же почтовый гейт, что у покупки подписки: это тоже покупка за деньги.
    _assert_email_verified(user)

    saved = await extra.order_by_request(session, body.request_id)
    if saved is not None:
        return _replay(saved, user.id)

    st = await extra.lock_state(session, user.id)
    # Под замком ищем ещё раз: двойной клик успевает дойти до этой точки дважды.
    saved = await extra.order_by_request(session, body.request_id)
    if saved is not None:
        await session.rollback()
        return _replay(saved, user.id)

    panel = await extra.read_panel(remnawave, st)
    if panel is None:
        await session.rollback()
        return {"result": "not_available", "reason": "panel_unavailable"}

    # Просроченные прибавки закрываем ДО расчёта: иначе покупка в окно «сброс
    # прошёл, крон не добежал» дала бы двойной объём и не тот потолок.
    ended_gb = sum(g.gb for g in st.grants if g.ends_at is not None and g.ends_at <= now)
    await extra.close_due_grants(session, st, now)
    st = extra.state_after_due(st, now)

    window_end, known, anchor = _window(st, panel, now)
    # Якорь мог прийти из кеша процесса, а не из ответа панели. В строку прибавки
    # пишем именно его: с пустым `panel_created_at` крон не смог бы посчитать момент
    # обнуления, и прибавка осталась бы в панели навсегда.
    panel = dc_replace(panel, created_at=anchor)
    why = extra.eligibility(st, cfg, now, window_end=window_end, window_known=known)
    if why is not None:
        await session.rollback()
        return {"result": "not_available", "reason": why, "quote": _safe_payload(st, panel, cfg, now)}

    q = extra.quote(cfg, now, window_end, st.expire_at)
    if q.amount > body.expected_amount or q.gb != body.expected_gb:
        # Владелец поменял цену или объём между показом и нажатием. Денег не трогаем:
        # человек должен увидеть новые условия и нажать ещё раз.
        await session.rollback()
        return {"result": "price_changed", "quote": _safe_payload(st, panel, cfg, now)}

    if body.pay == "gateway":
        await session.rollback()
        return await _checkout(
            body=body,
            user=user,
            session=session,
            st=st,
            panel=panel,
            q=q,
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

    limit_before = panel.limit_bytes
    try:
        result = await extra.buy_from_balance(
            session,
            sdk=getattr(remnawave, "sdk", None),
            transaction_dao=transaction_dao,
            gateway_type=gateway_type,
            st=st,
            panel=panel,
            q=q,
            request_id=body.request_id,
            source="balance",
            now=now,
            ended_gb=ended_gb,
        )
    except extra.PanelRejected as exc:
        await session.rollback()
        # КОМПЕНСАЦИЯ ОБЯЗАТЕЛЬНА И БЕЗУСЛОВНА. После отказа неизвестно, применился
        # PATCH или нет: таймаут и потерянный ответ неотличимы от «не дошло». Записи
        # о прибавке в базе не осталось — значит крон поднятый лимит никогда не
        # увидит и не опустит. Возврат прежнего значения безвреден, если ничего не
        # менялось, и чинит панель, если менялось.
        await extra.compensate_limit(getattr(remnawave, "sdk", None), st, limit_before)
        logger.warning(f"extra_traffic: панель отказала user_id={user.id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Не удалось добавить трафик — деньги не списаны. Попробуйте позже.",
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
        # (best-effort) и зовём владельца: молча оставить расхождение нельзя — повтор
        # того же заказа дал бы двойной объём за одну оплату.
        logger.critical(f"extra_traffic: commit упал после панели, user_id={user.id}: {exc}")
        await extra.compensate_limit(getattr(remnawave, "sdk", None), st, limit_before)
        await _notify_admins(
            notifier, extra.admin_text("commit_failed", user=user.log, limit=limit_before)
        )
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
                gb=q.gb,
                until=q.window_end,
                amount=_fmt(q.amount),
                source="balance",
            ),
        )
    return {
        "result": "applied",
        "gb": result["gb"],
        "traffic_limit_gb": result["traffic_limit_gb"],
        "unlocked": str(panel.status).upper() == "LIMITED",
        "until": _iso(result["until"]),
        "spent": _fmt(result["spent"]),
        "balance": _fmt(result["balance"]),
    }


def _assert_email_verified(user: Any) -> None:
    """Тот же гейт, что у покупки подписки: правило одно, реализация одна."""
    from overlay_patches.public_subscription import _assert_web_purchase_email_verified

    _assert_web_purchase_email_verified(user)


def _safe_payload(
    st: extra.UserState, panel: Optional[extra.PanelView], cfg: dict, now: datetime
) -> dict[str, Any]:
    try:
        return _payload(st, panel, cfg, now)
    except Exception:  # noqa: BLE001 — предложение для показа не должно ронять ответ
        logger.exception("extra_traffic: не собрал предложение для ответа")
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
            "gb": saved["gb"],
            "traffic_limit_gb": None,
            "until": _iso(saved["window_end"]),
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
    if saved["status"] == "credited":
        # Деньги уже на балансе, применение идёт (или его доведёт крон). Это НЕ отказ
        # и не «деньги не списаны»: наружу отдаём свой код, а не внутренний статус
        # заказа — кабинету его переводить нечем, и человек увидел бы слово «credited».
        return {"result": "not_available", "reason": "in_progress", "repeat": True}
    # Отклонённый заказ: причину отказа наружу не раскрываем (она про нашу кухню),
    # но код отдаём тот, который кабинет умеет показать человеком.
    return {"result": "not_available", "reason": "rejected", "repeat": True}


async def _checkout(
    *,
    body: BuyRequest,
    user: Any,
    session: AsyncSession,
    st: extra.UserState,
    panel: extra.PanelView,
    q: extra.Quote,
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
                plan_snapshot=extra.synthetic_snapshot(q.gb, q.days),
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
        logger.warning(f"extra_traffic: шлюз не выставил счёт user_id={user.id}: {exc}")
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
            panel=panel,
        )
    except Exception as exc:  # noqa: BLE001
        logger.critical(f"extra_traffic: заказ по счёту '{payment.id}' не записан: {exc}")
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
