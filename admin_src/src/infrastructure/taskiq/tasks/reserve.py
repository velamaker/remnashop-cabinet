"""Резервный доступ истёкшим подпискам: 1 ГБ на N дней (overlay).

Крон (почасовой): находит USER, у кого подписка истекла недавно (в пределах окна) и
кому резерв ещё не выдавали, через Remnawave SDK делает их ACTIVE на `window_days`
дней с лимитом `reserve_gb` ГБ (сброс использованного) — на текущем скваде или на
отдельном сквад-резерве (squad_uuid). Пишет строку в reserve_grants (дедуп: ОДИН
резерв на юзера). Шлёт goodwill-уведомление (Web Push).

СКВАДЫ — почему им отдельная забота. Панель меняет сквады юзера ТОЛЬКО если в PATCH
пришло поле activeInternalSquads (users.service: `if (newActiveInternalSquadsUuids)`);
при пустом squad_uuid мы его не шлём вовсе. А подписка отдаёт клиенту серверы именно
через сквады: юзер без сквадов — ACTIVE, со сроком и лимитом, но в приложении ПУСТО.
Поэтому при пустом squad_uuid сначала смотрим, что у человека в панели, и если сквадов
нет — возвращаем те, что были у его подписки (subscriptions.internal_squads). Совсем
нечего вернуть — резерв НЕ выдаём и говорим об этом в логе: «активен, но подключиться
некуда» хуже честного отказа, потому что выглядит как рабочий резерв.

ПОРЯДОК ВЫЗОВОВ. Сброс трафика идёт ПЕРВЫМ, PATCH — вторым. Если между ними что-то
падает, человек остаётся просто истёкшим (безобидно). В обратном порядке сорвавшийся
сброс оставлял бы его ACTIVE с лимитом 1 ГБ поверх израсходованных десятков — панель
пометит LIMITED, и получится ровно та же пустая подписка, но уже незаметно для нас.

РЕЗУЛЬТАТ ПРОВЕРЯЕМ. update_user возвращает юзера целиком (статус, сквады) — сверяем
ответ панели и пишем в reserve_grants только то, что реально применилось. Иначе админка
показывает «резерв выдан» там, где у клиента ничего не работает.

Окончание НЕ требует отдельной логики: резерв ставит expireAt = now + window_days,
который сам истекает в конце окна → панель авто-помечает EXPIRED → «подписка
закончилась». (Панель ЗАПРЕЩАЕТ ставить expireAt в прошлое, так что ручное истечение
и невозможно, и не нужно.) Продлился раньше — покупка перезапишет срок/лимит/сквад.

Надпись приходит сама из customRemarks панели (уже по-русски): израсходовал 1 ГБ →
LIMITED «кончился трафик»; окно вышло → EXPIRED «подписка закончилась».

Конфиг assets/reserve.json (админка). Дефолт ВЫКЛ. Ядро/биллинг НЕ трогаем — только
меняем срок/лимит/сквад юзера в панели. Best-effort: ошибка по одному не роняет проход.
"""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Optional

from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Remnawave
from src.infrastructure.services.overlay_push import notify_user_push
from src.infrastructure.services.overlay_reserve import load_config
from src.infrastructure.taskiq.broker import broker

_GB = 1024 ** 3

_MSG = {
    "ru": (
        "🛟 Резервный доступ включён",
        "Подписка закончилась. Мы оставили резервный доступ {gb} ГБ на {days} дн. — "
        "продлите в кабинете, чтобы не потерять сервис.",
    ),
    "en": (
        "🛟 Reserve access granted",
        "Your subscription ended. We left you {gb} GB reserve for {days} days — "
        "renew in the cabinet to keep your service.",
    ),
}


def _squad_uuids(user: object) -> list[str]:
    """UUID активных сквадов из ответа панели (элементы — объекты {uuid, name})."""
    out: list[str] = []
    for squad in getattr(user, "active_internal_squads", None) or []:
        uuid = getattr(squad, "uuid", None)
        if uuid is None and isinstance(squad, dict):
            uuid = squad.get("uuid")
        if uuid:
            out.append(str(uuid))
    return out


async def _plan_squads(session: AsyncSession, user_id: int) -> list[str]:
    """Сквады подписки человека — чтобы вернуть его на ЕГО ЖЕ серверы, а не в никуда.

    Колонка есть не во всех версиях базового образа, поэтому запрос защищённый: нет
    колонки — просто нет фолбэка, проход от этого не падает. Ошибка ломает транзакцию
    сессии, отсюда rollback.
    """
    try:
        raw = (
            await session.execute(
                text(
                    "SELECT s.internal_squads FROM users u "
                    "JOIN subscriptions s ON u.current_subscription_id = s.id "
                    "WHERE u.id = :u"
                ),
                {"u": user_id},
            )
        ).scalar()
    except Exception as exc:  # noqa: BLE001 — фолбэк опциональный
        await session.rollback()
        logger.debug(f"reserve: сквады подписки недоступны ({exc})")
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    return [str(x) for x in (raw or []) if x]


