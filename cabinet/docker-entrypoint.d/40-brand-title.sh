#!/bin/sh
# Подставляет бренд в <title>/<meta> собранного index.html при старте контейнера.
# Нужно, потому что превью ссылки в мессенджерах (Telegram и др.) читают HTML БЕЗ JS —
# и без этого у всех, кто поставил кабинет, в превью висел бы бренд разработчика.
#
# Источник бренда (по приоритету):
#   1) API бота  ${API_SCHEME}://${API_UPSTREAM}/api/v1/public/appearance → brand_name
#   2) env CABINET_BRAND
#   3) нейтральный дефолт «Личный кабинет»
#
# Запускается официальным entrypoint'ом nginx (после envsubst шаблонов).
set -e

HTML=/usr/share/nginx/html/index.html
[ -f "$HTML" ] || exit 0
grep -q "__CABINET_BRAND__" "$HTML" || exit 0  # уже подставлено — выходим

brand=""
url="${API_SCHEME:-http}://${API_UPSTREAM:-remnashop:5000}/api/v1/public/appearance"
# Кабинет может стартовать раньше API бота — ретраим ожидание (до ~20с).
i=0
while [ "$i" -lt 10 ]; do
  json="$(wget -q -T 4 -O - "$url" 2>/dev/null || true)"
  brand="$(printf '%s' "$json" | sed -n 's/.*"brand_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"
  [ -n "$brand" ] && break
  i=$((i + 1))
  sleep 2
done

[ -z "$brand" ] && brand="${CABINET_BRAND:-}"
[ -z "$brand" ] && brand="Личный кабинет"

# Экранируем спецсимволы sed-замены (& / \).
esc="$(printf '%s' "$brand" | sed -e 's/[&/\\]/\\&/g')"
sed -i "s/__CABINET_BRAND__/${esc}/g" "$HTML"
echo "40-brand-title: title/description set to '$brand'"
