#!/usr/bin/env bash
#
# update.sh — обновление RemnaShop для НАШЕГО форка. Две вещи отдельно:
#
#   ./update.sh                  # НАШ код: git pull → пересборка overlay+кабинета
#                                #   (заодно сообщит, если вышла новая версия базы;
#                                #    тарбол-установка без .git — код тянется свежим архивом).
#                                #   В терминале спросит, обновлять ли и бота (Enter = да)
#   ./update.sh --with-bot       # бот и кабинет, без вопроса (как было всегда)
#   ./update.sh --cabinet-only   # ТОЛЬКО кабинет (и адаптер «Бедолаги»), без вопроса:
#                                #   бот, HA-копия, воркеры, база и миграции не трогаются;
#                                #   функции кабинета, которым нужен новый бот, скрыты
#                                #   до его обновления
#   ./update.sh --base latest    # БАЗА бота: сам определит последнюю версию и обновит
#   ./update.sh --base <тег>     # БАЗА бота: обновить базовый образ до конкретного <тег>
#                                #   (snoups/remnashop) с валидацией и пересборкой
#   ./update.sh --no-backup      # любой из режимов без бэкапа БД
#                                #   (при --cabinet-only базу и так не дампим)
#   ./update.sh --force          # git reset --hard origin/<ветка> (СОТРЁТ локальные
#                                #   правки) перед сборкой — когда обычный pull не идёт
#
# ПОЧЕМУ не `docker compose pull && down && up` (авторская команда):
#   • overlay бота СОБИРАЕТСЯ локально поверх базового образа (а не тянется
#     готовым) — нужен `--build`, иначе ни наш код, ни новая база не применяются;
#   • базовый образ запиннен (BASE_TAG) ради стабильности — обновляется осознанно
#     через `--base` с прогоном ./check-update.sh (не сломает overlay молча);
#   • кабинет — в отдельном compose-файле.
#
# Без терминала (cron, ssh без -t, сессии агентов) вопроса нет и обновляется ВСЁ,
# как раньше: молча перестать обновлять бота (а с ним и исправления безопасности)
# хуже, чем обновить его как всегда. На сервере кабинета (режим site) скрипт
# останавливается и подсказывает site-install.sh; в режиме api (бот без кабинета)
# обновляет бота без вопроса.

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
# SCOPE: all — бот и кабинет; cabinet — только кабинет; пусто — спросить (или «all»
# без терминала). Флаг переживает перезапуск тарбол-ветки: решение принимается
# один раз, до скачивания.
BACKUP=1; BASE=0; BASE_TAG=""; FORCE=0; SCOPE=""
SCOPE_CONFLICT="--with-bot и --cabinet-only вместе не бывают: выберите одно."
while [ $# -gt 0 ]; do
  case "$1" in
    --no-backup) BACKUP=0 ;;
    --force)     FORCE=1 ;;
    --with-bot)     [ "$SCOPE" != cabinet ] || die "$SCOPE_CONFLICT"; SCOPE=all ;;
    --cabinet-only) [ "$SCOPE" != all ]     || die "$SCOPE_CONFLICT"; SCOPE=cabinet ;;
    --base)      BASE=1; shift; BASE_TAG="${1:-}"
                 { [ -n "$BASE_TAG" ] && [ "${BASE_TAG#-}" = "$BASE_TAG" ]; } || die "Укажите тег: ./update.sh --base <тег> (напр. v0.8.3)" ;;
    -h|--help)   grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
  shift
done
# --base пересобирает БОТА на новом базовом образе — «только кабинет» тут не бывает.
if [ "$BASE" = 1 ]; then
  [ "$SCOPE" != cabinet ] || die "--base обновляет базовый образ бота — с --cabinet-only не сочетается."
  SCOPE=all
fi

[ -f .env ] || die "Нет .env — это каталог установки бота?"

# Значение ключа из .env (последнее, без кавычек); пусто — ключа нет.
env_get() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'"'" || true; }
is_bedolaga() { grep -qE '^CABINET_BACKEND=bedolaga' .env 2>/dev/null; }

