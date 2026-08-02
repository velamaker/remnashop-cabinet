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
# set_env VAR VALUE — записать ПРИНУДИТЕЛЬНО, сразу в файл.
# ensure тут не годится дважды: он не трогает непустое (сменить бэкенд кабинета
# повторным запуском было нельзя) и откладывает новые ключи до flush_env — из-за
# чего две ветки, писавшие один ключ, давали в .env ДВЕ строки.
set_env() {
  local var="$1" val="$2" line keep=()
  if grep -qE "^$var=" .env; then
    awk -v k="$var" -v v="$val" 'BEGIN{FS="="} $1==k && !d {print k"="v; d=1; next} {print}' .env > .env.__tmp__ \
      && mv .env.__tmp__ .env
  else
    printf '%s=%s\n' "$var" "$val" >> .env
  fi
  # Тот же ключ мог ждать очереди в отложенном блоке — снимаем, иначе продублируется.
  if [ "${#ADD_LINES[@]}" -gt 0 ]; then
    for line in "${ADD_LINES[@]}"; do
      [[ "$line" == "$var="* ]] || keep+=("$line")
    done
    ADD_LINES=("${keep[@]+"${keep[@]}"}")
  fi
}
# compose_list_drop "a.yml:b.yml" "b.yml" → "a.yml": убрать один файл из списка
# COMPOSE_FILE, сохранив порядок и все остальные (в т.ч. добавленные оператором).
compose_list_drop() {
  local out="" f
  local IFS=:
  for f in $1; do
    [ -n "$f" ] || continue
    [ "$f" = "$2" ] && continue
    out="${out:+$out:}$f"
  done
  printf '%s' "$out"
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
  # В .env лежат токены бота, секреты JWT и пароль почты — читать их всем в
  # системе незачем (по умолчанию файл создаётся с правами 644).
  chmod 600 .env 2>/dev/null || true
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

# Достаёт ли адаптер до бота. Спрашиваем ИЗНУТРИ контейнера: адрес часто задан
# именем docker-сервиса, которое с хоста не резолвится в принципе, — проверка с
# хоста там ничего не доказывает.
#   $1 — имя контейнера адаптера; печатает «OK <код>» либо «FAIL <ошибка>»
adapter_probe_upstream() {
  docker exec "$1" python -c \
    'import os,urllib.request
u=os.environ.get("BEDOLAGA_UPSTREAM","").rstrip("/")
try:
    r=urllib.request.urlopen(u+"/cabinet/branding",timeout=6)
    print("OK",r.status)
except Exception as e:
    print("FAIL",type(e).__name__,e)' 2>/dev/null || true
}

# Адрес бота — имя контейнера на этой же машине? Тогда адаптеру не хватает лишь
# общей с ним docker-сети: в site-режиме у кабинета с адаптером она своя, и имя
# бота внутри неё не резолвится. Подключаем адаптер второй сетью — руками это
# делать оператор не догадается, а симптом будет «весь кабинет отдаёт 502».
#   $1 — контейнер адаптера, $2 — BEDOLAGA_UPSTREAM
adapter_join_bot_network() {
  local name="$1" host="$2" net
  host="${host#*://}"; host="${host%%[:/]*}"
  # Домен или IP сетью не лечится — там дело в маршруте/файрволе, не в резолве.
  case "$host" in *.*|"") return 1 ;; esac
  docker ps --format '{{.Names}}' | grep -qx "$host" || return 1
  net="$(docker inspect "$host" \
    --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' 2>/dev/null | awk '{print $1}')"
  [ -n "$net" ] || return 1
  docker network connect "$net" "$name" >/dev/null 2>&1 || return 1
  docker restart "$name" >/dev/null 2>&1 || true
  ok "  Адаптер подключён к сети бота (${net}) — без неё имя ${host} ему не резолвится"
  return 0
}

