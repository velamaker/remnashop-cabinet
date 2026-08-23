#!/usr/bin/env bash
#
# check-update.sh — проверка overlay на текущем (или указанном) базовом образе
# ПЕРЕД деплоем. На проде ничего не трогает: только собирает временный образ и
# гоняет статические проверки.
#
#   ./check-update.sh              # проверить с тегом base из Dockerfile
#   ./check-update.sh v0.8.3       # проверить с другим тегом base (для бампа)
#
# Зелёный прогон (один alembic head + сборка app + sed-патч точки входа) —
# можно деплоить / бампать пин в Dockerfile.

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

GRN=$'\e[32m'; RED=$'\e[31m'; YLW=$'\e[33m'; RST=$'\e[0m'
ok()   { printf '%s✓%s %s\n' "$GRN" "$RST" "$*"; }
die()  { printf '%s✗ %s%s\n' "$RED" "$*" "$RST" >&2; exit 1; }
info() { printf '%s➜%s %s\n' "$YLW" "$RST" "$*"; }

[ -f .env ] || die "Нет .env"
docker network inspect remnawave-network >/dev/null 2>&1 || die "Нет docker-сети remnawave-network"

IMG="remnashop-overlay-check"
DF="Dockerfile"

# Если передан тег — собрать на нём, не трогая Dockerfile (через временный Dockerfile).
if [ "${1:-}" != "" ]; then
  TAG="$1"
  info "Проверяю overlay на base ghcr.io/snoups/remnashop:${TAG}"
  DF="$(mktemp)"; trap 'rm -f "$DF"' EXIT
  sed "s#^FROM ghcr.io/snoups/remnashop:.*#FROM ghcr.io/snoups/remnashop:${TAG}#" Dockerfile > "$DF"
else
  # Тег базы вынесен в `ARG BASE_TAG`, и строка FROM ссылается на него переменной.
  # Читать её через sed значит получить литерал «${BASE_TAG}» — проверка тогда
  # пытается тянуть несуществующий образ и падает ещё до первого теста.
  # Порядок как при сборке: BASE_TAG из .env перекрывает умолчание Dockerfile.
  # `|| true` обязателен: при `set -e` присваивание из подстановки, где grep не
  # нашёл строку, роняет скрипт МОЛЧА — без единого слова в выводе. BASE_TAG в
  # .env появляется только после `update.sh --base`, так что «не нашёл» — норма.
  TAG="$(grep -m1 '^BASE_TAG=' .env 2>/dev/null | cut -d= -f2- | tr -d "\"' " || true)"
  [ -n "$TAG" ] || TAG="$(grep -m1 '^ARG BASE_TAG=' Dockerfile | cut -d= -f2- || true)"
  [ -n "$TAG" ] || die "Не удалось определить тег базового образа (ни BASE_TAG в .env, ни ARG BASE_TAG в Dockerfile)"
  info "Проверяю overlay на base (тег: ${TAG})"
fi

# Контакт с исходниками бота — ПЕРВЫМ делом, до сборки. Смысл проверки в том,
# что сборка её не заменит: образ соберётся и на изменившемся апстриме, просто
# наши копии молча затрут чужие правки. Здесь это становится видно поимённо.
info "Контакт overlay с исходниками бота…"
./scripts/check-base-contact.py "$TAG" || die "Контакт с базой разошёлся с манифестом (см. выше)"

info "Сборка образа (sed-патч точки входа проверяется здесь же)…"
docker build -f "$DF" --build-arg BASE_TAG="$TAG" -t "$IMG" . >/dev/null || die "Сборка упала (возможно, sed точки входа не сматчился на новом base)"
ok "Образ собран, точка входа overlay на месте"

info "Проверка alembic: должен быть РОВНО один head…"
HEADS="$(docker run --rm "$IMG" sh -c 'alembic -c src/infrastructure/database/alembic.ini heads 2>/dev/null' | grep -c '(head)')"
[ "$HEADS" = "1" ] || die "Ожидался один alembic head, найдено: ${HEADS} (конфликт миграций base/overlay)"
ok "alembic heads = 1"

info "Правки поведения бота (overlay_patches)…"
# Правка, которая не применилась, НЕ роняет интерпретатор (иначе сломались бы pip и
# alembic), поэтому её отсутствие надо ловить именно здесь — иначе бот уедет в прод
# на исходном поведении базы, и заметит это только владелец по симптомам.
docker run --rm --env-file .env "$IMG" sh -c 'python -c "
import overlay_patches as op, sys
for line in op.applied(): print(\"  \" + line)
if op.failures():
    for name, why in op.failures(): print(f\"НЕ ПРИМЕНИЛАСЬ: {name}: {why}\", file=sys.stderr)
    sys.exit(1)
"' || die "overlay-правки не применились к этому base (подробности выше)"
ok "Правки применились"

info "Сборка FastAPI-приложения (импорты overlay против base)…"
# Роуты считаем по openapi(), а НЕ обходом app.routes. С FastAPI 0.140 включённые
# роутеры лежат в app.routes обёртками `_IncludedRouter` и плоского `path` у них нет —
# прежняя проверка «any(... in r.path)» перестала находить что-либо вообще и роняла
# предсборочный тест на полностью исправном образе. openapi() — публичная поверхность
# приложения, она переживает такие перестановки внутри фреймворка.
docker run --rm --env-file .env --network remnawave-network "$IMG" \
  sh -c 'python -c "
import src.overlay_app as m
paths = m.application().openapi()[\"paths\"]
adm = [p for p in paths if p.startswith(\"/api/v1/admin/\")]
pub = [p for p in paths if p.startswith(\"/api/v1/public/\")]
assert adm, \"нет admin-роутов\"
assert pub, \"нет overlay public-роутов\"
print(f\"admin: {len(adm)}, public: {len(pub)}\")
"' \
  || die "application() не собрался — overlay несовместим с этим base (проверь переименованные импорты)"
ok "Приложение собирается, admin/public-роуты на месте"

printf '\n%sГОТОВО:%s overlay совместим с base %s — можно деплоить.\n' "$GRN" "$RST" "$TAG"
