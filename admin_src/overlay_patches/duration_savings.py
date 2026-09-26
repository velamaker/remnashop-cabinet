"""Выгода длинного срока на кнопках выбора срока в боте: «⌛ 90 дней · −25%».

ЗАЧЕМ. 45 из 70 покупок — на месяц. Скидка за длинный срок была, но незаметна: кнопка
показывала цену 90 дней отдельной цифрой, сравнивать её с месячной человек должен был
сам. Теперь выгода против помесячной оплаты дописана прямо в подпись срока — так же,
как на кнопке срока в кабинете (cabinet/src/lib/termSavings.ts, правила те же).

КАК СЧИТАЕМ. От цены 30 дней ЭТОГО ЖЕ тарифа для ЭТОГО ЖЕ человека (итоговая сумма
после личной скидки — она одинаково уменьшает обе цены и на процент не влияет). Срок
в N дней сравниваем с N/30 месячных цен. Вниз до целого; меньше MIN_SHOWN — не пишем:
«−2 %» — это шум округления, а не выгода. Нет месячного срока или цены — не пишем.

ПОЧЕМУ В ПОДПИСЬ СРОКА. Кнопку базы собирает `I18nFormat("btn-subscription.duration",
period=…, final_amount=…)` с фиксированным набором переменных; новую переменную не
передать, не трогая окно. Подпись `period` — готовая строка из геттера, её и дополняем.
Геттер базы при этом не копируем, а оборачиваем: он отдаёт данные как есть, мы только
дописываем хвост. Любой сбой расчёта — кнопки остаются как у базы.

КУДА ВСТРАИВАЕМСЯ. `dialog.py` делает `from .getters import duration_getter` и кладёт
функцию в окно; хук срабатывает сразу после загрузки `getters`, внутри того же импорта,
— имя подменяется раньше, чем его заберёт диалог (как plan_change_bot.py).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from loguru import logger

from . import PatchTargetChanged

TARGET_MODULE = "src.telegram.routers.subscription.getters"

MIN_SHOWN = 5

# Базовый геттер — кладёт apply(); обёртка берёт его отсюда (глобаль модуля).
_BASE: dict[str, Any] = {}


def _amount(value: Any) -> Optional[Decimal]:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number > 0 else None


def savings_percent(month_amount: Any, term_amount: Any, days: int) -> Optional[int]:
    """Выгода срока `days` против помесячной оплаты, % вниз до целого, или None."""
    if not isinstance(days, int) or days <= 30:
        return None
    month = _amount(month_amount)
    term = _amount(term_amount)
    if month is None or term is None:
        return None
    pct = int((100 - term * 100 * 30 / (month * days)).to_integral_value(rounding="ROUND_FLOOR"))
    return pct if pct >= MIN_SHOWN else None


def with_savings(durations: Any) -> Any:
    """Дописать « · −N%» к подписи сроков. Чистая функция; вход не меняет."""
    if not isinstance(durations, list):
        return durations
    month = next(
        (d.get("final_amount") for d in durations if isinstance(d, dict) and d.get("days") == 30),
        None,
    )
    if month is None:
        return durations
    out = []
    for item in durations:
        if not isinstance(item, dict):
            out.append(item)
            continue
        pct = savings_percent(month, item.get("final_amount"), item.get("days"))
        if pct is not None and isinstance(item.get("period"), str):
            item = {**item, "period": f"{item['period']} · −{pct}%"}
        out.append(item)
    return out


async def duration_getter(**kwargs: Any) -> dict[str, Any]:
    data = await _BASE["duration_getter"](**kwargs)
    try:
        data["durations"] = with_savings(data.get("durations"))
    except Exception as exc:  # noqa: BLE001 — кнопки базы важнее подсказки о выгоде
        logger.warning(f"duration_savings: выгода не посчитана ({exc}) — кнопки как у базы")
    return data


def apply() -> str:
    import src.telegram.routers.subscription.getters as target

    if getattr(target, "duration_getter", None) is None:
        raise PatchTargetChanged(
            "в subscription/getters.py больше нет duration_getter — выгода на кнопках "
            "сроков в боте пропадёт (цены останутся)"
        )
    if getattr(target.duration_getter, "_overlay_savings", False):
        return "уже обёрнут"

    # Обёртка передаёт kwargs базе как есть: dishka у базы разбирает их своим @inject,
    # своих зависимостей у обёртки нет — поэтому и собственный @inject ей не нужен.
    _BASE["duration_getter"] = target.duration_getter
    duration_getter._overlay_savings = True  # type: ignore[attr-defined]
    target.duration_getter = duration_getter
    return "на кнопках сроков видна выгода против помесячной оплаты"
