#!/usr/bin/env bash
#
# update.sh — обновление RemnaShop (overlay бота + кабинет) для НАШЕГО форка.
#
#   ./update.sh                # бэкап БД → git pull → пересборка → up → логи
#   ./update.sh --no-backup    # без бэкапа БД
#
# ПОЧЕМУ не `docker compose pull && down && up` (авторская команда):
#   • overlay бота СОБИРАЕТСЯ локально из нашего кода (а не тянется образом) —
#     нужен `--build`, иначе изменения не применяются;
#   • базовый образ ЗАПИННЕН в Dockerfile — `pull` тянет ту же версию
#     («нет обновления» в авторской проверке — это про базовый образ, не про нас);
#   • кабинет описан в ОТДЕЛЬНОМ compose-файле — без него он не обновляется.
# Поэтому обновление = git pull + пересборка с --build ОБОИХ compose-файлов.

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

if [ -t 1 ]; then
  BOLD=$'\e[1m'; DIM=$'\e[2m'; GRN=$'\e[32m'; YLW=$'\e[33m'; CYN=$'\e[36m'; RST=$'\e[0m'
else BOLD=""; DIM=""; GRN=""; YLW=""; CYN=""; RST=""; fi
info() { printf '%s➜%s %s\n' "$CYN" "$RST" "$*"; }
ok()   { printf '%s✓%s %s\n' "$GRN" "$RST" "$*"; }
warn() { printf '%s!%s %s\n' "$YLW" "$RST" "$*"; }
die()  { printf '✗ %s\n' "$*" >&2; exit 1; }

if docker compose version >/dev/null 2>&1; then DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then DC="docker-compose"
else die "Не найден 'docker compose'."; fi
COMPOSE=(-f docker-compose.yml -f cabinet/docker-compose.cabinet.yml)

[ -f .env ] || die "Нет .env — это каталог установки бота?"

# ── 1. Бэкап БД (по умолчанию; --no-backup чтобы пропустить) ──────────────────
if [ "${1:-}" != "--no-backup" ]; then
  if docker ps --format '{{.Names}}' | grep -qx remnashop-db; then
    F="backup-$(date +%F-%H%M%S).sql.gz"
    info "Бэкап БД → ${F}…"
    # Креды берём из env самого контейнера БД — надёжно при любом .env.
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

# ── 2. Обновление кода ────────────────────────────────────────────────────────
if [ -d .git ]; then
  info "git pull…"
  git pull --ff-only || warn "git pull не прошёл (локальные правки/ветка?) — собираю из текущего кода"
else
  warn "Не git-репозиторий — кода через git не обновляю (если ставили тарболом — перекачайте архив)."
fi

# ── 3. Пересборка и запуск (overlay бота + кабинет) ───────────────────────────
info "Сборка и запуск (overlay бота + кабинет, --build)…"
$DC "${COMPOSE[@]}" up -d --build

# ── 4. Логи ───────────────────────────────────────────────────────────────────
echo
ok "${BOLD}Обновление применено.${RST} Логи (${DIM}Ctrl+C — выход${RST}):"
$DC "${COMPOSE[@]}" logs -f --tail=30
