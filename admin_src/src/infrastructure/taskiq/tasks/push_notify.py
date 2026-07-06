"""Web Push: напоминание «подписка заканчивается» на устройства PWA.

Самодостаточно (базовый образ шлёт свои уведомления через Telegram/email — в него
не влезаем). За N дней до конца активной подписки шлёт push тем, у кого есть
push-подписки. Дедуп по assets/push_notify_state.json (user_id → expire_at, для
которого уже уведомляли) — чтобы не слать каждый запуск крона.

Тумблеры env: PUSH_EXPIRING_ENABLED (on), PUSH_EXPIRING_DAYS (3). Cron раз в 6ч.
Авто-обнаруживается taskiq по глобу tasks/*.py.
"""

import json
import os
from datetime import datetime
from pathlib import Path

from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.services.overlay_push import send_to_user
from src.infrastructure.taskiq.broker import broker

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
STATE_PATH = ASSETS_DIR / "push_notify_state.json"

# Минимальная локализация (RU/EN); прочие языки → RU-фолбэк.
_MSG = {
    "ru": ("⏳ Подписка заканчивается", "Ваша подписка истекает через {days} дн. Продлите, чтобы не остаться без доступа."),
    "en": ("⏳ Subscription ending", "Your subscription expires in {days} day(s). Renew to stay connected."),
}


def _enabled() -> bool:
    return (os.environ.get("PUSH_EXPIRING_ENABLED") or "true").strip().lower() == "true"


def _days() -> int:
    try:
        return max(1, int(os.environ.get("PUSH_EXPIRING_DAYS") or "3"))
    except ValueError:
        return 3


def _load_state() -> dict:
    try:
        if STATE_PATH.exists():
            with STATE_PATH.open(encoding="utf-8") as fh:
                return json.load(fh)
    except Exception:
        pass
    return {}


def _save_state(state: dict) -> None:
    try:
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        with STATE_PATH.open("w", encoding="utf-8") as fh:
            json.dump(state, fh)
    except Exception as exc:
        logger.warning(f"push_notify: не сохранил стейт: {exc}")


@broker.task(schedule=[{"cron": "0 */6 * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def run_push_expiring(session: FromDishka[AsyncSession]) -> None:
    if not _enabled():
        return

    n = _days()
    rows = (
        await session.execute(
            text(
                "SELECT u.id, lower(u.language::text), s.expire_at "
                "FROM users u "
                "JOIN subscriptions s ON u.current_subscription_id = s.id "
                "JOIN push_subscriptions p ON p.user_id = u.id "
                "WHERE s.status = 'ACTIVE' "
                "AND s.expire_at >= now() "
                "AND s.expire_at < now() + make_interval(days => :n) "
                "GROUP BY u.id, u.language, s.expire_at"
            ),
            {"n": n},
        )
    ).all()
    if not rows:
        return

    state = _load_state()
    live_keys: set[str] = set()
    changed = False
    sent = 0

    for uid, lang, expire_at in rows:
        key = str(uid)
        live_keys.add(key)
        exp_iso = expire_at.isoformat() if expire_at else ""
        if state.get(key) == exp_iso:
            continue  # для этого срока уже уведомляли

        days_left = (
            max(1, (expire_at - datetime.now(expire_at.tzinfo)).days)
            if expire_at
            else n
        )
        title, body_tpl = _MSG.get((lang or "ru")[:2], _MSG["ru"])
        payload = {
            "title": title,
            "body": body_tpl.format(days=days_left),
            "url": "/billing",
            "tag": "expiring",
        }
        try:
            ok = await send_to_user(session, uid, payload)
            if ok:
                sent += 1
                state[key] = exp_iso
                changed = True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"push_notify: user_id={uid} не удалось: {e}")

    await session.commit()

    # Подчищаем стейт от юзеров вне текущего окна (чтобы файл не рос бесконечно).
    stale = [k for k in state if k not in live_keys]
    if stale:
        for k in stale:
            state.pop(k, None)
        changed = True
    if changed:
        _save_state(state)
    if sent:
        logger.info(f"push_notify: отправлено напоминаний об истечении: {sent}")
