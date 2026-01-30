# Инструкция по применению изменений на сервере

## Вариант 1: Использование готового скрипта деплоя (рекомендуется)

| Скрипт | Назначение |
|--------|------------|
| `./deploy_production.sh` | Прод (docker-compose.prod.yml, .env) |
| `./deploy_staging.sh` | Staging crm-staging.groupprofi.ru (docker-compose.staging.yml, .env.staging) |
| `./deploy_crm_fixes.sh` | Быстрый деплой исправлений (docker-compose.yml) |
| `./deploy_security.sh` | Безопасность на VDS (docker-compose.yml + docker-compose.vds.yml) |

### Для Production (docker-compose.prod.yml):
```bash
cd /path/to/project
./deploy_production.sh
```

### Для Staging (crm-staging.groupprofi.ru):
```bash
cd /path/to/project
chmod +x deploy_staging.sh   # один раз, если Permission denied
./deploy_staging.sh
```

### Для быстрого деплоя исправлений:
```bash
cd /path/to/project
./deploy_crm_fixes.sh
```

### Для настройки безопасности на VDS (docker-compose.vds.yml):
```bash
cd /path/to/project
./deploy_security.sh
```

---

## Вариант 2: Ручное применение (пошагово)

### Шаг 1: Подключитесь к серверу
```bash
ssh user@your-server
cd /path/to/project
```

### Шаг 2: Обновите код из репозитория
```bash
git pull origin main
```

### Шаг 3: Примените миграции базы данных

**Если используете Docker:**
```bash
# Для docker-compose.yml
docker compose exec web python manage.py migrate

# Или для docker-compose.prod.yml
docker compose -f docker-compose.prod.yml exec web python manage.py migrate
```

**Если без Docker (прямой запуск):**
```bash
cd backend
source venv/bin/activate  # или ваш способ активации виртуального окружения
python manage.py migrate
```

### Шаг 4: Соберите статические файлы (если нужно)

**Если используете Docker:**
```bash
docker compose exec web python manage.py collectstatic --noinput
```

**Если без Docker:**
```bash
python manage.py collectstatic --noinput
```

### Шаг 5: Перезапустите приложение

**Если используете Docker:**
```bash
# Перезапуск только web-сервиса
docker compose restart web

# Или полный перезапуск
docker compose down
docker compose up -d
```

**Если используете systemd (gunicorn/uwsgi):**
```bash
sudo systemctl restart crm  # или имя вашего сервиса
```

**Если используете supervisor:**
```bash
sudo supervisorctl restart crm  # или имя вашего процесса
```

### Шаг 5.1: Опционально — Typesense (поиск компаний)

Если включён поиск через Typesense (`SEARCH_ENGINE_BACKEND=typesense` в окружении):

1. Убедитесь, что контейнер Typesense запущен (в `docker-compose.yml` есть сервис `typesense`).
2. После миграций выполните полную переиндексацию:
   ```bash
   docker compose exec web python manage.py index_companies_typesense
   ```
3. При необходимости обновите стоп-слова:
   ```bash
   docker compose exec web python manage.py sync_typesense_stopwords
   ```
4. Проверка в `/health/`: при `SEARCH_ENGINE_BACKEND=typesense` в ответе будет `checks.search_typesense` (`ok` или `unavailable`). При недоступности Typesense поиск автоматически идёт через Postgres (если `TYPESENSE_FALLBACK_TO_POSTGRES=1`).

### Шаг 6: Очистите кэш (опционально, но рекомендуется)

**Если используете Docker:**
```bash
docker compose exec web python manage.py shell -c "from django.core.cache import cache; cache.clear()"
```

**Если без Docker:**
```bash
python manage.py shell -c "from django.core.cache import cache; cache.clear()"
```

---

## Вариант 3: Для VDS с docker-compose.vds.yml

Можно использовать скрипт `./deploy_security.sh` (обновление кода, миграции, collectstatic, проверка DEBUG/SECRET_KEY, перезапуск) или вручную:

```bash
cd /path/to/project

# 1. Обновление кода
git pull origin main

# 2. Применение миграций
docker compose -f docker-compose.yml -f docker-compose.vds.yml exec web python manage.py migrate

# 3. Сбор статики
docker compose -f docker-compose.yml -f docker-compose.vds.yml exec web python manage.py collectstatic --noinput

# 4. Перезапуск
docker compose -f docker-compose.yml -f docker-compose.vds.yml restart web
```

---

## Staging (crm-staging.groupprofi.ru)

Staging поднят на **отдельном поддомене** `crm-staging.groupprofi.ru`, чтобы не затрагивать прод `crm.groupprofi.ru`. Сервер (IP **5.181.254.172**) общий с продом: прод занимает порты 80/443, staging — **8080** (контейнер nginx слушает `127.0.0.1:8080`).

### Подготовка (один раз)

1. **DNS:** A-запись `crm-staging.groupprofi.ru` → **5.181.254.172** (тот же IP, что и прод).

