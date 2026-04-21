# Staging operations runbook

_Когда нужно руками трогать staging — используй эти Makefile targets вместо прямых docker команд. Они инкапсулируют два урока SEV2 2026-04-21: nginx DNS cache flush + post-action smoke verification._

---

## Quick reference

| Ситуация | Команда | Что делает |
|----------|---------|------------|
| Web/celery подвис, хочешь пере-запустить | `make restart-staging-web` | `up -d --force-recreate` + nginx restart + smoke |
| Полный сбой, переинициализация всего | `make restart-staging-all` | `down` + `up -d` + nginx restart + smoke |
| Подозрение на cache / проблему | `make smoke-staging` | Только внешние 6 probe'ов, без рестартов |
| После любого деплоя (ручного или CI) | `make smoke-staging` | Обязательно, по правилу CLAUDE.md |

Все targets:
- Используют SSH через `root@5.181.254.172` (ключ `~/.ssh/id_proficrm_deploy`).
- Автоматически рестартят `crm_staging_nginx` после force-recreate (иначе DNS cache → 502).
- Заканчиваются smoke (exit код команды = smoke exit код).
- Не трогают prod.

---

## `make restart-staging-web`

Типовой use-case: «web или celery залипли, нужно мягко перезапустить без потери db/redis state».

Что делает:
```bash
# 1. force-recreate web + sidecars (сохраняет db/redis контейнеры)
docker compose -f docker-compose.staging.yml -p proficrm-staging \
  up -d --force-recreate web celery celery-beat websocket

# 2. Flush host-level nginx DNS cache (новый Docker IP у web)
docker restart crm_staging_nginx

# 3. Wait 30s + external smoke
sleep 30
bash tests/smoke/staging_post_deploy.sh
```

Длительность: ~1-2 минуты (build кэшируется, если код не менялся — force-recreate моментальный).

**Exit 0** — всё зелёное.
**Exit 1** — smoke red, нужна диагностика:
- `docker ps | grep staging` — все Up?
- `docker logs crm_staging_web --tail=50`
- `docker logs crm_staging_celery --tail=50`
- `docker exec crm_staging_nginx cat /var/log/nginx/error.log | tail -30`

---

## `make restart-staging-all`

Типовой use-case: «staging в бесконечной каше, проще снести всё и поднять заново». Осторожно: это **не теряет данные** (postgres на bind-mount), но перерывает все long-lived connections (SSE, WebSocket, operator-panel active sessions).

Что делает:
```bash
# 1. down ВСЕХ staging контейнеров (вкл. db, redis)
docker compose -f docker-compose.staging.yml -p proficrm-staging down

# 2. up ВСЕХ contаiner'ов с нуля
docker compose -f docker-compose.staging.yml -p proficrm-staging up -d

# 3. nginx restart (тот же reason)
docker restart crm_staging_nginx

# 4. Wait 45s (больше grace для db init) + smoke
sleep 45
bash tests/smoke/staging_post_deploy.sh
```

Длительность: ~2-3 минуты. Postgres должен успеть инициализироваться (обычно 5-10s) плюс Django миграции применятся при старте (если есть un-applied).

---

## Почему nginx restart — часть каждого target?

**Incident reference**: SEV2 2026-04-21 (`docs/audit/incidents/2026-04-21-staging-502.md`) Layer 2.

`docker compose up -d --force-recreate web` даёт web контейнеру **новый Docker IP**. Host-level `staging-nginx` (на `5.181.254.172`, вне compose сети) кэширует DNS резолв для `proxy_pass http://127.0.0.1:8030` через свой воркер. После recreate — upstream IP битый → 502 Bad Gateway для всех внешних пользователей, даже если `docker ps` показывает все Up healthy.

`docker restart crm_staging_nginx` заставляет nginx пере-резолвить upstream. Альтернатива — `nginx -s reload` внутри контейнера, но restart проще и надёжнее.

**Правило**: любой force-recreate web на staging обязан сопровождаться `docker restart crm_staging_nginx`. Makefile targets инкапсулируют это — не придётся помнить вручную.

---

## Когда НЕ использовать Makefile targets

| Ситуация | Альтернатива |
|----------|--------------|
| Меняешь только env/config файл на сервере | `docker compose up -d` (обычный, без --force-recreate) + smoke |
| Debug session, нужно посмотреть поведение контейнера поочерёдно | Прямой `docker exec` / `docker logs` |
| Pre-deploy dry-run (посмотреть что CI сделает) | Прочесть `.github/workflows/deploy-staging.yml` |
| Прод-деплой | `docs/runbooks/prod-deploy.md` (НЕ этот файл) |

---

## Troubleshooting smoke failures

Если `make restart-staging-web` или `restart-staging-all` заканчивается красным smoke:

### `Liveness /live/ FAIL` / `Readiness /ready/ FAIL` / `Health /health/ FAIL`

Причина 1: web контейнер не стартует (migration error, Django import error).
```bash
ssh root@5.181.254.172 'docker logs crm_staging_web --tail=100'
```

Причина 2: nginx не перезапустился или упал.
```bash
ssh root@5.181.254.172 'docker ps | grep nginx'
ssh root@5.181.254.172 'docker logs crm_staging_nginx --tail=50'
```

Причина 3: IP whitelist заблокировал probe (не должно быть — /live/ /ready/ /health/ в unrestricted location).
```bash
ssh root@5.181.254.172 'cat /etc/nginx/sites-enabled/crm-staging | grep -A 5 "/live"'
```

### `Home FAIL` (expected 200/302/403, got что-то другое)

Причина: Django error page (500 из-за missing env var / DB migration drift / template error).
```bash
ssh root@5.181.254.172 'docker logs crm_staging_web --tail=100'
curl -vk https://crm-staging.groupprofi.ru/ 2>&1 | head -40
```

### `Feature flags API FAIL` (expected 401/403)

Причина: authentication middleware сломана или DRF не стартанул.
```bash
ssh root@5.181.254.172 'docker exec crm_staging_web python manage.py check'
```

---

## Связанные документы

- `docs/audit/incidents/2026-04-21-staging-502.md` — incident, давший начало этому runbook
- `docs/audit/process-lessons.md` #2 — mandatory smoke правило
- `CLAUDE.md` §«MANDATORY» — end-of-session check
- `tests/smoke/staging_post_deploy.sh` — сам smoke скрипт
- `.github/workflows/deploy-staging.yml` — CI auto-deploy (те же защиты встроены в workflow)
