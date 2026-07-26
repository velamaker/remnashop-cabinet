#!/usr/bin/env bash
# Статический анализ проекта — гоняй после любых правок (или он сам сработает в pre-commit).
#
#   Кабинет (TS/React):  tsc -b (типы) + eslint
#   Бэкенд (admin_src):  проверка синтаксиса python (py_compile, без установки зависимостей)
#
# Использование:
#   scripts/check.sh            # проверить ВЕСЬ кабинет + бэкенд (типы+линт)
#   scripts/check.sh --staged   # быстро: только staged-файлы (так зовёт pre-commit)
#
# Пропустить (не рекомендуется): SKIP_CHECKS=1 в окружении.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

[ "${SKIP_CHECKS:-}" = "1" ] && { echo "⏭  статанализ пропущен (SKIP_CHECKS=1)"; exit 0; }

STAGED_ONLY=0
[ "${1:-}" = "--staged" ] && STAGED_ONLY=1
fail=0

if [ "$STAGED_ONLY" = 1 ]; then
  CH="$(git diff --cached --name-only --diff-filter=ACM)"
else
  CH=""
fi

need_cabinet() { [ "$STAGED_ONLY" = 0 ] || printf '%s\n' "$CH" | grep -qE '^cabinet/'; }
need_backend() { [ "$STAGED_ONLY" = 0 ] || printf '%s\n' "$CH" | grep -qE '^admin_src/.*\.py$'; }

# ---------- Кабинет: типы + линт ----------
if need_cabinet; then
  if command -v npm >/dev/null 2>&1; then
    echo "▶ cabinet: tsc (типы)…"
    ( cd cabinet && npx --no-install tsc -b ) || fail=1
    if [ "$STAGED_ONLY" = 1 ]; then
      # eslint только по изменённым .ts/.tsx — быстро
      files="$(printf '%s\n' "$CH" | grep -E '^cabinet/.*\.(ts|tsx)$' | sed 's#^cabinet/##' || true)"
      if [ -n "$files" ]; then
        echo "▶ cabinet: eslint (изменённые файлы)…"
        ( cd cabinet && printf '%s\n' "$files" | xargs npx --no-install eslint ) || fail=1
      fi
    else
      echo "▶ cabinet: eslint (весь проект)…"
      ( cd cabinet && npx --no-install eslint . ) || fail=1
    fi
    echo "▶ cabinet: vitest…"
    ( cd cabinet && npm run -s test ) || fail=1
  else
    echo "⚠ npm не найден — пропускаю проверку кабинета"
  fi
fi

# ---------- Бэкенд: синтаксис python ----------
if need_backend; then
  PY="$(command -v python3 || true)"
  if [ -n "$PY" ]; then
    echo "▶ backend: python syntax (py_compile)…"
    if [ "$STAGED_ONLY" = 1 ]; then
      files="$(printf '%s\n' "$CH" | grep -E '^admin_src/.*\.py$' || true)"
    else
      files="$(find admin_src/src -name '*.py' 2>/dev/null)"
    fi
    [ -n "$files" ] && { printf '%s\n' "$files" | xargs -r "$PY" -m py_compile || fail=1; }
  else
    echo "⚠ python3 не найден — пропускаю проверку бэкенда"
  fi
fi

if [ "$fail" = 0 ]; then
  echo "✅ статанализ OK"
else
  echo "❌ статанализ нашёл проблемы (см. выше)" >&2
fi
exit $fail
