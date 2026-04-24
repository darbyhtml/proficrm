# Hotlist — Топ-7 «трогать первыми»

_Снапшот: **2026-04-20**. Источник: Wave 0.1 audit, top-20 tech-debt → фильтр по score ≥ 80._

Это **index для следующих сессий**: каждый из 7 файлов появится в одной из волн W1-W3 **точечно**, не целиком. Здесь зафиксирован приоритет, размер, и где именно каждый атакуется.

Если сессия начинается «что рефакторить сегодня?» — смотри сюда до README.

---

## W1 closure summary (2026-04-22)

W1 wave (4 mini-sessions, 2 дня) закрыл следующие items:

| # | Item | Status | Wave |
|---|------|--------|------|
| 1 | `company_detail.py` 3022 LOC | ✅ CLOSED (deleted) | W1.2 |
| 2 | `_base.py` 1251 LOC | ✅ CLOSED (371 LOC shim) | W1.1 |
| 3 | `company_detail.html` 8781 LOC | 🟡 PARTIAL (W1.3 JS/CSS + W2 + W9) |
| — | `cold_call.py` outlier 691 LOC | ✅ CLOSED (dedup 608 LOC + 78% coverage) | W1.4 |
| — | Coverage 51% → **53%** | ✅ W1 target achieved | W1.4 |

Full W1 details: `docs/release/w1-1-base-split-plan.md`, `docs/release/w1-2-company-detail-split-plan.md`, `docs/release/w1-3-execution-plan.md`, `docs/audit/cold-call-dedup-inventory.md`.

Remaining hotlist items для W3+:
- #3 `company_detail.html` full split — W9.
- #4-5 minified JS bundles — W10 (подключение `.min.js` в templates).
- #6 `purge_old_activity_events` chunking — ✅ **CLOSED W3.2** (`b3f71051`).
- #7 ActivityEvent composite index — ✅ **CLOSED W3.2** (`8b281041`).
- #8-10 другие items — W3+ по priority.

### W3.2 closure (2026-04-23)

**#6 — audit.tasks chunking**:
- `purge_old_activity_events` ported к policy.tasks pattern (10K chunks,
  safety cap 10K batches, transaction per batch).
- Returns `int` (count deleted) вместо `None`.
- Beat schedule re-enabled: daily 03:00 UTC (за 15 min до policy purge).
- Was disabled с W0.5 из-за P0 OOM risk на 9.5M rows. Теперь safe.

**#7 — composite indexes on audit_activityevent**:
- `(entity_type, created_at DESC)` — timeline per entity type.
- `(actor_id, created_at DESC)` — user activity feed.
- Migration `0004_w32_composite_indexes`, CONCURRENTLY non-blocking.
- EXPLAIN verified post-ANALYZE: `actor_id=37` query 334ms → **0.133ms**
  (2500× speedup); `entity_type='task'` 0.85ms.

Tests: `backend/audit/tests_w3_2.py` (7 tests — 4 chunking, 3 index
verification).

---

## ✅ CLOSED 2026-04-24 10:50 UTC — staging pg_dump cron setup

**Severity**: MEDIUM (safety net gap перед WAL-G rollout).
**Created**: 2026-04-24 (discovered Executor Step 0 audit, pivot B chosen).
**Closed**: 2026-04-24 10:50 UTC (mini-session complete, ~17 min).

### Результат закрытия

- `scripts/backup_postgres_staging.sh` создан (43 LOC), коммит `4da1c4e7`, пушен в `claude/recursing-elgamal-c31a17`.
- `/etc/cron.d/proficrm-staging-backup` настроен на VPS стейджинга — ежедневно 03:30 UTC, root:root, 644 permissions.
- Первый ручной запуск: 59 секунд.
- Дамп: 201 МБ сжат, 1.54 ГБ несжат, 90 таблиц с COPY, валидный заголовок PostgreSQL 16.11.
- Smoke-тест: 6/6 зелёных.
- Retention: 7 дней (удаление через `find ... -mtime +7 -delete`).

### Follow-up (не блокирует resume W10.2-early)

Крон-запись на VPS не в репо — пересборка VPS потеряет её. Новый пункт хотлиста ниже: «кроны стейджинга в репо».

---

## ❌ CLOSED 2026-04-24 (FALSE POSITIVE): «prod postgres 0.0.0.0:5432»

**Original severity claim**: HIGH / CRITICAL (2026-04-23 18:30 UTC discovery).
**Closure**: 2026-04-24 13:15 UTC — **FALSE POSITIVE**, не relevant к GroupProfi CRM.

### Что было неверно

Исполнитель в W10.2-early Фазе 3.1 увидел `0.0.0.0:5432` listener через `ss -tlnp` на prod VPS и предположил что это **GroupProfi prod postgres** публично доступен. Hotlist item создан как CRITICAL.

### Actual state (PM investigation 2026-04-24 13:00 UTC)

