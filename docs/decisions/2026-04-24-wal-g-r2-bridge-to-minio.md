# ADR: WAL-G PITR через Cloudflare R2 как bridge к MinIO

**Date:** 2026-04-24.
**Status:** Accepted.
**Deciders:** Дмитрий (owner), PM-planner (technical counsel).
**Supersedes:** — (новое решение).
**Superseded-by:** — (actual на момент записи).

---

## Контекст

Wave 10 master plan (`docs/plan/11_wave_10_infra.md`) определяет строгий порядок:

```
10.1 MinIO (S3) bucket + IAM          ← фундамент
10.2 WAL-G setup → MinIO              ← зависит от 10.1
10.3 Media миграция → MinIO           ← зависит от 10.1
```

«Нельзя менять порядок 10.1→10.2→10.3» (прямая цитата плана).

Принцип wave 10: **«только self-hosted и free-tier»** — никаких платных S3 подписок.

### Текущее состояние (2026-04-24 audit)

- `scripts/backup_postgres.sh` — daily `pg_dump` (bronze-tier, RPO = 24h, SPOF = VPS).
- `scripts/glitchtip-backup.sh` — GlitchTip DB бэкап.
- Нет WAL-G, нет `archive_command`, нет MinIO.
- VPS memory tight — `docker-compose.observability.yml` комментарий: «swap уже 1 GB».

### Business driver

Pre-W9 prod deploy rehearsal требует **solid backup strategy**. 333+ коммитов drift между main и prod → eventual accumulated deploy критичен. Recovery window 24h (pg_dump) неприемлем для этого объёма.

### Problem statement

**WAL-G hard-зависит от S3-compat endpoint.** MinIO полный setup = 5-7h + secondary VPS rental (~300₽/месяц) или memory pressure на основном VPS. Time-to-PITR становится 8-12h минимум.

Нужен faster path.

---

## Решение

**Использовать Cloudflare R2 как временный S3-совместимый backend для WAL-G.** После MinIO deployment (future W10.1 proper session) — мигрировать WAL-G на MinIO.

Сессия переименована в **W10.2-early** (honest naming) вместо «W10.1» — чтобы не создавать confusion с master plan numbering.

### Specifics

- Bucket: `proficrm-walg-staging` на R2.
- Endpoint: `https://<account>.r2.cloudflarestorage.com`.
- Credentials: R2 access key + secret в `.env.staging` / systemd env-file (не hardcoded).
- Retention: `wal-g delete retain FULL 4 --confirm` (4 недели).
- PostgreSQL: `archive_mode = on`, `archive_command = 'envdir /etc/wal-g/walg.env wal-g wal-push %p'`, `archive_timeout = 60`.

---

## Альтернативы рассмотрены

### A. Master plan strict: 10.1 MinIO → 10.2 WAL-G

- **Плюсы:** правильный порядок, zero migration cost, MinIO сразу покрывает media + WAL-G + GlitchTip backup buckets.
- **Минусы:** 8-12h time-to-PITR, secondary VPS ≈ 300₽/мес или memory pressure (swap уже 1 GB на основном VPS).
- **Вердикт:** отложено. Вернёмся к этому через W10.1 proper session.

### B. Cloudflare R2 now, MinIO migration later → **ВЫБРАНО**

- **Плюсы:** time-to-PITR 4-6h, R2 = 10 GB free forever, no egress fees, WAL-G vendor-agnostic (любой S3-compat).
- **Минусы:** R2 = Cloudflare third-party (нарушает формальный принцип «only self-hosted» wave 10), ~2-3h migration cost когда MinIO дойдёт.
- **Вердикт:** выбрано с explicit acknowledgement deviation и migration plan.

### C. Local FS WAL-G (`WALG_FILE_PREFIX=file:///backups`)

