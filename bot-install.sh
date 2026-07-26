#!/usr/bin/env bash
#
# RemnaShop — установка кабинета+админки на сервере С БОТОМ (co-located) одной командой.
#
#   curl -fsSL https://raw.githubusercontent.com/velamaker/remnashop-cabinet/main/bot-install.sh | sudo bash
#
# Тонкая обёртка: бот уже установлен (значит Docker есть — НЕ ставим его заново).
# Скрипт только тянет код в каталог бота и запускает install.sh (он допишет
# web-переменные, сгенерит секреты кабинета, соберёт overlay+кабинет).
#
# Требуется заполненный .env бота. Если его нет — скрипт создаст из примера и
# попросит заполнить раздел «ОБЯЗАТЕЛЬНО» (BOT_TOKEN, REMNAWAVE_*, APP_DOMAIN…),
# затем запусти снова.

set -euo pipefail

REPO_URL="https://github.com/velamaker/remnashop-cabinet"
BRANCH="${BRANCH:-main}"
# Пиннинг supply-chain: REF может быть веткой, тегом или commit-SHA (воспроизводимость).
# Для проверки целостности задайте EXPECT_SHA256 (sha256 тарбола) — тогда установка
# прервётся при несовпадении. По умолчанию — ветка (как раньше), но рекомендуется тег/SHA.
REF="${REF:-$BRANCH}"
EXPECT_SHA256="${EXPECT_SHA256:-}"
DEST="${DEST:-/opt/remnashop}"

# Режим установки для install.sh:
#   (пусто) — overlay + локальный кабинет (кабинет и бот на одном сервере)
#   api     — ТОЛЬКО overlay/API (кабинет будет на отдельной машине)
MODE_ARG="${1:-}"
case "$MODE_ARG" in ""|api) ;; *) MODE_ARG="" ;; esac

if [ -t 1 ]; then
  BOLD=$'\e[1m'; DIM=$'\e[2m'; GRN=$'\e[32m'; YLW=$'\e[33m'; CYN=$'\e[36m'; RST=$'\e[0m'
else BOLD=""; DIM=""; GRN=""; YLW=""; CYN=""; RST=""; fi
info() { printf '%s➜%s %s\n' "$CYN" "$RST" "$*"; }
ok()   { printf '%s✓%s %s\n' "$GRN" "$RST" "$*"; }
warn() { printf '%s!%s %s\n' "$YLW" "$RST" "$*"; }
die()  { printf '✗ %s\n' "$*" >&2; exit 1; }
say()  { printf '%s\n' "$*"; }

if [ "$MODE_ARG" = api ]; then
  say "${BOLD}RemnaShop — установка ТОЛЬКО API на сервере бота (кабинет на др. машине)${RST}"
else
  say "${BOLD}RemnaShop — установка кабинета на сервере с ботом (одной командой)${RST}"
fi

[ "$(id -u)" = 0 ] || die "Запустите от root (или через sudo)."
command -v curl >/dev/null 2>&1 || die "Нужен curl."

# Docker НЕ ставим — бот уже работает, значит Docker есть. Только проверяем.
command -v docker >/dev/null 2>&1 || die "Не найден docker. Этот режим — для сервера, где бот УЖЕ установлен."
docker compose version >/dev/null 2>&1 || command -v docker-compose >/dev/null 2>&1 \
  || die "Не найден 'docker compose'."
ok "Docker на месте (не трогаю)"

# Код проекта (тарбол устойчивее git на плохом канале). REF = ветка/тег/commit-SHA;
# при заданном EXPECT_SHA256 проверяем целостность архива перед распаковкой.
if [ ! -f "$DEST/install.sh" ]; then
  info "Скачиваю код в $DEST (ref: $REF)…"
  mkdir -p "$DEST"
  TARBALL="$(mktemp)"
  curl -fL "$REPO_URL/archive/$REF.tar.gz" -o "$TARBALL" || die "Не удалось скачать код (ref=$REF)."
  if [ -n "$EXPECT_SHA256" ]; then
    GOT="$(sha256sum "$TARBALL" | awk '{print $1}')"
    [ "$GOT" = "$EXPECT_SHA256" ] || { rm -f "$TARBALL"; die "SHA-256 не совпал: ждали $EXPECT_SHA256, получили $GOT"; }
    ok "Контрольная сумма архива верна"
  fi
  tar xz -C "$DEST" --strip-components=1 -f "$TARBALL" || { rm -f "$TARBALL"; die "Распаковка не удалась."; }
  rm -f "$TARBALL"
fi
cd "$DEST"
ok "Код в $DEST"

# .env бота: если нет — создаём из примера и просим заполнить секреты бота.
if [ ! -f .env ]; then
  cp .env.example .env
  warn "Создан .env из примера — это дополнение ставится поверх НАСТРОЕННОГО бота."
  say  ""
  say  "  Заполни в ${BOLD}$DEST/.env${RST} раздел «ОБЯЗАТЕЛЬНО»:"
  say  "    ${DIM}BOT_TOKEN, BOT_OWNER_ID, BOT_SUPPORT_USERNAME, APP_DOMAIN,"
  say  "    REMNAWAVE_HOST, REMNAWAVE_TOKEN${RST}"
  say  "  (секреты APP_CRYPT_KEY/DATABASE_PASSWORD/*_SECRET сгенери по подсказкам в файле)"
  say  ""
  die  "Заполни .env и запусти снова ту же команду."
fi

# Всё готово — отдаём управление install.sh (он спросит пару значений, соберёт стек).
say ""
info "Запускаю install.sh ${MODE_ARG}…"
exec bash install.sh ${MODE_ARG}
