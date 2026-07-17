"""Крон: авто-подтяжка АКТУАЛЬНЫХ ссылок установки приложений + алерт владельцу.

Раз в сутки прогоняет курируемые резолверы (App Store bundleId / Play / GitHub,
см. overlay_app_links) и пишет живые ссылки в assets/app_links.json. Резолверы
работают всегда; upstream app-config.json (`links_source_url` из apps.json) —
необязательный доп.источник для приложений без резолвера.

Тир 2: когда ранее живая ОСНОВНАЯ ссылка умирает (ушла с родного стора = degraded
или вообще перестала резолвиться = missing), шлём Telegram-алерт владельцу. Чтобы
не спамить стабильным состоянием (Happ iOS давно снят из RU и т.п.), алертим только
на ПЕРЕХОДЫ: сравниваем с предыдущим снимком проблем (app_links_alert_state.json).
Первый прогон = baseline (запоминаем текущее, не алертим). Авто-обнаружение taskiq
по глобу tasks/*.py.
"""

import json
import os
from pathlib import Path

from loguru import logger

from src.infrastructure.services.overlay_app_links import ASSETS_DIR, fetch_and_store
from src.infrastructure.taskiq.broker import broker

_ALERT_STATE_PATH: Path = ASSETS_DIR / "app_links_alert_state.json"


def _load_alert_state() -> dict:
    try:
        if _ALERT_STATE_PATH.exists():
            with _ALERT_STATE_PATH.open(encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
    except Exception:  # noqa: BLE001
        pass
    return {}


def _save_alert_state(degraded: list[str], missing: list[str]) -> None:
    try:
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        with _ALERT_STATE_PATH.open("w", encoding="utf-8") as fh:
            json.dump(
                {"initialized": True, "degraded": sorted(degraded), "missing": sorted(missing)},
                fh,
                ensure_ascii=False,
                indent=2,
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"app_links: не сохранил alert-state: {exc}")


async def _alert_owner(new_degraded: list[str], new_missing: list[str], recovered: list[str]) -> None:
    """Отправить владельцу Telegram-сводку об изменениях (best-effort)."""
    if os.environ.get("APP_LINKS_ALERTS", "1").lower() in ("0", "false", "no", "off"):
        return
    if not (new_degraded or new_missing or recovered):
        return

    from aiogram import Bot

    from src.core.config import AppConfig

    config = AppConfig.get()
    owner_id = getattr(config.bot, "owner_id", None)
    if not owner_id:
        return

    lines = ["🔗 Ссылки приложений — изменения:"]
    if new_missing:
        lines.append("🔴 нет рабочей ссылки: " + ", ".join(new_missing))
    if new_degraded:
        lines.append("🟡 ушли с основного стора: " + ", ".join(new_degraded))
    if recovered:
        lines.append("✅ восстановлено: " + ", ".join(recovered))
    lines.append("\nПроверь раздел «Приложения» → «Актуальные ссылки установки».")
    text_msg = "\n".join(lines)

    bot: Bot | None = None
    try:
        bot = Bot(config.bot.token.get_secret_value())
        await bot.send_message(int(owner_id), text_msg)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"app_links: TG-алерт владельцу не доставлен: {exc}")
    finally:
        if bot is not None:
            try:
                await bot.session.close()
            except Exception:  # noqa: BLE001
                pass


@broker.task(schedule=[{"cron": "0 6 * * *"}], retry_on_error=False)
async def run_refresh_app_links() -> None:
    # Ленивый импорт — не тянем web-слой на старте воркера.
    from src.web.endpoints.public.apps import load_apps_config

    url = (load_apps_config().get("links_source_url") or "").strip()

    result = await fetch_and_store(url)
    if not result.get("ok"):
        logger.warning(f"app_links: не удалось обновить: {result.get('error')}")
        return

    degraded = list(result.get("degraded") or [])
    missing = list(result.get("missing") or [])
    msg = f"app_links: обновлены ссылки для {result.get('count')} приложений"
    if degraded:
        msg += f"; деградировали (не в родном сторе): {', '.join(degraded)}"
    if missing:
        msg += f"; без рабочей ссылки: {', '.join(missing)}"
    logger.info(msg)

    # ── Алерт по переходам (тир 2) ────────────────────────────────────────────
    state = _load_alert_state()
    prev_degraded = set(state.get("degraded") or [])
    prev_missing = set(state.get("missing") or [])
    cur_degraded, cur_missing = set(degraded), set(missing)

    if not state.get("initialized"):
        # Первый прогон — только запоминаем baseline, без алерта.
        _save_alert_state(degraded, missing)
        logger.info("app_links: alert-baseline сохранён (первый прогон, без уведомления)")
        return

    new_degraded = sorted(cur_degraded - prev_degraded)
    new_missing = sorted(cur_missing - prev_missing)
    # Восстановление: было проблемой (любого рода), теперь ни degraded, ни missing.
    recovered = sorted((prev_degraded | prev_missing) - (cur_degraded | cur_missing))

    if new_degraded or new_missing or recovered:
        await _alert_owner(new_degraded, new_missing, recovered)

    _save_alert_state(degraded, missing)
