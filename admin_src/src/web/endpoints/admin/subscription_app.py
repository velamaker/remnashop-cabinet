"""Админ: настройки подписки в приложении (Happ и др. клиенты).

Это НЕ наш json-конфиг, а настройки самой панели Remnawave
(`/api/subscription-settings`): заголовок профиля, ссылка поддержки, объявление
Happ, ссылка на конфиг маршрутизации, доп. заголовки ответа подписки.
Приложение читает их при импорте ссылки — отсюда «брендинг и роутинг
подтягиваются в Happ».

Двухветочно по версии панели. В Remnawave 3.x ровно эти шесть полей
(profileTitle, supportLink, profileUpdateInterval, isProfileWebpageUrlEnabled,
happAnnounce, happRouting) из настроек УДАЛЕНЫ — они стали обычными строками в
`customResponseHeaders`. Панель мигрирует существующие значения сама, ломается
только ЗАПИСЬ: старый PATCH ушёл бы с полями, которых в схеме больше нет.
Поэтому на 3.x читаем и пишем те же шесть настроек через заголовки, а форма в
админке остаётся прежней — оператору разница не видна.

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
from src.infrastructure.services.overlay_panel_compat import panel_is_v3
from src.web.endpoints.public.appearance import resolve_brand_name
from src.web.net_guard import is_safe_public_url

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

# Куда те же шесть настроек переехали в 3.x. Имена заголовков — те же, что панель
# отдаёт клиенту в ответе подписки (см. _PASS_HEADERS в public/sub_alias.py),
# то есть теперь оператор задаёт их напрямую, без промежуточного поля.
_HEADER_BY_FIELD: dict[str, str] = {
    "profile_title": "profile-title",
    "support_link": "support-url",
    "profile_update_interval": "profile-update-interval",
    "is_profile_webpage_url_enabled": "profile-web-page-url",
    "happ_announce": "announce",
    "happ_routing": "routing",
}
_MANAGED_HEADERS = frozenset(_HEADER_BY_FIELD.values())

# Префикс, которым сама панель помечает base64-значение при переносе своих полей
# в заголовки. Наш собственный "base64:" (см. _header_safe) — другое: он для
# свободных заголовков оператора и панелью не разворачивается.
_PANEL_B64 = "rwEncodeBase64:"

# Плейсхолдер подстановки: панель заменяет его на реальный sub-URL. Это и есть
# бывший тумблер isProfileWebpageUrlEnabled — включён, когда заголовок задан.
_SUBSCRIPTION_URL_TEMPLATE = "{{SUBSCRIPTION_URL}}"


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
    """Обратно к человекочитаемому — для формы в админке.

    Два префикса означают РАЗНОЕ, и путать их нельзя:

      • `base64:` — значение УЖЕ закодировано (так пишем произвольные заголовки мы
        сами, чтобы не-latin1 текст не ронял ответ), поэтому его надо раскодировать;

      • `rwEncodeBase64:` — это УКАЗАНИЕ панели закодировать значение при отдаче
        клиенту, а внутри лежит обычный текст. Проверено на живой 3.3.2: в
        настройках хранится `rwEncodeBase64:Begemot VPN`, а приложение получает
        `base64:QmVnZW1vdCBWUE4=`. Пытаться раскодировать такое — ошибка: раньше
        мы так и делали, декодирование падало, и оператор видел в поле
        «rwEncodeBase64:Begemot VPN» вместо названия сервиса.
    """
    if value.startswith(_PANEL_B64):
        return value[len(_PANEL_B64):]
    if value.startswith("base64:"):
        try:
            return base64.b64decode(value[len("base64:"):]).decode("utf-8")
        except Exception:
            return value
    return value


def _to_dict(settings: Any) -> dict[str, Any]:
    data = {f: getattr(settings, f, None) for f in _FIELDS}
    headers = data.get("custom_response_headers")
    if isinstance(headers, dict):
        data["custom_response_headers"] = {k: _header_display(v) for k, v in headers.items()}
    return data


def _split_headers(settings: Any) -> tuple[dict[str, str], dict[str, str]]:
    """customResponseHeaders панели → (управляемые нами, свободные оператора).

    Управляемые — те шесть, что на 3.x подменяют бывшие поля настроек; их нельзя
    показывать в свободном списке заголовков, иначе оператор увидит одно и то же
    значение дважды и правка в одном месте затрёт правку в другом.
    """
    if isinstance(settings, dict):  # сырой ответ 3.x, ключи camelCase
        raw = settings.get("customResponseHeaders")
    else:  # модель remnapy (ветка 2.x)
        raw = getattr(settings, "custom_response_headers", None)
    managed: dict[str, str] = {}
    free: dict[str, str] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            (managed if str(key).lower() in _MANAGED_HEADERS else free)[str(key)] = str(value)
    return managed, free


def _fields_from_headers(managed: dict[str, str]) -> dict[str, Any]:
    """Управляемые заголовки → те же шесть значений, что форма ждёт от 2.x."""
    by_name = {k.lower(): v for k, v in managed.items()}
    out: dict[str, Any] = {}
    for field, header in _HEADER_BY_FIELD.items():
        value = by_name.get(header)
        if field == "is_profile_webpage_url_enabled":
            # Тумблер = сам факт наличия заголовка (панель кладёт туда плейсхолдер).
            out[field] = value is not None
            continue
        if value is None:
            out[field] = None
            continue
        if field == "profile_update_interval":
            try:
                out[field] = int(str(value).strip())
            except ValueError:
                out[field] = None
            continue
        out[field] = _header_display(str(value))
    return out


def _field_to_header_value(field: str, value: Any) -> Optional[str]:
    """Значение поля формы → значение заголовка. None = заголовок надо убрать."""
    if field == "is_profile_webpage_url_enabled":
        return _SUBSCRIPTION_URL_TEMPLATE if value else None
    if value is None:
        return None
    if field == "profile_update_interval":
        return str(int(value))
    text = str(value)
    if field in ("profile_title", "happ_announce"):
        # Заголовок профиля и объявление — произвольный текст, у нас он почти
        # всегда кириллический, а Node роняет ответ на любом не-latin1 символе.
        # Маркер `rwEncodeBase64:` просит ЗАКОДИРОВАТЬ значение саму панель, поэтому
        # под ним лежит ОБЫЧНЫЙ ТЕКСТ. Кодировать здесь самим нельзя: панель
        # закодировала бы уже закодированное, и в приложении вместо названия
        # сервиса появилась бы белиберда. Ровно так пишет и сама панель при
        # переносе своих полей — сверено на живой 3.3.2.
        return _PANEL_B64 + text
    # support-url и routing — ссылка и happ://-deep-link, они ASCII по построению
    # (_resolve_routing не пропустит ничего другого), кодировать нечего.
    return text


def _view_v3(settings: Any) -> dict[str, Any]:
    """Ответ формы на 3.x: шесть настроек собраны из заголовков, а не из полей."""
    managed, free = _split_headers(settings)
    return {
        **_fields_from_headers(managed),
        "custom_response_headers": {k: _header_display(v) for k, v in free.items()},
    }


def _headers_for_v3(current: Any, changes: dict[str, Any]) -> dict[str, str]:
    """Правки формы → новый customResponseHeaders (единственное, что пишем на 3.x).

    Панель принимает заголовки только целиком, поэтому собираем полный словарь:
    свободные заголовки оператора + шесть управляемых. Не присланные поля берём
    из текущих настроек — иначе PATCH одного поля стирал бы остальные пять.
    """
    managed_now, free_now = _split_headers(current)

    if "custom_response_headers" in changes:
        sent = changes["custom_response_headers"]
        free = (
            {}
            if not isinstance(sent, dict)
            else {
                k: _header_safe(_minify_json(v))
                for k, v in sent.items()
                # Управляемые имена из свободного списка выкидываем: их источник —
                # соответствующие поля формы, иначе два поля дрались бы за один ключ.
                if k.strip() and k.strip().lower() not in _MANAGED_HEADERS
            }
        )
    else:
        free = dict(free_now)

    managed = {k.lower(): v for k, v in managed_now.items()}
    for field, header in _HEADER_BY_FIELD.items():
        if field not in changes:
            continue
        value = _field_to_header_value(field, changes[field])
        if value is None:
            managed.pop(header, None)
        else:
            managed[header] = value

    # Управляемые кладём последними: при совпадении имён побеждают они.
    return {**free, **managed}


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

    # SSRF-гард: хост URL должен быть публичным (не внутренняя docker-сеть / метаданные
    # облака / loopback). follow_redirects=False — иначе публичный 30x увёл бы во внутрь.
    if not is_safe_public_url(url):
        raise HTTPException(
            status_code=400,
            detail="Ссылка должна вести на публичный хост (внутренние адреса запрещены).",
        )
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
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


async def _settings_raw_v3(sdk: Any) -> dict[str, Any]:
    """GET /subscription-settings мимо моделей SDK — обязательный обход для 3.x.

    Модель remnapy `SubscriptionSettingsResponseDto` требует profileTitle,
    supportLink, profileUpdateInterval и isProfileWebpageUrlEnabled. В 3.x панель
    их не отдаёт вовсе, значит pydantic упал бы на валидации ещё до нашего кода —
    раздел настроек отвечал бы 502 при полностью живой панели. Берём сырой JSON с
    того же httpx-клиента, что и весь SDK (те же заголовки авторизации).
    """
    resp = await sdk.subscriptions_settings.client.get("/subscription-settings")
    resp.raise_for_status()
    data = resp.json()
    response = data.get("response") if isinstance(data, dict) else None
    return response if isinstance(response, dict) else {}


async def _patch_headers_v3(sdk: Any, uuid: Any, headers: dict[str, str]) -> dict[str, Any]:
    """PATCH только customResponseHeaders — единственное, чем на 3.x правятся эти
    шесть настроек. Ответ читаем сырым по той же причине, что и в _settings_raw_v3."""
    resp = await sdk.subscriptions_settings.client.patch(
        "/subscription-settings",
        json={"uuid": str(uuid), "customResponseHeaders": headers},
    )
    resp.raise_for_status()
    data = resp.json()
    response = data.get("response") if isinstance(data, dict) else None
    return response if isinstance(response, dict) else {}


@router.get("")
@inject
async def get_subscription_app(
    _admin: AdminUser,
    remnawave: FromDishka[Remnawave],
) -> dict[str, Any]:
    sdk = _sdk(remnawave)
    try:
        if await panel_is_v3(sdk):
            data = _view_v3(await _settings_raw_v3(sdk))
        else:
            data = _to_dict(await sdk.subscriptions_settings.get_settings())
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"RemnaWave error: {e}")
    return {**data, "limits": {"announce": HAPP_ANNOUNCE_MAX, "title": PROFILE_TITLE_MAX}}


@router.put("")
@inject
async def update_subscription_app(
    body: SubscriptionAppUpdate,
    _admin: AdminUser,
    remnawave: FromDishka[Remnawave],
) -> dict[str, Any]:
    from remnapy.models import UpdateSubscriptionSettingsRequestDto

    sdk = _sdk(remnawave)
    is_v3 = await panel_is_v3(sdk)

    try:
        current = await (
            _settings_raw_v3(sdk) if is_v3 else sdk.subscriptions_settings.get_settings()
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"RemnaWave error: {e}")

    # Пустая строка = «очистить» (панель принимает null), поэтому "" → None.
    changes: dict[str, Any] = {}
    for field, value in body.model_dump(exclude_unset=True).items():
        changes[field] = None if value == "" else value

    if changes.get("happ_routing"):
        changes["happ_routing"] = await _resolve_routing(changes["happ_routing"])

    if not is_v3:
        headers = changes.get("custom_response_headers")
        if headers is None and "custom_response_headers" in changes:
            # Панель не принимает null — «нет заголовков» это пустой объект.
            changes["custom_response_headers"] = {}
        elif isinstance(headers, dict):
            changes["custom_response_headers"] = {
                k: _header_safe(_minify_json(v)) for k, v in headers.items() if k.strip()
            }

    try:
        if is_v3:
            updated = await _patch_headers_v3(
                sdk, current.get("uuid"), _headers_for_v3(current, changes)
            )
        else:
            updated = await sdk.subscriptions_settings.update_settings(
                UpdateSubscriptionSettingsRequestDto(uuid=current.uuid, **changes)
            )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"RemnaWave error: {e}")

    data = _view_v3(updated) if is_v3 else _to_dict(updated)
    return {**data, "limits": {"announce": HAPP_ANNOUNCE_MAX, "title": PROFILE_TITLE_MAX}}
