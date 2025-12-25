# Инструкция по настройке безопасности на VDS

## Шаг 1: Подключитесь к VDS

```bash
ssh user@your-vds-ip
cd /opt/proficrm  # или путь к вашему проекту
```

## Шаг 2: Обновите код

```bash
git pull
```

## Шаг 3: Проверьте/настройте .env файл

Убедитесь, что в `backend/.env` установлены правильные значения:

```bash
cd backend
nano .env  # или используйте ваш редактор
```

**Обязательные настройки для production:**

```bash
# Безопасность
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=<сгенерируйте сильный ключ 50+ символов>
DJANGO_ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com
DJANGO_SECURE_SSL_REDIRECT=1
DJANGO_SESSION_COOKIE_SECURE=1
DJANGO_CSRF_COOKIE_SECURE=1
DJANGO_CSRF_TRUSTED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com

# База данных (если используете PostgreSQL)
DB_ENGINE=postgres
POSTGRES_DB=crm
POSTGRES_USER=crm
POSTGRES_PASSWORD=<сильный пароль>
POSTGRES_HOST=db
POSTGRES_PORT=5432
```

**Генерация SECRET_KEY:**

```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

## Шаг 4: Настройте кеш (рекомендуется Redis)

### Вариант A: Redis (рекомендуется)

Установите Redis:

```bash
sudo apt update
sudo apt install redis-server -y
sudo systemctl enable redis-server
sudo systemctl start redis-server
```

Добавьте в `docker-compose.yml`:

```yaml
services:
  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data
    command: redis-server --appendonly yes

  web:
    # ... существующие настройки ...
    depends_on:
      - db
      - redis
    environment:
      # ... существующие переменные ...
      REDIS_HOST: redis
      REDIS_PORT: 6379

volumes:
  pgdata:
  media:
  redis_data:
```

Обновите `backend/crm/settings.py` (если нужно):

```python
# В settings.py добавьте после DATABASES
if os.getenv("REDIS_HOST"):
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": f"redis://{os.getenv('REDIS_HOST', 'redis')}:{os.getenv('REDIS_PORT', '6379')}/1",
        }
    }
```

### Вариант B: Оставить LocMemCache (для тестирования)

Если не хотите настраивать Redis сейчас, можно оставить LocMemCache (но он не работает между процессами).

## Шаг 5: Примените изменения

```bash
# Вернитесь в корень проекта
cd /opt/proficrm

# Примените миграции
docker compose -f docker-compose.yml -f docker-compose.vds.yml exec web python manage.py migrate

# Соберите статические файлы
docker compose -f docker-compose.yml -f docker-compose.vds.yml exec web python manage.py collectstatic --noinput

# Перезапустите контейнеры
docker compose -f docker-compose.yml -f docker-compose.vds.yml up -d --build
```

## Шаг 6: Проверьте работу

```bash
# Проверьте логи
docker compose -f docker-compose.yml -f docker-compose.vds.yml logs -f web

# Проверьте, что приложение работает
curl -I https://yourdomain.com
```

## Шаг 7: Настройте Nginx (рекомендуется)

Добавьте в конфигурацию Nginx rate limiting:

```nginx
# В /etc/nginx/nginx.conf или в конфиге сайта
limit_req_zone $binary_remote_addr zone=general:10m rate=10r/s;
limit_req_zone $binary_remote_addr zone=login:10m rate=5r/m;

server {
    listen 80;
    server_name yourdomain.com www.yourdomain.com;
    
    # Редирект на HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name yourdomain.com www.yourdomain.com;
    
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    
    # Security headers
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Strict-Transport-Security "max-age=3600" always;
    
    # Rate limiting для логина
    location /login/ {
        limit_req zone=login burst=2 nodelay;
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    
    # Rate limiting для остальных запросов
    location / {
        limit_req zone=general burst=20 nodelay;
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    
    # Статические файлы
    location /static/ {
        alias /opt/proficrm/backend/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
    
    # Медиа файлы
    location /media/ {
        alias /opt/proficrm/backend/media/;
    }
}
```

После изменений перезагрузите Nginx:

```bash
sudo nginx -t  # Проверка конфигурации
sudo systemctl reload nginx
```

## Проверка безопасности

После настройки проверьте:

1. **Попробуйте войти с неверным паролем 6 раз** - аккаунт должен заблокироваться
2. **Попробуйте сделать много запросов быстро** - должен сработать rate limiting
3. **Проверьте логи безопасности:**

```bash
docker compose -f docker-compose.yml -f docker-compose.vds.yml exec web python manage.py shell
```

```python
from audit.models import ActivityEvent
ActivityEvent.objects.filter(entity_type="security").order_by("-created_at")[:10]
```

## Важные замечания

1. **НЕ коммитьте .env файл** - он должен быть в .gitignore
2. **Используйте сильные пароли** для базы данных и SECRET_KEY
3. **Регулярно проверяйте логи** на подозрительную активность
4. **Обновляйте зависимости** регулярно: `pip install -r requirements.txt --upgrade`

