# Workflow деплоя: Staging → Production

Два окружения работают **одновременно** на одном сервере. Все изменения сначала попадают на **staging**, тестируются, затем переносятся на **production**.

---

## 1. Разделение окружений (две папки)

Чтобы прод и стагинг не мешали друг другу и работали одновременно:

| Окружение | Папка на сервере | Домен | Compose | Файл окружения |
|-----------|------------------|--------|---------|----------------|
| **Production** | `/opt/proficrm` | crm.groupprofi.ru | prod.yml + vds.yml | `.env` |
| **Staging** | `/opt/proficrm-staging` | crm-staging.groupprofi.ru | staging.yml | `.env.staging` |

У каждого окружения свои тома БД, Redis (и, при необходимости, Typesense) и свои контейнеры — конфликтов нет.
Typesense может оставаться в docker-compose, но приложение его больше не использует (поиск полностью на PostgreSQL FTS).

---

## 2. Первоначальная настройка (один раз)

### Production (прод уже может быть в /opt/proficrm)

```bash
# Если прод ещё не развёрнут:
sudo mkdir -p /opt/proficrm
sudo chown $USER:$USER /opt/proficrm
cd /opt/proficrm
git clone https://github.com/darbyhtml/proficrm.git .
cp env.template .env
# Отредактировать .env: POSTGRES_PASSWORD, DJANGO_SECRET_KEY, DJANGO_ALLOWED_HOSTS, DJANGO_CSRF_TRUSTED_ORIGINS и т.д.
mkdir -p data/staticfiles data/media
# Деплой: ./deploy_security.sh  (или ./deploy_production.sh)
```

### Staging (отдельная папка)

```bash
sudo mkdir -p /opt/proficrm-staging
sudo chown $USER:$USER /opt/proficrm-staging
cd /opt/proficrm-staging
git clone https://github.com/darbyhtml/proficrm.git .
# Настройка паролей и ключей (подставит DJANGO_SECRET_KEY и MAILER_FERNET_KEY; пароль задать вручную):
chmod +x scripts/setup_staging_env.sh deploy_staging.sh
./scripts/setup_staging_env.sh
nano .env.staging   # задать POSTGRES_PASSWORD=надёжный_пароль (обязательно)
./deploy_staging.sh
```

**Обязательно в .env.staging:** `POSTGRES_PASSWORD`, `DJANGO_SECRET_KEY`, `MAILER_FERNET_KEY`. Скрипт `setup_staging_env.sh` подставляет ключи из шаблона; пароль БД задаёте вы. Без этого `deploy_staging.sh` выдаст ошибку и не запустит compose.

На хосте Nginx должен быть настроен:
- **crm.groupprofi.ru** → `127.0.0.1:8001` (прод)
- **crm-staging.groupprofi.ru** → `127.0.0.1:8080` (стагинг)

### Создание пользователя с ролью «Администратор»

После первого деплоя (стагинг или прод) нужно создать пользователя с ролью **Администратор** (не Менеджер), чтобы входить в CRM и управлять настройками.

**Создать нового администратора** (пароль можно не указывать — сгенерируется и выведется в консоль):

```bash
# На сервере в папке стагинга или прода:
docker compose -f docker-compose.staging.yml exec web python manage.py create_admin_user admin --email admin@example.com
# или с паролем:
docker compose -f docker-compose.staging.yml exec web python manage.py create_admin_user admin --email admin@example.com --password ваш_пароль
```

**Сделать существующего пользователя администратором** (например, у него была роль «Менеджер»):

```bash
docker compose -f docker-compose.staging.yml exec web python manage.py create_admin_user --promote имя_логина
```

Для прода замените `docker-compose.staging.yml` на `docker-compose.prod.yml -f docker-compose.vds.yml` и контейнер `web` из соответствующего compose.

### Разделение прод/стагинг: чтобы не путаться

**Проблема:** В обеих папках при `git pull` подтягивается один и тот же репозиторий — в стагинге есть прод-файлы, в проде — стагинг-файлы. Можно случайно запустить не тот скрипт или перепутать `.env` и `.env.staging`.

**Что сделано:**

1. **Очистка при каждом деплое**  
   При запуске `./deploy_staging.sh` вызывается `scripts/cleanup_for_staging.sh`: в стагинге `.env` всегда копируется из `.env.staging`, удаляются копии прод-файлов (если были). При запуске `./deploy_security.sh` и `./deploy_production.sh` вызывается `scripts/cleanup_for_prod.sh`: в проде удаляется `.env.staging`, если он случайно скопирован.

   **Важно:** CORS, виджет мессенджера и другие staging-переменные задавайте только в `.env.staging`, не в `.env` — при каждом деплое `.env` перезаписывается из `.env.staging`.

