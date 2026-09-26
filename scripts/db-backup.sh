#!/bin/bash
# Ежедневный бэкап БД remnashop → /opt/remnashop-backups/backup-DATE.sql.gz
# Формат имени совпадает с update.sh (predeploy) и мониторингом backup_monitor.py.
# Атомарная запись (.part → rename), sanity-проверка размера, ротация по дням.
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/opt/remnashop-backups}"
DB_CONTAINER="${DB_CONTAINER:-remnashop-db}"
DB_USER="${DB_USER:-remnashop}"
DB_NAME="${DB_NAME:-remnashop}"
RETAIN_DAYS="${RETAIN_DAYS:-14}"
MIN_BYTES="${MIN_BYTES:-1024}"
# Шифрование покоя: задайте BACKUP_PASSPHRASE (или файл в BACKUP_PASSPHRASE_FILE) —
# дамп шифруется openssl AES-256 (pbkdf2). Дамп содержит хэши паролей/платёжные данные,
# поэтому на внешнем хранилище держать его в открытом виде нельзя. Пусто → как раньше.
#
# Настройки берём из окружения, а чего там нет — из .env установки: крон этот файл не
# читает, и без этого включённое в .env шифрование или отправка наружу молча не работали
# бы. Только перечисленные ключи и только если они не заданы явно.
ENV_FILE="${ENV_FILE:-$(dirname "$(readlink -f "$0")")/../.env}"
env_default() {
    local key="$1" val
    [ -n "${!key:-}" ] && return 0
    [ -r "$ENV_FILE" ] || return 0
    val="$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")" || true
    [ -n "$val" ] && printf -v "$key" '%s' "$val"
    return 0
}
for _k in BACKUP_PASSPHRASE BACKUP_PASSPHRASE_FILE BACKUP_OFFSITE_RCLONE_REMOTE \
          BACKUP_OFFSITE_RSYNC BACKUP_OFFSITE_TELEGRAM_CHAT_ID BOT_TOKEN BOT_PROXY_URL; do
    env_default "$_k"
done
BACKUP_PASSPHRASE="${BACKUP_PASSPHRASE:-}"
if [ -z "$BACKUP_PASSPHRASE" ] && [ -n "${BACKUP_PASSPHRASE_FILE:-}" ] && [ -f "${BACKUP_PASSPHRASE_FILE}" ]; then
    BACKUP_PASSPHRASE="$(cat "$BACKUP_PASSPHRASE_FILE")"
fi
# openssl читает пароль из ОКРУЖЕНИЯ процесса (-pass env:…). Прочитанный из файла или
# .env пароль был обычной переменной оболочки — openssl его не видел, и зашифрованный
# бэкап падал целиком. Экспортируем только его: токен бота дочерним процессам не нужен.
export BACKUP_PASSPHRASE

mkdir -p "$BACKUP_DIR"
TS="$(date +%F-%H%M%S)"
if [ -n "$BACKUP_PASSPHRASE" ]; then
    OUT="$BACKUP_DIR/backup-${TS}.sql.gz.enc"
else
    OUT="$BACKUP_DIR/backup-${TS}.sql.gz"
fi
TMP="${OUT}.part"

cleanup() { rm -f "$TMP"; }
trap cleanup EXIT

# pipefail гарантирует: если pg_dump упал — весь конвейер считается упавшим.
if [ -n "$BACKUP_PASSPHRASE" ]; then
    if ! docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" | gzip -9 \
        | openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:BACKUP_PASSPHRASE > "$TMP"; then
        echo "$(date -Is) FAIL: pg_dump/gzip/encrypt не отработал" >&2
        exit 1
    fi
else
    if ! docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" | gzip -9 > "$TMP"; then
        echo "$(date -Is) FAIL: pg_dump/gzip не отработал" >&2
        exit 1
    fi
fi

SIZE="$(stat -c%s "$TMP")"
if [ "$SIZE" -lt "$MIN_BYTES" ]; then
    echo "$(date -Is) FAIL: дамп подозрительно мал (${SIZE} B < ${MIN_BYTES})" >&2
    exit 1
fi

mv "$TMP" "$OUT"
trap - EXIT
# SHA-256-сайдкар — проверка целостности при восстановлении/переносе offsite.
sha256sum "$OUT" | awk '{print $1}' > "${OUT}.sha256" 2>/dev/null || true
echo "$(date -Is) OK: $OUT (${SIZE} B)$([ -n "$BACKUP_PASSPHRASE" ] && echo ' [encrypted]')"

