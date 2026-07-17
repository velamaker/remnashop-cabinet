"""Авто-возобновление замороженных подписок по достижении max_days (overlay).

Крон (почасовой): паузы старше max_days авто-возобновляет — enable_user + expire =
now + сохранённый остаток. Так пауза не может длиться вечно. Работает даже при
выключенной фиче (докрутить открытые паузы). Уведомляет юзера (Web Push).
Ручные пауза/возобновление — public/freeze.py. Конфиг assets/freeze.json.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Remnawave
from src.infrastructure.services.overlay_freeze import load_config
from src.infrastructure.services.overlay_push import notify_user_push
from src.infrastructure.taskiq.broker import broker

_MSG = {
    "ru": ("▶️ Подписка возобновлена", "Пауза достигла лимита — подписка автоматически возобновлена."),
    "en": ("▶️ Subscription resumed", "Pause reached the limit — your subscription was auto-resumed."),
}


@broker.task(schedule=[{"cron": "52 * * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def run_freeze_autoresume(
    session: FromDishka[AsyncSession],
    remnawave: FromDishka[Remnawave],
) -> None:
    max_days = load_config()["max_days"]
    sdk = getattr(remnawave, "sdk", None)
    if sdk is None:
        return

    rows = (
        await session.execute(
            text(
                "SELECT f.user_id, f.remna_uuid, f.remaining_seconds, lower(u.language::text) "
                "FROM subscription_freezes f JOIN users u ON u.id = f.user_id "
                "WHERE f.active = true AND f.frozen_at < now() - make_interval(days => :d)"
            ),
            {"d": max_days},
        )
    ).all()
    if not rows:
        return

    from remnapy.models import UpdateUserRequestDto

    resumed = 0
    for uid, remna_uuid, remaining, lang in rows:
        new_expire = datetime.now(timezone.utc) + timedelta(seconds=int(remaining))
        try:
            await sdk.users.update_user(UpdateUserRequestDto(uuid=str(remna_uuid), expire_at=new_expire))
            await sdk.users.enable_user(str(remna_uuid))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"freeze: авто-возобновление user_id={uid} не удалось: {e}")
            continue
        await session.execute(
            text("UPDATE subscription_freezes SET active = false WHERE user_id = :u"), {"u": uid}
        )
        await session.commit()
        resumed += 1
        await notify_user_push(
            session, SimpleNamespace(id=uid, language=lang), _MSG, url="/", tag="freeze-resume"
        )

    if resumed:
        logger.info(f"freeze: авто-возобновлено подписок {resumed} (лимит {max_days} дн.)")
