# Runbook: GlitchTip self-hosted setup

_Wave 0.4 (2026-04-20). Инфраструктура: self-hosted GlitchTip 6.1 на том же VPS что и CRM.
Публичный URL: `https://glitchtip.groupprofi.ru/`. Отдельный docker-compose project
`proficrm-observability` в директории `/opt/proficrm-observability/`._

---

## Что это и зачем

**GlitchTip** — open-source error tracker, совместимый с Sentry SDK по протоколу.
Используем вместо платного Sentry ($26+/мес) self-hosted-версию. Free-tier Sentry
(5k events/mo) для нашего объёма недостаточен.

**Текущее состояние после W0.4**:
- Стек запущен: web + worker + postgres (отдельный от CRM)
- Redis шарится с CRM через host-gateway (DB 10/11, не пересекается с crm)
- TLS через Let's Encrypt (автоматическое продление)
- Ежедневный pg_dump бэкап (retention 30 дней)
- Hard memory limits: web 256 / worker 192 / db 128 MB (Q2 open-questions)

---

## Структура на сервере

```
/opt/proficrm-observability/
├── docker-compose.observability.yml
└── scripts/
    ├── glitchtip-bootstrap.sh      # разовый migrate + superuser
    └── glitchtip-backup.sh          # ежедневный pg_dump

/etc/proficrm/env.d/glitchtip.conf  # секреты (mode 600, НЕ в git)
/etc/cron.d/glitchtip-backup         # расписание бэкапа
/etc/nginx/sites-enabled/glitchtip.groupprofi.ru.conf  # reverse-proxy + TLS
/etc/letsencrypt/live/glitchtip.groupprofi.ru/          # сертификаты
/var/backups/glitchtip/                                 # pg_dump архивы
/var/log/glitchtip-backup.log                           # лог cron-бэкапа
```

---

## После деплоя: один раз настроить через UI

После `docker compose up -d` нужны **три ручных шага через UI** (без них GlitchTip
не получит events от приложения):

### Шаг 1 — войти в admin UI

1. Открой `https://glitchtip.groupprofi.ru/`
2. Email/password — из `/etc/proficrm/env.d/glitchtip.conf`:
   ```bash
   sudo grep -E '^GLITCHTIP_ADMIN_' /etc/proficrm/env.d/glitchtip.conf
   ```

### Шаг 2 — создать organization «GroupProfi»

- В UI → Create Organization
- Name: `GroupProfi`
- Slug: `groupprofi` (автоматически)

### Шаг 3 — создать 2 проекта → получить DSN

Для каждого проекта ниже — Organization `GroupProfi` → Project → Create:

| Project name | Platform | Для чего |
|--------------|----------|----------|
| `crm-backend` | Python (Django) | Ошибки прода `crm.groupprofi.ru` |
| `crm-staging` | Python (Django) | Ошибки staging `crm-staging.groupprofi.ru` |

После создания каждого проекта — страница `Settings > Client Keys (DSN)` даёт
строку вида:
```
https://<PUBLIC_KEY>@glitchtip.groupprofi.ru/<PROJECT_ID>
```

### Шаг 4 — вставить DSN в проект

**Для staging** (Claude Code может):
```bash
ssh root@5.181.254.172
# Добавь строку в /opt/proficrm-staging/.env (не через git — это secrets):
echo "SENTRY_DSN=https://<staging_key>@glitchtip.groupprofi.ru/<project_id>" >> /opt/proficrm-staging/.env
# Без restart контейнер env не перечитает:
docker compose -f /opt/proficrm-staging/docker-compose.staging.yml -p proficrm-staging up -d web celery
```

**Для прода** (только пользователь вручную, CLAUDE.md запрещает):
- Добавить `SENTRY_DSN=...` в `/opt/proficrm/.env`
- `docker compose -f /opt/proficrm/docker-compose.prod.yml -p proficrm up -d web celery`

### Шаг 5 — smoke-test: найти тестовую ошибку в UI

```bash
# На staging (DEBUG может быть включён через env):
curl https://crm-staging.groupprofi.ru/_debug/sentry-error/
# Даст HTTP 500 и Django стек-трейс.
```

Через 5-30 секунд в `https://glitchtip.groupprofi.ru/` → проект `crm-staging` →
появится issue **`RuntimeError: glitchtip-smoke-test (Wave 0.4)`** с тегами:

- `request_id` — 8-символьный UUID, кросс-референс с application logs
- `user_id` — id залогиненного юзера (если не DEBUG-public)
- `role` — его роль (MANAGER/...)
- `branch` — код филиала (ekb/tmn/krd)
- `feature_flags` — CSV активных флагов для этого юзера

Если теги **не видны** — проверить `SentryContextMiddleware` в `MIDDLEWARE`
settings.py (должен идти после `WaffleMiddleware`).

