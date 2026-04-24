# PM current context

_Живое состояние текущей PM-сессии. PM обновляет этот файл перед предсказуемым compact или каждые 30-60 минут активной работы. После compact — читается ПЕРВЫМ для восстановления контекста._

**Last updated:** 2026-04-23 16:40 UTC (PM).

---

## 🎯 Current session goal

Фаза 1 STABILIZE **закрыта успешно**. Ready к Фазе 2 INVESTIGATE с фокусом на **write-path test** (реальный WAL файл через fixed wrapper — работает ли upload в R2 из контейнера?). Это дешёвый эксперимент ~2-5 минут, различит Option A (fix-in-container работает) vs Option B (нужен host-level pivot).

## 📋 Active constraints

- Path E: **ACTIVE**.
- Staging API HTTP 200, все 7 контейнеров healthy, 0 crash events 10 минут.
- `archive_command = '/bin/true'` (safe default). Staging не crash'ит.
- Wrapper script `/etc/wal-g/archive-command.sh` **fixed** (`wal-push "$1"`), но **не активен** — reactivate одним `ALTER SYSTEM` когда решим Фазу 2.
- pg_dump safety net активен (вчерашний backup 03:30 UTC есть).
- MCP `context7` и `playwright` **disconnected** — если Фазе 2 нужен research, fallback через WebSearch/WebFetch.

## 🔄 Last decision made

**Timestamp:** 2026-04-23 16:40 UTC.
**Decision:** approve Фазу 2 с начальным write-path тестом (manual `archive-command.sh /path/to/real/WAL`). Contingent на результат — fix-in-container или pivot.
**Reasoning:** silent-loss подтверждён как root cause потерянных archives (wrapper bug, не networking). Container networking blocker может оказаться **только** в read-path (`st ls` timeout) — write-path не протестирован. Дешёвый тест даёт максимум эпистемической выгоды.
**Owner:** Дмитрий approved Фазу 2, PM даёт specifics.

## ⏭️ Next expected action

1. ✅ Обновить `docs/pm/current-context.md` (этот файл).
2. ✅ Коммит.
3. ⏭️ Передать исполнителю approval Фазы 2 с focus на write-path test.
4. ⏭️ Ждать Checkpoint 2 с результатом теста + рекомендацией A/B/C/D.
5. ⏭️ Решение вместе с Дмитрием.
6. ⏭️ Фаза 3 execute.
7. ⏭️ Фаза 4 close.

## ❓ Pending questions to Дмитрий

Нет. Decision A/B/C/D ожидается от Дмитрия после Checkpoint 2.

## 📊 State snapshot post-Checkpoint-1

### Фаза 1 Actions (все успешны)

| Step | Action | Result |
|------|--------|--------|
| 0a | checkout feature-ветки | ✅ HEAD `8e77027c`, артефакты видны |
| 0b | read-only audit staging (4 теста) | ✅ ground truth получен |
| 1 | `archive_command = '/bin/true'` + reload | ✅ crash loop остановлен |
| 2 | wrapper fix `wal-push "$1"` | ✅ на disk, chmod +x, ready |
| 3 | smoke + stability check | ✅ 6/6 green, 0 crash за 10 мин |

### Ground truth findings

| Finding | Evidence |
|---------|----------|
| **R2 bucket реально пустой** | `wal-g st ls` с хоста (где network работает) — basebackups_005/, wal_005/ empty |
| **Silent-loss через wrapper bug** | `wal-g wal-push ""` exit 0 за миллисекунды, 0 bytes uploaded |
| **`archived_count=48` — всё lies** | 48 WAL «архивированы» по счётчику postgres, 0 в R2 |
| **Local WAL сегменты удалены** | `.done` файлы удалены вместе с WAL — **реальная потеря PITR window** с ~12:00 до 15:11 UTC |
| **Crash loop окончился САМ в 15:11 UTC** | До моего PM diagnostic 15:20 UTC. stats_reset на этот же timestamp. |
| **Container networking — только read** | `st ls` из контейнера timeout 30s. Write-path НЕ тестирован. |
| **Дата** | Server UTC `2026-04-23 16:32` — **сегодня 23-е, не 24-е** (мой drift исправлен) |

### Data loss assessment

**Потеряно:** WAL archives с момента `archive_mode=on` (~12:00 UTC) по `/bin/true` активация (~16:10 UTC). Это ≈4 часа transactions. Staging — тестовое окружение, data loss приемлем в пределах business impact (тестеры, не прод).

