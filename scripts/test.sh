#!/usr/bin/env bash
# Запуск Django-тестов с PostgreSQL.
#
# Использование:
#   scripts/test.sh                    # все тесты
#   scripts/test.sh phonebridge        # только app
#   scripts/test.sh phonebridge.tests  # конкретный модуль
#
# Требует запущенного crm-test-pg (docker compose -f docker-compose.test.yml up -d).
# При передаче флага --up — поднимает контейнер автоматически и гасит после.

set -euo pipefail

COMPOSE_FILE="$(dirname "$0")/../docker-compose.test.yml"
AUTO_UP=0

if [[ "${1:-}" == "--up" ]]; then
  AUTO_UP=1
  shift
fi

if [[ $AUTO_UP -eq 1 ]]; then
  echo "→ Запускаем crm-test-pg..."
  docker compose -f "$COMPOSE_FILE" up -d
  trap 'echo "→ Останавливаем crm-test-pg..."; docker compose -f "$COMPOSE_FILE" down' EXIT

  # Ждём готовности
  echo "→ Ждём PostgreSQL..."
  for i in $(seq 1 30); do
    if docker compose -f "$COMPOSE_FILE" exec -T db_test pg_isready -U crm -d crm_test >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
fi

cd "$(dirname "$0")/../backend"

DB_ENGINE=postgres \
POSTGRES_DB=crm_test \
POSTGRES_USER=crm \
POSTGRES_PASSWORD=testpassword \
POSTGRES_HOST=localhost \
POSTGRES_PORT=5433 \
DJANGO_SECRET_KEY=test-secret-key-local \
DJANGO_DEBUG=1 \
REDIS_URL="" \
  python manage.py test --verbosity=2 "$@"