---

## Memory budget — что делать если swap растёт

GlitchTip стек ограничен hard-limits (576 MB суммарно). Мониторинг:

```bash
# За 1 раз:
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}' | grep glitchtip

# Swap использование:
free -m | grep Swap
```

**Зелёная зона**: swap < 1.2 GB, стабильно. Не трогать.

**Жёлтая зона**: swap 1.2-1.5 GB, рост за сутки > 100 MB. Действия:
1. Выключить нижние уровни логирования в GlitchTip (environment variable
   `GLITCHTIP_MAX_EVENT_LIFE_DAYS=14` — уменьшить с 30 до 14).
2. Чистить старые events в UI (Issues → Archive).

**Красная зона**: swap > 1.5 GB, либо OOM-kill web-контейнера. Действия:
1. `docker compose -f /opt/proficrm-observability/docker-compose.observability.yml -p proficrm-observability stop`
2. Документировать в `docs/open-questions.md` Q2 факт OOM и метрики.
3. **Варианты эскалации**:
   - Выключить Chatwoot (Release 2 cleanup) → освободит ~1 GB
   - Переехать на отдельный младший VPS (1-2 GB RAM)
   - Уменьшить GlitchTip до только web+db (убрать worker, потерять alerts)

---

## Первичная установка (один раз, для истории)

### Prerequisites

- DNS A-запись `glitchtip.groupprofi.ru` → IP сервера (проверка:
  `dig +short glitchtip.groupprofi.ru @8.8.8.8`)
- nginx + certbot установлены
- Docker + docker compose v2
- CAA-запись домена покрывает letsencrypt.org

### Шаги (выполнялись в Wave 0.4 deploy, 2026-04-20)

```bash
# 1. Директория
sudo mkdir -p /opt/proficrm-observability/scripts

# 2. Скопировать файлы из репозитория (после git pull в /opt/proficrm-staging/)
sudo cp /opt/proficrm-staging/docker-compose.observability.yml /opt/proficrm-observability/
sudo cp /opt/proficrm-staging/scripts/glitchtip-bootstrap.sh /opt/proficrm-observability/scripts/
sudo cp /opt/proficrm-staging/scripts/glitchtip-backup.sh /opt/proficrm-observability/scripts/
sudo chmod +x /opt/proficrm-observability/scripts/glitchtip-*.sh

# 3. Секреты
sudo mkdir -p /etc/proficrm/env.d
SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')
DB_PWD=$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)
sudo tee /etc/proficrm/env.d/glitchtip.conf > /dev/null <<EOF
GLITCHTIP_SECRET_KEY=$SECRET_KEY
GLITCHTIP_DB_PASSWORD=$DB_PWD
EMAIL_URL=smtp+tls://crm%40groupprofi.ru:<smtp_bz_password>@smtp.bz:587
GLITCHTIP_DEFAULT_FROM_EMAIL=noreply@groupprofi.ru
GLITCHTIP_DOMAIN=https://glitchtip.groupprofi.ru
EOF
sudo chmod 600 /etc/proficrm/env.d/glitchtip.conf

# 4. Запуск
cd /opt/proficrm-observability
sudo docker compose -f docker-compose.observability.yml \
    -p proficrm-observability \
    --env-file /etc/proficrm/env.d/glitchtip.conf \
    up -d

# 5. Nginx + TLS
sudo cp /opt/proficrm-staging/configs/nginx/glitchtip.groupprofi.ru.conf \
    /etc/nginx/sites-available/
sudo ln -sf /etc/nginx/sites-available/glitchtip.groupprofi.ru.conf \
    /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d glitchtip.groupprofi.ru \
    --non-interactive --agree-tos -m admin@groupprofi.ru --redirect

# 6. Bootstrap (migrate + superuser)
SUPERUSER_PWD=$(openssl rand -base64 18 | tr -d '/+=' | head -c 20)
docker compose -f docker-compose.observability.yml \
    -p proficrm-observability \
    --env-file /etc/proficrm/env.d/glitchtip.conf \
    exec -T glitchtip-web ./manage.py migrate --noinput
docker compose -f docker-compose.observability.yml \
    -p proficrm-observability \
    --env-file /etc/proficrm/env.d/glitchtip.conf \
    exec -T \
    -e DJANGO_SUPERUSER_EMAIL=admin@groupprofi.ru \
    -e DJANGO_SUPERUSER_PASSWORD=$SUPERUSER_PWD \
    glitchtip-web ./manage.py createsuperuser --noinput
echo "GLITCHTIP_ADMIN_EMAIL=admin@groupprofi.ru" | sudo tee -a /etc/proficrm/env.d/glitchtip.conf
echo "GLITCHTIP_ADMIN_PASSWORD=$SUPERUSER_PWD" | sudo tee -a /etc/proficrm/env.d/glitchtip.conf

# 7. Backup cron
sudo tee /etc/cron.d/glitchtip-backup > /dev/null <<'EOF'
0 3 * * * root /opt/proficrm-observability/scripts/glitchtip-backup.sh >> /var/log/glitchtip-backup.log 2>&1
EOF
sudo chmod 644 /etc/cron.d/glitchtip-backup
sudo systemctl reload cron
```