# Тип установки. site — кабинет на отдельном сервере: install.sh site пишет только
# ключи кабинета, бота там нет (BOT_TOKEN пуст), а /api уходит на API_UPSTREAM.
# api — бот без кабинета: install.sh api пишет COMPOSE_FILE без файла кабинета.
install_kind() {
  if [ -z "$(env_get BOT_TOKEN)" ] && [ -n "$(env_get API_UPSTREAM)" ]; then echo site; return 0; fi
  local cf; cf="$(env_get COMPOSE_FILE)"
  if [ -n "$cf" ] && [[ ":$cf:" != *":cabinet/docker-compose.cabinet.yml:"* ]]; then echo api; return 0; fi
  echo bot
}
INSTALL="$(install_kind)"
case "$INSTALL" in
  site)
    # Раньше скрипт здесь брал docker-compose.yml и пытался собрать и поднять бота с
    # базой, которых на сервере кабинета нет и быть не должно.
    die "Это сервер кабинета (режим site): бота здесь нет, а update.sh пересобирает и его.
   Обновление кабинета здесь — повтор установщика:
     cd $PWD && DEST=\"\$PWD\" bash site-install.sh
   Функции, которым нужен более новый бот, кабинет прячет сам и покажет, когда бота обновят." ;;
  api)
    [ "$SCOPE" != cabinet ] || die "Здесь нет кабинета (режим api: только бот) — обновлять «только кабинет» нечего. Запустите ./update.sh"
    SCOPE=all ;;
esac

if docker compose version >/dev/null 2>&1; then DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then DC="docker-compose"
else die "Не найден 'docker compose'."; fi
# Набор compose-файлов берём из COMPOSE_FILE в .env, если он там есть. Явные -f
# отключают COMPOSE_FILE целиком: установка, поднятая с дополнительным файлом
# (например, локальная резервная копия бэкенда и failover кабинета), после
# обычного ./update.sh молча теряла его — кабинет пересоздавался без запасного
# маршрута, а вторая копия бэкенда оставалась на старом образе. install.sh пишет в
# COMPOSE_FILE те же файлы, что стоят ниже по умолчанию, так что у стандартных
# установок ничего не меняется.
CF="$(env_get COMPOSE_FILE)"
if [ -n "$CF" ]; then
  COMPOSE=()
  IFS=: read -ra _cf <<<"$CF"
  for f in "${_cf[@]}"; do [ -n "$f" ] && COMPOSE+=(-f "$f"); done
else
  COMPOSE=(-f docker-compose.yml -f cabinet/docker-compose.cabinet.yml)
fi
# Кабинет поверх чужого бота работает через адаптер — он тоже собирается из кода
# репозитория. Без этого файла обновление оставляло адаптер на СТАРОМ образе
# (старые compose.py/route_map.json), а compose ругался «orphan containers»
# и по этой подсказке предлагал снести живой контейнер.
if is_bedolaga && [[ ":$CF:" != *":cabinet/docker-compose.adapter.yml:"* ]]; then
  COMPOSE+=(-f cabinet/docker-compose.adapter.yml)
fi

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

# записать VAR=VAL в .env (заменить строку или дописать)
set_env() {
  local var="$1" val="$2"
  if grep -qE "^$var=" .env; then
    awk -v k="$var" -v v="$val" 'BEGIN{FS="="} $1==k && !d {print k"="v; d=1; next} {print}' .env > .env.__tmp__ && mv .env.__tmp__ .env
  else
    printf '%s=%s\n' "$var" "$val" >> .env
  fi
}

