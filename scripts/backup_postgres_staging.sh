#!/bin/bash
# Бэкап staging Postgres (docker-compose.staging). Запускать с хоста
# из корня staging checkout. Создан 2026-04-24 как safety net перед
# W10.2-early WAL-G rollout.
#
# Требует: .env (staging checkout имеет .env.staging → .env symlink)
# с POSTGRES_* (или fallback к constants), running db.
# Очистка: файлы старше BACKUP_RETENTION_DAYS (по умолчанию 7) удаляются.

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

BACKUP_DIR="${PROFICRM_STAGING_BACKUP_DIR:-/opt/proficrm-staging/backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
COMPOSE="docker compose -f docker-compose.staging.yml -p proficrm-staging"

if [ -f ".env.staging" ]; then
  set -a
  . ./.env.staging
  set +a
elif [ -f ".env" ]; then
  set -a
  . ./.env
  set +a
fi

POSTGRES_USER="${POSTGRES_USER:-crm_staging}"
POSTGRES_DB="${POSTGRES_DB:-crm_staging}"

mkdir -p "$BACKUP_DIR"
TS=$(date +%Y%m%d_%H%M%S)
F="$BACKUP_DIR/${POSTGRES_DB}_${TS}.sql"

$COMPOSE exec -T db pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > "$F"
gzip "$F"
F="${F}.gz"

echo "Staging backup: $F"

# Удалить старше RETENTION_DAYS (staging-only, не trogaem prod backup dir).
find "$BACKUP_DIR" -maxdepth 1 -name "*.sql.gz" -mtime +"$RETENTION_DAYS" -delete
