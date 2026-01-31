#!/bin/bash
# Деплой staging (crm-staging.groupprofi.ru, docker-compose.staging.yml).
# Требует: .env.staging с POSTGRES_PASSWORD, DJANGO_SECRET_KEY и т.д.
# На одном хосте с продом (5.181.254.172): staging nginx слушает 127.0.0.1:8080; хост-Nginx проксирует crm-staging.groupprofi.ru на 8080.
# Запускать из корня проекта.

set -e

COMPOSE="docker compose -f docker-compose.staging.yml"
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

if [ ! -f ".env.staging" ]; then
    echo "Создайте .env.staging из env.staging.template и заполните POSTGRES_PASSWORD, DJANGO_SECRET_KEY и т.д."
    exit 1
fi
# Очистка: .env из .env.staging, напоминание не использовать прод-файлы
[ -x "scripts/cleanup_for_staging.sh" ] && ./scripts/cleanup_for_staging.sh || true
# Compose подставляет переменные только из .env
cp .env.staging .env

# Проверка обязательных переменных (не пустые и не плейсхолдеры)
check_var() {
    local name="$1"
    local val
    val=$(grep -E "^${name}=" .env.staging 2>/dev/null | cut -d= -f2- | tr -d '\r' || true)
    if [ -z "$val" ] || [ "$val" = "CHANGE_ME_STRONG_PASSWORD" ] || [ "$val" = "CHANGE_ME_GENERATE_STRONG_KEY" ] || [ "$val" = "CHANGE_ME_GENERATE_FERNET_KEY" ]; then
        echo "Ошибка: в .env.staging задайте ${name} (сейчас пусто или значение-плейсхолдер)."
        echo "Запустите: ./scripts/setup_staging_env.sh — затем отредактируйте .env.staging и задайте POSTGRES_PASSWORD."
        exit 1
    fi
}
check_var "POSTGRES_PASSWORD"
check_var "DJANGO_SECRET_KEY"

# 1) Обновление кода
echo ">>> git pull"
git pull origin main

# 2) Сборка образов
echo ">>> docker compose build"
$COMPOSE build

# 3) Запуск db, redis, typesense; ожидание готовности
$COMPOSE up -d db redis typesense
echo "Ожидание db/redis 15 сек..."
sleep 15

# 4) Миграции (в command web тоже есть migrate; здесь делаем явно для деплоя)
echo ">>> migrate"
$COMPOSE run --rm web python manage.py migrate --noinput

# 5) Статика (от root: том static_staging при создании принадлежит root, crmuser не может писать)
echo ">>> collectstatic"
$COMPOSE run --rm -u root web python manage.py collectstatic --noinput

# 6) Запуск всех сервисов (удаляем старый контейнер nginx, если имя занято от предыдущего запуска)
echo ">>> up -d"
docker rm -f crm_staging_nginx 2>/dev/null || true
$COMPOSE up -d

echo "Готово. Staging nginx на 127.0.0.1:8080. Проверка: curl -sI http://127.0.0.1:8080/health/"
