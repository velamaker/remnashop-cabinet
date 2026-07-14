"""Крон: авто-подтяжка ссылок установки приложений из upstream app-config.json.

Раз в сутки читает URL источника из apps.json (`links_source_url`, задаёт админ)
и, если он задан, скачивает актуальные ссылки в assets/app_links.json (см.
overlay_app_links). Ничего не делает, если источник не настроен. Авто-обнаружение
taskiq по глобу tasks/*.py.
"""

from loguru import logger

from src.infrastructure.services.overlay_app_links import fetch_and_store
from src.infrastructure.taskiq.broker import broker


@broker.task(schedule=[{"cron": "0 6 * * *"}], retry_on_error=False)
async def run_refresh_app_links() -> None:
    # Ленивый импорт — не тянем web-слой на старте воркера.
    from src.web.endpoints.public.apps import load_apps_config

    url = (load_apps_config().get("links_source_url") or "").strip()
    if not url:
        return

    result = await fetch_and_store(url)
    if result.get("ok"):
        logger.info(
            f"app_links: обновлены ссылки для {result.get('count')} приложений"
        )
    else:
        logger.warning(f"app_links: не удалось обновить: {result.get('error')}")
