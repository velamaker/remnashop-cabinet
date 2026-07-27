"""Мониторинг здоровья нод — алерты про то, что webhook Remnawave НЕ ловит.

Webhook панели уже шлёт CONNECTION_LOST/RESTORED (нода отвалилась целиком, см.
application/services/remnawave.py:handle_node_event). Здесь ловим случаи, когда
нода «на связи», но по факту сломана — их панель событием не сигналит:

  • xray упал при живой ноде  (isConnected=true, но xrayUptime=0) — алерт только
                               если лежит дольше NODE_XRAY_ALERT_AFTER_MIN (15 мин),
                               с диагностикой «что именно не работает»;
  • сменился IP ноды          (DNS-запись изменилась) — как в инциденте с Польшей
                               3-4 июля: сервер переехал, DNS смотрел в старый IP;
  • сертификат ноды           истекает через ≤ NODE_CERT_WARN_DAYS дней.

Алерт уходит админам штатным notify_admins (raw-message). Дедуп по состоянию в
assets/node_health.json — на каждый прогон не спамим, шлём только смену состояния.

Auto-discover taskiq по глобу tasks/*.py. Крутится раз в 5 минут (чтобы порог
алерта в 15 минут был точным).

Выключатель: env NODE_HEALTH_ALERTS (по умолчанию on). Порог серта: NODE_CERT_WARN_DAYS.
"""

import asyncio
import html
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

from src.application.common import Notifier, Remnawave
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


def _xray_alert_after_min() -> int:
    """Сколько минут xray должен лежать, прежде чем будить владельца.

    Короткие перезапуски xray (нода сама поднимает его за пару минут) алертом не
    считаем: 27 июля был ровно такой случай — упал и через ~7 минут поднялся,
    а владелец получил два сообщения подряд. Порог по умолчанию 15 минут, проверка
    крутится раз в 5 минут → сообщение приходит примерно на 15-й минуте простоя.
    """
    return _env_int("NODE_XRAY_ALERT_AFTER_MIN", 15)


def _auto_restart_enabled() -> bool:
    """Авто-рестарт ноды при упавшем xray. По умолчанию ВЫКЛ (осторожно)."""
    return (os.environ.get("NODE_AUTO_RESTART_XRAY") or "false").strip().lower() in (
        "1", "true", "yes", "on", "да",
    )


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _minutes_since(iso: Optional[str]) -> float:
    """Минут прошло с ISO-времени. None/битое → большое число (кулдаун пройден)."""
    if not iso:
        return 1e9
    try:
        t = datetime.fromisoformat(iso)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds() / 60.0
    except Exception:
        return 1e9


def _record_uptime(st: dict[str, Any], up: bool) -> None:
    """Суточный агрегат аптайма ноды для /status: history[YYYY-MM-DD]={t,u}. 30 дней."""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    hist = st.setdefault("history", {})
    slot = hist.setdefault(day, {"t": 0, "u": 0})
    slot["t"] = int(slot.get("t", 0)) + 1
    if up:
        slot["u"] = int(slot.get("u", 0)) + 1
    if len(hist) > 32:  # держим ~31 день
        for k in sorted(hist.keys())[:-31]:
            hist.pop(k, None)


async def _restart_node_via_sdk(remnawave: Remnawave, uuid: str) -> bool:
    """Авто-рестарт ноды через SDK панели. best-effort."""
    try:
        sdk = getattr(remnawave, "sdk", None)
        if sdk is None:
            return False
        # ВАЖНО: параметр SDK называется `uuid` (path-параметр). С `node_uuid=`
        # вызов падал TypeError ещё до запроса — авто-рестарт молча не работал.
        await sdk.nodes.restart_node(uuid=uuid)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"node_health: авто-рестарт {uuid} не удался: {e}")
        return False


def _load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, STATE_PATH)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"node_health: не смог сохранить состояние: {e}")


