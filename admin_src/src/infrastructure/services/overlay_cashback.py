"""Ступенчатый кэшбэк баллами ПОКУПАТЕЛЮ за оплату (overlay RемнаShop).

Начисляется покупателю (не рефереру) за каждую РЕАЛЬНУЮ оплату. % зависит от
длительности купленного тарифа (ступени), переводится в баллы по курсу
1 балл = point_value_rub ₽ — тот же курс, что у рефералки (см. rewards.py).
Пример: оплата 700 ₽, тариф на год → ступень 8% → 700×8% = 56 ₽ / 7 = 8 баллов.

Точка вызова — overlay `AssignReferralRewards._execute` (в самом начале, ДО
реферальных early-return): он зовётся синхронно базовым `ProcessPayment` для
КАЖДОЙ не-бесплатной оплаты — карта, оплата с баланса, autopay. Ядро
`ProcessPayment` при этом НЕ трогаем. Ошибки кэшбэка не должны ломать рефералку —
здесь всё в try/except, наружу не пробрасываем.

Только RUB: курс 1 балл=7 ₽ корректен лишь для рублёвых платежей; USD/XTR
пропускаем. Конфиг — assets/cashback.json (тумблер + ступени), правится из
админки (web/endpoints/admin/cashback.py). Дефолт — ВКЛ со ступенями 3/5/8%.
"""

# ВАЖНО: этот модуль импортируется из application-слоя (rewards.py). Чтобы не
# создать цикл (profile_edit → пакет user → referral → rewards → сюда), НЕ тянем
# application-модули на верхнем уровне: аннотации ленивые (`from __future__`),
# а ChangeUserPointsDto импортируем внутри функции.
from __future__ import annotations

import json
import os
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from src.core.enums import Currency

if TYPE_CHECKING:
    from src.application.dto import TransactionDto, UserDto
    from src.application.use_cases.user.commands.profile_edit import ChangeUserPoints

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
CONFIG_PATH = ASSETS_DIR / "cashback.json"

# Дефолт: включён, ступени по длительности тарифа, курс 1 балл = 7 ₽.
DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "point_value_rub": 7,
    "tiers": [
        {"min_days": 90, "percent": 3},
        {"min_days": 180, "percent": 5},
        {"min_days": 365, "percent": 8},
    ],
}


def _normalize_tiers(raw: Any) -> list[dict[str, int]]:
    """Приводит ступени к [{min_days:int>=1, percent:int 1..100}], сортирует по
    min_days. Мусор/дубли отбрасываются."""
    tiers: list[dict[str, int]] = []
    seen: set[int] = set()
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                min_days = int(item.get("min_days"))
                percent = int(item.get("percent"))
            except (TypeError, ValueError):
                continue
            if min_days < 1 or percent < 1 or percent > 100 or min_days in seen:
                continue
            seen.add(min_days)
            tiers.append({"min_days": min_days, "percent": percent})
    tiers.sort(key=lambda t: t["min_days"])
    return tiers


def load_config() -> dict[str, Any]:
    """Читает assets/cashback.json, нормализует. При отсутствии/ошибке — дефолт."""
    try:
        data = json.loads(CONFIG_PATH.read_text("utf-8"))
    except FileNotFoundError:
        return dict(DEFAULT_CONFIG)
    except Exception as exc:  # noqa: BLE001 — битый конфиг не должен ронять оплату
        logger.warning(f"cashback: не удалось прочитать конфиг ({exc}) — беру дефолт")
        return dict(DEFAULT_CONFIG)

    if not isinstance(data, dict):
        return dict(DEFAULT_CONFIG)

    try:
        point_value = int(data.get("point_value_rub", DEFAULT_CONFIG["point_value_rub"]))
    except (TypeError, ValueError):
        point_value = DEFAULT_CONFIG["point_value_rub"]
    if point_value < 1:
        point_value = DEFAULT_CONFIG["point_value_rub"]

    tiers = _normalize_tiers(data.get("tiers"))
    if not tiers:
        tiers = list(DEFAULT_CONFIG["tiers"])

    return {
        "enabled": bool(data.get("enabled", True)),
        "point_value_rub": point_value,
        "tiers": tiers,
    }


def save_config(config: dict[str, Any]) -> dict[str, Any]:
    """Нормализует и сохраняет конфиг, возвращает записанное."""
    try:
        point_value = int(config.get("point_value_rub", DEFAULT_CONFIG["point_value_rub"]))
    except (TypeError, ValueError):
        point_value = DEFAULT_CONFIG["point_value_rub"]
    if point_value < 1:
        point_value = DEFAULT_CONFIG["point_value_rub"]

    tiers = _normalize_tiers(config.get("tiers"))
    normalized = {
        "enabled": bool(config.get("enabled", True)),
        "point_value_rub": point_value,
        "tiers": tiers,
    }
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), "utf-8")
    return normalized


def _pick_percent(duration_days: int, tiers: list[dict[str, int]]) -> int:
    """Максимальный % среди ступеней, у которых min_days <= длительности. 0 — нет."""
    best = 0
    for tier in tiers:
        if duration_days >= tier["min_days"] and tier["percent"] > best:
            best = tier["percent"]
    return best


def compute_cashback_points(
    *,
    final_amount: Any,
    duration_days: int,
    config: dict[str, Any],
) -> int:
    """Сколько баллов начислить: round(сумма × % / курс). 0 — если не положено."""
    percent = _pick_percent(duration_days, config["tiers"])
    if percent <= 0:
        return 0
    try:
        amount = Decimal(str(final_amount))
    except (InvalidOperation, TypeError, ValueError):
        return 0
    if amount <= 0:
        return 0
    point_value = Decimal(str(config["point_value_rub"]))
    raw = amount * Decimal(percent) / Decimal(100) / point_value
    points = int(raw.to_integral_value(rounding=ROUND_HALF_UP))
    return points if points > 0 else 0


async def award_purchase_cashback(
    buyer: UserDto,
    transaction: TransactionDto,
    change_user_points: ChangeUserPoints,
) -> None:
    """Начислить кэшбэк-баллы покупателю за оплату. Никогда не бросает наружу."""
    try:
        from src.application.use_cases.user.commands.profile_edit import ChangeUserPointsDto

        config = load_config()
        if not config["enabled"]:
            return

        # Только рублёвые платежи (курс 1 балл = N ₽ определён для RUB).
        if transaction.currency != Currency.RUB:
            return

        snapshot = transaction.plan_snapshot
        if snapshot is None or snapshot.is_trial:
            return

        points = compute_cashback_points(
            final_amount=transaction.pricing.final_amount,
            duration_days=snapshot.duration,
            config=config,
        )
        if points <= 0:
            return

        await change_user_points.system(
            ChangeUserPointsDto(user_id=buyer.id, amount=points)
        )
        logger.info(
            f"cashback: начислено '{points}' баллов покупателю '{buyer.remna_name}' "
            f"за транзакцию '{transaction.payment_id}' "
            f"(сумма {transaction.pricing.final_amount} {transaction.currency})"
        )
    except Exception:  # noqa: BLE001 — кэшбэк best-effort, не ломаем оплату/рефералку
        logger.exception(
            f"cashback: не удалось начислить покупателю '{getattr(buyer, 'remna_name', '?')}'"
        )
