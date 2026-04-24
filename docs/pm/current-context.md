# PM current context

_Живое состояние текущей PM-сессии. PM обновляет этот файл перед предсказуемым compact или каждые 30-60 минут активной работы. После compact — читается ПЕРВЫМ для восстановления контекста._

**Last updated:** 2026-04-24 10:15 UTC (PM).

---

## 🎯 Current session goal

**W10.2-early — 🟢 COMPLETE.** Host-pivot WAL-G PITR deployed end-to-end. Restore drill 100% row match. Runbook + ADR обновлены. Retention cron active. PM закрыл lessons 9-19 + AP-9, AP-10. Ожидаемо 3 follow-up recommendation к Дмитрию.

## 📋 Active constraints

- Path E: **ACTIVE**.
- Staging: HTTP 200, 7/7 containers, archive_command → spool → cron → R2 running.
- pg_dump safety net активен.
- W10.2-early artifacts finalized (commits `0f84c6e7`..`3b7588f1`).

## 🔄 Last decision made

**Timestamp:** 2026-04-24 10:15 UTC.
**Decision:** closure W10.2-early. Lessons 9-19 + AP-9, AP-10 добавлены.
**Reasoning:** финальный rapport исполнителя подтвердил end-to-end success (archiving live, base backup в R2, restore drill 100%, retention cron active, runbook + ADR done). Моя closure работа — lessons + current-context + recommendations.
**Owner:** PM closure done. Next — Дмитрий решения по 3 recommendations.

## ⏭️ Next expected action

1. ✅ Обновить `docs/pm/lessons-learned.md` с Lessons 9-19 + AP-9, AP-10.
2. ✅ Обновить `docs/pm/current-context.md` (этот файл).
3. ✅ Коммит closure artifacts.
4. ⏭️ Финальный брифинг Дмитрию с 3 recommendations:
   - Revoke `CF_API_TOKEN`.
   - Merge `claude/recursing-elgamal-c31a17` → main.
   - Отдельная prod-session для `0.0.0.0:5432` exposure fix.

## ❓ Pending questions to Дмитрий

- [ ] **Revoke `CF_API_TOKEN`** через Cloudflare dashboard — token выполнил свою роль (создание bucket + верификация API). Daily ops использует R2 S3-style creds с узким scope. Рекомендую revoke в ближайшие 24 часа.
- [ ] **Merge feature-ветки `claude/recursing-elgamal-c31a17` → main** когда ready. Все W10.2-early артефакты в этой ветке, staging на ней работает. Merge восстановит R1 policy «main = staging state».
- [ ] **Prod postgres `0.0.0.0:5432` exposure** — CRITICAL hotlist item, требует отдельной prod mini-session с `CONFIRM_PROD=yes` + твоё approval. Оценка 30-60 минут (включая 30-сек prod downtime при restart).

## 📊 W10.2-early итоги

### Архитектура

```
postgres (container) → archive_command: cp %p /wal-spool
                                ↓ host bind mount
                 /var/lib/proficrm-staging/wal-spool/
                                ↓ cron каждую минуту
              walg-push-from-spool.sh → wal-g wal-push → R2
                                ↓
            Cloudflare R2: proficrm-walg-staging
                    ├── wal_005/ (непрерывно)
                    └── basebackups_005/ (еженедельно Sun 02:00 UTC)
```

### Metrics

- **Total сессия (весь день 2026-04-23 → 24 UTC):** ~8-9 часов активной работы PM + исполнитель.
- **Commits:** 38 на feature-branch `claude/recursing-elgamal-c31a17` ahead of main.
- **Tests:** 100% restore drill match (3 table counts + MAX(created_at) to microsecond).
- **Storage:** ~1.01 ГБ base backup + ~1 МБ/час WAL archives expected.
- **RPO:** ≤ 1 минута (archive_timeout=60s).
- **RTO:** ~15-30 секунд для backup-fetch + few seconds WAL replay.

### Key discoveries

1. **Wrapper bug** — `wal-g wal-push ""` silent-loss (Lesson 12, 14).
2. **TLS CA bundle mismatch** — Debian 12 не trust'ит Cloudflare (Lesson 17, 19, AP-9).
3. **Prod postgres public exposure** — `0.0.0.0:5432` CRITICAL security finding.
4. **Cloudflare API design limitation** — permanent S3-tokens только через dashboard (Lesson 11).

### Lessons added (9-19) + AP-9, AP-10

| # | Заголовок |
|---|-----------|
| L9 | Safe channel для секретов |
| L10 | Cloud service activation ≠ credentials |
| L11 | Cloudflare API и permanent R2 S3-tokens |
| L12 | Never trust pg_stat_archiver alone |
| L13 | Container vs host networking test |
| L14 | Wrapper scripts — обязательный `%p` test |
| L15 | PM sync даты через `date` |
| L16 | Port conflict audit в multi-env VPS |
| L17 | TLS CA bundle ≠ networking hang |
| L18 | Heredoc quote escaping multi-layer |
| L19 | Container CA bundle ≠ host CA bundle |
| AP-9 | «HTTPS hang = networking» — ранняя wrong гипотеза |
| AP-10 | «Exit 0 из archive_command = успех» |

## 🚨 Red flags (if any)

- **CRITICAL остаётся:** prod postgres `0.0.0.0:5432` exposure. Hotlist item present. Требует отдельной prod-session.
- Минорно: hotlist.md имеет один дубль «MinIO setup» header (cosmetic, non-blocking) — консолидация в будущем cleanup.

## 📝 Running notes

### Open follow-ups для following sessions

1. **Кроны стейджинга в репо.** Ранее созданный hotlist item. Текущие cron files (`proficrm-walg-spool`, `proficrm-walg-retention`, `proficrm-staging-backup`) только на VPS. Scope `deploy/cron/` directory + runbook для sync. 1-2 часа отдельной сессии.
2. **MinIO proper (W10.1 в master plan).** После W10.5 Prometheus stack. Возможно revisit in-container approach с CA mount (Lesson 19) — проще архитектура.
3. **Prod `0.0.0.0:5432` isolation.** Bind на `127.0.0.1:5432:5432` в prod compose + restart db. 30 мин + 30 сек downtime.
4. **Первый weekly cron monitoring.** Воскресенье (сегодня?) 02:00 UTC — full backup через helper compose. 03:00 UTC — retention delete retain FULL 4. Verify `/var/log/proficrm-walg-backup.log` + `/var/log/proficrm-walg-retention.log`.

### Update triggers (reminder)

- Перед каждой передачей Дмитрию.
- Каждые 30-60 минут активной работы.
- После получения рапорта исполнителя.
- После принятия решения.
- Перед длительной операцией.
- При приближении к компактификации контекста.
