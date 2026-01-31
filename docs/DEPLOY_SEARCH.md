# Деплой поиска компаний (PostgreSQL FTS)

Краткий чеклист для выкатки обновлённого поиска на staging и production.

## Подтверждение стека

- **Движок поиска:** PostgreSQL (FTS + pg_trgm). Переменная `SEARCH_ENGINE_BACKEND` по умолчанию `postgres`.
- **БД:** `DB_ENGINE=postgres`. Внешний движок (Typesense) не используется при настройках по умолчанию.

## Порядок деплоя

### 1. Staging

1. В папке стагинга: `git pull`, затем `./deploy_staging.sh`.
2. Скрипт применяет миграции, собирает статику и выполняет `rebuild_company_search_index`.
3. Проверка: https://crm-staging.groupprofi.ru → раздел «Компании»:
   - поиск по названию, ИНН, телефону;
   - частичное совпадение (первые цифры ИНН, фрагмент телефона, слово с опечаткой);
   - подсветка совпадений и объяснения.

### 2. Production

1. После успешной проверки на staging: в папке прода (например `/opt/proficrm`) — `git pull`, затем `./deploy_security.sh` (или `./deploy_production.sh`).
2. Скрипт применяет миграции (в т.ч. до 0042), собирает статику и запускает `rebuild_company_search_index`.
3. Убедиться, что миграции прошли без ошибок (расширения `pg_trgm`, `unaccent`, таблица `companies_companysearchindex`). При большом объёме данных GIN-индексы могут создаваться несколько минут.
4. Перезапуск сервисов выполняется самим скриптом деплоя.

### 3. Ручной запуск перестроения индекса (при необходимости)

Если шаг перестроения индекса убран из скрипта (например, из-за нагрузки), выполнить вручную:

```bash
# Production (docker-compose.prod.yml + docker-compose.vds.yml)
docker compose -f docker-compose.prod.yml -f docker-compose.vds.yml run --rm web python manage.py rebuild_company_search_index

# Staging
docker compose -f docker-compose.staging.yml run --rm web python manage.py rebuild_company_search_index
```

Желательно запускать в период низкой нагрузки; при тысячах компаний процесс может занять несколько минут.

### 4. Проверка на проде

- https://crm.groupprofi.ru → «Компании».
- Примеры: часть названия с опечаткой, первые 4–5 цифр ИНН, фрагмент телефона, фамилия контакта + цифры телефона.
- Убедиться, что подсветка и объяснения работают. В `.env` не должно быть `SEARCH_ENGINE_BACKEND=typesense`, если используется Postgres.

## Поддержка актуальности индекса

После первоначального заполнения индекс обновляется автоматически через сигналы (`companies/signals.py`): при сохранении компании и связанных сущностей (контакты, телефоны, email, заметки, задачи) по `transaction.on_commit` вызывается перестроение `CompanySearchIndex` для этой компании. Дополнительный ручной перезапуск `rebuild_company_search_index` нужен только после массового импорта или при сбоях.

## Полезные команды

```bash
# Миграции (если не через скрипт)
docker compose -f docker-compose.prod.yml -f docker-compose.vds.yml run --rm web python manage.py migrate --noinput

# Сбор статики
docker compose -f docker-compose.prod.yml -f docker-compose.vds.yml run --rm web python manage.py collectstatic --noinput
```
