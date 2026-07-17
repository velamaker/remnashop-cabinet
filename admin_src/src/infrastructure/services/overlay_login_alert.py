"""Алерт пользователю о новом входе (email/TG/Push) — overlay.

При входе с НОВОГО IP (которого не было в login_events юзера, и это не первый вход)
шлём уведомление. Детект и вызов — в login-tracking middleware (overlay_app), СРАЗУ
после записи события. Конфиг assets/login_alert.json, правится в админке. Дефолт ВЫКЛ.

Грабля (см. [[login-ip-is-tunnel-exit]]): под нашим VPN source-IP входа = exit-IP ноды
и меняется при смене сервера → ложные алерты. Поэтому node-IP (из node_health.json +
ручного abuse_ignore_ips.json) НЕ считаем «новым IP» — по ним не алертим.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
CONFIG_PATH = ASSETS_DIR / "login_alert.json"
_NODE_HEALTH_PATH = ASSETS_DIR / "node_health.json"
_IGNORE_IPS_PATH = ASSETS_DIR / "abuse_ignore_ips.json"

DEFAULT_CONFIG: dict[str, Any] = {"enabled": False}

_MSG = {
    "ru": ("🔐 Новый вход в аккаунт", "Вход с нового IP: {ip} ({device}). Если это были не вы — смените пароль в кабинете."),
    "en": ("🔐 New sign-in", "Sign-in from a new IP: {ip} ({device}). If it wasn't you, change your password in the cabinet."),
}


def _short_device(ua: str | None) -> str:
    """Грубый тип устройства из User-Agent (как в кабинете SessionsCard)."""
    if not ua:
        return "неизвестное устройство"
    for needle, label in (
        ("Happ", "Happ"), ("iPhone", "iOS"), ("iPad", "iOS"), ("iOS", "iOS"),
        ("Android", "Android"), ("Windows", "Windows"), ("Mac OS", "macOS"),
        ("Macintosh", "macOS"), ("Chrome", "Chrome"), ("Firefox", "Firefox"), ("Safari", "Safari"),
    ):
        if needle.lower() in ua.lower():
            return label
    return ua[:40]


def _excluded_ips() -> set[str]:
    """IP наших нод + ручной список — их не считаем «новым IP» (VPN-логин)."""
    ips: set[str] = set()
    try:
        data = json.loads(_NODE_HEALTH_PATH.read_text(encoding="utf-8"))
        for v in data.values():
            if isinstance(v, dict) and isinstance(v.get("ip"), str) and v["ip"].strip():
                ips.add(v["ip"].strip())
    except Exception:  # noqa: BLE001
        pass
    try:
        manual = json.loads(_IGNORE_IPS_PATH.read_text(encoding="utf-8"))
        if isinstance(manual, list):
            ips.update(str(x).strip() for x in manual if str(x).strip())
    except Exception:  # noqa: BLE001
        pass
    return ips


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    return {"enabled": bool(data.get("enabled", False))}


def load_config() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text("utf-8"))
    except FileNotFoundError:
        return dict(DEFAULT_CONFIG)
    except Exception:  # noqa: BLE001
        return dict(DEFAULT_CONFIG)
    return _normalize(data) if isinstance(data, dict) else dict(DEFAULT_CONFIG)


def save_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize(config)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), "utf-8")
    return normalized


async def maybe_alert_new_login(session: AsyncSession, user_id: int, ip: str, user_agent: str) -> None:
    """Best-effort: если вход с нового IP (не первый, не node-IP) — шлёт Push+TG+Email.

    Вызывать СРАЗУ ПОСЛЕ записи login_events (текущий вход уже в таблице). Никогда
    не бросает наружу — трекинг логина не должен ронять запрос.
    """
    try:
        if not load_config()["enabled"] or not ip:
            return
        if ip in _excluded_ips():  # VPN-логин через нашу ноду — не считаем новым
            return

        counts = (
            await session.execute(
                text(
                    "SELECT (SELECT count(*) FROM login_events WHERE user_id = :u) AS total, "
                    "(SELECT count(*) FROM login_events WHERE user_id = :u AND ip = :ip) AS same_ip"
                ),
                {"u": user_id, "ip": ip},
            )
        ).first()
        if not counts:
            return
        total, same_ip = int(counts[0]), int(counts[1])
        # total<2 — первый вход (baseline, без алерта); same_ip>1 — этот IP уже был
        if total < 2 or same_ip > 1:
            return

        row = (
            await session.execute(
                text("SELECT lower(language::text), telegram_id, email FROM users WHERE id = :u"),
                {"u": user_id},
            )
        ).first()
        if not row:
            return
        lang, tg_id, email = row
        device = _short_device(user_agent)
        title, body = _MSG.get((lang or "ru")[:2], _MSG["ru"])
        body_text = body.format(ip=ip, device=device)
    except Exception:  # noqa: BLE001
        return

    # Web Push
    try:
        from src.infrastructure.services.overlay_push import notify_user_push

        await notify_user_push(
            session, SimpleNamespace(id=user_id, language=lang), _MSG,
            url="/settings", tag="new-login", ip=ip, device=device,
        )
    except Exception:  # noqa: BLE001
        pass

    # Telegram
    if tg_id:
        try:
            from aiogram import Bot

            from src.core.config import AppConfig

            bot = Bot(AppConfig.get().bot.token.get_secret_value())
            await bot.send_message(int(tg_id), f"<b>{title}</b>\n\n{body_text}")
            await bot.session.close()
        except Exception as e:  # noqa: BLE001
            logger.debug(f"login_alert: TG user_id={user_id} не доставлено: {e}")

    # Email
    if email:
        try:
            from src.core.config import AppConfig
            from src.infrastructure.services.email_sender import SmtpEmailSender

            sender = SmtpEmailSender(AppConfig.get())
            if sender.is_enabled():
                await sender.send(to=email, subject=title, body=body_text)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"login_alert: email user_id={user_id} не доставлено: {e}")
