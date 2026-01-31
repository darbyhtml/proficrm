#!/bin/bash
# Напоминание: перед продом нужно протестировать на стагинге.
# Запускать из папки ПРОДА: cd /opt/proficrm && ./scripts/promote_to_prod.sh
# Либо вручную: cd /opt/proficrm && git pull origin main && ./deploy_security.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROD_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROD_ROOT"

if [ ! -f "deploy_security.sh" ]; then
    echo "Запустите скрипт из корня проекта (папка прода): cd /opt/proficrm && ./scripts/promote_to_prod.sh"
    exit 1
fi

echo "Прод (crm.groupprofi.ru). Перед выкаткой на прод нужно проверить изменения на стагинге (crm-staging.groupprofi.ru)."
echo ""
read -p "Стагинг уже протестирован? Выкатить на прод? (y/N): " confirm
if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
    echo "Отменено. Сначала задеплойте на стагинг и протестируйте: cd /opt/proficrm-staging && git pull && ./deploy_staging.sh"
    exit 0
fi

echo ">>> git pull origin main"
git pull origin main
echo ">>> ./deploy_security.sh"
./deploy_security.sh
echo "Готово. Проверьте: https://crm.groupprofi.ru"
