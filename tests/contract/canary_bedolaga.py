"""Канарейка на дрейф чужого API: что изменилось у «Бедолаги» с прошлого снимка.

ЗАЧЕМ. Адаптер (`adapter/`) держится на чужом коде, который развивается без нас:
путь переименовали, поле убрали, метод сменили — и кабинет ломается у клиента, а
мы узнаём об этом из жалобы. Канарейка сравнивает ИХ сегодняшний API с
зафиксированным снимком и печатает разницу, отделяя «нам всё равно» от «мы этим
пользуемся».

ЧТО СЧИТАЕТСЯ НАШИМ. Список путей канарейка собирает из репозитория сама —
`adapter/route_map.json` (карта трансляции) плюс строковые литералы `/cabinet/...`
в `adapter/compose.py`. Поэтому новый обработчик, который допишет кто угодно,
попадает под присмотр без правки этого файла. А вот КОНКРЕТНЫЕ ПОЛЯ, которые мы
читаем, перечислены руками в `USED_FIELDS`: автоматически их не отличить от
случайных `.get()` внутри обработчика, а именно исчезнувшее поле — самая тихая
поломка.

ДВА РЕЖИМА ИСТОЧНИКА.
  • openapi — точный: `/openapi.json` со схемами ответов. На боевых установках
    «Бедолаги» он выключен (`WEB_API_DOCS_ENABLED=false`), на нашем стенде включён.
  • probe — запасной: без openapi канарейка ходит GET-ом по известным путям и
    смотрит код ответа (404 = путь пропал, 401/403/200 = путь на месте). Поля так
    не проверить, если не дать учётку (--email/--password) — тогда сверяются
    фактические ответы.

БЕЗОПАСНОСТЬ: GET по чужим путям и, только если явно дали учётку, один POST входа.
Ничего мутирующего. Пути с параметрами (`{id}`) в режиме probe не трогаются вовсе.

ЗАПУСК
    # сравнить живой API со снимком (стенд «Бедолаги» на 8080)
    python3 tests/contract/canary_bedolaga.py

    # другой адрес бота / боевая установка без openapi
    python3 tests/contract/canary_bedolaga.py --upstream http://127.0.0.1:8080
    python3 tests/contract/canary_bedolaga.py --mode probe

    # без openapi, но с тестовой учёткой — тогда видны и поля живых ответов
    python3 tests/contract/canary_bedolaga.py --mode probe --email … --password …

    # ПЕРЕСНЯТЬ эталон (делать осознанно: после того, как разобрались с дрейфом)
    python3 tests/contract/canary_bedolaga.py --update-snapshot

Снимок лежит рядом: tests/contract/bedolaga-api-snapshot.json.

КОДЫ ВОЗВРАТА: 0 — дрейфа по нашим путям нет; 1 — есть (разбирать);
2 — ошибка запуска; 3 — бот недоступен или снимка нет (проверить не смогли,
падать по этому поводу нельзя — бота может просто не быть на этой машине).
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe import Client  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
ADAPTER = REPO / "adapter"
SNAPSHOT = Path(__file__).with_name("bedolaga-api-snapshot.json")

# --- поля, которые адаптер РЕАЛЬНО читает из ответов «Бедолаги» ---------------
#
# Собрано вычиткой обработчиков `adapter/compose.py`. Исчезновение любого из них
# = молча пустой экран в кабинете, поэтому список ведём руками и сверяем с их
# схемой при каждой пересъёмке снимка (несуществующие поля канарейка назовёт).
# Точка — вложенность, `[]` — элемент списка.
USED_FIELDS: dict[str, list[str]] = {
    "/cabinet/auth/email/login": ["access_token", "refresh_token"],
    "/cabinet/auth/refresh": ["access_token", "refresh_token"],
    "/cabinet/auth/me": [
        "id", "telegram_id", "email", "email_verified", "username", "language", "auth_type",
    ],
    "/cabinet/auth/me/is-admin": ["is_admin"],
    "/cabinet/auth/me/permissions": ["role_level"],
    "/cabinet/subscription": [
        "has_subscription",
        "subscription.id", "subscription.status", "subscription.is_trial",
        "subscription.traffic_limit_gb", "subscription.traffic_used_gb",
        "subscription.device_limit", "subscription.traffic_reset_mode",
        "subscription.start_date", "subscription.end_date",
        "subscription.subscription_url", "subscription.hide_subscription_link",
        "subscription.tariff_name",
        # Автоплатёж кабинет показывает в кошельке, а живёт он у них в подписке,
        # а не в балансе — легко перепутать при правке адаптера.
        "subscription.autopay_enabled",
    ],
    "/cabinet/subscription/trial": [
        "is_available", "duration_days", "traffic_limit_gb", "device_limit",
    ],
    "/cabinet/subscription/devices": [
        "devices[].hwid", "devices[].platform", "devices[].device_model",
        "devices[].created_at", "device_limit", "total",
    ],
    "/cabinet/subscription/countries": [
        "countries[].name", "countries[].country_code", "countries[].is_available",
    ],
    "/cabinet/subscription/purchase-options": [
        "sales_mode", "has_subscription", "subscription_status",
        "tariffs[].id", "tariffs[].name", "tariffs[].description",
        "tariffs[].traffic_limit_gb", "tariffs[].device_limit", "tariffs[].is_available",
        "tariffs[].is_current", "tariffs[].is_purchased",
        "tariffs[].periods[].days", "tariffs[].periods[].price_kopeks",
    ],
    # Ручка отдаёт СПИСОК — отсюда `[]` в начале имени поля. Цена продления
    # берётся именно тут: в каталоге она без персонального промо (BEDOLAGA-MONEY §3).
    "/cabinet/subscription/renewal-options": [
        "[].period_days", "[].price_kopeks", "[].original_price_kopeks", "[].discount_percent",
    ],
    "/cabinet/subscription/revoke": ["success"],
    "/cabinet/balance": ["balance_rubles"],
    "/cabinet/balance/transactions": [
        "total", "items[].id", "items[].type", "items[].amount_rubles",
        "items[].description", "items[].created_at",
    ],
    "/cabinet/balance/payment-methods": [
        "[].id", "[].name", "[].is_available", "[].min_amount_kopeks", "[].max_amount_kopeks",
    ],
    "/cabinet/promo/loyalty-tiers": ["current_spent_rubles"],
    "/cabinet/promo/active-discount": ["is_active", "discount_percent", "expires_at"],
    "/cabinet/promo/offers": [],
    "/cabinet/promocode/activate": ["success", "message"],
    "/cabinet/referral": ["referral_code", "total_referrals", "active_referrals"],
    "/cabinet/referral/list": ["total"],
    "/cabinet/referral/terms": [],
    "/cabinet/referral/earnings": ["total", "total_amount_rubles"],
    "/cabinet/tickets": [
        "items[].id", "items[].title", "items[].status", "items[].created_at",
        "items[].updated_at", "items[].messages_count",
    ],
    "/cabinet/tickets/notifications": ["unread_count"],
    "/cabinet/tickets/notifications/unread-count": ["unread_count"],
    "/cabinet/branding": ["name", "has_custom_logo"],
    "/cabinet/branding/colors": ["accent"],
    "/cabinet/info/support-config": ["support_username"],
    "/cabinet/info-pages/tab-replacements": [],
}


def _normalize(path: str) -> str:
    """Приводим путь с параметром к одному виду с ОБЕИХ сторон сравнения.

    У нас в коде параметр записан как угодно — `{hwid}`, `{slug}`,
    `{ctx.param('id')}`, а внутри f-строки регулярка вообще обрывается на
    кавычке (`/cabinet/tickets/{ctx.param(`). У них в openapi — свои имена
    (`{ticket_id}`, `{notification_id}`). Сравнивать имена бессмысленно, важна
    ФОРМА пути, поэтому любой сегмент с фигурной скобкой становится `{}`.
    """
    parts = [("{}" if "{" in seg else seg) for seg in path.split("/")]
    return "/".join(parts)


def used_paths() -> dict[str, str]:
    """Пути «Бедолаги», от которых зависит адаптер, — прямо из его исходников.

    Возвращает путь → откуда он взялся (для отчёта: чинить придётся именно там).
    """
    found: dict[str, str] = {}

    route_map = ADAPTER / "route_map.json"
    if route_map.exists():
        data = json.loads(route_map.read_text("utf-8"))
        for ours, route in (data.get("routes") or {}).items():
            target = route.get("target")
            if target:
                found.setdefault(_normalize(target), f"route_map.json ← {ours}")

    compose = ADAPTER / "compose.py"
    if compose.exists():
        for raw in re.findall(r'["\'](/cabinet/[^"\']*)["\']', compose.read_text("utf-8")):
            # В докстрингах встречаются ссылки на ИХ исходники (`/cabinet/routes/
            # balance.py`) — это не пути API.
            if raw.endswith(".py"):
                continue
            found.setdefault(_normalize(raw), "compose.py")
    return found


# --- разбор их openapi -------------------------------------------------------

def _resolve(schema: Any, components: dict[str, Any], seen: tuple[str, ...] = ()) -> dict[str, Any]:
    """Раскрываем `$ref`; циклы обрываем, чтобы не уйти в бесконечность."""
    if not isinstance(schema, dict):
        return {}
    ref = schema.get("$ref")
    if ref:
        name = ref.rsplit("/", 1)[-1]
        if name in seen:
            return {}
        return _resolve(components.get(name, {}), components, seen + (name,))
    return schema


def flatten(schema: Any, components: dict[str, Any], prefix: str = "", depth: int = 0,
            seen: tuple[str, ...] = ()) -> set[str]:
    """Плоский список полей схемы: `subscription.traffic_limit_gb`, `items[].id`.

    Глубина ограничена: у них встречаются схемы, ссылающиеся друг на друга, а нам
    нужны поля, которые адаптер реально читает — глубже третьего уровня их нет.
    """
    if depth > 5:
        return set()
    node = _resolve(schema, components, seen)
    if not node:
        return set()
    out: set[str] = set()

    # anyOf/oneOf/allOf: у FastAPI так выглядят Optional и наследование — берём всё,
    # кроме ветки `null`, иначе половина полей «пропадёт» на ровном месте.
    for key in ("anyOf", "oneOf", "allOf"):
        for branch in node.get(key) or []:
            if isinstance(branch, dict) and branch.get("type") == "null":
                continue
            out |= flatten(branch, components, prefix, depth, seen)

    if node.get("type") == "array" or "items" in node:
        out |= flatten(node.get("items"), components, f"{prefix}[]", depth + 1, seen)

    for name, sub in (node.get("properties") or {}).items():
        full = f"{prefix}.{name}" if prefix else name
        out.add(full)
        out |= flatten(sub, components, full, depth + 1, seen)
    return out


def read_openapi(client: Client) -> dict[str, Any] | None:
    reply = client.get("/openapi.json", absolute=True)
    if reply.status != 200 or not isinstance(reply.body, dict):
        return None
    return reply.body


def snapshot_from_openapi(spec: dict[str, Any], wanted: dict[str, str]) -> dict[str, Any]:
    components = (spec.get("components") or {}).get("schemas") or {}
    paths = spec.get("paths") or {}
    # Их пути нормализуем той же функцией — иначе `{hwid}` у нас никогда не
    # совпадёт с `{device_hwid}` у них и путь вечно числился бы пропавшим.
    by_shape = {_normalize(p): p for p in paths}
    used: dict[str, Any] = {}
    absent: list[str] = []

    for path in sorted(wanted):
        item = paths.get(by_shape.get(path, path))
        if not isinstance(item, dict):
            absent.append(path)
            continue
        methods, fields = [], set()
        for method, op in item.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete"):
                continue
            methods.append(method.upper())
            ok = ((op.get("responses") or {}).get("200") or {}).get("content") or {}
            schema = (ok.get("application/json") or {}).get("schema")
            fields |= flatten(schema, components)
        used[path] = {"methods": sorted(methods), "fields": sorted(fields)}

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "openapi",
        "api_title": (spec.get("info") or {}).get("title", ""),
        "api_version": (spec.get("info") or {}).get("version", ""),
        # Тоже в нормализованном виде: этот список используется для подсказки
        # «путь переименовали», и обе стороны должны быть в одной форме.
        "all_paths": sorted({_normalize(p) for p in paths}),
        "used": used,
        # Упомянуты в адаптере, но у бота их нет: обычно это ссылки из комментариев
        # или путь, который уже переименовали. Держим отдельно, чтобы не выглядело
        # как дрейф при каждом запуске.
        "not_in_api": absent,
    }


# --- запасной режим: без openapi ---------------------------------------------

def fields_of(value: Any, prefix: str = "", depth: int = 0) -> set[str]:
    """Плоский список полей ФАКТИЧЕСКОГО ответа — в той же записи, что и из схемы.

    Нужен, когда openapi выключен: сравнивать «что было» и «что стало» можно и по
    живым телам, лишь бы обе стороны считались одинаково.
    """
    if depth > 5:
        return set()
    out: set[str] = set()
    if isinstance(value, dict):
        for name, sub in value.items():
            full = f"{prefix}.{name}" if prefix else name
            out.add(full)
            out |= fields_of(sub, full, depth + 1)
    elif isinstance(value, list):
        for item in value[:5]:  # хватает нескольких: поля у элементов одинаковые
            out |= fields_of(item, f"{prefix}[]", depth + 1)
    return out


def snapshot_from_probe(client: Client, wanted: dict[str, str]) -> dict[str, Any]:
    """Есть ли путь — по коду ответа. 404 = нет, всё остальное = есть.

    Только GET и только пути без параметров: подставлять чужие id мы не вправе.
    Поля видны лишь там, где ответ реально пришёл (то есть с учёткой) — без неё
    режим отвечает на один, но важный вопрос: «пути ещё на месте?».
    """
    used: dict[str, Any] = {}
    absent: list[str] = []
    for path in sorted(wanted):
        if "{" in path:
            continue
        reply = client.get(path, absolute=True)
        if reply.status == 0:
            continue
        if reply.status == 404:
            absent.append(path)
            continue
        fields = sorted(fields_of(reply.body)) if reply.status == 200 else []
        used[path] = {
            "methods": ["GET"] if reply.status != 405 else [],
            "fields": fields,
            "probe_status": reply.status,
        }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "probe",
        "api_title": "",
        "api_version": "",
        "all_paths": sorted(used),
        "used": used,
        "not_in_api": absent,
    }


# --- сравнение ---------------------------------------------------------------

def compare(old: dict[str, Any], new: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(критично, к сведению). Критично = ломается то, чем мы пользуемся."""
    bad: list[str] = []
    info: list[str] = []

    old_used, new_used = old.get("used") or {}, new.get("used") or {}
    live_paths = set(new.get("all_paths") or new_used)
    coarse = new.get("source") == "probe" or old.get("source") == "probe"

    if not coarse and old.get("api_version") and old.get("api_version") != new.get("api_version"):
        info.append(f"версия их API: {old['api_version']} → {new.get('api_version')}")

    for path in sorted(old_used):
        was = old_used[path]
        now = new_used.get(path)
        # Пути с параметром обход не трогает (подставлять чужие id мы не вправе) —
        # «не проверяли» нельзя выдавать за «пропал».
        if now is None and coarse and "{" in path:
            continue
        if now is None:
            hint = _rename_hint(path, live_paths - set(old_used))
            tail = f"; похоже, переименован в {hint}" if hint else ""
            bad.append(f"ПУТЬ ПРОПАЛ: {path}{tail}")
            continue

        gone_methods = set(was.get("methods") or []) - set(now.get("methods") or [])
        if gone_methods and not coarse:
            bad.append(f"МЕТОД ПРОПАЛ: {path} больше не принимает {', '.join(sorted(gone_methods))}")

        was_fields, now_fields = set(was.get("fields") or []), set(now.get("fields") or [])
        # В режиме probe поля видны только у тех ответов, которые нам отдали без
        # входа: пустой список полей — это «не смотрели», а не «поля пропали».
        if not now_fields and coarse:
            continue
        gone = was_fields - now_fields
        if gone:
            mine = {f for f in gone if _is_ours(path, f)}
            if mine:
                bad.append(f"ПОЛЕ ПРОПАЛО: {path} → {', '.join(sorted(mine))} (адаптер их читает)")
            rest = gone - mine
            # В режиме probe «поля» — это фактический ответ: необязательное поле
            # могло просто не прийти на этих данных. Шуметь об этом нельзя.
            if rest and not coarse:
                info.append(f"{path}: убрали поля, которыми мы не пользуемся — {', '.join(sorted(rest))}")
        added = now_fields - was_fields
        if added and not coarse:
            info.append(f"{path}: появились поля — {', '.join(sorted(added))}")

    # Поле из USED_FIELDS, которого нет в их схеме сейчас, — это либо их правка,
    # либо наша ошибка в списке. И то и другое надо увидеть.
    #
    # Пустой список полей — НЕ «поля пропали»: часть их обработчиков объявлена без
    # схемы ответа (FastAPI пишет в openapi просто «object»), и проверять там
    # нечего. Такие пути честно считаем непроверяемыми, иначе канарейка кричит
    # всегда и её перестают читать.
    blind: list[str] = []
    for path, fields in USED_FIELDS.items():
        now = new_used.get(path)
        if not now:
            continue
        known = set(now.get("fields") or [])
        if not known:
            if fields:
                blind.append(path)
            continue
        missing = [f for f in fields if f not in known]
        if coarse:
            # По живым ответам пустой список не отличить от пропавших полей: у
            # тестовой учётки может не быть ни устройств, ни платежей. Ругаемся
            # только там, где соседние поля того же списка ВИДНЫ, — тогда список
            # непустой и поля действительно нет.
            missing = [f for f in missing if _sibling_seen(f, known)]
        if missing:
            bad.append(f"НЕТ В ИХ СХЕМЕ: {path} → {', '.join(missing)}")
    # В режиме probe пустые поля — это норма (ответ не отдали без входа), про них
    # уже сказано строкой про источник; в режиме openapi это реальная слепая зона.
    if blind and not coarse:
        info.append(
            f"ответ не описан схемой, поля не проверить ({len(blind)}): {', '.join(sorted(blind))}"
        )

    fresh = set(new_used) - set(old_used)
    if fresh:
        info.append(f"в снимке не было, а адаптер уже использует: {', '.join(sorted(fresh))}")

    old_all, new_all = set(old.get("all_paths") or []), set(new.get("all_paths") or [])
    if old_all and new_all and not coarse:
        vanished, appeared = old_all - new_all, new_all - old_all
        if vanished:
            info.append(f"всего у них пропало путей: {len(vanished)} (наших среди них нет)"
                        if not (vanished & set(old_used)) else f"пропало путей: {len(vanished)}")
        if appeared:
            info.append(f"у них появилось новых путей: {len(appeared)}")
    return bad, info


