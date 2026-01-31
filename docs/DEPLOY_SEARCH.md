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

## Включение Typesense на проде (рекомендуется)

Typesense уже поднят в Docker и даёт поиск с опечатками, префиксами и весами полей. Чтобы использовать его вместо Postgres:

**1. В `.env` на сервере добавить или изменить:**

```bash
# В /opt/proficrm/.env
SEARCH_ENGINE_BACKEND=typesense
TYPESENSE_HOST=typesense
TYPESENSE_PORT=8108
TYPESENSE_PROTOCOL=http
TYPESENSE_API_KEY=xyz
```
Значение `TYPESENSE_API_KEY` должно совпадать с ключом контейнера Typesense (в `docker-compose.prod.yml` используется `${TYPESENSE_API_KEY:-xyz}` — задайте тот же ключ в `.env` или оставьте `xyz` для внутренней сети).

**2. Перезапустить приложение (подхватить переменные):**

```bash
cd /opt/proficrm
docker compose -f docker-compose.prod.yml -f docker-compose.vds.yml up -d --force-recreate web celery celery-beat
```

**3. Заполнить индекс Typesense (один раз, ~32k компаний займёт несколько минут):**

```bash
cd /opt/proficrm
docker compose -f docker-compose.prod.yml -f docker-compose.vds.yml run --rm web python manage.py index_companies_typesense
```

**4. По желанию — стоп-слова и синонимы:**

```bash
docker compose -f docker-compose.prod.yml -f docker-compose.vds.yml run --rm web python manage.py sync_typesense_stopwords
docker compose -f docker-compose.prod.yml -f docker-compose.vds.yml run --rm web python manage.py sync_typesense_synonyms
```

**5. Проверка:** https://crm.groupprofi.ru → «Компании» → поиск. В `/health/` при Typesense появится `checks.search_typesense` (`ok` или `unavailable`).

При недоступности Typesense поиск автоматически идёт через Postgres (`TYPESENSE_FALLBACK_TO_POSTGRES=1` по умолчанию). Дальнейшие изменения компаний синхронизируются в Typesense через сигналы.

---

## Подтверждение стека

- **По умолчанию:** PostgreSQL (FTS + pg_trgm), `SEARCH_ENGINE_BACKEND=postgres`.
- **С Typesense:** задать `SEARCH_ENGINE_BACKEND=typesense` и `TYPESENSE_*`, выполнить `index_companies_typesense`.
- **БД:** `DB_ENGINE=postgres`. Postgres всегда используется для данных; Typesense — только для поиска при включённом бэкенде.

## Поддержка актуальности индекса

- **Postgres:** сигналы обновляют `CompanySearchIndex`; ручной `rebuild_company_search_index` — после массового импорта.
- **Typesense:** сигналы вызывают `index_company()` при сохранении компании; полная переиндексация — `index_companies_typesense`.
