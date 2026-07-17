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
#   ./install.sh api       — сервер С БОТОМ, но кабинет будет на ДРУГОЙ машине.
#                            Ставит ТОЛЬКО overlay-бота и воркеры (API на :5000),
#                            локальный кабинет НЕ поднимает. Спросит публичный URL
#                            удалённого кабинета — пропишет его в CORS (APP_ORIGINS).
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
  bot|site|api) ;;
  *) printf 'Неизвестный режим: %s. Используйте: ./install.sh [site|api]\n' "$MODE" >&2; exit 2 ;;
esac
# api = на сервере С БОТОМ ставим ТОЛЬКО overlay (API для удалённого кабинета),
#       сам кабинет тут не поднимаем (он будет на отдельной машине).
WITH_CABINET=yes
[ "$MODE" = "api" ] && WITH_CABINET=no

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

# ── выбор способа входа в кабинет ─────────────────────────────────────────────
# Сначала предлагаем современный Telegram OIDC. Если согласились — классический
# Login Widget НЕ спрашиваем (кабинет всё равно скрывает его при включённом OIDC,
# а сам виджет без /setdomain выдаёт «Bot domain invalid»). Если от OIDC отказались —
# предлагаем классический виджет. Если и от него отказ — остаётся вход ТОЛЬКО по
# email (он доступен всегда). Идемпотентно: если что-то уже задано в .env — молчим.
#   $1 — публичный URL кабинета (для подсказок Redirect URI / setdomain)
#   $2 — "site", если это сайт-сервер (там секреты OIDC не хранятся — их держит бот)
prompt_login_method() {
  local cab_url="${1:-}" site="${2:-}" bot_id
  if ! need_value TELEGRAM_OIDC_CLIENT_ID; then ok "  Telegram OIDC уже настроен — пропускаю выбор входа"; return; fi
  if ! need_value TELEGRAM_BOT_USERNAME;  then ok "  Классический Telegram-вход уже задан — пропускаю"; return; fi
  say ""
  say "${BOLD}Вход в кабинет${RST} ${DIM}(вход по email доступен всегда)${RST}"
  if [ "$site" = site ]; then
    # На сайт-сервере OIDC настраивается на стороне БОТА (там работает auth_oidc).
    # Здесь решаем лишь, вшивать ли классический Login Widget в бандл кабинета.
    if ask_yn "Вход будет через Telegram OIDC (он настраивается на сервере бота)?"; then
      ok "Классический Login Widget вшивать не буду — кабинет покажет кнопку OIDC."
      return
    fi
  else
    bot_id="$(getval BOT_TOKEN)"; bot_id="${bot_id%%:*}"
    if ask_yn "Настроить вход через Telegram OIDC (новый флоу, рекомендуется)?"; then
      info "Client ID и Secret берутся в @BotFather → Login Widget."
      [ -n "$cab_url" ] && info "Redirect URI там же: ${cab_url%/}/api/auth/telegram/oidc/callback"
      ask TELEGRAM_OIDC_CLIENT_ID "  Client ID (id бота)" "${bot_id:-}"
      [ -n "${ASKED:-}" ] && ensure TELEGRAM_OIDC_CLIENT_ID "$ASKED"; ASKED=""
      ask TELEGRAM_OIDC_CLIENT_SECRET "  Client Secret"
      [ -n "${ASKED:-}" ] && ensure TELEGRAM_OIDC_CLIENT_SECRET "$ASKED"; ASKED=""
      ok "Telegram OIDC настроен — классический Login Widget пропускаю."
      return
    fi
  fi
  if ask_yn "Настроить классический вход через Telegram (Login Widget)?"; then
    ask TELEGRAM_BOT_USERNAME "  Username бота без @"
    [ -n "${ASKED:-}" ] && ensure TELEGRAM_BOT_USERNAME "$ASKED"; ASKED=""
    [ -n "$cab_url" ] && warn "Не забудьте: @BotFather → /setdomain → ${cab_url#https://} (без https://)"
  else
    warn "Вход через Telegram пропущен — остаётся только вход по email."
  fi
}

