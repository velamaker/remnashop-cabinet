"""Уведомление «трафик заканчивается» (≥N% лимита) — overlay.

Крон (каждые 3ч): bulk-обходит юзеров панели (GET /api/users), находит активных с
лимитом трафика, у кого израсходовано ≥ порога (и <100% — 100% это уже LIMITED,
покрыто customRemarks), мапит на наших USER и шлёт Telegram + Web Push «трафик
заканчивается». Дедуп по assets/traffic_alert_state.json (user_id → used на момент
уведомления): при сбросе трафика (used упал) — уведомляем в новом цикле снова; ниже
порога — забываем.

Конфиг assets/traffic_alert.json (админка). Дефолт ВЫКЛ. Данные — из Remnawave
(клиент как node_health._fetch_nodes). Best-effort.

ДОКУПКА ТРАФИКА добавляет сюда две вещи, и обе — без единого лишнего запроса к
панели: ответ /users уже содержит и `createdAt`, и `trafficLimitStrategy`.
  * к сообщению «заканчивается» дописывается строка «можно докупить N ГБ за X ₽ —
    прибавка действует до обновления трафика DD.MM», если продажи включены и человек
    проходит те же проверки, что и покупка;
  * появляется ДОГОНЯЮЩАЯ ветка для pct >= 100: вебхук `user.limited` шлёт такое
    предложение мгновенно, но панель повторяет вебхуки не бесконечно, и этот проход
    подбирает тех, до кого оно не дошло. Дедуп — общий, в Redis (файл нельзя: вебхук
    исполняет процесс бота, крон — воркер, и JSON они затирали бы друг другу).
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from aiogram import Bot
from dishka.integrations.taskiq import FromDishka, inject
from httpx import AsyncClient, Timeout
from loguru import logger
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import AppConfig
from src.infrastructure.services import overlay_extra_traffic as extra
from src.infrastructure.services.overlay_push import _fill, notify_user_push
from src.infrastructure.services.overlay_traffic_alert import load_config
from src.infrastructure.taskiq.broker import broker

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
STATE_PATH = ASSETS_DIR / "traffic_alert_state.json"
_GB = 1024 ** 3
_PAGE = 500

_MSG = {
    "ru": (
        "⚠️ Трафик заканчивается",
        "Израсходовано {pct}% трафика ({used} из {limit} ГБ). "
        "Продлите или смените тариф, чтобы не остаться без доступа.",
    ),
    "en": (
        "⚠️ Traffic running low",
        "You've used {pct}% of your traffic ({used} of {limit} GB). "
        "Renew or upgrade to stay connected.",
    ),
}


def _load_state() -> dict[str, int]:
    try:
        return {str(k): int(v) for k, v in json.loads(STATE_PATH.read_text("utf-8")).items()}
    except Exception:  # noqa: BLE001
        return {}


def _save_state(state: dict[str, int]) -> None:
    try:
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state), "utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"traffic_alert: не сохранил стейт: {e}")


async def _fetch_all_users(config: AppConfig) -> list[dict[str, Any]]:
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
    users: list[dict[str, Any]] = []
    async with AsyncClient(
        base_url=f"{c.url.get_secret_value()}/api",
        headers=headers, cookies=c.cookies, verify=True,
        timeout=Timeout(connect=15, read=30, write=10, pool=5),
    ) as cl:
        start = 0
        while True:
            r = await cl.get("/users", params={"size": _PAGE, "start": start})
            if r.status_code != 200:
                logger.warning(f"traffic_alert: /users вернул {r.status_code}")
                break
            resp = r.json().get("response", {}) or {}
            batch = resp.get("users") or resp.get("data") or []
            if not batch:
                break
            users.extend(batch)
            total = int(resp.get("total", 0) or 0)
            start += len(batch)
            if start >= total or len(batch) < _PAGE:
                break
    return users


def _panel_view(u: dict[str, Any]) -> Any:
    """Строка /users → то, что нужно расчёту докупки. Второй раз в панель не идём."""
    return SimpleNamespace(
        uuid=u.get("uuid"),
        traffic_limit_bytes=int(u.get("trafficLimitBytes") or 0),
        status=u.get("status"),
        created_at=_parse_dt(u.get("createdAt")),
        user_traffic=SimpleNamespace(
            used_traffic_bytes=int((u.get("userTraffic") or {}).get("usedTrafficBytes") or 0)
        ),
    )


def _parse_dt(raw: Any) -> Any:
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def _extra_traffic_line(
    session: AsyncSession, cfg: dict[str, Any], user_id: int, u: dict[str, Any], now: Any
) -> str:
    """Строка «можно докупить» к сообщению о 80 %. Пусто — предлагать нечего."""
    if not extra.effective_enabled(cfg):
        return ""
    allowed, window_end = await extra.can_offer_now(
        session, cfg, user_id, extra.panel_view(_panel_view(u)), now
    )
    if not allowed:
        return ""
    return extra.user_text(
        "almost_out",
        gb=int(cfg["gb_per_purchase"]),
        price=int(cfg["price_rub"]),
        until=window_end,
    )


async def _catch_up_limited(
    session: AsyncSession,
    redis: Redis,
    config: AppConfig,
    user_id: int,
    telegram_id: Any,
    u: dict[str, Any],
    now: Any,
) -> bool:
    """Тем, у кого трафик уже кончился, а вебхук с предложением не дошёл.

    Дедуп общий с вебхуком (ключ Redis по человеку и окну трафика), поэтому второго
    сообщения этот проход не даёт даже сразу после вебхука.
    """
    return await extra.offer_when_limited(
        session,
        redis,
        config,
        SimpleNamespace(id=user_id, telegram_id=telegram_id),
        extra.panel_view(_panel_view(u)),
        now,
    )


@broker.task(schedule=[{"cron": "47 */3 * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def run_traffic_alert(
    session: FromDishka[AsyncSession],
    config: FromDishka[AppConfig],
    redis: FromDishka[Redis],
) -> None:
    cfg = load_config()
    extra_cfg = extra.load_config()
    now = extra.now_utc()
    if not cfg["enabled"] and not extra.effective_enabled(extra_cfg):
        # Ни предупреждений о трафике, ни докупки — ходить в панель незачем.
        return
    threshold = cfg["threshold_percent"]

    try:
        users = await _fetch_all_users(config)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"traffic_alert: не получил юзеров: {e}")
        return
    if not users:
        return

    # Карта remna-uuid → (user_id, lang, telegram_id) для наших активных USER.
    rows = (
        await session.execute(
            text(
                "SELECT s.user_remna_id, u.id, lower(u.language::text), u.telegram_id "
                "FROM users u JOIN subscriptions s ON u.current_subscription_id = s.id "
                "WHERE u.role = 'USER' AND s.user_remna_id IS NOT NULL"
            )
        )
    ).all()
    mine = {str(ru): (uid, lang, tg) for ru, uid, lang, tg in rows}

    state = _load_state()
    new_state: dict[str, int] = {}
    bot: Bot | None = None
    try:
        bot = Bot(config.bot.token.get_secret_value())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"traffic_alert: не смог создать Bot ({e}) — TG пропущены")

    sent = 0
    offered = 0
    for u in users:
        limit = int(u.get("trafficLimitBytes") or 0)
        # LIMITED пропускаем дальше намеренно: это ровно те, у кого трафик кончился,
        # и единственные адресаты догоняющего предложения докупить.
        status = str(u.get("status") or "")
        if limit <= 0 or status not in ("ACTIVE", "LIMITED"):
            continue
        ut = u.get("userTraffic") or {}
        used = int(ut.get("usedTrafficBytes") or 0)
        pct = int(used * 100 / limit)
        target = mine.get(str(u.get("uuid")))
        if target is None:
            continue
        uid, lang, tg_id = target

        if status == "LIMITED" or pct >= 100:
            # Трафик кончился: предупреждать поздно, но можно предложить докупить —
            # если продажи включены и человек реально может купить. Дедуп в Redis
            # общий с вебхуком, поэтому дубля не будет. В стейте не держим: при
            # обновлении трафика человек должен снова попасть под предупреждение.
            try:
                if await _catch_up_limited(session, redis, config, uid, tg_id, u, now):
                    offered += 1
            except Exception as e:  # noqa: BLE001 — один человек не рушит проход
                logger.warning(f"extra_traffic: догоняющее предложение user_id={uid}: {e}")
            continue
        if pct < threshold or not cfg["enabled"]:
            # ниже порога — не держим в стейте, чтобы при новом заходе за порог
            # уведомить снова. Выключенные предупреждения не мешают докупке выше.
            continue

        key = str(uid)
        prev = state.get(key)
        # Уже уведомляли в этом цикле, если prev есть и трафик не сбрасывался (used не упал).
        if prev is not None and used >= prev:
            new_state[key] = prev
            continue

        gb_used = round(used / _GB, 1)
        gb_limit = round(limit / _GB, 1)
        l = (lang or "ru")[:2]
        title, body_tpl = _MSG.get(l, _MSG["ru"])
        # Через _fill, а не .format(): вызов стоит внутри цикла по людям и вне
        # try, а у задачи retry_on_error=False — расхождение шаблона и аргументов
        # уронило бы ВЕСЬ проход, и все, кто в очереди после, остались бы без
        # предупреждения о трафике до следующего кроном. Текст в норме тот же.
        body = _fill(body_tpl, {"pct": pct, "used": gb_used, "limit": gb_limit})
        # Докупка — отдельной строкой и только для русского текста: остальные языки
        # берут переводы из кабинета, а здесь у нас Python-строки.
        try:
            offer = await _extra_traffic_line(session, extra_cfg, uid, u, now) if l == "ru" else ""
        except Exception as e:  # noqa: BLE001 — предупреждение важнее приписки
            logger.warning(f"extra_traffic: строка докупки для user_id={uid}: {e}")
            offer = ""
        if offer:
            body = f"{body}\n\n{offer}"

        await notify_user_push(
            session, SimpleNamespace(id=uid, language=lang),
            _MSG, url="/billing", tag="traffic-alert",
            pct=pct, used=gb_used, limit=gb_limit,
        )
        if bot is not None and tg_id:
            try:
                await bot.send_message(int(tg_id), f"<b>{title}</b>\n\n{body}")
            except Exception as e:  # noqa: BLE001
                logger.debug(f"traffic_alert: TG user_id={uid} не доставлено: {e}")
        new_state[key] = used
        sent += 1

    if cfg["enabled"]:
        # Стейт принадлежит предупреждениям о трафике. Если они выключены, а крон
        # всё равно прошёл ради докупки, чужое состояние затирать нельзя.
        _save_state(new_state)
    if bot is not None:
        try:
            await bot.session.close()
        except Exception:  # noqa: BLE001
            pass
    if sent:
        logger.info(f"traffic_alert: предупреждено о трафике {sent} юзеров (порог {threshold}%)")
    if offered:
        logger.info(f"extra_traffic: догоняющее предложение докупить ушло {offered} юзерам")
