#!/usr/bin/env bash
#
# update.sh — обновление RemnaShop для НАШЕГО форка. Две вещи отдельно:
#
#   ./update.sh                  # НАШ код: git pull → пересборка overlay+кабинета
#   ./update.sh --base <тег>     # БАЗА бота: обновить базовый образ до <тег>
#                                #   (snoups/remnashop) с валидацией и пересборкой
#   ./update.sh --no-backup      # любой из режимов без бэкапа БД
#
# ПОЧЕМУ не `docker compose pull && down && up` (авторская команда):
#   • overlay бота СОБИРАЕТСЯ локально поверх базового образа (а не тянется
#     готовым) — нужен `--build`, иначе ни наш код, ни новая база не применяются;
#   • базовый образ запиннен (BASE_TAG) ради стабильности — обновляется осознанно
#     через `--base` с прогоном ./check-update.sh (не сломает overlay молча);
#   • кабинет — в отдельном compose-файле.

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

if [ -t 1 ]; then
  BOLD=$'\e[1m'; DIM=$'\e[2m'; GRN=$'\e[32m'; YLW=$'\e[33m'; CYN=$'\e[36m'; RST=$'\e[0m'
else BOLD=""; DIM=""; GRN=""; YLW=""; CYN=""; RST=""; fi
info() { printf '%s➜%s %s\n' "$CYN" "$RST" "$*"; }
ok()   { printf '%s✓%s %s\n' "$GRN" "$RST" "$*"; }
warn() { printf '%s!%s %s\n' "$YLW" "$RST" "$*"; }
die()  { printf '✗ %s\n' "$*" >&2; exit 1; }

# ── разбор аргументов ─────────────────────────────────────────────────────────
BACKUP=1; BASE=0; BASE_TAG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-backup) BACKUP=0 ;;
    --base)      BASE=1; shift; BASE_TAG="${1:-}"
                 { [ -n "$BASE_TAG" ] && [ "${BASE_TAG#-}" = "$BASE_TAG" ]; } || die "Укажите тег: ./update.sh --base <тег> (напр. v0.8.3)" ;;
    -h|--help)   grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
  shift
done

if docker compose version >/dev/null 2>&1; then DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then DC="docker-compose"
else die "Не найден 'docker compose'."; fi
COMPOSE=(-f docker-compose.yml -f cabinet/docker-compose.cabinet.yml)

[ -f .env ] || die "Нет .env — это каталог установки бота?"

# записать VAR=VAL в .env (заменить строку или дописать)
set_env() {
  local var="$1" val="$2"
  if grep -qE "^$var=" .env; then
    awk -v k="$var" -v v="$val" 'BEGIN{FS="="} $1==k && !d {print k"="v; d=1; next} {print}' .env > .env.__tmp__ && mv .env.__tmp__ .env
  else
    printf '%s=%s\n' "$var" "$val" >> .env
  fi
}

# ── 1. Бэкап БД ───────────────────────────────────────────────────────────────
if [ "$BACKUP" = 1 ]; then
  if docker ps --format '{{.Names}}' | grep -qx remnashop-db; then
    F="backup-$(date +%F-%H%M%S).sql.gz"
    info "Бэкап БД → ${F}…"
    if docker exec remnashop-db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' 2>/dev/null | gzip > "$F" && [ -s "$F" ]; then
      ok "Бэкап готов (${F}, $(du -h "$F" | cut -f1))"
    else
      rm -f "$F"; warn "Бэкап не удался — продолжаю без него (Ctrl+C чтобы прервать)"
    fi
  else
    warn "Контейнер remnashop-db не запущен — пропускаю бэкап"
  fi
else
  info "Бэкап пропущен (--no-backup)"
fi

# ── 2. Что обновляем ──────────────────────────────────────────────────────────
if [ "$BASE" = 1 ]; then
  # БАЗА бота: сперва валидируем overlay на новом теге, потом фиксируем BASE_TAG.
  info "Проверяю совместимость overlay с базой ghcr.io/snoups/remnashop:${BASE_TAG}…"
  ./check-update.sh "$BASE_TAG" || die "Overlay несовместим с базой ${BASE_TAG} — база НЕ обновлена. Подробности выше."
  set_env BASE_TAG "$BASE_TAG"
  ok "Зафиксировал BASE_TAG=${BASE_TAG} в .env — пересобираю на новой базе"
else
  # НАШ код: подтянуть последние изменения форка.
  if [ -d .git ]; then
    info "git pull…"
    git pull --ff-only || warn "git pull не прошёл (локальные правки/ветка?) — собираю из текущего кода"
  else
    warn "Не git-репозиторий — код через git не обновляю (если ставили тарболом — перекачайте архив)."
  fi
fi

# ── 3. Пересборка и запуск (overlay бота + кабинет) ───────────────────────────
info "Сборка и запуск (overlay бота + кабинет, --build)…"
$DC "${COMPOSE[@]}" up -d --build

# ── 4. Логи ───────────────────────────────────────────────────────────────────
echo
ok "${BOLD}Обновление применено.${RST} Логи (${DIM}Ctrl+C — выход${RST}):"
$DC "${COMPOSE[@]}" logs -f --tail=30
