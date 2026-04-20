---
tags: [runbook, релиз-0, hotfix, ночь, безопасность]
created: 2026-04-20
status: DRAFT — ждёт подтверждения заказчика в 17:30 MSK
risk: low (только конфиги, 0 миграций БД)
expected_downtime: 5-10 минут
---

# Runbook Релиз 0 — Ночной hotfix безопасности и памяти

## Цель

Закрыть **7 находок** из day-1/day-2 аудита, которые **не требуют миграций БД** и **не зависят от 333-коммитного gap main→prod**. Это параллельный трек: пока главный Релиз 1 ещё 1-2 недели готовится, мы немедленно закрываем критические дыры.

## Что НЕ делает этот релиз

- ❌ Не деплоит main → prod
- ❌ Не применяет 44 миграции
- ❌ Не трогает код приложения (только docker-compose, nginx-host, postfix)
- ❌ Не меняет URL-схемы, endpoint'ы, UI

## Что делает

| # | Изменение | Почему | Downtime |
|---|-----------|--------|---------:|
| 1 | Chatwoot PostgreSQL mapping: `5432:5432` → `127.0.0.1:5432:5432` | Закрыть публичный порт 5432 (P0 из day-1) | 15 сек |
| 2 | Chatwoot PostgreSQL password: `ooqu1bieNg2` → длинный random (32+ символа) | Слабый пароль на публично открытой БД | в рамках #1 |
| 3 | Chatwoot Rails mapping: `3000:3000` → `127.0.0.1:3000:3000` + nginx `chat.groupprofi.ru` уже проксирует | Закрыть публичный порт 3000 | 15 сек |
| 4 | `docker-compose.prod.yml` db service: `shm_size: 512mb` | Фикс 14 ошибок/неделя `could not resize shared memory segment` | 15 сек |
| 5 | `docker-compose.prod.yml` web limit: 768M → 1536M | Защита от OOM на пике нагрузки (P0 из day-1) | 15 сек (rolling) |
| 6 | celery limit 384M → 512M, beat 128M → 256M | Близко к OOM (71% beat, 63% celery) | 30 сек |
| 7 | Celery healthcheck: починить или выключить (сломан 4 недели) | Невидимые падения. 40 209 подряд неуспешных проверок. | 0 |
| 8 | nginx: убрать `TLSv1 TLSv1.1` из `ssl_protocols` | Deprecated протоколы | 0 (reload) |
| 9 | nginx: `server_tokens off` + `listen 443 ssl http2;` | Скрыть версию + 15-30% быстрее | 0 (reload) |
| 10 | postfix: `inet_interfaces = loopback-only` | Закрыть публичный 25 порт (не используется) | 0 (restart postfix) |

Итого: **~5-10 минут** downtime CRM + **несколько рестартов Chatwoot** (~минута).

---

## Предусловия (проверить перед началом)

- [ ] Свежий бэкап Netangels есть (в админке Netangels → Параметры восстановления диска)
- [ ] Заказчик в слаке/онлайне — может ответить, если что-то пойдёт не так
- [ ] SSH открыт: `ssh -i ~/.ssh/id_proficrm_deploy sdm@5.181.254.172`
- [ ] Менеджеры предупреждены: «17:30-18:00 CRM будет временно недоступен»
- [ ] Смоук-скрипт готов: `scripts/smoke_check.sh`
- [ ] **ВАЖНО**: хуки Claude Code блокируют команды с `/opt/proficrm/`. Все изменения **пользователь выполняет сам** (Senior дал ему чеклист команд).

---

## План изменений

### Шаг 1. Бэкап (Т-0, 0 мин)

```bash
# На хосте (не в контейнере)
# Проверить, что Netangels бэкап сегодняшний действительно есть
# Дополнительно — локальный dump
TS=$(date +%Y%m%d_%H%M%S)
mkdir -p /tmp/release-0-backups
docker exec proficrm-db-1 pg_dump -U crm crm --no-owner --no-acl | gzip -6 > /tmp/release-0-backups/prod_pre_release0_${TS}.sql.gz
ls -lh /tmp/release-0-backups/
```

Должен получиться файл ~450 MB (как сегодняшний snapshot).

### Шаг 2. Подготовка nginx-конфигов (Т+1 мин, 0 downtime)

Создаём **ДО изменений** обновлённые конфиги. Проверяем `nginx -t`, не применяем.

