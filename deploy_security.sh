#!/bin/bash
# Деплой production на VDS (docker-compose.prod.yml + docker-compose.vds.yml).
# Прод с gunicorn, healthchecks, лимитами; vds — порт БД 15432 и web 8001.
# Поиск компаний: используется только PostgreSQL FTS (CompanySearchIndex, SEARCH_ENGINE_BACKEND=postgres).
# Ежедневная переиндексация компаний: 00:00 UTC+3 (Celery Beat).
# Использование: ./deploy_security.sh

set -e

COMPOSE="docker compose -f docker-compose.prod.yml -f docker-compose.vds.yml"
cd "$(cd "$(dirname "$0")" && pwd)"
# Очистка: убрать .env.staging и напомнить не использовать стагинг-файлы
[ -x "scripts/cleanup_for_prod.sh" ] && ./scripts/cleanup_for_prod.sh || true

echo "🔒 Деплой production CRM на VDS..."

# 1. Проверка директории и .env
if [ ! -f "docker-compose.prod.yml" ]; then
    echo "❌ Ошибка: запустите скрипт из корня проекта"
    exit 1
fi
if [ ! -f ".env" ]; then
    echo "❌ Создайте .env из env.template и заполните секреты"
    exit 1
fi

# 2. Каталоги для static/media (владелец 1000:1000 = crmuser в контейнере, иначе collectstatic/запись в media падают)
mkdir -p data/staticfiles data/media
if ! chown -R 1000:1000 data/staticfiles data/media 2>/dev/null; then
    if command -v sudo >/dev/null 2>&1; then
        sudo chown -R 1000:1000 data/staticfiles data/media
    else
        echo "❌ Нет прав на data/staticfiles и data/media. От root выполните: chown -R 1000:1000 data/staticfiles data/media"
        exit 1
    fi
fi

# 3. Обновление кода
echo "📥 Обновление кода..."
git pull origin main

# 4. Сборка и подъём db, redis, typesense (typesense остаётся в docker-compose, но не используется приложением)
echo "📦 Сборка образов и запуск db/redis/typesense..."
$COMPOSE build
$COMPOSE up -d db redis typesense
echo "Ожидание db/redis 15 сек..."
sleep 15

# 5. Миграции и статика
echo "🗄️  Миграции..."
$COMPOSE run --rm web python manage.py migrate --noinput
echo "📦 collectstatic..."
$COMPOSE run --rm web python manage.py collectstatic --noinput

# 5.1. Перестроение поискового индекса компаний (Postgres FTS)
# Обязательно при первом деплое; при последующих деплоях сигналы обновляют индекс.
echo "🔍 Перестроение поискового индекса компаний (FTS)..."
$COMPOSE run --rm web python manage.py rebuild_company_search_index

# 5.2. Историческая команда индексации в Typesense (no-op; оставлена для обратной совместимости)
echo "🔍 Индексация Typesense (Typesense отключён, команда no-op)..."
$COMPOSE run --rm web python manage.py index_companies_typesense --chunk 300 || true

# 6. Запуск всех сервисов
echo "🔄 Запуск web, celery, celery-beat..."
$COMPOSE up -d

# 7. Проверка настроек (опционально)
echo "🔍 Проверка настроек..."
DEBUG_VALUE=$($COMPOSE exec -T web python -c "import os; print(os.getenv('DJANGO_DEBUG', '1'))" 2>/dev/null || true)
[ "$DEBUG_VALUE" = "1" ] && echo "⚠️  DJANGO_DEBUG=1 — для прода установите 0 в .env"
SECRET_KEY=$($COMPOSE exec -T web python -c "import os; print(os.getenv('DJANGO_SECRET_KEY', ''))" 2>/dev/null || true)
[ -n "$SECRET_KEY" ] && [ ${#SECRET_KEY} -lt 50 ] && echo "⚠️  Установите сильный DJANGO_SECRET_KEY (50+ символов) в .env"

echo "✅ Готово. Проверка: curl -sI http://127.0.0.1:8001/health/"
echo "   Логи: $COMPOSE logs -f web"
