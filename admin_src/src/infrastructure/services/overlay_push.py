"""Web Push — браузерные push-уведомления PWA кабинета.

VAPID-ключи генерируются один раз и хранятся в assets/push_vapid.json (том
переживает пересоздание контейнера, ключи стабильны между рестартами). Отправка —
через pywebpush (ставится в образ через --target, см. Dockerfile). cryptography
уже есть в базе.

Мёртвые подписки (HTTP 404/410 от push-сервиса) вызывающий удаляет из таблицы
push_subscriptions — см. send_to_user().
"""

import base64
import json
import os
from pathlib import Path
from typing import Optional

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
VAPID_PATH = ASSETS_DIR / "push_vapid.json"


def _generate_vapid() -> dict:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    raw_pub = key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    public_key = base64.urlsafe_b64encode(raw_pub).rstrip(b"=").decode()
    return {"public_key": public_key, "private_key_pem": pem}


def get_vapid() -> dict:
    """VAPID-ключи из assets; при первом обращении генерирует и сохраняет."""
    try:
        if VAPID_PATH.exists():
            with VAPID_PATH.open(encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("public_key") and data.get("private_key_pem"):
                return data
    except Exception as exc:
        logger.warning(f"push: не смог прочитать VAPID ({exc}), генерирую заново")

    data = _generate_vapid()
    try:
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        with VAPID_PATH.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        logger.info("push: сгенерированы и сохранены VAPID-ключи")
    except Exception as exc:
        logger.error(f"push: не смог сохранить VAPID: {exc}")
    return data


def vapid_public_key() -> str:
    return get_vapid()["public_key"]


def _vapid_claims_sub() -> str:
    # sub для VAPID-клейма (mailto:/https:). Apple ОТКЛОНЯЕТ невалидный домен
    # (напр. .local) → BadJwtToken/403. Дефолт — валидный RFC-домен example.com;
    # переопределяется env PUSH_VAPID_SUB (напр. mailto:admin@ваш-домен).
    return os.environ.get("PUSH_VAPID_SUB") or "mailto:admin@example.com"


def send_web_push(subscription: dict, payload: dict) -> tuple[bool, Optional[int]]:
    """Отправить один push. Возврат (ok, status_code).

    ok=False и код 404/410 → подписка мертва, её надо удалить.
    """
    try:
        from py_vapid import Vapid01
        from pywebpush import WebPushException, webpush
    except Exception as exc:
        logger.error(f"push: pywebpush недоступен: {exc}")
        return False, None

    vapid = get_vapid()
    try:
        webpush(
            subscription_info=subscription,
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=Vapid01.from_pem(vapid["private_key_pem"].encode()),
            vapid_claims={"sub": _vapid_claims_sub()},
            timeout=10,
        )
        return True, 200
    except WebPushException as exc:
        code = exc.response.status_code if exc.response is not None else None
        if code not in (404, 410):
            logger.warning(f"push: отправка не удалась (HTTP {code}): {exc}")
        return False, code
    except Exception as exc:
        logger.warning(f"push: отправка не удалась: {exc}")
        return False, None


async def send_to_user(session: AsyncSession, user_id: int, payload: dict) -> int:
    """Отправить push на все устройства пользователя. Возврат — успешно доставлено.

    Мёртвые подписки (404/410) удаляет из push_subscriptions. Сессию НЕ коммитит
    сам — вызывающий делает commit (в кабинете — вручную, как принято в overlay).
    """
    rows = (
        await session.execute(
            text(
                "SELECT endpoint, p256dh, auth FROM push_subscriptions "
                "WHERE user_id = :uid"
            ),
            {"uid": user_id},
        )
    ).all()

    ok = 0
    dead: list[str] = []
    for endpoint, p256dh, auth in rows:
        sub = {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}}
        success, code = send_web_push(sub, payload)
        if success:
            ok += 1
        elif code in (404, 410):
            dead.append(endpoint)

    if dead:
        await session.execute(
            text("DELETE FROM push_subscriptions WHERE endpoint = ANY(:eps)"),
            {"eps": dead},
        )
    return ok


def _user_lang(user: object) -> str:
    """Двухбуквенный код языка пользователя (UserDto.language — enum/строка)."""
    lang = getattr(user, "language", None)
    lang = getattr(lang, "value", lang)
    return str(lang or "ru").lower()[:2]


