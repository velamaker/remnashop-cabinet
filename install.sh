#!/usr/bin/env bash
#
# RемнаShop — установка одной командой.
#
#   ./install.sh
#
# Скрипт:
#   • проверяет зависимости (docker, docker compose, openssl);
#   • создаёт docker-сеть remnawave-network, если её нет;
#   • генерирует .env: секреты создаёт сам, ставит разумные значения по умолчанию,
#     а спрашивает ТОЛЬКО то, что нельзя сгенерировать (токен бота, домен и т.п.);
#   • собирает и запускает бота, воркеры и веб-кабинет.
#
# Повторный запуск НЕ перезаписывает существующий .env (только пересобирает/поднимает).
# Запросы можно пропустить, заранее экспортировав переменные окружения с теми же именами.

set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

# ── оформление ─────────────────────────────────────────────────────────────
if [ -t 1 ]; then
  BOLD=$'\e[1m'; DIM=$'\e[2m'; RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; CYN=$'\e[36m'; RST=$'\e[0m'
else
  BOLD=""; DIM=""; RED=""; GRN=""; YLW=""; CYN=""; RST=""
fi
say()  { printf '%s\n' "$*"; }
info() { printf '%s➜%s %s\n' "$CYN" "$RST" "$*"; }
ok()   { printf '%s✓%s %s\n' "$GRN" "$RST" "$*"; }
warn() { printf '%s!%s %s\n' "$YLW" "$RST" "$*"; }
die()  { printf '%s✗ %s%s\n' "$RED" "$*" "$RST" >&2; exit 1; }

# ── 0. зависимости ─────────────────────────────────────────────────────────
say "${BOLD}RемнаShop — установка${RST}"
command -v docker  >/dev/null 2>&1 || die "Не найден docker. Установите Docker: https://docs.docker.com/engine/install/"
command -v openssl >/dev/null 2>&1 || die "Не найден openssl."
if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DC="docker-compose"
else
  die "Не найден 'docker compose'. Обновите Docker."
fi
ok "Зависимости на месте"

# ── генераторы секретов ────────────────────────────────────────────────────
gen_b64() { openssl rand -base64 "${1:-32}" | tr -d '\n'; }   # APP_CRYPT_KEY (44 симв.)
gen_hex() { openssl rand -hex "${1:-32}" | tr -d '\n'; }      # прочие секреты

# ── ввод значений ──────────────────────────────────────────────────────────
# ask VAR "Подсказка" ["значение по умолчанию"]
# Если переменная VAR уже задана в окружении — берём её без вопроса.
declare -A ENVVAL
ask() {
  local var="$1" prompt="$2" def="${3:-}" cur input
  cur="${!var:-}"
  if [ -n "$cur" ]; then ENVVAL[$var]="$cur"; return; fi
  if [ -n "$def" ]; then
    read -r -p "$(printf '%s%s%s [%s]: ' "$BOLD" "$prompt" "$RST" "$def")" input || true
    ENVVAL[$var]="${input:-$def}"
  else
    while :; do
      read -r -p "$(printf '%s%s%s: ' "$BOLD" "$prompt" "$RST")" input || true
      [ -n "$input" ] && { ENVVAL[$var]="$input"; break; }
      warn "Это поле обязательно."
    done
  fi
}
ask_yn() { # ask_yn "Вопрос" default(y/n) → возвращает 0 если да
  local prompt="$1" def="${2:-n}" input
  read -r -p "$(printf '%s%s%s [%s]: ' "$BOLD" "$prompt" "$RST" "$([ "$def" = y ] && echo Y/n || echo y/N)")" input || true
  input="${input:-$def}"
  [[ "$input" =~ ^[YyДд] ]]
}

if [ -f .env ]; then
  warn ".env уже существует — пропускаю настройку, перехожу к запуску."
  warn "Чтобы настроить заново — удалите/переименуйте .env и запустите снова."
else
  say ""
  say "${BOLD}Введите недостающие данные${RST} ${DIM}(остальное настроится автоматически)${RST}"
  say ""

  info "Telegram-бот"
  ask BOT_TOKEN            "  Токен бота (от @BotFather)"
  ask TELEGRAM_BOT_USERNAME "  Username бота без @ (напр. My_VPN_Bot)"
  ask BOT_OWNER_ID         "  Ваш Telegram ID (числовой, владелец)"
  ask BOT_SUPPORT_USERNAME "  Username поддержки без @"

  say ""
  info "Адреса"
  ask APP_DOMAIN     "  Домен бота без https:// (для вебхуков)"
  ask WEB_CABINET_URL "  Публичный URL кабинета" "https://cabinet.${ENVVAL[APP_DOMAIN]}"

  say ""
  info "Remnawave"
  ask REMNAWAVE_HOST  "  Хост/имя сервиса Remnawave API" "remnawave"
  ask REMNAWAVE_TOKEN "  API-токен Remnawave"

  # ── email (необязательно) ─────────────────────────────────────────────────
  EMAIL_ENABLED=false
  say ""
  if ask_yn "Настроить отправку email сейчас? (нужно для регистрации по почте)" n; then
    EMAIL_ENABLED=true
    info "Email (Brevo рекомендуется — обходит блокировки SMTP)"
    ask EMAIL_BREVO_API_KEY "  Brevo API key (xkeysib-…), пусто — обычный SMTP" "_skip_"
    ask EMAIL_FROM_EMAIL    "  Адрес отправителя (From)"
    ask EMAIL_FROM_NAME     "  Имя отправителя" "RемнаShop"
    ask EMAIL_HOST          "  SMTP host" "smtp.gmail.com"
    ask EMAIL_PORT          "  SMTP port" "587"
    ask EMAIL_USERNAME      "  SMTP логин" "${ENVVAL[EMAIL_FROM_EMAIL]}"
    ask EMAIL_PASSWORD      "  SMTP пароль / app password" "_skip_"
    [ "${ENVVAL[EMAIL_BREVO_API_KEY]}" = "_skip_" ] && ENVVAL[EMAIL_BREVO_API_KEY]=""
    [ "${ENVVAL[EMAIL_PASSWORD]:-}" = "_skip_" ]     && ENVVAL[EMAIL_PASSWORD]=""
  else
    warn "Email отключён — регистрация только через Telegram. Включить позже можно в .env."
  fi

  # ── секреты (генерируются) ─────────────────────────────────────────────────
  info "Генерирую секреты…"
  APP_CRYPT_KEY="$(gen_b64 32)"
  APP_JWT_SECRET="$(gen_hex 32)"
  APP_API_KEY="$(gen_hex 32)"
  BOT_SECRET_TOKEN="$(gen_hex 64)"
  REMNAWAVE_WEBHOOK_SECRET="$(gen_hex 64)"
  DATABASE_PASSWORD="$(gen_hex 24)"
  ok "Секреты созданы"

  # ── пишем .env ──────────────────────────────────────────────────────────────
  umask 077
  cat > .env <<EOF
