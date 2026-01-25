#!/bin/bash
# Тест восстановления: создаёт временную БД, восстанавливает последний бэкап, sanity-check, удаляет БД.
# Запускать с хоста из корня проекта. Требует: db, .env (POSTGRES_*), хотя бы один бэкап .sql.gz или .sql.gz.gpg.
# При .gpg — расшифровка через gpg (ключ в ключерке). При ошибке — exit 1.

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

BACKUP_DIR="${PROFICRM_BACKUP_DIR:-/opt/proficrm/backups}"
COMPOSE="docker compose -f docker-compose.prod.yml"
TEST_DB="crm_restore_test"

if [ -f ".env" ]; then
  set -a
  . ./.env
  set +a
fi

POSTGRES_USER="${POSTGRES_USER:-crm}"

# Последний бэкап по времени модификации
F=$(ls -t "$BACKUP_DIR"/*.sql.gz "$BACKUP_DIR"/*.sql.gz.gpg 2>/dev/null | head -1)
if [ -z "$F" ] || [ ! -f "$F" ]; then
  echo "ERROR: Нет бэкапов в $BACKUP_DIR (*.sql.gz или *.sql.gz.gpg)" >&2
  exit 1
fi

echo "Бэкап: $F"

# Создать тестовую БД
$COMPOSE exec -T db psql -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE $TEST_DB;"

cleanup() {
  $COMPOSE exec -T db psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS $TEST_DB;" 2>/dev/null || true
}
trap cleanup EXIT

# Восстановить
if [[ "$F" == *.gpg ]]; then
  gpg -d "$F" | gunzip | $COMPOSE exec -T db psql -U "$POSTGRES_USER" -d "$TEST_DB" -f - >/dev/null
else
  gunzip -c "$F" | $COMPOSE exec -T db psql -U "$POSTGRES_USER" -d "$TEST_DB" -f - >/dev/null
fi

# Sanity: список таблиц и SELECT 1
$COMPOSE exec -T db psql -U "$POSTGRES_USER" -d "$TEST_DB" -c "\dt" >/dev/null
$COMPOSE exec -T db psql -U "$POSTGRES_USER" -d "$TEST_DB" -c "SELECT 1;" | grep -q "1"

echo "restore_postgres_test: OK (БД $TEST_DB создана, восстановлена, проверена, удалена)"