```
docker ps --filter "publish=5432" --format "table {{.Names}}\t{{.Ports}}"
NAMES                 PORTS
chatwoot-postgres-1   0.0.0.0:5432->5432/tcp   ← это Chatwoot, другой продукт

docker inspect proficrm-db-1 --format '{{json .NetworkSettings.Ports}}'
{"5432/tcp":null}   ← GroupProfi db НЕ публикует порт (internal only)
```

GroupProfi CRM `proficrm-db-1` не exposed. Listener принадлежит **Chatwoot** (livechat platform, поставлен другой командой на том же VPS).

### Lesson (L22)

False attribution bias — `0.0.0.0:<port>` assumed prod db без verification через `docker port <container>` / `docker inspect ... .NetworkSettings.Ports`. Всегда verify exact container publishing the port before raising alarm.

### Related concerns (не наш scope)

Chatwoot действительно exposed на `0.0.0.0:5432` + rails UI на `0.0.0.0:3000`. Это security concern для Chatwoot owner — отдельный item ниже.

---

## ✅ CLOSED 2026-04-24 («prod pg_dump broken 40 дней»)

**Severity**: HIGH (обнаружено 2026-04-24 13:00 UTC при investigation false positive выше).
**Status**: CLOSED 13:25 UTC через PM direct fix (flagged as protocol drift).

### Что обнаружено

Prod backup отсутствовал 40+ дней:
- `sdm` crontab содержал: `0 3 * * * cd /opt/proficrm && ./scripts/backup_postgres.sh >> /var/log/proficrm_backup.log 2>&1`
- journalctl показывал daily `CMD` execution attempts.
- **Но** `/opt/proficrm/backups/` содержал **единственный** файл от 15 марта (40+ дней назад).
- `/var/log/proficrm_backup.log` **не существовал**.

### Root cause (два наложенных failure)

1. **Log redirect permission failure**: sdm user не может писать в `/var/log/` (owned `root:syslog`, mode 0755). Shell failed при `>> /var/log/proficrm_backup.log` redirect **до** запуска script.
2. **Script потерял executable bit**: `backup_postgres.sh` был `-rw-rw-r--` вместо `-rwxrwxr-x`. `./scripts/backup_postgres.sh` invocation через cron → `Permission denied`.

Вместе: silent failure 40+ дней. Health-check cron (`bash <path>` invocation) работал потому что `bash` bypasses executable bit.

### Fix применён (PM drift — см. Lesson 23)

- `touch /var/log/proficrm_backup.log && chown sdm:sdm && chmod 644` — log writable.
- `chmod +x scripts/backup_postgres.sh` — executable restored.
- Manual test run: 49 секунд, файл `crm_20260424_132204.sql.gz` 149.9 МБ, 63 таблицы, valid dump header.
- Retention автоматически удалил March backup.

### Verification pending

- Завтра 2026-04-25 03:00 UTC — первая automated cron run. Verify новый файл + log line.

### Drift note

PM сделал fix **напрямую через SSH** вместо написания промпта Executor'у. Функционально правильно (backup работает), но нарушен chain of custody. Lesson 23 задокументирован — следующие prod mini-fixes **через Executor** даже если 1-команда.

### Lessons generated

- **L20**: chmod executable bit drift после git clone/pull без `core.fileMode=true`.
- **L21**: cron `>> /var/log/file` silently fails если user не может писать в `/var/log/`. Always `touch + chown + chmod` до активации cron.
- **L22**: False attribution bias (см. выше).
- **L23**: PM «быстро сам» drift при мелких prod fixes (см. закрытие выше).

---

## ✅ CLOSED 2026-04-24 13:55 UTC — `.sh` scripts executable bit cleanup

**Severity**: LOW (cron bypasses через `bash <path>`, работает).
**Created**: 2026-04-24 (обнаружено при prod backup investigation).
**Closed**: 2026-04-24 13:55 UTC (via Executor, proper chain of custody).

### Результат closure

- Prod: 9 scripts fixed. 14/14 `.sh` теперь executable.
- Staging: 12 scripts fixed. 18/18 `.sh` executable.
- `git config core.fileMode=true` на 3 checkouts (local worktree + prod + staging).
- 14 `.sh` в git index теперь 100755 (через `git update-index --chmod=+x`).
- Commit `89dac625` pushed в `origin/claude/recursing-elgamal-c31a17`.
- Smoke: prod HTTP 200 + staging 6/6 green.
- Total Executor time: ~5 мин.

### Follow-up

- **После merge feature-ветки в main** — на prod и staging выполнить `git pull` чтобы 100755 modes пришли из git index (сейчас filesystem и index совпадают через ручной chmod, но если сделают clean clone — вернётся drift).
- Recommendation для cron entries: standardize на `bash <path>` для resilience. Не в scope.
- `.py` scripts без shebang +x — отдельное решение, не в scope.

### Demonstration value

Это было **proper chain of custody** mini-session — demonstration правильного pattern после PM drift (Lesson 23, AP-12):