# Сгенерировано install.sh — НЕ коммитьте этот файл (он в .gitignore).

# ── Telegram-бот ─────────────────────────────────────────────────────────
BOT_TOKEN=${ENVVAL[BOT_TOKEN]}
BOT_SECRET_TOKEN=$BOT_SECRET_TOKEN
BOT_OWNER_ID=${ENVVAL[BOT_OWNER_ID]}
BOT_SUPPORT_USERNAME=${ENVVAL[BOT_SUPPORT_USERNAME]}
TELEGRAM_BOT_USERNAME=${ENVVAL[TELEGRAM_BOT_USERNAME]}
BOT_MINI_APP=true
BOT_MINI_APP_RESERVE=true

# ── Приложение / веб ─────────────────────────────────────────────────────
APP_DOMAIN=${ENVVAL[APP_DOMAIN]}
APP_CRYPT_KEY=$APP_CRYPT_KEY
APP_JWT_SECRET=$APP_JWT_SECRET
APP_API_KEY=$APP_API_KEY
APP_ORIGINS=${ENVVAL[WEB_CABINET_URL]}
WEB_ENABLED=true
WEB_CABINET_URL=${ENVVAL[WEB_CABINET_URL]}

# ── Remnawave ────────────────────────────────────────────────────────────
REMNAWAVE_HOST=${ENVVAL[REMNAWAVE_HOST]}
REMNAWAVE_TOKEN=${ENVVAL[REMNAWAVE_TOKEN]}
REMNAWAVE_WEBHOOK_SECRET=$REMNAWAVE_WEBHOOK_SECRET

# ── База данных ──────────────────────────────────────────────────────────
DATABASE_PASSWORD=$DATABASE_PASSWORD

# ── Email / SMTP ─────────────────────────────────────────────────────────
EMAIL_ENABLED=$EMAIL_ENABLED
EMAIL_HOST=${ENVVAL[EMAIL_HOST]:-smtp.gmail.com}
EMAIL_PORT=${ENVVAL[EMAIL_PORT]:-587}
EMAIL_USE_TLS=true
EMAIL_USE_SSL=false
EMAIL_USERNAME=${ENVVAL[EMAIL_USERNAME]:-}
EMAIL_PASSWORD=${ENVVAL[EMAIL_PASSWORD]:-}
EMAIL_FROM_EMAIL=${ENVVAL[EMAIL_FROM_EMAIL]:-}
EMAIL_FROM_NAME=${ENVVAL[EMAIL_FROM_NAME]:-RемнаShop}
EMAIL_BREVO_API_KEY=${ENVVAL[EMAIL_BREVO_API_KEY]:-}
EOF
  ok ".env создан"
fi

# ── docker-сеть ──────────────────────────────────────────────────────────────
if ! docker network inspect remnawave-network >/dev/null 2>&1; then
  info "Создаю docker-сеть remnawave-network…"
  docker network create remnawave-network >/dev/null
  ok "Сеть создана"
else
  ok "Сеть remnawave-network уже существует"
fi

# ── сборка и запуск ──────────────────────────────────────────────────────────
say ""
info "Собираю и запускаю контейнеры (первый раз — несколько минут)…"
$DC -f docker-compose.yml -f cabinet/docker-compose.cabinet.yml up -d --build

say ""
ok "${BOLD}Готово!${RST}"
say ""
say "  Бот и API:   ${DIM}127.0.0.1:5000${RST}"
say "  Кабинет:     ${DIM}127.0.0.1:5002${RST}  → проксируйте на ${BOLD}${ENVVAL[WEB_CABINET_URL]:-ваш домен кабинета}${RST}"
say ""
say "  Логи:        ${DIM}$DC logs -f remnashop${RST}"
say "  Статус:      ${DIM}docker ps${RST}"
say ""
say "  ${YLW}Дальше:${RST} направьте домены на сервер и настройте reverse-proxy (Caddy/nginx) с TLS"
say "  на порты 5000 (API/вебхуки) и 5002 (кабинет)."