После этих шагов — UI `https://glitchtip.groupprofi.ru/` готов, осталось только
создать organization + project + DSN (см. выше).

---

## UptimeRobot monitoring (free tier)

**Не автоматизировано в W0.4 — настраивается вручную один раз через UI.**

### Шаги

1. Регистрация: `https://uptimerobot.com/` (free tier — 50 мониторов, 5-мин интервал)
2. Создать 3 HTTP(s) мониторов:

| URL | Интервал | Ожидаемый ответ |
|-----|----------|-----------------|
| `https://crm.groupprofi.ru/live/` | 5 мин | 200 + body `{"status":"ok"}` |
| `https://crm-staging.groupprofi.ru/live/` | 5 мин | 200 |
| `https://glitchtip.groupprofi.ru/_health/` | 5 мин | 200 |

3. Alert contact:
   - Email: `admin@groupprofi.ru` (дефолт)
   - **Telegram bot**: @UptimeRobotBot → /start → добавить chat_id в UR UI.

4. Alert thresholds:
   - Down after **2 failures** (чтобы не спамить флапами)
   - Send up-notification (важно для понимания что восстановилось)

### Проверка

После включения — `Down` тест: `docker stop crm_staging_web`, ждать 10 минут,
убедиться что алерт пришёл в Telegram. Запустить обратно.

---

## Real-HTTP middleware verification (периодически / после SDK config changes)

**Причина**: shell-level тест (`RequestFactory` + ручной вызов `_enrich_scope()`)
**не эквивалент** real HTTP через Django MIDDLEWARE chain. См.
`docs/audit/process-lessons.md` §«Shell-level middleware test ≠ real HTTP request».

**Когда запускать**:
- После любых изменений `core/sentry_context.py` или `backend/crm/settings.py::sentry_sdk.init(...)`
- После major upgrade Sentry SDK / GlitchTip
- Периодически раз в неделю как canary

### Pre-requisites

- Staging has `STAFF_DEBUG_ENDPOINTS_ENABLED=1` in .env
- Staging has `SENTRY_DSN` set
- `SENTRY_ENVIRONMENT=staging` set
- Staff user creds known (sdm на момент 2026-04-21, cm. `docs/open-questions.md` Q10)

### Level 1 — Django TestClient (integration)

```bash
# Copy script into web container + run via Django shell
ssh root@5.181.254.172 '
docker cp /opt/proficrm-staging/scripts/verify_sentry_real_traffic.py \
    crm_staging_web:/tmp/verify.py
docker exec -e VERIFY_USERNAME=sdm crm_staging_web bash -c \
    "cd /app/backend && python manage.py shell < /tmp/verify.py"
'
```

Expected output:
```
[verify] Found user: id=1 username='sdm' role='admin' branch='ekb' is_staff=True
[verify] HTTP status: 500
[verify] Status 500 OK — Exception прошёл через MIDDLEWARE chain.
[verify] Sentry SDK flushed.
```

Затем через API GlitchTip проверить тэги:
```bash
PASS=$(ssh root@5.181.254.172 'grep GLITCHTIP_ADMIN_PASSWORD /etc/proficrm/env.d/glitchtip.conf | cut -d= -f2')
curl -sk https://glitchtip.groupprofi.ru/_allauth/browser/v1/config -c /tmp/ck -o /dev/null
CSRF=$(grep csrftoken /tmp/ck | awk '{print $7}')
curl -sk -X POST https://glitchtip.groupprofi.ru/_allauth/browser/v1/auth/login \
    -H 'Content-Type: application/json' -H 'Referer: https://glitchtip.groupprofi.ru/' \
    -H "X-CSRFToken: $CSRF" -b /tmp/ck -c /tmp/ck \
    -d "{\"email\":\"admin@groupprofi.ru\",\"password\":\"$PASS\"}" -o /dev/null
# Latest event от issue с 'w04-real-traffic-verify' в title
curl -sk -b /tmp/ck 'https://glitchtip.groupprofi.ru/api/0/projects/groupprofi/crm-staging/issues/?limit=1' \
    | python3 -c 'import json, sys; d=json.load(sys.stdin); print(d[0]["id"], d[0]["title"])'
```

