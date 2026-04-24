#!/bin/bash
# Очистка папки прода: убрать стагинг-файлы и копии, чтобы не путаться.
# Запускать из папки прода: cd /opt/proficrm && ./scripts/cleanup_for_prod.sh
# Можно вызывать после git pull или вручную.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

echo "Очистка папки прода (crm.groupprofi.ru)..."

# Удалить .env.staging, если он случайно скопирован сюда (чтобы не перепутать с .env прода)
if [ -f ".env.staging" ]; then
    rm -f .env.staging
    echo "  Удалён .env.staging (используйте только .env для прода)"
fi

for f in .env.staging.copy env.staging; do
    if [ -f "$f" ]; then
        rm -f "$f"
        echo "  Удалён $f"
    fi
done

echo "В проде используйте только: deploy_security.sh или deploy_production.sh, docker-compose.prod.yml + vds.yml. Чтобы при pull не появлялись стагинг-файлы: ./scripts/configure_sparse_checkout.sh prod"
