"""Удаление человека — одна механика для самоудаления и для админки (overlay).

ЗАЧЕМ ОТДЕЛЬНЫЙ МОДУЛЬ. Удалять аккаунт умеют двое: сам человек из кабинета
(POST /account/delete, право на забвение) и админ из карточки пользователя
(DELETE /admin/users/{id}). Две отдельные реализации разъезжаются молча: одна
чистит внешние привязки, другая забывает — и «удалённый» аккаунт остаётся
наполовину живым. Поэтому шаги описаны здесь один раз.

ЧТО ПРОИСХОДИТ, ПО ШАГАМ.

 1. Аккаунт в панели Remnawave удаляется — VPN перестаёт работать. Панель не
    ответила → дальше НЕ идём и ничего не меняем: «полуудалённый» аккаунт с
    живым доступом хуже, чем неудалённый.
 2. Подписки помечаются DELETED. Иначе у несуществующего человека остаётся
    активная подписка — её видят отчёты, ею занимаются кроны.
 3. Вычищаются личные данные: история входов, push-подписки, известные
    устройства, устройства панели, привязки внешних входов, секрет 2FA.
 4. Развилка. Есть ДЕНЕЖНЫЙ след (платежи, пополнения, подарки) — строку
    пользователя не удаляем, а обезличиваем: имя, почта, телеграм, пароль
    стираются, аккаунт блокируется. Так требует и закон (персональные данные
    удалены), и здравый смысл: денежные таблицы ссылаются на пользователя с
    ON DELETE CASCADE, и физическое удаление унесло бы отчётность за год.
    Денежного следа нет (дубль из панели, мусорная регистрация) — строка
    удаляется целиком, каскадом уходит всё остальное.
 5. Сессии отзываются: открытые вкладки перестают работать сразу.

ЧТО ЭТОТ МОДУЛЬ НЕ ДЕЛАЕТ. Не решает, кому можно удалять (это дело эндпоинта:
себя удалять нельзя, владельца — нельзя) и не коммитит за вызывающего, кроме
отзыва сессий, который коммитит сам (так устроен overlay_sessions).
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.services.overlay_sessions import invalidate_all


# Слово, которое просят напечатать перед удалением, — одно и то же и в кабинете
# (человек удаляет себя), и в админке (админ удаляет чужой аккаунт). Английский
# вариант принимаем тоже: админка теперь и на английском, а набрать кириллицу на
# английской раскладке нечем.
CONFIRM_PHRASE = "УДАЛИТЬ"
CONFIRM_PHRASES = (CONFIRM_PHRASE, "DELETE")


def confirm_matches(value: Optional[str]) -> bool:
    """Подошло ли введённое слово подтверждения (регистр и пробелы не важны)."""
    return (value or "").strip().upper() in CONFIRM_PHRASES


class PanelUnavailable(RuntimeError):
    """Панель не отдала аккаунт — удаление прерываем, ничего не меняя."""


# Личные данные: вычищаются в обоих случаях, и при обезличивании тоже.
# `user_oauth_providers` здесь не для порядка: там лежит внешняя личность
# (кто этот человек у Google/Telegram), и она обязана исчезнуть вместе с ним.
_PERSONAL_TABLES = (
    "login_events",
    "push_subscriptions",
    "known_devices",
    "hwid_devices",
    "user_oauth_providers",
    "admin_2fa",
    "user_notifications",
)

# Таблицы, наличие строки в которых означает «за человеком есть деньги».
_MONEY_TABLES = ("transactions", "balance_topups", "gift_payments")

_ANONYMIZE_SQL = text(
    "UPDATE users SET "
    "email = NULL, pending_email = NULL, password_hash = NULL, "
    "email_verification_code_hash = NULL, email_verification_expires_at = NULL, "
    "is_email_verified = false, username = NULL, name = 'Удалённый аккаунт', "
    "telegram_id = NULL, referral_code = 'deleted_' || id::text, auth_type = 'deleted', "
    "is_blocked = true, cabinet_balance = 0, points = 0, autopay_enabled = false, "
    "current_subscription_id = NULL, updated_at = now() "
    "WHERE id = :u"
)


async def _quiet(session: AsyncSession, sql: str, params: dict[str, Any]) -> None:
    """Выполняет запрос, прощая отсутствие таблицы.

    Часть таблиц — наши, overlay-овские, и на свежей установке их может ещё не
    быть. Ронять из-за этого удаление аккаунта нельзя. Ошибка гасится в САВЕПОЙНТЕ:
    иначе упавший запрос переводит транзакцию в состояние «только откат», и
    следующий шаг падает уже без объяснений.
    """
    try:
        async with session.begin_nested():
            await session.execute(text(sql), params)
    except Exception:  # noqa: BLE001 — таблицы может не быть, это не повод падать
        pass


async def panel_uuids(session: AsyncSession, user_id: int) -> list[str]:
    """Аккаунты панели, заведённые под этого человека (по всем его подпискам)."""
    rows = (
        await session.execute(
            text(
                "SELECT DISTINCT user_remna_id::text FROM subscriptions WHERE user_id = :u"
            ),
            {"u": user_id},
        )
    ).all()
    return [r[0] for r in rows if r[0]]


async def revoke_panel_access(remnawave: Any, uuids: list[str]) -> int:
    """Удаляет аккаунты в панели. Возвращает, сколько там реально было.

    «Его там уже нет» — это успех (панель отвечает False), а вот ошибка связи —
    повод прекратить удаление: без снятия доступа в панели VPN продолжит
    работать у «удалённого» человека.
    """
    removed = 0
    for raw in uuids:
        try:
            gone = await remnawave.delete_user(UUID(raw))
        except Exception as exc:  # noqa: BLE001
            raise PanelUnavailable(str(exc)) from exc
        if gone:
            removed += 1
    return removed


async def has_money_trace(session: AsyncSession, user_id: int) -> bool:
    """Есть ли за человеком денежные записи — от этого зависит способ удаления."""
    for table in _MONEY_TABLES:
        try:
            async with session.begin_nested():
                found = (
                    await session.execute(
                        text(f"SELECT 1 FROM {table} WHERE user_id = :u LIMIT 1"),
                        {"u": user_id},
                    )
                ).first()
        except Exception:  # noqa: BLE001 — нет таблицы → нет и следа в ней
            continue
        if found:
            return True
    return False


async def scrub_personal_rows(
    session: AsyncSession,
    user_id: int,
    telegram_id: Optional[int],
    email: Optional[str],
) -> None:
    """Вычищает личные данные человека из вспомогательных таблиц.

    Отдельно — карта панельных личностей (`remna_identity_map`): саму строку
    сносить нельзя, по ней слой совместимости с панелью 3.x переводит uuid в
    числовой id, зато телеграм и почту из неё убираем.
    """
    for table in _PERSONAL_TABLES:
        await _quiet(session, f"DELETE FROM {table} WHERE user_id = :u", {"u": user_id})

    if telegram_id is not None:
        await _quiet(
            session,
            "UPDATE remna_identity_map SET telegram_id = NULL WHERE telegram_id = :t",
            {"t": telegram_id},
        )
    if email:
        await _quiet(
            session,
            "UPDATE remna_identity_map SET email = NULL WHERE lower(email) = lower(:e)",
            {"e": email},
        )


async def purge_user(
    session: AsyncSession,
    remnawave: Any,
    user_id: int,
) -> dict[str, Any]:
    """Удаляет человека целиком. Возвращает отчёт: как именно и что снято.

    Ключ `mode`: "purged" — строка удалена физически, "anonymized" — оставлена
    обезличенной ради денежной отчётности. Вызывающий обязан сам решить, что
    этому админу можно удалять этого человека.
    """
    who = (
        await session.execute(
            text("SELECT telegram_id, email FROM users WHERE id = :u"), {"u": user_id}
        )
    ).first()
    telegram_id = int(who[0]) if who and who[0] is not None else None
    email = who[1] if who else None

    # 1) Панель. Делаем ДО всего остального: сорвалась связь — ничего не меняли.
    removed_in_panel = await revoke_panel_access(
        remnawave, await panel_uuids(session, user_id)
    )

    keep_row = await has_money_trace(session, user_id)

    # 2) Подписки — в DELETED, чтобы не висели активными у несуществующего человека.
    await _quiet(
        session,
        "UPDATE subscriptions SET status = 'DELETED', updated_at = now() "
        "WHERE user_id = :u AND status <> 'DELETED'",
        {"u": user_id},
    )

    # 3) Личные данные.
    await scrub_personal_rows(session, user_id, telegram_id, email)

    # 4) Сама запись — и отзыв сессий там, где ему есть куда записаться.
    #    У физически удалённого пользователя отзывать нечего: строки нет, и
    #    любой его токен перестаёт проходить проверку сам собой.
    if keep_row:
        await session.execute(_ANONYMIZE_SQL, {"u": user_id})
        await invalidate_all(session, user_id)
    else:
        await session.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})

    return {
        "mode": "anonymized" if keep_row else "purged",
        "panel_accounts_removed": removed_in_panel,
    }
