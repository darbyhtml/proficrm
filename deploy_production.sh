#!/bin/bash
# Деплой production (docker-compose.prod.yml).
# Требует: .env с POSTGRES_PASSWORD, DJANGO_SECRET_KEY, DJANGO_ALLOWED_HOSTS, DJANGO_CSRF_TRUSTED_ORIGINS.
# Поиск компаний: только PostgreSQL FTS (CompanySearchIndex). Ежедневная переиндексация: Celery Beat.
# Быстрый деплой без ожидания индексирования: SKIP_INDEXING=1 ./deploy_production.sh
# Индекс потом: docker compose -f docker-compose.prod.yml run --rm web python manage.py rebuild_company_search_index

set -e

COMPOSE="docker compose -f docker-compose.prod.yml"
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"
# Очистка: убрать .env.staging и напомнить не использовать стагинг-файлы
[ -x "scripts/cleanup_for_prod.sh" ] && ./scripts/cleanup_for_prod.sh || true

if [ ! -f ".env" ]; then
    echo "Создайте .env из env.template и заполните POSTGRES_PASSWORD, DJANGO_SECRET_KEY, DJANGO_ALLOWED_HOSTS, DJANGO_CSRF_TRUSTED_ORIGINS"
    exit 1
fi

# 1) Каталоги для static/media (на проде: sudo chown 1000:1000 data/staticfiles data/media)
mkdir -p data/staticfiles data/media
if command -v chown >/dev/null 2>&1; then
    chown 1000:1000 data/staticfiles data/media 2>/dev/null || true
fi

# 2) Обновление кода
echo ">>> git pull"
git pull origin main

# 3) Сборка образов
echo ">>> docker compose build"
$COMPOSE build

# 4) Запуск db и redis, ожидание готовности
$COMPOSE up -d db redis
echo "Ожидание готовности db (до 60 сек)..."
_db_ready=0
for i in $(seq 1 60); do
  if $COMPOSE exec -T db pg_isready -U "${POSTGRES_USER:-postgres}" -q 2>/dev/null; then
    echo "  db готова (попытка ${i})"
    _db_ready=1
    break
  fi
  sleep 1
done
[ "$_db_ready" -eq 0 ] && echo "WARN: db не ответила за 60 сек — продолжаем всё равно"

echo "Ожидание готовности redis (до 30 сек)..."
_redis_ready=0
for i in $(seq 1 30); do
  if $COMPOSE exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; then
    echo "  redis готов (попытка ${i})"
    _redis_ready=1
    break
  fi
  sleep 1
done
[ "$_redis_ready" -eq 0 ] && echo "WARN: redis не ответил за 30 сек — продолжаем всё равно"

# 5) Миграции (один раз, не в celery/beat)
echo ">>> migrate"
$COMPOSE run --rm web python manage.py migrate --noinput

# 6) Статика
echo ">>> collectstatic"
$COMPOSE run --rm web python manage.py collectstatic --noinput

# 6.1) Перестроение поискового индекса (пропуск при SKIP_INDEXING=1 — потом: docker compose run --rm web python manage.py rebuild_company_search_index)
if [ -z "${SKIP_INDEXING}" ] || [ "${SKIP_INDEXING}" = "0" ]; then
  echo ">>> rebuild_company_search_index"
  $COMPOSE run --rm web python manage.py rebuild_company_search_index
  echo ">>> index_companies_typesense (Typesense отключён, команда no-op)"
  $COMPOSE run --rm web python manage.py index_companies_typesense --chunk 300 || true
else
  echo ">>> SKIP_INDEXING=1 — индексирование пропущено. Позже: $COMPOSE run --rm web python manage.py rebuild_company_search_index"
fi

# 7) Запуск всех сервисов
echo ">>> up -d"
$COMPOSE up -d

echo "Готово. Проверка: curl -sI http://127.0.0.1:8001/health/"