# ── 0. Обновлять ли бота ──────────────────────────────────────────────────────
# Что умеет РАБОТАЮЩИЙ бот, читаем из его образа, а не из контейнера и не по сети:
# в контейнер на части установок примонтирован хостовый VERSION (после «только
# кабинет» он новее кода), а сам бот может лежать. `docker run --entrypoint sh` с
# --network none ничего не запускает и ни к чему не подключается. Один запуск на оба
# файла — вопрос не должен ждать лишнюю секунду.
BOT_PROBED=0; BOT_SEEN=0; BOT_VER=""; BOT_CAPS_FILE=""
probe_bot() {
  [ "$BOT_PROBED" = 0 ] || return 0
  BOT_PROBED=1
  local img out
  img="$(docker inspect -f '{{.Image}}' remnashop 2>/dev/null || true)"
  [ -n "$img" ] || return 0
  out="$(docker run --rm --network none --entrypoint sh "$img" -c \
    'cat /opt/remnashop/VERSION 2>/dev/null; echo @@; cat /opt/remnashop/src/web/cabinet_capabilities.py 2>/dev/null' \
    2>/dev/null || true)"
  case "$out" in *@@*) ;; *) return 0 ;; esac
  BOT_SEEN=1
  BOT_VER="$(printf '%s\n' "${out%%@@*}" | tr -d ' \r\n')"
  BOT_CAPS_FILE="${out#*@@}"
}
# Форматы строк заперты тестами: admin_src/tests/test_cabinet_capabilities.py и
# cabinet/src/lib/botCapabilities.test.ts. Разъедутся — sed молча потеряет записи.
running_cabinet_caps() { printf '%s\n' "$BOT_CAPS_FILE" | sed -nE 's/^    "([a-z0-9_]+)",$/\1/p'; }
running_bot_only()     { printf '%s\n' "$BOT_CAPS_FILE" | sed -nE 's/^    "([a-z0-9_]+)": "[^"]*",$/\1/p'; }
known_cabinet_caps()   { sed -nE 's/^  ([a-z0-9_]+): \{ since: "[^"]*", label: "([^"]*)" \},$/\1\t\2/p' cabinet/src/lib/botCapabilities.ts 2>/dev/null || true; }
known_bot_only()       { sed -nE 's/^    "([a-z0-9_]+)": "([^"]*)",$/\1\t\2/p' admin_src/src/web/cabinet_capabilities.py 2>/dev/null || true; }
# Подписи записей из $1 (токен<TAB>подпись), которых нет в списке токенов $2, — «    • …».
missing_labels() {
  local have="$2" tok label
  while IFS=$'\t' read -r tok label; do
    [ -n "$tok" ] || continue
    printf '%s\n' "$have" | grep -qxF -- "$tok" || printf '    • %s\n' "$label"
  done <<<"$1"
  return 0
}