def _chunk_alerts(alerts: list[str], limit: int = 3500) -> list[str]:
    """Режет алерты на сообщения под лимит Telegram (4096 с запасом на шапку).

    Раньше всё склеивалось в один текст: при аварии сразу на нескольких нодах
    диагностика перерастала лимит, Telegram отвечал ошибкой — и владелец не
    получал НИЧЕГО. Теперь длинный алерт уезжает несколькими сообщениями, а
    отдельный сверхдлинный блок обрезается, но доходит.
    """
    chunks: list[str] = []
    cur: list[str] = []
    size = 0
    for a in alerts:
        piece = a if len(a) <= limit else a[: limit - 1] + "…"
        if cur and size + len(piece) + 1 > limit:
            chunks.append("\n".join(cur))
            cur, size = [], 0
        cur.append(piece)
        size += len(piece) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks


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


# ── Диагностика «что именно не работает» ────────────────────────────────────
# Алерт «xray не работает» без подробностей бесполезен: непонятно, отвалился
# сервер целиком, не поднялись инбаунды или просто пропала связка панель↔xray.
# Поэтому при падении мы сами достукиваемся до ноды с сервера бота.
#
# ВАЖНО про честность выводов: успешный TCP-коннект доказывает только то, что на
# порту кто-то принял соединение (это может быть и фолбэк-вебсервер selfsteal, и
# Caddy перед xray). Поэтому формулировки — в терминах наблюдаемого, без обещаний
# «трафик точно идёт».

# Классификация инбаундов. В панели живут не только наши протоколы (vless поверх
# raw/tcp/ws/grpc/xhttp + hysteria): есть shadowsocks для каскада/балансировщика, а
# у форков встречаются trojan, vmess, tuic, wireguard и прочее. Поэтому правило
# такое: щупаем ТОЛЬКО то, про что точно знаем, что это TCP-листенер; всё
# остальное честно помечаем «не проверяли» и НИКОГДА не считаем сломанным.
TCP_NETWORKS = {
    "tcp", "raw", "ws", "websocket", "grpc", "gun", "http", "h2", "h2c",
    "httpupgrade", "xhttp", "splithttp",
}
UDP_NETWORKS = {"hysteria", "hysteria2", "tuic", "quic", "kcp", "mkcp", "wireguard", "wg"}
# Протоколы, у которых поле network в API пустое (как у shadowsocks), но транспорт
# заведомо TCP. Для них ориентируемся на протокол.
TCP_PROTOCOLS = {"shadowsocks", "trojan", "vmess", "vless", "socks", "http", "mixed"}


def _inbound_transport(ib: dict[str, Any]) -> str:
    """tcp | udp | unknown — по сети инбаунда, а при её отсутствии по протоколу."""
    raw = ib.get("rawInbound") or {}
    network = str(ib.get("network") or (raw.get("streamSettings") or {}).get("network") or "").lower()
    if network in TCP_NETWORKS:
        return "tcp"
    if network in UDP_NETWORKS:
        return "udp"
    protocol = str(ib.get("type") or raw.get("protocol") or "").lower()
    if not network and protocol in TCP_PROTOCOLS:
        return "tcp"
    if not network and protocol in UDP_NETWORKS:
        return "udp"
    return "unknown"

# Состояния пробы: ok — приняли соединение; refused — порт закрыт; silent — не
# ответил за таймаут (фаервол DROP или тяжёлые потери); unknown — не смогли
# проверить со своей стороны (DNS, лимит сокетов, отмена таска). unknown НИКОГДА
# не считается доказательством того, что у ноды что-то сломано.
PROBE_OK, PROBE_REFUSED, PROBE_SILENT, PROBE_UNKNOWN = "ok", "refused", "silent", "unknown"


