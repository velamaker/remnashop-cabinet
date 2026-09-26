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
# бы. Только перечисленные ключи и только если они не заданы явно. Чтение .env и пароля
# — общие с проверкой восстановления (scripts/_env.sh): расшифровать должно тем же.
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
ENV_FILE="${ENV_FILE:-$SCRIPT_DIR/../.env}"
# shellcheck source=scripts/_env.sh
. "$SCRIPT_DIR/_env.sh" || { echo "$(date -Is) FAIL: нет $SCRIPT_DIR/_env.sh (скрипт скопирован без соседа?)" >&2; exit 1; }
resolve_backup_passphrase
for _k in BACKUP_OFFSITE_RCLONE_REMOTE BACKUP_OFFSITE_RSYNC BACKUP_OFFSITE_TELEGRAM_CHAT_ID \
          BOT_TOKEN BOT_OWNER_ID BOT_PROXY_URL; do
    env_default "$_k"
done

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
#   BACKUP_OFFSITE_TELEGRAM_CHAT_ID="owner"             (владельцу бота в личный чат)
#   BACKUP_OFFSITE_TELEGRAM_CHAT_ID="-100…"             (или закрытый канал; бот — админ)
# Рекомендуется гнать ШИФРОВАННЫЕ бэкапы (BACKUP_PASSPHRASE), т.к. уходят наружу.
# Telegram — независимо от двух первых: ничего заводить не нужно, лимит файла у бота
# 50 МБ, а дамп небольшого магазина весит сотни килобайт. Туда уходит ТОЛЬКО
# зашифрованный дамп: в открытом виде в нём хэши паролей и платежи, а чат Telegram —
# чужой сервер. Токен и прокси (в адресе прокси бывают логин и пароль) передаём curl
# конфигом через stdin, чтобы они не светились в списке процессов.
TG_MAX_BYTES="${TG_MAX_BYTES:-49000000}"
# Значение в кавычках конфига curl: обратная косая и кавычка внутри экранируются.
curl_cfg_quote() { local v="${1//\\/\\\\}"; printf '"%s"' "${v//\"/\\\"}"; }
# «owner» — владельцу бота в личный чат (BOT_OWNER_ID): свой id искать не нужно.
if [ "${BACKUP_OFFSITE_TELEGRAM_CHAT_ID:-}" = owner ]; then
    _owner="${BOT_OWNER_ID:-}"
    BACKUP_OFFSITE_TELEGRAM_CHAT_ID="${_owner%%,*}"
    [ -n "$BACKUP_OFFSITE_TELEGRAM_CHAT_ID" ] || echo "$(date -Is) WARN: offsite telegram «owner» — нет BOT_OWNER_ID" >&2
fi
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
        # --form-string для текстовых полей: у -F значение, начатое с @ или <, curl
        # читает как имя файла («@канал» в chat_id ушёл бы чтением файла «канал»).
        if {
            printf 'url = %s\n' "$(curl_cfg_quote "https://api.telegram.org/bot${BOT_TOKEN}/sendDocument")"
            if [ -n "${BOT_PROXY_URL:-}" ]; then
                printf 'proxy = %s\n' "$(curl_cfg_quote "$BOT_PROXY_URL")"
            fi
        } | curl -fsS --max-time 120 -K - \
                --form-string "chat_id=${BACKUP_OFFSITE_TELEGRAM_CHAT_ID}" \
                --form-string "caption=${CAPTION}" \
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