ask_scope() {
  [ -z "$SCOPE" ] || return 0
  if [ ! -t 0 ]; then
    SCOPE=all
    info "Запуск без терминала — обновляю и бота, и кабинет (как раньше). Только кабинет: ./update.sh --cabinet-only"
    return 0
  fi
  local missing=""
  if ! is_bedolaga; then
    probe_bot
    [ "$BOT_SEEN" = 0 ] || missing="$(missing_labels "$(known_cabinet_caps)" "$(running_cabinet_caps)")"
  fi
  # Вопрос — в stderr: при `./update.sh | tee лог` stdout не терминал, а человек у экрана.
  {
    printf '\n%sОбновление RemnaShop%s\n' "$BOLD" "$RST"
    if is_bedolaga; then
      echo "  Кабинет работает поверх бота «Бедолага» через адаптер — адаптер обновится вместе"
      echo "  с кабинетом. Наш бот на этом сервере на функции кабинета не влияет."
      echo
      echo "  Д — обновить и наш бот: бэкап его базы, пересборка, короткий перезапуск."
      echo "  н — только кабинет и адаптер. Наш бот и его база не трогаются."
    else
      echo "  Кабинет (сайт и админка) обновится в любом случае. Решите, обновлять ли бота —"
      echo "  его код, фоновые задачи и базу данных."
      echo
      if [ "$BOT_SEEN" = 1 ]; then
        echo "  Сейчас работает бот версии ${BOT_VER:-неизвестной}."
        if [ -n "$missing" ]; then
          echo "  Нынешнему кабинету он уже не даёт (скрыто до обновления бота):"
          printf '%s\n' "$missing"
        fi
      else
        echo "  Бот сейчас не запущен (контейнера remnashop нет) — его версию не узнать."
      fi
      echo
      echo "  Д — обновить всё, как раньше: бэкап базы, пересборка бота и кабинета,"
      echo "      короткий перезапуск бота (пока он поднимается, Telegram-бот не отвечает)."
      echo "  н — обновить только кабинет. Бот и база не трогаются, бот работает дальше"
      echo "      на своей версии и не перезапускается."
      echo "      Чем рискуете: функции кабинета, которым нужен новый бот, останутся скрыты,"
      echo "      а исправления и защита в самом боте не установятся. Скрытое включится само,"
      echo "      когда обновите бота (./update.sh --with-bot) — кабинет пересобирать не нужно."
    fi
    echo
  } >&2
  local answer tries=0
  while :; do
    printf 'Обновить и бота? [Д/н]: ' >&2
    # EOF (Ctrl+D) — не «да»: выбор должен быть осознанным. Таймаута нет по той же
    # причине; для запуска без присмотра есть флаги.
    if ! IFS= read -r answer; then
      printf '\n' >&2
      die "Ответа нет — ничего не обновлял. Без вопроса: ./update.sh --with-bot или ./update.sh --cabinet-only"
    fi
    answer="${answer%$'\r'}"
    answer="${answer#"${answer%%[![:space:]]*}"}"; answer="${answer%"${answer##*[![:space:]]}"}"
    # Перечислением, а не ${answer,,}: под локалью C кириллица в нижний регистр не
    # приводится. «н» и «y» — одна клавиша в разных раскладках, поэтому выбор ниже
    # проговаривается словами: ошибившийся успеет нажать Ctrl+C до бэкапа и сборки.
    case "$answer" in
      ""|д|Д|да|Да|ДА|y|Y|yes|Yes|YES) SCOPE=all ;;
      н|Н|нет|Нет|НЕТ|n|N|no|No|NO) SCOPE=cabinet ;;
      *)
        tries=$((tries + 1))
        [ "$tries" -lt 3 ] || die "Не понял ответ — ничего не обновлял. Без вопроса: ./update.sh --with-bot или ./update.sh --cabinet-only"
        printf 'Ответьте «д» — обновить и бота, или «н» — только кабинет.\n' >&2
        continue ;;
    esac
    break
  done
  if [ "$SCOPE" = all ]; then info "Обновляю бота и кабинет."
  elif is_bedolaga; then info "Обновляю только кабинет и адаптер «Бедолаги» — наш бот не трогаю."
  else info "Обновляю только кабинет — бот и база не трогаются."; fi
}
# Флаг для перезапуска: решение уже принято, второй раз не спрашиваем.
scope_flag() { if [ "$SCOPE" = cabinet ]; then echo --cabinet-only; else echo --with-bot; fi; }

ask_scope