# ── авто-публикация кабинета через Caddy панели Remnawave ──────────────────────
# Стандартная раскладка Remnawave: Caddy-контейнер с именем `caddy` на сети
# remnawave-network и конфигом /opt/remnawave/caddy/Caddyfile. Если он есть —
# дописываем туда vhost кабинета и перезагружаем Caddy. Это «всё автоматом»:
# второй Caddy не нужен, TLS на 443 уже работает у панели (нестандартный порт
# не подходит — на нём Caddy не выпустит сертификат и сломается вход Telegram).
# Возвращает 0, если кабинет опубликован через Caddy панели; иначе 1.
PANEL_CADDYFILE="/opt/remnawave/caddy/Caddyfile"
CABINET_CONTAINER="remnashop-cabinet"
wire_cabinet_into_panel_caddy() {
  local dom="$1" esc
  [ -n "$dom" ] || return 1
  [ -f "$PANEL_CADDYFILE" ] || return 1
  docker ps --format '{{.Names}}' | grep -qx caddy || return 1

  # Caddy должен видеть кабинет по имени контейнера — подключаем к сети при нужде
  docker network inspect remnawave-network --format '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null \
    | grep -qw caddy || docker network connect remnawave-network caddy 2>/dev/null || true

  esc="${dom//./\\.}"
  if grep -qE "[/[:space:]]${esc}[[:space:]]*\{" "$PANEL_CADDYFILE"; then
    ok "  Домен ${dom} уже есть в Caddyfile панели — оставляю как есть"
  else
    cp "$PANEL_CADDYFILE" "$PANEL_CADDYFILE.bak.$(date +%Y%m%d-%H%M%S)"
    cat >> "$PANEL_CADDYFILE" <<EOF

# RemnaShop cabinet — добавлено install.sh
https://${dom} {
	reverse_proxy * http://${CABINET_CONTAINER}:80
}
EOF
    docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 \
      || docker restart caddy >/dev/null 2>&1 || true
    ok "  Кабинет ${dom} вписан в Caddyfile панели (рядом бэкап) и Caddy перезагружен"
  fi

  # Системный Caddy (если кто-то ставил site-install) гасим — он дерётся за 443
  if systemctl list-unit-files 2>/dev/null | grep -q '^caddy\.service'; then
    if systemctl is-active caddy >/dev/null 2>&1 || systemctl is-enabled caddy >/dev/null 2>&1; then
      systemctl disable --now caddy >/dev/null 2>&1 || true
      warn "  Системный Caddy отключён, чтобы не конфликтовал с Caddy панели на 443"
    fi
  fi
  return 0
}

# ── публикация кабинета своим reverse-proxy (Caddy/nginx) ──────────────────────
# Принцип: если Caddy или nginx УЖЕ установлен — только ДОПИСЫВАЕМ в него vhost
# кабинета (не ставим второй прокси, не перезаписываем чужой конфиг). Если ни
# одного нет и 443 свободен — ставим выбранный и выпускаем сертификат сами.
# Цель — контейнер кабинета на 127.0.0.1:5002.
_install_caddy_pkg() {
  command -v caddy >/dev/null 2>&1 && return 0
  info "  Устанавливаю Caddy…"
  export DEBIAN_FRONTEND=noninteractive
  apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https gnupg >/dev/null 2>&1 || true
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg 2>/dev/null
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list 2>/dev/null
  apt-get update -qq && apt-get install -y -qq caddy >/dev/null
}

