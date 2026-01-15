# Быстрый старт: Развертывание STAGING

**IP:** 95.142.47.245  
**Сервер:** Ubuntu 24.04, root доступ

## Шаг 1: Подготовка сервера (выполнить на сервере)

```bash
# Обновление системы
apt update && apt upgrade -y

# Установка базовых пакетов
apt install -y curl wget git ufw software-properties-common apt-transport-https ca-certificates gnupg lsb-release

# Настройка firewall
ufw allow 22/tcp
ufw allow 80/tcp
ufw --force enable

# Установка Docker
apt remove -y docker docker-engine docker.io containerd runc 2>/dev/null || true
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable docker
systemctl start docker
```

## Шаг 2: Клонирование репозитория

```bash
mkdir -p /opt/crm-staging
cd /opt/crm-staging
git clone https://github.com/darbyhtml/proficrm.git .
```

**Примечание:** Если используете SSH, настройте deploy key (см. STAGING_DEPLOY.md раздел C).

## Шаг 3: Создание .env.staging

```bash
cd /opt/crm-staging

# Копируем шаблон
cp env.staging.template .env.staging

# Генерируем ключи
SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(50))")
FERNET_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
POSTGRES_PASSWORD=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-25)

# Заменяем в .env.staging
sed -i "s|DJANGO_SECRET_KEY=CHANGE_ME_GENERATE_STRONG_KEY|DJANGO_SECRET_KEY=$SECRET_KEY|g" .env.staging
sed -i "s|MAILER_FERNET_KEY=CHANGE_ME_GENERATE_FERNET_KEY|MAILER_FERNET_KEY=$FERNET_KEY|g" .env.staging
sed -i "s|POSTGRES_PASSWORD=CHANGE_ME_STRONG_PASSWORD|POSTGRES_PASSWORD=$POSTGRES_PASSWORD|g" .env.staging
```

## Шаг 4: Запуск

```bash
cd /opt/crm-staging

# Сборка и запуск
docker compose -f docker-compose.staging.yml build
docker compose -f docker-compose.staging.yml up -d

# Миграции и статика
docker compose -f docker-compose.staging.yml exec -T web python manage.py migrate
docker compose -f docker-compose.staging.yml exec -T web python manage.py collectstatic --noinput

# Создание суперпользователя (опционально)
docker compose -f docker-compose.staging.yml exec web python manage.py createsuperuser
```

## Шаг 5: Проверка

```bash
# Проверка статуса
docker compose -f docker-compose.staging.yml ps

# Проверка health check
curl http://95.142.47.245/health/

# Логи
docker compose -f docker-compose.staging.yml logs -f
```

## Деплой новых версий

```bash
cd /opt/crm-staging
git pull
docker compose -f docker-compose.staging.yml build
docker compose -f docker-compose.staging.yml up -d
docker compose -f docker-compose.staging.yml exec -T web python manage.py migrate
docker compose -f docker-compose.staging.yml exec -T web python manage.py collectstatic --noinput
```

Или используйте скрипт из STAGING_DEPLOY.md раздел F.

---

**Готово!** Откройте в браузере: http://95.142.47.245

Полная документация: см. `STAGING_DEPLOY.md`
