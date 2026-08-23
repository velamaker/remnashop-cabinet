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

info "Сборка образа…"
docker build -f "$DF" --build-arg BASE_TAG="$TAG" -t "$IMG" . >/dev/null || die "Сборка упала"
ok "Образ собран"

# Скрипт запуска бота мы больше не переписываем: он зовёт src.__main__:application,
# а эта фабрика сама отдаёт кабинет. Проверяем оба конца — что файл побайтово равен
# базовому И что через него поднимается именно наше приложение. Одного мало: файл
# может совпасть при неприменившейся правке, и тогда вместо кабинета встанет голый бот.
info "Точка входа: скрипт бота не тронут, приложение — наше…"
BASE_SUM="$(docker run --rm --entrypoint sh "ghcr.io/snoups/remnashop:${TAG}" -c 'sha256sum docker-entrypoint.sh' | awk '{print $1}')"
OURS_SUM="$(docker run --rm --entrypoint sh "$IMG" -c 'sha256sum docker-entrypoint.sh' | awk '{print $1}')"
[ "$BASE_SUM" = "$OURS_SUM" ] || die "docker-entrypoint.sh отличается от базового — кто-то снова его правит"
docker run --rm --env-file .env --network remnawave-network "$IMG" \
  sh -c 'python -c "
import src.__main__ as entry
paths = entry.application().openapi()[\"paths\"]
assert any(p.startswith(\"/api/v1/admin/\") for p in paths), \"через src.__main__ поднялся НЕ кабинет\"
"' >/dev/null || die "src.__main__:application не отдаёт кабинет — правка точки входа не применилась"
ok "Скрипт запуска базовый, приложение наше"

# Ни один файл бота не должен отличаться от базового образа. Проверка по всему
# образу, а не по одним исходникам: раньше сборка правила и скрипт запуска, и
# переводы, и это было видно только глазами. Библиотеки в venv исключены сознательно —
# их мы обновляем ради безопасности, это отдельное решение.
info "Образ: файлы бота не изменены…"
docker run --rm --entrypoint sh "ghcr.io/snoups/remnashop:${TAG}" -c \
  'find /opt/remnashop -type f -not -path "*/.venv/*" -not -path "*/__pycache__/*" -not -name "*.pyc" -not -path "*/logs/*" -not -path "*/backups/*" -exec sha256sum {} \;' 2>/dev/null | sort -k2 > /tmp/_base_files.txt
docker run --rm --entrypoint sh "$IMG" -c \
  'find /opt/remnashop -type f -not -path "*/.venv/*" -not -path "*/__pycache__/*" -not -name "*.pyc" -not -path "*/logs/*" -not -path "*/backups/*" -exec sha256sum {} \;' 2>/dev/null | sort -k2 > /tmp/_ours_files.txt
CHANGED="$(join -j 2 /tmp/_base_files.txt /tmp/_ours_files.txt | awk '$2 != $3 {print $1}')"
if [ -n "$CHANGED" ]; then
  printf '%s\n' "$CHANGED" | sed 's/^/    /' >&2
  die "перечисленные файлы бота изменены сборкой — кабинет должен ставиться ПОВЕРХ, не трогая их"
fi
ok "Файлы бота совпадают с базовым образом"

info "Проверка alembic: должен быть РОВНО один head…"
HEADS="$(docker run --rm "$IMG" sh -c 'alembic -c src/infrastructure/database/alembic.ini heads 2>/dev/null' | grep -c '(head)')"
[ "$HEADS" = "1" ] || die "Ожидался один alembic head, найдено: ${HEADS} (конфликт миграций base/overlay)"
ok "alembic heads = 1"

info "Правки поведения бота (overlay_patches)…"
# Правка, которая не применилась, НЕ роняет интерпретатор (иначе сломались бы pip и
# alembic), поэтому её отсутствие надо ловить именно здесь — иначе бот уедет в прод
# на исходном поведении базы, и заметит это только владелец по симптомам.
# Приложение поднимаем ПЕРЕД проверкой: правки отложенные, каждая срабатывает в
# момент, когда бот импортирует её модуль. Без этого список был бы пуст всегда.
docker run --rm --env-file .env --network remnawave-network "$IMG" sh -c 'python -c "
import sys
import overlay_patches as op
import src.overlay_app as m
m.application()
for line in op.applied(): print(\"  \" + line)
bad = False
for name, why in op.failures():
    print(f\"НЕ ПРИМЕНИЛАСЬ: {name}: {why}\", file=sys.stderr); bad = True
for module in op.pending():
    print(f\"НЕ СРАБОТАЛА: никто не импортировал {module}\", file=sys.stderr); bad = True
sys.exit(1 if bad else 0)
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