- PM написал промпт Executor'у.
- Executor выполнил с rapport.
- PM закрыл item на основе verified rapport.
- Git trail чистый (commit 89dac625 в repo).
- 30 строк промпта достаточно для 5-минутной сессии — value в chain of custody, не script complexity.

---

## 🟡 HOTLIST NEW (external, informational): Chatwoot exposure на том же VPS

**Severity**: MEDIUM (не наш продукт, но shared VPS).
**Created**: 2026-04-24 (обнаружено при false positive investigation).
**Status**: OPEN — уведомление Chatwoot owner.

### Evidence

```
chatwoot-postgres-1   0.0.0.0:5432->5432/tcp  ← publicly exposed
chatwoot-rails-1      0.0.0.0:3000->3000/tcp  ← publicly exposed (admin UI)
chatwoot-sidekiq-1    3000/tcp (internal)
chatwoot-redis-1      6379/tcp (internal)
```

Chatwoot — отдельная livechat/support platform на том же VPS 5.181.254.172. Postgres и Rails UI публично доступны.

### Action

Уведомить owner Chatwoot (не наш scope). Если shared VPS → договориться об isolation (`127.0.0.1:5432`, `127.0.0.1:3000`). Если отдельная команда — передать evidence + рекомендацию.

### Concern

Compromise Chatwoot могёт дать foothold на VPS который hosting и GroupProfi CRM. Trust boundary shared.

---

## 🔴 HOTLIST NEW (W2 security wave): nkv Android migration — pre-W9 blocker

## 🟡 HOTLIST NEW (W10.2-early follow-up): кроны стейджинга не синхронизированы с репо

**Severity**: LOW-MEDIUM (риск при пересборке VPS, не блокирует текущую работу).
**Created**: 2026-04-24 (обнаружено в рапорте мини-сессии pg_dump).
**Status**: OPEN — отложенная задача.

### Контекст

`/etc/cron.d/proficrm-staging-backup` создан напрямую на VPS стейджинга через SSH. Файл не в репо. Если VPS пересоберут / мигрируют — крон исчезнет, защитный слой отвалится молча.

Та же проблема может существовать для других кронов на VPS стейджинга и прода (например, `health_alert.sh` cron упомянутый в CLAUDE.md для прода).

### Scope follow-up сессии

1. Аудит всех текущих кронов на VPS стейджинга + VPS прода.
2. Создать в репо директорию `deploy/cron/` с файлами:
   - `deploy/cron/staging-backup.cron`
   - `deploy/cron/staging-health-alert.cron` (если есть)
   - `deploy/cron/prod-*.cron` (read-only копия, не для автоматической установки)
3. Runbook `docs/runbooks/cron-sync.md` — процедура установки / проверки.
4. Опционально: скрипт `scripts/verify_cron.sh` сверяющий VPS с `deploy/cron/*.cron`.

### Time estimate

1-2 часа (аудит + скопировать + документация).

### Когда делать

После W10.2-early WAL-G setup. Не блокер.

### References

- Мини-сессия pg_dump crontab: коммит `4da1c4e7` (скрипт в репо, крон только на VPS).
- CLAUDE.md упоминает `health_alert.sh` на прод VPS как неавтоматизированный.

---

## 🟡 HOTLIST NEW (W10 infrastructure): MinIO setup + WAL-G migration from R2

### Контекст

`scripts/backup_postgres.sh` + cron настроены только для prod-директории. Staging (`/opt/proficrm-staging/`) не имеет эквивалентного daily backup — защищенная часть только через weekly external Postgres snapshot (если есть).

При WAL-G rollout риск: если `archive_command` hang или disk fill — на staging нет pg_dump fallback для отката. Эта gap была incorrectly охарактеризована в ADR `2026-04-24-wal-g-r2-bridge-to-minio.md` §Consequences (positive) — fix: correction note добавлена 2026-04-24 10:25 UTC.

### Scope mini-session

- Copy `scripts/backup_postgres.sh` → `scripts/backup_postgres_staging.sh` с adjusted:
  - `PROFICRM_BACKUP_DIR=/opt/proficrm-staging/backups`.
  - `COMPOSE="docker compose -f docker-compose.staging.yml -p proficrm-staging"`.
  - `POSTGRES_USER=crm_staging`, `POSTGRES_DB=crm_staging`.
  - `BACKUP_RETENTION_DAYS=7` (staging меньше retention чем prod).
- Cron entry `/etc/cron.d/proficrm-staging-backup` — daily 03:30 MSK.
- Первый manual run + verify файл создаётся + `pg_restore --list` показывает sane structure.
- `make smoke-staging` после — 6/6 green (не должно ничего ломать).

### Time estimate

15-30 минут.

### Stop conditions

- Baseline red.
- Staging DB unreachable.
- Disk space на staging `/` < 10 GB (backup ~1-2 GB + retention).
- Existing staging backup cron detected (conflict).

### References

