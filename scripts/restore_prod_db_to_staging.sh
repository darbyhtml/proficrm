#!/bin/bash
# Восстановление БД прода в стаджинг (только данные БД — компании, заметки, задачи и т.д.).
# Запускать из папки стаджинга: /opt/proficrm-staging.
# Дамп берётся с прода (backup_postgres.sh) или передаётся путём к файлу.
#
# Использование:
#   ./scripts/restore_prod_db_to_staging.sh                    # последний бэкап из /opt/proficrm/backups
#   ./scripts/restore_prod_db_to_staging.sh /path/to/dump.sql.gz
#   ./scripts/restore_prod_db_to_staging.sh /path/to/dump.sql.gz.gpg

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

PROD_BACKUP_DIR="${PROFICRM_BACKUP_DIR_PROD:-/opt/proficrm/backups}"
COMPOSE="docker compose -f docker-compose.staging.yml"

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

# Путь к дампу: аргумент или последний файл из каталога бэкапов прода
if [ -n "$1" ]; then
  F="$1"
  if [ ! -f "$F" ]; then
    echo "Ошибка: файл не найден: $F" >&2
    exit 1
  fi
else
  F=$(ls -t "$PROD_BACKUP_DIR"/*.sql.gz "$PROD_BACKUP_DIR"/*.sql.gz.gpg 2>/dev/null | head -1)
  if [ -z "$F" ] || [ ! -f "$F" ]; then
    echo "Ошибка: не найден дамп в $PROD_BACKUP_DIR. Укажите путь к файлу .sql.gz или .sql.gz.gpg" >&2
    echo "  Пример: ./scripts/restore_prod_db_to_staging.sh /opt/proficrm/backups/crm_20260101_120000.sql.gz" >&2
    exit 1
  fi
fi

echo "Дамп: $F"
echo "БД стаджинга: $POSTGRES_DB (пользователь: $POSTGRES_USER)"
echo "Все данные в БД стаджинга будут заменены данными из дампа. Продолжить? [y/N]"
read -r confirm
if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
  echo "Отменено."
  exit 0
fi

# Остановить подключения к БД стаджинга (web и т.д.)
echo ">>> Остановка контейнеров web/nginx, использующих БД..."
$COMPOSE stop web nginx 2>/dev/null || true

# Завершить оставшиеся сессии к crm_staging
echo ">>> Завершение сессий к БД $POSTGRES_DB..."
$COMPOSE exec -T db psql -U "$POSTGRES_USER" -d postgres -c "
  SELECT pg_terminate_backend(pid) FROM pg_stat_activity
  WHERE datname = '$POSTGRES_DB' AND pid <> pg_backend_pid();
" 2>/dev/null || true

# Очистить схему public и восстановить из дампа
echo ">>> Очистка схемы public..."
$COMPOSE exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
  DROP SCHEMA public CASCADE;
  CREATE SCHEMA public;
  GRANT ALL ON SCHEMA public TO $POSTGRES_USER;
  GRANT ALL ON SCHEMA public TO public;
"

# Дамп с прода содержит OWNER TO crm; на стаджинге пользователь — crm_staging, подменяем
echo ">>> Восстановление из дампа (подмена владельца crm -> $POSTGRES_USER)..."
owner_sed="s/OWNER TO crm;/OWNER TO $POSTGRES_USER;/g; s/OWNER TO \"crm\"/OWNER TO \"$POSTGRES_USER\"/g"
if [[ "$F" == *.gpg ]]; then
  gpg -d "$F" | gunzip | sed "$owner_sed" | $COMPOSE exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f - >/dev/null
else
  gunzip -c "$F" | sed "$owner_sed" | $COMPOSE exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f - >/dev/null
fi

echo ">>> Миграции (на случай расхождения схемы)..."
$COMPOSE run --rm web python manage.py migrate --noinput

echo ">>> Перестроение поискового индекса компаний..."
$COMPOSE run --rm web python manage.py rebuild_company_search_index

echo ">>> Запуск сервисов..."
$COMPOSE up -d

echo "Готово. БД стаджинга заменена данными из прода. Staging: https://crm-staging.groupprofi.ru"