# Дождаться адаптера и показать его самопроверку. Без этого ошибка в адресе бота
# всплывала у пользователя 502-ми: контейнер поднят, а достучаться ему некуда.
#   $1 — имя контейнера адаптера
adapter_healthcheck() {
  local name="$1" i out
  for i in 1 2 3 4 5 6 7 8 9 10; do
    out="$(docker exec "$name" python -c \
      'import urllib.request;print(urllib.request.urlopen("http://127.0.0.1:8090/adapter/health",timeout=3).read().decode())' \
      2>/dev/null || true)"
    [ -n "$out" ] && break
    sleep 2
  done
  if [ -z "$out" ]; then
    warn "  Адаптер не ответил на самопроверку — смотрите логи: docker logs $name"
    return 1
  fi
  ok "  Адаптер отвечает: ${DIM}${out}${RST}"
  case "$out" in
    *'"panel": true'*|*'"panel":true'*) : ;;
    *) warn "  Панель Remnawave адаптеру не задана — серверы и устройства будут пустыми." ;;
  esac
  case "$out" in
    *'"admin_token": true'*|*'"admin_token":true'*) : ;;
    *) warn "  Админский токен Bedolaga не задан — заморозка подписки выключена." ;;
  esac
  # Главное — достаёт ли адаптер до бота. Иначе первым, кто узнает об ошибке в
  # адресе, будет пользователь — по 502 на весь кабинет.
  local up
  up="$(adapter_probe_upstream "$name")"
  # Не резолвится имя — почти всегда просто нет общей сети с ботом. Чиним и
  # проверяем ещё раз, чтобы не оставлять оператора с рабочей, но мёртвой связкой.
  case "$up" in
    FAIL*name*resolution*|FAIL*Name*not*known*|FAIL*getaddrinfo*)
      if adapter_join_bot_network "$name" "$(getval BEDOLAGA_UPSTREAM)"; then
        for i in 1 2 3 4 5; do
          sleep 2
          up="$(adapter_probe_upstream "$name")"
          [ -n "$up" ] && [ "${up#FAIL}" = "$up" ] && break
        done
      fi ;;
  esac
  case "$up" in
    "OK 200"*) ok "  Адаптер достучался до бота Bedolaga (branding: 200)" ;;
    OK*)       warn "  Бот Bedolaga ответил не 200: ${up#OK }. Проверьте, что это адрес их web-API." ;;
    FAIL*)     warn "  Адаптер НЕ достучался до бота Bedolaga: ${DIM}${up#FAIL }${RST}"
               warn "  Кабинет будет отдавать 502, пока адрес неверен: BEDOLAGA_UPSTREAM в .env."
               warn "  Их бот должен быть в одной docker-сети с адаптером ЛИБО публиковать порт наружу." ;;
    *)         : ;;  # старый образ адаптера без python в PATH — молчим, не пугаем
  esac
}

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
wire_cabinet_into_panel_caddy() {
  local dom="$1" esc CABINET_CONTAINER
  # Имя контейнера берём из .env: у второй установки на том же хосте оно своё
  # (CABINET_CONTAINER), и прошитое `remnashop-cabinet` увело бы Caddy панели
  # на чужой кабинет.
  CABINET_CONTAINER="$(getval CABINET_CONTAINER)"; CABINET_CONTAINER="${CABINET_CONTAINER:-remnashop-cabinet}"
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
# Цель — контейнер кабинета на 127.0.0.1:$CAB_PORT (порт задаётся CABINET_PORT:
# прошитый 5002 отправлял прокси мимо кабинета на нестандартном порту).
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
  local dom="$1" port="${2:-5002}" cfg="/etc/caddy/Caddyfile" esc
  [ -f "$cfg" ] || { mkdir -p /etc/caddy; : > "$cfg"; }
  esc="${dom//./\\.}"
  if grep -qE "(^|[[:space:]/])${esc}[[:space:]]*\{" "$cfg"; then
    ok "  Домен ${dom} уже есть в Caddyfile — оставляю как есть"
  else
    cp "$cfg" "$cfg.bak.$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
    cat >> "$cfg" <<EOF

# RemnaShop cabinet — добавлено install.sh
${dom} {
    reverse_proxy 127.0.0.1:${port}
}
EOF
  fi
  systemctl reload caddy 2>/dev/null || systemctl restart caddy 2>/dev/null || true
  ok "  Кабинет вписан в Caddy: ${dom} → 127.0.0.1:${port} (TLS авто)"
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
  local dom="$1" port="${2:-5002}"
  cat > /etc/nginx/sites-available/remnashop-cabinet.conf <<EOF
server {
    listen 80;
    server_name ${dom};
    location / {
        proxy_pass http://127.0.0.1:${port};
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
    ok "  nginx + TLS: ${dom} → 127.0.0.1:${port}"
  else
    warn "  nginx поднят на http, сертификат пока не выпустился (проверьте A-запись)."
    say  "    После настройки DNS: ${DIM}certbot --nginx -d ${dom} --redirect${RST}"
  fi
}

# HTTPS=caddy|nginx|none переопределяет вопрос (для неинтерактива).

# ── Какой бот обслуживает кабинет ────────────────────────────────────────────
# Кабинет всегда ходит в /api/*, но бэкенды называют свои пути по-разному:
#   remnashop → /api/v1/public/   bedolaga → /cabinet/
# Токен админского API «Бедолаги» — им адаптер делает то, чего их бот не умеет
# (сейчас: заморозка подписки). Кода их бота это не касается: токены выпускаются
# их же ручкой /tokens. Без токена кабинет работает, просто без таких разделов.
# Достроить адрес бота до URL. Схему угадываем осознанно: голый домен на сайт-
# сервере — это публичный API за TLS, и прошитый http гнал бы админский токен и
# данные пользователей открытым текстом через интернет. А имя контейнера,
# localhost, IP или явный не-443 порт — это «внутри машины», там TLS нет.
#   $1 — то, что ввёл оператор (host[:port] или URL); печатает URL без хвостового /
bedolaga_url() {
  local raw="$1" host port url
  case "$raw" in
    http://*|https://*) printf '%s' "${raw%/}"; return ;;
  esac
  host="${raw%%[:/]*}"
  port=""; case "${raw%%/*}" in *:*) port="${raw%%/*}"; port="${port#*:}" ;; esac
  url="http://$raw"
  # Домен (есть точка и хоть один не-цифровой символ, т.е. не IPv4) без порта
  # либо с 443 — единственный случай, где по умолчанию https.
  case "$host" in
    *[!0-9.]*)
      case "$host" in
        *.*) [ -z "$port" ] || [ "$port" = 443 ] && url="https://$raw" ;;
      esac ;;
  esac
  printf '%s' "${url%/}"
}