- **Плюсы:** 1-2h setup, zero external dependency.
- **Минусы:** SPOF (тот же VPS как основа), почти нулевое улучшение над pg_dump, backup теряется при потере VPS.
- **Вердикт:** rejected. Нивелирует смысл WAL-G.

---

## Rationale

**Почему B > A:**

1. **Time-to-PITR critical** — pre-W9 deploy rehearsal близко. 4-6h vs 8-12h важно для темпа.
2. **R2 free tier достаточен.** Staging PostgreSQL ~5 GB + WAL archives месяц ≤ 10 GB = внутри free tier.
3. **No egress fees** — restore drills не генерят расходов.
4. **WAL-G vendor-agnostic** — migration к MinIO = env-file change + fresh full backup. Нет lock-in.

**Почему B > C:**

- Local FS не решает SPOF. Цель WAL-G — recovery после потери VPS. Local FS не даёт.

**Почему «W10.2-early» а не «W10.1»:**

- Master plan numbering оставляем неизменным для других участников / future sessions.
- «Early» маркер явно указывает «10.2 done before 10.1» — surprise на будущий audit не возникнет.

---

## Consequences

### Positive

- PITR capability на staging в день decision (2026-04-24).
- Pre-W9 deploy rehearsal unblocked (backup strategy silver-tier, RPO ≈ 1 минута).
- Bronze → silver transition без waiting на MinIO.

> **⚠️ Correction 2026-04-24 10:25 UTC:** исходная версия ADR утверждала, что `scripts/backup_postgres.sh` daily pg_dump остаётся как fallback для staging — defense-in-depth (Pattern 3). Executor Step 0 audit (W10.2-early, 2026-04-24 10:10 UTC) обнаружил, что cron настроен **только для prod-директории, не для staging**. Для staging defense-in-depth **отсутствовал** до pivot session. Lesson для PM: ADR claims должны быть verified через audit actual state, не assumed cross-environment parity.
>
> **Резолюция:** Дмитрий выбрал pivot B — mini-session «staging pg_dump cron setup» (15-30 min) выполняется перед resume W10.2-early. После mini-session defense-in-depth restore'ен на staging. Hotlist item: `docs/audit/hotlist.md` → «staging pg_dump cron».
>
> **✅ Update 2026-04-24 10:55 UTC — defense-in-depth restored for staging:**
>
> - `scripts/backup_postgres_staging.sh` создан (коммит `4da1c4e7`).
> - `/etc/cron.d/proficrm-staging-backup` активен на VPS стейджинга, ежедневно 03:30 UTC, retention 7 дней.
> - Первый ручной запуск прошёл за 59 секунд, дамп 201 МБ сжат / 1.54 ГБ несжат, 90 таблиц, валидный заголовок.
> - Smoke-тест 6/6 зелёных после настройки.
> - Пункт хотлиста «staging pg_dump cron setup» закрыт.
>
> Теперь защитный слой на стейджинге есть: при любой проблеме с WAL-G `archive_command` откат через последний ежедневный pg_dump возможен (RPO до 24 часов). Это удовлетворяет Pattern 3 (defense-in-depth) из playbook §7.

### Negative

- Master plan order deviation — зафиксирована здесь, видна будущему audit'у.
- Double work ~2-3h при MinIO migration:
  - Новый `WALG_S3_PREFIX`.
  - Fresh full base backup.
  - Parallel run 7 days (R2 retention + MinIO archives).
  - Cut-over archive_command.
  - Decommission R2 bucket через 30 days retention window.
- R2 = vendor dependency до MinIO migration.
- Cloudflare account management (API token rotation, billing если превысит free tier).

### Neutral

- WAL-G config structure identical для R2 и MinIO (оба S3-compat). Только env-file меняется.

---

## Migration plan (WAL-G R2 → MinIO)

**Триггер:** future W10.1 proper session deploys MinIO (estimated after W10.5 Prometheus stack — нужен `node_exporter` + MinIO Prometheus endpoint).

**Steps:**