**Остаётся:** pg_dump вчерашний + текущее состояние БД (Postgres сам успешно оперировал на этих WAL перед их «архивацией»). Полная recovery window до последнего pg_dump (24h).

**Урок:** Lesson 12 (never trust pg_stat_archiver alone) уже candidate.

## 🚨 Red flags (if any)

### 🟢 Resolved

- Crash loop stopped (Step 1 `/bin/true`).
- Wrapper bug fixed (Step 2).
- Staging stable confirmed (Step 3).

### 🟡 PM self-detected drift (ранее)

- Я писал «2026-04-24» в sequential updates `current-context.md`, commit messages. **Фактическая дата — 2026-04-23**. Подтверждено VPS UTC. Исправлено в этом update. **Lesson 15 candidate** — PM sync даты через `date` command.

### 🟢 Stale PM context (resolved)

Мой diagnostic в 15:10-15:20 UTC писал «postgres в crash loop сейчас». На деле crash loop остановился **в 15:11 UTC** (stats_reset timestamp), то есть за ~9 минут до моего update. Новый исполнитель поймал drift. Это не PM failure — это **temporal staleness** inherent для любого snapshot. Полезно помнить: при delegation после передачи context исполнитель должен **re-verify** (что он и сделал).

## 📝 Running notes

### Фаза 2 Specifics

**Первый тест (write-path):**

```bash
# Изолированный test через temporary WAL file.
# Не trigger реальный archive_command (оставляем /bin/true до подтверждения).
ssh root@5.181.254.172 'docker compose -f /opt/proficrm-staging/docker-compose.staging.yml -p proficrm-staging exec -u postgres -T db bash -c "
  # Создать fake WAL file для теста (16MB null).
  TESTFILE=/tmp/test_wal_$(date +%s)
  dd if=/dev/zero of=\$TESTFILE bs=1M count=16 2>/dev/null
  
  # Trigger wrapper — должен попытаться upload в R2.
  set -a; . /etc/wal-g/walg.env; set +a
  START=\$(date +%s%N)
  timeout 120 /usr/local/bin/wal-g wal-push \"\$TESTFILE\"
  EXIT=\$?
  END=\$(date +%s%N)
  echo EXIT=\$EXIT
  echo DURATION_MS=\$(( (END - START) / 1000000 ))
  rm -f \$TESTFILE
"'

# Затем проверка что файл реально в R2 (с хоста).
ssh root@5.181.254.172 'set -a; . /etc/wal-g/walg.env; set +a; /usr/local/bin/wal-g st ls wal_005/ 2>&1 | head -10'
```

Expected outcomes:

- **Scenario A (write works):** EXIT=0, DURATION < 30s, `st ls wal_005/` shows uploaded file. → Option A fix-in-container. Restore wrapper activation, full backup-push, restore drill.
- **Scenario B (write hangs):** EXIT=124 (timeout), DURATION=120000ms. → Container networking blocker для write тоже. Переход к Option B (host-pivot) или Option D (different tool).
- **Scenario C (other failure):** EXIT non-zero but not timeout. Investigate specific error.

### Options A/B/C/D (для Checkpoint 2)

- **A — fix-in-container works:** reactivate wrapper, full backup-push, restore drill, close Фаза 3 → Фаза 4 runbook. 1.5-2h оставшегося времени.
- **B — host-pivot:** архитектурный redesign (archive_command в контейнере → shared spool; host cron → R2 push). ADR update. 3-4h.
- **C — rollback:** закрыть W10.2-early как PARTIAL (bucket, creds, script есть; PITR не работает). pg_dump safety net остаётся. Новый hotlist item для retry позже.
- **D — change tools:** restic / pgbackrest / Backblaze B2 вместо Cloudflare R2. Nuclear option, новый ADR.

### Lesson candidates (после W10.2-early closure)

- L9 — explicit safe channel для секретов.
- L10 — cloud service activation ≠ credentials (R2 10042).
- L11 — Cloudflare API не даёт permanent S3-tokens.
- L12 — Never trust `pg_stat_archiver` alone — cross-check bucket listing.
- L13 — Container networking ≠ host networking, тестировать оба пути.
- L14 — Wrapper scripts для archive_command — обязательный тест с реальным `%p`.
- L15 — PM sync даты через `date` command, не полагаться на implicit память.
- L16 (new) — temporal staleness in delegation: execut должен re-verify PM context if > 10 min old.

### Update triggers (reminder)

- Перед каждой передачей Дмитрию.
- Каждые 30-60 минут активной работы.
- После получения рапорта исполнителя.
- После принятия решения.
- Перед длительной операцией.
- При приближении к компактификации контекста.
