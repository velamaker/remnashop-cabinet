"""Детект абьюза триала (overlay).

Ищем группы аккаунтов, у которых совпадают идентифицирующие сигналы из нашей БД,
и которые при этом успели воспользоваться пробником (is_trial_available=false) —
то есть похоже на «мультиаккаунт ради нескольких бесплатных триалов».

Сигналы:
  • ip        — несколько аккаунтов заходили в кабинет с одного IP (login_events);
  • hwid      — один физический девайс (HWID) у разных аккаунтов — снимок с панели
                кладёт периодическая задача abuse_hwid в таблицу hwid_devices;
  • email     — «одинаковый» email с учётом gmail-трюков (точки, +алиасы, домен);
  • referral  — приглашённый по рефералке заходил с того же IP, что и пригласивший
                (само-реферал ради бонуса).

Никаких автодействий: только показываем группы и даём админу кнопки вручную
(заблокировать / снять триал). См. users.py block + trial.
"""

from typing import Any

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ._common import AdminUser

router = APIRouter(prefix="/abuse", tags=["Admin - Abuse"])

# Telegram id растёт со временем создания аккаунта — грубый порог «свежий аккаунт»
# (заведён недавно, типичный признак одноразовой регистрации под триал). Это лишь
# усилитель серьёзности внутри группы, НЕ самостоятельный сигнал.
_YOUNG_TG_ID = 7_500_000_000

_GMAIL_DOMAINS = {"gmail.com", "googlemail.com"}


def _normalize_email(email: str | None) -> str | None:
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


@router.get("/trials")
@inject
async def trial_abuse(
    _admin: AdminUser,
    session: FromDishka[AsyncSession],
    min_accounts: int = Query(2, ge=2, le=20),
    only_trial: bool = Query(True, description="показывать только группы, где ≥2 аккаунтов уже взяли триал"),
) -> dict[str, Any]:
    # Только обычные пользователи — персонал (OWNER/DEV/ADMIN/PREVIEW) не абьюзер,
    # а их тестовые аккаунты с одного IP давали бы ложные группы.
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

    # user_id ↔ IP (различающиеся пары), только непустые IP.
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
        ip_to_users.setdefault(ip, set()).add(uid)
        user_to_ips.setdefault(uid, set()).add(ip)

    # user_id ↔ HWID (снимок из периодической задачи abuse_hwid). Таблицы может не
    # быть на самом первом старте до её создания — тогда просто без HWID-сигнала.
    hwid_to_users: dict[str, set[int]] = {}
    try:
        hwid_rows = (
            await session.execute(text("SELECT hwid, user_id FROM hwid_devices"))
        ).all()
        for hwid, uid in hwid_rows:
            hwid_to_users.setdefault(hwid, set()).add(uid)
    except Exception:
        hwid_to_users = {}

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
    # Самый сильный сигнал: IP меняется (моб. интернет/VPN), а устройство — нет.
    for hwid, uids in hwid_to_users.items():
        accounts = [_account_view(users[u]) for u in uids if u in users]
        if not _passes(accounts):
            continue
        clusters.append(
            {
                "signal": "hwid",
                "key": hwid[:16] + "…" if len(hwid) > 18 else hwid,
                "accounts": sorted(accounts, key=lambda a: a["id"]),
                # Общий девайс — сильнее общего IP: поднимаем медиану до high при 2+ триалах.
                "severity": _severity(accounts, deliberate=sum(1 for a in accounts if a["trial_used"]) >= 2),
            }
        )

    # ── Сигнал 3: одинаковый email (с gmail-нормализацией) ─────────────────────
    email_to_users: dict[str, set[int]] = {}
    for u in users.values():
        norm = _normalize_email(u["email"])
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

    return {"clusters": clusters, "total": len(clusters)}
