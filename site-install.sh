#!/usr/bin/env bash
#
# RemnaShop — установка кабинета на ОТДЕЛЬНОМ сервере ОДНОЙ командой.
#
#   curl -fsSL https://raw.githubusercontent.com/alexdsndr161rus2015-maker/remnashop-cabinet/main/site-install.sh | bash
#
# Делает на чистом сервере ВСЁ:
#   1. Docker + Compose            (если нет)
#   2. Код проекта                 (тарбол — устойчиво на капризном канале)
#   3. Сборку кабинета             (install.sh site — спросит 3 значения)
#   4. HTTPS-публикацию            (Caddy — ОПЦИОНАЛЬНО, по выбору)
#
# Спросит username бота, домен API бота, URL кабинета.
# Секреты на сайт-сервере не нужны — их держит бот.
#
# Caddy не навязывается: если 443 уже занят (свой nginx / Caddy-контейнер панели
# Remnawave) — скрипт его НЕ трогает и просто покажет блок для вашего прокси.
# Принудительно: USE_CADDY=no (не ставить) либо USE_CADDY=yes (ставить).

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
  say "  ${BOLD}Кабинет поднят на 127.0.0.1:5002 (без TLS).${RST}"
  say "  Добавьте в ВАШ reverse-proxy блок для домена кабинета, например (Caddy):"
  say "    ${DIM}${CAB_DOM} {${RST}"
  say "    ${DIM}    reverse_proxy 127.0.0.1:5002${RST}"
  say "    ${DIM}}${RST}"
  say "  …или эквивалент в nginx (proxy_pass http://127.0.0.1:5002;)."
}

# Решение: ставить ли свой Caddy.
#   USE_CADDY=yes|no  — можно задать заранее (неинтерактивно).
#   Иначе: если 443 занят (свой прокси/панель) — НЕ трогаем; если свободен — спросим.
say ""
DECISION="${USE_CADDY:-}"
if [ -z "$DECISION" ]; then
  if ss -ltn 2>/dev/null | grep -q ':443 '; then
    warn "Порт 443 уже занят — похоже, у вас свой reverse-proxy (nginx / Caddy-панели)."
    warn "Свой Caddy НЕ ставлю, чтобы не сломать ваш 443 (и webhook'и панели)."
    DECISION="no"
  elif [ -e /dev/tty ]; then
    printf '%sПоставить Caddy и автоматически выпустить TLS на 443? [Y/n]: %s' "$BOLD" "$RST"
    read -r _ans </dev/tty || _ans=""
    case "${_ans:-y}" in [Nn]*) DECISION="no";; *) DECISION="yes";; esac
  else
    DECISION="yes"   # чистый сервер, неинтерактивно — ставим
  fi
fi

if [ "$DECISION" = "yes" ]; then
  if ss -ltn 2>/dev/null | grep -q ':443 '; then
    warn "443 занят — пропускаю настройку Caddy, чтобы не конфликтовать."
    proxy_hint
  else
    install_caddy
    cat > /etc/caddy/Caddyfile <<EOF
${CAB_DOM} {
    reverse_proxy 127.0.0.1:5002
}
EOF
    systemctl restart caddy
    ok "Caddy: ${CAB_DOM} → 127.0.0.1:5002 (TLS авто-сертификат)"
  fi
else
  proxy_hint
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
say "  Проверка после смены DNS (~30 сек на сертификат):"
say "    ${DIM}curl -s -o /dev/null -w 'SPA %{http_code}\\n' https://${CAB_DOM}/${RST}"
say "    ${DIM}curl -s -o /dev/null -w 'API %{http_code}\\n' https://${CAB_DOM}/api/auth/me${RST}"
