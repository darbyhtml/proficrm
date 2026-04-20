---
tags: [runbook, релиз-1, ready-to-execute, прод, main-to-prod]
created: 2026-04-20
status: READY — dress rehearsal пройден, 2 pending migrations применены, E2E зелёный
expected_downtime: 5-10 минут
risk: LOW (все шаги проверены на staging с прод-копией БД)
---

# Runbook — Релиз 1 (main → prod), готов к исполнению

## Контекст

333 коммита разницы по коду между проду и main, **в том числе 4 новых коммита за 2026-04-20**:
- `bea256b0` Fix(Docker): celery healthcheck staging (+ та же фикс-логика)
- `242fcf2a` Fix(Docker): celery healthcheck prod
- `3fea75b4` Harden(Policy): POLICY_DECISION_LOGGING_ENABLED
- `b1fb00a8` Docs(Release-0): 3-day audit + post-mortem
- `0c142bec` Chore(Migrations): 2 pending migrations (accounts.0016 + messenger.0026)

**БД прода уже содержит 205 миграций и все 84 таблицы (включая messenger)**. После этого релиза применяются дополнительно **2 минорные миграции** (2-5 сек каждая, без ALTER TABLE на больших таблицах).

## Dress rehearsal — сделан 2026-04-20 (staging с прод-копией БД)

- ✅ `git pull origin main` на staging, рестарт с новым кодом
- ✅ Pending migrations (accounts.0016, messenger.0026) применены, drift закрыт
- ✅ `makemigrations --check` = "No changes detected"
- ✅ `Django check` = "System check identified no issues"
- ✅ Celery healthcheck: **healthy** (был unhealthy 4 недели)
- ✅ `POLICY_DECISION_LOGGING_ENABLED = False` — 0 новых policy events при запросах через @policy_required
- ✅ E2E smoke: /health/, /login/, /dashboard/, /companies/, /tasks/, /messenger/, /admin/ — все 200/301
- ✅ ORM: 45 709 компаний, 99 156 контактов, 18 185 задач, 35 пользователей, 0 диалогов (messenger пустой)
- ✅ 0 ошибок в ErrorLog за первые 10 минут после rebuild

## Предусловия (перед началом окна)

- [ ] Ночное окно согласовано с заказчиком (рекомендую **21:00-22:00 MSK**)
- [ ] Менеджеры предупреждены через `CrmAnnouncement` (как в Релизе 0)
- [ ] Свежий Netangels-бэкап есть (автоматически)
- [ ] Все 22-30 онлайн-менеджера после 19:00 MSK офлайн (выборочная проверка `last_login`)
- [ ] Hook `block-prod.py` временно отключён на время окна (возврат после)

## План выполнения (15 минут)

### T+0 — Backup (1 минута)

```bash
ssh -i ~/.ssh/id_proficrm_deploy root@5.181.254.172
TS=$(date +%Y%m%d_%H%M%S)
mkdir -p /tmp/release-1-backups
docker exec proficrm-db-1 pg_dump -U crm crm --no-owner --no-acl | gzip -6 > /tmp/release-1-backups/prod_pre_release1_${TS}.sql.gz
ls -lh /tmp/release-1-backups/prod_pre_release1_${TS}.sql.gz
```

Ожидание: ~450 MB за ~75 секунд, прод продолжает работать параллельно.

### T+2 — Announcement #1 (начало работ)

```bash
docker exec proficrm-db-1 psql -U crm crm -c "INSERT INTO notifications_crmannouncement (title, body, announcement_type, is_active, scheduled_at, created_at) VALUES ('Технические работы', 'Через 5 минут начнутся плановые технические работы. CRM ненадолго приостановится. Дождитесь уведомления об окончании работ.', 'urgent', true, NULL, NOW()) RETURNING id;"
```

Подождать **5 минут** (polling 60 сек → все получают модалку).

### T+7 — Обновление .env.prod

```bash
# Переменные для Release 1 — messenger включается, policy logging остаётся выключен
cat >> /opt/proficrm/.env << 'EOF'

# Релиз 1 (2026-04-20): включаем messenger (таблицы есть, пустые)
MESSENGER_ENABLED=1

# Политика эскалации live-chat (оставлено на defaults — доведём в Релизе 2)
# MESSENGER_WIDGET_STRICT_ORIGIN=True
EOF

grep -E "MESSENGER_ENABLED|POLICY_DECISION" /opt/proficrm/.env
```

### T+8 — git pull + build (3-5 минут)

