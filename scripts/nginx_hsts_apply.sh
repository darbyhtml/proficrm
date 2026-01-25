#!/bin/bash
# Применяет HSTS snippet из nginx/snippets/hsts.conf.template в /etc/nginx/snippets/proficrm-hsts.conf.
# Переменная HSTS_HEADER (по умолчанию max-age=3600).
# Использование:
#   SAFE:  HSTS_HEADER="max-age=3600" ./scripts/nginx_hsts_apply.sh
#   STRICT: HSTS_HEADER="max-age=31536000; includeSubDomains" ./scripts/nginx_hsts_apply.sh
# После: sudo nginx -t && sudo systemctl reload nginx

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$PROJECT_ROOT/nginx/snippets/hsts.conf.template"
OUT="/etc/nginx/snippets/proficrm-hsts.conf"

HSTS_HEADER="${HSTS_HEADER:-max-age=3600}"

if [ ! -f "$TEMPLATE" ]; then
    echo "ERROR: Template not found: $TEMPLATE" >&2
    exit 1
fi

sudo mkdir -p /etc/nginx/snippets

# Предупреждение при STRICT (includeSubDomains): поддомены должны иметь HTTPS
if printf '%s' "$HSTS_HEADER" | grep -q 'includeSubDomains'; then
    echo "WARNING: HSTS STRICT (includeSubDomains) — убедитесь, что ВСЕ поддомены crm.groupprofi.ru имеют валидный HTTPS."
    echo "         Иначе браузеры будут блокировать к ним доступ." >&2
fi

export HSTS_HEADER
envsubst '${HSTS_HEADER}' < "$TEMPLATE" | sudo tee "$OUT" > /dev/null
echo "Written: $OUT (HSTS_HEADER=$HSTS_HEADER)"

if sudo nginx -t 2>/dev/null; then
    echo "nginx -t OK. Для применения: sudo systemctl reload nginx"
else
    echo "nginx -t failed. Проверьте конфиг." >&2
    exit 1
fi
