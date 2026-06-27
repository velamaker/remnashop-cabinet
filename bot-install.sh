#!/usr/bin/env bash
#
# RemnaShop — установка кабинета+админки на сервере С БОТОМ (co-located) одной командой.
#
#   curl -fsSL https://raw.githubusercontent.com/alexdsndr161rus2015-maker/remnashop-cabinet/main/bot-install.sh | sudo bash
#
# Тонкая обёртка: бот уже установлен (значит Docker есть — НЕ ставим его заново).
# Скрипт только тянет код в каталог бота и запускает install.sh (он допишет
# web-переменные, сгенерит секреты кабинета, соберёт overlay+кабинет).
#
# Требуется заполненный .env бота. Если его нет — скрипт создаст из примера и
# попросит заполнить раздел «ОБЯЗАТЕЛЬНО» (BOT_TOKEN, REMNAWAVE_*, APP_DOMAIN…),
# затем запусти снова.

set -euo pipefail

REPO_URL="https://github.com/alexdsndr161rus2015-maker/remnashop-cabinet"
BRANCH="${BRANCH:-main}"
DEST="${DEST:-/opt/remnashop}"

if [ -t 1 ]; then
  BOLD=$'\e[1m'; DIM=$'\e[2m'; GRN=$'\e[32m'; YLW=$'\e[33m'; CYN=$'\e[36m'; RST=$'\e[0m'
else BOLD=""; DIM=""; GRN=""; YLW=""; CYN=""; RST=""; fi
info() { printf '%s➜%s %s\n' "$CYN" "$RST" "$*"; }
ok()   { printf '%s✓%s %s\n' "$GRN" "$RST" "$*"; }
warn() { printf '%s!%s %s\n' "$YLW" "$RST" "$*"; }
die()  { printf '✗ %s\n' "$*" >&2; exit 1; }
say()  { printf '%s\n' "$*"; }

say "${BOLD}RemnaShop — установка кабинета на сервере с ботом (одной командой)${RST}"

[ "$(id -u)" = 0 ] || die "Запустите от root (или через sudo)."
command -v curl >/dev/null 2>&1 || die "Нужен curl."

# Docker НЕ ставим — бот уже работает, значит Docker есть. Только проверяем.
command -v docker >/dev/null 2>&1 || die "Не найден docker. Этот режим — для сервера, где бот УЖЕ установлен."
docker compose version >/dev/null 2>&1 || command -v docker-compose >/dev/null 2>&1 \
  || die "Не найден 'docker compose'."
ok "Docker на месте (не трогаю)"

# Код проекта (тарбол устойчивее git на плохом канале).
if [ ! -f "$DEST/install.sh" ]; then
  info "Скачиваю код в $DEST…"
  mkdir -p "$DEST"
  curl -fL "$REPO_URL/archive/refs/heads/$BRANCH.tar.gz" | tar xz -C "$DEST" --strip-components=1
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

# Всё готово — отдаём управление install.sh (он спросит 2 значения, соберёт стек).
say ""
info "Запускаю install.sh…"
exec bash install.sh
