#!/usr/bin/env bash
#
# RemnaShop — установка кабинета на ОТДЕЛЬНОМ сервере ОДНОЙ командой.
#
#   curl -fsSL https://raw.githubusercontent.com/alexdsndr161rus2015-maker/remnashop-cabinet/main/site-install.sh | bash
#
# Делает на чистом сервере ВСЁ сам:
#   1. Docker + Compose            (если нет)
#   2. Код проекта                 (тарбол — устойчиво на капризном канале)
#   3. Сборку кабинета             (install.sh site — спросит 3 значения)
#   4. HTTPS на 443                 (Caddy ИЛИ nginx — оба с авто-сертификатом)
#
# Спросит username бота, домен API бота, URL кабинета — и один выбор: чем поднять
# HTTPS (Caddy / nginx). Секреты на сайт-сервере не нужны — их держит бот.
#
# Если 443 УЖЕ занят (свой reverse-proxy / Caddy панели) — скрипт его НЕ трогает,
# а просто покажет готовые блоки (Caddy и nginx) для вставки в ваш прокси.
# Переопределить без вопроса: HTTPS=caddy | nginx | none.

set -euo pipefail

REPO_URL="https://github.com/alexdsndr161rus2015-maker/remnashop-cabinet"
BRANCH="${BRANCH:-main}"
DEST="${DEST:-/opt/remnashop-cabinet}"

if [ -t 1 ]; then
  BOLD=$'\e[1m'; DIM=$'\e[2m'; GRN=$'\e[32m'; YLW=$'\e[33m'; CYN=$'\e[36m'; RST=$'\e[0m'
else BOLD=""; DIM=""; GRN=""; YLW=""; CYN=""; RST=""; fi
info() { printf '%s➜%s %s\n' "$CYN" "$RST" "$*"; }
ok()   { printf '%s✓%s %s\n' "$GRN" "$RST" "$*"; }
warn() { printf '%s!%s %s\n' "$YLW" "$RST" "$*"; }
die()  { printf '✗ %s\n' "$*" >&2; exit 1; }

say() { printf '%s\n' "$*"; }
say "${BOLD}RemnaShop — установка кабинета на отдельном сервере (одной командой)${RST}"

[ "$(id -u)" = 0 ] || die "Запустите от root (или через sudo)."
command -v curl >/dev/null 2>&1 || die "Нужен curl."

# ── 1. Docker + Compose ───────────────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
  info "Устанавливаю Docker…"
  curl -fsSL https://get.docker.com | sh
fi
docker compose version >/dev/null 2>&1 || die "Нет плагина 'docker compose'. Установите Docker заново."
ok "Docker на месте"

# ── 2. Caddy ставим ПОЗЖЕ и ТОЛЬКО по выбору (см. секцию 5) ────────────────────
# Caddy НЕ навязываем: если у вас уже есть reverse-proxy (nginx / Caddy-контейнер
# панели Remnawave / др.), второй Caddy займёт 443 и сломает выдачу webhook'ов.
install_caddy() {
  command -v caddy >/dev/null 2>&1 && return 0
  info "Устанавливаю Caddy…"
  export DEBIAN_FRONTEND=noninteractive
  apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https gnupg >/dev/null
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq && apt-get install -y -qq caddy >/dev/null
}

# ── 3. Код проекта (тарбол устойчивее git на плохом канале) ────────────────────
# Тянем КАЖДЫЙ раз — поэтому повторный запуск этой команды = ОБНОВЛЕНИЕ кабинета
# до последней версии. .env не в архиве, поэтому настройки сохраняются.
info "Скачиваю/обновляю код в $DEST…"
mkdir -p "$DEST"
curl -fL "$REPO_URL/archive/refs/heads/$BRANCH.tar.gz" | tar xz -C "$DEST" --strip-components=1
cd "$DEST"
ok "Код в $DEST"

# ── 4. Сборка кабинета (спросит 3 значения, поднимет контейнер на :5002) ────────
say ""
bash install.sh site

# ── 5. Публикация кабинета по HTTPS (Caddy — ОПЦИОНАЛЬНО, по выбору) ────────────
CAB_URL="$(grep -E '^WEB_CABINET_URL=' .env | tail -1 | cut -d= -f2- || true)"
CAB_DOM="${CAB_URL#http://}"; CAB_DOM="${CAB_DOM#https://}"; CAB_DOM="${CAB_DOM%%/*}"
[ -n "$CAB_DOM" ] || die "Не нашёл WEB_CABINET_URL в .env."

# Блок, который пользователь добавит в СВОЙ reverse-proxy, если Caddy не ставим.
proxy_hint() {
  say ""
  say "  ${BOLD}Кабинет поднят на 127.0.0.1:5002 (без TLS).${RST} Опубликуйте его СВОИМ прокси —"
  say "  возьмите блок под то, что у вас стоит:"
  say ""
  say "  ${BOLD}Caddy${RST} (сертификат выпустит сам):"
  say "    ${DIM}${CAB_DOM} {${RST}"
  say "    ${DIM}    reverse_proxy 127.0.0.1:5002${RST}"
  say "    ${DIM}}${RST}"
  say ""
  say "  ${BOLD}nginx${RST} (сертификат — через certbot):"
  say "    ${DIM}server {${RST}"
  say "    ${DIM}    listen 443 ssl;  server_name ${CAB_DOM};${RST}"
  say "    ${DIM}    ssl_certificate     /etc/letsencrypt/live/${CAB_DOM}/fullchain.pem;${RST}"
  say "    ${DIM}    ssl_certificate_key /etc/letsencrypt/live/${CAB_DOM}/privkey.pem;${RST}"
  say "    ${DIM}    location / {${RST}"
  say "    ${DIM}        proxy_pass http://127.0.0.1:5002;${RST}"
  say "    ${DIM}        proxy_set_header Host \$host;${RST}"
  say "    ${DIM}        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;${RST}"
  say "    ${DIM}        proxy_set_header X-Forwarded-Proto \$scheme;${RST}"
  say "    ${DIM}    }${RST}"
  say "    ${DIM}}${RST}"
}

