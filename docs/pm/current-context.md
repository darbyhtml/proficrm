# PM current context

_Живое состояние текущей PM-сессии. PM обновляет этот файл перед предсказуемым compact или каждые 30-60 минут активной работы. После compact — читается ПЕРВЫМ для восстановления контекста._

**Last updated:** 2026-04-24 13:40 UTC (PM).

---

## 🎯 Current session goal

W10.2-early — сессия исполнителя упала локально, но `wal-g backup-push` **продолжает работать на VPS** (PID 2328740, запущен 13:22 UTC). PM ждёт завершения процесса, затем передаёт Дмитрию compact resume-сообщение для нового окна.

## 📋 Active constraints

- Path E: **ACTIVE**.
- Шаг 3b ✅ завершён: 31 WAL archived, 0 failed, smoke 6/6 до падения сессии.
- `archive_command` активен и работает корректно.
- Все 7 контейнеров стейджинга healthy.
- Staging HTTP 200 (curl проверил 13:35 UTC).
- `wal-g backup-push` процесс живёт независимо от сессии исполнителя (запущен в docker exec, процесс в контейнере).

## 🔄 Last decision made

**Timestamp:** 2026-04-24 13:40 UTC.
**Decision:** ждать завершения `wal-g backup-push` (ожидаемо ещё 5-15 минут). НЕ trogать окно исполнителя — fresh process может запустить ещё один backup параллельно.
**Reasoning:** backup-push идёт внутри db-контейнера через `docker exec`, отвязан от сломанной сессии Claude Code. Процесс завершится самостоятельно, backup появится в R2 по завершении.
**Owner:** PM (monitoring), Дмитрий (wait).

## ⏭️ Next expected action

1. ✅ Обновить `docs/pm/current-context.md`.
2. ✅ Коммит.
3. ⏭️ Сказать Дмитрию: wait 10-15 минут, потом спроси «проверь wal-g».
4. ⏭️ При следующем turn — PM проверяет `backup-list --pretty`, если backup в R2 → compact resume-промпт для нового окна исполнителя.
5. ⏭️ Новый исполнитель продолжит с Шага 4b (monitor 1h archive push) → Шаг 5 restore drill → Шаги 6-7.

## ❓ Pending questions to Дмитрий

- [ ] Через 10-15 минут вернись с «проверь wal-g» — я сделаю sanity check на VPS, если backup-push завершён — дам resume-промпт.

## 📊 Last Executor rapport summary

**Session:** W10.2-early Шаги 3b завершены + Шаг 4 начат.
**Received via screenshot:** 2026-04-24 ~13:35 UTC.
**Status:** 🟡 SESSION DEAD / 🟢 PROCESS ALIVE.
**Classification:** technical failure (Claude Code сессия), but operational work continues.

### Что до падения было сделано

- Шаг 3b полный: mounts, ALTER SYSTEM, рестарт №2, verify.
- 31 WAL archived, 0 failed.
- Smoke check прошёл.
- Шаг 4 full base backup начат (wal-g backup-push).

### Findings из SSH diagnostic

- PID 2328740 (`/usr/local/bin/wal-g backup-push`) активен, started 13:22 UTC.
- backup-list ещё пустой — backup commit'ится только после полной загрузки в R2.
- Все 7 контейнеров healthy.
- pg_stat_archiver продолжает recording WAL.
- Мой envdir в diagnostic (engineering mistake) — не задело staging.

## 🚨 Red flags (if any)

### Операционный риск: параллельный backup-push

Если Дмитрий пошлёт промпт в сломанное окно или откроет новое с тем же промптом — fresh process не узнает что backup-push уже работает, попытается запустить параллельно. **Это плохо:**

- wal-g может конкурировать за pg_start_backup()/pg_stop_backup() lock.
- Двойная загрузка в R2 = double egress + потраченные Class A ops.
- Непредсказуемое состояние backup-list.

**Mitigation:** **wait** до завершения текущего backup-push, потом resume с чистого состояния.

## 📝 Running notes

### Почему процесс wal-g выжил без сессии Claude Code

Архитектура:
- Claude Code (локально) → background bash shell.
- Background shell → `ssh root@VPS` → `docker compose exec db bash -c "wal-g backup-push"`.
- **wal-g работает внутри db-контейнера**, не в ssh session.
- Когда Claude Code сессия упала → ssh отвалился → docker exec tail остался ждать stdout, но сам wal-g процесс в контейнере продолжает работать.
- Backup завершится, докер exec узнает exit code, только для stdout некому слушать. Это ОК — stdout был для логирования, не для control.

### Ожидаемый финиш wal-g backup-push

Размер стейджинг-БД 5.3 ГБ → после brotli compression ~700 МБ - 1.5 ГБ (compression ratio 87% на pg_dump не применим для base backup — base backup включает raw data files, compression хуже).

Upload до R2 на default speed VPS → можно ожидать 10-30 минут total. 13:22 + 20 мин = 13:42 UTC. Сейчас 13:40 — скоро.

### После backup завершения

Compact resume-промпт для нового окна исполнителя:

> **W10.2-early — RESUME с Шага 4b (новое окно исполнителя).**
>
> Session 12:50-13:30 UTC упала локально, но wal-g backup-push завершился успешно на VPS.
>
> Шаги 3b + 4a ✅ done:
> - archive_command active (`envdir /etc/wal-g /usr/local/bin/wal-g wal-push %p`).
> - First full base backup в R2 bucket proficrm-walg-staging.
>
> **НЕ запускай backup-push снова!** Он уже там.
>
> Проверь:
> ```bash
> ssh -i ~/.ssh/id_proficrm_deploy root@5.181.254.172 'docker compose -f /opt/proficrm-staging/docker-compose.staging.yml -p proficrm-staging exec -T db bash -c "set -a; . /etc/wal-g/walg.env; set +a; /usr/local/bin/wal-g backup-list --pretty"'
> ```
>
> Должен быть 1 backup. Записать в rapport duration + size.
>
> Продолжай: monitor 1 час archive push (Шаг 4b) → Шаг 5 restore drill (critical) → Шаги 6-7 как в оригинальном промпте. Security discipline, stop conditions — те же.

### Update triggers (reminder)

- Перед каждой передачей Дмитрию.
- Каждые 30-60 минут активной работы.
- После получения рапорта исполнителя.
- После принятия решения.
- Перед длительной операцией.
- При приближении к компактификации контекста.