# --- Standalone-отправка со своей сессией: для мест без AsyncSession в аргументах
# (врезка web-push в базовый notification.py и в overlay support.py). Ленивый
# общий sessionmaker поверх DSN приложения. Все функции best-effort. ---

_sessionmaker = None


def _get_sessionmaker():
    global _sessionmaker
    if _sessionmaker is None:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from src.core.config import AppConfig

        engine = create_async_engine(AppConfig.get().database.dsn)
        _sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    return _sessionmaker


async def push_user_standalone(user_id: int, payload: dict) -> int:
    """Web-push пользователю по user_id со своей сессией. НИКОГДА не бросает."""
    try:
        async with _get_sessionmaker()() as session:
            sent = await send_to_user(session, user_id, payload)
            await session.commit()
            return sent
    except Exception as exc:  # noqa: BLE001 — push не должен ронять уведомления
        logger.debug(f"push: push_user_standalone user={user_id} не удалось: {exc}")
        return 0


async def _record_admin_notification(session: AsyncSession, payload: dict) -> None:
    """Пишет уведомление в историю (admin_notifications) + чистит старые.

    Best-effort: если таблицы ещё нет (старый деплой) — молча пропускаем, чтобы
    не сорвать саму отправку push. Храним последние 500 записей.
    """
    try:
        await session.execute(
            text(
                "INSERT INTO admin_notifications (title, body, url) "
                "VALUES (:t, :b, :u)"
            ),
            {
                "t": str(payload.get("title") or "")[:200],
                "b": str(payload.get("body") or ""),
                "u": str(payload.get("url") or "/")[:500],
            },
        )
        await session.execute(
            text(
                "DELETE FROM admin_notifications WHERE id <= "
                "(SELECT id FROM admin_notifications ORDER BY id DESC OFFSET 500 LIMIT 1)"
            )
        )
    except Exception as exc:  # noqa: BLE001 — история не должна ломать push
        logger.debug(f"push: не смог записать историю уведомления: {exc}")


async def push_admins_standalone(payload: dict) -> int:
    """Web-push всем админам (OWNER/DEV/ADMIN) с push-подписками. Best-effort.

    Помимо отправки пишет уведомление в историю (admin_notifications) — даже если
    ни одного устройства не подписано, чтобы владелец видел ленту в админке.
    """
    try:
        async with _get_sessionmaker()() as session:
            await _record_admin_notification(session, payload)
            rows = (
                await session.execute(
                    text(
                        "SELECT DISTINCT p.user_id FROM push_subscriptions p "
                        "JOIN users u ON u.id = p.user_id "
                        "WHERE u.role::text IN ('OWNER', 'DEV', 'ADMIN')"
                    )
                )
            ).all()
            total = 0
            for (uid,) in rows:
                total += await send_to_user(session, uid, payload)
            await session.commit()
            return total
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"push: push_admins_standalone не удалось: {exc}")
        return 0


async def notify_user_push(
    session: AsyncSession,
    user: object,
    messages: dict,
    *,
    url: str = "/",
    tag: Optional[str] = None,
    **fmt: object,
) -> int:
    """Best-effort web-push пользователю по его языку. НИКОГДА не бросает наружу.

    `messages` = {lang: (title, body_template)}, фолбэк — ru. Плейсхолдеры body
    подставляются из **fmt. Коммитит сессию сам (снятие мёртвых подписок) — во
    всех точках вызова к этому моменту незакоммиченной бизнес-логики нет.
    Используется для событийных push (реферал, автопродление и т.п.) вдобавок к
    штатным TG/email-уведомлениям базового образа.
    """
    try:
        l = _user_lang(user)
        title, body_tpl = messages.get(l) or messages["ru"]
        payload = {"title": title, "body": body_tpl.format(**fmt), "url": url}
        if tag:
            payload["tag"] = tag
        sent = await send_to_user(session, getattr(user, "id"), payload)
        await session.commit()
        return sent
    except Exception as exc:  # noqa: BLE001 — push не должен ломать оплату/рефералку
        logger.warning(
            f"push: notify_user_push не удалось (user={getattr(user, 'id', '?')}): {exc}"
        )
        return 0
