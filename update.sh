#!/usr/bin/env bash
#
# update.sh — обновление RemnaShop для НАШЕГО форка. Две вещи отдельно:
#
#   ./update.sh                  # НАШ код: git pull → пересборка overlay+кабинета
#                                #   (заодно сообщит, если вышла новая версия базы;
#                                #    тарбол-установка без .git — код тянется свежим архивом)
#   ./update.sh --base latest    # БАЗА бота: сам определит последнюю версию и обновит
#   ./update.sh --base <тег>     # БАЗА бота: обновить базовый образ до конкретного <тег>
#                                #   (snoups/remnashop) с валидацией и пересборкой
#   ./update.sh --no-backup      # любой из режимов без бэкапа БД
#   ./update.sh --force          # git reset --hard origin/<ветка> (СОТРЁТ локальные
#                                #   правки) перед сборкой — когда обычный pull не идёт
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
BACKUP=1; BASE=0; BASE_TAG=""; FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --no-backup) BACKUP=0 ;;
    --force)     FORCE=1 ;;
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

# ── определение версии базового образа (snoups/remnashop) ─────────────────────
BASE_REPO="snoups/remnashop"

# Дефолтный тег базы из docker-compose.yml (BASE_TAG:-vX.Y.Z).
default_base() { grep -oE 'BASE_TAG:-v[0-9.]+' docker-compose.yml | head -1 | sed 's/.*-//'; }
# Текущий тег: из .env, иначе дефолт из compose.
current_base_tag() {
  local t; t="$(grep -E '^BASE_TAG=' .env 2>/dev/null | tail -1 | cut -d= -f2- || true)"
  echo "${t:-$(default_base)}"
}
# Последний релиз базы из GitHub API (надёжнее, чем страничный GHCR tags/list).
fetch_latest_base() {
  local tag
  tag="$(curl -fsSL --max-time 8 \
    "https://api.github.com/repos/${BASE_REPO}/releases/latest" 2>/dev/null \
    | grep -oE '"tag_name"[[:space:]]*:[[:space:]]*"v[0-9]+\.[0-9]+\.[0-9]+"' | head -1 \
    | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+')"
  [ -n "$tag" ] && echo "$tag"
}

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
# Дампы храним ВНЕ папки репозитория, чтобы дампы БД с данными физически не лежали
# рядом с git (защита от случайной утечки). Путь можно переопределить: BACKUP_DIR=…
BACKUP_DIR="${BACKUP_DIR:-/opt/remnashop-backups}"
BACKUP_KEEP="${BACKUP_KEEP:-10}"   # сколько последних дампов хранить
if [ "$BACKUP" = 1 ]; then
  if docker ps --format '{{.Names}}' | grep -qx remnashop-db; then
    mkdir -p "$BACKUP_DIR"; chmod 700 "$BACKUP_DIR" 2>/dev/null || true
    F="$BACKUP_DIR/backup-$(date +%F-%H%M%S).sql.gz"
    info "Бэкап БД → ${F}…"
    if docker exec remnashop-db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' 2>/dev/null | gzip > "$F" && [ -s "$F" ]; then
      ok "Бэкап готов (${F}, $(du -h "$F" | cut -f1))"
      # Чистим старые: оставляем последние $BACKUP_KEEP.
      ls -1t "$BACKUP_DIR"/backup-*.sql.gz 2>/dev/null | tail -n +"$((BACKUP_KEEP+1))" | xargs -r rm -f
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
  # --base latest → скрипт сам определяет последнюю версию базы.
  if [ "$BASE_TAG" = latest ]; then
    CUR_BASE="$(current_base_tag)"
    info "Узнаю последнюю версию базового образа (ghcr.io/${BASE_REPO})…"
    RESOLVED="$(fetch_latest_base || true)"
    [ -n "$RESOLVED" ] || die "Не удалось определить последнюю версию (нет сети/доступа к ghcr.io). Укажите тег явно: ./update.sh --base vX.Y.Z"
    if [ "$RESOLVED" = "$CUR_BASE" ]; then
      ok "Базовый образ уже последний (${RESOLVED}) — обновлять нечего."
      exit 0
    fi
    BASE_TAG="$RESOLVED"
    ok "Последняя версия базы: ${BASE_TAG} (текущая ${CUR_BASE})"
  fi
  # БАЗА бота: сперва валидируем overlay на новом теге, потом фиксируем BASE_TAG.
  info "Проверяю совместимость overlay с базой ghcr.io/${BASE_REPO}:${BASE_TAG}…"
  ./check-update.sh "$BASE_TAG" || die "Overlay несовместим с базой ${BASE_TAG} — база НЕ обновлена. Подробности выше."
  set_env BASE_TAG "$BASE_TAG"
  ok "Зафиксировал BASE_TAG=${BASE_TAG} в .env — пересобираю на новой базе"
