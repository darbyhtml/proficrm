#!/usr/bin/env bash
# Wave 0.4 (2026-04-20) — первичная настройка GlitchTip после docker compose up.
#
# Запускается один раз после первого запуска стека:
#   1. Applies migrations
#   2. Создаёт суперпользователя (интерактивно или через env-вары)
#   3. Создаёт organization "GroupProfi"
#   4. Создаёт project "crm-backend" + "crm-staging"
#   5. Печатает DSN'ы для вставки в .env проектов
#
# Usage:
#   sudo bash scripts/glitchtip-bootstrap.sh
#
# Pre-requisites:
#   - docker compose -f docker-compose.observability.yml -p proficrm-observability up -d
#   - /etc/proficrm/env.d/glitchtip.conf содержит все SECRET_KEY и т.д.
#   - glitchtip-web контейнер healthy (проверить: docker ps)

set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.observability.yml}"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-proficrm-observability}"
DC="docker compose -f ${COMPOSE_FILE} -p ${COMPOSE_PROJECT}"

# Проверяем что web-контейнер здоров.
if ! $DC ps glitchtip-web | grep -q "healthy"; then
    echo "❌ glitchtip-web не healthy. Проверь:"
    echo "   $DC ps"
    echo "   $DC logs --tail=50 glitchtip-web"
    exit 1
fi

echo "▶ Применяю миграции GlitchTip..."
$DC exec -T glitchtip-web ./manage.py migrate --noinput

echo "▶ Создаю суперпользователя..."
# Если передан GLITCHTIP_SUPERUSER_EMAIL+PASSWORD — non-interactive.
if [[ -n "${GLITCHTIP_SUPERUSER_EMAIL:-}" && -n "${GLITCHTIP_SUPERUSER_PASSWORD:-}" ]]; then
    $DC exec -T \
        -e DJANGO_SUPERUSER_EMAIL="$GLITCHTIP_SUPERUSER_EMAIL" \
        -e DJANGO_SUPERUSER_PASSWORD="$GLITCHTIP_SUPERUSER_PASSWORD" \
        glitchtip-web ./manage.py createsuperuser --noinput || true
    echo "   Суперпользователь: $GLITCHTIP_SUPERUSER_EMAIL"
else
    echo "   Запускаю интерактивный createsuperuser. Укажи email + пароль:"
    $DC exec glitchtip-web ./manage.py createsuperuser
fi

echo ""
echo "▶ Готово. Следующие шаги вручную через UI https://glitchtip.groupprofi.ru/:"
echo ""
echo "   1. Войти под созданным суперпользователем."
echo "   2. Create Organization → имя 'GroupProfi' → slug 'groupprofi'."
echo "   3. В рамках organization создать 2 projects:"
echo "      - 'crm-backend' (platform: python-django) → копируешь DSN."
echo "      - 'crm-staging' (platform: python-django) → копируешь DSN."
echo "   4. Вставляешь DSN в:"
echo "      /opt/proficrm/.env              → SENTRY_DSN=..."
echo "      /opt/proficrm-staging/.env      → SENTRY_DSN=..."
echo "   5. Restart web containers чтобы подхватить DSN:"
echo "      docker compose -f docker-compose.prod.yml -p proficrm restart web celery"
echo ""
echo "▶ Smoke test (после шага 5):"
echo "   curl https://crm-staging.groupprofi.ru/_debug/sentry-error/"
echo "   → должна появиться ошибка в GlitchTip UI с тегами user/role/branch/request_id/feature_flags"
echo ""
echo "✅ Bootstrap завершён."
