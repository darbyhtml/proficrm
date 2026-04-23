# PM current context

_Живое состояние текущей PM-сессии. PM обновляет этот файл перед предсказуемым compact или каждые 30-60 минут активной работы. После compact — читается ПЕРВЫМ для восстановления контекста._

**Last updated:** 2026-04-24 12:55 UTC (PM).

---

## 🎯 Current session goal

W10.2-early Шаги 2-3a ✅ завершены бесшумно. Исполнитель на 🟡 PAUSE перед Шагом 3b — ждёт одобрения Дмитрия на 2 рестарта Postgres стейджинга (~1-2 мин суммарного простоя). После OK — идут Шаги 3b-7 автоматически.

## 📋 Active constraints

- Path E: **ACTIVE**.
- R2 bucket + creds: ✅ готовы.
- WAL-G v3.0.8 установлен в `/usr/local/bin/wal-g` (v3.0.3 asset отсутствует на GitHub, взят новее — совместимый). Connectivity к R2 подтверждён `wal-g st ls` (пустой бакет).
- `/etc/wal-g/walg.env` создан, пермишены 600, секреты не утекли.
- Коммит `9b3e956a` (`feat(backup): WAL-G mounts для db container`) в `claude/recursing-elgamal-c31a17` — добавлены 2 read-only монта в сервис `db`:
  - `/usr/local/bin/wal-g:/usr/local/bin/wal-g:ro`
  - `/etc/wal-g:/etc/wal-g:ro`
- Защитный слой pg_dump работает.

## 🔄 Last decision made

**Timestamp:** 2026-04-24 12:55 UTC.
**Decision pending:** Дмитрий одобряет 2 рестарта Postgres стейджинга (breaking action, ~1-2 мин downtime + Kuma Telegram alert ожидаемый, не инцидент).
**Reasoning:** `archive_mode=on` нельзя активировать без рестарта Postgres. Compose config change тоже требует `up -d` (пересоздание контейнера).
**Owner:** Дмитрий (ok/подожди).

## ⏭️ Next expected action

1. ✅ Обновить `docs/pm/current-context.md`.
2. ✅ Коммит.
3. ⏭️ Короткий брифинг Дмитрию + запрос «ok/подожди».
4. ⏭️ Передать исполнителю ответ Дмитрия.
5. ⏭️ Исполнитель отработает Шаги 3b-7 бесшумно (~3-4 часа, из которых ~2 часа restore drill).
6. ⏭️ Финальный рапорт → review + classification + closure.

## ❓ Pending questions to Дмитрий

- [ ] **OK на 2 рестарта Postgres стейджинга?**
  - Downtime: ≈1-2 минуты суммарно.
  - Telegram `[CRM Staging] [🔴 Down]` → через ~1-2 мин `[✅ Up]` — ожидаемое, не инцидент.
  - Rollback если что-то пойдёт не так: `git revert 9b3e956a && docker compose up -d db`.
  - Подходящее ли время (рабочий день в РФ)?

## 📊 Last Executor rapport summary

**Session:** W10.2-early Шаги 2-3a.
**Received:** 2026-04-24 12:50 UTC.
**Status:** 🟡 PAUSE — ожидает greenlight перед breaking Шагом 3b.
**Classification:** win — под бюджет (~15 мин на Шаги 2-3a), чистая security discipline, адекватный fallback на отсутствующий v3.0.3 asset.

### Ключевые факты

- WAL-G v3.0.8 установлен на хосте и доступен в контейнере через bind mount.
- `wal-g st ls` → empty bucket listing (R2 доступен, auth работает).
- `docker-compose.staging.yml` изменён (+4 строки), закоммичен `9b3e956a`, запушен.

### Шаг 3b — план после OK

1. `git pull` на стейджинг-хосте (берёт `9b3e956a`).
2. Рестарт №1: `docker compose up -d db` (пересоздание контейнера с новыми mounts, ~30 с).
3. `ALTER SYSTEM SET archive_mode / archive_command / archive_timeout / wal_level`.
4. Рестарт №2: `docker compose restart db` (для применения archive_mode=on, ~30 с).
5. Через 90 с — проверка `pg_stat_archiver.archived_count > 0`.
6. Если зелёно → Шаги 4-7 бесшумно.

## 🚨 Red flags (if any)

Нет. Rapport чист, план Шага 3b детален, rollback-path документирован.

## 📝 Running notes

### Отклонение от промпта: WAL-G v3.0.8 вместо v3.0.3

v3.0.3 asset не найден на GitHub (возможно удалён/переименован). Исполнитель взял v3.0.8 (latest, 2026-01-21). Это минорное отклонение, совместимость проверена на обоих средах (хост + контейнер). Приемлемо.

### После Шага 7 closure — что делаю

1. Review финального рапорта с фокусом на restore drill (Lesson 7: «CI green ≠ feature works» — здесь restore drill = primary acceptance criterion).
2. Classify: win / partial / blocked.
3. Update хотлиста: закрыть пункт W10.2-early pending.
4. Update ADR `2026-04-24-wal-g-r2-bridge-to-minio.md` §Consequences → «WAL-G PITR active on staging since 2026-04-24».
5. Написать **Lesson 9, 10, 11** в `docs/pm/lessons-learned.md`:
   - L9: explicit safe channel для секретов (incident с токеном в чате).
   - L10: cloud service activation ≠ credentials (R2 error 10042).
   - L11: Cloudflare API не даёт permanent S3-tokens через /user/tokens (error 9109) — планировать dashboard step.
6. Предложить Дмитрию revoke `CF_API_TOKEN` (его роль выполнена; WAL-G использует R2 S3 creds с узким scope).

### Update triggers (reminder)

- Перед каждой передачей Дмитрию.
- Каждые 30-60 минут активной работы.
- После получения рапорта исполнителя.
- После принятия решения.
- Перед длительной операцией.
- При приближении к компактификации контекста.
