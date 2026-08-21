#!/usr/bin/env bash
#
# reserve-doctor.sh — почему резервный доступ не даёт человеку зайти в Telegram.
#
# Резерв должен сажать истёкшего на сквад-резерв: сервер, пускающий только в Telegram,
# чтобы человек дошёл до бота и продлил подписку. Подписка Remnawave отдаёт серверы
# ЧЕРЕЗ СКВАДЫ, поэтому сбоев ровно три, и все видны в панели: сквадов нет (ACTIVE, но
# в приложении пусто), сквады ЧУЖИЕ (человек на обычных серверах — это полный доступ,
# а не вход в Telegram) и статус не ACTIVE (например LIMITED — расход больше лимита).
# Скрипт показывает все три величины рядом с настройками резерва и записями о выдачах.
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
    && warn "Сквад-резерв НЕ задан — без него резерв не выдаётся вовсе. Это сервер, пускающий в Telegram; задайте его в админке."
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
# Правило дедупа ТО ЖЕ, что у крона (reserve.py): пропускаем тех, у кого резерв
# действует или уже выдавался за текущее истечение. Иначе цифра здесь врала бы.
psql_t "SELECT count(*) AS кандидатов
        FROM users u
        JOIN subscriptions s ON u.current_subscription_id = s.id
        WHERE u.role = 'USER' AND s.user_remna_id IS NOT NULL
          AND s.expire_at < now() AND s.expire_at > now() - make_interval(days => ${WIN})
          AND NOT EXISTS (
            SELECT 1 FROM reserve_grants r
            WHERE r.user_id = u.id
              AND (r.ended = false OR r.granted_at >= s.expire_at));"

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

SQUAD_CFG="$(printf '%s\n' "$CFG" | sed -n 's/.*"squad_uuid": *"\([^"]*\)".*/\1/p')"
if [ -z "$UUIDS" ] && [ -z "$SQUAD_CFG" ]; then
  warn "Нет ни одного UUID для проверки и не задан сквад-резерв."
else
  # Сквад-резерв передаём внутрь: без него нельзя отличить «человек на ТГ-скваде»
  # от «человек на обычных серверах», а это разные диагнозы.
  printf '%s\n' "$UUIDS" | docker exec -i -e "RESERVE_SQUAD=${SQUAD_CFG}" "$APP_CONTAINER" \
    /opt/remnashop/.venv/bin/python - <<'PY'
import asyncio, os, sys

import httpx

host = (os.environ.get("REMNAWAVE_HOST") or "").strip()
token = (os.environ.get("REMNAWAVE_TOKEN") or "").strip()
if not host or not token:
    sys.exit("REMNAWAVE_HOST/REMNAWAVE_TOKEN не заданы в окружении контейнера")
# Нормализуем ровно как adapter/main.py:_panel_url: без схемы → http, и :3000
# ТОЛЬКО голому имени контейнера (нет ни двоеточия, ни точки). Домен трогать нельзя:
# «https://panel.example.com» с дописанным :3000 никуда не достучится.
host = host.rstrip("/")
if "://" not in host:
    host = "http://" + host
_hostname = host.split("://", 1)[1].split("/", 1)[0]
if ":" not in _hostname and "." not in _hostname:
    host = host.replace(_hostname, _hostname + ":3000", 1)

GB = 1024 ** 3
reserve_squad = (os.environ.get("RESERVE_SQUAD") or "").strip().lower()
uuids = [l.strip() for l in sys.stdin if l.strip()]


async def check_squad(cli) -> None:
    """Отдаст ли сам сквад-резерв хоть один сервер. Если нет — не работает НИ У КОГО."""
    if not reserve_squad:
        print("  Сквад-резерв не задан — резерв не выдаётся вовсе.")
        return
    try:
        r = await cli.get("/api/internal-squads")
        r.raise_for_status()
        body = r.json().get("response") or r.json()
        squads = body.get("internalSquads") if isinstance(body, dict) else body
        squad = next((s for s in (squads or [])
                      if str(s.get("uuid", "")).lower() == reserve_squad), None)
    except Exception as exc:  # noqa: BLE001
        print(f"  Сквады панели не прочитаны: {exc}")
        return
    if squad is None:
        print("  ► Сквад-резерв НЕ НАЙДЕН в панели — проверьте выбор в админке.")
        return
    inbounds = {str(i.get("uuid") or "") for i in (squad.get("inbounds") or [])} - {""}
    print(f"  сквад-резерв: {squad.get('name')}   инбаундов: {len(inbounds)}")
    if not inbounds:
        print("  ► ПРИЧИНА: у сквада нет инбаундов — подписка будет пустой У ВСЕХ.")
        return
    try:
        r = await cli.get("/api/hosts")
        r.raise_for_status()
        hosts = r.json().get("response") or r.json()
    except Exception as exc:  # noqa: BLE001
        print(f"  Хосты не прочитаны: {exc}")
        return
    live = 0
    for h in hosts or []:
        if h.get("isDisabled") or h.get("isHidden"):
            continue
        if str(h.get("inboundUuid") or "") not in inbounds:
            continue
        if reserve_squad in {str(x).lower() for x in (h.get("excludedInternalSquads") or [])}:
            continue
        live += 1
    print(f"  серверов отдаётся скваду: {live}")
    if not live:
        print("  ► ПРИЧИНА: скваду не отдаётся ни один сервер — хосты выключены,")
        print("    скрыты или сквад у них в исключениях. Резерв не работает НИ У КОГО.")


async def main() -> None:
    async with httpx.AsyncClient(base_url=host, timeout=15,
                                 headers={"Authorization": f"Bearer {token}"}) as cli:
        await check_squad(cli)
        for u in uuids:
            try:
                r = await cli.get(f"/api/users/{u}")
                r.raise_for_status()
                d = r.json().get("response") or r.json()
            except Exception as exc:  # noqa: BLE001
                print(f"  {u}: ОШИБКА запроса к панели: {exc}")
                continue
            squad_list = d.get("activeInternalSquads") or []
            squads = [s.get("name") or s.get("uuid") for s in squad_list]
            squad_uuids = [str(s.get("uuid") or "").lower() for s in squad_list]
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
                print("      Задайте «Сквад-резерв» в админке — это сервер для входа в Telegram.")
            elif reserve_squad and squad_uuids != [reserve_squad]:
                print("    ► ПРИЧИНА: человек НЕ на сквад-резерве, а на обычных серверах.")
                print("      Это полный доступ вместо входа в Telegram. Почасовой крон")
                print("      переведёт его сам; если не перевёл — ищите «reserve:» в логах.")
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
