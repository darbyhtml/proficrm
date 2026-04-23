# PM current context

_Живое состояние текущей PM-сессии. PM обновляет этот файл перед предсказуемым compact или каждые 30-60 минут активной работы. После compact — читается ПЕРВЫМ для восстановления контекста._

**Last updated:** 2026-04-24 09:15 UTC (PM).

---

## 🎯 Current session goal

Первая реальная PM-сессия после bootstrap. Дмитрий запросил запустить **W10.1 — WAL-G PITR setup**. Pre-session аудит обнаружил **scope mismatch** с master plan: 10.1 в `docs/plan/11_wave_10_infra.md` = MinIO, 10.2 = WAL-G (WAL-G hard-зависит от 10.1). Stop + ask перед написанием промпта — Pattern 1 discipline.

## 📋 Active constraints

- Path E: **ACTIVE** (prod freeze до W9).
- Executor mode: staging-only.
- Current wave focus: W10 infrastructure (25% готов — GlitchTip + Kuma + daily pg_dump + Telegram alerts).
- Principe W10: «только self-hosted и free-tier» (master plan §Принцип) — исключает платные S3.
- Critical: pre-W9 blocker **nkv Android migration** ещё open (не сегодня).

## 🔄 Last decision made

**Timestamp:** 2026-04-24 09:15 UTC.
**Decision:** pending от Дмитрия. Опции представлены в финальном response (A/B/C).
**Reasoning:** scope mismatch между user request («W10.1 WAL-G») и master plan («10.1 MinIO, 10.2 WAL-G зависит от 10.1») требует strategic decision перед implementation.
**Owner:** Дмитрий (decision pending).

## ⏭️ Next expected action

Получить decision Дмитрия (A / B / C):

- **A:** строгий master plan — сегодня 10.1 MinIO, завтра 10.2 WAL-G.
- **B:** WAL-G с внешним S3 first, MinIO migration позже (нарушает free-tier).
- **C:** WAL-G с локальным filesystem target (outside master plan).

После decision — написать детальный промпт Executor'у с Step 0 аудитом.

## ❓ Pending questions to Дмитрий

- [ ] Scope choice A / B / C для W10 infrastructure сегодня.
- [ ] Если A — есть ли второй VPS под MinIO (рекомендация master plan) или разворачивать на основном VPS?

## 📊 Last Executor rapport summary

N/A — Executor ещё не involved в этой сессии. Pre-session аудит выполнен PM.

## 🚨 Red flags (if any)

- **🔴 Scope mismatch обнаружен** (09:10 UTC): user request «W10.1 WAL-G» vs master plan «10.1 MinIO, 10.2 WAL-G». PM выполнил audit-first (Pattern 1), обнаружил dependency chain, stop + ask вместо написания промпта blindly. Demonstrates post-bootstrap discipline.

## 📝 Running notes

### Audit findings (pre-session)

**Существующая backup-инфраструктура:**

- `scripts/backup_postgres.sh` (44 строки): daily `pg_dump` + gzip + optional GPG, retention 14 дней, target = локальная директория `/opt/proficrm/backups/`. Bronze-tier (RPO = 24h, SPOF = VPS).
- `scripts/glitchtip-backup.sh` — отдельный бэкап БД GlitchTip.
- **Нет:** WAL-G install/config, `archive_command`/`archive_mode` в PostgreSQL, MinIO, внешнего S3 endpoint, PITR-возможности.

**Observability state:**

- `docker-compose.observability.yml` — только GlitchTip self-hosted (W0.4, 2026-04-20), memory-pinned (608 MB total). VPS swap уже 1 GB (упомянуто в комментарии файла).
- Нет Prometheus / Grafana / Loki / MinIO / WAL-G.

**Master plan:**

- `docs/plan/11_wave_10_infra.md` 837 строк.
- Порядок строгий: 10.1 MinIO → 10.2 WAL-G → 10.3 Media → 10.4 Standby → 10.5 Prometheus stack → 10.6 GlitchTip polish → 10.7 Backup drill → 10.8 DR → 10.9 CI/CD.
- «Нельзя менять порядок 10.1→10.2→10.3» (прямая цитата).
- Принцип: «только self-hosted и free-tier».

**Почему WAL-G hard-зависит от MinIO:**

WAL-G config (master plan §10.2):

```
WALG_S3_PREFIX=s3://proficrm-walg-prod/postgres
AWS_ENDPOINT=https://s3.groupprofi.ru
AWS_ACCESS_KEY_ID=walg
```

MinIO с bucket `proficrm-walg-prod` + IAM user `walg` + TLS-endpoint `s3.groupprofi.ru` — prerequisite. Без них WAL-G `archive_command` не заработает.

### Почему options B и C существуют

- **B (внешний S3):** WAL-G binary работает с любым S3-compat (AWS, Backblaze B2, Wasabi, Cloudflare R2). Free tier AWS S3 = 5 GB, R2 = 10 GB free, B2 = 10 GB free. Техническая возможность есть, но master plan принцип «free-tier only» — AWS требует платить при росте. Cloudflare R2 / B2 — 10 GB free достаточно для staging PostgreSQL (~5 GB данных + WAL archives месяц). **Faster time-to-WAL-G (~2-3h)**, но может потребовать migration к MinIO позже (double work).
- **C (локальный FS):** WAL-G поддерживает `WALG_FILE_PREFIX=file:///path`. Нет S3, просто пишет в директорию. Практически не даёт над pg_dump ничего нового (SPOF на том же VPS). **Не рекомендую** — нивелирует смысл WAL-G.

### Update triggers (reminder)

- Перед каждой передачей Дмитрию.
- Каждые 30-60 минут активной работы.
- После получения Executor rapport.
- После принятия decision.
- Перед long-running операцией.
- Когда conversation приближается к compact limit.

### Template для будущих updates

См. `docs/pm/current-context.md` predecessor commit `513b08e1` — initial structure template.
