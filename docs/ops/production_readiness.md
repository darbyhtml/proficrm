# Production readiness — проверяемое резюме (crm.groupprofi.ru)

## 1. Изменённые и новые файлы

| Файл | Действие |
|------|----------|
| `nginx/snippets/hsts.conf.template` | Новый |
| `scripts/nginx_hsts_apply.sh` | Новый |
| `nginx/production.conf` | Изменён (HSTS → include snippet) |
| `scripts/smoke_check.sh` | Новый |
| `scripts/restore_postgres_test.sh` | Новый |
| `scripts/health_check.sh` | Новый |
| `config/systemd/proficrm-backup.service` | Новый |
| `config/systemd/proficrm-backup.timer` | Новый |
| `docs/ops/backups.md` | Изменён (systemd, restore_test, порядок) |
| `docs/ops/monitoring.md` | Новый |
| `docs/ops/production_readiness.md` | Новый (этот файл) |
| `DEPLOY_PRODUCTION.txt` | Изменён (HSTS, nginx, бэкапы, мониторинг, проверки, smoke_check) |

---

## 2. Таблица P0 / P1

| ID | Сделано | Не сделано | Почему |
|----|---------|------------|--------|
| **P0.1** HSTS STRICT без ручного редактирования | ✓ | — | Snippet `hsts.conf.template` + `nginx_hsts_apply.sh` (envsubst). SAFE/STRICT переключение. Предупреждение про поддомены при STRICT. preload не используется. |
| **P0.2** Smoke-check одной командой | ✓ | — | `scripts/smoke_check.sh`: HTTPS, HSTS, security headers, web uid 1000, gunicorn, fail-fast, cap_drop ALL (web, celery, beat). PASS/FAIL, exit 0 только если все OK. |
| **P1.1** Тест восстановления бэкапа | ✓ | — | `scripts/restore_postgres_test.sh`. Дока: частота, команда (docs/ops/backups.md). |
| **P1.2** Systemd timers (fallback cron) | ✓ | — | `config/systemd/proficrm-backup.service`, `proficrm-backup.timer`. В backups.md: systemd предпочтительно, cron — fallback. |
| **P1.3** Мониторинг без SaaS (health + Telegram) | ✓ | — | `scripts/health_check.sh`, `docs/ops/monitoring.md`. Токены только из env. |

---

## 3. Команды проверки (копипаст)

```bash
# smoke-check (все 7 проверок; с хоста, из корня проекта, сервисы подняты)
./scripts/smoke_check.sh

# HTTPS и HSTS
curl -sI https://crm.groupprofi.ru | grep -i strict-transport-security

# cap_drop ALL (web, celery, celery-beat)
docker compose -f docker-compose.prod.yml ps -q web   | xargs -I{} docker inspect {} --format '{{.HostConfig.CapDrop}}'
docker compose -f docker-compose.prod.yml ps -q celery | xargs -I{} docker inspect {} --format '{{.HostConfig.CapDrop}}'
docker compose -f docker-compose.prod.yml ps -q celery-beat | xargs -I{} docker inspect {} --format '{{.HostConfig.CapDrop}}'

# fail-fast (пустой DJANGO_SECRET_KEY → exit 1, ERROR)
docker compose -f docker-compose.prod.yml run --rm -e DJANGO_SECRET_KEY= web python -c "pass"
# ожидаем: exit 1, в stderr: ERROR: DJANGO_SECRET_KEY is required and must not be empty. Set it in .env

# backup + restore test (требует db и хотя бы один бэкап)
./scripts/backup_postgres.sh
./scripts/restore_postgres_test.sh

# HSTS apply (SAFE / STRICT)
HSTS_HEADER="max-age=3600" ./scripts/nginx_hsts_apply.sh
# STRICT: HSTS_HEADER="max-age=31536000; includeSubDomains" ./scripts/nginx_hsts_apply.sh

# health_check (при недоступности — exit 1; Telegram при заданных TELEGRAM_*)
./scripts/health_check.sh

# nginx
nginx -t
```

---

## 4. Чеклист в числах

| Категория | Сделано | Всего | Риск, если не внедрять ручные шаги |
|-----------|---------|-------|------------------------------------|
| **P0** | 2 | 2 | HSTS: при STRICT без проверки поддоменов — возможна блокировка браузерами. Smoke: без скрипта — проверки выполняются вручную, выше шанс пропуска. |
| **P1** | 3 | 3 | Restore: бэкапы не проверяются на восстанавливаемость. Systemd: при использовании только cron — зависит от cron. Мониторинг: без health_check и Telegram — падение сервиса обнаруживается с задержкой. |

**Итого: P0 2/2, P1 3/3.**

---

## 5. Ручные шаги на сервере (напоминание)

- Перед первым `nginx -t` / `reload`: создать HSTS snippet: `HSTS_HEADER="max-age=3600" ./scripts/nginx_hsts_apply.sh`
- Systemd: скопировать `config/systemd/proficrm-backup.*` в `/etc/systemd/system/`, поправить `User=` и пути при необходимости, `daemon-reload`, `enable --now` таймера
- Мониторинг: добавить `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` в .env; настроить cron (или timer) для `health_check.sh`
- Restore-test: раз в 1–2 недели запускать `./scripts/restore_postgres_test.sh`
