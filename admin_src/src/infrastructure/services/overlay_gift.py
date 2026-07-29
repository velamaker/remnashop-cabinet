"""Подарочная подписка, оплаченная через шлюз (в обход баланса).

Механика повторяет пополнение баланса (overlay_topup) — это самый безопасный путь:
ядро выдачи подписок не трогаем вообще.

  • платёж создаётся штатным base `CreatePayment` с СИНТЕТИЧЕСКИМ снимком тарифа
    (id = -3, «Подарок»), чтобы шлюз получил корректный инвойс;
  • ПЕРЕД отдачей URL пишем строку в overlay-таблицу `gift_payments`
    (payment_id → тариф/длительность/сумма);
  • на вебхуке overlay-`ProcessPayment._handle_success` зовёт `try_issue_gift`:
    если payment_id есть в `gift_payments` — выпускаем подарочный код и делаем
    return, НЕ трогая подписку покупателя, событие покупки, рефералку и кэшбэк.

Идемпотентность — по флагу `issued`: повторный вебхук по тому же платежу не
создаст второй код.
"""

from __future__ import annotations

import json
import secrets
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional
from uuid import UUID

from loguru import logger
from sqlalchemy import text

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncSession

# Синтетический тариф для инвойса шлюза: −2 занят пополнением баланса.
GIFT_PLAN_ID = -3

# 128 бит энтропии: код — одноразовый ваучер на реальные деньги, а активация
# промокодов брутфорсится (см. комментарий в endpoints/public/gift.py).
_CODE_BYTES = 16


def new_gift_code() -> str:
    return "GIFT-" + secrets.token_hex(_CODE_BYTES).upper()


async def record_gift_payment(
    session: "AsyncSession",
    *,
    payment_id: UUID,
    user_id: int,
    plan_snapshot: dict[str, Any],
    duration_days: int,
    amount: Decimal,
    chat_id: Optional[int] = None,
    message_id: Optional[int] = None,
) -> None:
    """Фиксирует ожидаемый подарок ДО отдачи URL шлюза (код выпустим на вебхуке).

    Если эта запись не появится, платёж уйдёт как обычная покупка синтетического
    тарифа — поэтому вызывающий обязан не отдавать URL при ошибке.

    chat_id/message_id — сообщение бота с кнопкой «Перейти к оплате». После оплаты
    его надо убрать, иначе у покупателя навсегда висит кнопка на просроченный счёт.
    Из кабинета их нет (там своя страница) — тогда просто NULL.
    """
    await session.execute(
        text(
            "INSERT INTO gift_payments "
            "(payment_id, user_id, plan_snapshot, duration_days, amount, chat_id, message_id) "
            "VALUES (:pid, :uid, CAST(:snap AS JSONB), :days, :amt, :chat, :msg) "
            "ON CONFLICT (payment_id) DO NOTHING"
        ),
        {
            "pid": str(payment_id),
            "uid": user_id,
            "snap": json.dumps(plan_snapshot, ensure_ascii=False),
            "days": duration_days,
            "amt": amount,
            "chat": chat_id,
            "msg": message_id,
        },
    )
    await session.commit()


async def record_issued_gift(
    session: "AsyncSession",
    *,
    payment_id: UUID,
    user_id: int,
    plan_snapshot: dict[str, Any],
    duration_days: int,
    amount: Decimal,
    code: str,
) -> None:
    """Кладёт УЖЕ выпущенный подарок (оплата с баланса) в ту же историю, что и шлюзовые.

    Нужно, чтобы `list_user_gifts` показывал все коды покупателя: код виден один раз
    в ответе API, и без истории потерявший его даритель остаётся ни с чем.
    Вызывать best-effort — подарок уже действителен, ронять его из-за истории нельзя.
    """
    await session.execute(
        text(
            "INSERT INTO gift_payments "
            "(payment_id, user_id, plan_snapshot, duration_days, amount, code, issued, issued_at) "
            "VALUES (:pid, :uid, CAST(:snap AS JSONB), :days, :amt, :code, true, now()) "
            "ON CONFLICT (payment_id) DO NOTHING"
        ),
        {
            "pid": str(payment_id),
            "uid": user_id,
            "snap": json.dumps(plan_snapshot, ensure_ascii=False),
            "days": duration_days,
            "amt": amount,
            "code": code,
        },
    )
    await session.commit()


async def list_user_gifts(
    session: "AsyncSession", *, user_id: int, limit: int = 20
) -> list[dict[str, Any]]:
    """Подарки покупателя (и оплаченные с баланса, и через шлюз), свежие сверху."""
    rows = (
        await session.execute(
            text(
                "SELECT payment_id, plan_snapshot, duration_days, amount, code, issued, created_at "
                "FROM gift_payments WHERE user_id = :uid ORDER BY created_at DESC LIMIT :lim"
            ),
            {"uid": user_id, "lim": limit},
        )
    ).all()
    out: list[dict[str, Any]] = []
    for payment_id, snapshot, duration_days, amount, code, issued, created_at in rows:
        if isinstance(snapshot, str):
            try:
                snapshot = json.loads(snapshot)
            except Exception:  # noqa: BLE001
                snapshot = {}
        out.append(
            {
                "payment_id": str(payment_id),
                "plan_name": (snapshot or {}).get("name", ""),
                "duration_days": duration_days,
                "price": str(amount),
                # Код появляется только после успешной оплаты (у шлюзовых — на вебхуке).
                "code": code or None,
                "issued": bool(issued and code),
                "created_at": created_at.isoformat() if created_at else None,
            }
        )
    return out


