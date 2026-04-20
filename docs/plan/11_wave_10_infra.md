# Волна 10. Инфраструктура и DevOps

**Цель волны:** Поднять надёжность эксплуатации до уровня, когда «сервер умер посреди ночи» = «откатился за 15 минут на резервный VPS», а «данные повреждены неделю назад» = «восстановили через WAL-G PITR». Установить полный observability stack.

**Параллелизация:** в отдельном worktree — полностью автономна. Можно запускать с самого начала параллельно с W0/W1.

**Длительность:** 10–14 рабочих дней.

**Принцип:** **только self-hosted и free-tier**. Никаких платных подписок. Платим только за хостинг (тот же VPS, что и CRM, или соседний VPS для MinIO/replica).

**Требования:** Доступ root/sudo на staging и prod. Желательно второй VPS той же/похожей конфигурации для hot-standby и/или MinIO (если бюджет позволяет). Если второго VPS нет — MinIO поднимается на том же сервере, hot-standby — опционально (warm-standby через rsync на второй дешёвый VPS или даже на домашнюю машину).

---

## Внутренний порядок внутри волны (строго)

Шаги имеют жёсткие зависимости:

```
10.1 MinIO (S3) bucket + IAM                          ← фундамент
    ↓
10.2 WAL-G setup + archive_command → MinIO          ← зависит от 10.1
    ↓
10.3 Media миграция Django → MinIO                   ← зависит от 10.1
    ↓
10.4 Hot/warm standby Postgres                        ← зависит от 10.2 (WAL-G)
    ↓
10.5 Prometheus + Grafana + Loki + Alertmanager      ← параллельно с 10.3–10.4
    ↓
10.6 GlitchTip polish (release tracking, performance) ← после 10.5 (нужны alerts)
    ↓
10.7 Backup validation (restore drill еженедельно)   ← зависит от 10.2, 10.4
    ↓
10.8 DR runbook (финальный)                           ← все
    ↓
10.9 CI/CD upgrade                                    ← может быть раньше, независим
```

**Нельзя менять порядок 10.1→10.2→10.3.** 10.5 можно параллелить с 10.3–10.4. 10.9 можно делать в любой момент.

---

## Этап 10.1. MinIO self-hosted + bucket setup

### Контекст
Сейчас media в `/app/backend/media/` через Docker volume на одном сервере. Риски: (a) нет избыточности, (b) объём растёт, (c) при потере сервера — потеря всех файлов. Yandex Object Storage / VK Cloud платные по объёму и трафику. MinIO self-hosted — бесплатно, S3-совместимое, работает на том же или соседнем VPS.

### Цель
Поднять MinIO, получить S3-endpoint + ключи, настроить buckets для media и WAL-G.

### Что делать
1. **Выбор места установки**:
   - **Вариант A (рекомендуемый):** соседний VPS (даже недорогой — от 300 ₽/мес), чтобы не конкурировать за ресурсы с CRM и быть устойчивым к падению основного сервера.
   - **Вариант B (если бюджет только основной VPS):** тот же сервер, отдельный Docker volume на отдельном диске (если есть) или на том же. SPOF — да, но лучше чем ничего. В DR runbook (10.7) будет отдельный пункт про восстановление MinIO.

2. **Установка MinIO**:
   ```yaml
   # docker-compose.minio.yml
   services:
     minio:
       image: minio/minio:latest
       restart: always
       command: server /data --console-address ":9001"
       environment:
         MINIO_ROOT_USER: ${MINIO_ROOT_USER}
         MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
       ports:
         - "9000:9000"  # S3 API
         - "9001:9001"  # Web console
       volumes:
         - minio-data:/data
       healthcheck:
         test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
         interval: 30s
   volumes:
     minio-data:
       driver: local
       driver_opts:
         type: none
         device: /mnt/minio-data  # отдельный диск если есть
         o: bind
   ```

