# Workflow деплоя: Staging → Production

Два окружения работают **одновременно** на одном сервере. Все изменения сначала попадают на **staging**, тестируются, затем переносятся на **production**.

---

## 1. Разделение окружений (две папки)

Чтобы прод и стагинг не мешали друг другу и работали одновременно:

| Окружение | Папка на сервере | Домен | Compose | Файл окружения |
|-----------|------------------|--------|---------|----------------|
| **Production** | `/opt/proficrm` | crm.groupprofi.ru | prod.yml + vds.yml | `.env` |
| **Staging** | `/opt/proficrm-staging` | crm-staging.groupprofi.ru | staging.yml | `.env.staging` |

У каждого окружения свои тома БД, Redis, Typesense и свои контейнеры — конфликтов нет.

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