else
  # НАШ код: подтянуть последние изменения форка.
  if [ -d .git ]; then
    BR="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
    if [ "$FORCE" = 1 ]; then
      warn "--force: git fetch + reset --hard origin/${BR} (локальные правки будут СТЁРТЫ)…"
      git fetch origin || die "git fetch не прошёл — нет сети/доступа к origin. Код НЕ обновлён."
      git reset --hard "origin/${BR}" || die "git reset --hard origin/${BR} не прошёл. Код НЕ обновлён."
      ok "Код синхронизирован с origin/${BR} ($(git rev-parse --short HEAD))"
    else
      info "git pull…"
      # ВАЖНО: не «собираем из старого кода молча». Если pull не идёт (локальные
      # правки/расхождение) — СТОП с понятной инструкцией, иначе версия «застрянет».
      if ! git pull --ff-only; then
        die "git pull --ff-only не прошёл — есть локальные правки или ветка разошлась с origin.
   Код НЕ обновлён (иначе собралась бы старая версия «под видом» новой). Что делать:
     • убрать правки:      git stash        (или: git checkout -- <файлы>)
       затем повторить:    ./update.sh
     • ЛИБО принудительно: ./update.sh --force   (git reset --hard origin/${BR} — СОТРЁТ локальные правки)"
      fi
      ok "Код обновлён ($(git rev-parse --short HEAD))"
    fi
  else
    # Тарбол-установка (ставили по one-liner, без git): обновляем код свежим архивом
    # и перезапускаемся на нём. Архив GitHub содержит ТОЛЬКО отслеживаемые файлы —
    # .env и рантайм-конфиги (в .gitignore) в него не входят → остаются нетронутыми.
    REPO_SLUG="${REPO_SLUG:-velamaker/remnashop-cabinet}"
    UPD_BRANCH="${UPD_BRANCH:-main}"
    if [ "${_SELF_UPDATED:-0}" != 1 ]; then
      info "Тарбол-установка — тяну свежий код (архив ${REPO_SLUG}@${UPD_BRANCH})…"
      TMP="$(mktemp -d)"
      if curl -fL --max-time 180 "https://github.com/${REPO_SLUG}/archive/refs/heads/${UPD_BRANCH}.tar.gz" \
           | tar xz -C "$TMP" --strip-components=1; then
        cp -a "$TMP"/. .            # новый код поверх; рантайм (gitignore) не затрагивается
        rm -rf "$TMP"
        ok "Код обновлён из архива"
        # Перезапуск на свежем update.sh (иначе bash может дочитать старую версию файла).
        # Бэкап уже сделан этим запуском → на перезапуске его пропускаем.
        exec env _SELF_UPDATED=1 bash "$0" --no-backup
      fi
      rm -rf "$TMP"
      warn "Не удалось скачать архив (нет сети/доступа) — собираю из текущего кода."
    fi
  fi
  # Заодно (best-effort) проверяем, не вышла ли новая версия базового бота.
  CUR_BASE="$(current_base_tag)"; LATEST_BASE="$(fetch_latest_base 2>/dev/null || true)"
  if [ -n "$LATEST_BASE" ] && [ "$LATEST_BASE" != "$CUR_BASE" ]; then
    warn "Доступна новая версия базового бота: ${CUR_BASE} → ${LATEST_BASE}"
    warn "Подтянуть (с проверкой совместимости): ./update.sh --base latest"
  fi
fi

# ── 3. Пересборка и запуск (overlay бота + кабинет) ───────────────────────────
info "Сборка и запуск (overlay бота + кабинет, --build)…"
$DC "${COMPOSE[@]}" up -d --build

# ── 4. Логи ───────────────────────────────────────────────────────────────────
echo
ok "${BOLD}Обновление применено.${RST} Логи (${DIM}Ctrl+C — выход${RST}):"
$DC "${COMPOSE[@]}" logs -f --tail=30
