#!/bin/bash
# Бэкап Postgres (docker-compose.prod). Запускать с хоста из корня проекта.
# Требует: .env с POSTGRES_*, running db. Каталог backups создаётся при необходимости.
# Опционально: GPG_KEY_ID или BACKUP_GPG_KEY — id ключа для шифрования. Если пусто — без gpg.
# Очистка: файлы старше BACKUP_RETENTION_DAYS (по умолчанию 14) удаляются.

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

BACKUP_DIR="${PROFICRM_BACKUP_DIR:-/opt/proficrm/backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
COMPOSE="docker compose -f docker-compose.prod.yml"

if [ -f ".env" ]; then
  set -a
  . ./.env
  set +a
fi

POSTGRES_USER="${POSTGRES_USER:-crm}"
POSTGRES_DB="${POSTGRES_DB:-crm}"

mkdir -p "$BACKUP_DIR"
TS=$(date +%Y%m%d_%H%M%S)
F="$BACKUP_DIR/${POSTGRES_DB}_${TS}.sql"

$COMPOSE exec -T db pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > "$F"
gzip "$F"
F="${F}.gz"

if [ -n "${BACKUP_GPG_KEY}${GPG_KEY_ID}" ]; then
  KEY="${BACKUP_GPG_KEY:-$GPG_KEY_ID}"
  gpg --encrypt --recipient "$KEY" --trust-model always "$F" && rm -f "$F"
  F="${F}.gpg"
fi

echo "Backup: $F"

# Удалить старше RETENTION_DAYS
find "$BACKUP_DIR" -maxdepth 1 -name "*.sql.gz" -mtime +"$RETENTION_DAYS" -delete
find "$BACKUP_DIR" -maxdepth 1 -name "*.sql.gz.gpg" -mtime +"$RETENTION_DAYS" -delete
