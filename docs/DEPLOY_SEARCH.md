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

**Staging:**
```bash
curl -sI http://127.0.0.1:8080/health/
```

**Production:**
```bash
curl -sI http://127.0.0.1:8001/health/
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

- **Движок поиска:** PostgreSQL (FTS + pg_trgm). `SEARCH_ENGINE_BACKEND` по умолчанию `postgres`.
- **БД:** `DB_ENGINE=postgres`. Typesense при настройках по умолчанию не используется.

## Поддержка актуальности индекса

После первого заполнения индекс обновляется автоматически через сигналы при сохранении компании и связанных сущностей. Ручной `rebuild_company_search_index` нужен только после массового импорта или сбоев.