async def _tcp_probe(host: str, port: int, timeout: float = 4.0) -> tuple[str, str, Optional[int]]:
    """Состояние host:port по TCP: (состояние, что показать, мс отклика)."""
    writer = None
    started = datetime.now(timezone.utc)
    try:
        async with asyncio.timeout(timeout):
            _, writer = await asyncio.open_connection(host, port)
        ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        return PROBE_OK, f"ок, {ms} мс", ms
    except asyncio.TimeoutError:
        return PROBE_SILENT, f"не ответил за {int(timeout)} с", None
    except ConnectionRefusedError:
        return PROBE_REFUSED, "порт закрыт", None
    except socket.gaierror:
        return PROBE_UNKNOWN, "DNS не резолвится", None
    except OSError as e:  # noqa: BLE001
        return PROBE_UNKNOWN, f"проверка не удалась: {(getattr(e, 'strerror', None) or str(e))[:40]}", None
    except Exception as e:  # noqa: BLE001
        return PROBE_UNKNOWN, f"проверка не удалась: {str(e)[:40]}", None
    finally:
        if writer is not None:
            try:
                writer.close()
                await asyncio.wait_for(writer.wait_closed(), 1)
            except Exception:  # noqa: BLE001
                pass


def _active_inbounds(n: dict[str, Any]) -> list[dict[str, Any]]:
    """Инбаунды активного профиля ноды: tag/type/network/port/listen."""
    cp = n.get("configProfile") or {}
    return [i for i in (cp.get("activeInbounds") or []) if isinstance(i, dict)]


def _fmt_minutes(minutes: float) -> str:
    m = int(minutes)
    if m < 60:
        return f"{m} мин"
    h, rest = divmod(m, 60)
    if h < 24:
        return f"{h} ч {rest} мин" if rest else f"{h} ч"
    d, rest_h = divmod(h, 24)
    return f"{d} дн {rest_h} ч" if rest_h else f"{d} дн"


