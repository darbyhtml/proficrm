#!/bin/bash
# Скрипт для настройки безопасности на VDS
# Использование: ./deploy_security.sh

set -e

echo "🔒 Настройка безопасности CRM на VDS..."

# 1. Проверка, что мы в правильной директории
if [ ! -f "docker-compose.yml" ]; then
    echo "❌ Ошибка: запустите скрипт из корня проекта"
    exit 1
fi

# 2. Обновление кода
echo "📥 Обновление кода из репозитория..."
git pull

# 3. Применение миграций
echo "🗄️  Применение миграций..."
docker compose -f docker-compose.yml -f docker-compose.vds.yml exec web python manage.py migrate

# 4. Сбор статических файлов
echo "📦 Сбор статических файлов..."
docker compose -f docker-compose.yml -f docker-compose.vds.yml exec web python manage.py collectstatic --noinput

# 5. Проверка настроек безопасности
echo "🔍 Проверка настроек безопасности..."

# Проверка DEBUG
DEBUG_VALUE=$(docker compose -f docker-compose.yml -f docker-compose.vds.yml exec -T web python -c "import os; from dotenv import load_dotenv; load_dotenv(); print(os.getenv('DJANGO_DEBUG', '1'))")
if [ "$DEBUG_VALUE" = "1" ]; then
    echo "⚠️  ВНИМАНИЕ: DJANGO_DEBUG=1. Для production установите DJANGO_DEBUG=0 в .env"
fi

# Проверка SECRET_KEY
SECRET_KEY=$(docker compose -f docker-compose.yml -f docker-compose.vds.yml exec -T web python -c "import os; from dotenv import load_dotenv; load_dotenv(); print(os.getenv('DJANGO_SECRET_KEY', ''))")
if [ -z "$SECRET_KEY" ] || [ "$SECRET_KEY" = "change-me" ] || [ ${#SECRET_KEY} -lt 50 ]; then
    echo "⚠️  ВНИМАНИЕ: Установите сильный DJANGO_SECRET_KEY (50+ символов) в .env"
fi

# 6. Перезапуск контейнеров
echo "🔄 Перезапуск контейнеров..."
docker compose -f docker-compose.yml -f docker-compose.vds.yml up -d --build

echo "✅ Готово! Проверьте логи:"
echo "   docker compose -f docker-compose.yml -f docker-compose.vds.yml logs -f web"

