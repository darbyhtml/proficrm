# PM current context

_Живое состояние текущей PM-сессии. PM обновляет этот файл перед предсказуемым compact или каждые 30-60 минут активной работы. После compact — читается ПЕРВЫМ для восстановления контекста._

**Last updated:** 2026-04-24 15:30 UTC (PM).

---

## 🎯 Current session goal

**Handoff к новой сессии исполнителя.** W10.2-early blocked на container networking + wrapper script bug. Новый исполнитель получит deep-dive промпт с 4 фазами + checkpoint'ами к PM между ними. Приоритет: сначала **стабилизация staging** (остановить postgres crash loop), потом архитектурный investigation.

## 📋 Active constraints

- Path E: **ACTIVE**.
- Staging API работает (HTTP 200), 7/7 контейнеров healthy, данные целы.
- **🔴 Postgres в crash loop** — archive_command timeouts каждую минуту, exit 124, automatic recovery. Быстрые циклы, не катастрофично, но **нужно остановить как можно скорее** (Фаза 1 Step 1 нового промпта).
- Защитный слой pg_dump активен.
- R2 bucket пустой.

## 🔄 Last decision made

**Timestamp:** 2026-04-24 15:30 UTC.
**Decision:** новая сессия исполнителя с deep-dive промптом (4 фазы с checkpoint'ами). Старая сессия closed (session dead после длинных debug-циклов).
**Reasoning:** fresh context + research time + structured investigation лучше чем продолжение в hot fatigued сессии. Дмитрий явно запросил.
**Owner:** Дмитрий (запускает новую сессию), PM (написал промпт).

## ⏭️ Next expected action

1. ✅ Обновить `docs/pm/current-context.md`.
2. ✅ Коммит.
3. ⏭️ Передать Дмитрию полный deep-dive промпт.
4. ⏭️ Дмитрий запускает новую сессию исполнителя.
5. ⏭️ Checkpoint 1 от исполнителя (после Фазы 1 stabilization) — ожидаю через ~15 минут.
6. ⏭️ Checkpoint 2 (после Фазы 2 investigation) — через ~60-90 минут.
7. ⏭️ Decision вместе с Дмитрием по стратегии (A/B/C/D).
8. ⏭️ Фаза 3 execute, Фаза 4 close.

## ❓ Pending questions to Дмитрий

Нет pending вопросов. Ждём рапорт от нового исполнителя.

## 📊 State snapshot для нового исполнителя

### ✅ Done (НЕ повторять)

- `/usr/local/bin/wal-g v3.0.8` установлен (хост + db-контейнер через bind mount).
- `/etc/wal-g/walg.env` создан, chmod 600, R2 creds валидны.
- R2 bucket `proficrm-walg-staging` создан (2026-04-23 11:41:08 UTC) — empty.
- `docker-compose.staging.yml` коммит `9b3e956a` — bind mounts.
- Permissions fix `/var/lib/postgresql/data/pg_wal/walg_data/` → postgres:postgres (успешный).
- `archive_mode = on`.
- `scripts/backup_postgres_staging.sh` + cron 03:30 UTC работают (safety net).

### 🔴 Broken

- **Wrapper script bug:** `/etc/wal-g/archive-command.sh` содержит `wal-push ""` вместо `wal-push "$1"`. Теряет `%p` параметр.
- **Container networking:** wal-g в db-контейнере не коннектится к R2 (IPv6 hang → HTTP/2 handshake failure). Host — работает.
- **archive_command** = `/etc/wal-g/archive-command.sh %p`, running every минуту, timeout'ит → postgres kill → recovery. Цикл продолжается.
- **R2 bucket:** пустой.

### 📊 Evidence

- postgres logs last hour: 3 recovery цикла (14:57, 15:02 exit 124, 15:11 SIGTERM).
- `pg_stat_archiver`: archived_count=0, failed_count=0 (reset после recovery).
- `wal-g st ls basebackups_005/` + `wal_005/`: empty.
- disk `/`: 24 GB свободно.

## 🚨 Red flags (if any)

- **Postgres crash loop продолжается до Фазы 1 Step 1.** Чем быстрее новая сессия стартует, тем меньше recovery событий.

## 📝 Running notes

### Фазы нового промпта

1. **Фаза 1 STABILIZE** (~15 мин): остановить crash loop, fix wrapper bug, confirm stable. **Checkpoint PM.**
2. **Фаза 2 INVESTIGATE** (~30-60 мин): reproduce issue, Context7 research, test fixes. **Checkpoint PM с рекомендацией.**
3. **Фаза 3 EXECUTE** (conditional на PM approval): chosen strategy (fix-in-container / host-pivot / rollback).
4. **Фаза 4 CLOSE** (~15-30 мин): runbook + rapport + commits.

### Decision options для Фазы 2 checkpoint

- **A — Fix in container:** simple config change решает (например `AWS_DISABLE_CONCURRENT_UPLOAD` или Docker IPv6 settings). Best case.
- **B — Pivot host-level wal-g:** archive_command в контейнере пишет WAL в shared spool directory, host cron push'ит в R2. Обходит container networking.
- **C — Rollback completely:** archive_mode=off, вернуться к pg_dump only. W10.2-early → MIXED result. Worst case.
- **D — Retry with different approach:** например `restic` вместо `wal-g`, или Backblaze B2 вместо Cloudflare R2 — смена tools. Nuclear option.

### Lesson candidates (накопившиеся 12-14, добавить после closure)

- L9 — explicit safe channel для секретов.
- L10 — cloud service activation ≠ credentials.
- L11 — Cloudflare API не даёт permanent S3-tokens, планировать dashboard step.
- L12 — Never trust pg_stat_archiver alone (cross-check bucket listing).
- L13 — Container networking ≠ host networking, тест до архитектурного commit.
- L14 — Wrapper scripts для archive_command — обязательный тест с реальным `%p`.

### Update triggers (reminder)

- Перед каждой передачей Дмитрию.
- Каждые 30-60 минут активной работы.
- После получения рапорта исполнителя.
- После принятия решения.
- Перед длительной операцией.
- При приближении к компактификации контекста.
