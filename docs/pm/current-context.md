# PM current context

_Живое состояние текущей PM-сессии. PM обновляет этот файл перед предсказуемым compact или каждые 30-60 минут активной работы. После compact — читается ПЕРВЫМ для восстановления контекста._

**Last updated:** 2026-04-24 10:15 UTC (PM).

---

## 🎯 Current session goal

Review Executor rapport по W10.2-early — **🔴 BLOCKED на Step 0, proper stop**. Executor выполнил audit-only (zero mutations), обнаружил 2 blockers и 1 critical safety gap. Pivot decision pending от Дмитрия.

## 📋 Active constraints

- Path E: **ACTIVE** (prod freeze до W9).
- Executor mode: staging-only.
- Current wave focus: W10 infrastructure — W10.2-early **blocked**.
- **🔴 NEW DISCOVERY:** staging не имеет `pg_dump` fallback (cron только prod `/opt/proficrm`). Это **противоречит** утверждению ADR §Consequences (positive) о defense-in-depth. Safety net на staging **отсутствует**.

## 🔄 Last decision made

**Timestamp:** 2026-04-24 09:30 UTC.
**Decision:** Option B (R2 bridge), scope rename W10.2-early. ADR + hotlist + промпт написаны и commit'нуты (`32e9121b`).
**New pending decision:** pivot strategy после BLOCKED rapport — A / B / A+B (см. §Next expected action).
**Owner:** Дмитрий.

## ⏭️ Next expected action

Получить decision Дмитрия:

- **A:** Доставить R2 credentials → resume W10.2-early с Step 1. Риск: WAL-G сломает staging writes без pg_dump safety net.
- **B:** Pivot — сначала staging pg_dump mini-session (~15 min setup), потом W10.2-early unblock. Recommended.
- **A+B:** Параллельно — быстрый staging pg_dump + delivery R2 creds в одной coordinated передаче.

После decision PM:

1. Update ADR `2026-04-24-wal-g-r2-bridge-to-minio.md` — исправить §Consequences (positive) про defense-in-depth.
2. Update hotlist — add «staging pg_dump cron missing» как closed (если pg_dump session пройдёт) или open item.
3. Если B или A+B — написать mini-промпт «staging pg_dump cron setup» для Executor.
4. Resume W10.2-early промпт после unblock.

## ❓ Pending questions to Дмитрий

- [ ] **Pivot decision A / B / A+B.**
- [ ] **ADR visibility check:** Executor rapport цитирует `ls docs/decisions/` → 2 файла (2026-04-21-*), хотя я закоммитил ADR в `32e9121b`. Подозрение: Executor работает в другом worktree / на main без pull / прямо на staging server path. Нужно уточнить у Дмитрия — Executor-сессия откуда checkout'ит код? Нужно ли ему `git fetch && git checkout claude/recursing-elgamal-c31a17` или cherry-pick моих commits?

## 📊 Last Executor rapport summary

**Session:** W10.2-early WAL-G setup, Step 0 baseline + audit only.
**Received:** 2026-04-24 10:10 UTC (~8 минут после начала, под budget 30 min).
**Status:** 🔴 **BLOCKED** — proper stop at Step 0 per spec.
**Classification:** **win** (audit-first discipline exemplary, zero mutations, все stop conditions корректно triggered, detailed rapport с actionable findings).

### Key findings

**Blockers (spec-defined):**

1. R2 credentials не delivered в `.env.staging` (symlink → `.env`). Step 0 grep → `0 matches`.
2. ADR not visible в Executor checkout (см. pending question #2 о worktree coordination).

**Positive (clean slate для setup когда unblocked):**

- WAL-G binary not installed (expected).
- `archive_mode = off`, `archive_command = (disabled)` — no conflicting prior setup.
- `wal_level = replica` уже стоит (archive_mode=on restart нужен, wal_level — нет).
- `/etc/wal-g/` отсутствует.
- Staging DB 5.3 GB, `/` 79 GB total, 23 GB free.

**🔴 Critical surfaced risk (NEW):**

- **Staging не имеет pg_dump fallback.** `scripts/backup_postgres.sh` cron настроен только для prod `/opt/proficrm`, не для `/opt/proficrm-staging`. Если WAL-G setup сломает staging (archive_command hangs, disk fills) — **no rollback backup**.
- Это **противоречит** моему ADR §Consequences (positive) где я написал «daily pg_dump остаётся как fallback — defense-in-depth (Pattern 3)». **Для staging defense-in-depth отсутствует.**

**Secondary risks:**

- 23 GB free на `/` — acceptable initial, но 4-6 weeks WAL backlog может съесть до 15 GB. Monitor нужен.
- archive_mode=on requires restart — breaking action на staging. Координация нужна.

## 🚨 Red flags (if any)

- **2026-04-24 10:15 UTC:** ADR §Consequences (positive) содержит **неточное** утверждение про defense-in-depth на staging. Это ошибка PM в ADR при writing. Нужен update ADR после pivot decision.

## 📝 Running notes

### Почему это BLOCKED — это win, не issue

Executor правильно выполнил audit-first, правильно остановился на R2 creds missing (spec-defined stop condition), и правильно surfaced missing staging pg_dump fallback — **не указано в моём промпте**. Это именно behavior который Pattern 1 validate.

Сравнение с Test 4 (Critical review):
- Test 4 был short rapport ("W10.1 WAL-G COMPLETE") — 8 follow-up questions.
- Этот rapport был detailed — findings, risks, time actual, что НЕ сделано. Не требует follow-up — всё уже в rapport.

### Защита от recurrence

ADR должен быть переписан чтобы §Consequences (positive) отражало actual state, не planned state. Lesson: ADR assumptions должны быть verified через audit перед writing.

Возможно новый entry в `docs/pm/lessons-learned.md` — «Lesson 8: ADR claims must be verified against actual state» — после pivot completion.

### Update triggers (reminder)

- Перед каждой передачей Дмитрию.
- Каждые 30-60 минут активной работы.
- После получения Executor rapport.
- После принятия decision.
- Перед long-running операцией.
- Когда conversation приближается к compact limit.
