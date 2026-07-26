"""Крипто-ссылки подписки: непрозрачный алиас вместо реального sub-URL.

Реальная ссылка подписки (`https://sub.<домен>/<token>`) не должна светиться
клиенту/приложению. Вместо неё кабинет отдаёт алиас `https://<кабинет>/c/<opaque>`,
где `<opaque>` = AES-SIV(реальный url) — ДЕТЕРМИНИРОВАННЫЙ (стабильный) крипто-ключ,
реверсивный ТОЛЬКО на сервере (ключ выведен из APP_JWT_SECRET). Эндпоинт `/c/{alias}`
серверно reverse-проксирует запрос на реальный sub-хост (НЕ редирект — реальный хост
не покидает сервер) и стримит ответ Remnawave назад с его заголовками.

Гейт: тумблер `crypto_links_enabled` в админке (branding.json). Дефолт — выключено.

Топология-независимо (работает и когда кабинет на ОТДЕЛЬНОМ сервере): reverse-proxy
ходит на ПУБЛИЧНЫЙ sub-URL (через Caddy, который удовлетворяет ProxyCheckMiddleware
sub-page — прямой запрос к внутреннему :3010 он отбивает). Бэкенд достаёт sub-хост по
интернету откуда угодно; кабинет-nginx роутит `/c/` на тот же бэкенд, что и `/api/`.

Безопасность: алиас — bearer-capability (как и сам sub-URL публичен), потому эндпоинт
без JWT (VPN-приложение не шлёт куку кабинета). AES-SIV — AEAD: подделать алиас без
ключа нельзя, битый/чужой алиас не расшифруется → 404, значит SSRF через инъекцию
произвольного URL невозможен (декодируются только наши же сгенерированные алиасы).
"""

import base64
import hashlib
import os
import re

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from loguru import logger

from .appearance import load_branding

router = APIRouter(prefix="/c", tags=["Public - Crypto Link"])

# Заголовки ответа Remnawave, пробрасываемые VPN-приложению (без них клиенты не
# показывают трафик/название/интервал обновления). ВНИМАНИЕ: два из них требуют
# ПЕРЕЗАПИСИ в resolve_alias, иначе утекают приватные данные — profile-web-page-url
# несёт реальный sub-URL (→ гоним через alias_url), content-disposition несёт
# filename=rs_<telegram_id> (→ обезличиваем). Content-length/encoding НЕ проксируем —
# httpx уже распаковал тело, старые значения не совпали бы с контентом.
_PASS_HEADERS = frozenset(
    {
        "content-type",
        "content-disposition",
        "subscription-userinfo",
        "profile-title",
        "profile-update-interval",
        "announce",
        "profile-web-page-url",
        "support-url",
        # Маркеры HWID-гейта: клиент по ним понимает, что упёрся в лимит устройств
        # либо что приложение не поддерживает HWID.
        "x-hwid-not-supported",
        "x-hwid-max-devices-reached",
        "x-hwid-active",
        "x-hwid-limit",
    }
)