1. **New env-file** `/etc/wal-g/walg-minio.env` с MinIO endpoint/bucket/creds.
2. **Parallel run** 7 days:
   - PostgreSQL продолжает push в R2 через текущий `archive_command`.
   - Вручную (cron или Celery beat) — daily `wal-g backup-push` через новый env-file → MinIO.
3. **New full base backup** на MinIO через новый env-file.
4. **Cut-over:** reload `/etc/wal-g/walg.env` → MinIO endpoint. PostgreSQL archive_command автоматически новый endpoint.
5. **Verify 24h:** `pg_stat_archiver` archived_count растёт в MinIO bucket, R2 bucket стабилен.
6. **R2 decommission:**
   - Wait 30 days (retention window для старых WAL archives).
   - Delete R2 bucket `proficrm-walg-staging`.
   - Remove Cloudflare R2 account (если не используется для других нужд).
7. **Update docs:** `docs/runbooks/2026-04-24-wal-g-pitr.md` → rename в `docs/runbooks/wal-g-pitr.md` с MinIO-only instructions.
8. **Update этого ADR:** `Status: Superseded-by: docs/decisions/<future-date>-wal-g-migration-to-minio.md`.

**Migration cost:** 2-3h активной работы + 7 дней monitoring parallel run.

---

## References

- Master plan: `docs/plan/11_wave_10_infra.md` §10.1 MinIO, §10.2 WAL-G.
- Pre-session audit: `docs/pm/current-context.md` commit `1cbf7686`.
- Hotlist item (future W10.1 proper): `docs/audit/hotlist.md` секция «W10.1 proper MinIO setup».
- Cloudflare R2 free tier: <https://developers.cloudflare.com/r2/pricing/>.
- WAL-G docs: <https://wal-g.readthedocs.io/>.
- Related: `docs/decisions/2026-04-21-defer-prod-deploy-to-w9.md` (Path E — staging-only).
- Runbook (actual implementation): `docs/runbooks/2026-04-23-wal-g-pitr.md`.

---

## Actual Implementation (updated 2026-04-23)

Original plan: `wal-g` в db container → R2 прямо через `archive_command`.

Actual deployed: **host-pivot architecture** после discovery container TLS
issue (см. §Known Issues ниже):

- `archive_command` в контейнере: `cp %p /wal-spool/` (atomic через `.tmp` rename).
- `/var/lib/proficrm-staging/wal-spool/` — host-mounted в db container (UID 999).
- Host cron каждую минуту (`/etc/cron.d/proficrm-walg-spool`): `wal-g wal-push <oldest>`
  из spool в R2, удаление файла после successful push.
- First base backup + weekly retention: `docker-compose.walg-backup.yml` с
  `network_mode: host` + bind mount `/etc/ssl/certs:/etc/ssl/certs:ro` (см. §Known Issues).