# Дописать vhost кабинета в СУЩЕСТВУЮЩИЙ Caddyfile (не перезаписывая его). Идемпотентно.
_caddy_add_vhost() {
  local dom="$1" cfg="/etc/caddy/Caddyfile" esc
  [ -f "$cfg" ] || { mkdir -p /etc/caddy; : > "$cfg"; }
  esc="${dom//./\\.}"
  if grep -qE "(^|[[:space:]/])${esc}[[:space:]]*\{" "$cfg"; then
    ok "  Домен ${dom} уже есть в Caddyfile — оставляю как есть"
  else
    cp "$cfg" "$cfg.bak.$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
    cat >> "$cfg" <<EOF

# RemnaShop cabinet — добавлено install.sh
${dom} {
    reverse_proxy 127.0.0.1:5002
}
EOF
  fi
  systemctl reload caddy 2>/dev/null || systemctl restart caddy 2>/dev/null || true
  ok "  Кабинет вписан в Caddy: ${dom} → 127.0.0.1:5002 (TLS авто)"
}

_install_nginx_pkg() {
  command -v nginx >/dev/null 2>&1 && command -v certbot >/dev/null 2>&1 && return 0
  info "  Устанавливаю nginx и certbot…"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq nginx certbot python3-certbot-nginx >/dev/null
}

# Дописать vhost кабинета в nginx ОТДЕЛЬНЫМ файлом (чужие конфиги не трогаем).
_nginx_add_vhost() {
  local dom="$1"
  cat > /etc/nginx/sites-available/remnashop-cabinet.conf <<EOF
server {
    listen 80;
    server_name ${dom};
    location / {
        proxy_pass http://127.0.0.1:5002;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
  ln -sf /etc/nginx/sites-available/remnashop-cabinet.conf /etc/nginx/sites-enabled/remnashop-cabinet.conf
  nginx -t >/dev/null 2>&1 && systemctl reload nginx
  if certbot --nginx -d "${dom}" --non-interactive --agree-tos \
       --register-unsafely-without-email --redirect >/dev/null 2>&1; then
    ok "  nginx + TLS: ${dom} → 127.0.0.1:5002"
  else
    warn "  nginx поднят на http, сертификат пока не выпустился (проверьте A-запись)."
    say  "    После настройки DNS: ${DIM}certbot --nginx -d ${dom} --redirect${RST}"
  fi
}

# HTTPS=caddy|nginx|none переопределяет вопрос (для неинтерактива).
publish_cabinet_auto() {
  local dom="$1" choice="${HTTPS:-}"

  # 1) Caddy УЖЕ установлен — только дописываем в него (второй прокси не ставим).
  if command -v caddy >/dev/null 2>&1; then
    ok "  Обнаружен установленный Caddy — вписываю кабинет в него."
    _caddy_add_vhost "$dom"
    return 0
  fi
  # 2) nginx УЖЕ установлен — добавляем vhost кабинета + сертификат.
  if command -v nginx >/dev/null 2>&1; then
    ok "  Обнаружен установленный nginx — добавляю vhost кабинета."
    _install_nginx_pkg
    _nginx_add_vhost "$dom"
    return 0
  fi
  # 3) Прокси нет, но 443 занят чем-то сторонним — не трогаем.
  if ss -ltn 2>/dev/null | grep -q ':443 '; then
    warn "  Порт 443 занят сторонним reverse-proxy — не трогаю его."
    say  "  Направьте его на ${BOLD}127.0.0.1:5002${RST} (домен ${dom})."
    return 0
  fi
  # 4) Чистый сервер — ставим выбранный прокси и выпускаем сертификат сами.
  if [ -z "$choice" ] && [ -e /dev/tty ]; then
    say "  ${BOLD}Чем поднять HTTPS на 443?${RST} (поставится и выпустит сертификат само)"
    printf '    %s[1]%s Caddy (по умолчанию)   %s[2]%s nginx   %s[3]%s ничего (свой прокси): ' \
      "$BOLD" "$RST" "$BOLD" "$RST" "$BOLD" "$RST"
    read -r _a </dev/tty || _a=""
    case "${_a}" in 2) choice=nginx ;; 3) choice=none ;; *) choice=caddy ;; esac
  fi
  case "${choice:-caddy}" in
    nginx) _install_nginx_pkg; _nginx_add_vhost "$dom" ;;
    none)  say "  ${DIM}Опубликуйте кабинет своим прокси: домен ${dom} → 127.0.0.1:5002${RST}" ;;
    *)     _install_caddy_pkg; _caddy_add_vhost "$dom" ;;
  esac
}

# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  Режим SITE — только кабинет на отдельном сервере                         ║
# ╚══════════════════════════════════════════════════════════════════════════╝
if [ "$MODE" = "site" ]; then
  say "${BOLD}RemnaShop — установка кабинета на ОТДЕЛЬНОМ сервере${RST}"
  ok "Зависимости на месте"
  [ -f .env ] || { : > .env; ok "Создан пустой .env для кабинета"; }

  say ""
  say "${BOLD}Недостающие данные${RST} ${DIM}(секреты на сайт-сервере не нужны — их держит бот)${RST}"

  # Способ входа спросим ниже, когда узнаем URL кабинета (для подсказок).

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

  # способ входа: OIDC (на боте) → классический виджет → только email
  prompt_login_method "$(getval WEB_CABINET_URL)" site

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
  # Идемпотентность повторного запуска: убираем прежний контейнер/сеть кабинета,
  # чтобы застрявшее с прошлой (возможно неудачной) установки состояние не мешало —
  # частая причина «встаёт только после переустановки ОС» (занятый порт 5002 /
  # старый контейнер с тем же именем / протухший build-кэш). down чистит их, а
  # --force-recreate гарантирует пересоздание с новой конфигурацией/образом.
  if ss -ltn 2>/dev/null | grep -q '127.0.0.1:5002 '; then
    warn "  Порт 127.0.0.1:5002 занят — освобождаю прежний контейнер кабинета."
  fi
  $DC --env-file .env -f cabinet/docker-compose.site.yml down --remove-orphans 2>/dev/null || true
  info "Собираю и поднимаю кабинет (проксирует /api/ → https://${API_DOM})…"
  $DC --env-file .env -f cabinet/docker-compose.site.yml up -d --build --force-recreate

  say ""
  ok "${BOLD}Готово!${RST}"
  say "  Кабинет: ${DIM}127.0.0.1:5002${RST}  → проксируйте на ${BOLD}${CAB_URL:-ваш домен кабинета}${RST}"
  say ""
  say "  Логи:   ${DIM}$DC -f cabinet/docker-compose.site.yml logs -f${RST}"
  say "  ${YLW}Дальше:${RST} reverse-proxy с TLS (свободный 443) на домен кабинета → 127.0.0.1:5002."
  exit 0
fi

# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  Режим BOT (overlay + кабинет) и API (overlay без кабинета)               ║
# ╚══════════════════════════════════════════════════════════════════════════╝
if [ "$WITH_CABINET" = yes ]; then
  say "${BOLD}RemnaShop — установка кабинета и админки${RST}"
else
  say "${BOLD}RemnaShop — установка ТОЛЬКО API (кабинет на отдельном сервере)${RST}"
fi
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

# Способ входа в кабинет (OIDC / классический виджет / только email) спросим
# ниже — после того, как узнаем URL кабинета (нужен для подсказки Redirect URI).

# публичный URL кабинета (дефолт — по APP_DOMAIN из существующего .env).
# В режиме api это URL УДАЛЁННОГО кабинета (на другой машине) — он пойдёт в CORS.
DOM="$(getval APP_DOMAIN)"
if [ "$WITH_CABINET" = yes ]; then
  ask WEB_CABINET_URL "  Публичный URL кабинета" "${DOM:+https://cabinet.$DOM}"
else
  ask WEB_CABINET_URL "  Публичный URL кабинета на ДРУГОМ сервере (с https://)"
