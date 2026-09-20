"""Мониторинг бэкапов БД — алерт админам, если свежий бэкап не появился/пустой.

Бэкапы кладёт бот/скрипт в каталог (по умолчанию /opt/remnashop/backups, том
`../remnashop-backups`). Файлы вида `backup-YYYY-...sql.gz`. Здесь раз в 6 часов
проверяем САМЫЙ свежий: есть ли он, не старше ли порога, не пустой ли. Дедуп по
состоянию (assets/backup_monitor.json) — шлём только смену состояния (сломалось /
починилось), без спама.

Auto-discover taskiq по глобу tasks/*.py.

Env: BACKUP_MONITOR (вкл, по умолч. on), BACKUP_DIR (каталог),
BACKUP_MAX_AGE_HOURS (порог свежести, 26), BACKUP_MIN_BYTES (минимум, 1000),
BACKUP_GLOB (маска файлов, backup-*.sql.gz).
"""

import os
import time
from glob import glob
from pathlib import Path
from typing import Any, Optional

from dishka.integrations.taskiq import FromDishka, inject
from loguru import logger

from src.application.common import Notifier
from src.application.dto import MessagePayloadDto
from src.core.enums import Role
from src.infrastructure.taskiq.broker import broker

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
STATE_PATH = ASSETS_DIR / "backup_monitor.json"


def _enabled() -> bool:
    return (os.environ.get("BACKUP_MONITOR") or "true").strip().lower() in (
        "1", "true", "yes", "on", "да",
    )


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _load_state() -> dict[str, Any]:
    try:
        import json

        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    try:
        import json

        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"backup_monitor: не смог сохранить состояние: {e}")


def _drill_check() -> tuple[bool, str]:
    """Учение «восстановись из бэкапа»: прошло ли и когда.

    Файл пишет scripts/db-restore-verify.sh — он поднимает одноразовый Postgres,
    разворачивает последний бэкап, гонит по нему overlay-миграции и считает строки.
    Раньше учение было честным, но невидимым: провал жил в логе на сервере. Теперь
    его состояние доезжает до владельца тем же путём, что и «бэкап не делается».

    Нет файла — молчим НАМЕРЕННО: на установке, где учение не настроено, это не
    поломка, а отсутствие функции; ругаться на неё каждые шесть часов — шум.
    """
    path = Path(os.environ.get("RESTORE_DRILL_STATE", str(ASSETS_DIR / "restore_drill.json")))
    max_age_d = _env_int("RESTORE_DRILL_MAX_AGE_DAYS", 35)
    try:
        if not path.exists():
            return False, ""
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return True, f"итог учения по бэкапу нечитаем ({e})"

    status = str(data.get("status") or "").lower()
    message = str(data.get("message") or "")[:200]
    age_d = (time.time() - path.stat().st_mtime) / 86400.0
    if status != "ok":
        return True, f"учение «восстановись из бэкапа» УПАЛО: {message}"
    if age_d > max_age_d:
        return True, (
            f"учение «восстановись из бэкапа» не проходило {int(age_d)} дн. "
            f"(порог {max_age_d}). Бэкапы есть, но восстановимость не проверена"
        )
    return False, f"учение по бэкапу прошло {int(age_d)} дн. назад"


def _check() -> tuple[bool, str]:
    """Проверить самый свежий бэкап. Возврат (bad, reason).

    BACKUP_GLOB — маска(и) файлов, можно несколько через запятую. По умолчанию
    следим и за нашим `backup-*.sql.gz` (крон scripts/db-backup.sh), и за запасным
    `db_backup_*.sql` — берём самый свежий из всех."""
    backup_dir = os.environ.get("BACKUP_DIR", "/opt/remnashop/backups")
    patterns = [
        p.strip()
        for p in (os.environ.get("BACKUP_GLOB", "backup-*.sql.gz,db_backup_*.sql")).split(",")
        if p.strip()
    ]
    max_age_h = _env_int("BACKUP_MAX_AGE_HOURS", 26)
    min_bytes = _env_int("BACKUP_MIN_BYTES", 1000)

    files: list[str] = []
    for pattern in patterns:
        files.extend(glob(os.path.join(backup_dir, pattern)))
    if not files:
        return True, f"в каталоге {backup_dir} нет ни одного бэкапа ({', '.join(patterns)})"

    newest = max(files, key=lambda p: os.path.getmtime(p))
    age_h = (time.time() - os.path.getmtime(newest)) / 3600.0
    size = os.path.getsize(newest)
    fname = os.path.basename(newest)

    if size < min_bytes:
        return True, f"последний бэкап <b>{fname}</b> подозрительно мал ({size} Б < {min_bytes})"
    if age_h > max_age_h:
        return True, f"последний бэкап <b>{fname}</b> устарел — {int(age_h)} ч назад (порог {max_age_h} ч). Бэкап не делается?"

    # Бэкап на месте и свежий — но разворачивается ли он? Об этом знает учение.
    drill_bad, drill_reason = _drill_check()
    if drill_bad:
        return True, drill_reason
    tail = f"; {drill_reason}" if drill_reason else ""
    return False, f"последний бэкап {fname}, {int(age_h)} ч назад, {size // 1024} КБ{tail}"


@broker.task(schedule=[{"cron": "0 */6 * * *"}], retry_on_error=False)
@inject(patch_module=True)
async def check_backups(notifier: FromDishka[Notifier]) -> None:
    if not _enabled():
        return

    bad, reason = _check()
    state = _load_state()
    was_bad = bool(state.get("bad"))

    alert: Optional[str] = None
    if bad and not was_bad:
        alert = f"🛑 <b>Бэкап БД</b>\n\n{reason}"
    elif not bad and was_bad:
        alert = f"✅ <b>Бэкап БД</b>: снова в порядке — {reason}"

    state["bad"] = bad
    state["reason"] = reason
    _save_state(state)

    if alert:
        try:
            await notifier.notify_admins(
                payload=MessagePayloadDto(
                    i18n_key="raw-message",
                    i18n_kwargs={"content": alert},
                    delete_after=None,
                    # Иначе у алерта нет кнопки «Закрыть»: дефолт поля — True.
                    disable_default_markup=False,
                ),
                roles=[Role.OWNER, Role.DEV, Role.ADMIN],
            )
            logger.info(f"backup_monitor: алерт отправлен (bad={bad})")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"backup_monitor: не смог отправить алерт: {e}")
