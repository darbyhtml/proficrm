# Развертывание STAGING окружения на VDS Ubuntu 24.04

**IP сервера:** 95.142.47.245  
**ОС:** Ubuntu 24.04.2 LTS  
**Доступ:** root по SSH

---

## A) Подготовка системы

### 1. Обновление системы и установка базовых пакетов

```bash
# Обновление списка пакетов
apt update && apt upgrade -y

# Установка базовых утилит
apt install -y curl wget git ufw software-properties-common apt-transport-https ca-certificates gnupg lsb-release
```

### 2. Настройка Firewall (UFW)

```bash
# Разрешаем SSH (ВАЖНО: сначала SSH!)
ufw allow 22/tcp

# Разрешаем HTTP
ufw allow 80/tcp

# Включаем firewall
ufw --force enable

# Проверяем статус
ufw status
```

### 3. Установка Docker

```bash
# Удаляем старые версии (если есть)
apt remove -y docker docker-engine docker.io containerd runc 2>/dev/null || true

# Установка зависимостей
apt install -y ca-certificates curl gnupg lsb-release

# Добавляем официальный GPG ключ Docker
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

# Добавляем репозиторий Docker
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null

# Устанавливаем Docker Engine и Docker Compose plugin
apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Проверяем установку
docker --version
docker compose version

# Запускаем Docker при старте системы
systemctl enable docker
systemctl start docker

# Проверяем статус
systemctl status docker
```

---

## B) Структура каталогов

```bash
# Создаем директорию для staging
mkdir -p /opt/crm-staging
cd /opt/crm-staging
```

---

## C) Клонирование репозитория

### Вариант 1: Использование HTTPS с Personal Access Token (рекомендуется)

1. Создайте Personal Access Token на GitHub:
   - Settings → Developer settings → Personal access tokens → Tokens (classic)
   - Scopes: `repo` (полный доступ к репозиториям)

2. Клонируем репозиторий:

```bash
cd /opt/crm-staging
git clone https://github.com/darbyhtml/proficrm.git .
```

При запросе пароля используйте Personal Access Token вместо пароля.

### Вариант 2: Использование SSH ключа (deploy key)

```bash
# Генерируем SSH ключ для deploy
ssh-keygen -t ed25519 -C "deploy-staging" -f ~/.ssh/deploy_staging -N ""

# Показываем публичный ключ для добавления в GitHub
cat ~/.ssh/deploy_staging.pub
```

Добавьте публичный ключ в GitHub:
- Settings → Deploy keys → Add deploy key
- Title: `staging-server`
- Key: содержимое `~/.ssh/deploy_staging.pub`
- Allow write access: НЕ включайте (только чтение)

```bash
# Настраиваем SSH для использования этого ключа
cat >> ~/.ssh/config << EOF
Host github.com
    HostName github.com
    User git
    IdentityFile ~/.ssh/deploy_staging
    IdentitiesOnly yes
EOF

chmod 600 ~/.ssh/config

# Клонируем репозиторий
cd /opt/crm-staging
git clone git@github.com:darbyhtml/proficrm.git .
```

---

## D) Конфигурация

### 1. Создание .env.staging

```bash
cd /opt/crm-staging

# Копируем шаблон
cp env.staging.template .env.staging

# Генерируем SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(50))" > /tmp/secret_key.txt
SECRET_KEY=$(cat /tmp/secret_key.txt)

# Генерируем MAILER_FERNET_KEY
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" > /tmp/fernet_key.txt
FERNET_KEY=$(cat /tmp/fernet_key.txt)

# Генерируем пароль для PostgreSQL
POSTGRES_PASSWORD=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-25)

# Редактируем .env.staging
nano .env.staging
```

**Важно:** Замените в `.env.staging`:
- `DJANGO_SECRET_KEY=CHANGE_ME_GENERATE_STRONG_KEY` → `DJANGO_SECRET_KEY=<ваш сгенерированный ключ>`
- `MAILER_FERNET_KEY=CHANGE_ME_GENERATE_FERNET_KEY` → `MAILER_FERNET_KEY=<ваш сгенерированный ключ>`
- `POSTGRES_PASSWORD=CHANGE_ME_STRONG_PASSWORD` → `POSTGRES_PASSWORD=<ваш сгенерированный пароль>`

