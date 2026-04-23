# PM current context

_Живое состояние текущей PM-сессии. PM обновляет этот файл перед предсказуемым compact или каждые 30-60 минут активной работы. После compact — читается ПЕРВЫМ для восстановления контекста._

**Last updated:** 2026-04-24 09:35 UTC (PM).

---

## 🎯 Current session goal

**W10.2-early — WAL-G PITR setup с Cloudflare R2 backend.** Option B принят Дмитрием: R2 как временный S3-совместимый хранилище, MinIO migration deferred к future W10.1 proper session. Сейчас PM пишет промпт Executor'у.

## 📋 Active constraints

- Path E: **ACTIVE** (prod freeze до W9).
- Executor mode: staging-only.
- Current wave focus: W10 infrastructure — W10.2-early WAL-G PITR.
- Principle deviation: master plan §W10 «only self-hosted + free-tier» частично нарушен (R2 = Cloudflare third-party, но free forever). Deviation зафиксирована в ADR `docs/decisions/2026-04-24-wal-g-r2-bridge-to-minio.md`.
- Critical: pre-W9 blocker **nkv Android migration** ещё open (не сегодня).

## 🔄 Last decision made

**Timestamp:** 2026-04-24 09:30 UTC.
**Decision:** Option B — Cloudflare R2 (10 GB free forever, no egress fees) как bridge storage для WAL-G. Honest naming: **W10.2-early** (не W10.1). MinIO migration — future W10.1 proper session.
**Reasoning:** time-to-PITR 4-6h vs 8-12h для MinIO setup, VPS memory pressure (swap 1 GB), R2 free-tier достаточен для staging WAL archives (≤10 GB/месяц).
**Owner:** Дмитрий (approved). Migration plan documented в ADR.

## ⏭️ Next expected action

1. ✅ Update current-context.md (этот файл).
2. ✅ Create ADR `docs/decisions/2026-04-24-wal-g-r2-bridge-to-minio.md`.
3. ✅ Update `docs/audit/hotlist.md` — add item «W10.1 proper MinIO setup (WAL-G migration from R2)».
4. ✅ Commit все три одним коммитом.
5. ⏭️ Написать промпт Executor'у (полный scope, constraints от reviewer).
6. ⏭️ Передать промпт Дмитрию для copy-paste в окно Executor.
7. ⏭️ После rapport от Executor — review restore drill proof + classification.

## ❓ Pending questions to Дмитрий

- [ ] **R2 credentials** — Дмитрий передаёт Executor'у отдельно (не в промпте). Ожидаю confirmation что credentials доставлены до старта Executor-сессии.
- [ ] **R2 bucket name** — `proficrm-walg-staging` (предлагается) или другое?

## 📊 Last Executor rapport summary

N/A — Executor ещё не involved. Первый rapport ожидается через 4-6h после старта Executor-сессии.

Ожидаемый формат rapport (mandatory items):

- WAL archive count после 1h running.
- Full backup size + duration.
- Restore drill timestamp + result.
- R2 bucket space used.
- `docs/runbooks/2026-04-24-wal-g-pitr.md` created.
- PostgreSQL log excerpts showing `archive_command` success.
- DEPLOY FULLY COMPLETED marker (если deploy involved — для archive_command reload это не auto-deploy, может не применяться).

## 🚨 Red flags (if any)

Пусто. Scope mismatch resolved через explicit rename «W10.2-early» + ADR.

## 📝 Running notes

### Decision chain (этой сессии)

1. **09:05 UTC:** Дмитрий запросил «W10.1 WAL-G PITR».
2. **09:10 UTC:** PM audit обнаружил scope mismatch (master plan 10.1 = MinIO, 10.2 = WAL-G).
3. **09:15 UTC:** PM представил 3 options (A strict / B R2 / C local FS).
4. **09:30 UTC:** Дмитрий выбрал B (Cloudflare R2, honest rename «W10.2-early»).
5. **09:35 UTC:** PM начинает writing ADR + promт.

### Scope boundaries для Executor-сессии

**В scope:**

- Install WAL-G binary (Ubuntu 24.04 compat) → `/usr/local/bin/wal-g`.
- Config `/etc/wal-g/walg.env` с R2 endpoint + credentials (из env-файла staging).
- PostgreSQL config: `archive_mode = on`, `archive_command`, `archive_timeout = 60s`, `wal_level = replica` (если не уже).
- Full base backup → R2.
- Restore drill: test DB create → delete → PITR restore → data match verification.
- Runbook `docs/runbooks/2026-04-24-wal-g-pitr.md`.
- Retain `scripts/backup_postgres.sh` daily pg_dump как fallback (не удалять).

**Вне scope (future sessions):**

- MinIO install (future W10.1 proper).
- WAL-G migration R2 → MinIO (future W10.1 migration task).
- Prod rollout (Path E — не до W9).
- Hot/warm standby Postgres (W10.4).
- Prometheus exporter для WAL archiving age (W10.5).

### Update triggers (reminder)

- Перед каждой передачей Дмитрию.
- Каждые 30-60 минут активной работы.
- После получения Executor rapport.
- После принятия decision.
- Перед long-running операцией.
- Когда conversation приближается к compact limit.
