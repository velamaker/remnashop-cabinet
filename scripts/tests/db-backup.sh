#!/usr/bin/env bash
# Стенд scripts/db-backup.sh без базы и сети: копия бэкапа в закрытый Telegram-канал.
#
# docker и curl — подделки первыми в PATH: docker отдаёт «дамп», curl пишет свои
# аргументы и то, что пришло ему в stdin (туда уходит адрес с токеном), в журнал.
# Главное, что держим: в Telegram уходит только ЗАШИФРОВАННЫЙ дамп, токен не попадает
# в аргументы процесса, сбой отправки не роняет бэкап, настройки берутся из .env.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="$ROOT/scripts/db-backup.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
BIN="$TMP/bin"; mkdir -p "$BIN"
export HARNESS_LOG="$TMP/calls.log"

cat > "$BIN/docker" <<'SH'
#!/usr/bin/env bash
printf 'docker %s\n' "$*" >> "$HARNESS_LOG"
case "$*" in
  # «Дамп» заведомо больше MIN_BYTES после gzip: случайные байты не сжимаются.
  *pg_dump*) head -c 4096 /dev/urandom | base64 ;;
  # Проверка восстановления: «сколько строк в users».
  *-tAc*) echo 5 ;;
  # Образа бота нет — шаг миграции проверка восстановления пропускает.
  "image inspect"*) exit 1 ;;
esac
exit 0
SH
cat > "$BIN/curl" <<'SH'
#!/usr/bin/env bash
printf 'curl %s\n' "$*" >> "$HARNESS_LOG"
printf 'stdin %s\n' "$(cat)" >> "$HARNESS_LOG"
exit "${FAKE_CURL_RC:-0}"
SH
chmod +x "$BIN/docker" "$BIN/curl"
export PATH="$BIN:$PATH"

FAILS=0; PASSED=0; CASE=""
case_() { CASE="$1"; PASSED=$((PASSED + 1)); printf '▶ %s\n' "$1"; }
fail() { printf '  ✗ [%s] %s\n' "$CASE" "$*"; FAILS=$((FAILS + 1)); }
CLEAN_ENV=(-u BACKUP_PASSPHRASE -u BACKUP_PASSPHRASE_FILE -u BOT_TOKEN -u BOT_OWNER_ID
           -u BOT_PROXY_URL -u BACKUP_OFFSITE_TELEGRAM_CHAT_ID -u BACKUP_OFFSITE_RCLONE_REMOTE
           -u BACKUP_OFFSITE_RSYNC)
run() {
  : > "$HARNESS_LOG"
  rm -rf "$TMP/b"; mkdir -p "$TMP/b"
  env "${CLEAN_ENV[@]}" BACKUP_DIR="$TMP/b" ENV_FILE="$TMP/env" "$@" bash "$SCRIPT" > "$TMP/out" 2>&1
  RC=$?
}
# Проверка восстановления по тому, что оставил последний run: тот же .env, та же папка.
# Итог учения — во временный файл, а не в assets установки.
verify() {
  env "${CLEAN_ENV[@]}" BACKUP_DIR="$TMP/b" ENV_FILE="$TMP/env" DRILL_STATE="$TMP/drill.json" \
    IMAGE=нет-такого-образа "$@" bash "$ROOT/scripts/db-restore-verify.sh" > "$TMP/vout" 2>&1
  VRC=$?
}
: > "$TMP/env"

case_ "без отправки наружу — curl не зовётся"
run BACKUP_PASSPHRASE=secret
[ "$RC" = 0 ] || fail "код $RC"
grep -q '^curl' "$HARNESS_LOG" && fail "лишний curl"
ls "$TMP"/b/backup-*.sql.gz.enc >/dev/null 2>&1 || fail "нет зашифрованного бэкапа"

case_ "канал задан, дамп зашифрован — sendDocument, токен только в stdin"
run BACKUP_PASSPHRASE=secret BOT_TOKEN=123:SECRETTOKEN BACKUP_OFFSITE_TELEGRAM_CHAT_ID=-100777
[ "$RC" = 0 ] || fail "код $RC"
grep -q '^stdin url = "https://api.telegram.org/bot123:SECRETTOKEN/sendDocument"' "$HARNESS_LOG" || fail "адрес не через stdin"
grep '^curl' "$HARNESS_LOG" | grep -q 'SECRETTOKEN' && fail "токен в аргументах процесса"
grep '^curl' "$HARNESS_LOG" | grep -q 'chat_id=-100777' || fail "нет chat_id"
grep '^curl' "$HARNESS_LOG" | grep -qE 'document=@.*backup-.*\.sql\.gz\.enc' || fail "уходит не зашифрованный файл"
grep -q 'OK: offsite telegram' "$TMP/out" || fail "нет строки об успехе"