# ── 1. Бэкап БД ───────────────────────────────────────────────────────────────
# Дампы храним ВНЕ папки репозитория, чтобы дампы БД с данными физически не лежали
# рядом с git (защита от случайной утечки). Путь можно переопределить: BACKUP_DIR=…
BACKUP_DIR="${BACKUP_DIR:-/opt/remnashop-backups}"
BACKUP_KEEP="${BACKUP_KEEP:-10}"   # сколько последних дампов хранить
if [ "$BACKUP" = 1 ]; then
  if [ "$SCOPE" = cabinet ]; then
    # Контейнеры бота не пересоздаются → миграции не запускаются, а кабинет в базу не
    # пишет. Дамп был бы лишней минутой и лишней копией персональных данных на диске.
    info "Бэкап базы не нужен: бот и база не меняются (только кабинет)"
    # Каталог для бэкапа тома адаптера ниже — с теми же правами, что и для дампов.
    if is_bedolaga; then mkdir -p "$BACKUP_DIR"; chmod 700 "$BACKUP_DIR" 2>/dev/null || true; fi
  elif docker ps --format '{{.Names}}' | grep -qx remnashop-db; then
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
  # Память адаптера (паузы подписок) живёт в docker-томе, а не в БД бота: сам
  # бот «Бедолага» о заморозке не знает. Потеря тома = человек остался на паузе,
  # а вернуть накопленные дни нечем — поэтому кладём рядом с дампом.
  if is_bedolaga; then
    ADP="$(grep -E '^ADAPTER_CONTAINER=' .env 2>/dev/null | tail -1 | cut -d= -f2- || true)"
    ADP="${ADP:-remnashop-cabinet-adapter}"
    # ps -a, а не ps: у остановленного адаптера состояние ровно так же ценно,
    # а --volumes-from работает и с незапущенным контейнером.
    if docker ps -a --format '{{.Names}}' | grep -qx "$ADP"; then
      A="$BACKUP_DIR/adapter-state-$(date +%F-%H%M%S).tar.gz"
      # Копируем ЧУЖИМ контейнером (alpine) с --volumes-from: путь тома на хосте
      # знать не нужно, а имя тома у разных установок разное (префикс проекта).
      if docker run --rm --volumes-from "$ADP" -v "$BACKUP_DIR":/backup alpine \
           tar czf "/backup/$(basename "$A")" -C /data . >/dev/null 2>&1 && [ -s "$A" ]; then
        ok "Бэкап состояния адаптера (паузы) готов (${A}, $(du -h "$A" | cut -f1))"
        ls -1t "$BACKUP_DIR"/adapter-state-*.tar.gz 2>/dev/null | tail -n +"$((BACKUP_KEEP+1))" | xargs -r rm -f
      else
        rm -f "$A"
        warn "Не удалось сохранить состояние адаптера (${ADP}) — продолжаю без него."
        warn "Восстановить вручную: docker run --rm --volumes-from ${ADP} -v ${BACKUP_DIR}:/backup alpine tar czf /backup/adapter-state.tar.gz -C /data ."
      fi
    fi
  fi
else
  info "Бэкап пропущен (--no-backup)"
fi

# ── 1b. Снимок пользовательских ассетов (перед синком кода) ────────────────────
# assets/ бинд-маунтится = рабочее дерево git. Кастомные тексты (custom.ftl) и
# баннеры оператора раньше были ТРЕКНУТЫ → git reset --hard / архив их затирали
# (и фирменный баннер утекал в публичный репозиторий). Теперь они untracked+gitignore,
# а этот снапшот-ДО-синка + restore-ПОСЛЕ страхует правки даже на одноразовой миграции
# untrack и на случай случайного повторного трекинга. logo/branding/*.json уже
# в .gitignore — кладём в снапшот для полноты (их git и так не трогает).
ASSET_PRESERVE_DIR="${BACKUP_DIR}/assets-preserve-latest"

_list_user_assets() {
  find assets/translations -type f -name 'custom.ftl' 2>/dev/null
  find assets/banners -type f \( -name '*.jpg' -o -name '*.jpeg' -o -name '*.png' \
       -o -name '*.gif' -o -name '*.webp' \) 2>/dev/null
  for f in assets/logo.* assets/branding.json; do [ -f "$f" ] && printf '%s\n' "$f"; done
  # ВАЖНО: явный успех. Иначе статус функции = последняя итерация for (branding.json),
  # и при его отсутствии (свежая/site/HA-установка) вернулось бы 1 → под set -e+pipefail
  # это роняло ВЕСЬ апдейт до пересборки (тихий отказ обновлений).
  return 0
}

snapshot_user_assets() {
  rm -rf "$ASSET_PRESERVE_DIR" 2>/dev/null || true
  mkdir -p "$ASSET_PRESERVE_DIR" 2>/dev/null || {
    warn "нет доступа к ${ASSET_PRESERVE_DIR} — пропускаю снапшот ассетов"
    return 0
  }
  _list_user_assets | while IFS= read -r f; do
    [ -f "$f" ] || continue
    mkdir -p "$ASSET_PRESERVE_DIR/$(dirname "$f")" 2>/dev/null || continue
    cp -a "$f" "$ASSET_PRESERVE_DIR/$f" 2>/dev/null || true
  done
  return 0
}

