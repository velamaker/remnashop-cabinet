"""Лента обновлений: тянет релизы Vela с GitHub (releases → фолбэк на теги).

Работает у ЛЮБОГО, кто поставил кабинет: данные из публичного GitHub API
репозитория (не захардкожены). Кэш в памяти, чтобы не упираться в лимиты API.
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

REPO = (os.environ.get("UPDATE_REPO") or "alexdsndr161rus2015-maker/remnashop-cabinet").strip()
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


def _parse(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", (v or "").lstrip("vV"))
    return tuple(int(x) for x in nums) or (0,)


_SKIP_RE = re.compile(r"^(merge\b|bump version|v?\d+(\.\d+)+\s*$)", re.IGNORECASE)


async def _commit_notes(cli: "httpx.AsyncClient", base: str, head: str) -> tuple[str, Optional[str]]:
    """Список изменений между тегами (первые строки коммитов) + дата head."""
    try:
        r = await cli.get(f"https://api.github.com/repos/{REPO}/compare/{base}...{head}")
        if r.status_code != 200:
            return "", None
        commits = r.json().get("commits", []) or []
    except Exception:
        return "", None
    lines: list[str] = []
    date: Optional[str] = None
    for c in commits:
        cm = (c.get("commit") or {})
        date = (cm.get("committer") or {}).get("date") or date
        subject = (cm.get("message") or "").strip().splitlines()[0].strip()
        if not subject or _SKIP_RE.match(subject):
            continue
        # Убираем префикс «vX.Y: » — версия и так в заголовке карточки.
        subject = re.sub(r"^v?\d+(\.\d+)+:\s*", "", subject)
        lines.append(f"• {subject}")
    return "\n".join(lines[:25]), date


async def _fetch_items() -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=10) as cli:
        # 1) Настоящие релизы (если владелец заполнил описания) — берём их notes.
        rel = await cli.get(
            f"https://api.github.com/repos/{REPO}/releases", params={"per_page": 30}
        )
        rel_data = rel.json() if rel.status_code == 200 else []
        rel_notes: dict[str, str] = {}
        rel_url: dict[str, str] = {}
        if isinstance(rel_data, list):
            for r in rel_data:
                if isinstance(r, dict) and r.get("tag_name") and not r.get("draft"):
                    rel_notes[r["tag_name"]] = (r.get("body") or "").strip()
                    rel_url[r["tag_name"]] = r.get("html_url") or ""

        # 2) Теги (версии) по убыванию.
        tg = await cli.get(f"https://api.github.com/repos/{REPO}/tags", params={"per_page": 30})
        raw = tg.json() if tg.status_code == 200 else []
        names = [
            t["name"] for t in raw
            if isinstance(t, dict) and t.get("name")
            and re.match(r"^v?\d+(\.\d+)+$", t["name"].strip())
        ]
        names = sorted(names, key=_parse, reverse=True)[:12]

        changelog = _local_changelog()

        items: list[dict[str, Any]] = []
        for i, name in enumerate(names):
            # Приоритет: курируемый CHANGELOG.md → описание релиза на GitHub →
            # авто-список из коммитов (запасной вариант).
            notes = changelog.get(_norm(name), "") or rel_notes.get(name, "")
            date = None
            if not notes and i + 1 < len(names):
                notes, date = await _commit_notes(cli, names[i + 1], name)
            items.append({
                "version": name,
                "name": name,
                "date": date,
                "notes": notes,
                "url": rel_url.get(name) or f"https://github.com/{REPO}/releases/tag/{name}",
            })
        return items


@router.get("")
async def get_updates(_admin: AdminUser) -> dict[str, Any]:
    now = time.time()
    if _cache["items"] is not None and now - _cache["at"] < _TTL:
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