# Отвечает ли API «Бедолаги» по указанному адресу. Ставит BED_CHECKED=1, чтобы
# ниже не дёргать ту же ручку второй раз за запуск.
#   $1 — базовый URL их API (со схемой)
BED_CHECKED=0
check_bedolaga_api() {
  local url="$1" code
  BED_CHECKED=1
  # curl при обрыве связи сам печатает 000, поэтому запасной echo давал бы «000000».
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "${url}/cabinet/branding" 2>/dev/null || true)"
  code="${code:-000}"
  if [ "$code" = "200" ]; then
    ok "  API бота Bedolaga ${url} отвечает"
    return 0
  fi
  # Адрес вида `remnawave_bot:8080` — имя docker-сервиса: с хоста оно не
  # резолвится в принципе, поэтому ругаться тут не на что. Настоящую проверку
  # сделает adapter_healthcheck изнутри контейнера, после запуска.
  local hostpart="${url#*://}"; hostpart="${hostpart%%[:/]*}"
  case "$hostpart" in
    *.*) : ;;
    *) say "  ${DIM}Адрес похож на имя docker-сервиса — проверю его изнутри адаптера после запуска.${RST}"
       return 1 ;;
  esac
  warn "  ${url}/cabinet/branding вернул '${code}' (ожидался 200)."
  warn "  Проверьте адрес бота Bedolaga и что его web-API включён (WEB_API_ENABLED)."
  # 000 в одно-серверной установке почти всегда значит одно: их бот слушает
  # только внутри своей сети/на localhost, а адаптеру нужен доступный адрес.
  case "$url" in
    *172.17.0.1*|*localhost*|*127.0.0.1*)
      warn "  Их compose должен публиковать порт наружу ('8080:8080'), иначе адаптер не достучится." ;;
  esac
  return 1
}

