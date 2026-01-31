#!/bin/bash
# Настройка окружения стагинга: создаёт .env.staging из шаблона, подставляет сгенерированные ключи.
# Обязательно задайте POSTGRES_PASSWORD в .env.staging после запуска.
# Запуск: из корня репозитория (или из папки стагинга, если скрипт скопирован) — ./scripts/setup_staging_env.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if [ ! -f "env.staging.template" ]; then
    echo "Запустите скрипт из корня репозитория (где лежит env.staging.template)."
    exit 1
fi

echo "Настройка .env.staging для стагинга..."
if [ ! -f ".env.staging" ]; then
    cp env.staging.template .env.staging
    echo "Создан .env.staging из env.staging.template"
fi

python3 "$SCRIPT_DIR/setup_staging_env.py" 2>/dev/null || python "$SCRIPT_DIR/setup_staging_env.py" || {
    echo "Запустите вручную: python3 scripts/setup_staging_env.py"
    exit 1
}

echo ""
echo "Обязательно отредактируйте .env.staging и задайте:"
echo "  POSTGRES_PASSWORD=надёжный_пароль_для_БД_стагинга"
echo "При необходимости: SECURITY_CONTACT_EMAIL=ваш@email"
echo "Затем: ./deploy_staging.sh"