async def _diagnose_xray_down(
    n: dict[str, Any], address: str, st: dict[str, Any]
) -> tuple[list[str], str]:
    """Факты о лежащей ноде + вывод о вероятной причине (обе части — для алерта)."""
    facts: list[str] = []
    api_port = int(n.get("port") or 0)
    behind_proxy = bool(n.get("proxyUrl"))

    # Цели проверки: порт самой ноды + TCP-инбаунды, которые слушают наружу.
    targets: list[tuple[str, int]] = []
    if address and api_port and not behind_proxy:
        targets.append(("__api__", api_port))
    udp_names: list[str] = []
    skipped: list[str] = []
    local_only: list[str] = []
    for ib in _active_inbounds(n)[:8]:
        tag = str(ib.get("tag") or ib.get("type") or "?")
        port = int(ib.get("port") or 0)
        if not port:
            continue
        transport = _inbound_transport(ib)
        if transport == "udp":
            udp_names.append(f"{tag} :{port}/udp")
            continue
        if transport == "unknown":
            proto = str(ib.get("type") or "?")
            skipped.append(f"{tag} :{port} ({proto})")
            continue
        listen = str(((ib.get("rawInbound") or {}).get("listen") or "")).strip()
        if listen and listen not in ("0.0.0.0", "::", "[::]"):
            local_only.append(f"{tag} :{port} (слушает {listen})")
            continue
        if address:
            targets.append((tag, port))

    results: dict[str, tuple[str, str, Optional[int]]] = {}
    if targets:
        probes = await asyncio.gather(
            *[_tcp_probe(address, p) for _, p in targets], return_exceptions=True
        )
        for (tag, _), res in zip(targets, probes):
            results[tag] = res if isinstance(res, tuple) else (PROBE_UNKNOWN, "проверка не завершилась", None)

    # 1) Сама нода
    api_state = results.get("__api__", (None, "", None))[0]
    if behind_proxy:
        facts.append("• Нода за прокси — прямая проверка с сервера бота неприменима")
    elif api_state:
        facts.append(
            f"• Нода <code>{html.escape(address)}:{api_port}</code>: "
            + (results['__api__'][1] if api_state == PROBE_OK else f"<b>{html.escape(results['__api__'][1])}</b>")
        )

    # 2) Инбаунды
    inbound_results = [(tag, results[tag]) for tag, _ in targets if tag != "__api__" and tag in results]
    if inbound_results:
        shown = " · ".join(
            f"{html.escape(tag)} — " + (detail if state == PROBE_OK else f"<b>{html.escape(detail)}</b>")
            for tag, (state, detail, _) in inbound_results
        )
        facts.append(f"• Инбаунды: {shown[:300]}")
    if udp_names:
        facts.append(f"• UDP-инбаунды (TCP-проверке не поддаются): {html.escape(' · '.join(udp_names))[:150]}")
    if local_only:
        facts.append(f"• Не проверяются снаружи: {html.escape(' · '.join(local_only))[:150]}")
    if skipped:
        facts.append(f"• Транспорт не опознан, не проверяли: {html.escape(' · '.join(skipped))[:150]}")

    # 3) Панель и клиенты
    users_online = n.get("usersOnline")
    if users_online is not None:
        facts.append(f"• Клиентов онлайн на ноде: {users_online}")
    msg = n.get("lastStatusMessage")
    if msg:
        facts.append(f"• Панель сообщает: <code>{html.escape(str(msg)[:160])}</code>")

    # 4) Сертификат
    cert_days = st.get("cert_days")
    if isinstance(cert_days, int):
        facts.append(f"• Сертификат: {cert_days} дн." if cert_days > 0 else "• Сертификат: <b>ИСТЁК</b>")

    # 5) Длительность (алерт шлём только после порога, значит значение осмысленное)
    down_min = _minutes_since(st.get("xray_down_since")) if st.get("xray_down_since") else 0.0
    if 0 < down_min < 1e8:
        facts.append(f"• Лежит: {_fmt_minutes(down_min)}")

    # ── Вывод ───────────────────────────────────────────────────────────────
    bad = [t for t, (s_, _, _) in inbound_results if s_ in (PROBE_REFUSED, PROBE_SILENT)]
    ok_probes = [(t, ms) for t, (s_, _, ms) in inbound_results if s_ == PROBE_OK and ms is not None]
    checked = [t for t, (s_, _, _) in inbound_results if s_ != PROBE_UNKNOWN]
    api_bad = api_state in (PROBE_REFUSED, PROBE_SILENT)

    if api_bad and (not checked or len(bad) == len(checked)):
        cause = (
            f"нода не отвечает с сервера бота ({html.escape(results['__api__'][1])}) — "
            "похоже, лёг сам сервер, сеть или фаервол. Панель считает её «на связи» по прошлому состоянию."
        )
    elif checked and len(bad) == len(checked):
        cause = (
            "сервер отвечает, а порты инбаундов — нет: <b>xray, похоже, не поднялся</b>. "
            "Обычно это битый конфиг после правки инбаундов, занятый порт или проблема с сертификатом."
        )
    elif bad:
        cause = (
            f"часть инбаундов не отвечает (<b>{html.escape(', '.join(bad))}</b>), остальные "
            "принимают соединения. Смотрите их порты и конфиг — либо они намеренно закрыты "
            "фаерволом для всех, кроме своих (служебные инбаунды каскада так и настроены)."
        )
    elif checked:
        cause = (
            "порты инбаундов принимают TCP-соединения (что за ними — xray или фолбэк-вебсервер — "
            "такой проверкой не различить). Похоже, сломался не доступ, а сбор статистики панелью: "
            "панель не смогла опросить ноду и обнулила аптайм xray."
        )
        slow = [ms for _, ms in ok_probes if ms and ms > 800]
        if slow:
            cause += (
                f" Причём нода отвечает медленно (до {max(slow)} мс) — "
                "похоже на потери и деградацию канала до неё."
            )
    elif udp_names or skipped:
        cause = (
            "проверяемых TCP-инбаундов у ноды нет (только UDP и/или неопознанные транспорты) — "
            "проверьте ноду вручную."
        )
    else:
        cause = "проверить инбаунды не удалось (профиль пуст, адрес не задан или проверка не отработала) — смотрите ноду вручную."

    if isinstance(cert_days, int) and cert_days <= 0:
        cause += " Плюс <b>истёк сертификат</b> — с битым TLS xray не стартует."

    return facts[:8], cause


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