#### 2.1. `/etc/nginx/nginx.conf`

Найти строку:
```
ssl_protocols TLSv1 TLSv1.1 TLSv1.2 TLSv1.3;
```
Заменить на:
```
ssl_protocols TLSv1.2 TLSv1.3;
server_tokens off;
```

#### 2.2. `/etc/nginx/sites-enabled/crm.groupprofi.ru`

Найти:
```
  listen 443 ssl; # managed by Certbot
```
Заменить на:
```
  listen 443 ssl http2; # managed by Certbot
```

(*То же для `crm-staging` и `chatwoot`.*)

#### 2.3. Проверка

```bash
nginx -t
```

Ожидаемый вывод:
```
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
nginx: configuration file /etc/nginx/nginx.conf test is successful
```

**Если ошибка — СТОП**, не применять.

### Шаг 3. Docker compose — подготовка (Т+2 мин)

#### 3.1. `/opt/proficrm/docker-compose.prod.yml`

Изменения:

```yaml
services:
  db:
    shm_size: 512mb        # ← НОВОЕ
    # ... всё остальное без изменений

  web:
    deploy:
      resources:
        limits:
          memory: 1536M    # ← БЫЛО 768M
        reservations:
          memory: 768M     # ← БЫЛО 384M

  celery:
    deploy:
      resources:
        limits:
          memory: 512M     # ← БЫЛО 384M

  celery-beat:
    deploy:
      resources:
        limits:
          memory: 256M     # ← БЫЛО 128M
    healthcheck:
      # Старое: проверка через celery inspect ping (broken 4 недели)
      # Новое: проверить, что процесс beat живой и heartbeat в логах
      test: ["CMD-SHELL", "test -f /tmp/celerybeat.pid && ps -p $(cat /tmp/celerybeat.pid) > /dev/null"]
      interval: 60s
      timeout: 5s
      retries: 3
```

Альтернатива для Celery healthcheck (если scheduler pidfile не подходит):
```yaml
  celery:
    healthcheck:
      test: ["CMD-SHELL", "celery -A crm inspect ping -d celery@$$HOSTNAME -t 10 || exit 1"]
      # -d <destination> — важно, иначе проверяется ВСЕ workers
      # -t 10 — таймаут 10с вместо дефолтных 1с
      interval: 60s
      timeout: 15s
      retries: 3
      start_period: 30s  # время на старт воркера
```

#### 3.2. Chatwoot (`/opt/chatwoot/docker-compose.yml` или аналог — узнать путь)

```yaml
services:
  postgres:
    ports:
      - "127.0.0.1:5432:5432"    # ← БЫЛО "5432:5432"
    environment:
      POSTGRES_PASSWORD: <NEW_32CHAR_PASSWORD>   # ← сменить

  rails:
    ports:
      - "127.0.0.1:3000:3000"    # ← БЫЛО "3000:3000"
```

**Генерация пароля**:
```bash
openssl rand -base64 24 | tr -d '/+=' | head -c 32
```

**Важно**: после смены `POSTGRES_PASSWORD` Chatwoot rails + sidekiq тоже должны использовать новый пароль в DATABASE_URL (если он задан явно) — проверить `.env` или `environment:` блок.

#### 3.3. Postfix

`/etc/postfix/main.cf`:
```
inet_interfaces = loopback-only   # ← БЫЛО "all"
```

Применить: `systemctl restart postfix`.

### Шаг 4. Применение (Т+5 мин, начинается downtime)

Порядок важен — **сначала Chatwoot** (чтобы открытые порты закрылись как можно раньше), потом CRM.

```bash
# A. Chatwoot сначала (безопасность)
cd /opt/chatwoot  # или где он
docker compose up -d --force-recreate postgres rails sidekiq

# Сменить пароль внутри postgres (если миграция нужна):
docker exec chatwoot-postgres-1 psql -U chatwoot -c "ALTER USER chatwoot PASSWORD 'NEW_PASS';"
# (либо пересоздать volume, если acceptable — но это потеря данных Chatwoot)

# Проверить, что порты закрыты
ss -tlnp | grep -E ':5432|:3000' | grep -v '127.0.0.1'
# Ожидаемый вывод: пусто

# B. CRM prod
cd /opt/proficrm
docker compose -f docker-compose.prod.yml up -d --no-deps --force-recreate db   # shm_size apply
# Подождать 30 секунд, убедиться DB healthy
docker compose -f docker-compose.prod.yml ps db

docker compose -f docker-compose.prod.yml up -d web celery celery-beat  # apply memory limits + healthcheck

# C. Nginx + postfix (без downtime)
nginx -s reload
systemctl restart postfix
```

