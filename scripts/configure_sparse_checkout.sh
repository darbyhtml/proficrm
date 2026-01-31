#!/bin/bash
# Настроить sparse-checkout: в папке стагинга не будут подтягиваться прод-файлы при pull,
# в папке прода — стагинг-файлы. Запускать один раз в каждой папке после клона.
#
# Использование:
#   cd /opt/proficrm-staging && ./scripts/configure_sparse_checkout.sh staging
#   cd /opt/proficrm && ./scripts/configure_sparse_checkout.sh prod
#
# Требует Git 2.25+. После настройки при git pull в стагинге не появятся deploy_production.sh,
# docker-compose.prod.yml и т.д.; в проде — deploy_staging.sh, docker-compose.staging.yml и т.д.

set -e

MODE="${1:-}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if [ "$MODE" != "staging" ] && [ "$MODE" != "prod" ]; then
    echo "Использование: $0 staging | prod"
    echo "  staging — в этой папке при pull не будут подтягиваться прод-файлы"
    echo "  prod    — в этой папке при pull не будут подтягиваться стагинг-файлы"
    exit 1
fi

if [ ! -d ".git" ]; then
    echo "Запустите скрипт из корня репозитория (где есть .git)."
    exit 1
fi

echo "Настройка sparse-checkout для режима: $MODE"

git config core.sparseCheckout true
SPARSE_FILE=".git/info/sparse-checkout"
mkdir -p "$(dirname "$SPARSE_FILE")"

if [ "$MODE" = "staging" ]; then
    # В стагинге: включить всё, исключить прод-файлы
    cat > "$SPARSE_FILE" << 'SPARSE_EOF'
# Всё включено
/*
# Исключить прод-специфичные файлы (не использовать в папке стагинга)
!deploy_production.sh
!deploy_security.sh
!docker-compose.prod.yml
!docker-compose.vds.yml
!env.template
!scripts/promote_to_prod.sh
SPARSE_EOF
    echo "Исключены из рабочей копии: deploy_production.sh, deploy_security.sh, docker-compose.prod.yml, docker-compose.vds.yml, env.template, scripts/promote_to_prod.sh"
else
    # В проде: включить всё, исключить стагинг-файлы
    cat > "$SPARSE_FILE" << 'SPARSE_EOF'
# Всё включено
/*
# Исключить стагинг-специфичные файлы (не использовать в папке прода)
!deploy_staging.sh
!docker-compose.staging.yml
!env.staging.template
!scripts/setup_staging_env.sh
!scripts/setup_staging_env.py
SPARSE_EOF
    echo "Исключены из рабочей копии: deploy_staging.sh, docker-compose.staging.yml, env.staging.template, scripts/setup_staging_env.*"
fi

# Применить sparse-checkout (исключённые файлы исчезнут из рабочей копии)
if git rev-parse HEAD >/dev/null 2>&1; then
    git read-tree -mu HEAD 2>/dev/null || git sparse-checkout reapply 2>/dev/null || true
fi

echo "Готово. При следующих git pull исключённые файлы не появятся в этой папке."
echo "Чтобы отменить: git config core.sparseCheckout false && rm -f .git/info/sparse-checkout && git read-tree -mu HEAD"
