"""Уведомление владельца о новой версии форка (кабинет+админка).

Раз в день сверяет установленную версию (файл VERSION) с последним тегом на
GitHub. Если вышла новее — шлёт владельцу (BOT_OWNER_ID) сообщение: версия,
команда обновления и ссылка «что нового». Повторно про одну версию не пишет
(состояние в assets/update_state.json).

Отключить: UPDATE_NOTIFY=false. Авто-обнаруживается taskiq по tasks/*.py.
"""

import json
import os
import re
from pathlib import Path

import httpx
from aiogram import Bot
from loguru import logger

from src.infrastructure.taskiq.broker import broker

REPO = "alexdsndr161rus2015-maker/remnashop-cabinet"
ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
STATE_PATH = ASSETS_DIR / "update_state.json"
VERSION_PATH = Path("/opt/remnashop/VERSION")


def _enabled() -> bool:
    return (os.environ.get("UPDATE_NOTIFY") or "true").strip().lower() != "false"


def _local_version() -> str:
    try:
        return VERSION_PATH.read_text(encoding="utf-8").strip() or "0"
    except Exception:
        return "0"


def _parse(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", (v or "").lstrip("vV"))
    return tuple(int(x) for x in nums) or (0,)


async def _latest_tag() -> str | None:
    try:
        async with httpx.AsyncClient(timeout=8) as cli:
            resp = await cli.get(f"https://api.github.com/repos/{REPO}/tags")
        tags = resp.json()
        names = [t["name"] for t in tags if isinstance(t, dict) and t.get("name")]
        sem = [n for n in names if re.match(r"^v?\d+(\.\d+)+$", n.strip())]
        return max(sem, key=_parse) if sem else None
    except Exception as e:  # noqa: BLE001
        logger.debug(f"update-notifier: не смог получить теги: {e}")
        return None


def _load_state() -> dict:
    try:
        if STATE_PATH.exists():
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_state(version: str) -> None:
    try:
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps({"notified": version}), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"update-notifier: не смог сохранить состояние: {e}")


@broker.task(schedule=[{"cron": "0 9 * * *"}], retry_on_error=False)
async def check_update_and_notify() -> None:
    if not _enabled():
        return
    token = (os.environ.get("BOT_TOKEN") or "").strip()
    owner = (os.environ.get("BOT_OWNER_ID") or "").strip()
    if not token or not owner:
        return

    local = _local_version()
    latest = await _latest_tag()
    if not latest or _parse(latest) <= _parse(local):
        return  # уже последняя / не удалось узнать

    if _load_state().get("notified") == latest:
        return  # про эту версию уже писали

    latest_clean = latest.lstrip("vV")
    text = (
        "🆕 Доступно обновление RемнаShop (кабинет + админка)\n\n"
        f"Версия: {local} → {latest_clean}\n\n"
        "Как обновить (на сервере бота):\n"
        "  cd /opt/remnashop && ./update.sh\n\n"
        f"Что нового: https://github.com/{REPO}/compare/v{local}...{latest}"
    )

    bot = Bot(token)
    try:
        await bot.send_message(int(owner), text)
        _save_state(latest)
        logger.info(f"update-notifier: уведомил владельца об обновлении {latest}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"update-notifier: не смог отправить уведомление: {e}")
    finally:
        await bot.session.close()
