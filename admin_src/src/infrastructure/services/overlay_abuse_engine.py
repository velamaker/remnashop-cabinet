"""Движок детекта абьюза триала (общий для web-админки и бота).

Ищем группы аккаунтов с совпадающими идентифицирующими сигналами из нашей БД,
которые при этом успели воспользоваться пробником — похоже на «мультиаккаунт
ради нескольких бесплатных триалов». Никаких автодействий: только группы;
действия (блок / снять триал) — вручную.

Раньше вся логика жила в web/endpoints/admin/abuse.py. Вынесена сюда, чтобы её
мог переиспользовать раздел абьюза в Telegram-боте (Фаза 3), не дублируя SQL.
Слой infrastructure намеренно НЕ импортирует web/fastapi — чтобы движок можно
было безопасно тянуть в процесс бота. Пути к assets вычисляем сами.

Сигналы:
  • ip        — несколько аккаунтов заходили в кабинет с одного IP (login_events);
  • hwid      — один физический девайс (HWID) у разных аккаунтов (снимок abuse_hwid);
  • email     — «одинаковый» email с учётом gmail-трюков (точки, +алиасы, домен);
  • referral  — приглашённый заходил с того же IP, что и пригласивший (само-реферал).
"""

import json
import os
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Каталог assets вычисляем автономно (тот же дефолт, что и в web-слое), чтобы не
# импортировать web.endpoints.public.appearance в процесс бота.
ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))

# IP наших нод исключаем из сигнала «общий IP»: под нашим VPN source-IP логина =
# exit-IP ноды, иначе «туннельные» логины слипаются в ложную группу. Источники:
# node_health.json (пишет taskiq node_health.py) + ручной abuse_ignore_ips.json.
_NODE_HEALTH_PATH = ASSETS_DIR / "node_health.json"
_IGNORE_IPS_PATH = ASSETS_DIR / "abuse_ignore_ips.json"

# Telegram id растёт со временем — грубый порог «свежий аккаунт» (усилитель severity).
_YOUNG_TG_ID = 7_500_000_000

_GMAIL_DOMAINS = {"gmail.com", "googlemail.com"}


def excluded_ips() -> set[str]:
    """IP наших нод + ручной список — их не учитываем в сигналах по IP."""
    ips: set[str] = set()
    try:
        data = json.loads(_NODE_HEALTH_PATH.read_text(encoding="utf-8"))
        for v in data.values():
            if isinstance(v, dict) and isinstance(v.get("ip"), str) and v["ip"].strip():
                ips.add(v["ip"].strip())
    except Exception:
        pass
    try:
        manual = json.loads(_IGNORE_IPS_PATH.read_text(encoding="utf-8"))
        if isinstance(manual, list):
            ips.update(str(x).strip() for x in manual if str(x).strip())
    except Exception:
        pass
    return ips


def normalize_email(email: str | None) -> str | None:
    """Приводит email к «фактическому ящику»: gmail игнорирует точки и +алиасы,
    у остальных доменов срезаем только +алиас. Так a.b+x@gmail и ab@gmail = одно.
    """
    if not email or "@" not in email:
        return None
    local, _, domain = email.strip().lower().partition("@")
    local = local.split("+", 1)[0]
    if domain in _GMAIL_DOMAINS:
        local = local.replace(".", "")
        domain = "gmail.com"
    if not local:
        return None
    return f"{local}@{domain}"


def _account_view(u: dict[str, Any]) -> dict[str, Any]:
    tg = u["telegram_id"]
    return {
        "id": u["id"],
        "name": u["name"],
        "email": u["email"],
        "telegram_id": tg,
        "username": u["username"],
        "created_at": u["created_at"].isoformat() if u["created_at"] else None,
        "is_blocked": u["is_blocked"],
        "is_trial_available": u["is_trial_available"],
        "trial_used": not u["is_trial_available"],
        "young_tg": bool(tg and tg >= _YOUNG_TG_ID),
    }


def _severity(accounts: list[dict[str, Any]], *, deliberate: bool = False) -> str:
    """high — явный абьюз; medium — стоит посмотреть; low — слабый сигнал."""
    trials = sum(1 for a in accounts if a["trial_used"])
    if deliberate or trials >= 3 or (trials >= 2 and len(accounts) >= 3):
        return "high"
    if trials >= 2:
        return "medium"
    return "low"


