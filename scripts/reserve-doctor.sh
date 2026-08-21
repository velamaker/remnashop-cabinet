#!/usr/bin/env bash
#
# reserve-doctor.sh — почему резервный доступ не даёт рабочую подписку.
#
# Типовая жалоба: «в панели у человека стоят 7 дней резерва, а в приложении пусто».
# Подписка Remnawave отдаёт серверы ЧЕРЕЗ СКВАДЫ: юзер без сквадов — ACTIVE, со сроком
# и лимитом, но список конфигов пустой. Ещё один способ получить ту же пустоту —
# остаться в статусе LIMITED (расход больше выданного лимита). Скрипт показывает обе
# величины рядом с настройками резерва и записями о выдачах.
#
#   scripts/reserve-doctor.sh              # общая картина по установке
#   scripts/reserve-doctor.sh 123          # разбор одного клиента (user_id из БД бота)
#
# ТОЛЬКО ЧТЕНИЕ: ничего не меняет ни в БД, ни в панели. Запускать в каталоге бота.
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."

APP_CONTAINER="${APP_CONTAINER:-remnashop}"
DB_CONTAINER="${DB_CONTAINER:-remnashop-db}"
DB_USER="${DB_USER:-remnashop}"
DB_NAME="${DB_NAME:-remnashop}"
USER_ID="${1:-}"

if [ -t 1 ]; then
  BOLD=$'\e[1m'; DIM=$'\e[2m'; GRN=$'\e[32m'; RED=$'\e[31m'; YLW=$'\e[33m'; RST=$'\e[0m'