prompt_bedolaga_token() {
  local api="$1" boot named
  need_value BEDOLAGA_ADMIN_TOKEN || { ok "  Токен Bedolaga уже задан — пропускаю"; return; }
  say ""
  say "${BOLD}Доступ к админскому API Bedolaga${RST} ${DIM}(для заморозки подписки)${RST}"
  say "  Нужен любой их токен: строка ${DIM}WEB_API_DEFAULT_TOKEN${RST} из .env бота Bedolaga."
  say "  ${DIM}Пусто — пропустить: кабинет будет работать без заморозки.${RST}"
  # -s: токен не печатается в терминал и не остаётся в истории прокрутки.
  read -r -s -p "$(printf '%sТокен Bedolaga: %s' "$BOLD" "$RST")" boot </dev/tty || true
  say ""
  [ -z "${boot:-}" ] && { warn "Токен не задан — заморозка подписки будет выключена."; return; }

  # Просим у них ОТДЕЛЬНЫЙ именной токен, чтобы кабинет не жил на их стартовом:
  # стартовый оживает при каждом рестарте бота, пока он прописан в .env.
  # Заголовок с токеном отдаём файлом, а не аргументом: аргументы видны в списке
  # процессов любому пользователю сервера, пока команда выполняется.
  local hdr; hdr="$(mktemp)"; chmod 600 "$hdr"
  printf 'X-API-Key: %s\n' "$boot" > "$hdr"
  named="$(curl -fsS --max-time 10 -X POST "${api}/tokens" \
      -H @"$hdr" -H 'content-type: application/json' \
      -d '{"name":"cabinet-adapter","description":"Кабинет: заморозка подписки"}' 2>/dev/null \
    | grep -oE '"token"[[:space:]]*:[[:space:]]*"[^"]+"' | head -1 | cut -d'"' -f4)"
  rm -f "$hdr"

  if [ -n "$named" ]; then
    ensure BEDOLAGA_ADMIN_TOKEN "$named"
    ok "  Выпущен отдельный токен cabinet-adapter (стартовый можно отозвать)"
  else
    ensure BEDOLAGA_ADMIN_TOKEN "$boot"
    warn "Не удалось выпустить отдельный токен — использую введённый."
    warn "Проверьте, что API Bedolaga доступен по ${api} и токен верный."
  fi
}

# Префикс уезжает в nginx кабинета переменной API_PATH_PREFIX (см. nginx.conf).
#
# BACKEND_CHOICE — выбор ЭТОГО запуска. Читать его, а не .env: ensure кладёт новые
# ключи в отложенный блок, и до flush_env `getval CABINET_BACKEND` возвращает пусто
# — из-за этого адаптер не попадал в compose и кабинет вставал только со второго
# запуска установщика.
#   $1 — "site", если кабинет ставится отдельно от бота (адреса API_* там свои).
BACKEND_CHOICE=""
prompt_cabinet_backend() {
  local site="${1:-}" choice bed_host bed_url
  # Заданный в окружении бэкенд выигрывает у записанного: это единственный способ
  # передумать (`CABINET_BACKEND=remnashop ./install.sh`), не правя .env руками.
  if [ -n "${CABINET_BACKEND:-}" ]; then
    BACKEND_CHOICE="$CABINET_BACKEND"
    ok "  Бэкенд кабинета задан переменной окружения: $BACKEND_CHOICE"
  elif ! need_value CABINET_BACKEND; then
    BACKEND_CHOICE="$(getval CABINET_BACKEND)"
    ok "  Бэкенд кабинета уже выбран ($BACKEND_CHOICE) — пропускаю"
    say "  ${DIM}Сменить: CABINET_BACKEND=remnashop ./install.sh${RST}"
    return
  else
    say ""
    say "${BOLD}Какой бот обслуживает кабинет?${RST}"
    say "  1) RemnaShop ${DIM}(наш бот, по умолчанию)${RST}"
    say "  2) Bedolaga  ${DIM}(bedolaga-бот; экспериментально)${RST}"
    read -r -p "$(printf '%sВыбор [1]: %s' "$BOLD" "$RST")" choice </dev/tty || true
    if [[ "${choice:-1}" == "2" ]]; then
      # Выбор дорогой: он переписывает адреса API_* и поднимает лишний сервис.
      # Случайная «2» раньше ломала кабинет, и вернуться было нечем.
      if ask_yn "  Кабинет будет работать поверх бота Bedolaga. Подтвердить?"; then
        BACKEND_CHOICE=bedolaga
      else
        BACKEND_CHOICE=remnashop
      fi
    else
      BACKEND_CHOICE=remnashop
    fi
  fi

  set_env CABINET_BACKEND "$BACKEND_CHOICE"
  # Префикс наш в обоих случаях: кабинет ходит либо в наш бот, либо в адаптер,
  # а тот отдаёт наш же контракт (/api/v1/public/*) поверх чужого API.
  set_env API_PATH_PREFIX "/api/v1/public/"
  [ "$BACKEND_CHOICE" = bedolaga ] || return 0

  # Адрес их бота. Спрашиваем ВСЕГДА, дефолт ответа — уже сохранённый адрес:
  # Enter оставляет как есть, ввод нового — меняет. Через `ask` было нельзя:
  # он молча пропускает заполненную переменную, и сохранённый адрес затирался
  # дефолтом установщика (адрес бота нельзя было сменить повторным запуском).
  local def saved
  saved="$(getval BEDOLAGA_HOST)"
  def="$saved"
  # Дефолт для одного сервера — шлюз docker-моста: их compose публикует порт
  # наружу, а имени `bedolaga` в сети нет (у них своя сеть bot_network).
  [ -z "$def" ] && [ "$site" != site ] && def="http://172.17.0.1:8080"
  if [ -n "${BEDOLAGA_HOST:-}" ]; then
    # Переменной окружения можно поставить адрес без диалога (неинтерактив/CI).
    bed_host="$BEDOLAGA_HOST"
    ok "  Адрес бота Bedolaga задан переменной окружения: $bed_host"
  elif [ ! -e /dev/tty ]; then
    bed_host="$def"
    [ -n "$bed_host" ] || die "Нет терминала для вопроса. Задайте адрес: BEDOLAGA_HOST=https://bot.example.com ./install.sh site"
  else
    while :; do
      if [ -n "$def" ]; then
        read -r -p "$(printf '%s  Адрес API бота Bedolaga (host:port или URL)%s [%s]: ' "$BOLD" "$RST" "$def")" bed_host </dev/tty || true
        bed_host="${bed_host:-$def}"
      else
        read -r -p "$(printf '%s  Адрес API бота Bedolaga (напр. https://bot.example.com)%s: ' "$BOLD" "$RST")" bed_host </dev/tty || true
      fi
      [ -n "$bed_host" ] && break
      warn "  Поле обязательно."
    done
  fi
  # Схему принимаем из ответа, а не приклеиваем http:// поверх введённого —
  # иначе на «https://bot.example.com» выходило «http://https://…».
  bed_url="$(bedolaga_url "$bed_host")"
  case "$bed_url" in
    "https://$bed_host") info "  Схема не указана — беру https (домен). Нужен http — укажите его явно." ;;
  esac
  # Сохраняем ОБА: спрашивать адрес при каждом запуске и молча терять ответ —
  # ровно то, на что жаловались («сменить адрес бота повторным запуском нельзя»).
  set_env BEDOLAGA_HOST "$bed_host"
  set_env BEDOLAGA_UPSTREAM "$bed_url"
  # Проверяем адрес ДО вопроса про токен: иначе оператор вводит токен вслепую,
  # а ошибку в адресе видит только 502-ми в кабинете. `|| true` обязателен:
  # под `set -e` неудачная проверка иначе роняет всю установку.
  check_bedolaga_api "$bed_url" || true
  prompt_bedolaga_token "$bed_url"
}

