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
- `scripts/backup_postgres.sh` daily pg_dump остаётся как fallback — defense-in-depth (Pattern 3 playbook §7).

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
