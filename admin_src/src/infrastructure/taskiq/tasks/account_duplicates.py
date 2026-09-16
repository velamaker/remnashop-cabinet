"""Сторож двойников аккаунтов: ловит пару в момент поломки, а не по жалобе.

Почему поломка возникает и что считать двойником — в
`infrastructure/services/overlay_duplicates.py`. Здесь только расписание, поход в
панель и дедуп уведомлений.

РАЗ В ЧАС, СО СДВИГОМ. Синхрон панель→бот крутится каждые полчаса; проверять чаще
незачем, а сразу после него — можно попасть в середину прогона. Отсюда 37-я минута.

ДЕДУП ПО ПАРЕ ЗАПИСЕЙ, А НЕ ПО ВРЕМЕНИ: пока пару не слили, она будет находиться
каждый час, и без памяти владелец получал бы одно и то же сообщение круглые сутки.
Слили — ключ исчезает сам, потому что пара больше не находится.

Выключатель: env ACCOUNT_DUPLICATE_ALERTS (по умолчанию on).
"""

import json
import os
from pathlib import Path
from typing import Any

from dishka.integrations.taskiq import FromDishka, inject
from httpx import AsyncClient, Timeout
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common import Notifier
from src.application.dto import MessagePayloadDto
from src.core.config import AppConfig
from src.core.enums import Role
from src.infrastructure.services.overlay_duplicates import describe, find_broken_pairs
from src.infrastructure.taskiq.broker import broker

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
STATE_PATH = ASSETS_DIR / "account_duplicates.json"


def _enabled() -> bool:
    return (os.environ.get("ACCOUNT_DUPLICATE_ALERTS") or "true").strip().lower() in (
        "1", "true", "yes", "on", "да",
    )


def _load() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — нет файла или битый: начинаем с чистого
        return {}


def _save(state: dict[str, Any]) -> None:
    try:
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as exc:
        # Не смогли запомнить — значит в следующий час пришлём то же самое.
        # Это неприятно, но честнее, чем промолчать о настоящей поломке.
        logger.error(f"duplicates: не смог сохранить состояние {STATE_PATH}: {exc}")


async def _panel_users(config: AppConfig) -> list[dict[str, Any]]:
    """Пользователи панели. Клиент собираем так же, как node_health._fetch_nodes.

    Страницы обходим до конца. Одной страницы в 500 записей сегодня хватает (в
    панели 335), но молчаливое усечение на 501-м пользователе — ровно тот класс
    поломок, который этот сторож и призван ловить: часть аккаунтов просто выпала бы
    из разбора, и пара не нашлась бы никогда.
    """
    c = config.remnawave
    headers = {
        "Authorization": f"Bearer {c.token.get_secret_value()}",
        "X-Api-Key": c.caddy_token.get_secret_value(),
        "CF-Access-Client-Id": c.cf_client_id.get_secret_value(),
        "CF-Access-Client-Secret": c.cf_client_secret.get_secret_value(),
    }
    if not c.is_external:
        # Панель по http без этой пары рвёт соединение (её ProxyCheckMiddleware).
        headers["x-forwarded-proto"] = "https"
        headers["x-forwarded-for"] = "127.0.0.1"

    page_size = 500
    out: list[dict[str, Any]] = []
    async with AsyncClient(
        base_url=f"{c.url.get_secret_value()}/api",
        headers=headers,
        cookies=c.cookies,
        verify=True,
        timeout=Timeout(connect=15, read=40, write=10, pool=5),
    ) as cl:
        start = 0
        while True:
            r = await cl.get("/users", params={"size": page_size, "start": start})
            if r.status_code != 200:
                logger.warning(f"duplicates: /users вернул {r.status_code}")
                break
            body = (r.json() or {}).get("response") or {}
            chunk = body.get("users") or body.get("items") or []
            out.extend(x for x in chunk if isinstance(x, dict))
            if len(chunk) < page_size:
                break
            start += page_size
            if start > 20000:  # предохранитель от бесконечного обхода
                logger.warning("duplicates: обход пользователей панели прерван на 20000")
                break
    return out


@broker.task(schedule=[{"cron": "37 * * * *"}], retry_on_error=False)
# patch_module=True — как у всех остальных задач проекта. Без него dishka
# регистрирует задачу под своим именем (dishka.integrations.base:...), и в
# логах планировщика она неотличима от любой другой.
@inject(patch_module=True)
async def check_account_duplicates(
    session: FromDishka[AsyncSession],
    config: FromDishka[AppConfig],
    notifier: FromDishka[Notifier],
) -> None:
    if not _enabled():
        return

    try:
        panel_users = await _panel_users(config)
    except Exception as exc:  # noqa: BLE001 — панель недоступна: не наша беда
        logger.warning(f"duplicates: не смог получить пользователей панели: {exc}")
        return
    if not panel_users:
        return

    try:
        pairs = await find_broken_pairs(session, panel_users)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"duplicates: разбор не удался: {exc}")
        return

    state = _load()
    known = set(state.get("reported") or [])
    # По ОДНОЙ паре, а не по одной подписке: у безымянной записи их может быть
    # несколько, ключ дедупа у таких пар общий, и владелец получил бы одно и то же
    # сообщение дважды за прогон.
    seen: set[str] = set()
    fresh = []
    for p in pairs:
        if p.key in known or p.key in seen:
            continue
        seen.add(p.key)
        fresh.append(p)

    for pair in fresh:
        try:
            await notifier.notify_admins(
                payload=MessagePayloadDto(
                    i18n_key="raw-message",
                    i18n_kwargs={"content": describe(pair)},
                    delete_after=None,
                    disable_default_markup=False,
                ),
                roles=[Role.OWNER, Role.DEV],
            )
            known.add(pair.key)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"duplicates: не смог сообщить о паре {pair.key}: {exc}")

    # Ключи слитых пар выбрасываем: если та же пара сломается снова, о ней надо
    # сказать заново.
    state["reported"] = sorted(known & {p.key for p in pairs})
    _save(state)

    if pairs:
        logger.info(f"duplicates: найдено пар {len(pairs)}, новых {len(fresh)}")
