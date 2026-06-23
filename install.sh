#!/usr/bin/env bash
#
# RемнаShop (кабинет + админка) — установка-дополнение поверх готового бота.
#
#   ./install.sh
#
# Предполагается, что бот RемнаShop уже настроен и .env заполнен.
# Скрипт НЕ трогает существующие переменные — он только ДОПИСЫВАЕТ те, что
# добавляет это дополнение (кабинет / web / email):
#   • секреты (APP_API_KEY, APP_JWT_SECRET) генерирует сам;
#   • спрашивает только то, что нельзя сгенерировать (username бота, URL кабинета);
#   • email — по желанию.
# Затем собирает overlay-образ бота и веб-кабинет и поднимает всё.
#
# Уже заданные (непустые) переменные остаются как есть. Повторный запуск безопасен.

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

# ── оформление ─────────────────────────────────────────────────────────────
if [ -t 1 ]; then
  BOLD=$'\e[1m'; DIM=$'\e[2m'; RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; CYN=$'\e[36m'; RST=$'\e[0m'
else BOLD=""; DIM=""; RED=""; GRN=""; YLW=""; CYN=""; RST=""; fi
say()  { printf '%s\n' "$*"; }
info() { printf '%s➜%s %s\n' "$CYN" "$RST" "$*"; }
ok()   { printf '%s✓%s %s\n' "$GRN" "$RST" "$*"; }
warn() { printf '%s!%s %s\n' "$YLW" "$RST" "$*"; }
die()  { printf '%s✗ %s%s\n' "$RED" "$*" "$RST" >&2; exit 1; }

say "${BOLD}RемнаShop — установка кабинета и админки${RST}"

# ── зависимости ──────────────────────────────────────────────────────────────
command -v docker  >/dev/null 2>&1 || die "Не найден docker: https://docs.docker.com/engine/install/"
command -v openssl >/dev/null 2>&1 || die "Не найден openssl."
if docker compose version >/dev/null 2>&1; then DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then DC="docker-compose"
else die "Не найден 'docker compose'."; fi
ok "Зависимости на месте"

# ── проверяем существующий .env бота ──────────────────────────────────────────
if [ ! -f .env ]; then
  warn ".env не найден."
  warn "Это дополнение ставится поверх уже настроенного бота RемнаShop."
  warn "Сначала настройте бота (cp .env.example .env и заполните), затем запустите снова."
  die "Нет .env — нечего дополнять."
fi
ok "Найден существующий .env — дополняю недостающим"

# ── утилиты работы с .env ────────────────────────────────────────────────────
getval() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true; }
# need_value VAR → 0, если переменная отсутствует ИЛИ пустая
need_value() {
  local line; line="$(grep -E "^$1=" .env | tail -1 || true)"
  [ -z "$line" ] && return 0
  [ -z "${line#*=}" ] && return 0
  return 1
}
declare -a ADD_LINES=()
# ensure VAR VALUE — записать значение, только если VAR отсутствует/пустая.
# Если строка есть, но пустая — заменяем на месте; если нет — добавим в конец.
ensure() {
  local var="$1" val="$2"
  need_value "$var" || return 0
  if grep -qE "^$var=" .env; then
    awk -v k="$var" -v v="$val" 'BEGIN{FS="="} $1==k && !d {print k"="v; d=1; next} {print}' .env > .env.__tmp__ \
      && mv .env.__tmp__ .env
  else
    ADD_LINES+=("$var=$val")
  fi
}

# ── ввод (только если значение ещё не задано) ─────────────────────────────────
ask() { # ask VAR "Подсказка" ["default"]
  local var="$1" prompt="$2" def="${3:-}" input
  need_value "$var" || { ok "  $var уже задан — пропускаю"; return; }
  if [ -n "$def" ]; then
    read -r -p "$(printf '%s%s%s [%s]: ' "$BOLD" "$prompt" "$RST" "$def")" input </dev/tty || true
    ASKED="${input:-$def}"
  else
    while :; do
      read -r -p "$(printf '%s%s%s: ' "$BOLD" "$prompt" "$RST")" input </dev/tty || true
      [ -n "$input" ] && { ASKED="$input"; break; }
      warn "Поле обязательно."
    done
  fi
}
ask_yn() { local input; read -r -p "$(printf '%s%s%s [y/N]: ' "$BOLD" "$1" "$RST")" input </dev/tty || true; [[ "${input:-n}" =~ ^[YyДд] ]]; }

gen_hex() { openssl rand -hex "${1:-32}" | tr -d '\n'; }

say ""
say "${BOLD}Недостающие данные${RST} ${DIM}(остальное — автоматически)${RST}"

# username бота — нужен кабинету для входа через Telegram
ask TELEGRAM_BOT_USERNAME "  Username бота без @ (для входа в кабинет)" && [ -n "${ASKED:-}" ] && ensure TELEGRAM_BOT_USERNAME "$ASKED"; ASKED=""

