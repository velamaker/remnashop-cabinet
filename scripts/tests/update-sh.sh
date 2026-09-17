#!/usr/bin/env bash
# Стенд update.sh без docker: «обновить и бота или только кабинет», флаги, типы установки.
#
# ЗАЧЕМ. Настоящий update.sh в CI не запустить — он пересобирает боевые контейнеры, — а
# ошибки в нём дорогие: «только кабинет», который всё-таки пересоздал бота; вопрос, на
# котором молча повис cron; перезапуск тарбол-ветки, потерявший выбор. Здесь docker, git
# и curl — подделки первыми в PATH: пишут свои аргументы в журнал и отвечают по
# сценарию, а стенд сверяет журнал и вывод. Установки — временные каталоги.
#
# Запуск (из любого места):
#   bash scripts/tests/update-sh.sh
#   UPDATE_SH=/путь/к/другому/update.sh GOLDEN_ONLY=1 bash scripts/tests/update-sh.sh
#     — прогнать только «золотые» сценарии полного обновления на другой копии скрипта.
#       Так золотой журнал и снят: с update.sh до появления вопроса.
# Нужны: bash, script (util-linux), tar, gzip, sed, grep, timeout.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="${UPDATE_SH:-$ROOT/update.sh}"
MANIFEST_TS="$ROOT/cabinet/src/lib/botCapabilities.ts"
CAPS_PY="$ROOT/admin_src/src/web/cabinet_capabilities.py"
for f in "$SCRIPT" "$MANIFEST_TS" "$CAPS_PY"; do
  [ -f "$f" ] || { echo "Нет файла: $f" >&2; exit 2; }
done
for tool in script tar gzip timeout; do
  command -v "$tool" >/dev/null 2>&1 || { echo "Нужна утилита: $tool" >&2; exit 2; }
done

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
BIN="$TMP/bin"
mkdir -p "$BIN"
CALLS="$TMP/calls.log"
OUT="$TMP/out.txt"
export HARNESS_CALLS="$CALLS" HARNESS_TMP="$TMP" HARNESS_CAPS_PY="$CAPS_PY"

# ── подделки ──────────────────────────────────────────────────────────────────
# docker: FAKE_DB=1 — база запущена; FAKE_ADAPTER=1 — есть контейнер адаптера;
# FAKE_BOT: none — контейнера бота нет; old — образ без списка возможностей (1.3.8);
# new — образ с нынешним списком; partial — без одного токена и одного «только бот».
cat > "$BIN/docker" <<'EOF'
#!/usr/bin/env bash
printf 'docker %s\n' "$*" >> "$HARNESS_CALLS"
case "$1 ${2:-}" in
  "compose version") exit 0 ;;
  "ps --format") [ "${FAKE_DB:-1}" = 1 ] && echo remnashop-db; exit 0 ;;
  "ps -a") [ "${FAKE_ADAPTER:-0}" = 1 ] && echo remnashop-cabinet-adapter; exit 0 ;;
  "exec remnashop-db") echo "-- fake dump"; exit 0 ;;
  "inspect -f") [ "${FAKE_BOT:-old}" = none ] && exit 1; echo "sha256:fakebotimage"; exit 0 ;;
