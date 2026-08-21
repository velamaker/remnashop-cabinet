#!/bin/bash
# E2E миграций OVERLAY-схемы (alembic-env migrations_overlay) на ОДНОРАЗОВОМ Postgres.
# Проверяет 5 сценариев: fresh / idempotent / adopt-existing / concurrent-consistency /
# legacy-reserve (смена ключа reserve_grants на действующей базе, миграция 0005).
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

in_image() {
  docker run --rm --network "$NETWORK" --env-file "$ENV_FILE" \
    -e DATABASE_HOST="$PG" -e DATABASE_PORT=5432 -e DATABASE_PASSWORD=e2epass \
    -w /opt/remnashop "$IMAGE" sh -c "$1"
}
run_alembic() { in_image "$ALEMBIC"; }

# Ожидаемую версию СПРАШИВАЕМ У ALEMBIC, а не пишем числом. Захардкоженная «0001»
# молча перестала проверять что-либо, как только появилась 0002: сравнение падало бы
# на каждой новой ревизии, поэтому проверку просто перестали запускать. Теперь
# добавление миграции не требует правки этого файла.
alembic_head() {
  in_image "${ALEMBIC% upgrade head} heads" | awk '/\(head\)/{print $1; exit}'
}
q() { docker exec "$PG" psql -U remnashop -d remnashop -tAc "$1" 2>/dev/null; }
mk_users() { docker exec "$PG" psql -U remnashop -d remnashop -q -c \
  "CREATE TABLE users (id SERIAL PRIMARY KEY, telegram_id BIGINT, role VARCHAR(20) DEFAULT 'USER');" >/dev/null; }

echo "== поднимаю одноразовый Postgres ($PG_IMAGE) =="
docker run -d --name "$PG" --network "$NETWORK" -e POSTGRES_USER=remnashop \
  -e POSTGRES_PASSWORD=e2epass -e POSTGRES_DB=remnashop "$PG_IMAGE" >/dev/null
for i in $(seq 1 30); do docker exec "$PG" pg_isready -U remnashop >/dev/null 2>&1 && break; sleep 1; done
mk_users

HEAD="$(alembic_head)"
[ -n "$HEAD" ] || fail "не смог узнать head у alembic"
echo "== ожидаемая версия overlay: $HEAD =="

echo "[1/5] fresh upgrade → все overlay-таблицы + версия"
run_alembic >/dev/null
[ "$(q "SELECT to_regclass('admin_2fa')::text")" = "admin_2fa" ] || fail "admin_2fa не создана"
[ "$(q "SELECT to_regclass('session_invalidations')::text")" = "session_invalidations" ] || fail "session_invalidations не создана"
[ "$(q "SELECT version_num FROM alembic_version_overlay")" = "$HEAD" ] || fail "версия != $HEAD"
[ "$(q "SELECT count(*) FROM information_schema.columns WHERE table_name='users' AND column_name='cabinet_balance'")" = "1" ] || fail "users.cabinet_balance нет"
CREATED="$(q "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")"

echo "[2/5] idempotent → повтор ничего не ломает"
run_alembic >/dev/null
[ "$(q "SELECT version_num FROM alembic_version_overlay")" = "$HEAD" ] || fail "версия изменилась при повторе"
[ "$(q "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")" = "$CREATED" ] || fail "повтор изменил число таблиц"

echo "[3/5] adopt-existing → таблицы есть, version-таблицы нет (как у старой установки)"
docker exec "$PG" psql -U remnashop -d remnashop -q -c "DROP TABLE alembic_version_overlay;" >/dev/null
BEFORE="$(q "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")"
run_alembic >/dev/null
[ "$(q "SELECT version_num FROM alembic_version_overlay")" = "$HEAD" ] || fail "adopt не записал версию $HEAD"
AFTER="$(q "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")"
[ "$AFTER" = "$((BEFORE + 1))" ] || fail "adopt изменил схему (таблиц было $BEFORE, стало $AFTER; ожидалось +1 version-таблица)"