publish_cabinet_auto() {
  local dom="$1" port="${2:-5002}" choice="${HTTPS:-}"

  # 1) Caddy УЖЕ установлен — только дописываем в него (второй прокси не ставим).
  if command -v caddy >/dev/null 2>&1; then
    ok "  Обнаружен установленный Caddy — вписываю кабинет в него."
    _caddy_add_vhost "$dom" "$port"
    return 0
  fi
  # 2) nginx УЖЕ установлен — добавляем vhost кабинета + сертификат.
  if command -v nginx >/dev/null 2>&1; then
    ok "  Обнаружен установленный nginx — добавляю vhost кабинета."
    _install_nginx_pkg
    _nginx_add_vhost "$dom" "$port"
    return 0
  fi
  # 3) Прокси нет, но 443 занят чем-то сторонним — не трогаем.
  if ss -ltn 2>/dev/null | grep -q ':443 '; then
    warn "  Порт 443 занят сторонним reverse-proxy — не трогаю его."
    say  "  Направьте его на ${BOLD}127.0.0.1:${port}${RST} (домен ${dom})."
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
    nginx) _install_nginx_pkg; _nginx_add_vhost "$dom" "$port" ;;
    none)  say "  ${DIM}Опубликуйте кабинет своим прокси: домен ${dom} → 127.0.0.1:${port}${RST}" ;;
    *)     _install_caddy_pkg; _caddy_add_vhost "$dom" "$port" ;;
  esac
}

# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  Режим SITE — только кабинет на отдельном сервере                         ║
# ╚══════════════════════════════════════════════════════════════════════════╝
if [ "$MODE" = "site" ]; then
  say "${BOLD}RemnaShop — установка кабинета на ОТДЕЛЬНОМ сервере${RST}"
  ok "Зависимости на месте"
  # Имя compose-проекта в site-режиме compose берёт по КАТАЛОГУ первого -f, то
  # есть `cabinet` у всех установок сразу: вторая на том же хосте разделила бы с
  # первой том состояния заморозки и перетёрла бы её образы. Задаём имя по
  # каталогу установки — но ТОЛЬКО на свежей: у работающей установки смена имени
  # проекта означала бы новый (пустой) том, то есть потерю пауз.
  FRESH_SITE=no
  [ -s .env ] || FRESH_SITE=yes
  [ -f .env ] || { : > .env; ok "Создан пустой .env для кабинета"; }
  # Права сужаем СРАЗУ, а не в конце: до flush_env в файл уже успевает лечь
  # админский токен «Бедолаги» (set_env пишет напрямую), а новый файл создаётся
  # с 644 — читаемым любым пользователем сервера.
  chmod 600 .env 2>/dev/null || true
  if [ "$FRESH_SITE" = yes ]; then
    PROJ="$(basename "$PWD" | tr 'A-Z' 'a-z' | tr -cd 'a-z0-9_-')"
    # Каталог самого репозитория дал бы имя `remnashop` — то же, что у установки
    # с ботом на этом хосте.
    [ "$PROJ" = remnashop ] && PROJ=remnashop-cabinet
    ensure COMPOSE_PROJECT_NAME "${PROJ:-remnashop-cabinet}"
  fi

  say ""
  say "${BOLD}Недостающие данные${RST} ${DIM}(секреты на сайт-сервере не нужны — их держит бот)${RST}"

  # Способ входа спросим ниже, когда узнаем URL кабинета (для подсказок).

  # Бэкенд спрашиваем ПЕРВЫМ: от него зависит, куда смотрит кабинет. Раньше
  # сначала спрашивали домен бота и писали по нему API_*, а потом ветка «Бедолаги»
  # писала те же ключи второй раз — .env получал дубли и литерал '$host', после
  # чего падала любая последующая команда compose.
  prompt_cabinet_backend site

  # Домен API бота — кабинет проксирует /api/ на ПУБЛИЧНЫЙ API бота по https
  # (его уже отдаёт reverse-proxy бота; WG/приватный канал не нужен).
  # Из домена выводим API_UPSTREAM (домен:443), API_SCHEME=https, API_HOST_HEADER=домен.
  if [ "$BACKEND_CHOICE" = bedolaga ]; then
    # Кабинет и адаптер поднимаются одним compose и видят друг друга по имени
    # сервиса. Пишем в .env, а не только в export: иначе установка живёт лишь
    # внутри install.sh, а `docker compose logs/up` потом падает без значений.
    set_env API_UPSTREAM "cabinet-adapter:8090"
    set_env API_SCHEME http
    # Литерал имени, а не '$host': доллар compose принимает за свою переменную.
    set_env API_HOST_HEADER "cabinet-adapter"
  elif need_value API_UPSTREAM; then
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
  export API_PATH_PREFIX="$(getval API_PATH_PREFIX)"
  # Публичный адрес кабинета нужен адаптеру для проверки источника изменяющих
  # запросов (CSRF): свой Host он видит как имя контейнера, а не как домен.
  export WEB_CABINET_URL="$(getval WEB_CABINET_URL)"

  # Кабинет поверх чужого бота ходит не в бота, а в адаптер рядом с собой.
  SITE_COMPOSE=(-f cabinet/docker-compose.site.yml)
  if [ "$BACKEND_CHOICE" = bedolaga ]; then
    SITE_COMPOSE+=(-f cabinet/docker-compose.site-adapter.yml)
    export BEDOLAGA_UPSTREAM="$(getval BEDOLAGA_UPSTREAM)"
    export BEDOLAGA_ADMIN_TOKEN="$(getval BEDOLAGA_ADMIN_TOKEN)"
    export REMNAWAVE_URL="$(getval REMNAWAVE_URL)"
    export REMNAWAVE_TOKEN="$(getval REMNAWAVE_TOKEN)"
    # Значения уже записаны в .env выше (при выборе бэкенда) — здесь только
    # переносим их в окружение процесса compose.
    export API_UPSTREAM="$(getval API_UPSTREAM)"
    export API_SCHEME="$(getval API_SCHEME)"
    export API_HOST_HEADER="$(getval API_HOST_HEADER)"
  fi

  # Пред-проверка: отвечает ли API бота (частая ошибка — неверный домен).
  if [ "$BACKEND_CHOICE" = bedolaga ]; then
    # На повторном запуске вопрос про адрес пропускается — тогда проверяем здесь.
    [ "$BED_CHECKED" = 1 ] || check_bedolaga_api "$(getval BEDOLAGA_UPSTREAM)" || true
  else
    PRE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "https://${API_DOM}/api/v1/public/auth/me" 2>/dev/null || true)"
    PRE="${PRE:-000}"
    if [ "$PRE" = "401" ] || [ "$PRE" = "200" ]; then
      ok "  API бота https://${API_DOM} отвечает (${PRE})"
    else
      warn "  https://${API_DOM}/api/v1/public/auth/me вернул '${PRE}' (ожидался 401)."
      warn "  Проверьте, что это верный домен API бота и он доступен по https."
      warn "  Кабинет соберу, но /api/ может не работать, пока домен не отвечает."
    fi
  fi

  say ""
  # Идемпотентность повторного запуска: убираем прежний контейнер/сеть кабинета,
  # чтобы застрявшее с прошлой (возможно неудачной) установки состояние не мешало —
  # частая причина «встаёт только после переустановки ОС» (занятый порт 5002 /
  # старый контейнер с тем же именем / протухший build-кэш). down чистит их, а
  # --force-recreate гарантирует пересоздание с новой конфигурацией/образом.
  CAB_PORT="$(getval CABINET_PORT)"; CAB_PORT="${CAB_PORT:-5002}"
  if ss -ltn 2>/dev/null | grep -q "127.0.0.1:${CAB_PORT} "; then
    warn "  Порт 127.0.0.1:${CAB_PORT} занят — освобождаю прежний контейнер кабинета."
  fi
  $DC --env-file .env "${SITE_COMPOSE[@]}" down --remove-orphans 2>/dev/null || true
  if [ "$BACKEND_CHOICE" = bedolaga ]; then
    info "Собираю и поднимаю кабинет с адаптером (Bedolaga: $(getval BEDOLAGA_UPSTREAM))…"
  else
    info "Собираю и поднимаю кабинет (проксирует /api/ → https://${API_DOM})…"
  fi
  $DC --env-file .env "${SITE_COMPOSE[@]}" up -d --build --force-recreate

  if [ "$BACKEND_CHOICE" = bedolaga ]; then
    ADP="$(getval ADAPTER_CONTAINER)"
    adapter_healthcheck "${ADP:-remnashop-cabinet-adapter}" || true
  fi

  say ""
  ok "${BOLD}Готово!${RST}"
  say "  Кабинет: ${DIM}127.0.0.1:${CAB_PORT}${RST}  → проксируйте на ${BOLD}${CAB_URL:-ваш домен кабинета}${RST}"
  say ""
  say "  Логи:   ${DIM}$DC ${SITE_COMPOSE[*]} logs -f${RST}"
  say "  ${YLW}Дальше:${RST} reverse-proxy с TLS (свободный 443) на домен кабинета → 127.0.0.1:${CAB_PORT}."
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
# Сужаем права до записи секретов: в .env лежат токен бота, JWT и пароль почты,
# а дальше ляжет ещё и админский токен «Бедолаги» (set_env пишет сразу в файл).
chmod 600 .env 2>/dev/null || true

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
# Прежний бэкенд запоминаем ДО опроса: по нему видно, что это возврат с «Бедолаги»,
# и надо убрать её адаптер (сам он из compose уже исчезнет и станет «сиротой»).
PREV_BACKEND="$(getval CABINET_BACKEND)"
# В режиме api кабинета здесь нет — он на другой машине, и бэкенд выбирают ТАМ,
# своей установкой. Спрашивать тут было незачем: ответ ничего бы не изменил,
# а «2» молча прописала бы адрес несуществующего адаптера.
[ "$WITH_CABINET" = yes ] && prompt_cabinet_backend
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
# Кабинет поверх чужого бота работает через адаптер — добавляем его файл, чтобы
# обычный `docker compose ...` без -f видел и поднимал этот сервис тоже.
ADAPTER_YML="cabinet/docker-compose.adapter.yml"
CABINET_COMPOSE=(-f docker-compose.yml -f cabinet/docker-compose.cabinet.yml)
COMPOSE_LIST="docker-compose.yml:cabinet/docker-compose.cabinet.yml"
if [ "$BACKEND_CHOICE" = bedolaga ]; then
  CABINET_COMPOSE+=(-f "$ADAPTER_YML")
  # Кабинет ходит в адаптер рядом, а не в бота: имя контейнера адаптера в общей
  # сети. Пишем принудительно — при смене бэкенда адрес обязан перезаписаться.
  # API_HOST_HEADER/API_SCHEME не трогаем: их дефолты живут в образе кабинета
  # (Dockerfile), а литерал '$host' в .env compose принимает за свою переменную
  # и подставляет пустоту — после этого падает любая команда compose.
  # Имя контейнера адаптера читаем из .env (compose берёт его оттуда же): у второй
  # установки на хосте оно своё, и прошитое имя увело бы кабинет в чужой адаптер.
  ADP_NAME="$(getval ADAPTER_CONTAINER)"; ADP_NAME="${ADP_NAME:-${ADAPTER_CONTAINER:-remnashop-cabinet-adapter}}"
  set_env API_UPSTREAM "${ADP_NAME}:8090"
