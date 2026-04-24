#!/bin/bash
# Очистка папки стагинга: убрать прод-файлы и копии, чтобы не путаться.
# Запускать из папки стагинга: cd /opt/proficrm-staging && ./scripts/cleanup_for_staging.sh
# Можно вызывать после git pull или вручную.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

echo "Очистка папки стагинга (crm-staging.groupprofi.ru)..."

# .env всегда берём из .env.staging (Compose подставляет из .env)
if [ -f ".env.staging" ]; then
    cp .env.staging .env
    echo "  .env обновлён из .env.staging"
fi

# Удалить случайно скопированные прод-файлы (если они не из репо, а копии)
# Не удаляем отслеживаемые файлы — при следующем pull они всё равно будут. Используйте configure_sparse_checkout.sh для исключения.
for f in .env.prod env.prod; do
    if [ -f "$f" ]; then
        rm -f "$f"
        echo "  Удалён $f (прод-копия)"
    fi
done

echo "В стагинге используйте только: deploy_staging.sh, docker-compose.staging.yml. Чтобы при pull не появлялись прод-файлы: ./scripts/configure_sparse_checkout.sh staging"