async def create_gift_from_balance(
    session: "AsyncSession",
    *,
    user_id: int,
    plan_snapshot: dict[str, Any],
    price: Decimal,
) -> Optional[str]:
    """Списывает цену с ₽-баланса и выпускает код. None — если денег не хватило.

    Списание и выпуск кода — в ОДНОЙ транзакции: при любой ошибке rollback отменяет
    и списание (тот же приём, что в endpoints/public/gift.py — ручной возврат денег
    опасен, если упал сам commit).
    """
    row = (
        await session.execute(
            text(
                "UPDATE users SET cabinet_balance = cabinet_balance - :amt "
                "WHERE id = :uid AND cabinet_balance >= :amt RETURNING cabinet_balance"
            ),
            {"amt": price, "uid": user_id},
        )
    ).first()
    if not row:
        await session.rollback()
        return None

    code = new_gift_code()
    try:
        await session.execute(
            text(
                "INSERT INTO promocodes (code, is_active, reward_type, reward, plan_snapshot, "
                "availability, is_reusable, max_activations, expires_at) "
                "VALUES (:code, true, 'SUBSCRIPTION', NULL, CAST(:snap AS JSONB), 'ALL', false, 1, NULL)"
            ),
            {"code": code, "snap": json.dumps(plan_snapshot, ensure_ascii=False)},
        )
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        logger.warning(f"gift: покупка с баланса user_id={user_id} упала ({exc}), списание отменено")
        raise
    logger.info(f"gift: user_id={user_id} купил подарок с баланса, код '{code}'")
    return code


async def try_issue_gift(session: "AsyncSession", payment_id: UUID) -> Optional[dict[str, Any]]:
    """Если платёж — подарок, выпускает код. Иначе возвращает None.

    Идемпотентно: помечаем строку `issued` тем же запросом, что и выбираем, поэтому
    повторный вебхук второй код не создаст.
    """
    row = (
        await session.execute(
            text(
                "UPDATE gift_payments SET issued = true, issued_at = now() "
                "WHERE payment_id = :pid AND issued = false "
                "RETURNING user_id, plan_snapshot, duration_days, amount, chat_id, message_id"
            ),
            {"pid": str(payment_id)},
        )
    ).first()
    if not row:
        # Либо это не подарок, либо код уже выпущен — вернём код из строки, если он есть.
        existing = (
            await session.execute(
                text("SELECT code, plan_snapshot, duration_days FROM gift_payments WHERE payment_id = :pid"),
                {"pid": str(payment_id)},
            )
        ).first()
        if existing and existing[0]:
            logger.info(f"gift: платёж '{payment_id}' уже выпускал код, повтор проигнорирован")
            return None
        return None

    user_id, snapshot, duration_days, amount = row[0], row[1], row[2], row[3]
    chat_id, message_id = row[4], row[5]
    if isinstance(snapshot, str):
        snapshot = json.loads(snapshot)

    code = new_gift_code()
    try:
        await session.execute(
            text(
                "INSERT INTO promocodes (code, is_active, reward_type, reward, plan_snapshot, "
                "availability, is_reusable, max_activations, expires_at) "
                "VALUES (:code, true, 'SUBSCRIPTION', NULL, CAST(:snap AS JSONB), 'ALL', false, 1, NULL)"
            ),
            {"code": code, "snap": json.dumps(snapshot, ensure_ascii=False)},
        )
        await session.execute(
            text("UPDATE gift_payments SET code = :code WHERE payment_id = :pid"),
            {"code": code, "pid": str(payment_id)},
        )
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        # Откатываем пометку issued, чтобы повторный вебхук довыпустил код.
        await session.rollback()
        await session.execute(
            text("UPDATE gift_payments SET issued = false, issued_at = NULL WHERE payment_id = :pid"),
            {"pid": str(payment_id)},
        )
        await session.commit()
        logger.error(f"gift: не смог выпустить код по платежу '{payment_id}': {exc}")
        raise

    logger.info(f"gift: по платежу '{payment_id}' выпущен код '{code}' (user_id={user_id})")
    return {
        "code": code,
        "user_id": user_id,
        "plan_name": (snapshot or {}).get("name", ""),
        "duration_days": duration_days,
        "amount": Decimal(str(amount)),
        # Сообщение бота с кнопкой оплаты — вызывающий его удалит (может быть None).
        "chat_id": chat_id,
        "message_id": message_id,
    }
