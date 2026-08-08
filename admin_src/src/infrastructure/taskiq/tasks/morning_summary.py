"""Утренняя сводка владельцу в Telegram — раз в сутки одним сообщением.

За прошедшие сутки (вчерашний календарный день): выручка по валютам + число
оплат, новые регистрации, всего активных подписок, сколько истекает в ближайшие
N дней. Данные считаются на лету из БД (как /statistics) — всегда актуальны.

Шлём штатным notify_admins (raw-message) только владельцу (Role.OWNER). Базовый
образ шлёт свои уведомления сам — в него не влезаем, эта сводка самодостаточна.

Крон почасовой; внутри проверяем, что текущий час = настроенному (время правится
без правки cron). Дедуп по дате в assets/morning_summary_state.json — чтобы при
рестарте/повторе не слать дважды за день.

Настройка (тумблер/час/окно дней) — assets/morning_summary.json, правится из
админки (Настройки), см. services/overlay_morning_summary.py. Если файла ещё нет,
дефолты берутся из прежних env (MORNING_SUMMARY_ENABLED/HOUR/EXPIRING_DAYS).

Auto-discover taskiq по глобу tasks/*.py.
"""

import html as html_lib
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Notifier
from src.application.dto import MessagePayloadDto
from src.core.enums import Role
from src.infrastructure.services.overlay_morning_summary import load_config
from src.infrastructure.taskiq.broker import broker

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
STATE_PATH = ASSETS_DIR / "morning_summary_state.json"

# Символы валют для красивого вывода (фолбэк — сам код валюты).
_CURRENCY_SIGN = {"RUB": "₽", "USD": "$", "EUR": "€", "XTR": "⭐"}

# Сколько человек показываем поимённо. Сегодня истекающих единицы, к концу месяца
# может быть полсотни — весь список в сообщение не влезет: классический предел
# Telegram 4096 знаков (rich — 32768, но читать простыню всё равно невозможно).
# 20 строк — верх разумного для чтения с телефона, а точный предел считаем по
# реальной длине готовых строк (см. _LIST_BUDGET), а не «на глазок».
_LIST_LIMIT = 20
# Запас под список в классическом сообщении: 4096 минус шапка сводки (заголовок,
# четыре буллета, теги — около 350 знаков) и минус хвост «…и ещё N». Строку, после
# которой бюджет кончился, не печатаем — она уходит в счётчик остатка.
_LIST_BUDGET = 3500
# Длинное имя в строке списка обрезаем: на телефоне строка всё равно переносится,
# а из-за одного «Иван Иванович Иванов-Петровский» список превращается в кашу.
_NAME_MAX = 22


def _load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"morning_summary: не смог сохранить состояние: {e}")


def _fmt_amount(amount: float) -> str:
    # Целые суммы без хвоста .00, дробные — с двумя знаками.
    return f"{amount:.0f}" if float(amount).is_integer() else f"{amount:.2f}"


def _plural_days(n: int) -> str:
    """«1 день» / «3 дня» / «7 дней» — иначе строка сводки читается коряво."""
    if 11 <= n % 100 <= 14:
        return f"{n} дней"
    last = n % 10
    if last == 1:
        return f"{n} день"
    if 2 <= last <= 4:
        return f"{n} дня"
    return f"{n} дней"


def _left(expire_at: datetime, now: datetime) -> str:
    """Сколько осталось — «через 4 ч», «через 2 дня». Дата без остатка не читается.

    Владельцу важна срочность, а не арифметика: «08.08 04:27» ничего не говорит,
    пока не сравнишь с сегодняшним числом.
    """
    total = int((expire_at - now).total_seconds())
    if total <= 0:
        return "истекает"
    if total < 3600:
        return f"через {max(1, total // 60)} мин"
    if total < 86400:
        return f"через {total // 3600} ч"
    return f"через {_plural_days(total // 86400)}"


def _short_name(name: str) -> str:
    """Имя для строки списка: без переносов и не длиннее _NAME_MAX."""
    cleaned = " ".join((name or "").split()) or "без имени"
    if len(cleaned) <= _NAME_MAX:
        return cleaned
    return cleaned[: _NAME_MAX - 1].rstrip() + "…"


