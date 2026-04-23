# PM current context

_Живое состояние текущей PM-сессии. PM обновляет этот файл перед предсказуемым compact или каждые 30-60 минут активной работы. После compact — читается ПЕРВЫМ для восстановления контекста._

**Last updated:** 2026-04-24 12:25 UTC (PM).

---

## 🎯 Current session goal

W10.2-early unblocked: R2 S3 creds доставлены Дмитрием на VPS (2026-04-24 ~12:20 UTC). Исполнитель resume'ится с Шага 2 по оригинальному промпту — WAL-G install → archive_command → full backup → restore drill → runbook. Ожидаемо 4-5 часов.

## 📋 Active constraints

- Path E: **ACTIVE**.
- R2 bucket `proficrm-walg-staging` ✅ создан (2026-04-24 11:41 UTC).
- R2 S3 credentials в `/opt/proficrm-staging/.env` (R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME, R2_ENDPOINT), пермишены 600.
- `CF_API_TOKEN` свою роль выполнил (активация + bucket create). После успешного завершения W10.2-early можно revoke.
- Защитный слой pg_dump работает.
- Disk 23 ГБ свободно.

## 🔄 Last decision made

**Timestamp:** 2026-04-24 12:25 UTC.
**Decision:** передать исполнителю короткое resume от Шага 2. Оригинальный промпт W10.2-early остаётся в силе.
**Reasoning:** Шаг 1 fully closed (bucket + credentials). Шаги 2-7 идентичны оригинальному плану.
**Owner:** PM (resume сообщение), Дмитрий (copy-paste).

## ⏭️ Next expected action

1. ✅ Обновить `docs/pm/current-context.md`.
2. ✅ Коммит.
3. ⏭️ Передать Дмитрию короткое resume-сообщение.
4. ⏭️ Координация на Шаге 3 (restart Postgres, ~2 минуты простоя стейджинга) — ожидать запрос исполнителя на «ok to restart».
5. ⏭️ Ждать финальный рапорт W10.2-early через 4-5 часов.
6. ⏭️ После рапорта — review restore drill proof + classification + закрытие сессии + написание Lessons 9-11 в lessons-learned.md.

## ❓ Pending questions to Дмитрий

- [ ] На Шаге 3 исполнитель попросит ok на 2-минутный рестарт Postgres стейджинга. Просто ответь «ok» когда он уточнит.

## 📊 Last Executor rapport summary

**Session:** W10.2-early Шаги 1a-1c.
**Received:** 2026-04-24 12:05 UTC.
**Status:** 🟡 PARTIAL → UNBLOCKED (R2 S3 creds доставлены 12:20 UTC).
**Classification:** win.

Следующий рапорт: финальный W10.2-early end-to-end через 4-5 часов.

## 🚨 Red flags (if any)

Нет.

## 📝 Running notes

### Scope оставшейся сессии (Шаги 2-7)

- **Шаг 2:** WAL-G v3.0.3 binary в `/usr/local/bin/wal-g`. `/etc/wal-g/walg.env` конфиг с R2 creds из `.env.staging`. Test `wal-g st ls` → bucket reachable, empty.
- **Шаг 3:** `ALTER SYSTEM SET archive_mode/archive_command/archive_timeout/wal_level`. Volume mount `/etc/wal-g` в db-контейнер (изменение `docker-compose.staging.yml`, коммит). Restart db (breaking, ~2 мин). Verify `pg_stat_archiver.archived_count > 0`.
- **Шаг 4:** `wal-g backup-push /var/lib/postgresql/data`. Verify `wal-g backup-list --pretty`. Monitor 1 час archive push.
- **Шаг 5:** Mandatory restore drill — отдельный контейнер `db-drill`, wal-g backup-fetch LATEST, recovery.signal + restore_command, row counts match с staging primary.
- **Шаг 6:** Runbook `docs/runbooks/2026-04-24-wal-g-pitr.md` + cron `/etc/cron.d/proficrm-walg-retention` (weekly full + retention 4 недели).
- **Шаг 7:** Smoke + rapport с mandatory items.

### Lessons (добавить после закрытия W10.2-early)

1. **Lesson 9** — PM failure указать explicit safe channel для секретов (incident с токеном в чате).
2. **Lesson 10** — cloud service activation ≠ credentials (R2 error 10042).
3. **Lesson 11** — Cloudflare API не даёт создавать permanent R2 S3 tokens через publicAPI (error 9109, design limitation). Для новых проектов: планировать 1 dashboard step или Terraform-managed tokens.

### Update triggers (reminder)

- Перед каждой передачей Дмитрию.
- Каждые 30-60 минут активной работы.
- После получения рапорта исполнителя.
- После принятия решения.
- Перед длительной операцией.
- При приближении к компактификации контекста.