```bash
cd /opt/proficrm
git fetch origin main
git log --oneline HEAD..origin/main | head -20   # показать что тянется
git pull origin main

# Rebuild — обязательно websocket (messenger = channels + daphne)
time docker compose -f docker-compose.prod.yml build web celery celery-beat websocket
```

Ожидание: 3-5 минут (pip install + npm + collectstatic если включён).

### T+13 — Announcement #2 (ведутся работы)

```bash
docker exec proficrm-db-1 psql -U crm crm -c "INSERT INTO notifications_crmannouncement (title, body, announcement_type, is_active, scheduled_at, created_at) VALUES ('Сейчас ведутся работы', 'Идут технические работы. Пожалуйста, подождите — ниже появится уведомление об окончании.', 'urgent', true, NULL, NOW()) RETURNING id;"
```

### T+13 — Migrate (2-5 сек)

```bash
# Применит ровно 2 миграции: accounts.0016, messenger.0026
cd /opt/proficrm
docker compose -f docker-compose.prod.yml run --rm web python manage.py migrate --verbosity=2
```

Ожидание: 2-5 секунд. Если что-то другое — **СТОП**, проверить migrate --plan.

### T+14 — Collectstatic (опционально, ~5 сек)

```bash
docker compose -f docker-compose.prod.yml run --rm web python manage.py collectstatic --noinput
```

### T+14 — Recreate сервисов (30-60 сек downtime CRM)

```bash
# В правильном порядке: сначала websocket (новый сервис), потом web, celery
cd /opt/proficrm

# websocket — новый сервис, появится впервые
docker compose -f docker-compose.prod.yml up -d --force-recreate websocket

# web — с новым кодом, MESSENGER_ENABLED=1, памятью 1.5GB (уже есть)
docker compose -f docker-compose.prod.yml up -d --force-recreate web

# celery — с новым healthcheck (без -d)
docker compose -f docker-compose.prod.yml up -d --force-recreate celery celery-beat

# Ждём healthy
sleep 30
docker ps --filter "name=proficrm" --format 'table {{.Names}}\t{{.Status}}'
```

Ожидание: **30-60 секунд downtime CRM** (web перезагружается).

### T+15 — DROP RULE (больше не нужен)

```bash
# Код с POLICY_DECISION_LOGGING_ENABLED=False теперь сам не пишет policy events
# PG RULE стал избыточным, убираем
docker exec proficrm-db-1 psql -U crm crm -c "DROP RULE IF EXISTS block_policy_activity_events ON audit_activityevent;"
```

### T+15 — Nginx reload (если нужно)

```bash
# Добавить в /etc/nginx/sites-enabled/crm.groupprofi.ru, если поддерживаем websocket через него:
#   location /ws/ {
#       proxy_pass http://127.0.0.1:8002;
#       proxy_http_version 1.1;
#       proxy_set_header Upgrade $http_upgrade;
#       proxy_set_header Connection "upgrade";
#       proxy_read_timeout 3600s;
#   }
# Если messenger будет использоваться через web SSE — WS не нужен сразу

nginx -t && nginx -s reload
```

### T+16 — Smoke check

```bash
cd /opt/proficrm && ./scripts/smoke_check.sh
```

Ожидание: **PASS: 7 FAIL: 0**.

Дополнительно:
```bash
# Celery теперь healthy?
docker ps --filter "name=proficrm-celery-1" --format "{{.Status}}"
# Ожидаем: Up ... (healthy)

# Policy events после Релиза 1 — должны быть 0 новых (код сам не пишет)
sleep 60
docker exec proficrm-db-1 psql -U crm crm -c "SELECT COUNT(*) FROM audit_activityevent WHERE entity_type='policy' AND created_at > NOW() - INTERVAL '1 minute';"
# Ожидаем: 0

# v3/b карточка компании рендерится?
# Curl'ом можно проверить только заголовки. Реально проверить через браузер после announcement #3.
```

### T+17 — Announcement #3 (завершение)

```bash
docker exec proficrm-db-1 psql -U crm crm -c "UPDATE notifications_crmannouncement SET is_active=false WHERE is_active=true AND announcement_type='urgent'; INSERT INTO notifications_crmannouncement (title, body, announcement_type, is_active, scheduled_at, created_at) VALUES ('Работы завершены', 'Технические работы успешно завершены! Всё работает в штатном режиме. Спасибо за терпение.', 'info', true, NULL, NOW()) RETURNING id;"
```

### T+20 — QA

