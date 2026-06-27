#!/usr/bin/env bash
#
# RemnaShop — установка кабинета на ОТДЕЛЬНОМ сервере ОДНОЙ командой.
#
#   curl -fsSL https://raw.githubusercontent.com/alexdsndr161rus2015-maker/remnashop-cabinet/main/site-install.sh | bash
#
# Делает на чистом сервере ВСЁ:
#   1. Docker + Compose            (если нет)
#   2. Caddy                       (если нет)
#   3. Код проекта                 (тарбол — устойчиво на капризном канале)
#   4. Сборку кабинета             (install.sh site — спросит 3 значения)
#   5. TLS-прокси Caddy на 443      (по домену кабинета, авто-сертификат)
#
# Спросит ровно 3 значения: username бота, домен API бота, URL кабинета.
# Секреты на сайт-сервере не нужны — их держит бот.

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

# ── 2. Caddy ──────────────────────────────────────────────────────────────────
if ! command -v caddy >/dev/null 2>&1; then
  info "Устанавливаю Caddy…"
  export DEBIAN_FRONTEND=noninteractive
  apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https gnupg >/dev/null
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq && apt-get install -y -qq caddy >/dev/null
fi
ok "Caddy на месте"

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

# ── 5. Caddy на 443 для домена кабинета ────────────────────────────────────────
CAB_URL="$(grep -E '^WEB_CABINET_URL=' .env | tail -1 | cut -d= -f2- || true)"
CAB_DOM="${CAB_URL#http://}"; CAB_DOM="${CAB_DOM#https://}"; CAB_DOM="${CAB_DOM%%/*}"
[ -n "$CAB_DOM" ] || die "Не нашёл WEB_CABINET_URL в .env — пропускаю настройку Caddy."

say ""
if ss -ltn 2>/dev/null | grep -q ':443 '; then
  warn "Порт 443 занят — Caddy на 443 НЕ настраиваю (на этом сервере уже что-то слушает 443)."
  warn "Освободите 443 или добавьте блок в свой прокси вручную:"
  say  "    ${DIM}${CAB_DOM} { reverse_proxy 127.0.0.1:5002 }${RST}"
else
  cat > /etc/caddy/Caddyfile <<EOF
${CAB_DOM} {
    reverse_proxy 127.0.0.1:5002
}
EOF
  systemctl restart caddy
  ok "Caddy: ${CAB_DOM} → 127.0.0.1:5002 (TLS авто-сертификат)"
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
