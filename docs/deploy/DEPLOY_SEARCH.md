# Деплой поиска компаний (PostgreSQL FTS)

Чёткие команды для запуска на staging и production и проверки поиска.

---

## Команды для запуска

### Staging (crm-staging.groupprofi.ru)

**Папка на сервере:** `/opt/proficrm-staging`.

```bash
cd /opt/proficrm-staging
git pull origin main
./deploy_staging.sh
```

---

### Production (crm.groupprofi.ru)

**Папка на сервере:** `/opt/proficrm`.

**Вариант A — прод на VDS (порт 8001, prod + vds):**
```bash
cd /opt/proficrm
git pull origin main
./deploy_security.sh
```

**Вариант B — прод без vds (только prod.yml):**
```bash
cd /opt/proficrm
git pull origin main
./deploy_production.sh
```

Скрипты сами выполняют: сборку образов, подъём db/redis/typesense, миграции, collectstatic, **перестроение поискового индекса** (`rebuild_company_search_index`), запуск web/celery/celery-beat.

---

## Команды для проверки после деплоя

### 1. Health-check (с сервера)

Django принимает только запросы с заголовком Host из `ALLOWED_HOSTS`. Если в `.env` только домен (например `DJANGO_ALLOWED_HOSTS=crm.groupprofi.ru`), то `curl http://127.0.0.1:8001/health/` вернёт **400 Bad Request** (Invalid HTTP_HOST). Варианты:

**Вариант A — добавить 127.0.0.1 в .env (рекомендуется для проверки с сервера):**
```bash
# В /opt/proficrm/.env (прод) или .env.staging (стагинг):
# DJANGO_ALLOWED_HOSTS=crm.groupprofi.ru,127.0.0.1
# Затем перезапуск: docker compose -f docker-compose.prod.yml -f docker-compose.vds.yml up -d --force-recreate web celery celery-beat
```

**Вариант B — curl с заголовком Host (без смены .env):**

**Staging:**
```bash
curl -sI -H "Host: crm-staging.groupprofi.ru" http://127.0.0.1:8080/health/
```

**Production:**
```bash
curl -sI -H "Host: crm.groupprofi.ru" http://127.0.0.1:8001/health/
```

Ожидается ответ `200 OK`.

---

### 2. Логи (если что-то пошло не так)

**Staging:**
```bash
cd /opt/proficrm-staging
docker compose -f docker-compose.staging.yml logs web --tail=50
```

**Production:**
```bash
cd /opt/proficrm
docker compose -f docker-compose.prod.yml -f docker-compose.vds.yml logs web --tail=50
```

---

### 3. Проверка поиска в браузере

| Окружение | URL | Что проверить |
|-----------|-----|----------------|
| Staging | https://crm-staging.groupprofi.ru | Войти → «Компании» → поиск по названию, ИНН, телефону |
| Production | https://crm.groupprofi.ru | То же |

**Рекомендуемые тесты поиска:**
- Часть названия компании (одно слово).
- Название с опечаткой (триграммы).
- Первые 4–5 цифр ИНН.
- Несколько цифр телефона подряд.
- Фамилия контакта + фрагмент телефона.
- Убедиться, что в результатах есть подсветка совпадений и объяснения (если UI это показывает).

---

## Ручной запуск (без полного деплоя)

Если нужно только перестроить поисковый индекс:

**Production:**
```bash
cd /opt/proficrm
docker compose -f docker-compose.prod.yml -f docker-compose.vds.yml run --rm web python manage.py rebuild_company_search_index
```

**Staging:**
```bash
cd /opt/proficrm-staging
docker compose -f docker-compose.staging.yml run --rm web python manage.py rebuild_company_search_index
```

Только миграции + статика (без перезапуска сервисов):

**Production:**
```bash
cd /opt/proficrm
COMPOSE="docker compose -f docker-compose.prod.yml -f docker-compose.vds.yml"
$COMPOSE run --rm web python manage.py migrate --noinput
$COMPOSE run --rm web python manage.py collectstatic --noinput
```

---

## Подтверждение стека

- **По умолчанию и единственный вариант:** PostgreSQL (FTS + pg_trgm), `SEARCH_ENGINE_BACKEND=postgres`.
- **Typesense:** полностью отключён; контейнер может оставаться в docker-compose, но приложение его больше не использует.
- **БД:** `DB_ENGINE=postgres`. Postgres всегда используется и для данных, и для поиска.

## Поддержка актуальности индекса

- **Postgres:** сигналы обновляют `CompanySearchIndex`; ручной `rebuild_company_search_index` — после массового импорта.
- **Ночная гигиена:** команда `normalize_companies_data` приводится в действие задачей Celery `companies.tasks.reindex_companies_daily`
  перед полным `rebuild_company_search_index`.