def _expiring_line(index: int, row: Any, now: datetime) -> str:
    """Одна строка списка: «3. Имя @user — 09.08 04:58, через 2 дня».

    Кроме имени, ника и срока в сообщение ничего не тащим: это живые люди, почта
    и платежи в утренней сводке не нужны.

    Строка — на ЧЕЛОВЕКА, а не на подписку. У кого в окне несколько подписок, тот
    идёт одной строкой по ближайшему сроку с пометкой «×2»: раньше на одну пометку
    «проб.» надеяться было нельзя — две подписки одного типа давали две байт-в-байт
    одинаковые строки. «проб.» ставим, только если пробные ВСЕ подписки в окне,
    иначе пометка врала бы про оплаченную.
    """
    who = html_lib.escape(_short_name(row.name))
    if row.username:
        who += f" @{html_lib.escape(row.username)}"
    when = row.expire_at.astimezone().strftime("%d.%m %H:%M")
    trial = " · проб." if row.is_trial else ""
    cnt = int(getattr(row, "cnt", 1) or 1)
    many = f" · ×{cnt}" if cnt > 1 else ""
    return f"{index}. {who} — {when}, {_left(row.expire_at, now)}{trial}{many}"


def _expiring_block(rows: list[Any], total: int, days: int) -> str:
    """Сворачиваемая цитата со списком «кто истекает» (пусто — если некому).

    <blockquote expandable> Telegram схлопывает сам: в сообщении видно заголовок,
    список раскрывается нажатием — цифра из таблицы перестаёт быть поводом лезть
    в админку, но и сводку собой не заслоняет.

    Длину режем дважды: по числу строк (_LIST_LIMIT) и по реально набранным
    знакам (_LIST_BUDGET) — имена бывают любой длины, а сообщение обрежется молча.
    Всё, что не поместилось, честно считаем в «…и ещё N».
    """
    if not rows:
        return ""

    now = datetime.now(timezone.utc)
    lines: list[str] = []
    used = 0
    for row in rows[:_LIST_LIMIT]:
        line = _expiring_line(len(lines) + 1, row, now)
        if used + len(line) + 1 > _LIST_BUDGET:
            break
        lines.append(line)
        used += len(line) + 1

    if not lines:
        return ""

    rest = max(0, total - len(lines))
    if rest:
        lines.append(f"…и ещё {rest} — весь список в админке")

    head = f"<b>⏳ Кто истекает (≤ {_plural_days(days)})</b>"
    return "\n<blockquote expandable>" + head + "\n" + "\n".join(lines) + "</blockquote>"


def _summary_keyboard() -> Optional[InlineKeyboardMarkup]:
    """Кнопки-ссылки под сводкой: сразу уйти в статистику или в кабинет.

    Без WEB_CABINET_URL кнопок нет — ссылки на пустоту Telegram не примет.
    Кнопку «Закрыть» дорисует сам нотификатор (_prepare_reply_markup).
    """
    base = (os.environ.get("WEB_CABINET_URL") or "").strip().rstrip("/")
    if not base.startswith("http"):
        return None

    # Обе ссылки в один ряд: ниже нотификатор допишет «Закрыть», и три отдельные
    # широкие кнопки под сводкой смотрелись бы простынёй.
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Статистика", url=f"{base}/admin/stats"),
                InlineKeyboardButton(text="🏠 Кабинет", url=base),
            ]
        ]
    )


