# Инструкция по применению изменений на сервере

Два окружения: **прод** (crm.groupprofi.ru) и **стагинг** (crm-staging.groupprofi.ru). Они работают **одновременно** на одном сервере в **разных папках**. Workflow: изменения сначала деплоятся на стагинг, тестируются, затем — на прод.

**Подробный порядок работы:** см. **[DEPLOY_WORKFLOW.md](DEPLOY_WORKFLOW.md)** (две папки, Staging → Production).

---

## Раскладка окружений (две папки)

| Окружение | Папка на сервере | Домен | Скрипт деплоя |
|-----------|------------------|--------|----------------|
| **Production** | `/opt/proficrm` | crm.groupprofi.ru | `./deploy_security.sh` или `./deploy_production.sh` |
| **Staging** | `/opt/proficrm-staging` | crm-staging.groupprofi.ru | `./deploy_staging.sh` |

Staging — в **отдельной папке** (`/opt/proficrm-staging`), чтобы не путать с продом и чтобы оба стека могли работать одновременно (разные тома и контейнеры).

### Порядок выкатки (Staging → Production)

1. **Код** — пуш в `main` (или в ветку для стагинга).
2. **Стагинг** — в папке стагинга: `git pull && ./deploy_staging.sh`, проверить https://crm-staging.groupprofi.ru.
3. **Тест** — убедиться, что всё работает на стагинге.
4. **Прод** — в папке прода: `git pull && ./deploy_security.sh`, проверить https://crm.groupprofi.ru.

Подробно: [DEPLOY_WORKFLOW.md](DEPLOY_WORKFLOW.md).

---

## Вариант 1: Скрипты деплоя (рекомендуется)

| Скрипт | Где запускать | Назначение |
|--------|----------------|------------|
| `./deploy_staging.sh` | **Папка стагинга** (`/opt/proficrm-staging`) | Деплой стагинга (staging.yml, .env.staging) |
| `./deploy_security.sh` | **Папка прода** (`/opt/proficrm`) | Деплой прода на VDS (prod.yml + vds.yml) |
| `./deploy_production.sh` | **Папка прода** (`/opt/proficrm`) | Деплой прода без vds (только prod.yml) |

### Чем отличаются compose-файлы (прод)

| | docker-compose.yml (старый прод) | docker-compose.prod.yml (рекомендуется) |
|--|----------------------------------|----------------------------------------|
| **Образ web** | python:3.13-slim, код монтируется с хоста | Сборка из Dockerfile, код внутри образа |
| **Запуск** | runserver + pip install при старте | **gunicorn**, без монтирования кода |
| **Статика/медиа** | volume `media`, static при старте | `./data/staticfiles`, `./data/media` (каталоги на хосте) |
| **Безопасность** | нет | healthcheck, cap_drop, no-new-privileges, лимиты памяти |
| **Typesense** | есть | есть (добавлен в prod.yml) |
| **Тома БД** | pgdata, redisdata, typesense_data | те же — при переходе **данные не теряются** |

Для **прод на VDS** используйте **prod.yml + vds.yml**: тот же prod, плюс порты (15432 для БД, 8001 для web). Имена томов общие — БД сохраняется.

### Прод (crm.groupprofi.ru) — папка `/opt/proficrm`

**Вариант A — без VDS-оверлея (только prod.yml):**
```bash
cd /opt/proficrm
./deploy_production.sh
```

**Вариант B — прод на VDS (prod.yml + vds.yml, порт 8001):**
```bash
cd /opt/proficrm
./deploy_security.sh
```

Перед первым запуском: создать каталоги (на сервере):
```bash
cd /opt/proficrm
mkdir -p data/staticfiles data/media
chown 1000:1000 data/staticfiles data/media
```

### Staging (crm-staging.groupprofi.ru) — папка `/opt/proficrm-staging`

Staging разворачивается в **отдельной папке**, чтобы работать одновременно с продом.

