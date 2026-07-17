"""Авто-подтяжка АКТУАЛЬНЫХ ссылок установки приложений.

Проблема: ссылки на клиентов протухают. iOS-приложения (Happ, INCY…) снимают из
App Store конкретной страны и переиздают под НОВЫМ числовым id; десктоп-клиенты
выкладывают новые версии на GitHub. Захардкоженный каталог (cabinet/src/data/apps.ts)
и «слепой» upstream-конфиг это не ловят и молча отдают битую ссылку.

Решение: КУРИРУЕМЫЕ РЕЗОЛВЕРЫ. У каждого приложения — упорядоченная цепочка
кандидатов, привязанных к СТАБИЛЬНОМУ якорю (App Store bundleId, Play package,
GitHub repo), НЕ к поиску по имени (по имени возвращаются клоны-скамы). Резолвер
проверяет живость каждого кандидата и отдаёт первый рабочий:
  • appstore — iTunes Lookup по bundleId, перебор сторфронтов (ru→соседи); при
    удалении из RU автоматически берём ближайший стор, где приложение есть;
  • play     — HTTP-статус страницы (404 = снято);
  • github   — releases/latest, ассет по маске + версия из тега;
  • url      — курируемая прямая ссылка (TestFlight / сайт / APK) как последний рубеж.

Итог пишется в assets/app_links.json: `links` (совместимо: {app:{platform:url}})
+ `meta` ({app:{platform:{source,version,degraded}}}) для админки/алертов.
Дополнительно подмешиваем upstream app-config.json для приложений без резолвера.

Сервис — leaf-модуль (httpx/stdlib), без импортов app-слоя: безопасно тянется из
public/apps.py и из крон-таски.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

ASSETS_DIR = Path(os.environ.get("APP_ASSETS_DIR", "/opt/remnashop/assets"))
LINKS_PATH = ASSETS_DIR / "app_links.json"
# Авто-синхрон страницы подписки Remnawave (sub.begemot.cc). BASE — снятый один раз
# baked app-config.json (шаблон со всей структурой/текстами), OUT — пропатченный
# живыми ссылками; OUT bind-mount'ится в контейнер subscription-page. Если BASE
# нет — синхрон просто не делается (фича опциональна).
SUB_CONFIG_BASE = ASSETS_DIR / "sub_app_config.base.json"
SUB_CONFIG_OUT = ASSETS_DIR / "sub_app_config.json"
APPS_CONFIG_PATH = ASSETS_DIR / "apps.json"

# Порядок сторфронтов App Store: родной RU первым, дальше соседи, где RU-юзеры
# чаще всего держат вторые Apple ID. Первый живой — выигрывает.
_APPSTORE_COUNTRIES = ["ru", "kz", "am", "az", "ge", "by", "tr", "us"]

# Курируемые резолверы. Якоря (bundleId/package/repo) проверены вживую (16 июля).
# type=appstore  → bundle_id
# type=play      → package
# type=github    → repo + asset (regex по имени ассета latest-релиза)
# type=url       → url (последний рубеж; отдаётся, даже если проверка не прошла)
RESOLVERS: dict[str, dict[str, list[dict[str, str]]]] = {
    "happ": {
        "ios": [{"type": "appstore", "bundle_id": "su.ffg.happ"}],
        "android": [
            {"type": "play", "package": "com.happproxy"},
            {"type": "github", "repo": "Happ-proxy/happ-android", "asset": r"\.apk$"},
        ],
        "windows": [{"type": "github", "repo": "Happ-proxy/happ-desktop", "asset": r"setup-Happ\.x64\.exe$"}],
        "macos": [{"type": "github", "repo": "Happ-proxy/happ-desktop", "asset": r"Happ\.macOS\.universal\.dmg$"}],
        "androidtv": [{"type": "github", "repo": "Happ-proxy/happ-android", "asset": r"\.apk$"}],
    },
    "incy": {
        "ios": [{"type": "appstore", "bundle_id": "llc.itdev.incy"}],
        "android": [{"type": "play", "package": "llc.itdev.incy"}],
    },
    "v2raytun": {
        "ios": [{"type": "appstore", "bundle_id": "com.databridges.privacy.v2RayTun"}],
        "android": [{"type": "play", "package": "com.v2raytun.android"}],
    },
    "hiddify": {
        "ios": [{"type": "appstore", "bundle_id": "apple.hiddify.com"}],
        "android": [
            {"type": "play", "package": "app.hiddify.com"},
            {"type": "github", "repo": "hiddify/hiddify-app", "asset": r"android.*\.apk$"},
        ],
    },
    "streisand": {
        "ios": [{"type": "appstore", "bundle_id": "com.effect.streisand"}],
    },
    "shadowrocket": {
        "ios": [{"type": "appstore", "bundle_id": "com.liguangming.Shadowrocket"}],
    },
    "karing": {
        "ios": [{"type": "appstore", "bundle_id": "com.nebula.karing"}],
        "android": [{"type": "github", "repo": "KaringX/karing", "asset": r"android.*arm64.*\.apk$"}],
        "windows": [{"type": "github", "repo": "KaringX/karing", "asset": r"windows.*x64.*\.(exe|zip)$"}],
    },
}


# ─────────────────────────── резолверы кандидатов ───────────────────────────
# Каждый возвращает (url, version|None, source) при успехе либо None.


async def _resolve_appstore(client: httpx.AsyncClient, cand: dict[str, str]) -> Optional[tuple[str, Optional[str], str]]:
    """iTunes Lookup по bundleId. RU живой → нейтральная ссылка (откроется в
    сторе юзера). RU снят → первый живой сосед явной страной."""
    bundle = cand["bundle_id"]
    for country in _APPSTORE_COUNTRIES:
        try:
            resp = await client.get(
                "https://itunes.apple.com/lookup",
                params={"bundleId": bundle, "country": country},
            )
            data = resp.json()
        except Exception:  # noqa: BLE001
            continue
        results = data.get("results") or []
        if not results:
            continue  # resultCount 0 в этом сторфронте — пробуем следующий
        app = results[0]
        track_id = app.get("trackId")
        version = app.get("version")
        if not track_id:
            continue
        if country == "ru":
            return f"https://apps.apple.com/app/id{track_id}", version, "appstore:ru"
        return f"https://apps.apple.com/{country}/app/id{track_id}", version, f"appstore:{country}"
    return None


async def _resolve_play(client: httpx.AsyncClient, cand: dict[str, str]) -> Optional[tuple[str, Optional[str], str]]:
    """Play Store: 200 — приложение есть, 404 — снято."""
    pkg = cand["package"]
    url = f"https://play.google.com/store/apps/details?id={pkg}&hl=ru"
    try:
        resp = await client.get(url)
        if resp.status_code == 200:
            return url, None, "play"
    except Exception:  # noqa: BLE001
        pass
    return None


async def _resolve_github(client: httpx.AsyncClient, cand: dict[str, str]) -> Optional[tuple[str, Optional[str], str]]:
    """GitHub releases/latest: первый ассет по маске + версия из тега."""
    repo = cand["repo"]
    try:
        pattern = re.compile(cand["asset"], re.IGNORECASE)
    except re.error:
        return None
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = await client.get(f"https://api.github.com/repos/{repo}/releases/latest", headers=headers)
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception:  # noqa: BLE001
        return None
    tag = data.get("tag_name")
    if isinstance(tag, str):
        tag = tag[1:] if tag[:1] in ("v", "V") else tag  # "v3.1.0" → "3.1.0"
    for asset in data.get("assets") or []:
        name = str(asset.get("name") or "")
        if pattern.search(name):
            dl = asset.get("browser_download_url")
            if dl:
                return str(dl), tag, "github"
    return None


async def _resolve_url(client: httpx.AsyncClient, cand: dict[str, str]) -> Optional[tuple[str, Optional[str], str]]:
    """Курируемая прямая ссылка — отдаём даже при неудачной проверке (последний рубеж)."""
    url = cand.get("url") or ""
    if not url.startswith(("http://", "https://")):
        return None
    return url, None, "url"


_RESOLVER_FNS = {
    "appstore": _resolve_appstore,
    "play": _resolve_play,
    "github": _resolve_github,
    "url": _resolve_url,
}


async def resolve_all() -> tuple[dict[str, dict[str, str]], dict[str, dict[str, dict[str, Any]]]]:
    """Прогнать все резолверы → (links, meta). Первый живой кандидат на (app, platform)."""
    links: dict[str, dict[str, str]] = {}
    meta: dict[str, dict[str, dict[str, Any]]] = {}
    async with httpx.AsyncClient(
        timeout=15.0, follow_redirects=True, headers={"User-Agent": "remnashop-applinks/1.0"}
    ) as client:
        for app_id, platforms in RESOLVERS.items():
            for plat, candidates in platforms.items():
                won: Optional[tuple[str, Optional[str], str]] = None
                win_idx = -1
                for idx, cand in enumerate(candidates):
                    fn = _RESOLVER_FNS.get(cand.get("type", ""))
                    if not fn:
                        continue
                    try:
                        won = await fn(client, cand)
                    except Exception:  # noqa: BLE001
                        won = None
                    if won:
                        win_idx = idx
                        break
                if not won:
                    continue
                url, version, source = won
                # degraded = ушли с основного кандидата ИЛИ App Store не в родном RU-сторе.
                degraded = win_idx > 0 or (source.startswith("appstore:") and source != "appstore:ru")
                links.setdefault(app_id, {})[plat] = url
                meta.setdefault(app_id, {})[plat] = {
                    "source": source,
                    "version": version,
                    "degraded": bool(degraded),
                }
    return links, meta


# ─────────────────────────── upstream app-config.json ───────────────────────────

_PLATFORM_MAP = {
    "ios": "ios",
    "android": "android",
    "windows": "windows",
    "macos": "macos",
    "androidtv": "androidtv",
}


def _first_button_link(app: dict[str, Any]) -> Optional[str]:
    step = app.get("installationStep")
    if not isinstance(step, dict):
        return None
    for btn in step.get("buttons") or []:
        if not isinstance(btn, dict):
            continue
        link = str(btn.get("buttonLink") or "").strip()
        if link.startswith(("http://", "https://")):
            return link[:500]
    return None


def parse_app_config(raw: Any) -> dict[str, dict[str, str]]:
    """app-config.json → {app_id(lower): {platform: install_url}}."""
    out: dict[str, dict[str, str]] = {}
    platforms = (raw or {}).get("platforms") if isinstance(raw, dict) else None
    if not isinstance(platforms, dict):
        return out
    for plat_key, apps in platforms.items():
        plat = _PLATFORM_MAP.get(str(plat_key).lower())
        if not plat or not isinstance(apps, list):
            continue
        for app in apps:
            if not isinstance(app, dict):
                continue
            aid = str(app.get("id") or "").strip().lower()
            if not aid:
                continue
            link = _first_button_link(app)
            if link:
                out.setdefault(aid, {})[plat] = link
    return out


async def _fetch_upstream(url: str) -> dict[str, dict[str, str]]:
    """Скачать upstream app-config.json → распарсить (пусто при любой ошибке)."""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return {}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return parse_app_config(resp.json())
    except Exception:  # noqa: BLE001
        return {}


# ─────────────────────── авто-синхрон страницы подписки ───────────────────────


def _load_manual_links() -> dict[str, dict[str, str]]:
    """Ручные оверрайды ссылок из apps.json (best-effort, leaf-чтение файла)."""
    try:
        if APPS_CONFIG_PATH.exists():
            with APPS_CONFIG_PATH.open(encoding="utf-8") as fh:
                data = json.load(fh)
            ml = data.get("manual_links") if isinstance(data, dict) else None
            if isinstance(ml, dict):
                out: dict[str, dict[str, str]] = {}
                for aid, plats in ml.items():
                    if isinstance(plats, dict):
                        out[str(aid).lower()] = {
                            str(p): str(u) for p, u in plats.items() if isinstance(u, str)
                        }
                return out
    except Exception:
        pass
    return {}


def patch_subscription_config(
    base: dict[str, Any],
    links: dict[str, dict[str, str]],
    meta: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Пропатчить app-config.json страницы подписки живыми ссылками.

    Как в кабинете: для каждого приложения+платформы, где у нас есть ссылка, тихо
    заменяем buttonLink ПЕРВОЙ http-кнопки на живую. Текст кнопки и остальная
    структура/тексты/приложения сохраняются как в base (никаких «(KZ)» и т.п.).
    `meta` не используется — оставлен для совместимости сигнатуры."""
    import copy

    cfg = copy.deepcopy(base)
    platforms = cfg.get("platforms")
    if not isinstance(platforms, dict):
        return cfg
    for plat_key, apps in platforms.items():
        plat = _PLATFORM_MAP.get(str(plat_key).lower())
        if not plat or not isinstance(apps, list):
            continue
        for app in apps:
            if not isinstance(app, dict):
                continue
            aid = str(app.get("id") or "").strip().lower()
            url = (links.get(aid) or {}).get(plat)
            if not url:
                continue
            step = app.get("installationStep")
            if not isinstance(step, dict):
                continue
            buttons = step.get("buttons")
            if not isinstance(buttons, list):
                continue
            for btn in buttons:
                if isinstance(btn, dict) and str(btn.get("buttonLink") or "").startswith(("http://", "https://")):
                    btn["buttonLink"] = url
                    break
    return cfg


