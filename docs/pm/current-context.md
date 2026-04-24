# PM current context

_Живое состояние текущей PM-сессии. PM обновляет этот файл перед предсказуемым compact или каждые 30-60 минут активной работы. После compact — читается ПЕРВЫМ для восстановления контекста._

**Last updated:** 2026-04-23 17:00 UTC (PM).

---

## 🎯 Current session goal

Фаза 2 INVESTIGATE **закрыта**: Scenario B подтверждён через write-path test. Контейнер блокирует HTTPS к R2 и на чтение, и на запись. Host-level wal-g работает (bucket виден с хоста). Жду decision Дмитрия между Option B.1 (host-pivot) vs deep-dive Option A через WebSearch vs других.

## 📋 Active constraints

- Path E: **ACTIVE**.
- Staging стабилен: `archive_command = '/bin/true'`, 7/7 containers healthy, 0 crash 30+ минут.
- Wrapper script `/etc/wal-g/archive-command.sh` fixed но не активен.
- R2 bucket всё ещё пустой.
- pg_dump safety net активен.
- MCP `context7` disconnected. WebSearch/WebFetch доступны как fallback.

## 🔄 Last decision made

**Timestamp:** 2026-04-23 17:00 UTC.
**Decision pending:** Дмитрий choices one of:
- **B.1** — host-pivot с shared spool + host cron (моя рекомендация).
- **B.2** — host-pivot с inotify watcher.
- **A** — retry fix-in-container с WebSearch research.
- **C** — rollback, закрыть W10.2-early как PARTIAL.
- **D** — change tool (restic / pgbackrest / etc).
**Reasoning:** архитектурное решение, scope change от original plan → требует Дмитрий approval.
**Owner:** Дмитрий.

## ⏭️ Next expected action

1. ✅ Обновить `docs/pm/current-context.md`.
2. ✅ Коммит.
3. ⏭️ Брифинг Дмитрию + 5 options.
4. ⏭️ Decision.
5. ⏭️ Если B.1 — передать исполнителю Фазу 3 promт (draft готовится параллельно).
6. ⏭️ Фаза 4 close.

## ❓ Pending questions to Дмитрий

- [ ] **Architectural decision:** B.1 / B.2 / A / C / D — какую стратегию берём для Фазы 3?
- [ ] Если B.1 — ADR update для фиксации architecture change (host-level wal-g вместо in-container) — ok?

## 📊 Checkpoint 2 findings (write-path test)

### Evidence

| Path | From host | From container |
|------|-----------|----------------|
| `st ls /` read         | ✅ test/ dir seen   | ❌ timeout 30s |
| `wal-push` write 16MB  | not tested         | ❌ timeout 120s, EXIT=124 |
| Config parse + creds auth | ✅ | ✅ (INFO log подтверждает) |

### Interpretation

- wal-g **видит** `/etc/wal-g/walg.env` в контейнере (INFO лог + credentials parsed).
- **HTTPS layer блокирован** в контейнере независимо от read/write direction.
- С хоста — works perfectly.
- Значит блокер — **networking-layer** (TLS/IPv6/HTTP2/glibc NSS), не auth / конфиг / code.

Evidence ранее (read-path): `st ls` hang 30s.
Evidence сейчас (write-path): `wal-push` hang 120s.
Pattern consistent — тот же HTTPS-blocker.

## 🚨 Red flags (if any)

Нет. Staging стабилен. Это обычный архитектурный pivot point.

## 📝 Running notes

### Options для Фазы 3 (ranked recommendation)

1. **⭐ B.1 — host-pivot: shared spool + host cron** (моя top choice).
   - archive_command в контейнере: `cp %p /var/lib/proficrm-staging/wal-spool/` (local only, fast).
   - Host cron каждую минуту: `wal-g wal-push <oldest-file>` → R2. После success — удалить из spool.
   - Base backup: с хоста напрямую (не через контейнер).
   - **Плюсы:** reuse существующий wal-g + walg.env на хосте, archive_command trivial/fast, PostgreSQL happy.
   - **Минусы:** 1-min RPO (acceptable для staging), spool storage ~1 GB/день peak, delete-after-success discipline в cron script.
   - **Time to implement:** 1.5-2h.

2. **B.2 — host-pivot: inotify watcher service** (альтернатива B.1).
   - systemd service с inotifywait на spool dir.
   - Push в R2 немедленно при создании файла в spool.
   - **Плюсы:** near-zero lag.
   - **Минусы:** новый service, больше moving parts, overkill для staging PITR.
   - **Time:** 2-2.5h.

3. **A — retry fix-in-container** (low probability of success без Context7).
   - WebSearch research: wal-g Docker HTTPS hang, IPv6 NSS ordering, HTTP/2 in container.
   - Candidate fixes: `GODEBUG=netdns=go+4`, Docker IPv6 enable, MTU, network_mode: host.
   - **Плюсы:** если работает — оригинальная архитектура preserved.
   - **Минусы:** trial-and-error без надёжных docs, 30-60 мин research + ещё debugging. Риск прийти к B.1 после потери времени.
   - **Time:** 1-3h с uncertain outcome.

4. **C — rollback + close PARTIAL.**
   - `archive_command='/bin/true'` остаётся, wrapper не активирован.
   - W10.2-early status PARTIAL (bucket + creds есть; PITR не work).
   - Новый хотлист item для retry.
   - **Плюсы:** immediate closure, stable baseline.
   - **Минусы:** потеря всей работы дня, пре-W9 deploy rehearsal без PITR capability.
   - **Time:** 20 мин.

5. **D — change tool (restic / pgbackrest / Backblaze B2).**
   - Nuclear option, стирает 90% сделанного.
   - Нужен новый ADR.
   - **Плюсы:** потенциально обходит Cloudflare R2 specifics.
   - **Минусы:** ре-learning, 4-6h новая сессия.
   - **Time:** 4-6h отдельно.

### Почему моя рекомендация B.1

- Evidence definitive (Scenario B classified clean).
- B.1 reuses existing artifacts (wal-g binary, walg.env, bucket, creds) — не ломает сделанное.
- archive_command trivial (`cp`) — не может timeout'нуть, postgres stable.
- Host-level wal-g уже tested (работает).
- Time budget реалистичный (1.5-2h в оставшемся окне).
- 1-min RPO приемлем на staging.

### ADR update scope (если B.1)

`docs/decisions/2026-04-24-wal-g-r2-bridge-to-minio.md`:

- §Actual Implementation (new section) — describe host-level wal-g + shared spool вместо in-container.
- §Known Issues — container HTTPS blocker, evidence, workaround.
- §Migration plan к MinIO — адаптировать для host-level setup (переменит endpoint, spool и cron остаются).

Title ADR остаётся same (decision о R2 bridge), но implementation details другие.

### Lesson candidates (накопившиеся 9-16)

Плюс:
- L17 — «Container HTTPS hang = try host-level tool as pivot BEFORE Docker networking deep-dive. Faster path to working system.»

### Update triggers (reminder)

- Перед каждой передачей Дмитрию.
- Каждые 30-60 минут активной работы.
- После получения рапорта исполнителя.
- После принятия решения.
- Перед длительной операцией.
- При приближении к компактификации контекста.