echo "[4/5] concurrent-consistency → два upgrade разом дают консистентный итог"
docker exec "$PG" psql -U remnashop -d remnashop -q -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" >/dev/null
mk_users
# Прямой alembic (без lifespan-advisory-lock) может дать гонку на version-таблице — один
# из двух может упасть, НО итоговое состояние обязано быть консистентным (ровно head + все
# таблицы). Сериализацию через advisory-lock обеспечивает lifespan (_run_overlay_migrations).
run_alembic >/dev/null 2>&1 &
run_alembic >/dev/null 2>&1 &
wait || true
[ "$(q "SELECT version_num FROM alembic_version_overlay")" = "$HEAD" ] || fail "concurrent: версия != $HEAD"
[ "$(q "SELECT to_regclass('admin_2fa')::text")" = "admin_2fa" ] || fail "concurrent: таблицы не созданы"

echo "[5/5] legacy reserve_grants → 0005 переносит ключ, не теряя выдач"
# Симулируем БД действующей установки: таблица резерва в СТАРОМ виде (PK по user_id),
# с уже выданным резервом. Именно на таких базах 0005 и поедет.
docker exec "$PG" psql -U remnashop -d remnashop -q -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" >/dev/null
mk_users
docker exec "$PG" psql -U remnashop -d remnashop -q -c "
  INSERT INTO users (telegram_id) VALUES (777);
  CREATE TABLE reserve_grants (
      user_id           INTEGER      NOT NULL PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
      remna_uuid        VARCHAR(64)  NOT NULL,
      granted_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
      reserve_expire_at TIMESTAMPTZ  NOT NULL,
      ended             BOOLEAN      NOT NULL DEFAULT false);
  INSERT INTO reserve_grants (user_id, remna_uuid, reserve_expire_at)
    SELECT id, 'legacy-uuid', now() + interval '3 days' FROM users LIMIT 1;" >/dev/null
run_alembic >/dev/null
PKCOL="$(q "SELECT a.attname FROM pg_constraint c
            JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
            WHERE c.conrelid = to_regclass('reserve_grants') AND c.contype = 'p'")"
[ "$PKCOL" = "id" ] || fail "0005: первичный ключ не переехал на id (получено: $PKCOL)"
[ "$(q "SELECT count(*) FROM pg_indexes WHERE indexname='ux_reserve_grants_open'")" = "1" ] \
  || fail "0005: нет частичного уникального индекса ux_reserve_grants_open"
[ "$(q "SELECT count(*) FROM reserve_grants WHERE remna_uuid='legacy-uuid' AND id IS NOT NULL")" = "1" ] \
  || fail "0005: прежняя выдача потеряна при смене ключа"
# Второй ОТКРЫТЫЙ резерв тому же человеку невозможен, закрытый — не мешает новому.
docker exec "$PG" psql -U remnashop -d remnashop -q -c "
  INSERT INTO reserve_grants (user_id, remna_uuid, reserve_expire_at)
    SELECT user_id, 'second', now() + interval '3 days' FROM reserve_grants
    ON CONFLICT (user_id) WHERE ended = false DO NOTHING;" >/dev/null
[ "$(q "SELECT count(*) FROM reserve_grants")" = "1" ] || fail "0005: индекс пропустил вторую открытую выдачу"
docker exec "$PG" psql -U remnashop -d remnashop -q -c "
  UPDATE reserve_grants SET ended = true;
  INSERT INTO reserve_grants (user_id, remna_uuid, reserve_expire_at)
    SELECT user_id, 'second', now() + interval '3 days' FROM reserve_grants
    ON CONFLICT (user_id) WHERE ended = false DO NOTHING;" >/dev/null
[ "$(q "SELECT count(*) FROM reserve_grants")" = "2" ] \
  || fail "0005: после закрытия прошлой выдачи новая не прошла (резерв не повторится)"

echo "MIGRATION-E2E OK: fresh / idempotent / adopt-existing / concurrent-consistency / legacy-reserve — все прошли"