fi
[ -n "${ASKED:-}" ] && ensure WEB_CABINET_URL "$ASKED"
CAB_URL="$(getval WEB_CABINET_URL)"; CAB_URL="${CAB_URL:-${ASKED:-}}"; ASKED=""

# способ входа в кабинет: OIDC → классический виджет → только email
prompt_login_method "$CAB_URL"
# Ключ должен присутствовать даже при выборе OIDC/email — иначе сборка кабинета
# ругается на незаданную переменную build-arg (VITE_TELEGRAM_BOT_USERNAME).
ensure TELEGRAM_BOT_USERNAME ""

# разрешённый origin = URL кабинета
ensure APP_ORIGINS "$CAB_URL"

# секреты web-части
ensure APP_API_KEY  "$(gen_hex 32)"
ensure APP_JWT_SECRET "$(gen_hex 32)"

# дефолты
ensure WEB_ENABLED true
ensure BOT_MINI_APP_RESERVE true
# Чтобы обычный `docker compose ...` (без -f) охватывал нужные сервисы
# (logs/ps/restart, down/up) — задаём список compose-файлов через COMPOSE_FILE.
# В режиме api кабинет не поднимаем — только бот/воркеры.
if [ "$WITH_CABINET" = yes ]; then
  ensure COMPOSE_FILE "docker-compose.yml:cabinet/docker-compose.cabinet.yml"
else
  ensure COMPOSE_FILE "docker-compose.yml"
fi