esac
if [ "$1" = run ]; then
  case "$*" in
    *"--entrypoint sh sha256:fakebotimage"*)
      printf '1.3.8\n@@\n'
      case "${FAKE_BOT:-old}" in
        new) cat "$HARNESS_CAPS_PY" ;;
        partial) grep -v -e '^    "ad_link_url",$' -e '^    "device_webhooks_3x": ' "$HARNESS_CAPS_PY" ;;
      esac
      exit 0 ;;
    *--volumes-from*)
      # Бэкап тома адаптера: кладём непустой файл туда, куда смонтирован /backup.
      host=""; name=""; prev=""
      for a in "$@"; do
        if [ "$prev" = -v ]; then case "$a" in *:/backup) host="${a%:/backup}" ;; esac; fi
        case "$a" in /backup/*) name="${a#/backup/}" ;; esac
        prev="$a"
      done
      [ -n "$host" ] && [ -n "$name" ] && echo fake > "$host/$name"
      exit 0 ;;
  esac
fi
exit 0
EOF
cat > "$BIN/git" <<'EOF'
#!/usr/bin/env bash
printf 'git %s\n' "$*" >> "$HARNESS_CALLS"
case "$*" in
  "rev-parse --abbrev-ref HEAD") echo main ;;
  "rev-parse --short HEAD") echo abc1234 ;;
esac
exit 0
EOF
cat > "$BIN/curl" <<'EOF'
#!/usr/bin/env bash
printf 'curl %s\n' "$*" >> "$HARNESS_CALLS"
case "$*" in
  *archive/refs/heads/*) [ -f "$HARNESS_TMP/archive.tar.gz" ] && exec cat "$HARNESS_TMP/archive.tar.gz" ;;
esac
exit 22
EOF
chmod +x "$BIN/docker" "$BIN/git" "$BIN/curl"

export PATH="$BIN:$PATH"
# Предохранители: подделки обязаны стоять первыми, а проскочивший настоящий docker —
# никуда не подключиться; дампы и снимки ассетов — только во временный каталог.
for tool in docker git curl; do
  [ "$(command -v "$tool")" = "$BIN/$tool" ] || { echo "Подделка $tool не первая в PATH" >&2; exit 99; }
done
export DOCKER_HOST="unix://$TMP/no-docker.sock"
export BACKUP_DIR="$TMP/backups"
unset _SELF_UPDATED COMPOSE_FILE

# ── установки ─────────────────────────────────────────────────────────────────
# make_inst <имя> <bot|bedolaga|site|api> <git|tarball>; печатает каталог.
make_inst() {
  local d="$TMP/inst-$1"
  rm -rf "$d"
  mkdir -p "$d/cabinet/src/lib" "$d/admin_src/src/web"
  cp "$SCRIPT" "$d/update.sh"
  cp "$MANIFEST_TS" "$d/cabinet/src/lib/botCapabilities.ts"
  cp "$CAPS_PY" "$d/admin_src/src/web/cabinet_capabilities.py"
  printf 'services: {}\n# image tag: ${BASE_TAG:-v0.0.1}\n' > "$d/docker-compose.yml"
  : > "$d/cabinet/docker-compose.cabinet.yml"
  case "$2" in
    bot) printf 'BOT_TOKEN=123456:fake\nCOMPOSE_FILE=docker-compose.yml:cabinet/docker-compose.cabinet.yml\n' ;;
    bedolaga) printf 'BOT_TOKEN=123456:fake\nCABINET_BACKEND=bedolaga\nAPI_UPSTREAM=remnashop-cabinet-adapter:8090\nCOMPOSE_FILE=docker-compose.yml:cabinet/docker-compose.cabinet.yml:cabinet/docker-compose.adapter.yml\n' ;;
    site) printf 'API_UPSTREAM=bot.example.test:443\nAPI_SCHEME=https\nCABINET_PORT=8080\n' ;;
    api) printf 'BOT_TOKEN=123456:fake\nCOMPOSE_FILE=docker-compose.yml\n' ;;
  esac > "$d/.env"
  [ "$3" = git ] && mkdir "$d/.git"
  case "$(readlink -f "$d")" in "$ROOT"|"$ROOT"/*) echo "Стенд оказался внутри репозитория" >&2; exit 99 ;; esac
  printf '%s\n' "$d"
}

# Архив «свежего кода» для тарбол-установки: update.sh в нём — обёртка, которая
# записывает, с какими аргументами её перезапустили, и передаёт управление копии
# проверяемого скрипта.
make_archive() {
  local pkg="$TMP/pkg/remnashop-cabinet-main"
  rm -rf "$TMP/pkg" "$TMP/archive.tar.gz"
  mkdir -p "$pkg/cabinet/src/lib" "$pkg/admin_src/src/web"
  cp "$SCRIPT" "$pkg/update.real.sh"
  cat > "$pkg/update.sh" <<'EOF'
#!/usr/bin/env bash
printf 'reexec %s _SELF_UPDATED=%s\n' "$*" "${_SELF_UPDATED:-}" >> "$HARNESS_CALLS"
exec bash "$(dirname "$(readlink -f "$0")")/update.real.sh" "$@"
EOF
  cp "$MANIFEST_TS" "$pkg/cabinet/src/lib/botCapabilities.ts"
  cp "$CAPS_PY" "$pkg/admin_src/src/web/cabinet_capabilities.py"
  tar czf "$TMP/archive.tar.gz" -C "$TMP/pkg" remnashop-cabinet-main
}

# ── запуск и проверки ─────────────────────────────────────────────────────────
RC=0
# run_tty <каталог> <ввод, формат printf> [аргументы…] — stdin и stdout на псевдо-терминале.
run_tty() {
  local d="$1" input="$2" cmd="bash ./update.sh" a
  shift 2
  for a in "$@"; do cmd+=" $(printf '%q' "$a")"; done
  : > "$CALLS"
  # shellcheck disable=SC2059 # ввод сценария — формат printf намеренно
  ( cd "$d" && printf "$input" | timeout 60 script -qec "$cmd" /dev/null ) > "$OUT" 2>&1
  RC=$?
}
# run_notty <каталог> [аргументы…] — как из cron: stdin не терминал.
run_notty() {
  local d="$1"
  shift
  : > "$CALLS"
  ( cd "$d" && timeout 60 bash ./update.sh "$@" </dev/null ) > "$OUT" 2>&1
  RC=$?
}

FAILS=0
CASE=""
PASSED=0
case_() { CASE="$1"; PASSED=$((PASSED + 1)); printf '▶ %s\n' "$1"; }
fail() {
  printf '  ✗ [%s] %s\n' "$CASE" "$*"
  FAILS=$((FAILS + 1))
  if [ "${VERBOSE:-0}" = 1 ]; then
    echo "  ── журнал:"; sed 's/^/    /' "$CALLS"
    echo "  ── вывод:"; sed 's/^/    /' "$OUT"
  fi
}
rc_is()     { [ "$RC" = "$1" ] || fail "код выхода $RC, ждали $1"; }
rc_not0()   { [ "$RC" != 0 ] || fail "код выхода 0, ждали ошибку"; }
has_call()  { grep -qF -- "$1" "$CALLS" || fail "нет вызова: $1"; }
no_call()   { ! grep -qF -- "$1" "$CALLS" || fail "лишний вызов: $1"; }
no_calls()  { [ ! -s "$CALLS" ] || fail "ждали пустой журнал, а там: $(tr '\n' ';' < "$CALLS")"; }
out_has()   { grep -qF -- "$1" "$OUT" || fail "в выводе нет: $1"; }
out_lacks() { ! grep -qF -- "$1" "$OUT" || fail "в выводе лишнее: $1"; }
count_out() { grep -cF -- "$1" "$OUT"; }
# Журнал без пробы работающего бота (docker inspect/run только читают его образ).
calls_wo_probe() {
  grep -vE '^docker (inspect -f \{\{\.Image\}\} remnashop|run --rm --network none --entrypoint sh )' "$CALLS"
}
no_probe() { ! grep -qE '^docker (inspect|run --rm --network none)' "$CALLS" || fail "лишняя проба образа бота"; }

# «Только кабинет»: ни дампа, ни одного up/restart/stop/down мимо кабинета (и адаптера).
cabinet_only_calls() {
  local want="$1" line
  no_call "pg_dump"
  no_call "api.github.com"
  grep -qE "^docker compose .* up -d --build --no-deps ${want}\$" "$CALLS" || fail "нет up -d --build --no-deps ${want}"
  while IFS= read -r line; do
    case "$line" in
      *" up -d --build --no-deps ${want}") ;;
      *) fail "up не только кабинета: $line" ;;
    esac
  done < <(grep -E '^docker compose .* up( |$)' "$CALLS")
  if grep -qE '^docker (compose .* )?(down|stop|restart|rm|kill|pull)( |$)' "$CALLS"; then
    fail "остановка/перезапуск чего-то: $(grep -E '^docker (compose .* )?(down|stop|restart|rm|kill|pull)( |$)' "$CALLS" | head -1)"
  fi
  grep -qE "^docker compose .* logs -f --tail=30 ${want}\$" "$CALLS" || fail "логи не только кабинета"
}

COMPOSE_BOT="docker compose -f docker-compose.yml -f cabinet/docker-compose.cabinet.yml"
# Золотой журнал полного обновления git-установки — снят с update.sh ДО вопроса
# (UPDATE_SH=<старая копия> GOLDEN_ONLY=1). Полное обновление обязано остаться байт в байт.
GOLDEN_GIT_FULL="docker compose version
docker ps --format {{.Names}}
docker exec remnashop-db sh -c pg_dump -U \"\$POSTGRES_USER\" \"\$POSTGRES_DB\"
git rev-parse --abbrev-ref HEAD
git pull --ff-only
git rev-parse --short HEAD
curl -fsSL --max-time 8 https://api.github.com/repos/snoups/remnashop/releases/latest
$COMPOSE_BOT up -d --build
$COMPOSE_BOT logs -f --tail=30"
golden_full() {
  local got
  got="$(calls_wo_probe)"
  [ "$got" = "$GOLDEN_GIT_FULL" ] || fail "журнал полного обновления разошёлся с золотым:
$(diff <(printf '%s\n' "$GOLDEN_GIT_FULL") <(printf '%s\n' "$got") | sed 's/^/      /')"
}

QUESTION="Обновить и бота? [Д/н]"
N_CAB="$(grep -cE '^  [a-z0-9_]+: \{ since: "[^"]*", label: "[^"]*" \},$' "$MANIFEST_TS")"
N_BOTONLY="$(grep -cE '^    "[a-z0-9_]+": "[^"]*",$' "$CAPS_PY")"
[ "$N_CAB" -gt 0 ] && [ "$N_BOTONLY" -gt 0 ] || { echo "Манифест не прочитан" >&2; exit 2; }

# ── A, C: полное обновление, как было всегда (и для старой копии скрипта) ────
export FAKE_DB=1 FAKE_ADAPTER=0 FAKE_BOT=old

case_ "A: терминал, Enter — всё как раньше"
D="$(make_inst a bot git)"
run_tty "$D" '\n'
rc_is 0; golden_full

case_ "C: без терминала — всё как раньше, без вопроса"
D="$(make_inst c bot git)"
run_notty "$D"
rc_is 0; golden_full; out_lacks "$QUESTION"

if [ "${GOLDEN_ONLY:-0}" = 1 ]; then
  echo
  [ "$FAILS" = 0 ] && { echo "✅ золотые сценарии совпали ($PASSED)"; exit 0; }
  echo "❌ провалов: $FAILS"; exit 1
fi

out_has "Запуск без терминала"; no_probe

case_ "A': вопрос показан, с версией бота и тем, что уже скрыто"
D="$(make_inst a2 bot git)"
run_tty "$D" '\n'
rc_is 0; out_has "$QUESTION"; out_has "Сейчас работает бот версии 1.3.8"
out_has "Обновляю бота и кабинет."
[ "$(count_out "$QUESTION")" = 1 ] || fail "вопрос не один раз"

# ── B, D, E: только кабинет / флаги ───────────────────────────────────────────
case_ "B: терминал, «н» — только кабинет, итог со списком скрытого"
D="$(make_inst b bot git)"
run_tty "$D" 'н\n'
rc_is 0; cabinet_only_calls cabinet
has_call "git pull --ff-only"
out_has "Обновляю только кабинет"; out_has "Бэкап базы не нужен"
out_has "Бот работает на версии 1.3.8. До его обновления в кабинете не будет:"
out_has "Скидка на продление до окончания подписки"
out_has "В самом боте не установлено:"
out_has "./update.sh --with-bot"
[ "$(count_out "    • ")" -ge $((N_CAB * 2 + N_BOTONLY)) ] || fail "в вопросе и итоге не все пункты"

case_ "D: --cabinet-only без терминала — без вопроса, бот не тронут"
D="$(make_inst d bot git)"
run_notty "$D" --cabinet-only
rc_is 0; cabinet_only_calls cabinet; out_lacks "$QUESTION"; out_lacks "Запуск без терминала"
[ "$(count_out "    • ")" = $((N_CAB + N_BOTONLY)) ] || fail "в итоге не $N_CAB+$N_BOTONLY пунктов: $(count_out "    • ")"

case_ "E: --with-bot в терминале — без вопроса и без пробы, как раньше"
D="$(make_inst e bot git)"
run_tty "$D" '' --with-bot
rc_is 0; golden_full; out_lacks "$QUESTION"; no_probe

case_ "E': --force --cabinet-only — синк через reset, собран только кабинет"
D="$(make_inst e2 bot git)"
run_notty "$D" --force --cabinet-only
rc_is 0; has_call "git fetch origin"; has_call "git reset --hard origin/main"; cabinet_only_calls cabinet

case_ "E'': --no-backup --cabinet-only"
D="$(make_inst e3 bot git)"
run_notty "$D" --no-backup --cabinet-only
rc_is 0; cabinet_only_calls cabinet; out_has "Бэкап пропущен (--no-backup)"

# ── F: несовместимые флаги — стоп до любого вызова ───────────────────────────
for args in "--base v0.8.3 --cabinet-only" "--cabinet-only --base v0.8.3" "--with-bot --cabinet-only" "--cabinet-only --with-bot" "--cabinet-only --what"; do
  case_ "F: $args — стоп, журнал пуст"
  D="$(make_inst f bot git)"
  # shellcheck disable=SC2086 # аргументы сценария разбиваются намеренно
  run_notty "$D" $args
  rc_not0; no_calls
done

# ── G, H: тарбол-установка и перезапуск ──────────────────────────────────────
case_ "G: тарбол, терминал, «н» — вопрос один раз, перезапуск получил выбор"
make_archive
D="$(make_inst g bot tarball)"
run_tty "$D" 'н\n'
rc_is 0
[ "$(count_out "$QUESTION")" = 1 ] || fail "вопрос задан $(count_out "$QUESTION") раз"
has_call "reexec --no-backup --cabinet-only _SELF_UPDATED=1"
cabinet_only_calls cabinet

case_ "G': тарбол, без терминала, --cabinet-only — выбор пережил перезапуск"
make_archive
D="$(make_inst g2 bot tarball)"
run_notty "$D" --cabinet-only
rc_is 0; has_call "reexec --no-backup --cabinet-only _SELF_UPDATED=1"; cabinet_only_calls cabinet

case_ "G'': тарбол, терминал, Enter — перезапуск с --with-bot, полное обновление"
make_archive
D="$(make_inst g3 bot tarball)"
run_tty "$D" '\n'
rc_is 0; has_call "reexec --no-backup --with-bot _SELF_UPDATED=1"
has_call "pg_dump"; has_call "$COMPOSE_BOT up -d --build"
[ "$(count_out "$QUESTION")" = 1 ] || fail "вопрос задан $(count_out "$QUESTION") раз"

case_ "H: старый скрипт перезапустил новый без флага — новый спрашивает"
D="$(make_inst h bot tarball)"
export _SELF_UPDATED=1
run_tty "$D" 'н\n' --no-backup
unset _SELF_UPDATED
rc_is 0; out_has "$QUESTION"; cabinet_only_calls cabinet; no_call "curl -fL"

# ── I: поверх «Бедолаги» ──────────────────────────────────────────────────────
case_ "I: «Бедолага», «н» — кабинет и адаптер, бэкап тома адаптера, без дампа"
D="$(make_inst i bedolaga git)"
FAKE_ADAPTER=1 run_tty "$D" 'н\n'
rc_is 0; cabinet_only_calls "cabinet cabinet-adapter"
has_call "docker run --rm --volumes-from remnashop-cabinet-adapter"
out_has "адаптер обновится вместе"; out_has "Обновлены кабинет и адаптер «Бедолаги»"
no_probe
ls "$BACKUP_DIR"/adapter-state-*.tar.gz >/dev/null 2>&1 || fail "нет бэкапа тома адаптера"

case_ "I': «Бедолага», Enter — всё, включая дамп нашей базы"
D="$(make_inst i2 bedolaga git)"
FAKE_ADAPTER=1 run_tty "$D" '\n'
rc_is 0; has_call "pg_dump"
has_call "docker compose -f docker-compose.yml -f cabinet/docker-compose.cabinet.yml -f cabinet/docker-compose.adapter.yml up -d --build"
no_call "--no-deps"

# ── J: сервер кабинета ────────────────────────────────────────────────────────
for mode in tty notty flag; do
  case_ "J: сервер кабинета (site, $mode) — стоп с командой site-install, ни одного вызова"
  D="$(make_inst j site git)"
  case "$mode" in
    tty) run_tty "$D" 'н\n' ;;
    notty) run_notty "$D" ;;
    flag) run_notty "$D" --cabinet-only ;;
  esac
  rc_not0; no_calls; out_has "site-install.sh"; out_has "режим site"; out_lacks "$QUESTION"
done

# ── K: бот без кабинета ───────────────────────────────────────────────────────
case_ "K: api, терминал — без вопроса, обновляется бот"
D="$(make_inst k api git)"
run_tty "$D" ''
rc_is 0; out_lacks "$QUESTION"; has_call "pg_dump"
has_call "docker compose -f docker-compose.yml up -d --build"; no_call "--no-deps"; no_probe

case_ "K': api, --cabinet-only — стоп, журнал пуст"
D="$(make_inst k2 api git)"
run_notty "$D" --cabinet-only
rc_not0; no_calls; out_has "режим api"

# ── L: непонятный ответ и EOF ─────────────────────────────────────────────────
case_ "L: трижды мусор — стоп с подсказкой флагов, ничего не собрано"
D="$(make_inst l bot git)"
run_tty "$D" 'может\nда нет\n?\n'
rc_not0; out_has "Не понял ответ"; out_has "--cabinet-only"; no_call " up "; no_call "pg_dump"
[ "$(count_out "Ответьте «д»")" = 2 ] || fail "переспросили не дважды"

case_ "L': EOF (Ctrl+D) — не «да», стоп"
D="$(make_inst l2 bot git)"
run_tty "$D" ''
rc_not0; out_has "Ответа нет"; no_call " up "; no_call "pg_dump"

case_ "L'': мусор, потом «н» — только кабинет"
D="$(make_inst l3 bot git)"
run_tty "$D" 'ага\nн\n'
rc_is 0; cabinet_only_calls cabinet

# Варианты ответа — под локалью C тоже: кириллица сравнивается байтами, без ${a,,}.
for ans in "д" "Д" "да" "ДА" "y" "YES" " да " ; do
  case_ "ответ «$ans» (LC_ALL=C) — всё"
  D="$(make_inst yes bot git)"
  LC_ALL=C run_tty "$D" "$ans\\n"
  rc_is 0; has_call "$COMPOSE_BOT up -d --build"; no_call "--no-deps"
done
for ans in "н" "Н" "нет" "Нет" "n" "NO" " н " "н"$'\r'; do
  case_ "ответ «$(printf '%s' "$ans" | tr -d '\r')» (LC_ALL=C) — только кабинет"
  D="$(make_inst no bot git)"
  LC_ALL=C run_tty "$D" "$ans\\n"
  rc_is 0; cabinet_only_calls cabinet
done

# ── M: что называет итог ──────────────────────────────────────────────────────
case_ "M: бот без одного токена и одного «только бот» — ровно по пункту"
D="$(make_inst m bot git)"
FAKE_BOT=partial run_notty "$D" --cabinet-only
rc_is 0
[ "$(count_out "    • ")" = 2 ] || fail "пунктов $(count_out "    • "), ждали 2"
out_has "Готовая ссылка в окне новой рекламной ссылки"
out_has "Уведомления о новых и удалённых устройствах"
out_lacks "Скидка на продление до окончания подписки"

case_ "M': бот умеет всё — «умеет всё», ни одного пункта"
D="$(make_inst m2 bot git)"
FAKE_BOT=new run_notty "$D" --cabinet-only
rc_is 0; out_has "умеет всё, что нужно этой версии кабинета"
[ "$(count_out "    • ")" = 0 ] || fail "лишние пункты"

case_ "M'': контейнера бота нет — честно «не удалось узнать»"
D="$(make_inst m3 bot git)"
FAKE_BOT=none run_tty "$D" 'н\n'
rc_is 0; out_has "Бот сейчас не запущен"; out_has "Версию работающего бота узнать не удалось"
cabinet_only_calls cabinet

echo
if [ "$FAILS" = 0 ]; then
  echo "✅ update.sh: все сценарии прошли ($PASSED)"
  exit 0
fi
echo "❌ update.sh: провалов $FAILS (сценариев $PASSED). Подробно: VERBOSE=1 bash $0"
exit 1
