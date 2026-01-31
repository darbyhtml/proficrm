#!/bin/bash
# Деплой production (docker-compose.prod.yml).
# Требует: .env с POSTGRES_PASSWORD, DJANGO_SECRET_KEY, DJANGO_ALLOWED_HOSTS, DJANGO_CSRF_TRUSTED_ORIGINS.
# Запускать из корня проекта.

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
echo "Ожидание db/redis 15 сек..."
sleep 15

# 5) Миграции (один раз, не в celery/beat)
echo ">>> migrate"
$COMPOSE run --rm web python manage.py migrate --noinput

# 6) Статика
echo ">>> collectstatic"
$COMPOSE run --rm web python manage.py collectstatic --noinput

# 7) Запуск всех сервисов
echo ">>> up -d"
$COMPOSE up -d

echo "Готово. Проверка: curl -sI http://127.0.0.1:8001/health/"
