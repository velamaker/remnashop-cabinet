"""Двойники аккаунтов: один человек, две записи в боте.

ОТКУДА БЕРУТСЯ. Синхрон панель→бот (`SyncAllUsersFromPanel`, раз в 30 минут) при
импорте панельного юзера, у которого ЕЩЁ НЕТ Telegram ID, создаёт безымянную
запись: `telegram_id = NULL`, `name` = uuid панельного юзера. Подписка вешается на
неё. Если Telegram ID проставить в панели ПОЗЖЕ, синхрон найдёт по подписке ту же
безымянную запись и обновит её — к телеграм-аккаунту подписка НЕ переедет. Человек
жмёт /start, получает ВТОРОЙ аккаунт и видит «Нет текущей подписки», хотя в панели
у него всё есть.

ПОЧЕМУ ЭТО НАДО ЛОВИТЬ АВТОМАТИЧЕСКИ. Со стороны владельца поломка невидима: обе
записи выглядят исправными, и узнать о ней можно только от самого человека. Разбор
7 августа 2026 занял ручной SQL, и каждая следующая пара обошлась бы так же.

ЧТО НЕ ЯВЛЯЕТСЯ ДВОЙНИКОМ. Безымянная запись сама по себе — норма: это честный
панельный юзер без телеграма (на боевой базе таких подавляющее большинство). Пара считается
сломанной ТОЛЬКО когда у панельного юзера телеграм ЕСТЬ и на этот телеграм заведена
отдельная запись бота.

ДВА СПОСОБА УЗНАТЬ ПАНЕЛЬНОГО ЮЗЕРА. До перехода на панель 3.x подписка хранит его
`uuid`, и он ищется в `remna_identity_map`. После перехода слой совместимости выдаёт
ОБРАТИМЫЙ синтетический uuid, собранный из числового id, — такого в карте нет и не
должно быть, id вынимается прямо из него. Проверять надо оба пути: на живой базе
часть подписок оказалась как раз синтетическими, и разбор, знающий только карту,
объявил бы их «панельный юзер не найден».
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class BrokenPair:
    """Человек, у которого подписка осталась на безымянной записи."""

    orphan_user_id: int          # запись, на которой висит подписка
    live_user_id: int            # запись, которой человек пользуется
    telegram_id: int
    subscription_id: int
    panel_id: Optional[int]
    expire_at: Optional[str]

    @property
    def key(self) -> str:
        """Ключ для дедупа уведомлений — пара записей, а не момент времени."""
        return f"{self.orphan_user_id}:{self.live_user_id}"


def panel_id_from_remna(value: Any, id_map: dict[str, int]) -> Optional[int]:
    """Числовой id панельного юзера по тому, что лежит в подписке.

    Сначала карта (панель 2.x и мигрированные записи), затем разбор синтетического
    uuid (всё, что появилось после перехода на 3.x). Порядок важен: карта —
    источник правды там, где она есть.
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw in id_map:
        return id_map[raw]
    try:
        from src.infrastructure.services.remnawave_v3 import synthetic_id

        return synthetic_id(UUID(raw))
    except Exception:  # noqa: BLE001 — не uuid и не наш синтетический
        return None


async def _id_map(session: AsyncSession) -> dict[str, int]:
    try:
        rows = (await session.execute(text("SELECT panel_uuid, panel_id FROM remna_identity_map"))).all()
    except Exception as exc:  # noqa: BLE001 — на 2.x таблицы может не быть вовсе
        logger.debug(f"duplicates: карта идентичности недоступна: {exc}")
        return {}
    return {str(u): int(i) for u, i in rows}


async def find_broken_pairs(
    session: AsyncSession, panel_users: list[dict[str, Any]]
) -> list[BrokenPair]:
    """Сломанные пары. `panel_users` — ответ `/api/users` панели.

    Читает только базу; в панель не ходит (её ответ передают снаружи), чтобы этот
    разбор можно было прогнать и из задачи, и из инструмента слияния, и из теста.
    """
    by_panel_id = {
        int(u["id"]): u for u in panel_users if isinstance(u, dict) and u.get("id") is not None
    }
    id_map = await _id_map(session)

    orphans = (
        await session.execute(
            text(
                "SELECT u.id AS uid, s.id AS sid, s.user_remna_id AS remna, "
                "       s.expire_at::text AS expire_at "
                "FROM users u JOIN subscriptions s ON s.user_id = u.id "
                "WHERE u.telegram_id IS NULL AND s.user_remna_id IS NOT NULL"
            )
        )
    ).mappings().all()
    if not orphans:
        return []

    live = {
        int(t): int(i)
        for t, i in (
            await session.execute(
                text("SELECT telegram_id, id FROM users WHERE telegram_id IS NOT NULL")
            )
        ).all()
    }

    pairs: list[BrokenPair] = []
    for row in orphans:
        panel_id = panel_id_from_remna(row["remna"], id_map)
        panel_user = by_panel_id.get(panel_id) if panel_id is not None else None
        if not panel_user:
            continue
        tg = panel_user.get("telegramId")
        if not tg:
            continue  # честный панельный юзер без телеграма — это не двойник
        live_id = live.get(int(tg))
        if live_id is None:
            # Телеграм в панели есть, но человек ещё не нажал /start. Синхрон
            # свяжет записи сам, ломать нечего.
            continue
        pairs.append(
            BrokenPair(
                orphan_user_id=int(row["uid"]),
                live_user_id=live_id,
                telegram_id=int(tg),
                subscription_id=int(row["sid"]),
                panel_id=panel_id,
                expire_at=row["expire_at"],
            )
        )
    return pairs


def describe(pair: BrokenPair) -> str:
    """Человеческое описание пары для уведомления владельцу."""
    return (
        f"👥 <b>Двойник аккаунта</b>\n"
        f"Подписка #{pair.subscription_id} (до {pair.expire_at or '—'}) осталась на "
        f"служебной записи #{pair.orphan_user_id}, а человек пользуется записью "
        f"#{pair.live_user_id} (telegram {pair.telegram_id}) и видит «Нет подписки».\n\n"
        f"Слить: <code>scripts/merge-duplicate.py "
        f"--from {pair.orphan_user_id} --to {pair.live_user_id}</code> "
        f"(сначала без <code>--apply</code> — покажет, что переедет)."
    )
