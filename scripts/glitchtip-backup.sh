#!/usr/bin/env bash
# Wave 0.4 (2026-04-20) — daily pg_dump бэкап БД GlitchTip.
#
# Запускается через cron:
#   /etc/cron.d/glitchtip-backup:
#     0 3 * * * root /opt/proficrm/scripts/glitchtip-backup.sh >> /var/log/glitchtip-backup.log 2>&1
#
# Retention: 30 последних дней. Старые дампы удаляются автоматически.
#
# В W10 этот скрипт перепишется на push в MinIO (proficrm-glitchtip-backup bucket).
# Пока — локально.

set -euo pipefail

BACKUP_DIR="/var/backups/glitchtip"
RETENTION_DAYS=30
COMPOSE_FILE="/opt/proficrm/docker-compose.observability.yml"
COMPOSE_PROJECT="proficrm-observability"

mkdir -p "$BACKUP_DIR"

DATE_TAG="$(date +%Y%m%d_%H%M%S)"
DUMP_FILE="$BACKUP_DIR/glitchtip_${DATE_TAG}.sql.gz"

echo "[$(date --iso-8601=s)] ▶ pg_dump glitchtip..."
docker compose -f "$COMPOSE_FILE" -p "$COMPOSE_PROJECT" \
    exec -T glitchtip-db pg_dump -U glitchtip -d glitchtip --format=plain --no-owner \
    | gzip -9 > "$DUMP_FILE"

DUMP_SIZE=$(du -h "$DUMP_FILE" | cut -f1)
echo "[$(date --iso-8601=s)] ✅ Backup saved: $DUMP_FILE ($DUMP_SIZE)"

# Удаляем старые бэкапы (старше $RETENTION_DAYS дней)
DELETED=$(find "$BACKUP_DIR" -name "glitchtip_*.sql.gz" -mtime +$RETENTION_DAYS -delete -print | wc -l)
if [[ $DELETED -gt 0 ]]; then
    echo "[$(date --iso-8601=s)] 🗑  Удалено старых бэкапов: $DELETED"
fi

# Проверка здоровья (минимум 1 KB сжатый дамп — значит что-то есть).
DUMP_BYTES=$(stat -c%s "$DUMP_FILE")
if [[ $DUMP_BYTES -lt 1024 ]]; then
    echo "[$(date --iso-8601=s)] ❌ ALERT: Dump слишком маленький ($DUMP_BYTES bytes) — проверь БД!"
    exit 1
fi

echo "[$(date --iso-8601=s)] Backup complete."