restore_user_assets() {
  [ -d "$ASSET_PRESERVE_DIR" ] || return 0
  ( cd "$ASSET_PRESERVE_DIR" && find . -type f 2>/dev/null ) | sed 's#^\./##' | while IFS= read -r f; do
    [ -n "$f" ] || continue
    mkdir -p "$(dirname "$f")"
    cp -a "$ASSET_PRESERVE_DIR/$f" "$f" 2>/dev/null || true
  done
}

# Снапшот делаем только на ИСХОДНОМ запуске (не на self-update re-exec тарбол-ветки,
# где код уже подменён). Снапшот персистентный (в $BACKUP_DIR) → переживает exec.
if [ "${_SELF_UPDATED:-0}" != 1 ]; then
  snapshot_user_assets || true  # снапшот ассетов best-effort — не блокирует обновление
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
        cp -a "$TMP"/. .            # новый код поверх; gitignored-рантайм не в архиве,
                                    # а пользовательские ассеты вернёт restore ниже
        rm -rf "$TMP"
        ok "Код обновлён из архива"
        # Перезапуск на свежем update.sh (иначе bash может дочитать старую версию файла).
        # Бэкап уже сделан этим запуском → на перезапуске его пропускаем. Выбор «бот или
        # только кабинет» передаём флагом: без него новый скрипт спросил бы второй раз,
        # а без терминала молча обновил бы и бота вопреки --cabinet-only.
        exec env _SELF_UPDATED=1 bash "$0" --no-backup "$(scope_flag)"
      fi
      rm -rf "$TMP"
      warn "Не удалось скачать архив (нет сети/доступа) — собираю из текущего кода."
    fi
  fi
  # Заодно (best-effort) проверяем, не вышла ли новая версия базового бота. При «только
  # кабинет» бот не трогаем — и советовать сейчас обновить его базу не к месту.
  if [ "$SCOPE" = all ]; then
    CUR_BASE="$(current_base_tag)"; LATEST_BASE="$(fetch_latest_base 2>/dev/null || true)"
    if [ -n "$LATEST_BASE" ] && [ "$LATEST_BASE" != "$CUR_BASE" ]; then
      warn "Доступна новая версия базового бота: ${CUR_BASE} → ${LATEST_BASE}"
      warn "Подтянуть (с проверкой совместимости): ./update.sh --base latest"
    fi
  fi
fi

# Возвращаем пользовательские ассеты, если синк кода их затёр (см. секцию 1b).
# Идемпотентно; в тарбол-ветке выполняется в re-exec-процессе (снапшот персистентный).
restore_user_assets || true  # best-effort — не блокирует пересборку

# Памяти хватит на сборку кабинета?
#
# ЗАЧЕМ. Самая частая поломка установки — не наш код, а нехватка памяти: `npm ci`
# выходит с «Exit handler never called!», а `vite build` молча убивается ядром. Со
# стороны это выглядит как сломанное обновление (жалоба 19 сентября на 1.4.1), и человек
# идёт искать ошибку в проекте. Предупредить дешевле, чем разбирать потом.
#
# Считаем ДОСТУПНУЮ память плюс swap: 1 ГБ свободной оперативной памяти и 2 ГБ swap для
# сборки достаточно, а 700 МБ без swap — почти гарантированное падение.
check_build_memory() {
  command -v free >/dev/null 2>&1 || return 0
  local avail_mb swap_mb total_mb
  avail_mb="$(free -m 2>/dev/null | awk '/^Mem:/{print $7}')"
  swap_mb="$(free -m 2>/dev/null | awk '/^Swap:/{print $2}')"
  [ -n "$avail_mb" ] || return 0
  total_mb=$(( avail_mb + ${swap_mb:-0} ))
  [ "$total_mb" -lt 1400 ] || return 0
  warn "Для сборки кабинета мало памяти: свободно ${avail_mb} МБ, swap ${swap_mb:-0} МБ."
  warn "Сборка фронтенда обычно падает с «Exit handler never called!» или молча обрывается."
  warn "Помогает временный файл подкачки — могу включить его сам."

  # Предлагаем починить, а не только диагностировать: человек пришёл обновиться, а не
  # изучать управление памятью. Без терминала (запуск из скрипта) молча не трогаем
  # чужую машину — только совет.
  if [ ! -t 0 ] || [ "${RS_NO_SWAP:-}" = "1" ]; then
    warn "  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile"
    warn "Удалить потом: swapoff /swapfile && rm -f /swapfile"
    return 0
  fi
  printf 'Создать временный файл подкачки 2 ГБ? [Д/н]: ' >&2
  local answer=""
  IFS= read -r answer || answer=""
  case "$answer" in
    [нНnN]*) warn "Хорошо, продолжаю без подкачки — сборка может не дойти до конца."; return 0 ;;
  esac
  if [ -e /swapfile ]; then
    warn "/swapfile уже есть — включаю его."
  elif ! fallocate -l 2G /swapfile 2>/dev/null && ! dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none; then
    warn "Не получилось создать /swapfile — продолжаю без подкачки."
    rm -f /swapfile 2>/dev/null || true
    return 0
  fi
  chmod 600 /swapfile 2>/dev/null || true
  mkswap /swapfile >/dev/null 2>&1 || true
  if swapon /swapfile 2>/dev/null; then
    ok "Подкачка включена (2 ГБ). После обновления можно убрать: swapoff /swapfile && rm -f /swapfile"
  else
    warn "Подкачку включить не вышло (бывает в контейнере или при запрете ядра) — продолжаю так."
  fi
}