```bash
cd /opt/proficrm-staging
chmod +x deploy_staging.sh   # один раз, если Permission denied
./deploy_staging.sh
```

Первый раз: клонировать репозиторий в `/opt/proficrm-staging`, запустить `./scripts/setup_staging_env.sh` (подставит ключи), задать в `.env.staging` **POSTGRES_PASSWORD**, затем `./deploy_staging.sh`. Подробно: [DEPLOY_WORKFLOW.md](DEPLOY_WORKFLOW.md).

### Переход с docker-compose.yml на prod.yml (без потери БД)

1. Запускать из **того же каталога** (`/opt/proficrm`), **не** использовать `docker compose down -v`.
2. Создать `data/staticfiles`, `data/media`, один раз выполнить collectstatic (через текущий web или после первого `up` с prod.yml).
3. Остановить старый стек, поднять prod: `docker compose -f docker-compose.prod.yml -f docker-compose.vds.yml up -d --build`. Тома `pgdata`, `redisdata`, `typesense_data` общие — данные останутся.

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

1. Убедитесь, что контейнер Typesense запущен (в `docker-compose.prod.yml` и `docker-compose.staging.yml` есть сервис `typesense`).
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

## Вариант 3: Прод на VDS (prod.yml + vds.yml)

Скрипт `./deploy_security.sh` или вручную:

```bash
cd /path/to/project

# 1. Обновление кода
git pull origin main

# 2. Применение миграций
docker compose -f docker-compose.prod.yml -f docker-compose.vds.yml exec web python manage.py migrate

# 3. Сбор статики
docker compose -f docker-compose.prod.yml -f docker-compose.vds.yml exec web python manage.py collectstatic --noinput

# 4. Перезапуск
docker compose -f docker-compose.prod.yml -f docker-compose.vds.yml restart web
```

---

## Синхронизация пароля PostgreSQL с .env

Если в логах **web** или **celery** появляется `password authentication failed for user "crm"`, значит пароль, который приложение читает из `.env` (переменная `POSTGRES_PASSWORD`), **не совпадает** с паролем пользователя `crm` в PostgreSQL.

### Откуда берётся пароль и почему «раньше был другой»

- **Пароль всегда берётся из файла `.env`** в каталоге проекта (рядом с `docker-compose.yml`). Docker Compose при `docker compose up` читает этот файл и подставляет `${POSTGRES_PASSWORD}` в контейнеры. Других источников пароля в compose нет.
- **При первом запуске** контейнер `db` (Postgres) **инициализирует** базу и создаёт пользователя `crm` с паролем из `POSTGRES_PASSWORD` на тот момент. То есть изначально пароль в БД и в `.env` совпадали — поэтому раньше всё работало.
- **После этого** пароль мог разъехаться, если:
  1. В БД вручную выполнили `ALTER USER crm WITH PASSWORD 'новый';` и **не** обновили тот же пароль в `.env`, или  
  2. Восстановили БД из бэкапа (в бэкапе уже был другой пароль), а в `.env` оставили старый/пустой.

**Итог:** либо приведите пароль в БД к значению из `.env` (ALTER USER), либо измените в `.env` на тот пароль, который сейчас в БД — и перезапустите сервисы.

### Почему БД «сбрасывается» при смене пароля

Данные PostgreSQL лежат в **томе** `pgdata`. БД обнуляется только если этот том удалён. Это происходит при:

- **`docker compose down -v`** — флаг `-v` удаляет тома, в том числе с базой. После следующего `up` создаётся пустая БД с паролем из `.env`.
- Ручное удаление тома или каталога с данными.

**Важно:** никогда не используйте `down -v`, если нужно сохранить данные. Менять пароль можно **без пересоздания контейнера `db`** и без удаления тома — только через `ALTER USER` в работающей БД и обновление `.env`, затем пересоздание только приложения (web, celery, celery-beat).

### Безопасная смена пароля (данные БД сохраняются)