@broker.task(schedule=[{"cron": "*/5 * * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def check_node_health(
    config: FromDishka[AppConfig],
    notifier: FromDishka[Notifier],
    remnawave: FromDishka[Remnawave],
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

        # 1) xray лёг при живой ноде. Молчим, пока падение короче порога
        #    (NODE_XRAY_ALERT_AFTER_MIN, по умолчанию 15 мин) — самовосстановившийся
        #    перезапуск владельца не дёргает. Когда алертим — сразу с диагностикой:
        #    строчка «xray не работает» не отвечает на главный вопрос (лёг сервер,
        #    не поднялись инбаунды или отвалилась только статистика панели).
        # Аптайм может отсутствовать в ответе панели — тогда состояние xray нам
        # НЕ известно: молчим, ничего не сбрасываем (об обрыве связи придёт webhook).
        uptime_known = isinstance(xray_uptime, (int, float)) and not isinstance(xray_uptime, bool)
        xray_down = connected and uptime_known and xray_uptime == 0
        xray_up = connected and uptime_known and xray_uptime > 0
        alert_after = _xray_alert_after_min()
        if xray_down:
            if not st.get("xray_down"):
                st["xray_down"] = True
                st["xray_down_since"] = now_iso
            down_min = _minutes_since(st.get("xray_down_since"))
            if down_min >= alert_after and not st.get("xray_alerted"):
                try:
                    facts, cause = await _diagnose_xray_down(n, address, st)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"node_health: диагностика {name} не удалась: {e}")
                    facts, cause = [], "диагностика не отработала, проверьте ноду вручную."
                block = [
                    f"🛑 <b>{html.escape(name)}</b> — xray не работает уже "
                    f"{_fmt_minutes(down_min)}, при этом нода числится на связи."
                ]
                block.extend(facts)
                block.append(f"<b>Похоже на:</b> {cause}")
                alerts.append("\n".join(block))
                st["xray_alerted"] = True
        elif xray_up:
            if st.get("xray_alerted"):
                down_min = _minutes_since(st.get("xray_down_since")) if st.get("xray_down_since") else 0.0
                suffix = f" — лежал {_fmt_minutes(down_min)}" if 0 < down_min < 1e8 else ""
                alerts.append(f"✅ <b>{html.escape(name)}</b>: xray снова работает{suffix}.")
            elif st.get("xray_down"):
                short = _minutes_since(st.get("xray_down_since")) if st.get("xray_down_since") else 0.0
                logger.info(
                    f"node_health: {name} — xray поднялся сам за {_fmt_minutes(short)} "
                    f"(порог алерта {alert_after} мин), владельцу не писали"
                )
            st["xray_down"] = False
            st["xray_alerted"] = False

        # 1a) авто-восстановление: xray лежит дольше порога → авто-рестарт ноды.
        #     Env: NODE_AUTO_RESTART_XRAY (вкл), NODE_AUTO_RESTART_AFTER_MIN (порог),
        #     NODE_AUTO_RESTART_COOLDOWN_MIN (не чаще раза в N мин).
        if xray_down:
            if not st.get("xray_down_since"):
                st["xray_down_since"] = now_iso
            if _auto_restart_enabled():
                down_min = _minutes_since(st.get("xray_down_since"))
                cooldown_min = _minutes_since(st.get("last_auto_restart"))
                uuid = n.get("uuid")
                if (
                    uuid
                    and down_min >= _env_int("NODE_AUTO_RESTART_AFTER_MIN", 20)
                    and cooldown_min >= _env_int("NODE_AUTO_RESTART_COOLDOWN_MIN", 60)
                ):
                    ok = await _restart_node_via_sdk(remnawave, str(uuid))
                    st["last_auto_restart"] = now_iso
                    alerts.append(
                        f"🔁 <b>{html.escape(name)}</b>: xray лежал ~{int(down_min)} мин — "
                        + ("отправлен <b>авто-рестарт</b> ноды." if ok else "авто-рестарт <b>не удался</b> (см. логи).")
                    )
        elif xray_up:
            st.pop("xray_down_since", None)

        # 1b) история аптайма для /status (нода на связи и xray жив).
        _record_uptime(st, connected and not xray_down)

        # 2) смена IP ноды (DNS)
        if address:
            ip = await _resolve_ip(address)
            if ip:
                old_ip = st.get("ip")
                if old_ip and ip != old_ip:
                    alerts.append(f"🔀 <b>{html.escape(name)}</b>: сменился IP <code>{old_ip}</code> → <code>{ip}</code>. Проверьте DNS/серт/доступность.")
                st["ip"] = ip

        # 3) срок сертификата (сам замер — не чаще раза в 6 часов)
        if address and _minutes_since(st.get("cert_checked_at")) >= 360:
            days = await _cert_days_left(address)
            st["cert_checked_at"] = now_iso
            if days is not None:
                st["cert_days"] = days
        cert_days = st.get("cert_days")
        if isinstance(cert_days, int):
            if cert_days <= warn_days and not st.get("cert_warned"):
                alerts.append(
                    f"📜 <b>{html.escape(name)}</b>: сертификат <code>{html.escape(address)}</code> "
                    f"истекает через <b>{cert_days} дн.</b> — перевыпустите, иначе xray не стартует."
                )
                st["cert_warned"] = True
            elif cert_days > warn_days and st.get("cert_warned"):
                st["cert_warned"] = False

        st["checked_at"] = now_iso

    # 3a) Уборка состояния: ноды, переименованные или удалённые из панели, иначе
    #     копятся в файле навсегда (были записи двухнедельной давности).
    seen = {str(n.get("name") or n.get("uuid") or "?") for n in nodes if isinstance(n, dict)}
    for stale in [
        k
        for k, v in state.items()
        if not k.startswith("_")
        and k not in seen
        and isinstance(v, dict)
        and _minutes_since(v.get("checked_at")) > 7 * 24 * 60
    ]:
        state.pop(stale, None)
        logger.info(f"node_health: убрал из состояния исчезнувшую ноду «{stale}»")

    # 4) Health-чек кабинета/страницы подписки (URL-ы из env NODE_HEALTH_URLS).
    web_state = state.setdefault("_web", {})
    for url in _healthcheck_urls():
        code = await _check_url(url)
        bad = code is None or code >= 500
        key = url
        was_bad = web_state.get(key, False)
        if bad and not was_bad:
            shown = "недоступен (таймаут/ошибка)" if code is None else f"HTTP {code}"
            alerts.append(f"🌐 <b>{html.escape(url)}</b>: {shown} — сервис не отвечает.")
            web_state[key] = True
        elif not bad and was_bad:
            alerts.append(f"✅ <b>{html.escape(url)}</b>: снова отвечает (HTTP {code}).")
            web_state[key] = False

    _save_state(state)

    if alerts:
        sent = 0
        for chunk in _chunk_alerts(alerts):
            text = ("🖥 <b>Мониторинг нод</b>\n\n" + chunk)[:4000]
            try:
                await notifier.notify_admins(
                    payload=MessagePayloadDto(
                        i18n_key="raw-message",
                        i18n_kwargs={"content": text},
                        delete_after=None,
                    ),
                    roles=[Role.OWNER, Role.DEV, Role.ADMIN],
                )
                sent += 1
            except Exception as e:  # noqa: BLE001
                logger.warning(f"node_health: не смог отправить алерт: {e}")
        logger.info(f"node_health: отправлено {len(alerts)} алертов админам в {sent} сообщ.")
