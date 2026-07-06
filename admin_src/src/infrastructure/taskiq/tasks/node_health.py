"""Мониторинг здоровья нод — алерты про то, что webhook Remnawave НЕ ловит.

Webhook панели уже шлёт CONNECTION_LOST/RESTORED (нода отвалилась целиком, см.
application/services/remnawave.py:handle_node_event). Здесь ловим случаи, когда
нода «на связи», но по факту сломана — их панель событием не сигналит:

  • xray упал при живой ноде  (isConnected=true, но xrayUptime=0);
  • сменился IP ноды          (DNS-запись изменилась) — как в инциденте с Польшей
                               3-4 июля: сервер переехал, DNS смотрел в старый IP;
  • сертификат ноды           истекает через ≤ NODE_CERT_WARN_DAYS дней.

Алерт уходит админам штатным notify_admins (raw-message). Дедуп по состоянию в
assets/node_health.json — на каждый прогон не спамим, шлём только смену состояния.

Auto-discover taskiq по глобу tasks/*.py. Крутится раз в 10 минут.

Выключатель: env NODE_HEALTH_ALERTS (по умолчанию on). Порог серта: NODE_CERT_WARN_DAYS.
"""

import asyncio
import json
import os
import socket
import ssl
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dishka.integrations.taskiq import FromDishka, inject
from httpx import AsyncClient, Timeout
from loguru import logger

from src.application.common import Notifier
from src.application.dto import MessagePayloadDto
from src.core.config import AppConfig
from src.core.enums import Role
from src.infrastructure.taskiq.broker import broker

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
STATE_PATH = ASSETS_DIR / "node_health.json"


def _enabled() -> bool:
    return (os.environ.get("NODE_HEALTH_ALERTS") or "true").strip().lower() in (
        "1", "true", "yes", "on", "да",
    )


def _cert_warn_days() -> int:
    try:
        return int(os.environ.get("NODE_CERT_WARN_DAYS", "10"))
    except ValueError:
        return 10


def _load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"node_health: не смог сохранить состояние: {e}")


async def _fetch_nodes(config: AppConfig) -> list[dict[str, Any]]:
    """Список нод из API панели — клиент строим как RemnawaveProvider (инлайн)."""
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
    async with AsyncClient(
        base_url=f"{c.url.get_secret_value()}/api",
        headers=headers,
        cookies=c.cookies,
        verify=True,
        timeout=Timeout(connect=15, read=25, write=10, pool=5),
    ) as cl:
        r = await cl.get("/nodes")
        if r.status_code != 200:
            logger.warning(f"node_health: /nodes вернул {r.status_code}")
            return []
        return r.json().get("response", []) or []


def _healthcheck_urls() -> list[str]:
    """URL-ы для health-чека (кабинет, страница подписки) — через env, по умолчанию пусто.

    Пример: NODE_HEALTH_URLS="https://cabinet.example.com,https://sub.example.com/healthz"
    Чистая сборка не хардкодит домены — каждый установщик задаёт свои.
    """
    raw = os.environ.get("NODE_HEALTH_URLS") or ""
    return [u.strip() for u in raw.split(",") if u.strip()]


async def _check_url(url: str) -> Optional[int]:
    """HTTP-код URL-а (None — если недоступен/таймаут)."""
    try:
        async with AsyncClient(timeout=Timeout(10.0), follow_redirects=True) as cl:
            r = await cl.get(url)
            return r.status_code
    except Exception:
        return None


async def _resolve_ip(host: str) -> Optional[str]:
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, None, family=socket.AF_INET)
        return infos[0][4][0] if infos else None
    except Exception:
        return None