def write_subscription_config(links: dict[str, dict[str, str]], meta: dict[str, dict[str, Any]]) -> bool:
    """Записать пропатченный app-config.json страницы подписки (in-place, тот же
    inode — чтобы bind-mount увидел изменения). Пусто/нет base → тихо False."""
    try:
        if not SUB_CONFIG_BASE.exists():
            return False
        with SUB_CONFIG_BASE.open(encoding="utf-8") as fh:
            base = json.load(fh)
        # Ручные оверрайды админа — поверх резолвера (как в кабинете).
        merged_links = {aid: dict(plats) for aid, plats in links.items()}
        merged_meta = {aid: dict(plats) for aid, plats in meta.items()}
        for aid, plats in _load_manual_links().items():
            for plat, u in plats.items():
                if u.startswith(("http://", "https://")):
                    merged_links.setdefault(aid, {})[plat] = u
                    merged_meta.setdefault(aid, {})[plat] = {"source": "manual", "version": None, "degraded": False}
        patched = patch_subscription_config(base, merged_links, merged_meta)
        with SUB_CONFIG_OUT.open("w", encoding="utf-8") as fh:
            json.dump(patched, fh, ensure_ascii=False, indent=2)
        return True
    except Exception as exc:  # noqa: BLE001 — синхрон sub-страницы не должен ронять refresh
        from loguru import logger

        logger.debug(f"app_links: sub-config не обновлён: {exc}")
        return False