2. **Sparse-checkout (опционально)**  
   Можно один раз настроить так, чтобы при `git pull` в папке стагинга **не появлялись** прод-файлы, а в папке прода — стагинг-файлы.

   **В папке стагинга** (после клона и первого деплоя):
   ```bash
   cd /opt/proficrm-staging
   chmod +x scripts/configure_sparse_checkout.sh
   ./scripts/configure_sparse_checkout.sh staging
   ```
   После этого в этой папке не будет файлов: `deploy_production.sh`, `deploy_security.sh`, `docker-compose.prod.yml`, `docker-compose.vds.yml`, `env.template`, `scripts/promote_to_prod.sh`. При следующих `git pull` они не подтянутся.

   **В папке прода**:
   ```bash
   cd /opt/proficrm
   chmod +x scripts/configure_sparse_checkout.sh
   ./scripts/configure_sparse_checkout.sh prod
   ```
   После этого в этой папке не будет: `deploy_staging.sh`, `docker-compose.staging.yml`, `env.staging.template`, `scripts/setup_staging_env.sh`, `scripts/setup_staging_env.py`. При следующих `git pull` они не подтянутся.

   Требуется Git 2.25+. Отменить: `git config core.sparseCheckout false && rm -f .git/info/sparse-checkout && git read-tree -mu HEAD`.

3. **Ручная очистка**  
   В любой момент можно запустить вручную:
   - в стагинге: `cd /opt/proficrm-staging && ./scripts/cleanup_for_staging.sh`
   - в проде: `cd /opt/proficrm && ./scripts/cleanup_for_prod.sh`

---

## 3. Ежедневный workflow: изменения → стагинг → тест → прод

### Шаг 1: Код в репозитории

Все изменения коммитятся и пушатся в нужную ветку (обычно `main`):

```bash
git add -A && git commit -m "Описание изменений" && git push origin main
```

### Шаг 2: Деплой на Staging

На сервере в **папке стагинга**:

```bash
cd /opt/proficrm-staging
git pull origin main
./deploy_staging.sh
```

Проверка: открыть https://crm-staging.groupprofi.ru, убедиться, что всё работает (логин, компании, поиск и т.д.).

### Шаг 3: Тестирование на Staging

- Проверить основной сценарий (вход, список компаний, поиск).
- При необходимости: проверить миграции, статику, интеграции (Typesense, почта и т.д.).
- Если что-то сломалось — чинить в коде, снова пуш в `main`, снова шаг 2 (только стагинг). На прод не деплоить.

### Шаг 4: Деплой на Production (после успешного теста)

Когда на стагинге всё ок:

```bash
cd /opt/proficrm
git pull origin main
./deploy_security.sh
```

(Или `./deploy_production.sh`, если не используете vds.yml.)

Проверка: https://crm.groupprofi.ru, быстрый smoke-тест.

---

## 4. Краткая шпаргалка

| Действие | Команды |
|----------|--------|
| **Выкатить на стагинг** | `cd /opt/proficrm-staging && git pull origin main && ./deploy_staging.sh` |
| **После теста — выкатить на прод** | `cd /opt/proficrm && git pull origin main && ./deploy_security.sh` |
| **Только обновить код на стагинге (без полного деплоя)** | `cd /opt/proficrm-staging && git pull && docker compose -f docker-compose.staging.yml up -d --build web celery celery-beat` |
| **Только обновить код на проде (без полного деплоя)** | `cd /opt/proficrm && git pull && docker compose -f docker-compose.prod.yml -f docker-compose.vds.yml up -d --build web celery celery-beat` |
| **Промоут на прод с напоминанием про стагинг** | `cd /opt/proficrm && ./scripts/promote_to_prod.sh` (спросит, протестирован ли стагинг, затем pull + deploy_security.sh) |

---

## 5. Важно

- **Не деплоить на прод**, пока не проверили на стагинге.
- В обеих папках подтягивается **один и тот же репозиторий** (один и тот же `main`). Отличие только в окружении: `.env` vs `.env.staging` и разные compose-файлы.
- БД прод и стагинг **разные** (разные тома). Данные с прод в стагинг не копируются автоматически; при необходимости делайте дамп/восстановление вручную.
- Не использовать `docker compose down -v` на проде — иначе удалятся тома с БД.
