# Runbook: GlitchTip troubleshooting

_Wave 0.4. Расширяется по мере обнаружения новых сбоев._

---

## Login returns HTTP 500

**Симптом.** GET `/login` → 200 (UI рендерится), POST login (API или UI form) → 500 Server Error. `/_health/` при этом 200 OK.

**Причина (2026-04-20 инцидент)**: Redis недоступен для glitchtip-web, timeout при `django-allauth` rate-limit check.

### Диагностика

1. **Свежие логи**:
   ```bash
   ssh root@5.181.254.172 'cd /opt/proficrm-observability && \
     docker compose -f docker-compose.observability.yml -p proficrm-observability \
     --env-file /etc/proficrm/env.d/glitchtip.conf logs glitchtip-web --since=2m | tail -50'
   ```

2. **Искать в трейсе** `ConnectionError` к Redis или `django_vcache`:
   ```
   File ".../django_vcache/backend.py", line ..., in _get_or_create_driver
     driver = RustValkeyDriver.connect(...)
   ConnectionError: Connection failed: timed out
   ```

3. **Проверить Redis**:
   ```bash
   docker exec proficrm-observability-glitchtip-web-1 \
     python -c 'import socket; s=socket.socket(); s.settimeout(3); s.connect(("glitchtip-redis", 6379))'
   # Должно быть без ошибки (OK).
   ```

4. **Если Redis контейнер не запущен**:
   ```bash
   docker ps --filter name=proficrm-observability-glitchtip-redis
   # Пусто → пересоздать стек:
   cd /opt/proficrm-observability && \
     docker compose -f docker-compose.observability.yml -p proficrm-observability \
     --env-file /etc/proficrm/env.d/glitchtip.conf up -d
   ```

### Фикс

```bash
# 1. Убедиться что в docker-compose.observability.yml есть glitchtip-redis сервис.
# 2. REDIS_URL = redis://glitchtip-redis:6379/0
#    CELERY_BROKER_URL = redis://glitchtip-redis:6379/1
#    (НЕ host.docker.internal — ломается)
# 3. Recreate стек.
```

### Verification (обязательна после любого restart)

См. **Smoke tests** ниже — без их зелёного прогона, W0.4 deploy НЕ завершён.

---

## Containers unhealthy after config change

**Симптом.** После `docker compose up -d` (без `--force-recreate`) старые контейнеры остаются со старыми healthcheck-параметрами — `Up N minutes (unhealthy)` хотя приложение работает.

**Причина.** Healthcheck определяется при **создании** контейнера. `restart` / `up -d` не переопределяют его если параметры в compose не менялись существенно.

**Фикс**:
```bash
cd /opt/proficrm-observability && \
  docker compose -f docker-compose.observability.yml -p proficrm-observability \
  --env-file /etc/proficrm/env.d/glitchtip.conf up -d --force-recreate
```

---

## Smoke tests (обязательны после любого restart/recreate)

После любых изменений стека GlitchTip — **прогнать оба smoke-теста**. Если хотя бы один красный — deploy считается **НЕ завершённым**.

### Test 1 — API login (быстрый, 2 секунды)

```bash
ssh root@5.181.254.172 '
PWD=$(grep GLITCHTIP_ADMIN_PASSWORD /etc/proficrm/env.d/glitchtip.conf | cut -d= -f2)

# 1. Получить CSRF через /_allauth/browser/v1/config
curl -sk https://glitchtip.groupprofi.ru/_allauth/browser/v1/config -c /tmp/c.txt -o /dev/null

# 2. Попытка login
CSRF=$(grep csrftoken /tmp/c.txt | awk "{print \$7}")
curl -sk -X POST https://glitchtip.groupprofi.ru/_allauth/browser/v1/auth/login \
    -H "Content-Type: application/json" \
    -H "Referer: https://glitchtip.groupprofi.ru/" \
    -H "X-CSRFToken: $CSRF" \
    -b /tmp/c.txt -c /tmp/c.txt \
    -d "{\"email\":\"admin@groupprofi.ru\",\"password\":\"$PWD\"}" \
    -w "\nHTTP %{http_code}\n"
'
```

**Ожидается**: HTTP 200 + JSON `{"status": 200, "data": {"user": {...}, "meta": {"is_authenticated": true}}}`.

Если 500 → смотреть логи (см. выше).
Если 403 → забыл Referer или CSRF.
Если 401 → неправильный пароль.

### Test 2 — UI login через Playwright (2 минуты)

```bash
# На dev-машине (Windows / Linux):
cat > /tmp/glitchtip_login_smoke.js <<'EOF'
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const resp = await page.goto('https://glitchtip.groupprofi.ru/login');
  if (!resp.ok()) { console.error('PAGE LOAD FAIL', resp.status()); process.exit(1); }
  await page.fill('input[type="email"]', 'admin@groupprofi.ru');
  await page.fill('input[type="password"]', process.env.GLITCHTIP_ADMIN_PASSWORD);
  await Promise.all([
    page.waitForURL(url => !url.pathname.startsWith('/login'), { timeout: 15000 }),
    page.click('button[type="submit"]')
  ]);
  console.log('LOGIN OK, landed on:', page.url());
  await browser.close();
})();
EOF

# Пароль из сервера:
export GLITCHTIP_ADMIN_PASSWORD=$(ssh root@5.181.254.172 \
    'grep GLITCHTIP_ADMIN_PASSWORD /etc/proficrm/env.d/glitchtip.conf | cut -d= -f2')

node /tmp/glitchtip_login_smoke.js
```

**Ожидается**: `LOGIN OK, landed on: https://glitchtip.groupprofi.ru/`.

Если timeout/ошибка — сохрани `page.content()` и скриншот:
```javascript
// Добавить в catch:
await page.screenshot({ path: '/tmp/login_fail.png', fullPage: true });
console.error('HTML:', await page.content());
```

---

## Out of memory / swap пик

См. `docs/runbooks/glitchtip-setup.md` §«Memory budget — что делать если swap растёт».

Короткий чеклист:
1. `free -m`, `docker stats` — найти кто именно ест память.
2. Если web/worker > 95% limit — OOM-kill возможен. Посмотреть `dmesg | grep -i oom`.
3. Эскалация в `docs/open-questions.md` Q2.

---

## Certbot renewal failed

```bash
# Ручной renew:
sudo certbot renew --nginx -d glitchtip.groupprofi.ru --force-renewal --dry-run
# Если dry-run OK — запустить без --force-renewal.
```

---

## GlitchTip не принимает events от Django SDK

1. Проверить `SENTRY_DSN` в `/opt/proficrm-staging/.env`.
2. Проверить что DSN host = `glitchtip.groupprofi.ru` (не `sentry.io`).
3. Triger test error: `curl https://crm-staging.groupprofi.ru/_debug/sentry-error/`.
4. Посмотреть в UI GlitchTip через 30 сек — должна появиться issue.
5. Если не появилась — проверить `docker logs crm_staging_web --since=1m | grep -iE 'sentry|glitchtip'`.
