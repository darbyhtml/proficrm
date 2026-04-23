# PM current context

_Живое состояние текущей PM-сессии. PM обновляет этот файл перед предсказуемым compact или каждые 30-60 минут активной работы. После compact — читается ПЕРВЫМ для восстановления контекста._

**Last updated:** 2026-04-24 10:25 UTC (PM).

---

## 🎯 Current session goal

**Pivot B confirmed Дмитрием.** Staging `pg_dump` mini-session сначала (15-30 min) — даст safety net. Потом resume W10.2-early с Step 1 после delivery R2 credentials. PM сейчас пишет mini-промпт.

## 📋 Active constraints

- Path E: **ACTIVE** (prod freeze до W9).
- Executor mode: staging-only.
- Current wave focus: staging `pg_dump` cron (prerequisite для W10.2-early).
- ADR assumption error fixed в `2026-04-24-wal-g-r2-bridge-to-minio.md` §Consequences (corrected 10:25 UTC).

## 🔄 Last decision made

**Timestamp:** 2026-04-24 10:20 UTC.
**Decision:** Pivot B — сначала staging `pg_dump` mini-session, потом W10.2-early.
**Reasoning:** safety net перед WAL-G rollout обязателен (Pattern 3 defense-in-depth). Executor Step 0 audit (10:10 UTC) обнаружил gap, моя рекомендация B принята Дмитрием.
**Owner:** Дмитрий approved. PM executes.

## ⏭️ Next expected action

1. ✅ Update current-context.md (этот файл).
2. ✅ Fix ADR §Consequences про defense-in-depth.
3. ✅ Add hotlist item «staging pg_dump cron».
4. ✅ Commit три файла.
5. ⏭️ Написать mini-промпт «staging pg_dump cron setup» для Executor.
6. ⏭️ Передать Дмитрию с confirmation request о worktree coordination.
7. ⏭️ После rapport mini-session → close hotlist item → deliver R2 creds → resume W10.2-early промпт.

## ❓ Pending questions to Дмитрий

- [ ] **Executor worktree coordination:** Executor не видел ADR commit `32e9121b` в rapport (Step 0 `ls docs/decisions/` показал только 2 файла 2026-04-21). Откуда Executor checkout'ит код? Варианты:
  - На main branch без pull → нужно `git fetch && git checkout claude/recursing-elgamal-c31a17`.
  - В отдельном worktree → нужно синхронизация.
  - Работает напрямую на staging server clone → нужно `git pull origin claude/recursing-elgamal-c31a17`.
  Mini-промпт включает explicit `git status` + branch check в Step 0.
- [ ] **R2 credentials delivery** — отложено до после mini-session completion. Не вставлять в mini-промпт (не relevant для pg_dump).

## 📊 Last Executor rapport summary

**Session:** W10.2-early WAL-G setup, Step 0 audit.
**Received:** 2026-04-24 10:10 UTC.
**Status:** 🔴 BLOCKED (proper stop).
**Classification:** **win** (audit-first discipline, zero mutations, critical gap surfaced).
**Key finding:** staging pg_dump fallback отсутствует. PM ADR assumption error — fixed.

Next expected Executor rapport: staging pg_dump mini-session (15-30 min).

## 🚨 Red flags (if any)

- **RESOLVED 2026-04-24 10:25 UTC:** ADR §Consequences (positive) содержал неточное утверждение про staging pg_dump fallback. Fixed в этом commit'е (correction note добавлен в ADR с timestamp).

## 📝 Running notes

### Pivot rationale

Executor обнаружил gap через audit-first — это exactly то, что Pattern 1 предотвращает. Если бы PM + Executor сразу начали WAL-G без audit, archive_command hang или disk fill сломал бы staging без возможности rollback через pg_dump.

Mini-session scope: minimal (copy existing prod script с substituted paths + cron + verify). ~15-30 min Executor time.

### Что НЕ в scope mini-session

- WAL-G setup.
- R2 configuration.
- PostgreSQL archive_command changes.
- Удалять или модифицировать prod cron.
- Prod side anything.

### Learning candidate (не commit'ю сейчас)

**Lesson 8 candidate:** «ADR claims must be verified against actual state, not planned state».

Ситуация:
- PM писал ADR §Consequences utверждая defense-in-depth через pg_dump.
- Предполагал identity prod ↔ staging config.
- Executor audit обнаружил что это не true.

Lesson для future: перед writing ADR §Consequences — audit actual cross-environment state, не assume parity.

Буду commit'ить после pivot completion (когда есть full data point).

### Update triggers (reminder)

- Перед каждой передачей Дмитрию.
- Каждые 30-60 минут активной работы.
- После получения Executor rapport.
- После принятия decision.
- Перед long-running операцией.
- Когда conversation приближается к compact limit.