# ── 3. Пересборка и запуск (overlay бота + кабинет) ───────────────────────────
# Дотянемся ли мы до registry.npmjs.org?
#
# ЗАЧЕМ. Вторая половина падений сборки — не память, а сеть: npm 10 прячет ЛЮБУЮ свою
# беду за «Exit handler never called!», и человек час ищет ошибку в проекте, хотя у
# него просто не открывается реестр (маршрут, DNS, сломанный IPv6, блокировка).
# Проверка занимает секунды и делает диагноз до, а не после двадцати минут сборки.
check_registry() {
  command -v docker >/dev/null 2>&1 || return 0
  if docker run --rm --network bridge node:22-alpine \
      sh -c 'npm ping --registry https://registry.npmjs.org >/dev/null 2>&1' 2>/dev/null; then
    return 0
  fi
  warn "Не получилось достучаться до registry.npmjs.org из docker-контейнера."
  warn "Сборка кабинета без него не соберётся — и упадёт с невнятной ошибкой npm."
  warn "Проверьте DNS и доступ к сети; на серверах со сломанным IPv6 помогает"
  warn "отключить его для docker или задать IPv4-DNS в /etc/docker/daemon.json."
}

# Учение «восстановись из бэкапа» — стоит ли оно в расписании?
#
# ЗАЧЕМ. Бэкап, который никто не разворачивал, — это надежда, а не резервная копия.
# Мониторинг умеет сказать «бэкап не делается», но «бэкап не разворачивается» он
# узнаёт только из учения (scripts/db-restore-verify.sh): оно поднимает одноразовый
# Postgres, восстанавливает последний дамп, гонит по нему миграции и считает строки.
# Ничего в проде не трогает. Раз в месяц — достаточно, чтобы поломка не ждала беды.
check_restore_drill() {
  command -v crontab >/dev/null 2>&1 || return 0
  # Каталог установки — тот, из которого запущен update.sh: свой путь скрипт знает
  # только так, отдельной переменной с корнем в нём нет.
  local here; here="$(cd "$(dirname "$0")" && pwd)"
  [ -f "$here/scripts/db-restore-verify.sh" ] || return 0
  crontab -l 2>/dev/null | grep -q 'db-restore-verify.sh' && return 0

  local line="17 5 1 * * $here/scripts/db-restore-verify.sh >> /var/log/remnashop_restore_drill.log 2>&1"
  warn "Проверка восстановления бэкапа не стоит в расписании."
  warn "Она раз в месяц разворачивает последний бэкап в одноразовый Postgres и проверяет, что он живой."
  if [ ! -t 0 ] || [ "${RS_NO_CRON:-}" = "1" ]; then
    warn "Поставить вручную:  (crontab -l 2>/dev/null; echo '$line') | crontab -"
    return 0
  fi
  printf 'Добавить ежемесячную проверку восстановления бэкапа в cron? [Д/н]: ' >&2
  local answer=""
  IFS= read -r answer || answer=""
  case "$answer" in
    [нНnN]*) warn "Хорошо, пропускаю — поставить можно позже."; return 0 ;;
  esac
  if (crontab -l 2>/dev/null; echo "$line") | crontab - 2>/dev/null; then
    ok "Проверка восстановления добавлена в cron (1-го числа в 05:17)."
  else
    warn "Не получилось записать cron — поставьте вручную:"
    warn "  (crontab -l 2>/dev/null; echo '$line') | crontab -"
  fi
}