### Шаг 5. Smoke-check (Т+10 мин)

```bash
cd /opt/proficrm
./scripts/smoke_check.sh
```

Ожидаемый вывод:
```
PASS: 7  FAIL: 0
Smoke-check: OK
```

**Если хотя бы один FAIL — СТОП, откат** (см. ниже).

Дополнительно:
```bash
# Chatwoot жив
curl -sI https://chat.groupprofi.ru | head -3
# Ожидаем 200 или 301

# Порты закрыты
ss -tlnp | grep -E ':(5432|3000|25)' | grep -v '127.0.0.1'
# Ожидаем: пусто

# TLS-профиль обновился
echo | openssl s_client -servername crm.groupprofi.ru -connect crm.groupprofi.ru:443 2>/dev/null | grep -E 'Protocol|Cipher'
# Ожидаем: TLSv1.2 или TLSv1.3, никакого TLSv1 / TLSv1.1

# Celery healthcheck восстановился
docker ps --filter "name=proficrm" --format 'table {{.Names}}\t{{.Status}}'
# Ожидаем: ни одного (unhealthy)
```

### Шаг 6. QA 5 минут (Т+15 мин)

Заходит менеджер (или вы), проверяет:
- [ ] Открывается `/companies/`
- [ ] Открывается любая карточка компании
- [ ] Создаётся задача
- [ ] Отправляется письмо через рассылку
- [ ] Chatwoot на `chat.groupprofi.ru` работает, менеджер видит свои чаты

**Если всё ок — Релиз 0 закрыт. Суммарный downtime: 5-7 минут.**

---

## План отката

### Если упал `nginx -t` (до применения)

Ничего не применено — откат не нужен. Правите ошибку, пробуете заново.

### Если упал CRM web/celery после `up -d`

```bash
cd /opt/proficrm
# Вернуть старые лимиты:
git checkout HEAD -- docker-compose.prod.yml
docker compose -f docker-compose.prod.yml up -d --no-deps --force-recreate web celery celery-beat
./scripts/smoke_check.sh
```

### Если упала БД прода после `shm_size`

Это крайне маловероятно (shm_size добавляет память, не убирает), но на случай:
```bash
git checkout HEAD -- docker-compose.prod.yml
docker compose -f docker-compose.prod.yml up -d --no-deps --force-recreate db
# После того как БД подняла — проверить контакт
docker exec proficrm-db-1 psql -U crm crm -c "SELECT COUNT(*) FROM companies_company;"
```

### Если упал Chatwoot postgres после смены пароля

Раскатать rails/sidekiq с **новым паролем в DATABASE_URL**:
```bash
cd /opt/chatwoot
# проверить .env — обновлён ли POSTGRES_PASSWORD
# если нужно, исправить и:
docker compose up -d --force-recreate rails sidekiq
```

### Полный откат (восстановление бэкапа прод-БД)

Только если что-то **катастрофическое** (БД повреждена):
```bash
# Netangels админка → восстановить из последнего бэкапа (который делался сегодня утром)
# это займёт ~10-15 минут
```

### Откат nginx

```bash
git checkout HEAD -- /etc/nginx/
# или редактировать вручную, вернуть:
#   ssl_protocols TLSv1 TLSv1.1 TLSv1.2 TLSv1.3;
#   listen 443 ssl;
nginx -t && nginx -s reload
```

---

## Чек-лист для заказчика (в 17:30)

Перед стартом подтвердить со мной:
- [ ] «Netangels бэкап сегодняшний готов» — **да/нет**
- [ ] «Можно окно 18:00-18:30 MSK» (или другое удобное время) — **да/нет**
- [ ] «Предупредить менеджеров о 5-10 мин downtime» — **да, через скажу кому**
- [ ] «Chatwoot-postgres пароль можно менять — кто ещё им пользуется?» — **вопрос к вам**

---

## Аудитор

Подготовлено: 2026-04-20, Day 3 onboarding audit.
Статус: **DRAFT — ждёт подтверждения в 17:30 MSK**.
