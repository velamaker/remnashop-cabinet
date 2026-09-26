"""Win-back истёкших: «вернись, вот скидка» через N дней после конца подписки (overlay).

Через `days_after` дней ПОСЛЕ окончания подписки юзеру выдаётся одноразовая скидка
на возврат (`users.purchase_discount` — база гасит её после покупки) + напоминание в
Telegram/Web Push. Конфиг assets/winback.json, правится в админке. Дефолт ВЫКЛ.
Родственно скидке триальщикам (см. overlay_trial_discount) — та же механика скидки.

ВИД ПРЕДЛОЖЕНИЯ (`mode`):
  • `term` — «90 дней по цене двух месяцев». Ушедший чаще всего брал месяц; ещё одна
    скидка на месяц возвращает его на месяц. Предложение длинного срока возвращает
    надолго. Процент считается ДЛЯ ТАРИФА ЧЕЛОВЕКА так, чтобы `term_days` стоили не
    дороже его же `pay_days` (term_percent) В КАЖДОЙ валюте, где у тарифа есть оба
    срока, и берётся наибольший (tasks/winback.py, _pick_prices); кнопка ведёт сразу
    на этот тариф с выбранным сроком. Скидка — та же одноразовая `purchase_discount` базы: она
    действует на любую покупку, а срок подсказывают текст и кнопка. Ограничить её
    одним сроком можно только правкой расчёта цен в денежном пути базы — не делаем.
    Посчитать нельзя (нет тарифа, нет цены на один из сроков, длинный срок и так не
    дороже) — этому человеку уходит прежняя «скидка N %».
  • `percent` — прежнее «возвращайтесь, скидка N %».
Файл, сохранённый ДО появления выбора, остаётся на `percent`: обновление не должно
молча менять предложение, которое владелец уже включил. Новая установка — `term`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
CONFIG_PATH = ASSETS_DIR / "winback.json"

MODES = ("term", "percent")

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,       # по умолчанию выключено
    "mode": "term",         # «90 дней по цене двух месяцев»; percent — прежняя скидка
    "term_days": 90,        # какой срок предлагаем
    "pay_days": 60,         # по цене какого срока
    "percent": 20,          # % скидки на возврат (режим percent и запасной путь term)
    "days_after": 3,        # через сколько дней после окончания слать
    "lifetime_hours": 168,  # сколько живёт промо (дефолт 7 дней)
}


def _clamp(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    mode = data.get("mode")
    term = _clamp(data.get("term_days"), DEFAULT_CONFIG["term_days"], 31, 365)
    pay = _clamp(data.get("pay_days"), DEFAULT_CONFIG["pay_days"], 30, 364)
    if pay >= term:  # «90 по цене 90» — не предложение
        term, pay = DEFAULT_CONFIG["term_days"], DEFAULT_CONFIG["pay_days"]
    return {
        "enabled": bool(data.get("enabled", DEFAULT_CONFIG["enabled"])),
        "mode": mode if mode in MODES else DEFAULT_CONFIG["mode"],
        "term_days": term,
        "pay_days": pay,
        "percent": _clamp(data.get("percent"), DEFAULT_CONFIG["percent"], 1, 100),
        "days_after": _clamp(data.get("days_after"), DEFAULT_CONFIG["days_after"], 1, 90),
        "lifetime_hours": _clamp(data.get("lifetime_hours"), DEFAULT_CONFIG["lifetime_hours"], 1, 1440),
    }


def load_config() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text("utf-8"))
    except FileNotFoundError:
        return dict(DEFAULT_CONFIG)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"winback: не удалось прочитать конфиг ({exc}) — беру дефолт")
        return dict(DEFAULT_CONFIG)
    if not isinstance(data, dict):
        return dict(DEFAULT_CONFIG)
    if "mode" not in data:
        # Сохранено до появления выбора — у владельца уже работает «скидка N %».
        data = {**data, "mode": "percent"}
    return _normalize(data)


def term_percent(term_price: Any, pay_price: Any) -> int | None:
    """Скидка, при которой `term_days` стоят не дороже `pay_days`, % вверх до целого.

    None — посчитать нельзя или предложение не имеет смысла (нет цены, длинный срок и
    так не дороже). Потолок 90 %: дальше это уже не «по цене двух месяцев», а ошибка
    в ценах, и раздавать почти даром по ней нельзя. Чистая функция.
    """
    from decimal import ROUND_CEILING, Decimal, InvalidOperation

    try:
        term = Decimal(str(term_price))
        pay = Decimal(str(pay_price))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if term <= 0 or pay <= 0 or pay >= term:
        return None
    pct = int((100 - pay * 100 / term).to_integral_value(rounding=ROUND_CEILING))
    return pct if 1 <= pct <= 90 else None


def save_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize(config)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), "utf-8")
    return normalized
