#!/bin/bash
# Проверка доступности https://crm.groupprofi.ru/health/ (или HEALTH_URL). При ошибке — уведомление в Telegram (если заданы TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID).
# Переменные: HEALTH_URL (default https://crm.groupprofi.ru/health/), TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
# Exit 0 — сервис доступен, 1 — недоступен.

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HEALTH_URL="${HEALTH_URL:-https://crm.groupprofi.ru/health/}"

if [ -f "$PROJECT_ROOT/.env" ]; then
  set -a
  . "$PROJECT_ROOT/.env"
  set +a
fi

code=$(curl -sI -o /dev/null -w "%{http_code}" --connect-timeout 15 "$HEALTH_URL" 2>/dev/null) || code=000

if [ "$code" -ge 200 ] && [ "$code" -lt 400 ]; then
  exit 0
fi

msg="Proficrm health_check: $HEALTH_URL -> HTTP $code (unavailable)"
echo "$msg" >&2

if [ -n "${TELEGRAM_BOT_TOKEN}" ] && [ -n "${TELEGRAM_CHAT_ID}" ]; then
  curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d "chat_id=${TELEGRAM_CHAT_ID}" \
    -d "text=${msg}" \
    -d "disable_web_page_preview=1" >/dev/null 2>&1 || true
fi

exit 1
