# Runbook: восстановление GlitchTip из бэкапа

_Wave 0.4 (2026-04-20). Disaster recovery сценарий._

---

## Когда применять

- БД GlitchTip повреждена (ошибки PostgreSQL, corrupt data)
- Случайно удалены issues/events из UI
- Миграция на новый VPS
- Rollback после неудачного major-upgrade GlitchTip

---

## Быстрый чеклист

1. Остановить web + worker (не db)
2. Восстановить из последнего дампа
3. Запустить web + worker
4. Проверить UI + тестовую ошибку

---

## Шаг 1 — выбрать бэкап

Бэкапы в `/var/backups/glitchtip/glitchtip_YYYYMMDD_HHMMSS.sql.gz`.
Retention 30 дней (настроено в `glitchtip-backup.sh`).

```bash
ls -la /var/backups/glitchtip/ | tail -10
```

Берём свежий или нужную дату:
```bash
LATEST=$(ls -t /var/backups/glitchtip/glitchtip_*.sql.gz | head -1)
echo "Будем восстанавливать: $LATEST"
```

Проверить содержимое (не пустой, structured):
```bash
gunzip -c "$LATEST" | head -40
# Должны быть CREATE TABLE ..., COPY ... FROM stdin
```

---

## Шаг 2 — остановить GlitchTip web + worker (не db!)

```bash
cd /opt/proficrm-observability
docker compose -f docker-compose.observability.yml \
    -p proficrm-observability \
    --env-file /etc/proficrm/env.d/glitchtip.conf \
    stop glitchtip-web glitchtip-worker

# Проверить что db ещё жива:
docker compose -f docker-compose.observability.yml \
    -p proficrm-observability ps glitchtip-db
# Должен быть Up (healthy)
```

---

## Шаг 3 — pre-restore бэкап текущего состояния

**Важно**: перед destructive restore сделать свежий дамп текущей БД — на случай
если restore сломается или придётся откатить.

```bash
SAFETY_DUMP="/var/backups/glitchtip/pre_restore_$(date +%Y%m%d_%H%M%S).sql.gz"
docker compose -f /opt/proficrm-observability/docker-compose.observability.yml \
    -p proficrm-observability \
    --env-file /etc/proficrm/env.d/glitchtip.conf \
    exec -T glitchtip-db pg_dump -U glitchtip -d glitchtip --format=plain --no-owner \
    | gzip -9 > "$SAFETY_DUMP"
echo "Pre-restore backup: $SAFETY_DUMP"
```

---

## Шаг 4 — удалить текущую БД и создать пустую

```bash
# Подключаемся к postgres-контейнеру от имени superuser (внутри контейнера
# postgres user по умолчанию — тот что создан POSTGRES_USER=glitchtip).
docker compose -f /opt/proficrm-observability/docker-compose.observability.yml \
    -p proficrm-observability \
    --env-file /etc/proficrm/env.d/glitchtip.conf \
    exec -T glitchtip-db psql -U glitchtip -d postgres <<'SQL'
DROP DATABASE IF EXISTS glitchtip;
CREATE DATABASE glitchtip OWNER glitchtip;
SQL
```

---

## Шаг 5 — восстановить из dump

```bash
gunzip -c "$LATEST" | docker compose \
    -f /opt/proficrm-observability/docker-compose.observability.yml \
    -p proficrm-observability \
    --env-file /etc/proficrm/env.d/glitchtip.conf \
    exec -T glitchtip-db psql -U glitchtip -d glitchtip
```

Ожидаем длительный вывод — `CREATE TABLE`, `COPY N`, `CREATE INDEX`.
При ошибках — смотри лог + используй `--on-error-stop=0` чтобы увидеть всё.

---

## Шаг 6 — запустить web + worker обратно

```bash
docker compose -f /opt/proficrm-observability/docker-compose.observability.yml \
    -p proficrm-observability \
    --env-file /etc/proficrm/env.d/glitchtip.conf \
    up -d glitchtip-web glitchtip-worker

# Подождать health-start (60 сек):
sleep 70
docker ps --filter name=proficrm-observability --format 'table {{.Names}}\t{{.Status}}'
```

---

## Шаг 7 — verify

```bash
# 1. HTTP endpoint отвечает
curl -sI https://glitchtip.groupprofi.ru/_health/ | head -2
# HTTP/2 200

# 2. UI заходится (залогиниться)
# https://glitchtip.groupprofi.ru/

# 3. Issues на месте
# В UI → Organization GroupProfi → Project crm-backend → Issues
# Должны быть старые ошибки (если были до повреждения).

# 4. Отправить тестовую ошибку через smoke endpoint
curl https://crm-staging.groupprofi.ru/_debug/sentry-error/
# Через 5-30 секунд — новая issue в GlitchTip UI.
```

---

## Что делать если restore сломался

**Сценарий**: шаг 5 упал с ошибками — БД частично восстановлена, GlitchTip
не стартует.

```bash
# 1. Повторно DROP + CREATE + восстановить из pre-restore дампа (см. $SAFETY_DUMP)
docker compose -f /opt/proficrm-observability/docker-compose.observability.yml \
    -p proficrm-observability \
    --env-file /etc/proficrm/env.d/glitchtip.conf \
    exec -T glitchtip-db psql -U glitchtip -d postgres <<SQL
DROP DATABASE IF EXISTS glitchtip;
CREATE DATABASE glitchtip OWNER glitchtip;
SQL

gunzip -c "$SAFETY_DUMP" | docker compose \
    -f /opt/proficrm-observability/docker-compose.observability.yml \
    -p proficrm-observability \
    --env-file /etc/proficrm/env.d/glitchtip.conf \
    exec -T glitchtip-db psql -U glitchtip -d glitchtip

# 2. Запустить контейнеры
docker compose -f /opt/proficrm-observability/docker-compose.observability.yml \
    -p proficrm-observability \
    --env-file /etc/proficrm/env.d/glitchtip.conf \
    up -d
```

Это откатит к состоянию ДО restore-попытки.

---

## Тренировочный restore (раз в месяц)

Рекомендация: прогонять restore-drill раз в месяц чтобы быть уверенным что
бэкапы работают.

```bash
# Создать временный namespace:
mkdir -p /tmp/glitchtip-drill
# Поднять отдельный postgres для теста:
docker run --rm -d --name drill-pg -e POSTGRES_PASSWORD=drill postgres:16-alpine
sleep 5
# Восстановить туда:
LATEST=$(ls -t /var/backups/glitchtip/glitchtip_*.sql.gz | head -1)
docker exec -i drill-pg psql -U postgres -c 'CREATE DATABASE drill;'
gunzip -c "$LATEST" | docker exec -i drill-pg psql -U postgres -d drill

# Проверить COUNT'ы:
docker exec drill-pg psql -U postgres -d drill -c "SELECT COUNT(*) FROM django_migrations;"
docker exec drill-pg psql -U postgres -d drill -c "\dt"

# Стоп:
docker stop drill-pg
```

Если `django_migrations` содержит >50 записей и `\dt` показывает таблицы —
бэкап валиден.

---

## Связанные документы

- `docs/runbooks/glitchtip-setup.md` — первичная установка.
- `scripts/glitchtip-backup.sh` — скрипт ежедневного бэкапа.
- `/etc/cron.d/glitchtip-backup` — расписание.