else
  # Возврат на наш бот: адрес обязан уйти с адаптера, иначе кабинет продолжит
  # смотреть в несуществующий сервис и отвечать 502 на весь /api/*. Сверяем и с
  # именем из .env: у второй установки адаптер называется по-своему, и проверки
  # по подстроке `cabinet-adapter` не хватало. Чужие адреса не трогаем.
  ADP_NAME="$(getval ADAPTER_CONTAINER)"; ADP_NAME="${ADP_NAME:-remnashop-cabinet-adapter}"
  case "$(getval API_UPSTREAM)" in
    *cabinet-adapter*|"${ADP_NAME}:8090") set_env API_UPSTREAM "remnashop:5000" ;;
  esac
fi
if [ "$BACKEND_CHOICE" != bedolaga ] && [ "$PREV_BACKEND" = bedolaga ]; then
  # Контейнер адаптера остаётся работать и без своего compose-файла: compose его
  # больше не видит и ругается «orphan containers», подсказывая --remove-orphans
  # (которым легко снести заодно и нужное). Убираем его сами. Том с паузами
  # (adapter-state) не трогаем — он понадобится, если оператор вернётся обратно.
  ADP="$(getval ADAPTER_CONTAINER)"; ADP="${ADP:-remnashop-cabinet-adapter}"
  if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$ADP"; then
    docker rm -f "$ADP" >/dev/null 2>&1 || true
    ok "  Адаптер Bedolaga удалён (память о паузах сохранена в томе adapter-state)"
  fi