# Заголовки запроса, которые НЕЛЬЗЯ форвардить наверх: hop-by-hop (RFC 7230) плюс
# host (его выставляет httpx под реальный sub-хост), cookie (куки кабинета наверх не
# отдаём) и accept-encoding (httpx сам договаривается о сжатии).
_SKIP_REQUEST_HEADERS = frozenset(
    {
        "host",
        "cookie",
        "accept-encoding",
        "content-length",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)


def _key() -> bytes:
    """32-байтный ключ AES-SIV, выведенный из APP_JWT_SECRET (один бэкенд → один ключ,
    поэтому алиас, сгенерированный в /subscription/current, резолвится здесь же)."""
    secret = (os.environ.get("APP_JWT_SECRET") or "").encode("utf-8")
    return hashlib.sha256(b"remnashop:sub-alias:v1:" + secret).digest()


def encode_alias(real_url: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESSIV

    ct = AESSIV(_key()).encrypt(real_url.encode("utf-8"), [])
    return base64.urlsafe_b64encode(ct).decode("ascii").rstrip("=")


def decode_alias(alias: str) -> str | None:
    from cryptography.hazmat.primitives.ciphers.aead import AESSIV

    try:
        pad = "=" * (-len(alias) % 4)
        ct = base64.urlsafe_b64decode(alias + pad)
        url = AESSIV(_key()).decrypt(ct, []).decode("utf-8")
    except Exception:
        return None
    # Defense-in-depth: только https (AEAD и так исключает подделку/инъекцию URL).
    if not url.startswith("https://"):
        return None
    return url


def _cabinet_base() -> str:
    return (os.environ.get("WEB_CABINET_URL") or "").strip().rstrip("/")


def crypto_links_enabled() -> bool:
    try:
        return bool(load_branding().get("crypto_links_enabled"))
    except Exception:
        return False


def alias_url(real_url: str) -> str:
    """Реальный sub-URL → алиас `https://<кабинет>/c/<opaque>`. Домен берётся из
    WEB_CABINET_URL (per-install — у каждой установки свой), поэтому привязка
    динамическая: колеги, ставящие кабинет с гита, получают алиас на своём домене
    без правок кода."""
    base = _cabinet_base()
    if not real_url:
        return real_url
    if not base:
        # Тумблер включён, но домен кабинета не задан → не можем построить алиас и
        # молча отдаём РЕАЛЬНУЮ ссылку (fail-open, чтобы не сломать подключение).
        # Громко логируем: иначе оператор думает, что ссылка скрыта, а она утекает.
        logger.warning(
            "crypto_links_enabled=on, но WEB_CABINET_URL пуст → отдаю РЕАЛЬНЫЙ sub-URL "
            "(алиас не построить). Задайте WEB_CABINET_URL в .env."
        )
        return real_url
    return f"{base}/c/{encode_alias(real_url)}"


def maybe_alias_url(real_url: str) -> str:
    """Алиас, если тумблер включён; иначе реальный url (текущее поведение)."""
    return alias_url(real_url) if crypto_links_enabled() else real_url


# Внутренний remna-username = rs_<telegram_id> / rs_web_<user_id> (см. dto/user.py).
# Remnawave кладёт его в filename ответа → раскрывает Telegram-ID держателю алиаса.
_RS_USERNAME_RE = re.compile(r"^rs_(?:web_)?\d+$")


def _scrub_content_disposition(value: str) -> str:
    """Обезличивает filename, если это внутренний username (раскрывает TG-ID). Кастомные
    имена профиля в панели не трогаем — их оператор задал осознанно."""

    def _repl(m: "re.Match[str]") -> str:
        name = (m.group("q") or m.group("u") or "").strip()
        return 'filename="subscription"' if _RS_USERNAME_RE.match(name) else m.group(0)

    return re.sub(
        r'filename\s*=\s*(?:"(?P<q>[^"]*)"|(?P<u>[^;]+))',
        _repl,
        value,
        flags=re.IGNORECASE,
    )


@router.get("/{alias}")
async def resolve_alias(alias: str, request: Request) -> Response:
    """Reverse-proxy: алиас → реальный sub-URL → серверный fetch → ответ клиенту.

    Реальный хост НИКОГДА не уходит клиенту (не редирект). UA/Accept форвардим —
    Remnawave по UA отдаёт нужный формат (base64/clash/plain).
    """
    real_url = decode_alias(alias)
    if not real_url:
        return Response(status_code=404)

    # Форвардим ВСЕ заголовки клиента, кроме hop-by-hop/host/cookie. Критично для
    # HWID: Remnawave требует x-hwid (+ x-device-os/x-ver-os/x-device-model), иначе
    # при включённом лимите устройств отдаёт заглушку «App not supported» вместо
    # конфига. Прозрачный проброс заодно покрывает будущие заголовки панели.
    ua = request.headers.get("user-agent", "")
    # Заголовки форвардим БАЙТАМИ, ровно как пришли с провода. Иначе httpx кодирует
    # значения в ASCII и падает с UnicodeEncodeError на не-ASCII заголовке — а Happ
    # шлёт X-Device-Model с именем устройства пользователя, которое бывает
    # кириллическим («ноут_x86_64»). Раньше это выливалось в 502 и подписка не
    # открывалась. Starlette отдаёт заголовки latin-1-строками, поэтому обратное
    # .encode("latin-1") возвращает исходные байты без искажения.
    fwd: list[tuple[bytes, bytes]] = []
    for k, v in request.headers.items():
        if k.lower() in _SKIP_REQUEST_HEADERS:
            continue
        try:
            fwd.append((k.encode("latin-1"), v.encode("latin-1")))
        except UnicodeEncodeError:
            continue
    if not any(k.lower() == b"accept" for k, _ in fwd):
        fwd.append((b"accept", b"*/*"))
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            up = await client.get(real_url, headers=fwd)
    except Exception as e:
        logger.warning(f"sub-alias reverse-proxy failed: {type(e).__name__}")
        return Response(status_code=502)

    # Человек, открывший алиас в браузере, получил бы сломанную HTML-обёртку Remnawave
    # (сырой sub-URL для браузера отдаёт 5xx) — уводим в кабинет. VPN-клиентам (не
    # Mozilla) отдаём статус как есть.
    if up.status_code >= 500 and "mozilla" in ua.lower():
        base = _cabinet_base()
        if base:
            return RedirectResponse(base, status_code=302)

    headers: dict[str, str] = {}
    for k, v in up.headers.items():
        lk = k.lower()
        if lk not in _PASS_HEADERS:
            continue
        if lk == "profile-web-page-url" and v:
            # Панель кладёт сюда ПОЛНЫЙ реальный sub-URL (https://sub.…/<token>). Без
            # перезаписи он утёк бы клиенту мимо алиаса (и Happ его персистит как
            # «страницу подписки») → вся фича обходится. alias_url детерминирован
            # (AES-SIV) → тот же /c/<opaque>, что и в пути; реальный хост не покидает сервер.
            v = alias_url(v)
        elif lk == "content-disposition" and v:
            v = _scrub_content_disposition(v)
        headers[k] = v
    return Response(
        content=up.content,
        status_code=up.status_code,
        headers=headers,
        media_type=up.headers.get("content-type"),
    )
