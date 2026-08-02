"""Самопроверка адаптера: что кабинет реально получит на этом бэкенде.

Зачем отдельный скрипт. `/adapter/health` показывает только счётчики путей, а
знать нужно другое: какие экраны кабинета оживут, а какие упрутся в 501. Скрипт
логинится тестовым пользователем и обходит ручки, которые кабинет дёргает при
обычной работе, показывая код ответа и первые поля тела.

Запуск (адаптер должен быть поднят):
    python3 adapter/selfcheck.py --email me@example.org --password '…'
    SELFCHECK_EMAIL=me@example.org SELFCHECK_PASSWORD='…' python3 adapter/selfcheck.py

Только чтение: ни одной ручки, которая списывает деньги или что-то создаёт.

СОСЕДНИЕ ИНСТРУМЕНТЫ — они отвечают на ДРУГИЕ вопросы, этот их не заменяет:
  • `tests/contract/run_contract.py` — «а правильные ли данные в ответе»: поля,
    типы, единицы измерения (ГБ против байт, рубли против копеек), формы 401/501.
    Здесь же виден только код ответа, поэтому 200 с мусором тут выглядит «готово».
  • `tests/contract/canary_bedolaga.py` — «а не переехало ли что-то у них»:
    сравнение их сегодняшнего API с зафиксированным снимком.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

# Пути, которые кабинет дёргает сам, без действий пользователя: если тут 501 —
# соответствующий экран пустой или с ошибкой.
READ_PATHS = [
    "/appearance",
    "/auth/me",
    "/auth/whoami",
    "/subscription/current",
    "/subscription/trial-info",
    "/subscription/devices",
    "/subscription/servers",
    "/subscription/service-status",
    "/subscription/server-stats",
    "/subscription/traffic-history",
    "/subscription/freeze-status",
    "/subscription/offers",
    "/plans/public",
    "/balance",
    "/balance/transactions?limit=5",
    "/balance/topup/config",
    "/referral/program",
    "/referral/earnings",
    "/promo-banner",
    "/trial-discount",
    "/notifications",
    "/notifications/unread-count",
    "/support/tickets",
    "/sessions",
    "/apps",
    "/info",
    "/status",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8090")
    ap.add_argument("--prefix", default="/api/v1/public")
    # Никаких учёток по умолчанию: это диагностический скрипт в публичном
    # репозитории, а значение по умолчанию — это опубликованный рабочий пароль.
    ap.add_argument("--email", default=os.environ.get("SELFCHECK_EMAIL", ""))
    ap.add_argument("--password", default=os.environ.get("SELFCHECK_PASSWORD", ""))
    args = ap.parse_args()

    # Куки сессии адаптер ставит с флагом Secure (так и надо в проде), а проверяем
    # мы по http — штатный CookieJar такие куки не отправит. Поэтому забираем
    # Set-Cookie руками: это диагностика на локальном адаптере, не браузер.
    cookies: dict[str, str] = {}

    def call(path: str, body: dict | None = None) -> tuple[int, str]:
        url = args.base + args.prefix + path
        data = json.dumps(body).encode() if body is not None else None
        headers = {"content-type": "application/json"} if data else {}
        if cookies:
            headers["cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                for raw in resp.headers.get_all("set-cookie") or []:
                    name, _, rest = raw.partition("=")
                    cookies[name.strip()] = rest.split(";")[0]
                return resp.status, resp.read(400).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(400).decode("utf-8", "replace")
        except OSError as exc:
            return 0, f"нет связи: {exc}"

    if not args.email or not args.password:
        print("Укажите учётку: --email/--password или SELFCHECK_EMAIL/SELFCHECK_PASSWORD")
        return 2
    code, body = call("/auth/login", {"email": args.email, "password": args.password})
    print(f"вход: {code} {body[:120]}")
    if code != 200:
        print("без сессии дальше смысла нет — проверь почту/пароль и что адаптер поднят")
        return 1

    ok = missing = broken = 0
    for path in READ_PATHS:
        code, body = call(path)
        if code == 200:
            ok += 1
            mark = "готово "
        elif code == 501:
            missing += 1
            mark = "НЕТ    "
        else:
            broken += 1
            mark = f"?{code:<6}"
        print(f"{mark} {path:<40} {body[:90].replace(chr(10), ' ')}")

    print(f"\nитог: готово {ok}, не реализовано {missing}, требует разбора {broken}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