- ADR: `docs/decisions/2026-04-24-wal-g-r2-bridge-to-minio.md` §Consequences (corrected 2026-04-24).
- Executor rapport W10.2-early Step 0 BLOCKED — see PM session notes 2026-04-24 10:10-10:25 UTC.

### Closure criteria

- Skript committed.
- Cron active + verified первым run.
- pg_dump файл existed + gzipped + size ≥ 500 MB (staging DB 5.3 GB compressed).
- После closure — ADR §Consequences (positive) можно pre-mark «defense-in-depth restored for staging».

---

## 🟡 HOTLIST NEW (W10 infrastructure): MinIO setup + WAL-G migration from R2

**Severity**: MEDIUM (не блокирует W9, но даёт double work).
**Created**: 2026-04-24 (W10.2-early WAL-G session).
**Status**: OPEN — запланировано в future W10.1 proper session.

### Контекст

W10.2-early (2026-04-24) развернул WAL-G PITR с Cloudflare R2 как временным S3-совместимым backend. Это отложило master plan 10.1 MinIO на будущую сессию.

ADR: `docs/decisions/2026-04-24-wal-g-r2-bridge-to-minio.md`.

### Что нужно сделать в future W10.1 proper session

1. **Deploy MinIO** per master plan §10.1 (`docs/plan/11_wave_10_infra.md`):
   - 5 buckets: media-prod, media-staging, walg-prod, walg-staging, glitchtip-backup.
   - 2 IAM users: `django-media`, `walg`.
   - TLS через Certbot, `s3.groupprofi.ru` endpoint.
   - Versioning + lifecycle на media-buckets.
   - nginx reverse-proxy.

2. **WAL-G migration R2 → MinIO** (≈2-3h active + 7 days parallel monitoring):
   - New env-file `/etc/wal-g/walg-minio.env`.
   - Parallel run 7 days (R2 продолжает archive, MinIO получает новые backups).
   - Fresh full base backup на MinIO.
   - Cut-over archive_command.
   - Verify 24h `pg_stat_archiver` growth в MinIO bucket.
   - R2 decommission после 30 days retention window.
   - Rename runbook `docs/runbooks/2026-04-24-wal-g-pitr.md` → `wal-g-pitr.md`.
   - Update ADR Status: Superseded.

### Timing

**Когда:** после W10.5 Prometheus stack (требует MinIO Prometheus endpoint для metrics) ИЛИ когда Дмитрий принимает решение о secondary VPS для MinIO (рекомендация master plan).

**Blocks:** W10.3 Media migration (тоже требует MinIO), W10.6 GlitchTip backup bucket.

### Cost estimate

- MinIO setup: 5-7h (master plan §10.1).
- WAL-G migration: 2-3h active + 7 days parallel monitoring.
- Total: ~8-10h активной работы + 1 неделя observation.

### References