def _cert_days_left_sync(host: str, port: int = 443) -> Optional[int]:
    """Сколько дней до истечения серта на host:443 (без проверки — просто читаем)."""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection((host, port), timeout=6) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ss:
                der = ss.getpeercert(binary_form=True)
        if not der:
            return None
        from cryptography import x509

        cert = x509.load_der_x509_certificate(der)
        try:
            not_after = cert.not_valid_after_utc  # cryptography ≥ 42
        except AttributeError:
            not_after = cert.not_valid_after.replace(tzinfo=timezone.utc)
        return (not_after - datetime.now(timezone.utc)).days
    except Exception:
        return None


async def _cert_days_left(host: str) -> Optional[int]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _cert_days_left_sync, host)


@broker.task(schedule=[{"cron": "*/10 * * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def check_node_health(
    config: FromDishka[AppConfig],
    notifier: FromDishka[Notifier],
) -> None:
    if not _enabled():
        return

    try:
        nodes = await _fetch_nodes(config)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"node_health: не смог получить ноды: {e}")
        return
    if not nodes:
        return

    state = _load_state()
    warn_days = _cert_warn_days()
    alerts: list[str] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for n in nodes:
        if not isinstance(n, dict):
            continue
        name = str(n.get("name") or n.get("uuid") or "?")
        address = n.get("address") or ""
        connected = bool(n.get("isConnected"))
        disabled = bool(n.get("isDisabled"))
        xray_uptime = n.get("xrayUptime")
        st = state.setdefault(name, {})

        if disabled:
            continue  # выключенную ноду не мониторим

        # 1) xray лёг при живой ноде
        xray_down = connected and xray_uptime == 0
        if xray_down and not st.get("xray_down"):
            alerts.append(f"⚠️ <b>{name}</b>: нода на связи, но <b>xray не работает</b> (uptime=0). Инбаунды не поднимаются.")
            st["xray_down"] = True
        elif not xray_down and st.get("xray_down"):
            alerts.append(f"✅ <b>{name}</b>: xray снова работает.")
            st["xray_down"] = False

        # 2) смена IP ноды (DNS)
        if address:
            ip = await _resolve_ip(address)
            if ip:
                old_ip = st.get("ip")
                if old_ip and ip != old_ip:
                    alerts.append(f"🔀 <b>{name}</b>: сменился IP <code>{old_ip}</code> → <code>{ip}</code>. Проверьте DNS/серт/доступность.")
                st["ip"] = ip

        # 3) срок сертификата
        if address:
            days = await _cert_days_left(address)
            if days is not None:
                st["cert_days"] = days
                if days <= warn_days and not st.get("cert_warned"):
                    alerts.append(f"📜 <b>{name}</b>: сертификат истекает через <b>{days} дн.</b> — перевыпустите.")
                    st["cert_warned"] = True
                elif days > warn_days and st.get("cert_warned"):
                    st["cert_warned"] = False

        st["checked_at"] = now_iso

    # 4) Health-чек кабинета/страницы подписки (URL-ы из env NODE_HEALTH_URLS).
    web_state = state.setdefault("_web", {})
    for url in _healthcheck_urls():
        code = await _check_url(url)
        bad = code is None or code >= 500
        key = url
        was_bad = web_state.get(key, False)
        if bad and not was_bad:
            shown = "недоступен (таймаут/ошибка)" if code is None else f"HTTP {code}"
            alerts.append(f"🌐 <b>{url}</b>: {shown} — сервис не отвечает.")
            web_state[key] = True
        elif not bad and was_bad:
            alerts.append(f"✅ <b>{url}</b>: снова отвечает (HTTP {code}).")
            web_state[key] = False

    _save_state(state)

    if alerts:
        text = "🖥 <b>Мониторинг нод</b>\n\n" + "\n".join(alerts)
        try:
            await notifier.notify_admins(
                payload=MessagePayloadDto(
                    i18n_key="raw-message",
                    i18n_kwargs={"content": text},
                    delete_after=None,
                ),
                roles=[Role.OWNER, Role.DEV, Role.ADMIN],
            )
            logger.info(f"node_health: отправлено {len(alerts)} алертов админам")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"node_health: не смог отправить алерт: {e}")
