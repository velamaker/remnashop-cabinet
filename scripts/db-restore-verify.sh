#!/bin/bash
# Restore-drill: восстанавливает ПОСЛЕДНИЙ бэкап в одноразовый PostgreSQL-контейнер и
# проверяет целостность (валидный дамп + непустые ключевые таблицы). Ничего не трогает
# в проде. Запускать по cron (напр. ежемесячно) — доказывает, что бэкап реально
# восстановим (аудит: «нет автоматической restore-проверки»).
#
# Env: BACKUP_DIR, BACKUP_PASSPHRASE (если бэкапы шифрованы), PG_IMAGE, DB_NAME/USER.
#      DRILL_HOOK — необязательный python-скрипт, который после миграции прогоняется
#      в образе бота против восстановленной копии (например,
#      scripts/drills/renewal_discount_drill.py). Сеть drill-а --internal: до Telegram,
#      почты и панели из неё не достучаться, так что живым людям ничего не уйдёт.
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/opt/remnashop-backups}"
PG_IMAGE="${PG_IMAGE:-postgres:17}"
DB_NAME="${DB_NAME:-remnashop}"
DB_USER="${DB_USER:-remnashop}"
BACKUP_PASSPHRASE="${BACKUP_PASSPHRASE:-}"
if [ -z "$BACKUP_PASSPHRASE" ] && [ -n "${BACKUP_PASSPHRASE_FILE:-}" ] && [ -f "${BACKUP_PASSPHRASE_FILE}" ]; then
    BACKUP_PASSPHRASE="$(cat "$BACKUP_PASSPHRASE_FILE")"
fi
MIN_ROWS="${MIN_ROWS:-1}"        # минимум строк в users для «успеха»
CHECK_TABLE="${CHECK_TABLE:-users}"
# Опциональный шаг «restore→migrate»: после восстановления прогнать overlay-alembic на
# восстановленной БД и убедиться, что схема миграционно-консистентна (доказывает, что
# из бэкапа можно не только восстановиться, но и штатно стартовать). Включается, если
# доступны образ overlay и ENV_FILE. Для старых бэкапов (до alembic) проверяет adopt.
IMAGE="${IMAGE:-remnashop-remnashop:latest}"
ENV_FILE="${ENV_FILE:-/opt/remnashop/.env}"
DRILL_NET="remnashop-drill-net-$$"
DRILL_HOOK="${DRILL_HOOK:-}"

fail() { echo "$(date -Is) RESTORE-DRILL FAIL: $*" >&2; exit 1; }

# Последний бэкап (шифрованный или нет).
LATEST="$(ls -1t "$BACKUP_DIR"/backup-*.sql.gz.enc "$BACKUP_DIR"/backup-*.sql.gz 2>/dev/null | head -1 || true)"
[ -n "$LATEST" ] || fail "нет бэкапов в $BACKUP_DIR"
echo "$(date -Is) restore-drill: проверяю $LATEST"

# Проверка контрольной суммы, если сайдкар есть.
if [ -f "${LATEST}.sha256" ]; then
    GOT="$(sha256sum "$LATEST" | awk '{print $1}')"
    WANT="$(cat "${LATEST}.sha256")"
    [ "$GOT" = "$WANT" ] || fail "SHA-256 бэкапа не совпал"
    echo "$(date -Is) restore-drill: checksum OK"
fi

