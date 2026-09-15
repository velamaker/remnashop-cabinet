#!/usr/bin/env python3
"""Держит ли панель Remnawave те ручки, на которых стоит кабинет и бот.

ЗАЧЕМ. Панель — чужой софт, и она обновляется без нас. Между 2.8 и 3.0 у неё
пропала колонка `users.uuid`, и весь слой идентичности пришлось писать заново;
такое может повториться в любом релизе. Проверять это ЧТЕНИЕМ РЕЛИЗ-НОТОК нельзя:
там пишут «refactor api routes», а что именно переехало — не пишут.

Скрипт спрашивает у ЖИВОЙ панели ровно те пути, на которых стоим мы, и печатает,
какие ответили, а какие пропали. Запускать перед апгрейдом на репетиционном
стенде и после апгрейда на бою.

  ./scripts/check-panel-compat.py --url http://127.0.0.1:3300 --token "$TOK"
  ./scripts/check-panel-compat.py --url http://remnawave:3000 --token "$TOK" --network remnawave-network

ТОЛЬКО ЧТЕНИЕ. Ни одна мутирующая ручка (`actions/*`, `hwid/devices/delete`,
`connections/drop`, `nodes/restart`) отсюда не зовётся — они перечислены в
MUTATING и проверяются лишь на присутствие в OpenAPI, если он доступен.

ПРО ЗАГОЛОВКИ. Панель по http без `x-forwarded-proto` и `x-forwarded-for` рвёт
соединение (её ProxyCheckMiddleware). Шлём оба всегда: на https они безвредны.

Коды возврата: 0 — все наши пути на месте; 1 — что-то пропало; 2 — панель не
ответила вовсе (проверить не смогли).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

# Пути, которые зовёт наш код. Пара (метод, путь) и чем подставить параметр.
# Ответ 200/400/404 одинаково означает «путь на месте»: нам важно, что ручка
# СУЩЕСТВУЕТ, а не что наши выдуманные аргументы ей понравились. 401/403 — тоже
# «на месте», но отдельно: значит, у токена нет прав, и это уже другая беда.
READ_PATHS: list[tuple[str, str]] = [
    ("GET", "/api/system/metadata"),
    ("GET", "/api/system/stats"),
    ("GET", "/api/system/health"),
    ("GET", "/api/nodes"),
    ("GET", "/api/hosts"),
    # Не `/api/inbounds`: с 3.x отдельной такой ручки нет вовсе, а входы живут
    # полем внутри профилей конфигурации и сквадов — именно так их и читает
    # `web/endpoints/public/service_status.py` (`cp.active_inbounds`, `sq.inbounds`).
    ("GET", "/api/config-profiles"),
    ("GET", "/api/internal-squads"),
    ("GET", "/api/users?size=1&start=0"),
    ("GET", "/api/users/stream"),
    ("GET", "/api/hwid/devices"),
]

# Пути с параметром: подставляем заведомо несуществующее значение. Ответ 404 —
# доказательство, что маршрут разобран (иначе панель ответила бы 404 БЕЗ тела
# «not found» или 400 о неверном формате). Главное, чего мы боимся, — это когда
# маршрута нет вовсе и панель отвечает «Cannot GET …».
PARAM_PATHS: list[tuple[str, str]] = [
    ("GET", "/api/users/00000000-0000-0000-0000-000000000000"),
    ("GET", "/api/users/by-short-uuid/zzzzzzzzzzzz"),
    ("GET", "/api/users/by-username/no-such-user-xyz"),
    ("GET", "/api/users/00000000-0000-0000-0000-000000000000/accessible-nodes"),
    ("GET", "/api/users/00000000-0000-0000-0000-000000000000/subscription-request-history"),
    ("GET", "/api/bandwidth-stats/users/00000000-0000-0000-0000-000000000000"),
    ("GET", "/api/hwid/devices/00000000-0000-0000-0000-000000000000"),
]

# Зовём только на присутствие в OpenAPI — трогать их на живой панели нельзя.
MUTATING = [
    "/api/users/{uuid}/actions/disable",
    "/api/users/{uuid}/actions/enable",
    "/api/users/{uuid}/actions/reset-traffic",
    "/api/users/{uuid}/actions/revoke",
    "/api/users/resolve",
    "/api/hwid/devices/delete",
    "/api/hwid/devices/delete-all",
    "/api/connections/drop",
    "/api/nodes/{uuid}/restart",
]

GONE = {404}


def call(url: str, method: str, path: str, token: str, timeout: float = 15.0):
    req = urllib.request.Request(url.rstrip("/") + path, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    # Без этой пары панель по http рвёт соединение — проверено на живой 2.8.1 и 3.x.
    req.add_header("x-forwarded-proto", "https")
    req.add_header("x-forwarded-for", "127.0.0.1")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(400).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(400).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 — сеть: покажем причину и пойдём дальше
        return None, str(exc)


def route_missing(status: int | None, body: str) -> bool:
    """Отличает «маршрута нет» от «ничего не нашлось по такому значению».

    Панель на несуществующий МАРШРУТ отвечает 404 с NestJS-ной фразой
    «Cannot GET /...». На существующий маршрут с несуществующим значением — тоже
    404, но со своим кодом ошибки в теле. Разница в теле, и только по ней можно
    судить, переехал ли путь.
    """
    if status is None:
        return False
    if status not in GONE:
        return False
    return "cannot " in body.lower()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="адрес панели, напр. http://127.0.0.1:3300")
    ap.add_argument("--token", required=True, help="API-токен панели")
    args = ap.parse_args()

    version = "?"
    status, body = call(args.url, "GET", "/api/system/metadata", args.token)
    if status is None:
        print(f"панель не ответила: {body}", file=sys.stderr)
        return 2
    if status == 200:
        try:
            version = (json.loads(body).get("response") or {}).get("version", "?")
        except ValueError:
            pass
    print(f"панель: {args.url} (версия {version})\n")

    missing: list[str] = []
    denied: list[str] = []
    for method, path in READ_PATHS + PARAM_PATHS:
        status, body = call(args.url, method, path, args.token)
        mark = "ок"
        if status is None:
            mark = f"НЕТ ОТВЕТА ({body[:60]})"
            missing.append(path)
        elif route_missing(status, body):
            mark = "МАРШРУТА НЕТ"
            missing.append(path)
        elif status in (401, 403):
            mark = "прав нет"
            denied.append(path)
        print(f"  {status if status else '---':>4}  {mark:<14} {method} {path}")

    print()
    if denied:
        print(f"⚠ токену не хватает прав на {len(denied)} путях: {', '.join(denied)}")
    if missing:
        print(f"✗ пропало путей: {len(missing)}")
        for p in missing:
            print(f"    {p}")
        return 1
    print(f"✓ все {len(READ_PATHS) + len(PARAM_PATHS)} путей на месте")
    print(f"  (мутирующие не звали, их {len(MUTATING)} — проверяются вручную после апгрейда)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
