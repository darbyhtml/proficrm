#!/usr/bin/env bash
# Wave 0.4 SEV2 postmortem (2026-04-21): MANDATORY end-of-session check.
#
# Запускать после ЛЮБОГО docker compose up / build / restart на staging.
# Без зелёного прогона сессия считается неоконченной — либо fix, либо revert.
#
# Проверяет external-reachability через host nginx → staging nginx → web.
# Именно здесь проявлялся 502 из инцидента: web контейнер был healthy локально,
# но staging-nginx кэшировал DNS на старый IP пересозданного web'а.
#
# Usage:
#   tests/smoke/staging_post_deploy.sh
#   BASE_URL=... tests/smoke/staging_post_deploy.sh
#
# Exit 0 — все зелёные. Exit 1 — хоть одна проверка красная.

set -euo pipefail

BASE_URL="${BASE_URL:-https://crm-staging.groupprofi.ru}"
TIMEOUT="${TIMEOUT:-10}"
FAIL_COUNT=0

check() {
    local name="$1" url="$2" expected_regex="${3:-^200$}"
    local got
    got=$(curl -sSk -o /dev/null -w '%{http_code}' --max-time "$TIMEOUT" "$url" 2>/dev/null || echo "000")
    if [[ "$got" =~ $expected_regex ]]; then
        printf "%-30s  OK  (%s)\n" "$name" "$got"
    else
        printf "%-30s  FAIL (got %s, expected %s)\n" "$name" "$got" "$expected_regex"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
}

echo "=== Staging smoke starting ==="
echo "BASE_URL: $BASE_URL"
echo ""

# Critical: оба probe GET-уровня + health (backward compat)
check "Liveness /live/"   "$BASE_URL/live/"    "^200$"
check "Readiness /ready/" "$BASE_URL/ready/"   "^200$"
check "Health /health/"   "$BASE_URL/health/"  "^200$"

# Root and login (not IP-whitelisted only if from managers' IPs, so 302/403 ok)
check "Home"              "$BASE_URL/"                   "^(200|302|403)$"
check "Login"             "$BASE_URL/login/"             "^(200|403)$"

# API endpoint that should respond (auth required → 401/403 OK)
check "Feature flags API" "$BASE_URL/api/v1/feature-flags/" "^(401|403)$"

echo ""
if [[ $FAIL_COUNT -eq 0 ]]; then
    echo "=== Staging smoke OK — все проверки зелёные ==="
    exit 0
else
    echo "=== Staging smoke FAILED — $FAIL_COUNT проблем ==="
    echo ""
    echo "Вероятные причины 502 при failed /live/ /ready/ /health/:"
    echo "  1. Web container Up но staging-nginx кэширует DNS на старый IP."
    echo "     Fix: docker restart crm_staging_nginx"
    echo "  2. Web container crashing (waffle missing, migrations failed)."
    echo "     Fix: docker compose logs web + rebuild image"
    echo "  3. Celery crash-loop не ломает web, но sidekick-healthcheck red."
    echo "     Fix: docker compose build celery + up -d --force-recreate celery"
    echo ""
    echo "See docs/audit/incidents/2026-04-21-staging-502.md для полного playbook."
    exit 1
fi