# Caddy: поставить + настроить, сертификат выпустится сам.
setup_caddy() {
  install_caddy
  cat > /etc/caddy/Caddyfile <<EOF
${CAB_DOM} {
    reverse_proxy 127.0.0.1:5002
}
EOF
  systemctl restart caddy
  ok "Caddy настроен сам: ${CAB_DOM} → 127.0.0.1:5002 (TLS авто-сертификат)"
}

# nginx + certbot: поставить, настроить vhost и ВЫПУСТИТЬ СЕРТИФИКАТ автоматически.
setup_nginx() {
  info "Устанавливаю nginx и certbot…"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq nginx certbot python3-certbot-nginx >/dev/null
  cat > /etc/nginx/sites-available/cabinet.conf <<EOF
server {
    listen 80;
    server_name ${CAB_DOM};
    location / {
        proxy_pass http://127.0.0.1:5002;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
  ln -sf /etc/nginx/sites-available/cabinet.conf /etc/nginx/sites-enabled/cabinet.conf
  rm -f /etc/nginx/sites-enabled/default
  nginx -t >/dev/null 2>&1 && systemctl reload nginx
  # Сертификат: нужна A-запись на этот сервер и открытый 80. certbot сам пропишет
  # 443 в конфиг nginx и включит редирект.
  if certbot --nginx -d "${CAB_DOM}" --non-interactive --agree-tos \
       --register-unsafely-without-email --redirect >/dev/null 2>&1; then
    ok "nginx + TLS настроены сами: ${CAB_DOM} → 127.0.0.1:5002"
  else
    warn "nginx поднят на http, но сертификат пока не выпустился"
    warn "(обычно — A-запись ещё не указывает на сервер). После настройки DNS:"
    say  "    ${DIM}certbot --nginx -d ${CAB_DOM} --redirect${RST}"
  fi
}

# Решаем сами, минимум вопросов:
#   • 443 ЗАНЯТ    → у вас уже есть свой reverse-proxy — не трогаем, показываем блоки;
#   • 443 СВОБОДЕН → ставим и настраиваем всё сами. Один выбор: Caddy или nginx
#     (оба выпускают сертификат автоматически). По умолчанию Caddy.
# Переопределить без вопроса: HTTPS=caddy | nginx | none (none = свой прокси).
say ""
if ss -ltn 2>/dev/null | grep -q ':443 '; then
  warn "Порт 443 уже занят — у вас свой reverse-proxy. Не трогаю его."
  proxy_hint
else
  CHOICE="${HTTPS:-}"
  if [ -z "$CHOICE" ] && [ -e /dev/tty ]; then
    say "  ${BOLD}Чем поднять HTTPS на 443?${RST} (оба настроятся и выпустят сертификат сами)"
    printf '    %s[1]%s Caddy (по умолчанию)   %s[2]%s nginx   %s[3]%s ничего (свой прокси): ' \
      "$BOLD" "$RST" "$BOLD" "$RST" "$BOLD" "$RST"
    read -r _a </dev/tty || _a=""
    case "${_a}" in 2) CHOICE=nginx ;; 3) CHOICE=none ;; *) CHOICE=caddy ;; esac
  fi
  case "${CHOICE:-caddy}" in
    nginx) setup_nginx ;;
    none)  proxy_hint ;;
    *)     setup_caddy ;;
  esac
fi

# ── Итог ───────────────────────────────────────────────────────────────────────
say ""
ok "${BOLD}Готово!${RST}"
say "  Кабинет: ${BOLD}https://${CAB_DOM}${RST}"
say ""
say "  ${YLW}Проверьте:${RST}"
say "   • A-запись ${BOLD}${CAB_DOM}${RST} → IP этого сервера;"
say "   • у провайдера открыты входящие ${BOLD}TCP 80 и 443${RST} (80 нужен для выпуска сертификата)."
say ""
say "  ${YLW}Не забудьте (для входа через Telegram в браузере):${RST}"
say "   привяжите домен кабинета к боту в @BotFather:"
say "     • новые боты: Bot Settings → Web Login → Allowed URLs → ${BOLD}https://${CAB_DOM}${RST}"
say "     • старые боты: /setdomain → ${BOLD}${CAB_DOM}${RST} (без https://)"
say ""
say "  Проверка после смены DNS (~30 сек на сертификат):"
say "    ${DIM}curl -s -o /dev/null -w 'SPA %{http_code}\\n' https://${CAB_DOM}/${RST}"
say "    ${DIM}curl -s -o /dev/null -w 'API %{http_code}\\n' https://${CAB_DOM}/api/auth/me${RST}"