# ─────────────────────────── сборка и запись ───────────────────────────


async def fetch_and_store(source_url: str = "") -> dict[str, Any]:
    """Прогнать резолверы + (низкий приоритет) upstream, записать app_links.json.

    Резолверы главнее upstream: upstream заполняет ТОЛЬКО те (app, platform),
    которых нет у резолверов. `source_url` — необязательный upstream app-config.json.
    Возвращает {ok, count, updated_at, apps, degraded} либо {ok:false, error}.
    """
    try:
        links, meta = await resolve_all()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Резолверы упали: {exc}", "count": 0}

    upstream = await _fetch_upstream(source_url) if source_url else {}
    for aid, plats in upstream.items():
        for plat, url in plats.items():
            if aid not in links or plat not in links[aid]:
                links.setdefault(aid, {})[plat] = url
                meta.setdefault(aid, {})[plat] = {"source": "upstream", "version": None, "degraded": False}

    if not links:
        # Ничего не срезолвили и upstream пуст — НЕ затираем прошлый файл.
        return {"ok": False, "error": "Не удалось получить ни одной ссылки (сеть?)", "count": 0}

    updated_at = datetime.now(timezone.utc).isoformat()
    degraded = sorted(
        f"{aid}:{plat}"
        for aid, plats in meta.items()
        for plat, m in plats.items()
        if m.get("degraded")
    )
    # missing = у приложения есть резолвер на эту платформу, но ни один кандидат
    # не отдал живую ссылку (полностью мёртвая цель) — сильнее degraded.
    missing = sorted(
        f"{aid}:{plat}"
        for aid, plats in RESOLVERS.items()
        for plat in plats
        if plat not in links.get(aid, {})
    )
    payload = {"updated_at": updated_at, "source_url": source_url or None, "links": links, "meta": meta}
    try:
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        with LINKS_PATH.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Не удалось сохранить: {exc}", "count": len(links)}

    # Авто-синхрон страницы подписки Remnawave (best-effort, не влияет на результат).
    sub_synced = write_subscription_config(links, meta)

    return {
        "ok": True,
        "count": len(links),
        "updated_at": updated_at,
        "apps": sorted(links.keys()),
        "degraded": degraded,
        "missing": missing,
        "sub_synced": sub_synced,
    }


def load_links() -> dict[str, Any]:
    """Прочитать assets/app_links.json (оверрайды ссылок + meta). Безопасно → дефолт."""
    try:
        if LINKS_PATH.exists():
            with LINKS_PATH.open(encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and isinstance(data.get("links"), dict):
                return {
                    "links": data["links"],
                    "meta": data.get("meta") if isinstance(data.get("meta"), dict) else {},
                    "updated_at": data.get("updated_at"),
                    "source_url": data.get("source_url"),
                }
    except Exception:
        pass
    return {"links": {}, "meta": {}, "updated_at": None, "source_url": None}