@broker.task(schedule=[{"cron": "0 * * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def send_morning_summary(
    session: FromDishka[AsyncSession],
    notifier: FromDishka[Notifier],
) -> None:
    cfg = load_config()
    if not cfg["enabled"]:
        return
    if datetime.now().hour != cfg["hour"]:
        return

    today = date.today().isoformat()
    state = _load_state()
    if state.get("last_sent") == today:
        return  # уже слали сегодня

    days = cfg["expiring_days"]

    # Регистрации за вчера (только обычные пользователи).
    new_users = (
        await session.execute(
            text(
                """
                SELECT count(*) FROM users
                WHERE role::text = 'USER'
                  AND created_at >= date_trunc('day', now()) - interval '1 day'
                  AND created_at <  date_trunc('day', now())
                """
            )
        )
    ).scalar_one() or 0

    # Выручка за вчера по валютам + число оплат.
    revenue_rows = (
        await session.execute(
            text(
                """
                SELECT currency::text AS currency,
                       sum((pricing->>'final_amount')::numeric) AS amount,
                       count(*) AS cnt
                FROM transactions
                WHERE status::text = 'COMPLETED'
                  AND is_test = false
                  AND (pricing->>'final_amount')::numeric > 0
                  AND created_at >= date_trunc('day', now()) - interval '1 day'
                  AND created_at <  date_trunc('day', now())
                GROUP BY currency
                ORDER BY currency
                """
            )
        )
    ).all()

    # Всего активных подписок + истекают в ближайшие N дней.
    active_subs = (
        await session.execute(
            text("SELECT count(*) FROM subscriptions WHERE status::text = 'ACTIVE'")
        )
    ).scalar_one() or 0
    # Истекающих считаем по ЛЮДЯМ (count DISTINCT), а не по подпискам: у кого две
    # подписки — это один человек, которому надо продлиться. Так цифра сходится и
    # со списком ниже, и с фильтром «истекают в N дней» в админке (он тоже про
    # людей, web/endpoints/admin/users.py), куда зовёт хвост «…и ещё N».
    expiring = (
        await session.execute(
            text(
                """
                SELECT count(DISTINCT user_id) FROM subscriptions
                WHERE status::text = 'ACTIVE'
                  AND expire_at >= now()
                  AND expire_at < now() + make_interval(days => :n)
                """
            ),
            {"n": days},
        )
    ).scalar_one() or 0

    # Кто именно истекает — условия ОДИН В ОДИН как у счётчика выше (та же
    # транзакция, значит и now() тот же), поэтому цифра и список всегда сходятся.
    # Строка на человека: срок — ближайший из его подписок в окне, cnt — сколько
    # их всего (уходит в «×2»), «проб.» — только если пробные все до одной.
    # Сортировка по срочности, ближайшие сверху.
    expiring_rows = (
        await session.execute(
            text(
                """
                SELECT u.name AS name, u.username AS username,
                       min(s.expire_at) AS expire_at,
                       bool_and(s.is_trial) AS is_trial,
                       count(*) AS cnt
                FROM subscriptions s
                JOIN users u ON u.id = s.user_id
                WHERE s.status::text = 'ACTIVE'
                  AND s.expire_at >= now()
                  AND s.expire_at < now() + make_interval(days => :n)
                GROUP BY u.id, u.name, u.username
                ORDER BY min(s.expire_at) ASC, u.id ASC
                LIMIT :lim
                """
            ),
            {"n": days, "lim": _LIST_LIMIT},
        )
    ).all()

    pay_count = sum(int(r.cnt or 0) for r in revenue_rows)
    if revenue_rows:
        parts = []
        for r in revenue_rows:
            sign = _CURRENCY_SIGN.get(r.currency, r.currency)
            parts.append(f"{_fmt_amount(float(r.amount or 0))} {sign}")
        revenue_line = ", ".join(parts) + f" ({pay_count} опл.)"
    else:
        revenue_line = "нет оплат"

    yday_str = (datetime.now() - timedelta(days=1)).strftime("%d.%m")

    # Буллеты «• <b>Ключ</b>: значение» — тот же формат, что у остальных наших
    # уведомлений: rich-слой (overlay_rich_notify) разбирает их в таблицу, а если
    # Bot API rich не поддержит, строки нормально читаются и обычным текстом.
    # Список «кто истекает» идёт отдельной сворачиваемой цитатой ПОСЛЕ буллетов:
    # rich-слой такие блоки не разбирает, а доставляет как есть (там <details>).
    body = (
        f"<b>☀️ Сводка за {yday_str}</b>\n\n"
        f"<blockquote>\n"
        f"• <b>💰 Выручка</b>: {revenue_line}\n"
        f"• <b>🆕 Новых регистраций</b>: {int(new_users)}\n"
        f"• <b>📊 Активных подписок</b>: {int(active_subs)}\n"
        f"• <b>⏳ Истекают через {_plural_days(days)}</b>: {int(expiring)}\n"
        f"</blockquote>"
        f"{_expiring_block(list(expiring_rows), int(expiring), days)}"
    )

    try:
        await notifier.notify_admins(
            payload=MessagePayloadDto(
                i18n_key="raw-message",
                i18n_kwargs={"content": body},
                delete_after=None,
                reply_markup=_summary_keyboard(),
                # Без этого «Закрыть» не появляется: у MessagePayloadDto поле по
                # умолчанию True, то есть кнопку нотификатор НЕ дописывает. Владелец
                # просил её вернуть (7 августа: «нет кнопки закрыть») — ставим явно,
                # свои кнопки при этом остаются своим рядом.
                disable_default_markup=False,
            ),
            roles=[Role.OWNER],
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"morning_summary: не смог отправить сводку: {e}")
        return

    state["last_sent"] = today
    _save_state(state)
    logger.info("morning_summary: сводка владельцу отправлена")
