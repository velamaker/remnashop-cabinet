#!/bin/bash
# E2E миграций OVERLAY-схемы (alembic-env migrations_overlay) на ОДНОРАЗОВОМ Postgres.
# Проверяет 4 сценария: fresh / idempotent / adopt-existing / concurrent-consistency.
# Прод НЕ трогает (свой контейнер PG). Запускать в CI после сборки образа или локально.
#
# Env: IMAGE (дефолт remnashop-remnashop:latest), NETWORK (remnawave-network),
#      ENV_FILE (.env — нужен для AppConfig в alembic env.py), PG_IMAGE (postgres:17).
set -euo pipefail

IMAGE="${IMAGE:-remnashop-remnashop:latest}"
NETWORK="${NETWORK:-remnawave-network}"
ENV_FILE="${ENV_FILE:-.env}"
PG_IMAGE="${PG_IMAGE:-postgres:17}"
PG="mig-e2e-pg-$$"
ALEMBIC="/opt/remnashop/.venv/bin/alembic -c src/infrastructure/database/migrations_overlay/alembic_overlay.ini upgrade head"

fail() { echo "MIGRATION-E2E FAIL: $*" >&2; exit 1; }
cleanup() { docker rm -f "$PG" >/dev/null 2>&1 || true; }
trap cleanup EXIT

[ -f "$ENV_FILE" ] || fail "нет $ENV_FILE (нужен для AppConfig в alembic env.py)"

run_alembic() {
  docker run --rm --network "$NETWORK" --env-file "$ENV_FILE" \
    -e DATABASE_HOST="$PG" -e DATABASE_PORT=5432 -e DATABASE_PASSWORD=e2epass \
    -w /opt/remnashop "$IMAGE" sh -c "$ALEMBIC"
}
q() { docker exec "$PG" psql -U remnashop -d remnashop -tAc "$1" 2>/dev/null; }
mk_users() { docker exec "$PG" psql -U remnashop -d remnashop -q -c \
  "CREATE TABLE users (id SERIAL PRIMARY KEY, telegram_id BIGINT, role VARCHAR(20) DEFAULT 'USER');" >/dev/null; }

echo "== поднимаю одноразовый Postgres ($PG_IMAGE) =="
docker run -d --name "$PG" --network "$NETWORK" -e POSTGRES_USER=remnashop \
  -e POSTGRES_PASSWORD=e2epass -e POSTGRES_DB=remnashop "$PG_IMAGE" >/dev/null
for i in $(seq 1 30); do docker exec "$PG" pg_isready -U remnashop >/dev/null 2>&1 && break; sleep 1; done
mk_users

echo "[1/4] fresh upgrade → все overlay-таблицы + версия"
run_alembic >/dev/null
[ "$(q "SELECT to_regclass('admin_2fa')::text")" = "admin_2fa" ] || fail "admin_2fa не создана"
[ "$(q "SELECT to_regclass('session_invalidations')::text")" = "session_invalidations" ] || fail "session_invalidations не создана"
[ "$(q "SELECT version_num FROM alembic_version_overlay")" = "0001" ] || fail "версия != 0001"
[ "$(q "SELECT count(*) FROM information_schema.columns WHERE table_name='users' AND column_name='cabinet_balance'")" = "1" ] || fail "users.cabinet_balance нет"
CREATED="$(q "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")"

echo "[2/4] idempotent → повтор ничего не ломает"
run_alembic >/dev/null
[ "$(q "SELECT version_num FROM alembic_version_overlay")" = "0001" ] || fail "версия изменилась при повторе"
[ "$(q "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")" = "$CREATED" ] || fail "повтор изменил число таблиц"

echo "[3/4] adopt-existing → таблицы есть, version-таблицы нет (как у старой установки)"
docker exec "$PG" psql -U remnashop -d remnashop -q -c "DROP TABLE alembic_version_overlay;" >/dev/null
BEFORE="$(q "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")"
run_alembic >/dev/null
[ "$(q "SELECT version_num FROM alembic_version_overlay")" = "0001" ] || fail "adopt не записал версию 0001"
AFTER="$(q "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")"
[ "$AFTER" = "$((BEFORE + 1))" ] || fail "adopt изменил схему (таблиц было $BEFORE, стало $AFTER; ожидалось +1 version-таблица)"

echo "[4/4] concurrent-consistency → два upgrade разом дают консистентный итог"
docker exec "$PG" psql -U remnashop -d remnashop -q -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" >/dev/null
mk_users
# Прямой alembic (без lifespan-advisory-lock) может дать гонку на version-таблице — один
# из двух может упасть, НО итоговое состояние обязано быть консистентным (ровно 0001 + все
# таблицы). Сериализацию через advisory-lock обеспечивает lifespan (_run_overlay_migrations).
run_alembic >/dev/null 2>&1 &
run_alembic >/dev/null 2>&1 &
wait || true
[ "$(q "SELECT version_num FROM alembic_version_overlay")" = "0001" ] || fail "concurrent: версия != 0001"
[ "$(q "SELECT to_regclass('admin_2fa')::text")" = "admin_2fa" ] || fail "concurrent: таблицы не созданы"

echo "MIGRATION-E2E OK: fresh / idempotent / adopt-existing / concurrent-consistency — все прошли"