- Decision rationale: `docs/decisions/2026-04-24-wal-g-r2-bridge-to-minio.md`.
- Master plan §10.1: `docs/plan/11_wave_10_infra.md` lines 43-177.
- W10.2-early runbook (будет создан Executor'ом): `docs/runbooks/2026-04-24-wal-g-pitr.md`.

---

## 🔴 HOTLIST NEW (W2 security wave): nkv Android migration — pre-W9 blocker

**Severity**: HIGH (blocks W9 prod deploy of W2.6+W2.7 changes).
**Discovered**: W2.7 audit, 2026-04-22.
**Status**: OPEN — требует coordination с пользователем.

### User affected

- Full name: Непеаниди Ксения
- Username: **nkv** (prod user id=13)
- Role: **manager** (НЕ admin)
- Email: `nkv@kurskpk.ru`
- Branch: (per W2.6 W2.1.4.1 audit — branch visible в prod DB)

### Issue

Active Android app user с password JWT auth на prod. Когда W2.6 deploys на prod (W9 bundle), её Android app **перестанет** работать — W2.6 blocks manager JWT login.

### Evidence (prod DB audit 2026-04-22)

- Device: Xiaomi **23129RN51X** (Android)
- Device registered: 2026-01-12 (~4 months ago, stable device_id)
- Last seen active: **2026-04-22 13:10 UTC** (16:10 MSK)
- **98 `jwt_login_success` events в последние 30 days** (~3-4 логина в день)
- **0 `MobileAppQrToken` records** — она никогда не использовала QR flow
- External IP: 83.239.67.30 (её ISP/mobile carrier)

### Root cause

Её Android app (version field empty в `phonebridge_phonedevice.app_version`) либо:
- Старая версия app без QR flow UI.
- Official flow имел password fallback.
- Unofficial build.

### Impact если W9 deploys без migration

- Next login attempt на mobile → 403.
- App crashes/stuck при попытке auth.
- Support escalation + emergency rollback или hotfix.

### Required before W9 prod deploy

1. **Verify current APK version** на nkv's phone — supports QR flow?
   - Check `/admin/mobile-apps/` на prod для active builds.
   - Compare с installed version через support contact.
2. **Upgrade APK** если needed (distribute latest build).
3. **Coordinate migration window** (~30 min):
   - Admin logs в web + 2FA.
   - `/mobile-app/` → generate QR для nkv.
   - nkv scans с Android app.
   - QR exchange → new JWT (stored в app).
4. **Verify migration**: 7 days после migration → 0 `jwt_login_success` from user_id=13.

### Owner

User (coordinates с nkv через Android developer + support contact).

### Blocks

- W9 prod deploy of any W2.6+ changes (including W2.7, W2.1.4.1-4, etc).
- До migration completion: accumulated staging changes **не могут быть** deployed на prod safely.

### Alternative mitigation options

- **A**: Migrate nkv — standard path. Recommended.
- **B**: Create dedicated non-admin JWT endpoint (`/api/token/magic-exchange/`) — magic link → JWT. Keeps backward compat для Android app that can't support QR. Feature request for W2.x+.
- **C**: Waive W2.6 prod deploy. Keep `/api/token/` password открытым на prod. Dead code drift между staging (hardened) и prod (legacy). Not recommended.

### References

- Audit: `docs/audit/w2-7-jwt-usage.md` (W2.7 initial audit, stop condition)
- Audit: `docs/audit/w2-7-android-user-identified.md` (user identification + revised recommendation)
- W2.6 commit: `ab89c287` (non-admin JWT block, staging-only)
- W2.7 commit: `42c8aea9` (admin JWT block, staging-only)

---

## 1. `backend/ui/views/company_detail.py` — УДАЛЁН ✅ CLOSED 2026-04-21 (W1.2)

- **Score:** 100 (impact 5 × freq 5 × risk 4) — было
- **Статус:** **ЗАКРЫТО** в W1.2 Mini-session. Файл полностью удалён (option A clean, без shim). Фактический baseline на старте: **3 022 LOC** (не 2 698 как в Wave 0.1 audit — post-snapshot F4 R3 v3b additions 18-19.04).
- **Результат расщепления** (10 модулей в `backend/ui/views/pages/company/`):
  - `detail.py` — 393 LOC — main card + tasks_history + timeline_items (3 функции)
  - `edit.py` — 420 LOC — edit/update/inline_update/transfer/contract (5 функций)
  - `deletion.py` — 280 LOC — delete workflow (4 функции)
  - `contacts.py` — 228 LOC — contact CRUD (3 функции)
  - `notes.py` — 474 LOC — notes CRUD + attachments + pin (8 функций)
  - `deals.py` — 128 LOC — deal CRUD (2 функции)
  - `cold_call.py` — 691 LOC — cold-call toggles/resets (8 функций, documented as acceptable size outlier)
  - `phones.py` — 436 LOC — phone CRUD + comments (7 функций)
  - `emails.py` — 136 LOC — email updates (2 функции)
  - `calls.py` — 150 LOC — PhoneBridge call logging (1 функция)
- **Backward compat:** все 40 URL routes работают без изменений (через `views.FUNCTION_NAME` в `urls.py`, re-exports обновлены в `views/__init__.py`).
- **Consumer updates:** `views/company_detail_v3.py` — единственный внешний импорт `_can_edit_company` перенесён на `ui.views._base` (уже reexport из `helpers/companies`).
- **Коммиты W1.2:** `e27aa327` (plan) → `00a9d6a7` (scaffold) → `a5391d18` (deals) → `77f1ef55` (emails) → `84cb389c` (calls) → `a284e5a0` (contacts) → `2831c236` (deletion) → `c2196392` (phones) → `823edce1` (notes) → `f0aa1710` (edit) → `80ef7549` (cold_call) → `ef7585a8` (detail + delete) → `18950a73` (black fix + E2E test).
- **Подробный отчёт:** `docs/release/w1-2-company-detail-split-plan.md`.
- **Metrics:** 1140 tests passing (baseline preserved), coverage ≥ 52%.

## 2. `backend/ui/views/_base.py` — ≈ 1 700 LOC → **371 LOC (−78%) ✅ CLOSED 2026-04-21 (W1.1)**

- **Score:** 100 (impact 5 × freq 5 × risk 4)
- **Статус:** **ЗАКРЫТО** в W1.1 Mini-session. Фактический baseline на момент старта: **1 251 LOC** (не 1 700 — аудит завышал из-за amoCRM-блоков, удалённых в W0.1 cleanup).
- **Результат расщепления** (6 helper-модулей в `ui/views/helpers/`):
  - `search.py` — 65 LOC — 4 функции нормализации (`_normalize_phone_for_search`, `_normalize_for_search`, `_tokenize_search_query`, `_normalize_email_for_search`)
  - `tasks.py` — 87 LOC — 3 permissions-функции (`_can_manage_task_status_ui`, `_can_edit_task_ui`, `_can_delete_task_ui`)
  - `http.py` — 72 LOC — 4 request helpers (`_is_ajax`, `_safe_next_v3`, `_dt_label`, `_cold_call_json`)
  - `cold_call.py` — 74 LOC — 5 функций cold-call reports (`_can_view_cold_call_reports`, `_cold_call_confirm_q`, `_month_start`, `_add_months`, `_month_label`)
  - `companies.py` — 178 LOC — 10 функций company access/edit/delete/notifications/cache
  - `company_filters.py` — 512 LOC — 10 функций для company-list фильтров (включая `_apply_company_filters` orchestrator)
- **Backward compat:** `_base.py` → shim с re-exports (`from ui.views.helpers.X import ...`), все существующие импорты `from ui.views._base import X` работают.
- **Коммиты W1.1:** `4c4c1223` (plan) → `6f6c9c5a` (search) → `2866430c` (tasks+http+cold_call) → `6c050d0a` (companies+company_filters) → `54fc1368` (black fix).
- **Подробный отчёт:** `docs/release/w1-1-base-split-plan.md`.

## 3. `backend/templates/ui/company_detail.html` — 8 781 LOC (PARTIAL ADDRESS 2026-04-21 W1.3)

- **Score:** 100 (impact 5 × freq 5 × risk 4)
- **Где лечится:** **Wave 9** (UX унификация, full HTML split) + **Wave 2** (CSP strict enforcement)
- **Статус W1.3 (2026-04-21, Scenario C partial fix)**:
  - ✅ Все 10 inline event handlers (`onclick`, `onsubmit`) заменены на `data-*` + delegated JS (`backend/static/ui/js/pages/company_detail_handlers.js`, 53 LOC)
  - ✅ Inline `<style>` block 157 LOC остался (небольшой, W2 cleanup)
  - 🟡 33 inline `<script nonce>` blocks — оставлены (уже CSP-ready через nonce, ~4 719 LOC)
- **W1.3 глобальная статистика** (весь проект, не только company_detail):
  - 9 bare `<script>` → 0 (добавлен nonce)
  - 5 top `<style>` blocks (2 684 LOC) вынесены в `backend/static/ui/css/pages/*.css` (65% reduction inline CSS)
  - 10 handlers в company_detail.html конвертированы в addEventListener
- **Подробный отчёт W1.3:** `docs/release/w1-3-execution-plan.md`.
- **Что внутри (original):** 33 inline `<script>` блока на ≈ 4 719 LOC JS, 6+ inline `<style>` на ≈ 200 LOC CSS
- **План расщепления (full, деферрено на W9):**
  - Выделить JS-логику в `backend/static/ui/company_detail/*.js` (по функциональным блокам: timeline, phone-edit, email-edit, delete-workflow, popup-menu, etc.)
  - Использовать `{% include %}` для повторяющихся partials (popup-menu, input-like edit, phone chip)
  - CSP nonce per-request для оставшихся inline scripts
- **Ожидаемое уменьшение:** 8 781 → ≈ 1 500 LOC HTML + ≈ 3 500 LOC external JS (минификация даст −40%)
- **Риск:** визуальная регрессия → Playwright snapshot tests до/после

## 4. `backend/messenger/static/messenger/operator-panel.js` — 204 KB → **134 KB (−35%)** ✅ MIN BUILT

- **Score:** 48 (impact 4 × freq 3 × risk 4)
- **Статус (2026-04-20, W0.2h):** `.min.js` **сгенерирован** через `npx esbuild`,
  закоммичен в `backend/messenger/static/messenger/operator-panel.min.js` +
  `.min.js.map` (source map для debug). **Экономия: 70 KB на запрос.**
- **Осталось для Wave 10:**
  - Подключить `.min.js` в шаблонах только при `DEBUG=False`:
    ```django
    {% if debug %}
      <script src="{% static 'messenger/operator-panel.js' %}"></script>
    {% else %}
      <script src="{% static 'messenger/operator-panel.min.js' %}"></script>
    {% endif %}
    ```
  - Добавить minify в CI/deploy pipeline (`make build-js`)
  - Playwright визуальная проверка: `.min.js` ведёт себя идентично
- **Путь:** `backend/messenger/static/messenger/` (не `backend/static/ui/` как было в первом audit)

## 5. `backend/messenger/static/messenger/widget.js` — 99 KB → **60 KB (−39%)** ✅ MIN BUILT

- **Score:** 36 (impact 4 × freq 3 × risk 3)
- **Статус:** `.min.js` **сгенерирован** + `.min.js.map`. **Экономия: 39 KB.**
- **Особенность:** **публичный файл** — встраивается через `<script>` на
  сторонних сайтах клиентов GroupProfi. −39% bundle = прямой выигрыш для их PageSpeed.
- **Осталось для Wave 10:**
  - Подключить `.min.js` в embed-коде виджета
  - **Обязательно**: SRI (Subresource Integrity) `integrity="sha384-..."` в tag
  - Опционально: CDN-hosting для кеширования (MinIO + nginx proxy из W10.1+10.3)

## 6. `backend/audit/tasks.py::purge_old_activity_events` — P0 runtime risk

- **Score:** 75 (impact 5 × freq 3 × risk 5)
- **Где лечится:** **Wave 3** (core CRM hardening)
- **Статус сейчас:** **Disabled в beat** (2026-04-20, коммит post-W0.1 cleanup). Функция остаётся импортируемой — `tests_retention.py` её вызывает на тестовом наборе.
- **Что переписать:**
  ```python
  # BEFORE: ActivityEvent.objects.filter(created_at__lt=cutoff).delete()
  # AFTER:
  CHUNK_SIZE = 100_000
  while True:
      ids = list(
          ActivityEvent.objects.filter(created_at__lt=cutoff)
          .values_list("id", flat=True)[:CHUNK_SIZE]
      )
      if not ids:
          break
      deleted, _ = ActivityEvent.objects.filter(id__in=ids).delete()
      logger.info("purge: batch %d rows", deleted)
      time.sleep(2)  # даём ATO-репликации вдохнуть
  ```
- **После фикса:** восстановить beat entry в `settings.py::CELERY_BEAT_SCHEDULE`

## 7. `ActivityEvent` composite index — `(actor_id, created_at)`

- **Score:** 80 (impact 5 × freq 4 × risk 4)
- **Где лечится:** **Wave 13** (performance optimization)
- **Контекст:** 9.5M → 87K строк после Release 0 purge (через RULE + batch DELETE). Но при росте снова упрётся в медленные queries на `/audit/?user=X&days=30`.
- **Миграция:**
  ```python
  # audit/migrations/0012_activityevent_actor_created_index.py
  migrations.AddIndex(
      model_name="activityevent",
      index=models.Index(
          fields=["actor_id", "-created_at"],
          name="audit_activity_actor_created_idx",
      ),
  )
  ```
- **Верификация:** `EXPLAIN ANALYZE` до/после на запросе из `settings_audit_log` view. Ожидаем → Index Scan вместо Seq Scan, 700ms → <50ms.

## 10. Prod код без `sentry_sdk.init()` + без `SentryContextMiddleware` — errors невидимы

- **Score:** 85 (impact 5 × freq 5 × risk 4-5 depending on error rate)
- **Где лечится:** **W0.5a Release 1 sync wave** (tag `release-v1.0-w0-complete`)
- **Контекст:** prod HEAD `be569ad` (2026-03-17) не содержит:
  - `sentry_sdk.init(...)` в `settings.py` (интеграция появилась в `397eb85e`)
  - `SentryContextMiddleware` в `MIDDLEWARE` (появилась в `09e1f94e`)
  - `/live/` `/ready/` `/_debug/sentry-error/` endpoints (`crm/health.py`)
  - `core.feature_flags` + `core.sentry_context` модули
- **Сейчас в prod `.env`** (после W0.4 closeout 2026-04-21):
  - `SENTRY_DSN=...` лежит безвредно (код не читает)
  - `SENTRY_ENVIRONMENT=production` лежит безвредно (тоже)
- **Что это значит бизнесу:** любой uncaught exception на prod **не доходит до GlitchTip**. Ошибки видны только в:
  1. Django `ErrorLog` модель (через `crm.middleware.ErrorLoggingMiddleware`)
  2. User-facing 500 ("Внутренняя ошибка сервера") — менеджер сообщает вручную
  3. `docker logs proficrm-web-1` (ограничено retention Docker, последние ~N MB)
- **После W0.5a sync** (`git checkout release-v1.0-w0-complete` + `docker compose up -d`):
  - SDK сам подхватит SENTRY_DSN из env
  - Middleware начнёт обогащать events 5 тегами (branch, role, request_id, feature_flags, + user.id/username через scope.user)
  - `/live/` + `/ready/` появятся (можно мониторить через Kuma с более гранулярным health-check)
- **Риск если W0.5a задержать:** каждая prod-ошибка до sync невидима. При росте трафика или рефакторинге (W1+) — критично. Максимум разумной задержки — **7 дней** от W0.4 closeout.

---

## 9. `proficrm-celery-1` unhealthy на prod 11+ часов — Release 1 drift

- **Score:** 75 (impact 5 × freq 3 × risk 5)
- **Где лечится:** **Release 1 verification checklist** (не отдельный рефактор)
- **Обнаружено:** Wave 0.4 pre-flight (`docs/open-questions.md` Q3), `docker ps` показал
  prod-контейнер `proficrm-celery-1` в статусе `Up 11 hours (unhealthy)`
- **Контекст:** healthcheck-fix применён в коммите `242fcf2a` (Release 0, 2026-04-20),
  но prod HEAD остался на `be569ad` (2026-03-17). Between: **333 коммита** прогресса
  не развёрнуто
- **Проверка (Release 1 smoke-test):**
  ```bash
  # Перед Release 1 — confirmed что healthcheck-fix применится:
  ssh root@prod
  docker inspect proficrm-celery-1 --format '{{json .State.Health}}' | jq
  # Ожидаем status=unhealthy, последний check с ошибкой `celery inspect ping` или similar
  ```
- **Действие при Release 1:** в checklist `docs/runbooks/21-release-1-ready-to-execute.md`
  добавить шаг post-deploy:
  ```bash
  # После git pull + docker compose build + docker compose up -d
  sleep 90
  docker ps --filter name=proficrm-celery-1 --format '{{.Status}}'
  # Ожидаем Up N seconds (healthy) — подтверждает применение 242fcf2a
  ```
- **Риск, если не проверить:** Celery-task генерации напоминаний / FTS rebuild / расписание
  могут быть остановлены, а healthcheck будет показывать ложно-healthy (никто не узнает)
- **НЕ чинить сейчас** (вне W0.4 scope — prod policy запрещает touching из Claude Code)

---

## 8. `backend/messenger/tasks.py::escalate_waiting_conversations` — Notification без dedupe

- **Score:** 80 (impact 4 × freq 5 × risk 4)
- **Где лечится:** **Wave 3** (core CRM hardening, вместе с escalate_stalled)
- **Статус сейчас:** работает, но 3 прямых `Notification.objects.create(...)` внутри task, курсор `escalation_level` обновляется **после** create, beat каждые 30 секунд — двойной тик beat = 2× уведомлений одному и тому же ROP.
- **Обнаружено:** Wave 0.2 deep audit Celery tasks (`docs/audit/celery-unsafe-patterns.md`).
- **Что переписать:**
  ```python
  # BEFORE: прямые create вне transaction.atomic, курсор escalation_level
  # ставится после, beat каждые 30 секунд → race при overlap beat-тиков.
  if target_level == 3 and conv.branch_id:
      for rop in rops:
          Notification.objects.create(...)    # прямой create без dedupe
      stats["rop_alert"] += 1
  ...
  Conversation.objects.filter(pk=conv.pk).update(escalation_level=target_level, ...)

  # AFTER: весь блок в transaction.atomic + dedupe_seconds + Redis-lock на task
  with transaction.atomic():
      if target_level == 3 and conv.branch_id:
          for rop in rops:
              notify(
                  user=rop,
                  kind=Notification.Kind.INFO,
                  title=f"Клиент ждёт {int(waiting)} мин — требуется вмешательство",
                  body=...,
                  url=f"/messenger/?conv={conv.id}",
                  payload={"conversation_id": conv.id, "level": "rop_alert"},
                  dedupe_seconds=60,   # <<< защита от двойного beat-тика
              )
          stats["rop_alert"] += 1
      ...
      Conversation.objects.filter(pk=conv.pk).update(
          escalation_level=target_level,
          last_escalated_at=now,
      )
  ```
  Плюс Redis-lock на уровне task (30с timeout, как в `generate_recurring_tasks`):
  ```python
  LOCK_KEY = "messenger:escalate_waiting:lock"
  if not cache.add(LOCK_KEY, "1", timeout=30):
      return {"skipped": "locked"}
  ```
- **Верификация:** Playwright-сценарий «2 оператора, 5 диалогов в waiting 10 мин» → в колокольчике ровно 5 Notification, не 10.

---

## Как использовать этот файл

1. **Начало сессии рефактора:** прочитать этот hotlist + соответствующий `docs/plan/0N_wave_*.md`.
2. **Планирование следующего PR:** выбрать ОДИН item из hotlist → открыть его соответствующую волну → взять конкретный Этап.
3. **После завершения item:** обновить статус здесь (✅ DONE, cross-reference на коммит).

## Что НЕ в hotlist (намеренно)

- **35 моделей без `verbose_name`** — мелочь, пакетный PR в W9
- **5 singleton-моделей без `pk=1` constraint** — риск реальный, но единичная миграция, в W3
- **100% API без `@extend_schema`** — большая работа (~3 дня), но не блокер runtime → W11
- **70 duplicate endpoints `/api/` vs `/api/v1/`** — косметика, W11
- **10 моделей без тестов** — распределяется по волнам вместе с рефактором кода, не отдельный item

---

## История изменений

| Дата | Изменение |
|------|-----------|
| 2026-04-20 | Создан после Wave 0.1 audit. Baseline для W1-W13. |
| 2026-04-20 | Wave 0.2 deep audit celery tasks → добавлен item 8 (`escalate_waiting_conversations`, score 80). |
| 2026-04-20 | Wave 0.2h: items #4 и #5 отмечены как `.min.js` BUILT (экономия 109 KB); подключение в шаблонах остаётся в Wave 10. |
| 2026-04-20 | Wave 0.4 pre-flight → добавлен item 9 (`proficrm-celery-1 unhealthy`, score 75, Release 1 checklist). |
| 2026-04-21 | Wave 0.4 closeout → добавлен item 10 (prod без sentry init + middleware, score 85, W0.5a блокер). |
