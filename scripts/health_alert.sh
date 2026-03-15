#!/usr/bin/env bash
# Мониторинг здоровья CRM и алерты в Telegram.
# Запускается каждые 5 минут через crontab.
# Алерт отправляется только при смене статуса (up→down или down→up).
#
# Переменные окружения (из .env):
#   TG_BOT_TOKEN  — токен бота от @BotFather
#   TG_CHAT_ID    — chat_id куда слать сообщения
#   HEALTH_URL    — (опционально) URL для проверки, по умолчанию localhost

set -euo pipefail

HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8001/health/}"
STATE_FILE="/tmp/crm_health_state"
LOG_FILE="/var/log/crm_health_alert.log"

# Загружаем .env если переменные не заданы
if [ -z "${TG_BOT_TOKEN:-}" ] && [ -f "/opt/proficrm/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source /opt/proficrm/.env
    set +a
fi

if [ -z "${TG_BOT_TOKEN:-}" ] || [ -z "${TG_CHAT_ID:-}" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') ERROR: TG_BOT_TOKEN или TG_CHAT_ID не заданы" >> "$LOG_FILE"
    exit 1
fi

_send_tg() {
    local text="$1"
    curl -s -X POST "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
        -d "chat_id=${TG_CHAT_ID}" \
        -d "parse_mode=HTML" \
        --data-urlencode "text=${text}" \
        -o /dev/null
}

_log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"
}

# Предыдущее состояние (up/down), по умолчанию up
prev_state=$(cat "$STATE_FILE" 2>/dev/null || echo "up")

# Проверяем health endpoint
response=$(curl -s --max-time 8 "$HEALTH_URL" 2>/dev/null || echo "")
http_status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 "$HEALTH_URL" 2>/dev/null || echo "000")

if [ "$http_status" = "200" ] && echo "$response" | grep -q '"status": "ok"'; then
    current_state="up"
else
    current_state="down"
    # Извлекаем детали проблемы
    if [ "$http_status" = "000" ]; then
        detail="Сервер недоступен (нет ответа)"
    elif [ "$http_status" = "503" ]; then
        # Какой компонент упал
        detail=$(echo "$response" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    bad = [k + ': ' + v.get('detail', v.get('status','?')) for k,v in d.get('checks',{}).items() if v.get('status') not in ('ok','warning')]
    print(', '.join(bad) if bad else 'degraded')
except: print('HTTP 503')
" 2>/dev/null || echo "HTTP 503")
    else
        detail="HTTP $http_status"
    fi
fi

# Алерт только при смене состояния
if [ "$current_state" != "$prev_state" ]; then
    if [ "$current_state" = "down" ]; then
        msg="🔴 <b>CRM ПРОФИ — УПАЛ</b>

❌ ${detail}
🕐 $(date '+%d.%m.%Y %H:%M:%S')
🔗 https://crm.groupprofi.ru"
        _send_tg "$msg"
        _log "DOWN — $detail"
    else
        msg="🟢 <b>CRM ПРОФИ — ВОССТАНОВЛЕН</b>

✅ Все компоненты в норме
🕐 $(date '+%d.%m.%Y %H:%M:%S')
🔗 https://crm.groupprofi.ru"
        _send_tg "$msg"
        _log "UP — recovered"
    fi
fi

# Сохраняем текущее состояние
echo "$current_state" > "$STATE_FILE"
