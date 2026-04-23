# PM current context

_Живое состояние текущей PM-сессии. PM обновляет этот файл перед предсказуемым compact или каждые 30-60 минут активной работы. После compact — читается ПЕРВЫМ для восстановления контекста._

**Last updated:** 2026-04-24 14:05 UTC (PM).

---

## 🎯 Current session goal

W10.2-early 🔴 **BLOCKED критично**: archive_command активен, но **R2 bucket пустой**, WAL-G не загружает ни WAL archives ни base backups. Причина — permission denied на директории `/var/lib/postgresql/data/pg_wal/walg_data/walg_archive_status/` (root-owned вместо postgres-owned). Требуется fix permissions + retry.

## 📋 Active constraints

- Path E: **ACTIVE**.
- Staging API работает (HTTP 200), все 7 контейнеров healthy.
- Защитный слой pg_dump активен — данные не потеряются если потребуется rollback archive_mode.
- `pg_stat_archiver` показывает `archived_count=31, failed_count=0` — **ложная информация** (postgres увеличивает счётчик когда `archive_command` вернул exit 0, но на самом деле WAL не в R2).
- R2 bucket `proficrm-walg-staging` **пустой** — проверено `wal-g st ls` с префиксами `basebackups_005/` и `wal_005/`.

## 🔄 Last decision made

**Timestamp:** 2026-04-24 14:05 UTC.
**Decision:** mini-fix сессия — chown директорий, retry backup-push, verify R2. После успеха — resume оригинальный план с Шага 4b.
**Reasoning:** проблема простая (permissions), fix локальный, pg_dump safety net активен, не требуется rollback archive_command.
**Owner:** PM даёт mini-промпт, Дмитрий copy-paste в **новое** окно исполнителя.

## ⏭️ Next expected action

1. ✅ Обновить `docs/pm/current-context.md`.
2. ✅ Коммит.
3. ⏭️ Передать Дмитрию mini-промпт «fix permissions + retry backup-push».
4. ⏭️ Ждать рапорт fix-сессии.
5. ⏭️ После успеха — resume main промпт с Шага 4b.

## ❓ Pending questions to Дмитрий

- [ ] Запустить fix-сессию исполнителя с mini-промптом (ниже в брифинге).

## 📊 Diagnostic findings (PM side, 14:00-14:05 UTC)

### Состояние процесса

- wal-g backup-push **завершился** (PID 2328740 больше не в ps).
- Результат upload в R2: **ничего** (bucket root + подпрефиксы пусты).

### Логи db-контейнера (последние 80 строк)

```
ERROR: unmark wal-g status for file failed due following error 
       remove /var/lib/postgresql/data/pg_wal/walg_data/walg_archive_status: 
       permission denied
```

Повторяется **каждую минуту** (при каждом `archive_command`).

### Permissions check

```
drwxr-xr-x 3 root     root     4096 Apr 23 12:51 walg_data    ← root:root (wrong)
drwx------ 4 postgres postgres 4096 Apr 23 14:03 pg_wal       ← postgres:postgres (correct)
drwxr-xr-x 2 root     root     4096 Apr 23 12:51 walg_archive_status  ← root:root (wrong)
```

**Root cause:** при первом запуске `wal-g backup-push` через `docker compose exec db` исполнитель работал как `root` (default для `docker exec`), wal-g создал директории `walg_data/` + `walg_archive_status/` как `root:root`. PostgreSQL запущен как `postgres` (UID 999) — не может писать в root-owned директории. Все последующие `archive_command` попытки fail silently.

### R2 bucket state

- `wal-g st ls basebackups_005/` → empty.
- `wal-g st ls wal_005/` → empty.
- bucket полностью пустой.

### Что НЕ пострадало

- Staging API — HTTP 200, работает.
- Контейнеры все healthy.
- Данные БД — целы.
- pg_dump cron safety net — активен.

## 🚨 Red flags (if any)

### 🔴 Ложный положительный `pg_stat_archiver.archived_count`

Postgres считает archive_command успешным если exit code = 0. WAL-G видимо возвращает 0 даже когда не может записать local status file (или upload в R2 не происходит, но по другим причинам wal-g не сигнализирует failure).

Это значит **нельзя доверять `archived_count` как proof что WAL в R2**. Нужна cross-проверка через `wal-g st ls` или `wal-g backup-list`.

### 🟡 Lesson candidate (Lesson 12)

«Never trust pg_stat_archiver alone — always verify R2 bucket listing before объявлять success». Добавить после closure.

## 📝 Running notes

### Fix plan (для mini-промпта)

1. Fix permissions:
   ```bash
   docker compose exec db chown -R postgres:postgres /var/lib/postgresql/data/pg_wal/walg_data/
   ```
2. Verify логи — errors должны исчезнуть в течение 1-2 минут (next archive_command attempt).
3. Clear any partial state (check что нет stale lock файлов).
4. Retry backup-push как `postgres` user:
   ```bash
   docker compose exec -u postgres -T db bash -c "set -a; . /etc/wal-g/walg.env; set +a; /usr/local/bin/wal-g backup-push /var/lib/postgresql/data"
   ```
   Ключевое отличие: `-u postgres` вместо default root.
5. Verify:
   - `wal-g backup-list --pretty` → должен показать 1 backup.
   - `wal-g st ls basebackups_005/` → не empty.
   - Логи db — 0 `ERROR: unmark wal-g status` за последние 2 минуты.
   - `pg_stat_archiver.archived_count` продолжает расти (теперь с реальным upload).

### После fix — resume оригинальный план

Шаг 4b (monitor 1 час) → Шаг 5 restore drill (critical) → Шаги 6-7.

### Update triggers (reminder)

- Перед каждой передачей Дмитрию.
- Каждые 30-60 минут активной работы.
- После получения рапорта исполнителя.
- После принятия решения.
- Перед длительной операцией.
- При приближении к компактификации контекста.