WORK="$(mktemp -d)"
CNAME="remnashop-restore-drill-$$"
cleanup() {
    rm -rf "$WORK"
    docker rm -f "$CNAME" >/dev/null 2>&1 || true
    docker network rm "$DRILL_NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Расшифровка (если .enc) → gunzip → plain SQL.
SQL="$WORK/dump.sql"
if [ "${LATEST##*.}" = "enc" ]; then
    [ -n "$BACKUP_PASSPHRASE" ] || fail "бэкап шифрован, но BACKUP_PASSPHRASE не задан"
    openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_PASSPHRASE -in "$LATEST" | gunzip > "$SQL" \
        || fail "не удалось расшифровать/распаковать (неверный пароль?)"
else
    gunzip -c "$LATEST" > "$SQL" || fail "не удалось распаковать"
fi

# Одноразовый PostgreSQL на своей сети (чтобы шаг миграции мог до него достучаться по имени).
# --internal: у сети нет выхода наружу. Восстановленная копия — это настоящие люди с
# настоящими telegram_id, и любой код, запущенный рядом с ней (DRILL_HOOK), не должен
# иметь даже теоретической возможности им написать.
docker network create --internal "$DRILL_NET" >/dev/null 2>&1 || fail "не удалось создать сеть $DRILL_NET"
docker run -d --name "$CNAME" --network "$DRILL_NET" -e POSTGRES_PASSWORD=drill -e POSTGRES_USER="$DB_USER" \
    -e POSTGRES_DB="$DB_NAME" "$PG_IMAGE" >/dev/null || fail "не удалось поднять $PG_IMAGE"
for i in $(seq 1 30); do
    docker exec "$CNAME" pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1 && break
    sleep 1
    [ "$i" = 30 ] && fail "PostgreSQL не поднялся за 30с"
done

# Восстановление.
docker exec -i "$CNAME" psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=0 < "$SQL" >/dev/null 2>&1 \
    || fail "psql restore завершился с ошибкой"

# Санити: ключевая таблица существует и непуста.
ROWS="$(docker exec "$CNAME" psql -U "$DB_USER" -d "$DB_NAME" -tAc "SELECT count(*) FROM ${CHECK_TABLE}" 2>/dev/null || echo "ERR")"
[ "$ROWS" = "ERR" ] && fail "таблица ${CHECK_TABLE} не восстановилась"
[ "$ROWS" -ge "$MIN_ROWS" ] 2>/dev/null || fail "в ${CHECK_TABLE} строк: ${ROWS} (< ${MIN_ROWS})"

# Опциональный шаг restore→migrate: overlay-alembic на восстановленной БД → консистентно.
if [ -f "$ENV_FILE" ] && docker image inspect "$IMAGE" >/dev/null 2>&1; then
    docker run --rm --network "$DRILL_NET" --env-file "$ENV_FILE" \
        -e DATABASE_HOST="$CNAME" -e DATABASE_PORT=5432 -e DATABASE_PASSWORD=drill \
        -e DATABASE_NAME="$DB_NAME" -e DATABASE_USER="$DB_USER" -w /opt/remnashop "$IMAGE" \
        sh -c "/opt/remnashop/.venv/bin/alembic -c src/infrastructure/database/migrations_overlay/alembic_overlay.ini upgrade head" \
        >/dev/null 2>&1 || fail "overlay-миграция на восстановленной БД не прошла"
    VER="$(docker exec "$CNAME" psql -U "$DB_USER" -d "$DB_NAME" -tAc "SELECT version_num FROM alembic_version_overlay" 2>/dev/null || echo "ERR")"
    [ -n "$VER" ] && [ "$VER" != "ERR" ] || fail "после миграции нет alembic_version_overlay"
    echo "$(date -Is) restore-drill: overlay-миграция OK (alembic_version_overlay=${VER})"

    # Необязательный прогон своего кода на копии. BOT_TOKEN заведомо фальшивый, assets —
    # временная папка внутри контейнера: даже ошибка в скрипте не дойдёт ни до бота,
    # ни до настроек установки.
    if [ -n "$DRILL_HOOK" ]; then
        [ -f "$DRILL_HOOK" ] || fail "DRILL_HOOK не найден: $DRILL_HOOK"
        docker run --rm --network "$DRILL_NET" --env-file "$ENV_FILE" \
            -e BOT_TOKEN=123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA \
            -e APP_ASSETS_DIR=/tmp/drill-assets \
            -e DATABASE_HOST="$CNAME" -e DATABASE_PORT=5432 -e DATABASE_PASSWORD=drill \
            -e DATABASE_NAME="$DB_NAME" -e DATABASE_USER="$DB_USER" \
            -v "$(cd "$(dirname "$DRILL_HOOK")" && pwd)/$(basename "$DRILL_HOOK"):/tmp/drill_hook.py:ro" \
            -w /opt/remnashop "$IMAGE" \
            /opt/remnashop/.venv/bin/python /tmp/drill_hook.py \
            || fail "DRILL_HOOK $DRILL_HOOK завершился с ошибкой"
        echo "$(date -Is) restore-drill: DRILL_HOOK OK ($DRILL_HOOK)"
    fi
else
    echo "$(date -Is) restore-drill: шаг миграции пропущен (нет $ENV_FILE или образа $IMAGE)"
    [ -z "$DRILL_HOOK" ] || fail "DRILL_HOOK задан, но шаг миграции пропущен — проверять не на чем"
fi

echo "$(date -Is) RESTORE-DRILL OK: $LATEST восстановлен, ${CHECK_TABLE}=${ROWS} строк"
