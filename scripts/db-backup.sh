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

mkdir -p "$BACKUP_DIR"
TS="$(date +%F-%H%M%S)"
OUT="$BACKUP_DIR/backup-${TS}.sql.gz"
TMP="${OUT}.part"

cleanup() { rm -f "$TMP"; }
trap cleanup EXIT

# pipefail гарантирует: если pg_dump упал — весь конвейер считается упавшим.
if ! docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" | gzip -9 > "$TMP"; then
    echo "$(date -Is) FAIL: pg_dump/gzip не отработал" >&2
    exit 1
fi

SIZE="$(stat -c%s "$TMP")"
if [ "$SIZE" -lt "$MIN_BYTES" ]; then
    echo "$(date -Is) FAIL: дамп подозрительно мал (${SIZE} B < ${MIN_BYTES})" >&2
    exit 1
fi

mv "$TMP" "$OUT"
trap - EXIT
echo "$(date -Is) OK: $OUT (${SIZE} B)"

# Ротация: удаляем backup-*.sql.gz старше RETAIN_DAYS дней (predeploy-файлы тоже).
find "$BACKUP_DIR" -maxdepth 1 -name 'backup-*.sql.gz' -mtime "+${RETAIN_DAYS}" -delete 2>/dev/null || true
