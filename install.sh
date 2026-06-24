#!/usr/bin/env bash
#
# RemnaShop (кабинет + админка) — установка-дополнение поверх готового бота.
#
# Два режима:
#
#   ./install.sh           — сервер С БОТОМ (co-located). Ставит overlay-бота,
#                            воркеры и кабинет рядом с уже настроенным ботом.
#                            Допишет только недостающие переменные дополнения
#                            (web/email), секреты web-части сгенерирует сам.
#                            Требует уже заполненный .env бота.
#
#   ./install.sh site      — ОТДЕЛЬНЫЙ сервер сайта. Ставит ТОЛЬКО кабинет
#                            (nginx + React), который проксирует /api/ на бота
#                            по приватному каналу (API_UPSTREAM). Секреты здесь
#                            не нужны — их держит бот. Спросит только username
#                            бота, адрес API бота и URL кабинета. Предпосылка —
#                            поднятый WG/VPN-туннель и API_BIND_HOST на боте.
#
# Уже заданные (непустые) переменные остаются как есть. Повторный запуск безопасен.

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

MODE="${1:-bot}"
case "$MODE" in
  bot|site) ;;
  *) printf 'Неизвестный режим: %s. Используйте: ./install.sh [site]\n' "$MODE" >&2; exit 2 ;;
esac

# ── оформление ─────────────────────────────────────────────────────────────
if [ -t 1 ]; then
  BOLD=$'\e[1m'; DIM=$'\e[2m'; RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; CYN=$'\e[36m'; RST=$'\e[0m'
else BOLD=""; DIM=""; RED=""; GRN=""; YLW=""; CYN=""; RST=""; fi
say()  { printf '%s\n' "$*"; }
info() { printf '%s➜%s %s\n' "$CYN" "$RST" "$*"; }
ok()   { printf '%s✓%s %s\n' "$GRN" "$RST" "$*"; }
warn() { printf '%s!%s %s\n' "$YLW" "$RST" "$*"; }
die()  { printf '%s✗ %s%s\n' "$RED" "$*" "$RST" >&2; exit 1; }

# ── зависимости ──────────────────────────────────────────────────────────────
command -v docker  >/dev/null 2>&1 || die "Не найден docker: https://docs.docker.com/engine/install/"
command -v openssl >/dev/null 2>&1 || die "Не найден openssl."
if docker compose version >/dev/null 2>&1; then DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then DC="docker-compose"
else die "Не найден 'docker compose'."; fi

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
# flush_env "заголовок блока" — дописать накопленные ADD_LINES одним блоком
flush_env() {
  if [ "${#ADD_LINES[@]}" -gt 0 ]; then
    {
      printf '\n# ── %s — %s ──\n' "$1" "$(date +%F)"
      printf '%s\n' "${ADD_LINES[@]}"
    } >> .env
    ok "Добавлено новых строк в .env: ${#ADD_LINES[@]}"
    ADD_LINES=()
  else
    ok ".env уже содержит все нужные ключи — добавлять нечего"
  fi
}

