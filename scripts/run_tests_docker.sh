#!/usr/bin/env bash
# Запуск тестов Django в Docker (сервис web, БД PostgreSQL).
# Использование из корня проекта:
#   ./scripts/run_tests_docker.sh              # все тесты
#   ./scripts/run_tests_docker.sh ui.tests.test_view_as   # только тесты view_as
set -e
cd "$(dirname "$0")/.."
TEST_TARGET="${1:-}"
CMD="pip install -q -r /app/backend/requirements.txt && python manage.py test"
if [ -n "$TEST_TARGET" ]; then
  CMD="$CMD $TEST_TARGET"
fi
CMD="$CMD --verbosity=2"
docker compose run --rm web sh -c "$CMD"
