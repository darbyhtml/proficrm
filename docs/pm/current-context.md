# PM current context

_Живое состояние текущей PM-сессии. PM обновляет этот файл перед предсказуемым compact или каждые 30-60 минут активной работы. После compact — читается ПЕРВЫМ для восстановления контекста._

**Last updated:** 2026-04-23 17:10 UTC (PM).

---

## 🎯 Current session goal

**Фаза 3 B.1 approved Дмитрием.** Host-pivot: archive_command пишет WAL в shared spool, host cron push'ит в R2. Дальше — first base backup через helper-контейнер с host network, restore drill, runbook, ADR update.

## 📋 Active constraints

- Path E: **ACTIVE**.
- Staging стабилен: `archive_command = '/bin/true'`, 7/7 containers healthy.
- Wrapper на disk но не активен.
- R2 bucket пустой.
- pg_dump safety net активен.

## 🔄 Last decision made

**Timestamp:** 2026-04-23 17:10 UTC.
**Decision:** B.1 approved — shared spool + host cron architecture.
**Reasoning:** evidence definitive (Scenario B), reuse existing artifacts, низкий риск, 1-min RPO приемлем для staging.
**Owner:** Дмитрий approved, PM пишет Фазу 3 промпт.

## ⏭️ Next expected action

1. ✅ Обновить `docs/pm/current-context.md`.
2. ✅ Коммит.
3. ⏭️ Передать Дмитрию Фазу 3 промпт (разбит на 3.1 → Checkpoint → 3.2 → Checkpoint → Фаза 4).
4. ⏭️ Ждать Checkpoint 3.1 (WAL archiving работает через spool → cron → R2).
5. ⏭️ Ждать Checkpoint 3.2 (first base backup + restore drill).
6. ⏭️ Фаза 4 close.

## ❓ Pending questions to Дмитрий

- [ ] **Одобрение 1 breaking action в Фазе 3.1:** restart db-контейнера (~30 сек простоя) для подхвата двух новых bind-mounts: spool directory + port mapping 127.0.0.1:5432→5432 (для host wal-g → postgres connection в Фазе 3.2).
- [ ] Telegram Kuma alert ожидаемый, не инцидент.

## 📊 Фаза 3 B.1 — detailed plan

### Sub-phase 3.1 — WAL archiving setup (~45 min)

Objective: `archive_command` → spool copy; host cron push'ит в R2.

Steps:

1. **Spool directory на хосте:** `/var/lib/proficrm-staging/wal-spool/`, owner `999:999` (postgres UID в контейнере = UID на хосте).
2. **Обновить `docker-compose.staging.yml`:** добавить два mount'а в сервис db:
   - `/var/lib/proficrm-staging/wal-spool:/wal-spool` (rw для postgres).
   - `ports: - "127.0.0.1:5432:5432"` (для host wal-g backup-push в 3.2).
3. **Rewrite `/etc/wal-g/archive-command.sh`** — простой cp:
   ```bash
   #!/bin/bash
   set -e
   SRC="$1"
   DEST="/wal-spool/$(basename "$SRC")"
   cp --no-clobber "$SRC" "$DEST.tmp"
   sync
   mv "$DEST.tmp" "$DEST"
   ```
4. **Host cron script** `/opt/proficrm-staging/scripts/walg-push-from-spool.sh`:
   - flock для защиты от concurrent runs.
   - Source walg.env.
   - Для каждого WAL файла (skip .tmp): `wal-g wal-push $SRC` на хосте; при успехе `rm $SRC`; при ошибке — keep и log.
5. **Cron entry** `/etc/cron.d/proficrm-walg-spool`: `* * * * *`.
6. **Commit + push + pull на VPS + restart db** (breaking action ~30s).
7. **Reactivate archive_command:** `ALTER SYSTEM SET archive_command = '/etc/wal-g/archive-command.sh %p'; SELECT pg_reload_conf();`.
8. **Verify flow через 2-3 мин:** spool наполняется, cron tick, spool пустеет, R2 `wal_005/` получает файлы, pg_stat_archiver archived_count растёт, failed_count=0.

