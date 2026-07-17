"""Уведомление «новое устройство подключилось» — overlay.

Крон (каждые 2ч): снимает все HWID-устройства панели (GET /hwid/devices), мапит на
наших USER. Первый прогон = ГЛОБАЛЬНЫЙ baseline (все текущие устройства заносятся в
known_devices без уведомлений — чтобы не заспамить на раскатке). Дальше: любой HWID,
которого нет в known_devices, считается НОВЫМ → шлём юзеру Telegram + Web Push и
заносим в known_devices (дедуп — повторно не шлём).

Конфиг assets/new_device.json (админка). Дефолт ВЫКЛ. Данные — из Remnawave (клиент
как node_health). Best-effort.
"""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from aiogram import Bot
from dishka.integrations.taskiq import FromDishka, inject
from httpx import AsyncClient, Timeout
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import AppConfig
from src.infrastructure.services.overlay_new_device import load_config
from src.infrastructure.services.overlay_push import notify_user_push
from src.infrastructure.taskiq.broker import broker

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
STATE_PATH = ASSETS_DIR / "new_device_state.json"
_PAGE = 1000

_MSG = {
    "ru": (
        "🔔 Новое устройство",
        "К вашей подписке подключилось новое устройство{model}. "
        "Если это не вы — смените ссылку подписки в кабинете.",
    ),
    "en": (
        "🔔 New device",
        "A new device{model} connected to your subscription. "
        "If it wasn't you, reissue your subscription link in the cabinet.",
    ),
}


def _baselined() -> bool:
    try:
        return bool(json.loads(STATE_PATH.read_text("utf-8")).get("baselined"))
    except Exception:  # noqa: BLE001
        return False


def _set_baselined() -> None:
    try:
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps({"baselined": True}), "utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"new_device: не сохранил стейт: {e}")


async def _fetch_devices(config: AppConfig) -> list[dict[str, Any]]:
    c = config.remnawave
    headers = {
        "Authorization": f"Bearer {c.token.get_secret_value()}",
        "X-Api-Key": c.caddy_token.get_secret_value(),
        "CF-Access-Client-Id": c.cf_client_id.get_secret_value(),
        "CF-Access-Client-Secret": c.cf_client_secret.get_secret_value(),
    }
    if not c.is_external:
        headers["x-forwarded-proto"] = "https"
        headers["x-forwarded-for"] = "127.0.0.1"
    devices: list[dict[str, Any]] = []
    async with AsyncClient(
        base_url=f"{c.url.get_secret_value()}/api",
        headers=headers, cookies=c.cookies, verify=True,
        timeout=Timeout(connect=15, read=30, write=10, pool=5),
    ) as cl:
        start = 0
        while True:
            r = await cl.get("/hwid/devices", params={"size": _PAGE, "start": start})
            if r.status_code != 200:
                logger.warning(f"new_device: /hwid/devices вернул {r.status_code}")
                break
            resp = r.json().get("response", {}) or {}
            batch = resp.get("devices", []) or []
            if not batch:
                break
            devices.extend(batch)
            total = int(resp.get("total", 0) or 0)
            start += len(batch)
            if start >= total or len(batch) < _PAGE:
                break
    return devices


@broker.task(schedule=[{"cron": "17 */2 * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def run_new_device(
    session: FromDishka[AsyncSession],
    config: FromDishka[AppConfig],
) -> None:
    cfg = load_config()
    if not cfg["enabled"]:
        return

    try:
        devices = await _fetch_devices(config)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"new_device: не получил устройства: {e}")
        return
    if not devices:
        return

    # remna-uuid → (user_id, lang, telegram_id) для наших USER.
    rows = (
        await session.execute(
            text(
                "SELECT DISTINCT s.user_remna_id, u.id, lower(u.language::text), u.telegram_id "
                "FROM subscriptions s JOIN users u ON u.id = s.user_id "
                "WHERE u.role = 'USER' AND s.user_remna_id IS NOT NULL"
            )
        )
    ).all()
    mine = {str(ru): (uid, lang, tg) for ru, uid, lang, tg in rows}

    # Текущие (user_id, hwid[, model]) для наших юзеров.
    current: dict[tuple[int, str], str] = {}
    meta: dict[int, tuple[str, int | None]] = {}  # user_id → (lang, tg)
    for d in devices:
        t = mine.get(str(d.get("userUuid") or ""))
        hwid = (d.get("hwid") or "").strip()
        if t is None or not hwid:
            continue
        uid, lang, tg = t
        current[(uid, hwid[:256])] = (d.get("deviceModel") or "").strip()
        meta[uid] = (lang, tg)

    # baseline: первый прогон — заносим всё без уведомлений.
    if not _baselined():
        for (uid, hwid) in current:
            await session.execute(
                text("INSERT INTO known_devices (user_id, hwid) VALUES (:u, :h) ON CONFLICT DO NOTHING"),
                {"u": uid, "h": hwid},
            )
        await session.commit()
        _set_baselined()
        logger.info(f"new_device: baseline — занесено {len(current)} устройств, уведомления со след. прохода")
        return

    known = {
        (uid, hwid)
        for uid, hwid in (
            await session.execute(text("SELECT user_id, hwid FROM known_devices"))
        ).all()
    }

    new_pairs = [(uid, hwid, model) for (uid, hwid), model in current.items() if (uid, hwid) not in known]
    if not new_pairs:
        return

    bot: Bot | None = None
    try:
        bot = Bot(config.bot.token.get_secret_value())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"new_device: не смог создать Bot ({e}) — TG пропущены")

    sent = 0
    for uid, hwid, model in new_pairs:
        await session.execute(
            text("INSERT INTO known_devices (user_id, hwid) VALUES (:u, :h) ON CONFLICT DO NOTHING"),
            {"u": uid, "h": hwid},
        )
        await session.commit()
        lang, tg_id = meta.get(uid, ("ru", None))
        l = (lang or "ru")[:2]
        model_txt = f" ({model})" if model else ""
        await notify_user_push(
            session, SimpleNamespace(id=uid, language=lang),
            _MSG, url="/devices", tag="new-device", model=model_txt,
        )
        if bot is not None and tg_id:
            try:
                title, body_tpl = _MSG.get(l, _MSG["ru"])
                await bot.send_message(int(tg_id), f"<b>{title}</b>\n\n{body_tpl.format(model=model_txt)}")
            except Exception as e:  # noqa: BLE001
                logger.debug(f"new_device: TG user_id={uid} не доставлено: {e}")
        sent += 1

    if bot is not None:
        try:
            await bot.session.close()
        except Exception:  # noqa: BLE001
            pass
    if sent:
        logger.info(f"new_device: уведомлено о новых устройствах {sent}")
