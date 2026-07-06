"""Пополнение ₽-баланса через существующие платёжные шлюзы + бонус (overlay).

Пользователь платит N ₽ через обычный RUB-шлюз (YooKassa и т.п.) → на
cabinet_balance зачисляется N + бонус% (дефолт 7%). Приём входящих денег —
самый рискованный кусок, поэтому:
  • платёж создаётся штатным base `CreatePayment` (транзакция + URL шлюза);
  • перед возвратом URL пишем строку в overlay-таблицу `balance_topups`
    (payment_id → сумма/бонус/зачислено);
  • на вебхуке base `ProcessPayment._handle_success` (ОВЕРЛЕЙ, вариант B) в самом
    начале зовёт `try_credit_topup`: если payment_id есть в balance_topups —
    зачисляет баланс+бонус (идемпотентно по флагу credited) и делает return,
    НЕ трогая подписку/событие покупки/рефералку/кэшбэк.

Конфиг — assets/topup.json (тумблер/бонус/лимиты/пресеты), правится из админки.
Только RUB (баланс рублёвый).
"""

from __future__ import annotations

import json
import os
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional
from uuid import UUID

from loguru import logger
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
CONFIG_PATH = ASSETS_DIR / "topup.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "bonus_percent": 7,
    "min_amount": 100,
    "max_amount": 50000,
    "presets": [300, 500, 1000, 2000],
}


def _norm_presets(raw: Any) -> list[int]:
    presets: list[int] = []
    seen: set[int] = set()
    if isinstance(raw, list):
        for v in raw:
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            if n >= 1 and n not in seen:
                seen.add(n)
                presets.append(n)
    presets.sort()
    return presets


def _norm_int(raw: Any, default: int, minimum: int = 0) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return default
    return n if n >= minimum else default


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    bonus = _norm_int(data.get("bonus_percent"), DEFAULT_CONFIG["bonus_percent"], 0)
    if bonus > 100:
        bonus = 100
    min_amount = _norm_int(data.get("min_amount"), DEFAULT_CONFIG["min_amount"], 1)
    max_amount = _norm_int(data.get("max_amount"), DEFAULT_CONFIG["max_amount"], 1)
    if max_amount < min_amount:
        max_amount = min_amount
    presets = _norm_presets(data.get("presets"))
    if not presets:
        presets = list(DEFAULT_CONFIG["presets"])
    return {
        "enabled": bool(data.get("enabled", True)),
        "bonus_percent": bonus,
        "min_amount": min_amount,
        "max_amount": max_amount,
        "presets": presets,
    }


def load_config() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text("utf-8"))
    except FileNotFoundError:
        return dict(DEFAULT_CONFIG)
    except Exception as exc:  # noqa: BLE001 — битый конфиг не должен ронять оплату
        logger.warning(f"topup: не удалось прочитать конфиг ({exc}) — беру дефолт")
        return dict(DEFAULT_CONFIG)
    if not isinstance(data, dict):
        return dict(DEFAULT_CONFIG)
    return _normalize(data)


def save_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize(config)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), "utf-8")
    return normalized


def compute_bonus(amount: Decimal, config: dict[str, Any]) -> Decimal:
    """Бонус в ₽ = сумма × бонус% (округление до копеек)."""
    percent = Decimal(str(config.get("bonus_percent", 0)))
    if percent <= 0:
        return Decimal("0")
    return (amount * percent / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def validate_amount(raw: Any, config: dict[str, Any]) -> Optional[Decimal]:
    """Приводит сумму к Decimal и проверяет лимиты. None — если невалидна."""
    try:
        amount = Decimal(str(raw)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return None
    if amount < Decimal(str(config["min_amount"])) or amount > Decimal(str(config["max_amount"])):
        return None
    return amount


async def record_topup(
    session: "AsyncSession",
    *,
    payment_id: UUID,
    user_id: int,
    amount: Decimal,
    bonus: Decimal,
) -> None:
    """Фиксирует ожидаемое пополнение до отдачи URL шлюза (зачислим на вебхуке)."""
    await session.execute(
        text(
            "INSERT INTO balance_topups (payment_id, user_id, amount, bonus) "
            "VALUES (:p, :u, :a, :b) ON CONFLICT (payment_id) DO NOTHING"
        ),
        {"p": payment_id, "u": user_id, "a": amount, "b": bonus},
    )
    await session.commit()


async def try_credit_topup(session: "AsyncSession", payment_id: UUID) -> Optional[dict[str, Decimal]]:
    """Если payment_id — пополнение: атомарно зачисляет баланс+бонус (идемпотентно
    по флагу credited) и возвращает {amount,bonus,total}. Если это НЕ пополнение —
    возвращает None (обычная покупка идёт своим путём)."""
    row = (
        await session.execute(
            text(
                "SELECT user_id, amount, bonus, credited "
                "FROM balance_topups WHERE payment_id = :p FOR UPDATE"
            ),
            {"p": payment_id},
        )
    ).first()
    if row is None:
        return None  # обычная покупка

    user_id, amount, bonus, credited = row
    amount = Decimal(str(amount))
    bonus = Decimal(str(bonus))
    total = amount + bonus

    if not credited:
        await session.execute(
            text("UPDATE users SET cabinet_balance = cabinet_balance + :t WHERE id = :u"),
            {"t": total, "u": user_id},
        )
        await session.execute(
            text(
                "UPDATE balance_topups SET credited = true, credited_at = now() "
                "WHERE payment_id = :p"
            ),
            {"p": payment_id},
        )
        await session.commit()
        logger.info(
            f"topup: зачислено '{total}' ₽ (сумма {amount} + бонус {bonus}) "
            f"пользователю id={user_id}, payment '{payment_id}'"
        )
    else:
        logger.info(f"topup: платёж '{payment_id}' уже зачислён — пропускаю (идемпотентно)")

    return {"amount": amount, "bonus": bonus, "total": total}