async def detect_abuse_clusters(
    session: AsyncSession,
    *,
    min_accounts: int = 2,
    only_trial: bool = True,
) -> dict[str, Any]:
    """Возвращает {clusters, total, excluded_node_ips}. Логика без изменений —
    ровно то, что раньше жило в web-эндпоинте /abuse/trials."""
    # Только обычные пользователи — персонал не абьюзер, их тестовые аккаунты
    # с одного IP давали бы ложные группы.
    rows = (
        await session.execute(
            text(
                "SELECT id, name, email, telegram_id, username, created_at, "
                "       is_blocked, is_trial_available "
                "FROM users WHERE role = 'USER'"
            )
        )
    ).mappings().all()
    users: dict[int, dict[str, Any]] = {r["id"]: dict(r) for r in rows}

    # user_id ↔ IP (различающиеся пары), только непустые IP. IP наших нод исключаем
    # здесь один раз — это чистит и сигнал «общий IP», и реферальный.
    excluded = excluded_ips()
    ip_pairs = (
        await session.execute(
            text(
                "SELECT DISTINCT user_id, ip FROM login_events "
                "WHERE ip IS NOT NULL AND ip <> ''"
            )
        )
    ).all()
    ip_to_users: dict[str, set[int]] = {}
    user_to_ips: dict[int, set[str]] = {}
    for uid, ip in ip_pairs:
        if ip in excluded:
            continue
        ip_to_users.setdefault(ip, set()).add(uid)
        user_to_ips.setdefault(uid, set()).add(ip)

    # user_id ↔ HWID (снимок abuse_hwid). Таблицы может не быть на самом первом
    # старте до её создания — тогда просто без HWID-сигнала.
    hwid_to_users: dict[str, set[int]] = {}
    hwid_device: dict[str, str] = {}
    try:
        hwid_rows = (
            await session.execute(
                text("SELECT hwid, user_id, device_model, platform FROM hwid_devices")
            )
        ).all()
        for hwid, uid, model, platform in hwid_rows:
            hwid_to_users.setdefault(hwid, set()).add(uid)
            if hwid not in hwid_device and (model or platform):
                label = model or ""
                if platform and platform != model:
                    label = f"{label} ({platform})" if label else platform
                hwid_device[hwid] = label
    except Exception:
        hwid_to_users = {}
        hwid_device = {}

    clusters: list[dict[str, Any]] = []

    def _passes(accounts: list[dict[str, Any]], deliberate: bool = False) -> bool:
        if len(accounts) < min_accounts:
            return False
        if only_trial and not deliberate:
            return sum(1 for a in accounts if a["trial_used"]) >= 2
        return True

    # ── Сигнал 1: общий IP ────────────────────────────────────────────────────
    for ip, uids in ip_to_users.items():
        accounts = [_account_view(users[u]) for u in uids if u in users]
        if not _passes(accounts):
            continue
        clusters.append(
            {
                "signal": "ip",
                "key": ip,
                "accounts": sorted(accounts, key=lambda a: a["id"]),
                "severity": _severity(accounts),
            }
        )

    # ── Сигнал 2: общий HWID (один физический девайс у разных аккаунтов) ───────
    for hwid, uids in hwid_to_users.items():
        accounts = [_account_view(users[u]) for u in uids if u in users]
        if not _passes(accounts):
            continue
        short = hwid[:16] + "…" if len(hwid) > 18 else hwid
        device = hwid_device.get(hwid)
        clusters.append(
            {
                "signal": "hwid",
                "key": f"{short} · {device}" if device else short,
                "device": device,
                "accounts": sorted(accounts, key=lambda a: a["id"]),
                "severity": _severity(accounts, deliberate=sum(1 for a in accounts if a["trial_used"]) >= 2),
            }
        )

    # ── Сигнал 3: одинаковый email (с gmail-нормализацией) ─────────────────────
    email_to_users: dict[str, set[int]] = {}
    for u in users.values():
        norm = normalize_email(u["email"])
        if norm:
            email_to_users.setdefault(norm, set()).add(u["id"])
    for norm, uids in email_to_users.items():
        if len(uids) < min_accounts:
            continue
        accounts = [_account_view(users[u]) for u in uids]
        if not _passes(accounts):
            continue
        clusters.append(
            {
                "signal": "email",
                "key": norm,
                "accounts": sorted(accounts, key=lambda a: a["id"]),
                "severity": _severity(accounts),
            }
        )

    # ── Сигнал 4: само-реферал (реферер и приглашённый с общего IP) ────────────
    ref_rows = (
        await session.execute(text("SELECT referrer_id, referred_id FROM referrals"))
    ).all()
    referrer_to_referred: dict[int, list[int]] = {}
    for referrer_id, referred_id in ref_rows:
        shared = user_to_ips.get(referrer_id, set()) & user_to_ips.get(referred_id, set())
        if shared:
            referrer_to_referred.setdefault(referrer_id, []).append(referred_id)
    for referrer_id, referred_ids in referrer_to_referred.items():
        member_ids = [referrer_id, *referred_ids]
        accounts = [_account_view(users[u]) for u in member_ids if u in users]
        if not _passes(accounts, deliberate=True):
            continue
        clusters.append(
            {
                "signal": "referral",
                "key": f"referrer #{referrer_id}",
                "accounts": accounts,
                "severity": _severity(accounts, deliberate=True),
            }
        )

    order = {"high": 0, "medium": 1, "low": 2}
    clusters.sort(key=lambda c: (order.get(c["severity"], 3), -len(c["accounts"])))

    return {
        "clusters": clusters,
        "total": len(clusters),
        "excluded_node_ips": len(excluded),
    }