def _not_applied(user: object) -> Optional[str]:
    """None — резерв реально применился; иначе причина, по которой он бесполезен.

    Формат ответа SDK неизвестен (база может обновиться) → отсутствие поля статуса
    считаем «проверить не смогли» и грант не блокируем: молча ломать рабочую установку
    хуже, чем не поймать один сбой.
    """
    status = getattr(user, "status", None)
    if status is None:
        return None
    if str(getattr(status, "value", status)) != "ACTIVE":
        return f"панель оставила статус {status}"
    if not _squad_uuids(user):
        return "у юзера нет активных сквадов — подписка отдаст пустой список серверов"
    return None


@broker.task(schedule=[{"cron": "27 * * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def run_reserve(
    session: FromDishka[AsyncSession],
    remnawave: FromDishka[Remnawave],
) -> None:
    cfg = load_config()
    if not cfg["enabled"]:
        return

    sdk = getattr(remnawave, "sdk", None)
    if sdk is None:
        logger.warning("reserve: Remnawave SDK недоступен — пропуск")
        return

    from remnapy.enums.users import UserStatus
    from remnapy.models import UpdateUserRequestDto

    gb = cfg["reserve_gb"]
    window = cfg["window_days"]
    squad = cfg["squad_uuid"]

    rows = (
        await session.execute(
            text(
                "SELECT u.id, s.user_remna_id, lower(u.language::text) "
                "FROM users u "
                "JOIN subscriptions s ON u.current_subscription_id = s.id "
                "LEFT JOIN reserve_grants r ON r.user_id = u.id "
                "WHERE u.role = 'USER' AND s.user_remna_id IS NOT NULL "
                "AND s.expire_at < now() "
                "AND s.expire_at > now() - make_interval(days => :w) "
                "AND r.user_id IS NULL"
            ),
            {"w": window},
        )
    ).all()
    if not rows:
        return

    reserve_expire = datetime.now(timezone.utc) + timedelta(days=window)
    granted = 0
    for uid, uuid, lang in rows:
        uuid_s = str(uuid)
        try:
            # Сброс расхода — ПЕРВЫМ шагом: если следующий сорвётся, человек останется
            # просто истёкшим, а не «активным с лимитом 1 ГБ поверх израсходованных».
            await sdk.users.reset_user_traffic(uuid_s)

            # Сквад-резерв задан — переносим на него. Не задан — панель сквады не
            # тронет, и это правильно ровно до тех пор, пока сквады у человека ЕСТЬ.
            squads = [squad] if squad else []
            if not squads and not _squad_uuids(await sdk.users.get_user_by_uuid(uuid_s)):
                squads = await _plan_squads(session, uid)
                if not squads:
                    logger.warning(
                        f"reserve: user_id={uid} ({uuid_s}) пропущен — у юзера нет активных "
                        "сквадов, а сквад-резерв в настройках не задан. Резерв дал бы "
                        "«активную» подписку без единого сервера (в приложении пусто)"
                    )
                    continue

            body = UpdateUserRequestDto(
                uuid=uuid,
                status=UserStatus.ACTIVE,
                expire_at=reserve_expire,
                traffic_limit_bytes=gb * _GB,
            )
            if squads:
                body.active_internal_squads = squads
            applied = await sdk.users.update_user(body)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"reserve: выдача user_id={uid} ({uuid_s}) не удалась: {e}")
            continue

        # Панель ответила юзером целиком — сверяем, что получилось именно то, что нужно.
        # Не сошлось — строку не пишем: следующий проход попробует снова, а владелец
        # видит причину в логе, а не «резерв выдан» при неработающем доступе.
        problem = _not_applied(applied)
        if problem:
            logger.warning(f"reserve: user_id={uid} ({uuid_s}) — резерв не применился: {problem}")
            continue

        await session.execute(
            text(
                "INSERT INTO reserve_grants (user_id, remna_uuid, granted_at, reserve_expire_at, ended) "
                "VALUES (:u, :ru, now(), :re, false) ON CONFLICT (user_id) DO NOTHING"
            ),
            {"u": uid, "ru": str(uuid), "re": reserve_expire},
        )
        await session.commit()
        granted += 1
        await notify_user_push(
            session,
            SimpleNamespace(id=uid, language=lang),
            _MSG,
            url="/billing",
            tag="reserve",
            gb=gb,
            days=window,
        )

    if granted:
        logger.info(f"reserve: выдано резервов {granted} (по {gb} ГБ / {window} дн.)")