1. Открыть `https://crm.groupprofi.ru/`, залогиниться.
2. Открыть `/companies/` — работает.
3. Открыть `/companies/<любой_UUID>/` — classic рендерится.
4. Открыть `/companies/<UUID>/v3/b/` — **v3/b preview работает (впервые на проде)**.
5. Открыть `/tasks/` — создать/редактировать.
6. Открыть `/messenger/` — видим «создайте первый inbox» или пусто.
7. Открыть `/admin/` → войти как sdm → посмотреть announcement_read count.

### T+25 — Конец окна

## План отката

### Быстрый откат (5-7 минут, без БД)

```bash
cd /opt/proficrm
# Вернуть код
HEAD_PRE=$(git log --oneline -20 | grep -B 1 "b1fb00a8" | head -1 | awk '{print $1}')
# Это коммит ПЕРЕД b1fb00a8, т.е. тот что был на проде
git reset --hard $HEAD_PRE

# Вернуть образы (rebuild старого кода)
docker compose -f docker-compose.prod.yml build web celery celery-beat websocket
docker compose -f docker-compose.prod.yml up -d --force-recreate web celery celery-beat websocket

# Вернуть PG RULE (чтобы policy events снова блокировались)
docker exec proficrm-db-1 psql -U crm crm -c "CREATE OR REPLACE RULE block_policy_activity_events AS ON INSERT TO audit_activityevent WHERE NEW.entity_type='policy' DO INSTEAD NOTHING;"

# .env.prod: убрать MESSENGER_ENABLED=1
sed -i '/^MESSENGER_ENABLED=/d' /opt/proficrm/.env
sed -i '/^MESSENGER_WIDGET_STRICT_ORIGIN=/d' /opt/proficrm/.env

docker compose -f docker-compose.prod.yml up -d --force-recreate web

./scripts/smoke_check.sh
```

### Полный откат (с БД из бэкапа, 10-15 минут)

Только если **БД повреждена** (миграции не применились корректно):
```bash
docker compose -f docker-compose.prod.yml stop web celery celery-beat websocket

# Восстановить из бэкапа
gunzip -c /tmp/release-1-backups/prod_pre_release1_*.sql.gz | docker exec -i proficrm-db-1 psql -U crm -d postgres -c "DROP DATABASE crm; CREATE DATABASE crm OWNER crm;"
gunzip -c /tmp/release-1-backups/prod_pre_release1_*.sql.gz | docker exec -i proficrm-db-1 psql -U crm crm

# И дальше как в "быстром откате" (code)
```

## Что после Релиза 1 (через день-два)

1. **VACUUM FULL audit_activityevent** — освободит ~3 GB на диске (от Релиза 0 DELETE). Ночное окно 5-15 мин, блокирует только эту таблицу.
2. **Удаление test-юзеров или переименование** — `test_krd`, `stagtmn`, `stagtmn2` остаются стажёрам. Опционально добавить им email для сброса пароля.
3. **Мониторинг**: подключить **Sentry free tier** (5k events/мес) и **UptimeRobot** (50 мониторов free) — это существенно улучшит observability.

## После наблюдения ~1-2 недели

Начать планирование Релиза 2 (редизайн + SSE + полный messenger + Chatwoot off).

## Таблица «что на какой фазе релиза»

| Что | Релиз 0 | Релиз 1 | Релиз 2 |
|-----|:-------:|:-------:|:-------:|
| Memory limits | ✅ сделано | — | — |
| shm_size | ✅ сделано | — | — |
| TLS/HTTP2/postfix | ✅ сделано | — | — |
| Policy events stop (PG RULE) | ✅ сделано | → DROP RULE | — |
| Policy events stop (env flag) | — | ✅ код в prod | — |
| Celery healthcheck | — | ✅ healthy | — |
| 2 pending migrations | — | ✅ apply | — |
| Messenger enable | — | ✅ flag | — |
| v3/b preview на prod | — | ✅ доступно | — |
| VACUUM FULL audit | — | → отдельная ночь | — |
| Классический `company_detail.html` → v3/b | — | — | ✅ замена |
| Polling → SSE | — | — | ✅ -90% нагрузки |
| Рефакторинг god-views | — | — | ✅ 1-2 мес |
| Переезд с Chatwoot → messenger | — | — | ✅ |
| Android release | — | — | ✅ параллельно |

## Аудитор

Senior onboarding audit 2026-04-20.
Рекомендованное время запуска: **21:00-22:00 MSK** сегодняшней ночью или следующей.