check_build_memory
check_registry
check_restore_drill

if [ "$SCOPE" = cabinet ]; then
  # Адаптер «Бедолаги» — реализация контракта кабинета, а не бот: он меняется в тех же
  # коммитах, и новый кабинет со старым адаптером посчитал бы доступным то, что адаптер
  # ещё не выключает. --no-deps: соседей (бот, HA-копию, воркеры, базу) не трогаем.
  SERVICES=(cabinet)
  if is_bedolaga; then SERVICES+=(cabinet-adapter); fi
  info "Сборка и запуск ТОЛЬКО кабинета (${SERVICES[*]}, --build); бот не трогаю…"
  $DC "${COMPOSE[@]}" up -d --build --no-deps "${SERVICES[@]}"
else
  info "Сборка и запуск (overlay бота + кабинет, --build)…"
  $DC "${COMPOSE[@]}" up -d --build
fi

# ── 4. Итог «только кабинет» ──────────────────────────────────────────────────
# Владелец должен увидеть, что осталось скрытым и чем это включить. Что нужно кабинету —
# из свежего кода кабинета, заметные изменения «только в боте» — из свежего кода бота,
# что есть у работающего бота — из его образа (см. probe_bot).
cabinet_only_summary() {
  local rev="" hidden bot_only
  if [ -d .git ]; then rev=" (код $(git rev-parse --short HEAD 2>/dev/null || echo '?'))"; fi
  echo
  if is_bedolaga; then
    ok "Обновлены кабинет и адаптер «Бедолаги»${rev}. Наш бот не тронут."
    return 0
  fi
  ok "Обновлён только кабинет${rev}. Бот не тронут."
  probe_bot
  if [ "$BOT_SEEN" = 0 ]; then
    warn "Версию работающего бота узнать не удалось (нет контейнера remnashop)."
  else
    hidden="$(missing_labels "$(known_cabinet_caps)" "$(running_cabinet_caps)")"
    bot_only="$(missing_labels "$(known_bot_only)" "$(running_bot_only)")"
    if [ -z "$hidden" ]; then
      ok "Бот (версия ${BOT_VER:-неизвестна}) умеет всё, что нужно этой версии кабинета."
    else
      warn "Бот работает на версии ${BOT_VER:-неизвестной}. До его обновления в кабинете не будет:"
      printf '%s\n' "$hidden"
    fi
    if [ -n "$bot_only" ]; then
      warn "В самом боте не установлено:"
      printf '%s\n' "$bot_only"
    fi
  fi
  echo "  Обновить бота, когда будете готовы:  ./update.sh --with-bot"
  echo "  Любая ручная пересборка (docker compose up -d --build) тоже обновит бота —"
  echo "  но без бэкапа базы. Обновляйте его через ./update.sh."
}

# ── 5. Логи ───────────────────────────────────────────────────────────────────
if [ "$SCOPE" = cabinet ]; then
  cabinet_only_summary
  echo
  ok "${BOLD}Обновление применено.${RST} Логи кабинета (${DIM}Ctrl+C — выход${RST}):"
  $DC "${COMPOSE[@]}" logs -f --tail=30 "${SERVICES[@]}"
else
  echo
  ok "${BOLD}Обновление применено.${RST} Логи (${DIM}Ctrl+C — выход${RST}):"
  $DC "${COMPOSE[@]}" logs -f --tail=30
fi