**Expected tags** (всего 8):
```
  branch         = 'ekb'          ← Bug 1 fix (always set)
  environment    = 'staging'       ← Bug 2 fix (not 'production')
  feature_flags  = 'none'          ← W0.3 integration
  request_id     = <8-char UUID>   ← RequestIdMiddleware
  role           = 'admin'         ← user.role
  server_name    = <container-id>  (Sentry auto)
  user.id        = '1'             (scope.user auto)
  user.username  = 'sdm'           (scope.user auto)
```

**Если хоть один custom tag (branch/role/request_id/feature_flags) отсутствует** —
middleware chain сломан. Debug:
1. `docker exec crm_staging_web bash -c 'cd /app/backend && python -c "from django.conf import settings; print(settings.MIDDLEWARE)"'`
2. `SentryContextMiddleware` должен быть ПОСЛЕ `AuthenticationMiddleware`.
3. Если tag `feature_flags=unknown` — проверить waffle (см. `docs/audit/process-lessons.md`).

### Level 2 — Playwright browser flow (E2E, optional)

Пропускаем если IP разработчика не в nginx staging whitelist. Или запускать
из машины менеджера (whitelist IPs: 87.248.*, 185.*, 77.*, 193.*, 23.*, 109.*).

Конфигурация — в `scripts/verify_sentry_real_traffic.py` docstring §«Level 2».

---

## Login smoke tests (ОБЯЗАТЕЛЬНЫ после любого restart/recreate)

Без зелёного прогона обоих тестов ниже — W0.4 deploy считается **НЕ завершённым**.
Это closes the gap, который проявился 2026-04-20 (HTTP 500 на login из-за
Redis timeout при shared `host.docker.internal` подключении).

### Test 1 — API login (быстрый, 2 секунды)

```bash
ssh root@5.181.254.172 '
PWD=$(grep GLITCHTIP_ADMIN_PASSWORD /etc/proficrm/env.d/glitchtip.conf | cut -d= -f2)
curl -sk https://glitchtip.groupprofi.ru/_allauth/browser/v1/config -c /tmp/c.txt -o /dev/null
CSRF=$(grep csrftoken /tmp/c.txt | awk "{print \$7}")
curl -sk -X POST https://glitchtip.groupprofi.ru/_allauth/browser/v1/auth/login \
    -H "Content-Type: application/json" \
    -H "Referer: https://glitchtip.groupprofi.ru/" \
    -H "X-CSRFToken: $CSRF" -b /tmp/c.txt -c /tmp/c.txt \
    -d "{\"email\":\"admin@groupprofi.ru\",\"password\":\"$PWD\"}" \
    -w "\nHTTP %{http_code}\n"
'
```

**Ожидается**: HTTP 200 + `{"meta": {"is_authenticated": true}}`.

### Test 2 — UI login через Playwright

См. `docs/runbooks/glitchtip-troubleshooting.md` §«Smoke tests» Test 2.

### Если хотя бы один тест красный

1. Смотреть логи — `docker compose logs glitchtip-web --since=2m | tail -50`.
2. Искать `ConnectionError`, `TimeoutError`, `OperationalError`.
3. Диагностика — `docs/runbooks/glitchtip-troubleshooting.md`.
4. **НЕ считать deploy завершённым** пока оба теста не зелёные.

---

## Проверка что всё работает

```bash
# 1. TLS сертификат
curl -sI https://glitchtip.groupprofi.ru/ | head -3
# Ожидаем: HTTP/2 200, server: nginx

# 2. Healthcheck
curl -s https://glitchtip.groupprofi.ru/_health/
# Ожидаем: {"ok": true} или похожее

# 3. Контейнеры живы
docker ps --filter name=proficrm-observability --format 'table {{.Names}}\t{{.Status}}'
# Все Up и (healthy/health: starting после перезапуска)

# 4. Memory
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}' | grep glitchtip
# web < 256M, worker < 192M, db < 128M

# 5. Backup actual
ls -la /var/backups/glitchtip/ | tail -5
# Свежие файлы glitchtip_YYYYMMDD_HHMMSS.sql.gz

# 6. Cron queued
ls -la /etc/cron.d/glitchtip-backup
# -rw-r--r-- 1 root root N /etc/cron.d/glitchtip-backup
```

---

## Связанные документы

- `docs/runbooks/glitchtip-restore.md` — восстановление из pg_dump.
- `docs/decisions.md` ADR-003 — обоснование self-hosted вместо paid.
- `docs/architecture/feature-flags.md` — связь с W0.3 (feature_flags в tags).
- `docker-compose.observability.yml` — сам compose.
- `backend/core/sentry_context.py` — middleware (5 тегов).
- `backend/core/celery_signals.py` — request_id для task'ов.
- `backend/crm/health.py` — /live/ /ready/ /_debug/sentry-error/.
- `scripts/glitchtip-backup.sh` — ежедневный pg_dump.
- `configs/nginx/glitchtip.groupprofi.ru.conf` — reverse-proxy.