# публичный URL кабинета (дефолт — по APP_DOMAIN из существующего .env)
DOM="$(getval APP_DOMAIN)"
ask WEB_CABINET_URL "  Публичный URL кабинета" "${DOM:+https://cabinet.$DOM}"
[ -n "${ASKED:-}" ] && ensure WEB_CABINET_URL "$ASKED"
CAB_URL="$(getval WEB_CABINET_URL)"; CAB_URL="${CAB_URL:-${ASKED:-}}"; ASKED=""

# разрешённый origin = URL кабинета
ensure APP_ORIGINS "$CAB_URL"

# секреты web-части
ensure APP_API_KEY  "$(gen_hex 32)"
ensure APP_JWT_SECRET "$(gen_hex 32)"

# дефолты
ensure WEB_ENABLED true
ensure BOT_MINI_APP_RESERVE true

# ── email (необязательно) ─────────────────────────────────────────────────────
if need_value EMAIL_ENABLED; then
  say ""
  if ask_yn "Настроить отправку email сейчас? (нужно для регистрации по почте)"; then
    ensure EMAIL_ENABLED true
    info "Email (Brevo рекомендуется — обходит блокировки SMTP)"
    ask EMAIL_BREVO_API_KEY "  Brevo API key (xkeysib-…), Enter — пропустить" "-"
    [ "${ASKED:-}" != "-" ] && ensure EMAIL_BREVO_API_KEY "${ASKED}"; ASKED=""
    ask EMAIL_FROM_EMAIL "  Адрес отправителя (From)"; [ -n "${ASKED:-}" ] && ensure EMAIL_FROM_EMAIL "$ASKED"; FROM="${ASKED:-}"; ASKED=""
    ask EMAIL_FROM_NAME  "  Имя отправителя" "RемнаShop"; ensure EMAIL_FROM_NAME "$ASKED"; ASKED=""
    ask EMAIL_HOST "  SMTP host" "smtp.gmail.com"; ensure EMAIL_HOST "$ASKED"; ASKED=""
    ask EMAIL_PORT "  SMTP port" "587"; ensure EMAIL_PORT "$ASKED"; ASKED=""
    ask EMAIL_USERNAME "  SMTP логин" "${FROM}"; ensure EMAIL_USERNAME "$ASKED"; ASKED=""
    ask EMAIL_PASSWORD "  SMTP пароль / app password, Enter — пропустить" "-"
    [ "${ASKED:-}" != "-" ] && ensure EMAIL_PASSWORD "${ASKED}"; ASKED=""
  else
    ensure EMAIL_ENABLED false
    warn "Email отключён — регистрация только через Telegram. Включить позже можно в .env."
  fi
fi
# гарантируем, что email-ключи существуют (с дефолтами), чтобы конфиг был полным
ensure EMAIL_HOST smtp.gmail.com
ensure EMAIL_PORT 587
ensure EMAIL_USE_TLS true
ensure EMAIL_USE_SSL false
ensure EMAIL_USERNAME ""
ensure EMAIL_PASSWORD ""
ensure EMAIL_FROM_EMAIL ""
ensure EMAIL_FROM_NAME "RемнаShop"
ensure EMAIL_BREVO_API_KEY ""

# ── дописываем отсутствовавшие ключи одним блоком ─────────────────────────────
if [ "${#ADD_LINES[@]}" -gt 0 ]; then
  {
    printf '\n# ── Добавлено RемнаShop (кабинет/web/email) — %s ──\n' "$(date +%F)"
    printf '%s\n' "${ADD_LINES[@]}"
  } >> .env
  ok "Добавлено новых строк в .env: ${#ADD_LINES[@]}"
else
  ok ".env уже содержит все нужные ключи — добавлять нечего"
fi

# ── docker-сеть ──────────────────────────────────────────────────────────────
docker network inspect remnawave-network >/dev/null 2>&1 || {
  info "Создаю docker-сеть remnawave-network…"; docker network create remnawave-network >/dev/null; }

# ── сборка и запуск ──────────────────────────────────────────────────────────
say ""
info "Собираю и поднимаю бота (overlay), воркеры и кабинет…"
$DC -f docker-compose.yml -f cabinet/docker-compose.cabinet.yml up -d --build

say ""
ok "${BOLD}Готово!${RST}"
say "  Бот и API:  ${DIM}127.0.0.1:5000${RST}"
say "  Кабинет:    ${DIM}127.0.0.1:5002${RST}  → проксируйте на ${BOLD}${CAB_URL:-ваш домен кабинета}${RST}"
say ""
say "  Логи:   ${DIM}$DC -f docker-compose.yml -f cabinet/docker-compose.cabinet.yml logs -f${RST}"
say "  ${YLW}Дальше:${RST} reverse-proxy с TLS на порты 5000 (API/вебхуки) и 5002 (кабинет)."
