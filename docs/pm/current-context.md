# PM current context

_Живое состояние текущей PM-сессии. PM обновляет этот файл перед предсказуемым compact или каждые 30-60 минут активной работы. После compact — читается ПЕРВЫМ для восстановления контекста._

**Last updated:** 2026-04-24 09:55 UTC (PM).

---

## 🎯 Current session goal

**Checkpoint 3.2 закрыт — win.** PITR работает end-to-end: base backup 1.01 ГБ в R2, restore drill 100% row match. Reveal критичного root cause: блокер — **TLS cert trust**, не networking. Approve Фазы 4 close — runbook, ADR update с revised root cause, retention cron, финальный rapport.

## 📋 Active constraints

- Path E: **ACTIVE**.
- Staging: API HTTP 200, 7/7 containers healthy.
- archive_command активно → host cron push в R2 (каждую минуту).
- R2 bucket содержит: 1 base backup + 11 WAL archives + рост.
- pg_dump safety net работает.
- `CF_API_TOKEN` всё ещё валиден — после Фазы 4 предложу revoke.

## 🔄 Last decision made

**Timestamp:** 2026-04-24 09:55 UTC.
**Decision:** approve Фазу 4 close с specifics:
- Retention cron через helper compose (не host-direct script).
- ADR §Known Issues переписать — root cause = **TLS CA bundle mismatch**, не «container networking».
- Runbook document host-direct как emergency fallback.
- Symlink `/var/lib/postgresql/data` keep (low cost, potential future use).
- Lesson 19 добавить в list.
**Owner:** PM approved, исполнитель идёт Фазу 4.

## ⏭️ Next expected action

1. ✅ Обновить `docs/pm/current-context.md`.
2. ✅ Коммит.
3. ⏭️ Передать исполнителю approve + Фаза 4 specifics.
4. ⏭️ Ждать финальный rapport ~30-40 минут.
5. ⏭️ После rapport: формальный close W10.2-early + написание Lessons 9-19 в lessons-learned.md + update хотлиста.
6. ⏭️ Предложить Дмитрию revoke `CF_API_TOKEN` (не нужен для daily ops).

## ❓ Pending questions to Дмитрий

Нет. После финального rapport Фазы 4 — presentation outcomes + revoke CF token recommendation.

## 📊 Checkpoint 3.2 findings

### Backup + restore drill (primary evidence)

| Property | Value |
|----------|-------|
| Backup name | `base_000000010000009B000000E0` |
| Duration push | 1 мин 6 сек |
| Uncompressed | 5.70 ГБ |
| Compressed (brotli) | 1.01 ГБ |
| start_lsn / finish_lsn | 669478027304 / 669494804560 |
| Restore drill duration | ~14 сек fetch + ~3 сек WAL replay |
| Validation match | 100% (3 table counts + MAX(created_at) exact) |

### Revised root cause (CRITICAL для ADR update)

Original W10.2-early hypothesis: «container networking broken».
**Actual:** `x509: certificate signed by unknown authority`.

- Debian 12 CA bundle в `postgres:16` image не trust'ит Cloudflare R2 chain.
- Host Ubuntu 24.04 `/etc/ssl/certs/ca-certificates.crt` (3610 entries) trust'ит.
- Исполнитель проверил retroactive: compose с bind mount `/etc/ssl/certs` работает.

**Impact:**

- Host-pivot architecture (текущий deploy) работает.
- Alternative: in-container archive_command + CA bundle mount — **мог бы работать** (проще архитектура).
- Для MinIO migration можно revisit in-container approach.

### Commits

- `a13bf9a6` — helper compose + drill + host fallback script в репо.

## 🚨 Red flags (if any)

Нет. Staging стабилен, archiving активный, restore drill passed.

## 📝 Running notes

### Фаза 4 specifics

1. **Runbook** `docs/runbooks/2026-04-23-wal-g-pitr.md`:
   - Architecture diagram (host-pivot + TLS insight).
   - Daily ops: spool check, log tail, archived_count growth.
   - Weekly: `docker compose -f docker-compose.walg-backup.yml run --rm walg-backup` для full base backup.
   - Emergency restore PITR: command sequence from Фаза 3.2.5.
   - Fallback host-direct для случая если compose сломается.
   - Troubleshooting: TLS cert issues, disk fill, cron failures.
   - Migration к MinIO: revisit in-container с CA mount.

2. **ADR update** `docs/decisions/2026-04-24-wal-g-r2-bridge-to-minio.md`:
   - §Actual Implementation — host-pivot details.
   - §Known Issues **переписать** — TLS CA bundle, не networking:
     - Exact error: `x509: certificate signed by unknown authority`.
     - Impact: any HTTPS к R2 из container hangs через retry loops.
     - Workaround 1 (deployed): host-pivot.
     - Workaround 2 (alternative): bind mount `/etc/ssl/certs` с хоста.
     - Future investigation: root cause почему Debian 12 bundle не trust'ит Cloudflare (bundle staleness? missing intermediate?).

3. **Retention cron** — через helper compose:
   ```cron
   # Воскресенье 02:00 UTC — full base backup.
   0 2 * * 0 root cd /opt/proficrm-staging && docker compose -f docker-compose.walg-backup.yml run --rm walg-backup >> /var/log/proficrm-walg-backup.log 2>&1

   # Воскресенье 03:00 UTC — retention 4 недели.
   0 3 * * 0 root set -a; . /etc/wal-g/walg.env; set +a; /usr/local/bin/wal-g delete retain FULL 4 --confirm >> /var/log/proficrm-walg-retention.log 2>&1
   ```

4. **Smoke + final rapport**.

### Lessons 9-19 (для финального writeup)

- L9 — explicit safe channel для секретов.
- L10 — cloud service activation ≠ credentials.
- L11 — Cloudflare API не даёт permanent S3-tokens.
- L12 — Never trust `pg_stat_archiver` alone.
- L13 — Container networking ≠ host networking (revised: → TLS bundle, см. L19).
- L14 — Wrapper scripts для archive_command — тест с реальным `%p`.
- L15 — PM sync даты через `date`.
- L16 — temporal staleness в delegation.
- L17 — Container HTTPS hang → try host-level tool as pivot.
- L18 — Port audit ДО mapping в multi-env VPS.
- **L19 (new) — Container CA bundle ≠ host. При HTTPS hang — check `/etc/ssl/certs` first.**

### Post-closure plan

После финального rapport:

1. Close W10.2-early commits summary в хотлисте.
2. Recommend Дмитрий revoke `CF_API_TOKEN` (daily ops не нужен).
3. Merge ветки `claude/recursing-elgamal-c31a17` → main (когда подтвердишь).
4. Follow-up hotlist items остаются:
   - Кроны стейджинга в репо (созданный ранее).
   - MinIO proper setup (созданный ранее).
   - **Prod postgres 0.0.0.0:5432 exposure (CRITICAL)** — отдельная сессия после.

### Date fix successful

Я писал «2026-04-24» в header (previous) — теперь **подтверждено** VPS time (backup_modified 2026-04-24 09:36 UTC). Date mismatch был из-за UTC rollover в процессе long сессии (сессия стартовала 2026-04-23 MSK вечером, через midnight UTC стало 24-е).

### Update triggers (reminder)

- Перед каждой передачей Дмитрию.
- Каждые 30-60 минут активной работы.
- После получения рапорта исполнителя.
- После принятия решения.
- Перед длительной операцией.
- При приближении к компактификации контекста.
