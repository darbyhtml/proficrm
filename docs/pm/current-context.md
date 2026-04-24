# PM current context

_Живое состояние текущей PM-сессии. PM обновляет этот файл перед предсказуемым compact или каждые 30-60 минут активной работы. После compact — читается ПЕРВЫМ для восстановления контекста._

**Last updated:** 2026-04-23 18:40 UTC (PM).

---

## 🎯 Current session goal

**Фаза 3.1 закрыта успешно.** Host-pivot архитектура работает end-to-end — WAL `000000010000009B000000DA` долетел до R2 bucket (171 bytes compressed). Исполнитель ждёт approval на Sub-phase 3.2 (first base backup + restore drill).

## 📋 Active constraints

- Path E: **ACTIVE**.
- Staging API HTTP 200, 7/7 containers healthy.
- archive_command = `/etc/wal-g/archive-command.sh %p` (активно, reactivated).
- Host cron `/etc/cron.d/proficrm-walg-spool` — каждую минуту, работает.
- R2 bucket содержит первый WAL (эpoch начала полноценного archiving).
- pg_dump safety net активен.
- **Staging postgres port изменён на `127.0.0.1:15432:5432`** (не 5432 как в моём оригинальном промпте) из-за prod port conflict.

## 🔄 Last decision made

**Timestamp:** 2026-04-23 18:40 UTC.
**Decision pending:** approval на Sub-phase 3.2 + как обработать 2 side-findings (prod port exposure + downtime 4 мин).
**Owner:** Дмитрий (formal approval 3.2), PM (hotlist item для prod port).

## ⏭️ Next expected action

1. ✅ Обновить `docs/pm/current-context.md`.
2. ✅ Добавить CRITICAL hotlist item про prod 0.0.0.0:5432.
3. ✅ Коммит.
4. ⏭️ Передать Дмитрию брифинг + approve 3.2 с port adjustment.
5. ⏭️ Ждать Checkpoint 3.2 через ~60 минут.

## ❓ Pending questions to Дмитрий

- [ ] **Approve Sub-phase 3.2?** Base backup + restore drill через host-level helper. Ожидаемое время ~60 минут.
- [ ] **Prod postgres exposure** 0.0.0.0:5432 — critical security finding. Добавил в hotlist как CRITICAL, но отдельный fix-session рекомендую **после W10.2-early closure** (не смешивать scope). OK?
- [ ] **Downtime 4 мин** в 3.1 (вместо ожидаемых 30-60 сек) — acceptable для staging (not prod), не блокирующий. Lesson candidate 18: «port mapping changes в multi-env на одном VPS требуют port audit ДО mapping». OK?

## 📊 Checkpoint 3.1 findings

### Successful

- Spool dir + ownership (UID 999, chmod 755).
- docker-compose mounts (+ spool + port).
- archive-command.sh rewrite (cp с atomic .tmp→rename).
- Host cron script + entry.
- WAL switch trigger → archive → spool → cron → R2.
- First WAL `000000010000009B000000DA.br` in R2 (171 bytes).
- pg_stat_archiver: archived_count=646 cumulative, **failed_count=0, last_failed_time=NULL** (нет failed attempts в persistent stats — silent loss был pre-reset).
- Smoke 6/6, containers 7/7 healthy.

### 🚨 Side-finding CRITICAL: prod postgres exposure

**Prod postgres слушает 0.0.0.0:5432** (публично на весь интернет) на том же VPS что staging.

```
LISTEN 0.0.0.0:5432   docker-proxy pid=156678
LISTEN [::]:5432      docker-proxy pid=156684
```

Это не часть W10.2-early scope, но **Severity HIGH** — добавляю в hotlist как critical security issue. Fix отдельной сессией после W10.2-early closure.

### ⚠️ Incident: 4-минутный downtime в 3.1

Port mapping `127.0.0.1:5432:5432` conflict с prod postgres → db не смог стартовать → retry циклы → ~4 мин API down. Исполнитель быстро обнаружил, поменял на `127.0.0.1:15432:5432`, восстановил web/nginx (DNS к db устарел после recreate).

Mitigation lesson: port audit на VPS ДО mapping changes в multi-env deployment.

## 🚨 Red flags (if any)

- **CRITICAL security:** prod postgres public exposure (`0.0.0.0:5432`). Hotlist item создан, fix отдельной сессией.
- **⚠️ Lesson 18 candidate:** port audit ДО mapping.

## 📝 Running notes

### Adjustment для Sub-phase 3.2 промпта

**Port:** staging postgres теперь `127.0.0.1:15432` (не 5432). В walg.env нужно добавить:
- `PGPASSWORD=<value>` (Step 3.2.1 как раньше).
- **`PGPORT=15432`** (новое, для host wal-g backup-push).
- `PGHOST=127.0.0.1` (уже есть, `localhost` эквивалентен).

Helper `docker-compose.walg-backup.yml` + drill `docker-compose.walg-drill.yml` уже используют `network_mode: host`, значит `PGHOST=127.0.0.1 PGPORT=15432` в их окружении будет подключаться к правильной staging db.

### Lesson candidates update (9-18)

- L18 (new) — port conflict audit ДО mapping changes в multi-env VPS.

### Update triggers (reminder)

- Перед каждой передачей Дмитрию.
- Каждые 30-60 минут активной работы.
- После получения рапорта исполнителя.
- После принятия решения.
- Перед длительной операцией.
- При приближении к компактификации контекста.