Или используйте sed для автоматической замены:

```bash
cd /opt/crm-staging
sed -i "s|DJANGO_SECRET_KEY=CHANGE_ME_GENERATE_STRONG_KEY|DJANGO_SECRET_KEY=$SECRET_KEY|g" .env.staging
sed -i "s|MAILER_FERNET_KEY=CHANGE_ME_GENERATE_FERNET_KEY|MAILER_FERNET_KEY=$FERNET_KEY|g" .env.staging
sed -i "s|POSTGRES_PASSWORD=CHANGE_ME_STRONG_PASSWORD|POSTGRES_PASSWORD=$POSTGRES_PASSWORD|g" .env.staging

# Удаляем временные файлы
rm -f /tmp/secret_key.txt /tmp/fernet_key.txt
```

### 2. Проверка наличия файлов

```bash
cd /opt/crm-staging
ls -la docker-compose.staging.yml Dockerfile.staging nginx/staging.conf env.staging.template
```

Все файлы должны существовать.

---

## E) Первый запуск

### 1. Сборка и запуск контейнеров

```bash
cd /opt/crm-staging

# Собираем образы
docker compose -f docker-compose.staging.yml build

# Запускаем контейнеры
docker compose -f docker-compose.staging.yml up -d

# Проверяем статус
docker compose -f docker-compose.staging.yml ps
```

### 2. Выполнение миграций и collectstatic

```bash
cd /opt/crm-staging

# Миграции (если не выполнились автоматически)
docker compose -f docker-compose.staging.yml exec web python manage.py migrate

# Сбор статики
docker compose -f docker-compose.staging.yml exec web python manage.py collectstatic --noinput
```

### 3. Создание суперпользователя (опционально)

```bash
docker compose -f docker-compose.staging.yml exec web python manage.py createsuperuser
```

Следуйте инструкциям для создания администратора.

### 4. Проверка работоспособности

```bash
# Проверяем логи
docker compose -f docker-compose.staging.yml logs -f

# Проверяем health check
curl http://95.142.47.245/health/

# Проверяем доступность главной страницы
curl -I http://95.142.47.245/
```

---

## F) Деплой новых версий

Создайте скрипт для удобного деплоя:

```bash
cat > /opt/crm-staging/deploy.sh << 'EOF'
#!/bin/bash
set -e

cd /opt/crm-staging

echo "🔄 Обновление кода из репозитория..."
git pull

echo "🔨 Сборка Docker образов..."
docker compose -f docker-compose.staging.yml build

echo "🚀 Перезапуск контейнеров..."
docker compose -f docker-compose.staging.yml up -d

echo "📦 Выполнение миграций..."
docker compose -f docker-compose.staging.yml exec -T web python manage.py migrate

echo "📁 Сбор статических файлов..."
docker compose -f docker-compose.staging.yml exec -T web python manage.py collectstatic --noinput

echo "✅ Деплой завершен!"
echo "📊 Статус контейнеров:"
docker compose -f docker-compose.staging.yml ps
EOF

chmod +x /opt/crm-staging/deploy.sh
```

**Использование:**

```bash
/opt/crm-staging/deploy.sh
```

Или вручную:

```bash
cd /opt/crm-staging
git pull
docker compose -f docker-compose.staging.yml build
docker compose -f docker-compose.staging.yml up -d
docker compose -f docker-compose.staging.yml exec -T web python manage.py migrate
docker compose -f docker-compose.staging.yml exec -T web python manage.py collectstatic --noinput
```

---

## G) Troubleshooting

### Проверка контейнеров