fi
if [ "$WITH_CABINET" = yes ]; then
  # В COMPOSE_FILE у оператора бывают СВОИ файлы (напр. docker-compose.local-ha.yml).
  # Поэтому список не переписываем целиком, а трогаем ровно одну запись — адаптера:
  # переписав его под себя, повторный install.sh молча выкинул бы чужие файлы, и
  # `docker compose` без -f перестал бы видеть их сервисы.
  CUR_CF="$(getval COMPOSE_FILE)"
  NEW_CF="$(compose_list_drop "${CUR_CF:-$COMPOSE_LIST}" "$ADAPTER_YML")"
  [ "$BACKEND_CHOICE" = bedolaga ] && NEW_CF="${NEW_CF:+$NEW_CF:}$ADAPTER_YML"
  [ "$NEW_CF" = "$CUR_CF" ] || set_env COMPOSE_FILE "$NEW_CF"
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
  if [ "$BACKEND_CHOICE" = bedolaga ]; then
    # Честно: этот режим поднимает и НАШЕГО бота с его БД/воркерами — он нужен
    # только тем, у кого оба бота на одной машине. Для чистой «Бедолаги» путь
    # другой, иначе оператор будет искать, зачем ему наша инфраструктура.
    warn "Поднимутся и наш бот с БД/воркерами. Только кабинет+адаптер ставит ./install.sh site"
    info "Собираю и поднимаю бота (overlay), воркеры, кабинет и адаптер Bedolaga…"
  else
    info "Собираю и поднимаю бота (overlay), воркеры и кабинет…"
  fi
  $DC "${CABINET_COMPOSE[@]}" up -d --build
  if [ "$BACKEND_CHOICE" = bedolaga ]; then
    ADP="$(getval ADAPTER_CONTAINER)"
    adapter_healthcheck "${ADP:-remnashop-cabinet-adapter}" || true
  fi
  say ""
  ok "${BOLD}Готово!${RST}"
  CAB_PORT="$(getval CABINET_PORT)"; CAB_PORT="${CAB_PORT:-5002}"
  say "  Бот и API:  ${DIM}127.0.0.1:5000${RST}"
  say "  Кабинет:    ${DIM}127.0.0.1:${CAB_PORT}${RST}"
  say ""

  # Пытаемся опубликовать кабинет автоматически через Caddy панели Remnawave.
  CAB_DOM="${CAB_URL#http://}"; CAB_DOM="${CAB_DOM#https://}"; CAB_DOM="${CAB_DOM%%/*}"
  if wire_cabinet_into_panel_caddy "$CAB_DOM"; then
    say "  ${GRN}Кабинет опубликован автоматически (Caddy панели):${RST} ${BOLD}https://${CAB_DOM}${RST}"
    say "  ${DIM}(проверьте A-запись ${CAB_DOM} → IP этого сервера)${RST}"
  else
    # Caddy панели нет — ставим свой reverse-proxy сами (выбор Caddy/nginx).
    publish_cabinet_auto "$CAB_DOM" "$CAB_PORT"
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
  say "  Логи:   ${DIM}$DC ${CABINET_COMPOSE[*]} logs -f${RST}"
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