# Offsite-копия (best-effort, не валит бэкап). Задайте ОДНО из:
#   BACKUP_OFFSITE_RCLONE_REMOTE="remote:bucket/path"   (нужен rclone + его конфиг)
#   BACKUP_OFFSITE_RSYNC="user@host:/path"              (нужен rsync + ssh-ключ)
#   BACKUP_OFFSITE_TELEGRAM_CHAT_ID="-100…"             (закрытый канал; бот — админ)
# Рекомендуется гнать ШИФРОВАННЫЕ бэкапы (BACKUP_PASSPHRASE), т.к. уходят наружу.
# Telegram — независимо от двух первых: ничего заводить не нужно, лимит файла у бота
# 50 МБ, а дамп небольшого магазина весит сотни килобайт. Туда уходит ТОЛЬКО
# зашифрованный дамп: в открытом виде в нём хэши паролей и платежи, а чат Telegram —
# чужой сервер. Токен передаём curl через stdin, чтобы он не светился в списке процессов.
TG_MAX_BYTES="${TG_MAX_BYTES:-49000000}"
if [ -n "${BACKUP_OFFSITE_TELEGRAM_CHAT_ID:-}" ]; then
    if [ -z "$BACKUP_PASSPHRASE" ]; then
        echo "$(date -Is) WARN: offsite telegram пропущен — дамп не зашифрован (задайте BACKUP_PASSPHRASE)" >&2
    elif [ -z "${BOT_TOKEN:-}" ]; then
        echo "$(date -Is) WARN: offsite telegram пропущен — нет BOT_TOKEN" >&2
    elif [ "$SIZE" -gt "$TG_MAX_BYTES" ]; then
        echo "$(date -Is) WARN: offsite telegram пропущен — дамп ${SIZE} B больше лимита бота" >&2
    else
        SUM="$(cat "${OUT}.sha256" 2>/dev/null || true)"
        CAPTION="Бэкап базы $(date +%F\ %H:%M), ${SIZE} B, sha256 ${SUM:0:16}…"
        PROXY_ARGS=()
        [ -n "${BOT_PROXY_URL:-}" ] && PROXY_ARGS=(--proxy "$BOT_PROXY_URL")
        if printf 'url = "https://api.telegram.org/bot%s/sendDocument"\n' "$BOT_TOKEN" \
            | curl -fsS --max-time 120 "${PROXY_ARGS[@]}" -K - \
                -F "chat_id=${BACKUP_OFFSITE_TELEGRAM_CHAT_ID}" \
                -F "caption=${CAPTION}" \
                -F "document=@${OUT}" >/dev/null 2>&1; then
            echo "$(date -Is) OK: offsite telegram → ${BACKUP_OFFSITE_TELEGRAM_CHAT_ID}"
        else
            echo "$(date -Is) WARN: offsite telegram не удался (бот админ в канале? id верный?)" >&2
        fi
    fi
fi
if [ -n "${BACKUP_OFFSITE_RCLONE_REMOTE:-}" ] && command -v rclone >/dev/null 2>&1; then
    if rclone copy "$OUT" "$BACKUP_OFFSITE_RCLONE_REMOTE" >/dev/null 2>&1 \
        && { [ ! -f "${OUT}.sha256" ] || rclone copy "${OUT}.sha256" "$BACKUP_OFFSITE_RCLONE_REMOTE" >/dev/null 2>&1; }; then
        echo "$(date -Is) OK: offsite rclone → $BACKUP_OFFSITE_RCLONE_REMOTE"
    else
        echo "$(date -Is) WARN: offsite rclone не удался" >&2
    fi
elif [ -n "${BACKUP_OFFSITE_RSYNC:-}" ] && command -v rsync >/dev/null 2>&1; then
    if rsync -a "$OUT" "${OUT}.sha256" "$BACKUP_OFFSITE_RSYNC"/ >/dev/null 2>&1; then
        echo "$(date -Is) OK: offsite rsync → $BACKUP_OFFSITE_RSYNC"
    else
        echo "$(date -Is) WARN: offsite rsync не удался" >&2
    fi
fi

# Ротация: удаляем дампы (и .enc, и .sha256) старше RETAIN_DAYS дней (predeploy-файлы тоже).
find "$BACKUP_DIR" -maxdepth 1 \( -name 'backup-*.sql.gz' -o -name 'backup-*.sql.gz.enc' \
    -o -name 'backup-*.sql.gz.sha256' -o -name 'backup-*.sql.gz.enc.sha256' \) \
    -mtime "+${RETAIN_DAYS}" -delete 2>/dev/null || true