```bash
# Список контейнеров
docker compose -f docker-compose.staging.yml ps

# Логи всех сервисов
docker compose -f docker-compose.staging.yml logs -f

# Логи конкретного сервиса
docker compose -f docker-compose.staging.yml logs -f web
docker compose -f docker-compose.staging.yml logs -f nginx
docker compose -f docker-compose.staging.yml logs -f celery
```

### Проверка health check

```bash
# Health check endpoint
curl http://95.142.47.245/health/

# Должен вернуть JSON с статусом "ok"
```

### Проверка Nginx

```bash
# Проверка конфигурации Nginx
docker compose -f docker-compose.staging.yml exec nginx nginx -t

# Логи Nginx
docker compose -f docker-compose.staging.yml logs nginx
```

### Проверка базы данных

```bash
# Подключение к PostgreSQL
docker compose -f docker-compose.staging.yml exec db psql -U crm_staging -d crm_staging

# Список таблиц
\dt

# Выход
\q
```

### Проверка Redis

```bash
# Подключение к Redis
docker compose -f docker-compose.staging.yml exec redis redis-cli

# Проверка ping
PING

# Выход
exit
```

### Проверка Celery

```bash
# Логи Celery worker
docker compose -f docker-compose.staging.yml logs celery

# Логи Celery beat
docker compose -f docker-compose.staging.yml logs celery-beat

# Проверка активных задач
docker compose -f docker-compose.staging.yml exec celery celery -A crm inspect active
```

### Перезапуск сервисов

```bash
# Перезапуск всех сервисов
docker compose -f docker-compose.staging.yml restart

# Перезапуск конкретного сервиса
docker compose -f docker-compose.staging.yml restart web
docker compose -f docker-compose.staging.yml restart celery
```

### Очистка и пересборка

```bash
# Остановка и удаление контейнеров
docker compose -f docker-compose.staging.yml down

# Удаление volumes (ОСТОРОЖНО: удалит данные БД!)
docker compose -f docker-compose.staging.yml down -v

# Пересборка без кэша
docker compose -f docker-compose.staging.yml build --no-cache
docker compose -f docker-compose.staging.yml up -d
```

---

## H) Настройка для отключения реальной отправки писем

В staging окружении важно не отправлять реальные письма. Для этого:

1. **Вариант 1: Console backend (письма в логи)**

Добавьте в `backend/crm/settings.py` (или создайте `backend/crm/settings_staging.py`):

```python
# Для staging: письма в консоль (логи)
if os.getenv("DJANGO_ENV") == "staging":
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
```

И добавьте в `.env.staging`:
```
DJANGO_ENV=staging
```

2. **Вариант 2: Отключить Celery beat задачу отправки писем**

В `.env.staging` можно установить флаг для отключения периодических задач:
```
CELERY_BEAT_ENABLED=0
```

Или просто не запускать `celery-beat` контейнер в staging.

---

## I) Добавление домена и SSL (опционально, для будущего)

Когда будет готов домен:

1. **Установка Certbot (Let's Encrypt)**

```bash
apt install -y certbot python3-certbot-nginx
```

2. **Обновление nginx/staging.conf**

Замените `server_name 95.142.47.245;` на `server_name staging.example.com;`

3. **Получение SSL сертификата**

```bash
certbot --nginx -d staging.example.com
```

4. **Автоматическое обновление сертификата**

```bash
certbot renew --dry-run
```

Certbot автоматически обновит конфигурацию Nginx для HTTPS.

---

## J) Мониторинг и логи

### Просмотр логов в реальном времени

```bash
# Все сервисы
docker compose -f docker-compose.staging.yml logs -f

# Только Django
docker compose -f docker-compose.staging.yml logs -f web

# Только Nginx
docker compose -f docker-compose.staging.yml logs -f nginx
```

### Ротация логов Docker

Docker по умолчанию ротирует логи. Настройки в `/etc/docker/daemon.json`:

```json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
```

После изменения:
```bash
systemctl restart docker
```

---

## Готово! 🎉

Теперь staging окружение доступно по адресу: **http://95.142.47.245**

Для деплоя новых версий используйте:
```bash
/opt/crm-staging/deploy.sh
```
