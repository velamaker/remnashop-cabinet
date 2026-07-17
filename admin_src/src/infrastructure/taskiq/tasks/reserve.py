"""Резервный доступ истёкшим подпискам: 1 ГБ на N дней (overlay).

Крон (почасовой): находит USER, у кого подписка истекла недавно (в пределах окна) и
кому резерв ещё не выдавали, через Remnawave SDK делает их ACTIVE на `window_days`
дней с лимитом `reserve_gb` ГБ (сброс использованного) — на текущем скваде или на
отдельном сквад-резерве (squad_uuid). Пишет строку в reserve_grants (дедуп: ОДИН
резерв на юзера). Шлёт goodwill-уведомление (Web Push).

Окончание НЕ требует отдельной логики: резерв ставит expireAt = now + window_days,
который сам истекает в конце окна → панель авто-помечает EXPIRED → «подписка
закончилась». (Панель ЗАПРЕЩАЕТ ставить expireAt в прошлое, так что ручное истечение
и невозможно, и не нужно.) Продлился раньше — покупка перезапишет срок/лимит/сквад.

Надпись приходит сама из customRemarks панели (уже по-русски): израсходовал 1 ГБ →
LIMITED «кончился трафик»; окно вышло → EXPIRED «подписка закончилась».

Конфиг assets/reserve.json (админка). Дефолт ВЫКЛ. Ядро/биллинг НЕ трогаем — только
меняем срок/лимит/сквад юзера в панели. Best-effort: ошибка по одному не роняет проход.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

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
        try:
            body = UpdateUserRequestDto(
                uuid=uuid,
                status=UserStatus.ACTIVE,
                expire_at=reserve_expire,
                traffic_limit_bytes=gb * _GB,
            )
            if squad:
                body.active_internal_squads = [squad]
            await sdk.users.update_user(body)
            await sdk.users.reset_user_traffic(str(uuid))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"reserve: выдача user_id={uid} не удалась: {e}")
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
