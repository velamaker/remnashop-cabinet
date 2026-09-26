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
from uuid import UUID, uuid4

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


async def insert_issued_gift(
    session: "AsyncSession",
    *,
    payment_id: UUID,
    user_id: int,
    plan_snapshot: dict[str, Any],
    duration_days: int,
    amount: Decimal,
    code: str,
) -> None:
    """Строка истории выпущенного подарка БЕЗ коммита — для вызова внутри транзакции
    покупки: списание, код и история либо появляются вместе, либо не появляются."""
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
                # Ссылка на сертификат — её и пересылают получателю.
                "certificate_url": certificate_url(code) if (issued and code) else None,
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
        # История подарков — в ТОЙ ЖЕ транзакции. Раньше бот её не писал вовсе:
        # подарок с баланса не попадал в «Мои подарки», а ссылка-сертификат из
        # сообщения бота вела на «такого подарка нет» (страница ищет по истории).
        await insert_issued_gift(
            session,
            payment_id=uuid4(),
            user_id=user_id,
            plan_snapshot=plan_snapshot,
            duration_days=int(plan_snapshot.get("duration") or 0),
            amount=price,
            code=code,
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


# ─── Сертификат по ссылке ─────────────────────────────────────────────────────
#
# ЗАЧЕМ. Подарок и раньше был кодом, и телеграм получателя знать не требовалось.
# Мешало другое: получатель должен был сам найти в боте раздел «Промокод» и
# вручную набрать `GIFT-` и 32 символа. Сертификат — это тот же код, завёрнутый в
# ссылку: страница `/gift/<код>` показывает, что подарено, и активирует в один
# тап — в боте через штатный deep link `?start=promo_<код>` (его бот умеет давно,
# с двойным подтверждением), в кабинете — через ввод промокода с подставленным
# кодом. Ни новых таблиц, ни новой механики активации.

GIFT_CODE_PREFIX = "GIFT-"


def is_gift_code(code: str) -> bool:
    """Похоже ли на наш подарочный код. Страница сертификата отвечает ТОЛЬКО за
    подарки: иначе она стала бы справочной по любым промокодам магазина."""
    raw = (code or "").strip().upper()
    body = raw[len(GIFT_CODE_PREFIX):]
    return (
        raw.startswith(GIFT_CODE_PREFIX)
        and len(body) == _CODE_BYTES * 2
        and all(ch in "0123456789ABCDEF" for ch in body)
    )


def certificate_url(code: str) -> str:
    """Адрес страницы сертификата. Пусто — адрес кабинета не задан (WEB_CABINET_URL)."""
    import os

    base = (os.environ.get("WEB_CABINET_URL") or "").strip().rstrip("/")
    return f"{base}/gift/{code}" if base else ""


def share_url(cert_url: str, plan_name: str, days: int) -> str:
    """Штатное «Поделиться» Telegram: выбор чата и готовый текст со ссылкой."""
    from urllib.parse import quote

    note = f"Дарю тебе подписку «{plan_name}» на {days} дн. — открой ссылку, чтобы активировать 🎁"
    return f"https://t.me/share/url?url={quote(cert_url, safe='')}&text={quote(note, safe='')}"


async def bot_activation_url(bot_service: Any, code: str) -> Optional[str]:
    """`https://t.me/<бот>?start=promo_<код>` — активация в боте одним нажатием.

    Основу берём из публичного метода бота (у рекламных ссылок тот же приём) и
    склеиваем штатным `Deeplink.PROMOCODE` — по тому же правилу бот её и разбирает.
    None — Telegram сейчас не ответил на getMe: страница покажет только кабинет.
    """
    try:
        from src.core.enums import Deeplink

        base = (await bot_service.get_ad_link_url("")).split("?", 1)[0]
        return Deeplink.PROMOCODE.build_url(base, code) if base else None
    except Exception as exc:  # noqa: BLE001 — ссылка удобство, не условие работы
        logger.warning(f"gift: не удалось собрать ссылку на бота: {exc}")
        return None


async def get_certificate(session: "AsyncSession", code: str) -> Optional[dict[str, Any]]:
    """Что подарено и можно ли ещё активировать. None — это не наш подарок.

    Отдаём только то, что видно на открытке: тариф, срок, состояние. Кто купил и
    когда — нет: ссылку пересылают, и открыть её может кто угодно.

    Состояния:
      * `ready`     — код жив и не активирован;
      * `activated` — подарок уже забрали;
      * `void`      — оплачен, но промокода больше нет. Так бывает, когда код
                      удалили из списка промокодов в админке: подарочные коды лежат
                      там вперемешку с обычными, и их «чистят». Покупатель заплатил,
                      поэтому страница направляет в поддержку, а не молчит.
    """
    raw = (code or "").strip().upper()
    if not is_gift_code(raw):
        return None

    row = (
        await session.execute(
            text(
                "SELECT g.plan_snapshot->>'name' AS plan_name, g.duration_days, "
                "  p.id AS promo_id, p.is_active, "
                "  (SELECT count(*) FROM promocode_activations a WHERE a.promocode_id = p.id) AS used "
                "FROM gift_payments g "
                "LEFT JOIN promocodes p ON p.code = g.code "
                "WHERE g.code = :code AND g.issued = true"
            ),
            {"code": raw},
        )
    ).first()
    if row is None:
        # Запасной путь: подарок, выпущенный мимо истории (так бот продавал подарки
        # с баланса до этой правки). Код с префиксом GIFT- и наградой-подпиской —
        # это подарок, даже если строки в gift_payments нет.
        row = (
            await session.execute(
                text(
                    "SELECT p.plan_snapshot->>'name', "
                    "  NULLIF(p.plan_snapshot->>'duration', '')::int, "
                    "  p.id, p.is_active, "
                    "  (SELECT count(*) FROM promocode_activations a WHERE a.promocode_id = p.id) "
                    "FROM promocodes p "
                    "WHERE p.code = :code AND p.reward_type = 'SUBSCRIPTION'"
                ),
                {"code": raw},
            )
        ).first()
    if row is None:
        return None

    plan_name, days, promo_id, is_active, used = row
    if int(used or 0) > 0:
        state = "activated"
    elif promo_id is None or not is_active:
        # Кода нет или его выключили в админке. Про активации здесь сказать нечего:
        # когда строку промокода удаляют, активации уходят каскадом вместе с ней.
        # Но подарок ОПЛАЧЕН, а значит «уже активирован» — неправда, от которой и
        # покупатель, и получатель решат, что код украли. Зовём в поддержку.
        state = "void"
    else:
        state = "ready"
    return {
        "code": raw,
        "plan_name": plan_name or "Подписка",
        "days": int(days or 0),
        "state": state,
    }


def gift_ready_text(plan_name: str, days: int, code: str, cert_url: str, *, paid: bool) -> str:
    """Сообщение покупателю с готовым подарком — одно на бота и на вебхук шлюза.

    Раньше здесь был голый код и совет «пусть введёт его в разделе „Промокод“».
    Теперь главное — ссылка: её пересылают как есть. Код остаётся ниже, на случай
    если получатель пользуется кабинетом без Telegram.
    """
    import html

    head = "🎁 Подарок оплачен" if paid else "🎁 Подарок готов"
    lines = [f"{head}: <b>{html.escape(plan_name)}</b> на {days} дн.", ""]
    if cert_url:
        lines += [
            "Перешлите получателю ссылку — по ней он активирует подарок в одно нажатие:",
            cert_url,
            "",
            f"Код на всякий случай: <code>{code}</code>",
        ]
    else:
        lines += [f"Код для получателя:\n<code>{code}</code>", "", "Он вводит его в разделе «Промокод»."]
    return "\n".join(lines)


def gift_share_keyboard(cert_url: str, plan_name: str, days: int) -> Any:
    """Кнопки под готовым подарком: «Поделиться» (выбор чата) и «Открыть сертификат».

    None — адрес кабинета не задан: без него делиться нечем, остаётся код в тексте.
    """
    if not cert_url:
        return None
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📤 Поделиться подарком", url=share_url(cert_url, plan_name, days))],
            [InlineKeyboardButton(text="🎁 Открыть сертификат", url=cert_url)],
        ]
    )
