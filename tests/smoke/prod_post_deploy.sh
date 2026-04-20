#!/usr/bin/env bash
# Prod post-deploy smoke tests — Wave 0.4 (2026-04-20).
# Fail-fast на любой регрессии критичных публичных путей.
#
# Usage:
#   tests/smoke/prod_post_deploy.sh                               # → crm.groupprofi.ru
#   BASE_URL=https://crm-staging.groupprofi.ru tests/smoke/prod_post_deploy.sh
#
# Exit 0 — все зелёные. Exit 1 — хоть один красный.
#
# Расширяется по мере развития продукта (каждая волна может добавить
# свой критичный путь). Для W0.4 — базовый набор: liveness/readiness,
# главные страницы, static, API schema, feature-flags auth gate.

set -euo pipefail

BASE_URL="${BASE_URL:-https://crm.groupprofi.ru}"
TIMEOUT="${TIMEOUT:-10}"

FAIL_COUNT=0

check() {
    local name="$1" url="$2" expected="${3:-200}"
    local got
    got=$(curl -sSk -o /dev/null -w '%{http_code}' --max-time "$TIMEOUT" "$url" 2>/dev/null || echo "000")
    if [[ "$got" == "$expected" ]]; then
        printf "%-45s  OK  (%s)\n" "$name" "$got"
    else
        printf "%-45s  FAIL (got %s, want %s)\n" "$name" "$got" "$expected"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
}

echo "=== Prod post-deploy smoke ==="
echo "BASE_URL: $BASE_URL"
echo ""

# 1. Infrastructure probes (базовые — процесс + зависимости)
check "Liveness /live/"       "$BASE_URL/live/"   200
check "Readiness /ready/"     "$BASE_URL/ready/"  200
check "Legacy /health/"       "$BASE_URL/health/" 200

# 2. Публичный UI (unauthenticated)
check "Home → login redirect" "$BASE_URL/"                 302
check "Login page"            "$BASE_URL/login/"           200
check "robots.txt"            "$BASE_URL/robots.txt"       200
check "security.txt"          "$BASE_URL/.well-known/security.txt"  200

# 3. Static assets (after collectstatic)
check "Static favicon"        "$BASE_URL/static/ui/favicon-v2.svg"  200

# 4. API endpoints (должны отвечать — даже если auth required)
check "API schema"            "$BASE_URL/api/schema/"                200
check "Feature flags (401)"   "$BASE_URL/api/v1/feature-flags/"      401
check "JWT token (405)"       "$BASE_URL/api/token/"                 405   # GET not allowed, POST yes
check "Widget bootstrap"      "$BASE_URL/api/widget/bootstrap/"      400   # needs token param

# 5. Admin (доступен, но требует login)
check "Django admin login"    "$BASE_URL/django-admin/login/"        200

# 6. TLS
TLS_EXPIRES=$(curl -skI --max-time 5 "$BASE_URL" 2>/dev/null | grep -i 'strict-transport' | head -1 | tr -d '\r' || true)
if [[ -n "$TLS_EXPIRES" ]]; then
    printf "%-45s  OK  (HSTS present)\n" "TLS HSTS header"
else
    printf "%-45s  WARN (no HSTS header)\n" "TLS HSTS header"
fi

echo ""
if [[ $FAIL_COUNT -eq 0 ]]; then
    echo "=== Smoke OK — все проверки зелёные ==="
    exit 0
else
    echo "=== Smoke FAILED — $FAIL_COUNT критичных проблем ==="
    echo ""
    echo "Rollback procedure: docs/runbooks/prod-deploy.md §«Step 6»"
    exit 1
fi