else BOLD=""; DIM=""; GRN=""; RED=""; YLW=""; RST=""; fi
head() { printf '\n%s── %s %s\n' "$BOLD" "$*" "$RST"; }
warn() { printf '%s!%s %s\n' "$YLW" "$RST" "$*"; }
die()  { printf '%s✗ %s%s\n' "$RED" "$*" "$RST" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "Не найден docker."
docker inspect "$APP_CONTAINER" >/dev/null 2>&1 || die "Нет контейнера ${APP_CONTAINER}. Запущен ли бот?"

psql_q() { docker exec -i "$DB_CONTAINER" psql -qAtX -U "$DB_USER" -d "$DB_NAME" -c "$1" 2>&1; }
psql_t() { docker exec -i "$DB_CONTAINER" psql -X -U "$DB_USER" -d "$DB_NAME" -c "$1" 2>&1; }

# ── 1. Настройки резерва (читаем ВНУТРИ контейнера: том assets, а не копия с хоста) ──
head "Настройки резерва (assets/reserve.json в контейнере)"
CFG="$(docker exec "$APP_CONTAINER" sh -c 'cat /opt/remnashop/assets/reserve.json 2>/dev/null')"
if [ -z "$CFG" ]; then
  warn "Файла нет — значит действуют дефолты, а дефолт: резерв ВЫКЛЮЧЕН (enabled=false)."
  warn "Включите его в админке: «Резервный доступ истёкшим»."
else
  printf '%s\n' "$CFG"
  printf '%s\n' "$CFG" | grep -q '"enabled": *true' \
    || warn "enabled=false — крон выходит сразу, ничего не выдаётся."
  printf '%s\n' "$CFG" | grep -q '"squad_uuid": *""' \
    && warn "Сквад-резерв НЕ задан. Тогда панель сквады не меняет — и если у истёкшего их не осталось, подписка будет пустой."
fi

# ── 2. Что говорит сам крон ──────────────────────────────────────────────────
head "Логи крона резерва за сутки (ищем строки «reserve:»)"
FOUND=0
for c in remnashop-taskiq-scheduler remnashop-taskiq-worker "$APP_CONTAINER"; do
  docker inspect "$c" >/dev/null 2>&1 || continue
  OUT="$(docker logs --since 24h "$c" 2>&1 | grep -a 'reserve:' | tail -25)"
  [ -n "$OUT" ] && { printf '%s[%s]%s\n%s\n' "$DIM" "$c" "$RST" "$OUT"; FOUND=1; }
done
[ "$FOUND" = 0 ] && warn "Ни одной строки «reserve:» за сутки. Либо резерв выключен, либо выдавать было некому, либо крон не запускается (крон ходит раз в час, в :27)."

# ── 3. Состояние в БД бота ───────────────────────────────────────────────────
head "Кандидаты на резерв прямо сейчас (истёкшие в пределах окна, без выданного резерва)"
WIN="$(printf '%s\n' "$CFG" | sed -n 's/.*"window_days": *\([0-9]*\).*/\1/p')"; WIN="${WIN:-7}"
psql_t "SELECT count(*) AS кандидатов
        FROM users u
        JOIN subscriptions s ON u.current_subscription_id = s.id
        LEFT JOIN reserve_grants r ON r.user_id = u.id
        WHERE u.role = 'USER' AND s.user_remna_id IS NOT NULL
          AND s.expire_at < now() AND s.expire_at > now() - make_interval(days => ${WIN})
          AND r.user_id IS NULL;"

head "Последние выдачи резерва (таблица reserve_grants)"
if [ -n "$USER_ID" ]; then
  psql_t "SELECT user_id, remna_uuid, granted_at, reserve_expire_at, ended
          FROM reserve_grants WHERE user_id = ${USER_ID};"
else
  psql_t "SELECT user_id, remna_uuid, granted_at, reserve_expire_at, ended
          FROM reserve_grants ORDER BY granted_at DESC LIMIT 20;"
fi

# ── 4. Что на самом деле в панели ────────────────────────────────────────────
# Главный шаг: строка «выдано» в нашей таблице ничего не говорит о том, может ли
# человек подключиться. Смотрим статус, лимит, расход и — ключевое — СКВАДЫ.
head "Состояние в панели Remnawave (статус / срок / лимит / расход / сквады)"
if [ -n "$USER_ID" ]; then
  UUIDS="$(psql_q "SELECT s.user_remna_id FROM users u
                   JOIN subscriptions s ON u.current_subscription_id = s.id
                   WHERE u.id = ${USER_ID} AND s.user_remna_id IS NOT NULL;")"
else
  UUIDS="$(psql_q "SELECT remna_uuid FROM reserve_grants ORDER BY granted_at DESC LIMIT 10;")"
fi
UUIDS="$(printf '%s\n' "$UUIDS" | grep -Eo '[0-9a-fA-F-]{36}')"

if [ -z "$UUIDS" ]; then
  warn "Нет ни одного UUID для проверки (резерв никому не выдан либо неверный user_id)."
else
  printf '%s\n' "$UUIDS" | docker exec -i "$APP_CONTAINER" /opt/remnashop/.venv/bin/python - <<'PY'
import asyncio, os, sys

import httpx

host = (os.environ.get("REMNAWAVE_HOST") or "").strip()
token = (os.environ.get("REMNAWAVE_TOKEN") or "").strip()
if not host or not token:
    sys.exit("REMNAWAVE_HOST/REMNAWAVE_TOKEN не заданы в окружении контейнера")
# Нормализуем так же, как это делает бот: имя без порта → :3000, без схемы → http.
if "://" not in host:
    host = "http://" + host
if not host.rsplit(":", 1)[-1].isdigit():
    host += ":3000"

GB = 1024 ** 3
uuids = [l.strip() for l in sys.stdin if l.strip()]


async def main() -> None:
    async with httpx.AsyncClient(base_url=host, timeout=15,
                                 headers={"Authorization": f"Bearer {token}"}) as cli:
        for u in uuids:
            try:
                r = await cli.get(f"/api/users/{u}")
                r.raise_for_status()
                d = r.json().get("response") or r.json()
            except Exception as exc:  # noqa: BLE001
                print(f"  {u}: ОШИБКА запроса к панели: {exc}")
                continue
            squads = [s.get("name") or s.get("uuid") for s in (d.get("activeInternalSquads") or [])]
            limit = int(d.get("trafficLimitBytes") or 0)
            used = int((d.get("userTraffic") or {}).get("usedTrafficBytes") or d.get("usedTrafficBytes") or 0)
            status = d.get("status")
            print(f"\n  {u}")
            print(f"    статус   : {status}")
            print(f"    истекает : {d.get('expireAt')}")
            print(f"    лимит    : {limit / GB:.2f} ГБ    расход: {used / GB:.2f} ГБ")
            print(f"    сквады   : {squads or 'ПУСТО'}")
            if not squads:
                print("    ► ПРИЧИНА: сквадов нет — подписка отдаст пустой список серверов.")
                print("      Задайте «Сквад-резерв» в админке (или верните человеку его сквады).")
            elif status != "ACTIVE":
                print(f"    ► ПРИЧИНА: статус {status}, а не ACTIVE — панель не отдаёт конфиги.")
            elif limit and used >= limit:
                print("    ► ПРИЧИНА: расход больше лимита — панель пометит LIMITED.")
            else:
                print("    ► В панели всё в порядке: проверяйте сам сквад — есть ли у него")
                print("      inbounds и не исключают ли его хосты (excludedInternalSquads).")


asyncio.run(main())
PY
fi

printf '\n%sГотово.%s Пустые сквады — самая частая причина «резерв есть, а в приложении пусто».\n' "$GRN" "$RST"
printf '%sПочасовой крон теперь сам перепроверяет действующие резервы и доводит до рабочего\n' "$DIM"
printf 'состояния те, что оказались без сквадов, — удалять строки из reserve_grants руками не нужно.\n'
printf 'Израсходованный гигабайт (LIMITED при расходе ≥ лимита) — штатный конец резерва, не поломка.%s\n' "$RST"