def _rename_hint(path: str, candidates: set[str]) -> str:
    """Подсказка «путь переименовали» — только по соседям в том же разделе.

    Сравнивать пути целиком бесполезно: у них общий длинный префикс, и
    `/cabinet/subscription/info` оказывается «похож» на что угодно из
    `/cabinet/subscription/*`. Ищем среди СОСЕДЕЙ и по последнему сегменту —
    так ловятся настоящие переименования (`countries` → `country-list`).
    Не нашли — молчим: неверная подсказка хуже её отсутствия.
    """
    parent, _, tail = path.rpartition("/")
    siblings = {p.rpartition("/")[2]: p for p in candidates if p.rpartition("/")[0] == parent}
    hit = difflib.get_close_matches(tail, list(siblings), n=1, cutoff=0.6)
    return siblings[hit[0]] if hit else ""


def _sibling_seen(field: str, known: set[str]) -> bool:
    """Видели ли мы хоть одно поле рядом с этим (тот же объект/элемент списка)."""
    parent = field.rsplit(".", 1)[0] if "." in field else ""
    if not parent:
        return True
    return any(f != field and f.startswith(parent + ".") for f in known)


def _is_ours(path: str, field: str) -> bool:
    """Читает ли адаптер это поле (или вложенное в него)."""
    for mine in USED_FIELDS.get(path, []):
        if mine == field or mine.startswith(field + ".") or field.startswith(mine + "."):
            return True
    return False


