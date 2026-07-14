"""Админ: настройки подписки в приложении (Happ и др. клиенты).

Это НЕ наш json-конфиг, а настройки самой панели Remnawave
(`/api/subscription-settings`): заголовок профиля, ссылка поддержки, объявление
Happ, ссылка на конфиг маршрутизации, доп. заголовки ответа подписки.
Приложение читает их при импорте ссылки — отсюда «брендинг и роутинг
подтягиваются в Happ».

Раздел прав — «settings» (см. permissions.py).
"""

import base64
import re
from typing import Any, Optional

import httpx
from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import json

from src.application.common import Remnawave
from src.web.endpoints.public.appearance import resolve_brand_name

from ._common import AdminUser

router = APIRouter(prefix="/subscription-app", tags=["Admin - Subscription app"])

# Happ ждёт в заголовке routing САМ deep-link (happ://routing/onadd/<base64 JSON>),
# а не ссылку на файл с ним. Ссылку на файл (github raw и т.п.) резолвим сами.
_ROUTING_DEEPLINK_RE = re.compile(r"happ://routing/\S+")

# Happ обрезает объявление до 200 символов, заголовок профиля — до 25.
HAPP_ANNOUNCE_MAX = 200
PROFILE_TITLE_MAX = 25

# Поля панели, которые отдаём/принимаем. Остальное (шаблоны, response_rules,
# hwid) не трогаем — их редактируют в самой панели.
_FIELDS = (
    "profile_title",
    "support_link",
    "profile_update_interval",
    "is_profile_webpage_url_enabled",
    "happ_announce",
    "happ_routing",
    "custom_response_headers",
)


class SubscriptionAppUpdate(BaseModel):
    profile_title: Optional[str] = Field(default=None, max_length=PROFILE_TITLE_MAX)
    support_link: Optional[str] = None
    profile_update_interval: Optional[int] = Field(default=None, ge=1, le=168)
    is_profile_webpage_url_enabled: Optional[bool] = None
    happ_announce: Optional[str] = Field(default=None, max_length=HAPP_ANNOUNCE_MAX)
    happ_routing: Optional[str] = None
    custom_response_headers: Optional[dict[str, str]] = None


def _sdk(remnawave: Remnawave):
    if hasattr(remnawave, "sdk"):
        return remnawave.sdk  # type: ignore[attr-defined]
    raise HTTPException(status_code=500, detail="RemnaWave SDK недоступен")


def _header_display(value: str) -> str:
    """Обратно к человекочитаемому: base64:… → текст (для формы в админке)."""
    if not value.startswith("base64:"):
        return value
    try:
        return base64.b64decode(value[len("base64:"):]).decode("utf-8")
    except Exception:
        return value


def _to_dict(settings: Any) -> dict[str, Any]:
    data = {f: getattr(settings, f, None) for f in _FIELDS}
    headers = data.get("custom_response_headers")
    if isinstance(headers, dict):
        data["custom_response_headers"] = {k: _header_display(v) for k, v in headers.items()}
    return data


def _minify_json(value: str) -> str:
    """JSON-значение (тема) — одной строкой без переносов: заголовок их не переживёт."""
    stripped = value.strip()
    if not stripped.startswith("{"):
        return value
    try:
        return json.dumps(json.loads(stripped), separators=(",", ":"), ensure_ascii=False)
    except Exception:
        return value


def _header_safe(value: str) -> str:
    """Значение, которое можно поставить в HTTP-заголовок.

    Панель отдаёт custom-заголовки как есть, а Node роняет ответ на любом символе
    вне latin-1 (`ERR_INVALID_CHAR`) — то есть кириллица в плашке = 502 на ВСЕХ
    ссылках подписки. Поэтому не-latin1 кодируем в base64: — формат, который Happ
    понимает. (Собственные поля панели, напр. announce, она кодирует сама.)
    """
    try:
        value.encode("latin-1")
        return value
    except UnicodeEncodeError:
        return "base64:" + base64.b64encode(value.encode("utf-8")).decode("ascii")


async def _resolve_routing(value: str) -> str:
    """Ссылка на файл с deep-link → сам deep-link. Готовый deep-link — как есть.

    GitHub-страницу (/blob/) тихо переводим в raw: по обычной ссылке отдаётся HTML,
    в котором deep-link не найти.
    """
    value = value.strip()
    if value.startswith("happ://"):
        return value
    if not value.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail="Нужна ссылка на конфиг (http/https) или готовый deep-link happ://routing/…",
        )

    url = value
    if "github.com" in url and "/blob/" in url:
        url = url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/", 1)

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            text = resp.text
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Не удалось скачать конфиг маршрутизации: {e}")

    match = _ROUTING_DEEPLINK_RE.search(text)
    if not match:
        raise HTTPException(
            status_code=400,
            detail=(
                "По ссылке нет deep-link вида happ://routing/… — приложение такой конфиг "
                "не поймёт. Соберите правила в конструкторе Happ Routing Builder и вставьте "
                "полученный happ://routing/onadd/… сюда."
            ),
        )
    return match.group(0)