3. **nginx reverse-proxy** с TLS:
   - `s3.groupprofi.ru` → MinIO:9000 (S3 API)
   - `minio-console.groupprofi.ru` → MinIO:9001 (Web UI, basic auth или VPN-only)
   - TLS через Certbot (Let's Encrypt free)

4. **Buckets**:
   ```bash
   # Через mc (MinIO client)
   mc alias set proficrm https://s3.groupprofi.ru $MINIO_ROOT_USER $MINIO_ROOT_PASSWORD
   mc mb proficrm/proficrm-media-prod
   mc mb proficrm/proficrm-media-staging
   mc mb proficrm/proficrm-walg-prod         # для WAL-G archives
   mc mb proficrm/proficrm-walg-staging
   mc mb proficrm/proficrm-glitchtip-backup   # для БД GlitchTip
   # Versioning на media (защита от случайного удаления)
   mc version enable proficrm/proficrm-media-prod
   mc version enable proficrm/proficrm-media-staging
   # Lifecycle: старые версии → удалить через 90 дней
   mc ilm add --expired-object-delete-marker --noncurrent-expire-days 90 proficrm/proficrm-media-prod
   ```

5. **IAM — отдельные пользователи с минимальными правами**:
   ```bash
   # User для Django media
   mc admin user add proficrm django-media $DJANGO_MEDIA_SECRET
   mc admin policy attach proficrm readwrite --user django-media
   # Ограничить только своим bucket:
   # Создать policy proficrm-media-prod-rw.json и attached к django-media
   # User для WAL-G
   mc admin user add proficrm walg $WALG_SECRET
   # Policy только на walg bucket
   ```

6. **Мониторинг MinIO** (integrated в 10.4):
   - Prometheus endpoint: `http://minio:9000/minio/v2/metrics/cluster` (нужен bearer token).
   - Алерты: disk usage > 80%, bucket size spike, failed requests.

7. **Backup MinIO data** (важно — это наш источник правды для media и WAL-G):
   - Вариант: `mc mirror` на второй сервер или внешний диск (rsync over SSH).
   - Частота: еженедельно.
   - В 10.2 на втором VPS будет hot-standby Postgres, туда же можно и MinIO mirror.

### Инструменты
- `mcp__context7__*` — MinIO docs
- `Bash`, `mc` CLI, `curl`, `openssl`

### Definition of Done
- [ ] MinIO работает на `https://s3.groupprofi.ru` с TLS
- [ ] 5 buckets созданы: media-prod, media-staging, walg-prod, walg-staging, glitchtip-backup
- [ ] 2 IAM-пользователя с narrow policies: `django-media`, `walg`
- [ ] Versioning + lifecycle на media-buckets
- [ ] MinIO console защищён (basic auth или VPN)
- [ ] Backup MinIO данных на второй носитель еженедельно (cron)
- [ ] Endpoint, credentials сохранены в password manager + в `.env.example` с placeholder-значениями

### Артефакты
- `docker-compose.minio.yml`
- `scripts/minio-setup.sh` — идемпотентная настройка buckets и users
- `configs/minio-media-policy.json` — IAM policy
- `configs/minio-walg-policy.json`
- `configs/nginx/s3.conf`, `minio-console.conf`
- `scripts/minio-mirror-backup.sh` + cron entry
- `docs/runbooks/minio-setup.md`
- `docs/runbooks/minio-restore.md`

### Валидация
```bash
curl -I https://s3.groupprofi.ru/minio/health/live
# 200 OK

mc ls proficrm/  # пять buckets
mc admin user list proficrm  # django-media, walg

# Test S3 API from Python
python -c "
import boto3
s3 = boto3.client('s3', endpoint_url='https://s3.groupprofi.ru',
    aws_access_key_id='django-media', aws_secret_access_key='...')
s3.put_object(Bucket='proficrm-media-staging', Key='test.txt', Body=b'hello')
print(s3.list_objects_v2(Bucket='proficrm-media-staging'))
"
```

### Откат
```bash
docker compose -f docker-compose.minio.yml down -v  # v удалит volume
# или без -v если данные нужны
```

### Обновить в документации
- `docs/runbooks/minio-setup.md`
- `docs/decisions.md`: ADR-019 «MinIO self-hosted вместо managed S3»
- `docs/architecture.md`: раздел «Object Storage»

---

## Этап 10.2. WAL-G PITR в MinIO

### Контекст
Сейчас бэкапы Postgres — `scripts/backup_postgres.sh` (pg_dump, локально). Это bronze-tier: восстановление только до момента дампа (RPO = интервал дампа, обычно 24ч). WAL-G даёт PITR — восстановление на любой момент (RPO ≈ 1 минута).

**Зависит от 10.1** — bucket `proficrm-walg-prod` должен существовать.

### Цель
PostgreSQL archives + base backups в MinIO. PITR возможен для любой минуты последних 30 дней.

### Что делать
1. **Установка WAL-G** на prod и staging DB-хостах:
   ```bash
   # Скачать бинарь
   wget https://github.com/wal-g/wal-g/releases/download/v3.0.3/wal-g-pg-ubuntu-22.04-amd64.tar.gz
   tar xf wal-g-*.tar.gz -C /usr/local/bin/
   ```

2. **Конфиг** `/etc/wal-g/walg.env`:
   ```bash
   WALG_S3_PREFIX=s3://proficrm-walg-prod/postgres
   AWS_ENDPOINT=https://s3.groupprofi.ru
   AWS_ACCESS_KEY_ID=walg
   AWS_SECRET_ACCESS_KEY=...
   AWS_S3_FORCE_PATH_STYLE=true
   WALG_COMPRESSION_METHOD=brotli
   WALG_DELTA_MAX_STEPS=6
   PGHOST=localhost
   PGDATABASE=proficrm
   PGUSER=proficrm
   ```

3. **postgresql.conf**:
   ```
   archive_mode = on
   archive_command = 'envdir /etc/wal-g/walg.env /usr/local/bin/wal-g wal-push %p'
   archive_timeout = 60  # архив минимум раз в минуту
   wal_level = replica
   max_wal_senders = 10
   ```
   Reload Postgres.

4. **Full base backup** еженедельно через Celery beat или cron:
   ```bash
   # /etc/cron.d/proficrm-walg
   0 2 * * 0 postgres envdir /etc/wal-g/walg.env /usr/local/bin/wal-g backup-push /var/lib/postgresql/16/main
   ```

5. **Retention policy**:
   ```bash
   # Еженедельно — удалить backup'ы старше 30 дней
   0 3 * * 0 postgres envdir /etc/wal-g/walg.env /usr/local/bin/wal-g delete retain FULL 4 --confirm
   ```
   Оставляем 4 недели × полный backup + все WAL между ними.

6. **Мониторинг** (в 10.4):
   - Prometheus: `pg_last_wal_push_age_seconds` < 120.
   - Алерт: если > 300 → WAL-G сломан.

7. **Restore runbook** — step-by-step процедура:
   - Восстановить latest: `wal-g backup-fetch /var/lib/postgresql/16/main LATEST`
   - PITR на конкретное время: настройка `recovery_target_time` в `postgresql.auto.conf`
   - Стартовать Postgres, проверить.

### Инструменты
- `mcp__context7__*` — WAL-G docs
- `Bash`, `pg_basebackup` (для сравнения)

### Definition of Done
- [ ] WAL-G установлен на prod и staging
- [ ] `archive_command` работает, WAL-файлы появляются в MinIO минутно
- [ ] Еженедельный base backup работает (проверено 2 цикла)
- [ ] Retention работает (старые удаляются)
- [ ] `wal-g backup-list` показывает backups
- [ ] **Restore drill на staging**: взяли prod-backup, восстановили на staging, cross-проверили данные — успех
- [ ] Runbook `docs/runbooks/postgres-restore.md` с PITR процедурой

### Артефакты
- `/etc/wal-g/walg.env` (в password manager, не в git)
- `configs/postgresql.conf` (изменения)
- `scripts/walg-base-backup.sh`
- `/etc/cron.d/proficrm-walg`
- `docs/runbooks/walg-setup.md`
- `docs/runbooks/postgres-restore.md`
- `docs/runbooks/postgres-pitr.md`

### Валидация
```bash
# Check WAL push
envdir /etc/wal-g/walg.env wal-g wal-verify timeline

# List backups
envdir /etc/wal-g/walg.env wal-g backup-list

# Simulate restore on staging (см. runbook)
```

### Откат
```bash
# Отключить archiving
# postgresql.conf: archive_mode = off
systemctl reload postgresql
# Backups в MinIO остаются — можно использовать если что
```

### Обновить в документации
- `docs/runbooks/walg-setup.md`
- `docs/decisions.md`: ADR-020 «WAL-G для PITR в MinIO»
- `docs/architecture.md`: Backup strategy

---

## Этап 10.3. Media миграция Django → MinIO

### Контекст
Media в `/app/backend/media/` через Docker volume. Нужно перевести на MinIO.

**Зависит от 10.1** — bucket `proficrm-media-prod` должен существовать.

### Цель
Zero-downtime миграция media в MinIO без потери файлов.

### Что делать
1. **django-storages**:
   - `pip install django-storages boto3`.
   - `STORAGES` (Django 5+) backend: `storages.backends.s3.S3Storage`.
   - Конфиг (в `settings/production.py`):
     ```python
     STORAGES = {
         "default": {
             "BACKEND": "backend.core.storage.DualWriteStorage",  # кастомный на период миграции
         },
         "staticfiles": {
             "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
         },
     }
     AWS_S3_ENDPOINT_URL = env('MINIO_ENDPOINT')  # https://s3.groupprofi.ru
     AWS_ACCESS_KEY_ID = env('MINIO_MEDIA_KEY')  # django-media
     AWS_SECRET_ACCESS_KEY = env('MINIO_MEDIA_SECRET')
     AWS_STORAGE_BUCKET_NAME = env('MINIO_MEDIA_BUCKET')  # proficrm-media-prod
     AWS_S3_ADDRESSING_STYLE = 'path'  # MinIO требует path-style
     AWS_DEFAULT_ACL = None
     AWS_QUERYSTRING_AUTH = True  # presigned URLs
     AWS_QUERYSTRING_EXPIRE = 3600
     ```

2. **Dual-write период (48 часов)**:
   - Custom `DualWriteStorage` — пишет и в local, и в MinIO.
   - Reads: prefer MinIO, fallback на local + auto-upload в MinIO при чтении (healing).
   - Monitoring: сколько read'ов пришло из local (если > 0 после 48h — что-то недомигрировано).

3. **Migration script** `scripts/migrate_media_to_minio.py`:
   - Walks через existing `media/`.
   - Uploads с `--dry-run` опцией.
   - Manifest: files + checksums + statuses.
   - Idempotent: re-run safe.
   - Progress bar (tqdm).

4. **Switch reads**:
   - После успешной миграции + 48h dual-write — переключить на MinIO-only read (STORAGES → `storages.backends.s3.S3Storage`).
   - Удалить local media через месяц (страховка, на случай найденных проблем).

5. **Presigned URLs**:
   - Для приватного контента (attachments, user avatars) — presigned с expiration 1 час.
   - Для публичного (widget images) — public bucket или CDN-подобный cache в nginx.

6. **CDN-подобный nginx-cache** (вместо Cloudflare CDN — опционально, бесплатно):
   - Для часто запрашиваемого media (widget logo, static images в письмах) — nginx proxy_cache перед MinIO.
   - Уменьшает нагрузку на MinIO и latency.

### Инструменты
- `mcp__context7__*` — django-storages docs
- `Bash` — `mc cp`, `mc diff`, проверка миграции

### Definition of Done
- [ ] django-storages подключён, `DualWriteStorage` работает
- [ ] Dual-write работает 48h без ошибок (логи чистые)
- [ ] Migration script прогнан на staging, manifest чистый
- [ ] Migration script прогнан на prod
- [ ] `mc diff` между local и MinIO — 0 diffs
- [ ] Reads переключены на MinIO-only
- [ ] Presigned URLs работают для приватных файлов
- [ ] E2E тест: создать Company, прикрепить файл, выйти, войти, скачать — успех
- [ ] Old files удалены с сервера через 30 дней (после бэкапа)

### Артефакты
- `scripts/migrate_media_to_minio.py`
- `scripts/migration-manifest-YYYY-MM-DD.json`
- `backend/core/storage.py` — `DualWriteStorage`
- `docs/runbooks/media-migration.md`
- `docs/runbooks/minio-operations.md`

### Валидация
```bash
python scripts/migrate_media_to_minio.py --dry-run
python scripts/migrate_media_to_minio.py --execute
mc diff backend/media/ proficrm/proficrm-media-prod/
# После — upload test file через CRM, download — проверить что файл в MinIO
```

### Откат
```bash
# 1. Switch reads обратно на local (feature flag MEDIA_READ_FROM=local)
# 2. Старые файлы остаются в local пока не удалены — можно прочитать
# 3. MinIO bucket оставить — не ломается
```

### Обновить в документации
- `docs/runbooks/media-migration.md`
- `docs/decisions.md`: ADR-021 «Django media в MinIO»

---

## Этап 10.4. Postgres hot/warm-standby replica

### Контекст
WAL-G уже настроен (10.2). Есть PITR — можно восстановиться куда угодно. Но восстановление занимает от 30 минут. Для снижения RTO нужен standby — реплика, которая либо принимает нагрузку моментально (hot), либо быстро промоутится (warm).

**Зависит от 10.2** — WAL-G уже работает.

**Компромисс по бюджету**: если второго VPS нет совсем — этот этап можно **отложить** в V2 и ограничиться быстрым восстановлением из WAL-G (30 мин RTO вместо 5 мин).

### Цель
- **RPO** ≤ 1 минуты (через WAL archiving + streaming replication).
- **RTO** ≤ 5–30 минут (в зависимости от выбранного варианта).

### Что делать
1. **Выбор уровня HA** (зависит от бюджета на второй VPS):

   | Вариант | Второй VPS | RTO | Сложность |
   |---|---|---|---|
   | **A. Hot standby** (streaming replication) | Аналогичной мощности | 5 мин (manual promote) | Средняя |
   | **B. Warm standby** (WAL-G restore) | Минимальный, достаточно диска | 15–30 мин | Низкая |
   | **C. Только WAL-G** (без standby) | Не нужен | 30–60 мин | — |

   Рекомендую **B** — самый экономный: второй VPS за 300–500 ₽/мес только для hotwarm standby + MinIO mirror.

2. **Для варианта A (hot standby)**:
   - VPS с аналогичными ресурсами.
   - WireGuard-tunnel между серверами.
   - `pg_basebackup` → standby.
   - Streaming replication: primary `max_wal_senders=10`, `wal_keep_size=1GB`.
   - Standby: `hot_standby=on`, `primary_conninfo=...`.
   - Async replication (lag ~1 сек допустим).

3. **Для варианта B (warm standby)**:
   - Второй VPS с Postgres в `standby_mode=on`.
   - `restore_command = 'wal-g wal-fetch %f %p'` — получает WAL из MinIO.
   - Lag может быть до 1 минуты (частота archive_timeout).
   - Плюс: не требует прямой связности между серверами, всё идёт через MinIO.

4. **Read-replica использование** (только для варианта A):
   - Отдельный Django database alias `replica` для тяжёлых read-only операций (аналитика, reports).
   - Router: analytics queries → replica, остальное → primary.
   - Careful: replication lag может дать stale reads; для critical path — primary.

5. **Failover drill**:
   - Ручная процедура promote standby → primary при падении primary.
   - Runbook с пошаговыми командами.
   - Тестовый drill раз в квартал.

6. **Monitoring** (из 10.4):
   - `pg_stat_replication` — lag графики.
   - `pg_last_wal_receive_lsn()` vs `pg_last_wal_replay_lsn()` на standby.
   - Alert: replication lag > 60 сек.

### Инструменты
- `mcp__context7__*` — PostgreSQL replication docs
- `Bash` — все команды через SSH
- `pg_basebackup`, `wal-g`

### Definition of Done
- [ ] Выбранный вариант (A или B) реализован
- [ ] Standby работает, lag < 60 сек
- [ ] Failover drill — успешный (на staging)
- [ ] Runbook `postgres-failover.md` с пошаговыми командами
- [ ] Alert на replication lag настроен

### Артефакты
- `/etc/postgresql/16/main/postgresql.conf` (обновлённый) — primary
- `/etc/postgresql/16/main/postgresql.conf` — standby
- `scripts/failover-to-standby.sh`
- `docs/runbooks/postgres-failover.md`
- `docs/runbooks/postgres-standby-setup.md`

### Валидация
```bash
# On primary:
psql -c "SELECT pg_is_in_recovery()"  # false
psql -c "SELECT * FROM pg_stat_replication"  # есть standby

# On standby:
psql -c "SELECT pg_is_in_recovery()"  # true
psql -c "SELECT now() - pg_last_xact_replay_timestamp()"  # lag
```

### Откат
Standby можно оставить (read-only). Ничего не ломается.

### Обновить в документации
- `docs/runbooks/postgres-failover.md`
- `docs/decisions.md`: ADR-022 «Postgres HA вариант <A|B|C>»

---

## Этап 10.5. Prometheus + Grafana + Loki + Alertmanager

### Контекст
Нет централизованного monitoring. Логи в журнале systemd, метрик нет. GlitchTip показывает **ошибки**, но не **метрики**. Нужен полный стек observability, self-hosted.

### Цель
Поднять стек observability (весь self-hosted, free): Prometheus (metrics), Grafana (dashboards), Loki (logs), Alertmanager (алертинг).

### Что делать
1. **Stack deployment**:
   - Docker Compose с Prometheus, Grafana, Loki, Promtail (log collector), Alertmanager.
   - На том же сервере если запас CPU/RAM (~1 ГБ доп. памяти) или на соседнем «ops» VPS — на том же, что и MinIO/standby.
   - nginx reverse-proxy: `grafana.groupprofi.ru` с TLS, basic auth ИЛИ только через VPN.

2. **Exporters**:
   - `node_exporter` на каждом сервере (CPU, RAM, disk, network).
   - `postgres_exporter` (pg metrics).
   - `redis_exporter` (redis metrics).
   - `nginx_exporter` или `nginx-prometheus-exporter` (requests, errors).
   - `celery-exporter` (queue depth, task rates).
   - Django middleware `django-prometheus` (HTTP metrics, DB queries, cache hits).
   - MinIO built-in Prometheus endpoint.
   - **GlitchTip** — нет нативного Prometheus exporter, но можно тянуть stats через API.

3. **Dashboards** (в Grafana, dashboards as code):
   - **System Health**: CPU, RAM, disk, load per host.
   - **Django Performance**: request rate, p95/p99, error rate, active users, top slow endpoints.
   - **Postgres**: connections, slow queries, deadlocks, replication lag, cache hit ratio.
   - **Redis**: memory, ops/s, hit ratio, slowlog.
   - **Celery**: queue depth per queue, task duration, failure rate.
   - **Policy Engine**: denied requests per role per hour (**критично для W2 preconditions**).
   - **Business KPI**: active users now, deals created today, revenue this month, calls made today, emails sent today.
   - **MinIO**: bucket sizes, requests, errors.

4. **Loki + Promtail**:
   - Все Docker logs → Loki.
   - Structured logs (Wave 0.4) — queryable в Grafana Explore.
   - Retention: 30 дней full fidelity.

5. **Alertmanager**:
   - Routing: critical → Telegram + email, warning → email only.
   - Telegram bot (через @BotFather, бесплатно) — твой рабочий канал.
   - Alert rules:
     - Disk > 80%.
     - Memory > 90% > 5 min.
     - Postgres replication lag > 60s.
     - Error rate spike (> 5%/min за последние 10 мин).
     - Celery queue backlog > 1000.
     - Response p95 > 3s > 5 min.
     - WAL-G не push-ил файлы > 5 мин.
     - MinIO недоступен.
     - **Policy denied-request spike (для W2 мониторинга)** — > 20 deny/мин.

6. **Runbooks**:
   - Каждый alert имеет runbook URL → `docs/runbooks/alerts/<alert-name>.md`.
   - В runbook: что значит, как диагностировать, как починить, к кому эскалировать.

### Инструменты
- `mcp__context7__*` — Prometheus, Grafana, Loki docs
- `Docker`, `docker compose`

### Definition of Done
- [ ] Prometheus + Grafana + Loki + Alertmanager развёрнуты self-hosted
- [ ] 8 dashboards работают, данные приходят
- [ ] 9+ alert rules настроены
- [ ] Telegram-бот шлёт critical alerts
- [ ] Runbook для каждого alert
- [ ] Retention настроен (30 дней logs, Prometheus 30 дней, downsampled 1 год)
- [ ] Grafana доступ только через VPN или basic auth
- [ ] **Dashboard «Policy denied requests per role per hour»** готов для W2

### Артефакты
- `deploy/ops/docker-compose.yml` (Prometheus/Grafana/Loki/Alertmanager/Promtail)
- `deploy/ops/prometheus/prometheus.yml`
- `deploy/ops/grafana/provisioning/` (dashboards as code)
- `deploy/ops/alertmanager/alertmanager.yml`
- `deploy/ops/alerts/*.yml`
- `docs/runbooks/alerts/*.md`
- `docs/ops/observability.md`

### Валидация
```bash
# Grafana UI: все dashboards зелёные
# Prometheus targets: все up
# Trigger test alert: убить redis на 2 минуты → получить в Telegram
curl -s http://prometheus:9090/api/v1/targets | jq '.data.activeTargets[] | select(.health=="down")'
# должно быть пусто
```

### Откат
Ops stack — отдельный Compose, можно выключить без влияния на prod.

### Обновить в документации
- `docs/ops/observability.md`
- `docs/decisions.md`: ADR-023 «Prometheus+Grafana+Loki self-hosted»

---

## Этап 10.6. GlitchTip advanced features (polish)

### Контекст
В W0.4 подняли GlitchTip как self-hosted Sentry-замену. Здесь — довести до production-grade: release tracking, source maps, performance monitoring, ownership.

### Цель
Полная настройка GlitchTip с release tracking, performance, ownership.

### Что делать
1. **Release tracking**:
   - В CI: `sentry-cli --url https://glitchtip.groupprofi.ru releases new <version> && set-commits --auto && finalize`.
   - Deploy hook: после каждого deploy — `sentry-cli releases deploys <version> new -e production`.
   - Errors связываются с релизом → легко понять, какой коммит сломал.

2. **Performance Monitoring** (GlitchTip поддерживает менее детально чем Sentry, но основное есть):
   - `traces_sample_rate=0.1`.
   - Custom spans для тяжёлых операций (bulk updates, exports, campaigns).
   - Transaction filtering в `before_send_transaction`.

3. **Source maps** (если используется минификация frontend JS):
   - `sentry-cli --url https://glitchtip... sourcemaps upload`.
   - В CI после npm run build.

4. **Issue Owners** (GlitchTip ownership ограничен):
   - Простой эквивалент — tag `component` на каждое событие через `before_send`.
   - Routing алертов по component → email/Telegram.

5. **Alert rules**:
   - New unhandled issue → Telegram critical.
   - Error rate spike > 3x baseline → Telegram critical.
   - Regression (issue reopened) → email.

6. **GlitchTip БД бэкап**:
   - pg_dump GlitchTip БД раз в день → MinIO bucket `proficrm-glitchtip-backup`.
   - Retention 30 дней.

### Definition of Done
- [ ] Release tracking работает в CI
- [ ] Performance traces видны
- [ ] Source maps загружаются
- [ ] Alert rules настроены, протестированы
- [ ] GlitchTip БД бэкапится daily

### Артефакты
- `.github/workflows/deploy.yml` (обновлён — sentry-cli releases)
- `scripts/glitchtip-backup.sh` + cron
- `docs/ops/glitchtip.md`

### Валидация
```bash
sentry-cli --url https://glitchtip.groupprofi.ru info
# Trigger error — проверить, что release + source map есть в web UI
```

### Откат
Никакого — это polish для уже работающего GlitchTip.

### Обновить в документации
- `docs/ops/glitchtip.md`

---

## Этап 10.7. Backup validation automation

### Контекст
Бэкапы существуют, но если не проверять — «бэкап Шрёдингера». Регулярно (еженедельно) — автоматически восстанавливаем в sandbox и проверяем.

### Цель
Automated weekly restore drill.

### Что делать
1. **Sandbox env** для restore:
   - Отдельная VM (или Docker env на ops-сервере).
   - Изолирована от prod, но имеет доступ к backup storage.

2. **Weekly Celery beat task** или cron на ops-сервере:
   - Скрипт:
     1. Pick latest base backup from WAL-G.
     2. Restore to sandbox.
     3. Replay WAL up to latest.
     4. Run validation queries (counts, checksums, sanity checks).
     5. Cleanup sandbox.
     6. Report to Grafana + Telegram.

3. **Validation queries**:
   - `SELECT COUNT(*) FROM auth_user` — больше 0.
   - `SELECT MAX(created_at) FROM companies_company` — в последние 24h.
   - `SELECT SUM(amount) FROM companies_deal WHERE status='won'` — разумное число.
   - Sample: fetch random 10 companies — без errors.

4. **Alert**:
   - Если drill не прошёл → critical alert.

### Definition of Done
- [ ] Drill automatic каждое воскресенье
- [ ] Validation queries passing
- [ ] Alert при failure
- [ ] История drills в Grafana

### Артефакты
- `scripts/backup-validation-drill.sh`
- `scripts/validation-queries.sql`
- `docs/runbooks/backup-drill.md`

### Валидация
```bash
# Manually run:
bash scripts/backup-validation-drill.sh
```

### Откат
Можно отключить cron.

### Обновить в документации
- `docs/runbooks/backup-drill.md`

---

## Этап 10.8. Disaster Recovery runbook

### Контекст
В случае полной потери prod сервера — что делать?

### Цель
Runbook, по которому соло-админ восстанавливает prod за 60 минут.

### Что делать
1. **Scenario 1**: prod сервер упал.
   - Если есть standby: promote standby → DNS switch.
   - Если standby тоже нет: spin up new VPS → restore from WAL-G.

2. **Scenario 2**: Postgres corruption.
   - Stop Postgres.
   - Restore from backup (PITR).
   - Resume replication from primary (если primary жив).

3. **Scenario 3**: hacked / ransomware.
   - Isolate: stop all services.
   - Rotate all secrets (DB passwords, Redis, JWT signing keys, SMTP creds).
   - Restore from pre-incident backup.
   - Forensic: check access logs.
   - Notify users.

4. **Scenario 4**: DC down для провайдера.
   - Plan for migration to secondary provider (Selectel / VK Cloud).
   - DNS TTL снижать до 60с на время инцидента.

5. **Communication plan**:
   - Внутренняя группа в Telegram.
   - Status page (простая статическая страница на Cloudflare Pages).
   - Notification template для юзеров.

6. **Practice**:
   - Tabletop exercise раз в квартал.

### Definition of Done
- [ ] 4 DR scenarios documented
- [ ] Tabletop exercise проведён
- [ ] Runbook опубликован с access для альтернативного лица (доверенного)
- [ ] Secrets rotation план

### Артефакты
- `docs/runbooks/disaster-recovery.md`
- `docs/runbooks/incident-response.md`
- `docs/runbooks/secrets-rotation.md`

### Валидация
Walk-through runbook с timer. Должно занимать ≤ 60 мин в теории.

### Откат
N/A.

### Обновить в документации
- `docs/runbooks/disaster-recovery.md`

---

## Этап 10.9. CI/CD upgrade

### Контекст
GitHub Actions есть: ci.yml + deploy-staging.yml. Prod deploy — ручной. Нет required checks, нет blue-green, нет smoke tests after deploy.

### Цель
Улучшить pipeline.

### Что делать
1. **Required checks** на main branch:
   - ruff / black / mypy / pytest / coverage / bandit / pip-audit / gitleaks / playwright-smoke — все required.
   - Merge queue (GitHub Merge Queue) — for conflict prevention.

2. **Auto-deploy staging** on main merge — уже есть. Оставить.

3. **Prod deploy**:
   - Manual trigger с approvals (2 reviewers для высоко-risk changes).
   - Tagged releases (`v1.2.3`).
   - Automated release notes (из commit history).

4. **Blue-green deploy**:
   - Для prod: запустить новую версию в параллель → switch nginx upstream → kill старую.
   - Zero-downtime.
   - Rollback — быстрый switch обратно.

5. **Post-deploy smoke**:
   - После deploy — automated smoke test через Playwright.
   - Если failure — auto-rollback + alert.

6. **DB migrations**:
   - Pre-deploy: `python manage.py migrate --plan` (check what will change).
   - Migrations запускаются до switch traffic.
   - Long migrations (> 5s) — предупреждение reviewer'у.

### Definition of Done
- [ ] Required checks установлены
- [ ] Merge queue работает
- [ ] Prod deploy с approvals
- [ ] Blue-green работает, tested
- [ ] Post-deploy smoke + auto-rollback
- [ ] Migrations workflow

### Артефакты
- `.github/workflows/deploy-prod.yml`
- `.github/CODEOWNERS`
- `scripts/blue-green-deploy.sh`
- `scripts/post-deploy-smoke.sh`
- `docs/ops/deployment.md`

### Валидация
Manual: deploy bogus version, убедиться что smoke ловит, rollback работает.

### Откат
```bash
bash scripts/rollback.sh <previous-version>
```

### Обновить в документации
- `docs/ops/deployment.md`

---

## Checklist завершения волны 10

- [ ] MinIO self-hosted работает, 5 buckets с versioning + IAM
- [ ] WAL-G PITR работает, архивы в MinIO, retention 30 дней
- [ ] Media мигрированы в MinIO, dual-write fade-out завершён
- [ ] Postgres standby (вариант A/B/C) работает, failover drill успешен
- [ ] Prometheus + Grafana + Loki + Alertmanager + 8 dashboards + 9+ alerts
- [ ] GlitchTip polish: release tracking, performance, source maps, бэкап БД
- [ ] Backup validation drill автоматический еженедельный
- [ ] DR runbook полный, проверен учением
- [ ] CI/CD pipeline с blue-green + auto-rollback
- [ ] **Dashboard «Policy denied requests»** готов для W2 preconditions
- [ ] Вся ops-инфраструктура self-hosted, платных подписок нет

**После этого** — инфраструктура готова к финальной нагрузке в Wave 14 (QA).