2. **Файл окружения:** скопируйте шаблон и задайте секреты:
   ```bash
   cp env.staging.template .env.staging
   # Отредактируйте .env.staging: DJANGO_SECRET_KEY, POSTGRES_PASSWORD, MAILER_FERNET_KEY и т.д.
   ```

3. **Запуск:**
   ```bash
   docker compose -f docker-compose.staging.yml up -d --build
   ```
   Или скрипт: `./deploy_staging.sh`

4. **Хост-Nginx (staging и прод на одном сервере):** прод слушает 80/443, staging-контейнер — только `127.0.0.1:8080`. Добавьте на хосте в конфиг Nginx блок для staging: скопируйте содержимое `nginx/snippets/staging-proxy.conf` в конфиг или в секцию `http { }` добавьте `include /path/to/project/nginx/snippets/staging-proxy.conf;`, затем `nginx -t` и `systemctl reload nginx`. После выдачи HTTPS для staging — добавьте `listen 443 ssl` и редирект с 80 на 443 для `crm-staging.groupprofi.ru`.

5. **После первого запуска (миграции уже в command):** при необходимости включите Typesense и переиндексируйте:
   ```bash
   # В .env.staging: SEARCH_ENGINE_BACKEND=typesense (и TYPESENSE_*)
   docker compose -f docker-compose.staging.yml exec web python manage.py index_companies_typesense
   docker compose -f docker-compose.staging.yml exec web python manage.py sync_typesense_stopwords
   docker compose -f docker-compose.staging.yml exec web python manage.py sync_typesense_synonyms
   ```

6. **HTTPS (опционально):** на сервере с staging установите certbot и получите сертификат:
   ```bash
   certbot certonly --nginx -d crm-staging.groupprofi.ru
   ```
   Затем добавьте в `nginx/staging.conf` блок `server { listen 443 ssl; server_name crm-staging.groupprofi.ru; ... }` с путями к сертификату и редирект с 80 на 443 для этого host. В `.env.staging` при HTTPS можно включить `DJANGO_SECURE_SSL_REDIRECT=1` и т.д.

### Обновление staging

Рекомендуется скрипт (миграции и collectstatic включены):

```bash
cd /path/to/project
chmod +x deploy_staging.sh   # один раз, если ещё не исполняемый
./deploy_staging.sh
```

Если `git pull` выдаёт **«would be overwritten by merge»** для `deploy_staging.sh` — на сервере есть локальная копия; уберите или переименуйте её, затем снова выполните pull:

```bash
mv deploy_staging.sh deploy_staging.sh.bak   # или: rm deploy_staging.sh
git pull origin main
chmod +x deploy_staging.sh
./deploy_staging.sh
```

Вручную:

```bash
cd /path/to/project
git pull origin main
docker compose -f docker-compose.staging.yml build web
docker compose -f docker-compose.staging.yml up -d
# Миграции выполняются при старте web в command; при необходимости: exec web python manage.py migrate
```

На хосте для прокси staging можно использовать готовый сниппет: скопируйте `nginx/snippets/staging-proxy.conf` в конфиг Nginx или добавьте `include /path/to/project/nginx/snippets/staging-proxy.conf;`.

---

## Проверка после деплоя

### 1. Проверьте, что миграции применены:
```bash
docker compose exec web python manage.py showmigrations ui
```

Должны быть отмечены как `[X]`:
- `[X] 0007_remove_uiuserpreference_company_list_view_mode`
- `[X] 0008_uiuserpreference_company_detail_view_mode`

### 2. Проверьте работоспособность:
- Откройте `/companies/` - должен быть обычный список (без переключателя)
- Откройте любую карточку компании `/companies/<uuid>/`
- Проверьте наличие переключателя режимов (две иконки в шапке)
- Переключите режим и убедитесь, что он сохраняется

### 3. Проверьте логи (если что-то не работает):
```bash
# Docker
docker compose logs web --tail=50

# Или systemd
sudo journalctl -u crm -n 50
```

---

## Важные замечания

1. **Резервная копия БД (рекомендуется перед миграциями):**
   ```bash
   # Если есть скрипт backup
   ./scripts/backup_postgres.sh
   
   # Или вручную
   docker compose exec db pg_dump -U crm crm > backup_$(date +%Y%m%d_%H%M%S).sql
   ```

2. **Миграции безопасны:**
   - `0007` - удаляет поле `company_list_view_mode` (если оно было)
   - `0008` - добавляет поле `company_detail_view_mode` с default='classic'
   - Обе миграции обратимые и безопасные

3. **Если возникли проблемы:**
   - Проверьте логи: `docker compose logs web`
   - Проверьте статус контейнеров: `docker compose ps`
   - Убедитесь, что база данных доступна

---

## Быстрая команда (все в одной строке)

```bash
cd /path/to/project && \
git pull origin main && \
docker compose exec web python manage.py migrate && \
docker compose exec web python manage.py collectstatic --noinput && \
docker compose restart web && \
echo "✅ Деплой завершен!"
```
