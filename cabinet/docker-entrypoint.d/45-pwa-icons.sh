#!/bin/sh
# Генерирует иконки PWA из ЗАГРУЖЕННОГО логотипа бренда (appearance.logo_url),
# чтобы на «экране Домой» была иконка бренда установки, а не дефолтная «R».
# Если логотип не загружен, imagemagick недоступен или конвертация не удалась —
# остаются дефолтные PNG из образа (graceful fallback, старт не блокируется).
#
# Запускается официальным entrypoint'ом nginx ПОСЛЕ 40-brand-title.sh.
set -e

HTML_DIR=/usr/share/nginx/html
command -v convert >/dev/null 2>&1 || { echo "45-pwa-icons: no imagemagick, keep default"; exit 0; }

api="${API_SCHEME:-http}://${API_UPSTREAM:-remnashop:5000}"
json="$(wget -q -T 5 -O - "${api}/api/v1/public/appearance" 2>/dev/null || true)"
logo_path="$(printf '%s' "$json" | sed -n 's/.*"logo_url"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"
[ -n "$logo_path" ] || { echo "45-pwa-icons: no brand logo, keep default"; exit 0; }

# logo_url отдаётся как путь для БРАУЗЕРА через прокси кабинета (/api/appearance/...).
# Из контейнера ходим напрямую в API бота: /api/... → /api/v1/public/...
botpath="$(printf '%s' "$logo_path" | sed 's#^/api/#/api/v1/public/#')"
tmp=/tmp/brand-logo.src
wget -q -T 8 -O "$tmp" "${api}${botpath}" 2>/dev/null || { echo "45-pwa-icons: logo fetch failed, keep default"; exit 0; }
[ -s "$tmp" ] || exit 0

# any-иконки: заливаем квадрат логотипом (кроп излишка по центру).
gen() { # size out
  convert "$tmp" -resize "${1}x${1}^" -gravity center -extent "${1}x${1}" -strip "$HTML_DIR/$2" 2>/dev/null
}
gen 192 icon-192.png || { echo "45-pwa-icons: convert failed, keep default"; exit 0; }
gen 512 icon-512.png || exit 0
gen 180 apple-touch-icon.png || true

# maskable: логотип в safe-zone (~79%) на фоне углового пикселя (бесшовно под маску ОС).
bg="$(convert "$tmp" -format '%[pixel:p{0,0}]' info: 2>/dev/null || echo '#0a0a0d')"
convert "$tmp" -resize 404x404 -background "$bg" -gravity center -extent 512x512 \
  -strip "$HTML_DIR/icon-maskable-512.png" 2>/dev/null || true

# Фавикон вкладки браузера из лого бренда (по умолчанию был статичный «R» из
# favicon.svg). Генерим PNG и подменяем <link rel="icon"> в собранном index.html.
# Если конвертация не удалась — index.html не трогаем, остаётся дефолтный favicon.svg.
if gen 32 favicon-32.png; then
  sed -i 's#<link rel="icon"[^>]*href="/favicon\.svg"[^>]*/>#<link rel="icon" type="image/png" href="/favicon-32.png" />#' \
    "$HTML_DIR/index.html" 2>/dev/null || true
fi

rm -f "$tmp"
echo "45-pwa-icons: brand icons generated from $logo_path"
