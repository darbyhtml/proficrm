#!/bin/bash
# Smoke-check одной командой. Запускать с хоста из корня проекта. Ожидаются поднятые сервисы.
# Переменные: SMOKE_URL (default https://crm.groupprofi.ru), COMPOSE (default docker compose -f docker-compose.prod.yml).
# Exit 0 только если ВСЕ проверки PASS.

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

SMOKE_URL="${SMOKE_URL:-https://crm.groupprofi.ru}"
COMPOSE="${COMPOSE:-docker compose -f docker-compose.prod.yml}"

if [ -f ".env" ]; then
  set -a
  . ./.env
  set +a
fi

PASS=0
FAIL=0

_ok() { echo "  PASS: $1"; PASS=$((PASS+1)); }
_fail() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

echo "=== 1) HTTPS доступен ==="
if code=$(curl -sI -o /dev/null -w "%{http_code}" --connect-timeout 10 "$SMOKE_URL" 2>/dev/null); then
  if [ "$code" -ge 200 ] && [ "$code" -lt 400 ]; then
    _ok "HTTPS $SMOKE_URL -> $code"
  else
    _fail "HTTPS $SMOKE_URL -> $code (ожидаем 2xx/3xx)"
  fi
else
  _fail "HTTPS $SMOKE_URL — соединение не удалось"
fi

echo "=== 2) HSTS присутствует ==="
if curl -sI --connect-timeout 10 "$SMOKE_URL" 2>/dev/null | grep -qi "Strict-Transport-Security"; then
  _ok "Strict-Transport-Security в ответе"
else
  _fail "Strict-Transport-Security отсутствует"
fi

echo "=== 3) Security headers (X-Content-Type-Options, X-Frame-Options, Referrer-Policy) ==="
H=$(curl -sI --connect-timeout 10 "$SMOKE_URL" 2>/dev/null)
h1=$(echo "$H" | grep -ci "X-Content-Type-Options" || true)
h2=$(echo "$H" | grep -ci "X-Frame-Options" || true)
h3=$(echo "$H" | grep -ci "Referrer-Policy" || true)
if [ "${h1:-0}" -ge 1 ] && [ "${h2:-0}" -ge 1 ] && [ "${h3:-0}" -ge 1 ]; then
  _ok "X-Content-Type-Options, X-Frame-Options, Referrer-Policy на месте"
else
  _fail "Не все заголовки: X-Content-Type-Options=$h1 X-Frame-Options=$h2 Referrer-Policy=$h3"
fi

echo "=== 4) web не root (uid 1000) ==="
if out=$($COMPOSE exec -T web id 2>/dev/null); then
  if echo "$out" | grep -q "uid=1000"; then
    _ok "web uid=1000"
  else
    _fail "web не uid=1000: $out"
  fi
else
  _fail "web exec id не удался (контейнер запущен?)"
fi

echo "=== 5) gunicorn в Cmd контейнера web ==="
WEB_ID=$($COMPOSE ps -q web 2>/dev/null)
CMD=$(docker inspect -f '{{join .Config.Cmd " "}}' "$WEB_ID" 2>/dev/null || true)
if echo "$CMD" | grep -q "gunicorn"; then
  _ok "gunicorn запущен (Cmd: $CMD)"
else
  _fail "gunicorn не найден в web (Cmd: $CMD)"
fi

echo "=== 6) fail-fast (пустой DJANGO_SECRET_KEY -> exit 1, ERROR) ==="
set +e
out=$($COMPOSE run --rm -e DJANGO_SECRET_KEY= web python -c "pass" 2>&1); ex=$?
set -e
if [ "$ex" = "1" ] && echo "$out" | grep -q "ERROR: DJANGO_SECRET_KEY is required"; then
  _ok "fail-fast: exit 1 и ERROR DJANGO_SECRET_KEY"
else
  _fail "fail-fast: ожидали exit 1 и ERROR DJANGO_SECRET_KEY (exit=$ex)"
fi

echo "=== 7) cap_drop ALL (web, celery, celery-beat) ==="
cap_ok=1
for svc in web celery celery-beat; do
  cid=$($COMPOSE ps -q "$svc" 2>/dev/null) || true
  if [ -z "$cid" ]; then
    _fail "cap_drop: сервис $svc не найден"
    cap_ok=0
    break
  fi
  cap=$(docker inspect "$cid" --format '{{.HostConfig.CapDrop}}' 2>/dev/null) || true
  if echo "$cap" | grep -q "ALL"; then
    : "ok $svc"
  else
    _fail "cap_drop: $svc -> $cap (ожидаем ALL)"
    cap_ok=0
    break
  fi
done
if [ "$cap_ok" = "1" ]; then
  _ok "cap_drop ALL у web, celery, celery-beat"
fi

echo "---"
echo "PASS: $PASS  FAIL: $FAIL"
if [ "$FAIL" -eq 0 ]; then
  echo "Smoke-check: OK"
  exit 0
else
  echo "Smoke-check: FAIL"
  exit 1
fi