case_ "канал задан, но без шифрования — в Telegram не шлём"
run BOT_TOKEN=123:T BACKUP_OFFSITE_TELEGRAM_CHAT_ID=-100777
[ "$RC" = 0 ] || fail "код $RC"
grep -q '^curl' "$HARNESS_LOG" && fail "открытый дамп ушёл в Telegram"
grep -q 'дамп не зашифрован' "$TMP/out" || fail "нет предупреждения"

case_ "сбой отправки — бэкап цел, код 0, предупреждение"
run BACKUP_PASSPHRASE=secret BOT_TOKEN=123:T BACKUP_OFFSITE_TELEGRAM_CHAT_ID=-100777 FAKE_CURL_RC=22
[ "$RC" = 0 ] || fail "код $RC"
ls "$TMP"/b/backup-*.sql.gz.enc >/dev/null 2>&1 || fail "бэкап пропал"
grep -q 'offsite telegram не удался' "$TMP/out" || fail "нет предупреждения"

case_ "больше лимита бота — не шлём"
run BACKUP_PASSPHRASE=secret BOT_TOKEN=123:T BACKUP_OFFSITE_TELEGRAM_CHAT_ID=-100777 TG_MAX_BYTES=10
grep -q '^curl' "$HARNESS_LOG" && fail "отправили файл больше лимита"
grep -q 'больше лимита' "$TMP/out" || fail "нет предупреждения"

case_ "настройки из .env, если их нет в окружении (крон .env не читает)"
printf 'BOT_TOKEN="123:FROMENV"\nBACKUP_PASSPHRASE=fromenv\nBACKUP_OFFSITE_TELEGRAM_CHAT_ID=-100555\n' > "$TMP/env"
run
[ "$RC" = 0 ] || fail "код $RC"
grep -q 'bot123:FROMENV/sendDocument' "$HARNESS_LOG" || fail "токен из .env не подхвачен"
grep '^curl' "$HARNESS_LOG" | grep -q 'chat_id=-100555' || fail "канал из .env не подхвачен"
ls "$TMP"/b/backup-*.sql.gz.enc >/dev/null 2>&1 || fail "пароль из .env не подхвачен — бэкап не зашифрован"
: > "$TMP/env"

case_ "«owner» — владельцу бота из BOT_OWNER_ID (.env)"
printf 'BOT_TOKEN=123:T\nBOT_OWNER_ID=4242\nBACKUP_PASSPHRASE=p\nBACKUP_OFFSITE_TELEGRAM_CHAT_ID=owner\n' > "$TMP/env"
run
[ "$RC" = 0 ] || fail "код $RC"
grep '^curl' "$HARNESS_LOG" | grep -q 'chat_id=4242' || fail "не ушло владельцу"
: > "$TMP/env"

case_ "зашифрованный бэкап открывается тем же паролем"
run BACKUP_PASSPHRASE=secret
f="$(ls "$TMP"/b/backup-*.sql.gz.enc | head -1)"
BACKUP_PASSPHRASE=secret openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_PASSPHRASE -in "$f" | gunzip >/dev/null 2>&1 \
  || fail "не расшифровывается"

opens_with() {  # $1 — пароль: последний бэкап им открывается
  local f; f="$(ls "$TMP"/b/backup-*.sql.gz.enc 2>/dev/null | head -1)"
  [ -n "$f" ] && BACKUP_PASSPHRASE="$1" openssl enc -d -aes-256-cbc -pbkdf2 \
    -pass env:BACKUP_PASSPHRASE -in "$f" 2>/dev/null | gunzip >/dev/null 2>&1
}

case_ "пароль в одинарных кавычках в .env — бэкап и проверка восстановления читают одинаково"
printf "BACKUP_PASSPHRASE='s3cret word'\n" > "$TMP/env"
run
[ "$RC" = 0 ] || fail "бэкап: код $RC"
opens_with 's3cret word' || fail "кавычки попали в пароль бэкапа"
verify
[ "$VRC" = 0 ] || fail "проверка восстановления: код $VRC ($(tail -1 "$TMP/vout"))"
grep -q 'RESTORE-DRILL OK' "$TMP/vout" || fail "учение не прошло"
grep -q '"status":"ok"' "$TMP/drill.json" 2>/dev/null || fail "итог учения не записан"
: > "$TMP/env"