# ── ввод (только если значение ещё не задано) ─────────────────────────────────
ASKED=""
ask() { # ask VAR "Подсказка" ["default"]
  local var="$1" prompt="$2" def="${3:-}" input
  need_value "$var" || { ok "  $var уже задан — пропускаю"; ASKED=""; return; }
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

# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  Режим SITE — только кабинет на отдельном сервере                         ║
# ╚══════════════════════════════════════════════════════════════════════════╝
if [ "$MODE" = "site" ]; then
  say "${BOLD}RemnaShop — установка кабинета на ОТДЕЛЬНОМ сервере${RST}"
  ok "Зависимости на месте"
  [ -f .env ] || { : > .env; ok "Создан пустой .env для кабинета"; }

  say ""
  say "${BOLD}Недостающие данные${RST} ${DIM}(секреты на сайт-сервере не нужны — их держит бот)${RST}"

  # username бота — вшивается в бандл кабинета на сборке (Telegram-вход)
  ask TELEGRAM_BOT_USERNAME "  Username бота без @ (для входа в кабинет)"
  [ -n "${ASKED:-}" ] && ensure TELEGRAM_BOT_USERNAME "$ASKED"; ASKED=""

  # Домен API бота — кабинет проксирует /api/ на ПУБЛИЧНЫЙ API бота по https
  # (его уже отдаёт reverse-proxy бота; WG/приватный канал не нужен).
  # Из домена выводим API_UPSTREAM (домен:443), API_SCHEME=https, API_HOST_HEADER=домен.
  if need_value API_UPSTREAM; then
    while :; do
      ask API_BOT_DOMAIN "  Домен API бота, где отвечает /api/v1/* (напр. bot.example.com)"
      D="${ASKED:-}"; ASKED=""
      D="${D#http://}"; D="${D#https://}"; D="${D%%/*}"   # убираем схему и путь
      if [[ "$D" =~ ^[A-Za-z0-9.-]+$ ]] && [[ "$D" == *.* ]]; then
        ensure API_UPSTREAM "$D:443"
        ensure API_SCHEME https
        ensure API_HOST_HEADER "$D"
        break
      fi
      warn "Нужен домен, напр. bot.example.com"
    done
  else
    ok "  API_UPSTREAM уже задан — пропускаю"
  fi

  # публичный URL кабинета — для подсказки про reverse-proxy
  ask WEB_CABINET_URL "  Публичный URL кабинета (с https://)"
  [ -n "${ASKED:-}" ] && ensure WEB_CABINET_URL "$ASKED"; ASKED=""

  flush_env "Добавлено RemnaShop (кабинет, отдельный сервер)"

  CAB_URL="$(getval WEB_CABINET_URL)"
  UP="$(getval API_UPSTREAM)"
  API_DOM="$(getval API_HOST_HEADER)"

  # ВАЖНО: при одном `-f cabinet/...` compose берёт project-directory по папке
  # этого файла (cabinet/) и ищет .env ТАМ, а не в корне репозитория, где мы его
  # пишем. Поэтому передаём .env явно и экспортируем переменные в окружение
  # (process-env у compose имеет высший приоритет — годится и для build-args).
  export TELEGRAM_BOT_USERNAME="$(getval TELEGRAM_BOT_USERNAME)"
  export API_UPSTREAM="$UP"
  export API_SCHEME="$(getval API_SCHEME)"
  export API_HOST_HEADER="$API_DOM"

  # Пред-проверка: отвечает ли публичный API бота (частая ошибка — неверный домен).
  PRE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "https://${API_DOM}/api/v1/public/auth/me" 2>/dev/null || echo 000)"
  if [ "$PRE" = "401" ] || [ "$PRE" = "200" ]; then
    ok "  API бота https://${API_DOM} отвечает (${PRE})"
  else
    warn "  https://${API_DOM}/api/v1/public/auth/me вернул '${PRE}' (ожидался 401)."
    warn "  Проверьте, что это верный домен API бота и он доступен по https."
    warn "  Кабинет соберу, но /api/ может не работать, пока домен не отвечает."
  fi

  say ""
  info "Собираю и поднимаю кабинет (проксирует /api/ → https://${API_DOM})…"
  $DC --env-file .env -f cabinet/docker-compose.site.yml up -d --build

  say ""
  ok "${BOLD}Готово!${RST}"
  say "  Кабинет: ${DIM}127.0.0.1:5002${RST}  → проксируйте на ${BOLD}${CAB_URL:-ваш домен кабинета}${RST}"
  say ""
  say "  Логи:   ${DIM}$DC -f cabinet/docker-compose.site.yml logs -f${RST}"
  say "  ${YLW}Дальше:${RST} reverse-proxy с TLS (свободный 443) на домен кабинета → 127.0.0.1:5002."
  exit 0
fi

# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  Режим BOT (по умолчанию) — бот + кабинет на одном сервере                ║
# ╚══════════════════════════════════════════════════════════════════════════╝
say "${BOLD}RemnaShop — установка кабинета и админки${RST}"
ok "Зависимости на месте"

# ── проверяем существующий .env бота ──────────────────────────────────────────
if [ ! -f .env ]; then
  warn ".env не найден."
  warn "Это дополнение ставится поверх уже настроенного бота RemnaShop."
  warn "Сначала настройте бота (cp .env.example .env и заполните), затем запустите снова."
  die "Нет .env — нечего дополнять."
fi
ok "Найден существующий .env — дополняю недостающим"

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
# Чтобы обычный `docker compose ...` (без -f) охватывал и кабинет (logs/ps/restart,
# а также down/up) — задаём список compose-файлов через COMPOSE_FILE.
ensure COMPOSE_FILE "docker-compose.yml:cabinet/docker-compose.cabinet.yml"

# ── email (необязательно) ─────────────────────────────────────────────────────
if need_value EMAIL_ENABLED; then
  say ""
  if ask_yn "Настроить отправку email сейчас? (нужно для регистрации по почте)"; then
    ensure EMAIL_ENABLED true
    info "Email (Brevo рекомендуется — обходит блокировки SMTP)"
    ask EMAIL_BREVO_API_KEY "  Brevo API key (xkeysib-…), Enter — пропустить" "-"
    [ "${ASKED:-}" != "-" ] && ensure EMAIL_BREVO_API_KEY "${ASKED}"; ASKED=""
    ask EMAIL_FROM_EMAIL "  Адрес отправителя (From)"; [ -n "${ASKED:-}" ] && ensure EMAIL_FROM_EMAIL "$ASKED"; FROM="${ASKED:-}"; ASKED=""
    ask EMAIL_FROM_NAME  "  Имя отправителя" "RemnaShop"; ensure EMAIL_FROM_NAME "$ASKED"; ASKED=""
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
ensure EMAIL_FROM_NAME "RemnaShop"
ensure EMAIL_BREVO_API_KEY ""

# ── дописываем отсутствовавшие ключи одним блоком ─────────────────────────────
flush_env "Добавлено RemnaShop (кабинет/web/email)"

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