- Restore drill: `docker-compose.walg-drill.yml` аналогичной конфигурации (на порту 5433).
- Port exposure: `docker-compose.staging.yml` expose staging postgres на
  `127.0.0.1:15432:5432` (5432 занят prod postgres на том же VPS — см. §Known Issue #2).

Документация: `docs/runbooks/2026-04-23-wal-g-pitr.md`.

### Evidence первого успешного flow (2026-04-23)

- **WAL archiving:** первый WAL `000000010000009B000000DA.br` в R2 `wal_005/`
  через 1 минуту после `pg_switch_wal()`. `pg_stat_archiver`: `failed_count=0`.
- **Base backup:** `base_000000010000009B000000E0`, 5.70 GB uncompressed →
  1.01 GB (brotli), upload 1 мин 6 сек, exit=0.
- **Restore drill:** `backup-fetch LATEST` 14 сек, WAL replay (3 segments)
  2.68 сек, `pg_is_in_recovery=f`. Row counts primary vs drill:
  `accounts_user=40`, `companies_company=45711`, `audit_activityevent=1671354`
  — **100% match**, `MAX(audit_activityevent.created_at)` identical до microseconds.

### Commits

- `4da1c4e7` — pg_dump safety net (pre-req).
- `4e6186e4` — docker-compose mounts (spool + port).
- `abaa31d9` — host cron push script.
- `0c0b8c17` — port fix `127.0.0.1:15432:5432` (5432 conflict с prod).
- `a13bf9a6` — helper compose (backup + drill) + host fallback script.

---

## Known Issues

### 1. Container HTTPS к Cloudflare R2 — TLS certificate trust (не networking)

**Discovered:** 2026-04-23 (в процессе W10.2-early Фаза 3.2).

**Evidence:** `wal-g` binary внутри `postgres:16` container (Debian 12 bookworm):

- Любой HTTPS call к `https://<account>.r2.cloudflarestorage.com` возвращает
  `x509: certificate signed by unknown authority`.
- То же `wal-g` binary на хосте Ubuntu 24.04: работает идеально.
- `st ls`, `wal-push`, `backup-push` — все блокируются на TLS handshake (но
  `backup-push` зависает весь процесс на 27 мин через Go runtime retry'и
  futex'ов прежде чем сообщить ошибку наружу — симптом выглядел как networking hang).

**Root cause:** Debian 12 CA bundle в postgres:16 image не trust'ит Cloudflare's
certificate chain. Host Ubuntu 24.04 `/etc/ssl/certs/ca-certificates.crt` (3610
entries) trust'ит (проверено прямым host-level `wal-g st ls wal_005/`).

**Workaround 1 (deployed — host-pivot):** `archive_command` в контейнере делает
trivial `cp` в shared spool. Host cron запускает `wal-g` (видит host CA bundle).
Обходит проблему полностью для continuous WAL archiving.

**Workaround 2 (verified в 3.2, deployed для full backup и drill):** bind mount
`/etc/ssl/certs` с хоста в container. Позволяет in-container `wal-g wal-push`
работать. Используется в `docker-compose.walg-backup.yml` (Sunday 02:00 UTC
retention cron) и `docker-compose.walg-drill.yml` (manual restore drills).

**Когда выбирать:**

- **Host-pivot** (текущий choice для continuous WAL archiving) — простой debug,
  изоляция от container state, archive_command путь остаётся trivial.
- **CA mount** — для helper compose operations (backup + drill). Кандидат
  как основной approach при MinIO migration (internal endpoint).

**Future investigation:** почему Debian 12 CA bundle не trust'ит Cloudflare —
stale bundle? missing intermediate в postgres:16 image? upstream Debian 12
ca-certificates version вопрос? В scope W10.1 proper session или отдельная
сессия инвестигации.

### 2. Port conflict: prod postgres на `0.0.0.0:5432` того же VPS

**Discovered:** 2026-04-23 при попытке exposure staging db на `127.0.0.1:5432`.

**Symptom:** `docker compose up -d db` для staging упал с
`failed to bind port 127.0.0.1:5432/tcp: address already in use`.

**Root cause:** prod `/opt/proficrm/docker-compose.yml` expose postgres на
`0.0.0.0:5432` (публично, все интерфейсы), на том же VPS что и staging.

**Workaround (deployed):** staging db exposed на `127.0.0.1:15432:5432`
(нестандартный loopback port). Helper compose connects через `PGPORT=15432`
(в walg.env).

**Security concern — отдельный hotlist item:** prod postgres экспозит 5432
на публичный интернет. Это должно быть bound на loopback или internal Docker
network only. Action: отдельная prod-session (с `CONFIRM_PROD=yes` маркером)
для prod postgres isolation.

---

## Status

- **2026-04-24 initial decision:** Accepted.
- **2026-04-23 implementation close:** delivered host-pivot architecture,
  first restore drill 100% match, retention cron активирован.

**Supersedes:** — (новое решение).
**Superseded-by:** — (actual на момент закрытия W10.2-early).
