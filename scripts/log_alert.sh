#!/usr/bin/env bash
# Мониторинг ошибок в логах Docker-контейнеров CRM.
# Собирает ERROR/CRITICAL строки за последние N минут и отправляет батчем в Telegram.
# Запускается каждые 15 минут через crontab.
#
# Переменные окружения (из .env):
#   TG_BOT_TOKEN  — токен бота
#   TG_CHAT_ID    — chat_id
#   LOG_INTERVAL  — (опционально) интервал проверки в минутах, по умолчанию 15

set -euo pipefail

LOG_INTERVAL="${LOG_INTERVAL:-15}"
LOG_FILE="/opt/proficrm/logs/log_alert.log"
CONTAINERS=("proficrm-web-1" "proficrm-celery-1" "proficrm-celery-beat-1")

mkdir -p "$(dirname "$LOG_FILE")"

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

# Собираем ошибки из всех контейнеров
all_errors=""

for container in "${CONTAINERS[@]}"; do
    # Проверяем что контейнер запущен
    if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${container}$"; then
        continue
    fi

    # Читаем логи за последние N минут, фильтруем ERROR/CRITICAL
    errors=$(docker logs "$container" --since "${LOG_INTERVAL}m" 2>&1 \
        | grep -E " (ERROR|CRITICAL) " \
        | grep -v "health_check\|_hc\|healthcheck" \
        | tail -20 \
        || true)

    if [ -n "$errors" ]; then
        short_name="${container#proficrm-}"   # убираем префикс proficrm-
        short_name="${short_name%-1}"          # убираем суффикс -1
        all_errors="${all_errors}
<b>[${short_name}]</b>
<code>${errors}</code>"
    fi
done

# Если ошибок нет — молчим
if [ -z "$all_errors" ]; then
    exit 0
fi

# Обрезаем до лимита Telegram (4096 символов с запасом на заголовок)
header="⚠️ <b>CRM ПРОФИ — ОШИБКИ В ЛОГАХ</b>
🕐 $(date '+%d.%m.%Y %H:%M:%S')
"
max_body=$((3800 - ${#header}))
if [ ${#all_errors} -gt $max_body ]; then
    all_errors="${all_errors:0:$max_body}
<i>...обрезано</i>"
fi

msg="${header}${all_errors}"
_send_tg "$msg"
echo "$(date '+%Y-%m-%d %H:%M:%S') Отправлено $(echo "$all_errors" | wc -l) строк ошибок" >> "$LOG_FILE"