**Checkpoint 3.1 → PM.**

### Sub-phase 3.2 — First base backup + restore drill (~60 min)

Objective: full base backup в R2, restore drill proof.

Steps:

1. **Helper для backup-push:** temp `docker-compose.walg-backup.yml` с `network_mode: host` + mount data volume + wal-g binary + walg.env.
2. **Добавить PGPASSWORD в walg.env** (если ещё не там) — Дмитрий передаст через SSH, не в чат.
3. **Run backup-push:** `wal-g backup-push /var/lib/postgresql/data` через helper.
4. **Verify:** `wal-g backup-list --pretty` с хоста показывает 1 backup с sane size.
5. **Restore drill:** scratch-контейнер с host network, backup-fetch LATEST, recovery.signal, replay WAL из R2 (wal-fetch), row counts match.
6. **Cleanup drill env.**

**Checkpoint 3.2 → PM.**

### Фаза 4 — Close (~30 min)

1. **Runbook** `docs/runbooks/2026-04-23-wal-g-pitr.md` (адаптирован для host-pivot architecture).
2. **ADR update** `docs/decisions/2026-04-24-wal-g-r2-bridge-to-minio.md`:
   - §Actual Implementation: архитектура host-pivot.
   - §Known Issues: container HTTPS blocker evidence + workaround.
3. **Retention cron** — weekly full base backup + delete retain FULL 4.
4. **Smoke + final rapport.**

## 🚨 Red flags (if any)

Нет. План чистый, breaking action одобрен.

## 📝 Running notes

### Почему port mapping 127.0.0.1:5432:5432 необходим

wal-g `backup-push` читает postgres data directory + зовёт `pg_start_backup()` через postgres connection. Helper-контейнер с `network_mode: host` видит only host network — не Docker bridge. Postgres в bridge, недоступен. Port mapping 127.0.0.1:5432 делает postgres reachable с хоста (и host-network helper). Localhost-only binding безопасно.

### PGPASSWORD

`/etc/wal-g/walg.env` сейчас содержит `PGHOST=localhost, PGDATABASE=crm_staging, PGUSER=crm_staging` но нет `PGPASSWORD`. Нужно добавить для Фазы 3.2. Варианты:

- Дмитрий `nano /opt/proficrm-staging/.env` → `cat POSTGRES_PASSWORD` → append `PGPASSWORD=<value>` в `/etc/wal-g/walg.env` через SSH.
- Или исполнитель читает POSTGRES_PASSWORD из `.env.staging` и пишет в walg.env через SSH.

Второй вариант автономнее, исполнитель сам справится в Step 3.2-2.

### Architecture diagram (для ADR)

```
┌─────────────────────────────────────────────┐
│ staging VPS 5.181.254.172                   │
│                                             │
│ ┌──────────────┐         ┌─────────────┐    │
│ │ db container │         │ host        │    │
│ │              │         │             │    │
│ │  postgres ───┼─cp──►  /wal-spool/    │    │
│ │  archive_cmd │         │     │       │    │
│ └──────────────┘         │     ▼       │    │
│                          │  cron every │    │
│                          │  1 min:     │    │
│                          │  wal-g      │    │
│                          │  wal-push   │    │
│                          └─────┼───────┘    │
│                                │            │
└────────────────────────────────┼────────────┘
                                 │ HTTPS
                                 ▼
                      ┌────────────────────┐
                      │ Cloudflare R2      │
                      │ proficrm-walg-     │
                      │ staging bucket     │
                      └────────────────────┘
```

### Lesson candidates (обновлены)

L9-17 уже перечислены. Ничего нового в этот момент.

### Update triggers (reminder)

- Перед каждой передачей Дмитрию.
- Каждые 30-60 минут активной работы.
- После получения рапорта исполнителя.
- После принятия решения.
- Перед длительной операцией.
- При приближении к компактификации контекста.