# --- запуск ------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Канарейка на дрейф API «Бедолаги»")
    ap.add_argument("--upstream", default=os.environ.get("BEDOLAGA_UPSTREAM", "http://127.0.0.1:8080"))
    ap.add_argument("--mode", choices=("auto", "openapi", "probe"), default="auto",
                    help="auto — openapi, если отдают, иначе обход путей")
    ap.add_argument("--update-snapshot", action="store_true", help="переснять эталон")
    ap.add_argument("--snapshot", default=str(SNAPSHOT),
                    help="другой файл эталона — например снимок с другой установки")
    # Учётка нужна ТОЛЬКО режиму probe и только чтобы увидеть поля: без неё их
    # ручки отвечают 401 и сравнивать нечего. Значения по умолчанию нет намеренно.
    ap.add_argument("--email", default=os.environ.get("BEDOLAGA_EMAIL", ""))
    ap.add_argument("--password", default=os.environ.get("BEDOLAGA_PASSWORD", ""))
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args()
    snapshot = Path(args.snapshot)

    client = Client(args.upstream.rstrip("/"), "", args.timeout)
    wanted = used_paths()
    if not wanted:
        print("не нашёл путей «Бедолаги» в adapter/ — проверять нечего")
        return 2
    print(f"бот: {args.upstream}; путей, от которых зависит адаптер: {len(wanted)}")

    # Живой бот может быть просто не поднят (канарейку гоняют и на машинах, где
    # его нет) — это не повод падать с трассировкой.
    alive = client.get("/health", absolute=True)
    if alive.status == 0:
        alive = client.get("/", absolute=True)
    if alive.status == 0:
        print(f"бот недоступен ({alive.error}) — сравнивать не с чем, выходим без ошибки")
        return 3

    spec = read_openapi(client) if args.mode in ("auto", "openapi") else None
    if spec is None and args.mode == "openapi":
        print("openapi.json не отдаётся (у них WEB_API_DOCS_ENABLED=false?) — "
              "запустите с --mode probe")
        return 3
    if spec is not None:
        fresh = snapshot_from_openapi(spec, wanted)
        print(f"источник: openapi ({fresh['api_title']} {fresh['api_version']}, "
              f"путей всего {len(fresh['all_paths'])})")
    else:
        if args.email and args.password:
            # Единственный не-GET за весь запуск. Их вход — это Bearer-токен,
            # который дальше подставляется в заголовок (кук у бота нет).
            login = client.request(
                "POST", "/cabinet/auth/email/login",
                {"email": args.email, "password": args.password}, absolute=True,
            )
            token = login.body.get("access_token") if isinstance(login.body, dict) else None
            if token:
                client.bearer = token
                print("источник: обход известных путей с учёткой — поля берём из живых ответов")
            else:
                print(f"вход не прошёл ({login.status}) — поля проверить не удастся, "
                      "смотрим только наличие путей")
        else:
            print("источник: обход известных путей (openapi выключен). Без учётки "
                  "видно только наличие путей — поля не проверить")
        fresh = snapshot_from_probe(client, wanted)

    if fresh.get("not_in_api"):
        print(f"упомянуты в адаптере, но у бота их нет: {', '.join(fresh['not_in_api'])}")

    if args.update_snapshot:
        # Пересъёмка — момент, когда стоит проверить и наш собственный список
        # полей: опечатку в USED_FIELDS иначе видно только как вечный «дрейф».
        wrong = []
        for path, fields in USED_FIELDS.items():
            known = set((fresh["used"].get(path) or {}).get("fields") or [])
            if not known:
                continue
            wrong += [f"{path} → {f}" for f in fields if f not in known]
        if wrong:
            print("\nВНИМАНИЕ: в USED_FIELDS перечислены поля, которых нет в их схеме:")
            for item in wrong:
                print(f"  · {item}")
        snapshot.write_text(json.dumps(fresh, ensure_ascii=False, indent=1) + "\n", "utf-8")
        print(f"\nснимок переписан: {snapshot}")
        return 0

    if not snapshot.exists():
        print(f"снимка нет ({snapshot}) — снимите эталон: --update-snapshot")
        return 3
    old = json.loads(snapshot.read_text("utf-8"))
    print(f"эталон от {old.get('generated_at', '?')} (источник {old.get('source', '?')})")

    bad, info = compare(old, fresh)
    print()
    if bad:
        print("ДРЕЙФ, который нас ломает:")
        for line in bad:
            print(f"  ✗ {line}")
    if info:
        print("\nк сведению (кабинет не ломает):")
        for line in info:
            print(f"  · {line}")
    if not bad and not info:
        print("их API не изменился")
    elif not bad:
        print("\nпо нашим путям и полям дрейфа нет")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
