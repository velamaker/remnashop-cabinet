"""Лента обновлений: список релизов из локального CHANGELOG.md.

Работает у ЛЮБОГО, кто поставил кабинет, БЕЗ сети: версии и понятные пункты
берутся из курируемого `CHANGELOG.md` (лежит в образе). GitHub опрашивается
только как best-effort обогащение (даты релизов, ссылки) — если репозиторий
приватный/недоступен/не тот, лента всё равно показывается. Кэш в памяти.
"""

import os
import re
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter

from ._common import AdminUser

router = APIRouter(prefix="/updates", tags=["Admin - Updates"])

REPO = (os.environ.get("UPDATE_REPO") or "ander1k/remnashop-cabinet").strip()
VERSION_PATH = Path("/opt/remnashop/VERSION")
CHANGELOG_PATH = Path("/opt/remnashop/CHANGELOG.md")

_cache: dict[str, Any] = {"at": 0.0, "items": None}
_TTL = 900  # 15 минут


def _local_version() -> str:
    try:
        return VERSION_PATH.read_text(encoding="utf-8").strip() or "0"
    except Exception:
        return "0"


def _norm(v: str) -> str:
    """Нормализуем версию к ключу вида '0.7' (без v, лишних пробелов)."""
    return (v or "").strip().lstrip("vV")


def _local_changelog() -> dict[str, str]:
    """Курируемый CHANGELOG.md → {'0.7': '• пункт\\n• пункт', ...}.

    Это приоритетный источник: понятные пользователю пункты, а не сырые коммиты.
    """
    try:
        text = CHANGELOG_PATH.read_text(encoding="utf-8")
    except Exception:
        return {}
    return _parse_changelog_text(text)


def _parse(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", (v or "").lstrip("vV"))
    return tuple(int(x) for x in nums) or (0,)


def _parse_changelog_text(text: str) -> dict[str, str]:
    """CHANGELOG.md → {'0.9.6': '• пункт\\n• пункт', ...} (общий парсер для локального/remote)."""
    result: dict[str, str] = {}
    cur: Optional[str] = None
    lines: list[str] = []
    for raw in text.splitlines():
        m = re.match(r"^##\s+v?(\d+(?:\.\d+)+)\s*$", raw.strip())
        if m:
            if cur is not None and lines:
                result[cur] = "\n".join(lines)
            cur = m.group(1)
            lines = []
            continue
        if cur is not None:
            s = raw.strip()
            if s.startswith(("-", "*", "•")):
                lines.append("• " + s.lstrip("-*• ").strip())
    if cur is not None and lines:
        result[cur] = "\n".join(lines)
    return result


async def _remote_changelog() -> dict[str, str]:
    """CHANGELOG.md из репозитория (default-ветка) — чтобы показать НОВЫЕ версии и их
    пункты ДО установки. Best-effort: приватный/недоступный репо → пусто, лента строится
    из локального CHANGELOG (офлайн-режим). Репо публичный (см. update-notifier)."""
    for branch in ("main", "master"):
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as cli:
                r = await cli.get(
                    f"https://raw.githubusercontent.com/{REPO}/{branch}/CHANGELOG.md"
                )
            if r.status_code == 200 and r.text.strip():
                parsed = _parse_changelog_text(r.text)
                if parsed:
                    return parsed
        except Exception:
            continue
    return {}


async def _github_releases() -> tuple[dict[str, str], dict[str, str]]:
    """Best-effort обогащение из GitHub: {ver_norm: url}, {ver_norm: date}.

    Никогда не бросает: приватный/недоступный/чужой репозиторий → пустые словари,
    и лента всё равно строится из локального CHANGELOG.md.
    """
    urls: dict[str, str] = {}
    dates: dict[str, str] = {}
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as cli:
            rel = await cli.get(
                f"https://api.github.com/repos/{REPO}/releases", params={"per_page": 50}
            )
            data = rel.json() if rel.status_code == 200 else []
            if isinstance(data, list):
                for r in data:
                    if not (isinstance(r, dict) and r.get("tag_name") and not r.get("draft")):
                        continue
                    key = _norm(r["tag_name"])
                    urls[key] = r.get("html_url") or ""
                    dates[key] = r.get("published_at") or r.get("created_at") or ""
    except Exception:
        pass
    return urls, dates


async def _fetch_items() -> list[dict[str, Any]]:
    # Мержим локальный (в образе, офлайн) и УДАЛЁННЫЙ CHANGELOG. Remote даёт НОВЫЕ версии
    # (ещё не установленные) и их пункты — чтобы показать «что изменится» ДО обновления;
    # при совпадении версии remote приоритетнее (свежее). Офлайн → только локальный.
    local = _local_changelog()
    remote = await _remote_changelog()
    changelog: dict[str, str] = {**local, **remote}
    urls, dates = await _github_releases()

    current = _parse(_local_version())
    versions = sorted(changelog, key=_parse, reverse=True)[:20]
    items: list[dict[str, Any]] = []
    for v in versions:
        label = f"v{v}"
        items.append({
            "version": label,
            "name": label,
            "date": dates.get(v) or None,
            "notes": changelog.get(v, ""),
            # installed=False → версия НОВЕЕ установленной (доступна, ещё не стоит).
            "installed": _parse(v) <= current,
            "url": urls.get(v) or f"https://github.com/{REPO}/releases/tag/{label}",
        })
    return items


@router.get("")
async def get_updates(_admin: AdminUser, force: bool = False) -> dict[str, Any]:
    now = time.time()
    # force=1 (кнопка «Проверить») — обходим кэш, чтобы новую версию видно было СРАЗУ.
    if not force and _cache["items"] is not None and now - _cache["at"] < _TTL:
        items = _cache["items"]
    else:
        try:
            items = await _fetch_items()
            _cache["items"] = items
            _cache["at"] = now
        except Exception:
            items = _cache["items"] or []

    items = sorted(items, key=lambda x: _parse(x["version"]), reverse=True)
    current = _local_version()
    latest = items[0]["version"] if items else None
    return {
        "current": current,
        "latest": latest,
        "update_available": bool(latest and _parse(latest) > _parse(current)),
        "repo": REPO,
        "items": items,
    }