case_ "BACKUP_PASSPHRASE_FILE только в .env — тем же паролем расшифровывает и проверка"
printf 'из файла\n' > "$TMP/pass.txt"
printf 'BACKUP_PASSPHRASE_FILE="%s"\n' "$TMP/pass.txt" > "$TMP/env"
run
[ "$RC" = 0 ] || fail "бэкап: код $RC"
ls "$TMP"/b/backup-*.sql.gz.enc >/dev/null 2>&1 || fail "файл пароля из .env не подхвачен — бэкап не зашифрован"
opens_with 'из файла' || fail "бэкап зашифрован не паролем из файла"
verify
[ "$VRC" = 0 ] || fail "проверка восстановления: код $VRC ($(tail -1 "$TMP/vout"))"
grep -q 'RESTORE-DRILL OK' "$TMP/vout" || fail "учение не открыло бэкап паролем из файла"
: > "$TMP/env"

case_ "явный пароль важнее файла, явный файл важнее .env — одинаково в обоих скриптах"
printf 'из явного файла\n' > "$TMP/pass2.txt"
printf 'BACKUP_PASSPHRASE=из-env\n' > "$TMP/env"
run BACKUP_PASSPHRASE_FILE="$TMP/pass2.txt"
opens_with 'из явного файла' || fail "бэкап: пароль из .env перебил явный файл"
verify BACKUP_PASSPHRASE_FILE="$TMP/pass2.txt"
[ "$VRC" = 0 ] || fail "проверка: явный файл не важнее .env ($(tail -1 "$TMP/vout"))"
run BACKUP_PASSPHRASE=явный BACKUP_PASSPHRASE_FILE="$TMP/pass2.txt"
opens_with 'явный' || fail "бэкап: явный пароль не важнее явного файла"
verify BACKUP_PASSPHRASE=явный BACKUP_PASSPHRASE_FILE="$TMP/pass2.txt"
[ "$VRC" = 0 ] || fail "проверка: явный пароль не важнее явного файла"
: > "$TMP/env"

case_ "прокси с логином и паролем — не в аргументах curl, а в конфиге через stdin"
run BACKUP_PASSPHRASE=secret BOT_TOKEN=123:T BACKUP_OFFSITE_TELEGRAM_CHAT_ID=-100777 \
  BOT_PROXY_URL='socks5://user:PR0XYPASS@proxy.example:1080'
[ "$RC" = 0 ] || fail "код $RC"
grep '^curl' "$HARNESS_LOG" | grep -q 'PR0XYPASS' && fail "пароль прокси в аргументах процесса"
grep -q '^proxy = "socks5://user:PR0XYPASS@proxy.example:1080"' "$HARNESS_LOG" || fail "прокси не передан через stdin"
grep -q 'OK: offsite telegram' "$TMP/out" || fail "нет строки об успехе"

case_ "текстовые поля — --form-string: «@канал» не читается как файл"
run BACKUP_PASSPHRASE=secret BOT_TOKEN=123:T BACKUP_OFFSITE_TELEGRAM_CHAT_ID=@mychannel
grep '^curl' "$HARNESS_LOG" | grep -q -- '--form-string chat_id=@mychannel' || fail "chat_id не через --form-string"
grep '^curl' "$HARNESS_LOG" | grep -q -- '--form-string caption=' || fail "подпись не через --form-string"
grep '^curl' "$HARNESS_LOG" | grep -qE -- '-F document=@.*\.sql\.gz\.enc' || fail "файл должен уходить через -F"

case_ "«owner» без BOT_OWNER_ID — бэкап цел, предупреждение, без падения на set -u"
printf 'BOT_TOKEN=123:T\nBACKUP_PASSPHRASE=p\nBACKUP_OFFSITE_TELEGRAM_CHAT_ID=owner\n' > "$TMP/env"
run
[ "$RC" = 0 ] || fail "код $RC: $(tail -1 "$TMP/out")"
ls "$TMP"/b/backup-*.sql.gz.enc >/dev/null 2>&1 || fail "бэкап пропал"
grep -q 'нет BOT_OWNER_ID' "$TMP/out" || fail "нет предупреждения"
grep -q '^curl' "$HARNESS_LOG" && fail "отправка без адресата"
: > "$TMP/env"

echo
if [ "$FAILS" = 0 ]; then echo "✅ db-backup.sh: все сценарии прошли ($PASSED)"; exit 0; fi
echo "❌ db-backup.sh: провалов $FAILS (сценариев $PASSED)"; exit 1