def _default_routing_profile(brand: str) -> str:
    """Базовый профиль маршрутизации: всё в туннель, РФ-ресурсы — напрямую.

    Российские сайты, банки и госуслуги из-за рубежа часто не открываются, поэтому
    ходят мимо VPN. Категории берутся из geo-файлов, которые приложение качает само
    (свои Geoipurl/Geositeurl НЕ задаём: крупные сборки CDN не отдаёт, а телефону
    незачем тянуть десятки мегабайт).
    """
    profile = {
        "Name": brand[:25],
        "GlobalProxy": "true",
        "UseChunkFiles": "false",
        "RemoteDns": "8.8.8.8",
        "RemoteDNSType": "DoH",
        "RemoteDNSDomain": "https://8.8.8.8/dns-query",
        "RemoteDNSIP": "8.8.8.8",
        "DomesticDns": "77.88.8.8",
        "DomesticDNSType": "DoH",
        "DomesticDNSDomain": "https://77.88.8.8/dns-query",
        "DomesticDNSIP": "77.88.8.8",
        "DirectSites": [
            "geosite:category-ru",
            "geosite:category-gov-ru",
            "geosite:category-bank-ru",
            "geosite:private",
        ],
        "DirectIp": ["geoip:ru", "geoip:private"],
        "ProxySites": [],
        "ProxyIp": [],
        "BlockSites": [],
        "BlockIp": [],
        "DnsHosts": {"lkfl2.nalog.ru": "213.24.64.175", "lknpd.nalog.ru": "213.24.64.181"},
        "RouteOrder": "block-proxy-direct",
        "DomainStrategy": "IPIfNonMatch",
        "FakeDNS": "false",
    }
    payload = base64.b64encode(json.dumps(profile, ensure_ascii=False).encode("utf-8")).decode("ascii")
    return "happ://routing/onadd/" + payload


@router.post("/routing/default")
async def build_default_routing(_admin: AdminUser) -> dict[str, str]:
    """Готовый deep-link базового профиля — фронт подставляет его в поле routing."""
    return {"routing": _default_routing_profile(resolve_brand_name())}


@router.get("")
@inject
async def get_subscription_app(
    _admin: AdminUser,
    remnawave: FromDishka[Remnawave],
) -> dict[str, Any]:
    try:
        settings = await _sdk(remnawave).subscriptions_settings.get_settings()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"RemnaWave error: {e}")
    return {**_to_dict(settings), "limits": {"announce": HAPP_ANNOUNCE_MAX, "title": PROFILE_TITLE_MAX}}


@router.put("")
@inject
async def update_subscription_app(
    body: SubscriptionAppUpdate,
    _admin: AdminUser,
    remnawave: FromDishka[Remnawave],
) -> dict[str, Any]:
    from remnapy.models import UpdateSubscriptionSettingsRequestDto

    sdk = _sdk(remnawave)
    try:
        current = await sdk.subscriptions_settings.get_settings()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"RemnaWave error: {e}")

    # Пустая строка = «очистить» (панель принимает null), поэтому "" → None.
    changes: dict[str, Any] = {}
    for field, value in body.model_dump(exclude_unset=True).items():
        changes[field] = None if value == "" else value

    if changes.get("happ_routing"):
        changes["happ_routing"] = await _resolve_routing(changes["happ_routing"])

    headers = changes.get("custom_response_headers")
    if headers is None and "custom_response_headers" in changes:
        # Панель не принимает null — «нет заголовков» это пустой объект.
        changes["custom_response_headers"] = {}
    elif isinstance(headers, dict):
        changes["custom_response_headers"] = {
            k: _header_safe(_minify_json(v)) for k, v in headers.items() if k.strip()
        }

    try:
        updated = await sdk.subscriptions_settings.update_settings(
            UpdateSubscriptionSettingsRequestDto(uuid=current.uuid, **changes)
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"RemnaWave error: {e}")

    return {**_to_dict(updated), "limits": {"announce": HAPP_ANNOUNCE_MAX, "title": PROFILE_TITLE_MAX}}