1. Придумайте новый пароль и запишите его.
2. Задайте его в **работающей** БД (контейнер `db` не перезапускайте и не пересоздавайте):
   ```bash
   cd /opt/proficrm
   docker compose -f docker-compose.prod.yml -f docker-compose.vds.yml exec db psql -U crm -d crm -c "ALTER USER crm WITH PASSWORD 'ваш_новый_пароль';"
   ```
3. Пропишите **тот же** пароль в `.env` в строке `POSTGRES_PASSWORD=...`, сохраните файл.
4. Пересоздайте **только** приложение (не `db`), чтобы подхватить новый `.env`:
   ```bash
   docker compose -f docker-compose.prod.yml -f docker-compose.vds.yml up -d --force-recreate web celery celery-beat
   ```
5. Не выполняйте `docker compose down -v` — иначе том с БД будет удалён и при следующем `up` база создастся заново (пустая).

### Проверка, что Compose видит .env

Запускать из **корня проекта** (где лежит `.env`), например `/opt/proficrm`:

```bash
cd /opt/proficrm
docker compose -f docker-compose.prod.yml -f docker-compose.vds.yml run --rm --no-deps web sh -c 'if [ -n "$POSTGRES_PASSWORD" ]; then echo "POSTGRES_PASSWORD: задан, длина ${#POSTGRES_PASSWORD}"; else echo "POSTGRES_PASSWORD: пустой или не задан"; fi'
```

Если видите «пустой или не задан» — файл `.env` не найден, пустой или переменная не экспортируется (проверьте, что запускаете из каталога с `.env`).

### Что сделать

1. **Откройте `.env` на сервере** (для прода — `/opt/proficrm/.env`, для staging — `.env.staging`):
   ```bash
   nano /opt/proficrm/.env
   ```
2. **Проверьте строку `POSTGRES_PASSWORD=`** — без кавычек, без пробелов до/после `=`, без лишних символов в конце строки. Значение должно **точно** совпадать с паролем, заданным в БД.
3. **Выберите один из вариантов:**
   - **Вариант A:** Запомнить пароль из `.env` и задать его в PostgreSQL:
     ```bash
     # Подставьте вместо YOUR_PASSWORD_FROM_ENV значение из .env
     docker compose -f docker-compose.prod.yml -f docker-compose.vds.yml exec db psql -U crm -d crm -c "ALTER USER crm WITH PASSWORD 'YOUR_PASSWORD_FROM_ENV';"
     ```
   - **Вариант B:** Задать новый пароль в БД (как вы уже делали), затем **обязательно** прописать тот же пароль в `.env` в `POSTGRES_PASSWORD=...` и сохранить файл.
4. **Пересоздайте только контейнеры приложения** (не только restart, и не трогайте `db`): при `restart` переменные из `.env` не перечитываются. Чтобы приложение подхватило новый пароль:
   ```bash
   docker compose -f docker-compose.prod.yml -f docker-compose.vds.yml up -d --force-recreate web celery celery-beat
   ```
   Не используйте `docker compose down -v` — это удалит том с БД и при следующем запуске база будет пустой.
5. **Проверка:**
   ```bash
   curl -sI http://127.0.0.1:8001/health/
   docker compose -f docker-compose.prod.yml -f docker-compose.vds.yml logs web --tail=30
   ```

Если после этого ошибка сохраняется — проверьте, что в `.env` нет опечатки (например, лишняя цифра в пароле) и что при редактировании не появились пробелы или кавычки вокруг значения.

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

Если `git pull` выдаёт **«would be overwritten by merge»** или **«Your local changes would be overwritten by merge»** для `deploy_staging.sh` — на сервере есть локальные правки; сбросьте их и подтяните версию из репозитория:

```bash
# Вариант 1: отменить локальные правки и подтянуть
git checkout -- deploy_staging.sh
git pull origin main
chmod +x deploy_staging.sh
./deploy_staging.sh
```

```bash
# Вариант 2: сохранить правки в stash, потом pull
git stash push deploy_staging.sh
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