# ── email (необязательно) ─────────────────────────────────────────────────────
if need_value EMAIL_ENABLED; then
  say ""
  if ask_yn "Настроить отправку email сейчас? (нужно для регистрации/сброса пароля по почте)"; then
    ensure EMAIL_ENABLED true
    # Провайдер на выбор: пресеты host/port/TLS подставляются автоматически,
    # вводятся только логин/пароль/отправитель. Можно поменять позже в админке.
    say "  ${BOLD}Провайдер почты:${RST}"
    printf '    %s[1]%s Gmail   %s[2]%s Yandex   %s[3]%s Mail.ru   %s[4]%s Brevo (API)   %s[5]%s свой SMTP: ' \
      "$BOLD" "$RST" "$BOLD" "$RST" "$BOLD" "$RST" "$BOLD" "$RST" "$BOLD" "$RST"
    read -r _p </dev/tty || _p=""
    case "${_p}" in
      2) ensure EMAIL_HOST smtp.yandex.ru; ensure EMAIL_PORT 465; ensure EMAIL_USE_TLS false; ensure EMAIL_USE_SSL true ;;
      3) ensure EMAIL_HOST smtp.mail.ru;   ensure EMAIL_PORT 465; ensure EMAIL_USE_TLS false; ensure EMAIL_USE_SSL true ;;
      4) EMAIL_PROVIDER_BREVO=1 ;;
      5) ask EMAIL_HOST "  SMTP host" "smtp.example.com"; ensure EMAIL_HOST "$ASKED"; ASKED=""
         ask EMAIL_PORT "  SMTP port (587 STARTTLS / 465 SSL)" "587"; ensure EMAIL_PORT "$ASKED"
         if [ "${ASKED}" = "465" ]; then ensure EMAIL_USE_TLS false; ensure EMAIL_USE_SSL true; else ensure EMAIL_USE_TLS true; ensure EMAIL_USE_SSL false; fi; ASKED="" ;;
      *) ensure EMAIL_HOST smtp.gmail.com; ensure EMAIL_PORT 587; ensure EMAIL_USE_TLS true; ensure EMAIL_USE_SSL false ;;
    esac

    ask EMAIL_FROM_EMAIL "  Адрес отправителя (From)"; [ -n "${ASKED:-}" ] && ensure EMAIL_FROM_EMAIL "$ASKED"; FROM="${ASKED:-}"; ASKED=""
    ask EMAIL_FROM_NAME  "  Имя отправителя" "RemnaShop"; ensure EMAIL_FROM_NAME "$ASKED"; ASKED=""

    if [ "${EMAIL_PROVIDER_BREVO:-}" = 1 ]; then
      # Brevo: нужен только API-ключ (письма уходят через HTTP API, порт 443).
      ask EMAIL_BREVO_API_KEY "  Brevo API key (xkeysib-…)"; [ -n "${ASKED:-}" ] && ensure EMAIL_BREVO_API_KEY "$ASKED"; ASKED=""
    else
      # SMTP-провайдер: логин + пароль приложения (app password!).
      ask EMAIL_USERNAME "  SMTP логин (email)" "${FROM}"; ensure EMAIL_USERNAME "$ASKED"; ASKED=""
      ask EMAIL_PASSWORD "  Пароль приложения (app password)"; [ -n "${ASKED:-}" ] && ensure EMAIL_PASSWORD "$ASKED"; ASKED=""
    fi
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
if [ "$WITH_CABINET" = yes ]; then
  info "Собираю и поднимаю бота (overlay), воркеры и кабинет…"
  $DC -f docker-compose.yml -f cabinet/docker-compose.cabinet.yml up -d --build
  say ""
  ok "${BOLD}Готово!${RST}"
  say "  Бот и API:  ${DIM}127.0.0.1:5000${RST}"
  say "  Кабинет:    ${DIM}127.0.0.1:5002${RST}"
  say ""

  # Пытаемся опубликовать кабинет автоматически через Caddy панели Remnawave.
  CAB_DOM="${CAB_URL#http://}"; CAB_DOM="${CAB_DOM#https://}"; CAB_DOM="${CAB_DOM%%/*}"
  if wire_cabinet_into_panel_caddy "$CAB_DOM"; then
    say "  ${GRN}Кабинет опубликован автоматически (Caddy панели):${RST} ${BOLD}https://${CAB_DOM}${RST}"
    say "  ${DIM}(проверьте A-запись ${CAB_DOM} → IP этого сервера)${RST}"
  else
    # Caddy панели нет — ставим свой reverse-proxy сами (выбор Caddy/nginx).
    publish_cabinet_auto "$CAB_DOM"
  fi
  say ""
  if [ -n "$(getval TELEGRAM_OIDC_CLIENT_ID)" ]; then
    say "  ${YLW}Не забудьте (вход через Telegram OIDC):${RST}"
    say "    @BotFather → Login Widget → Add a Redirect URI:"
    say "      ${BOLD}${CAB_URL%/}/api/auth/telegram/oidc/callback${RST}"
  elif [ -n "$(getval TELEGRAM_BOT_USERNAME)" ]; then
    say "  ${YLW}Не забудьте (классический вход через Telegram):${RST}"
    say "    привяжите домен кабинета к боту в @BotFather:"
    say "      /setdomain → ${BOLD}${CAB_DOM:-домен}${RST} (без https://)"
  else
    say "  ${DIM}Вход через Telegram не настроен — в кабинет входят по email.${RST}"
  fi
  say ""
  say "  Логи:   ${DIM}$DC -f docker-compose.yml -f cabinet/docker-compose.cabinet.yml logs -f${RST}"
else
  info "Собираю и поднимаю бота (overlay) и воркеры — БЕЗ локального кабинета…"
  $DC -f docker-compose.yml up -d --build
  say ""
  ok "${BOLD}Готово! API для удалённого кабинета поднят.${RST}"
  say "  Бот и API:  ${DIM}127.0.0.1:5000${RST}"
  say "  CORS открыт для: ${BOLD}${CAB_URL:-URL вашего кабинета}${RST}"
  say ""
  say "  Логи:   ${DIM}$DC logs -f${RST}"
  say "  ${YLW}Дальше:${RST} убедитесь, что API бота доступен по https снаружи (домен → :5000),"
  say "          затем на ОТДЕЛЬНОМ сервере запустите site-install.sh."
fi
