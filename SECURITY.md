# Руководство по безопасности CRM ПРОФИ

## Реализованные меры защиты

### 1. Защита от DDoS атак и перегрузки API

- **Rate Limiting Middleware** (`accounts.middleware.RateLimitMiddleware`)
  - Ограничение запросов по IP адресу
  - Для логина: 5 попыток в минуту
  - Для API: 60 запросов в минуту
  - Для строгих путей (логин, токены): 10 запросов в минуту

### 2. Защита от взлома аккаунтов (брутфорс)

- **Блокировка аккаунта после неудачных попыток**
  - Максимум 5 неудачных попыток входа
  - Блокировка на 15 минут после превышения лимита
  - Автоматическая разблокировка после истечения времени

- **Логирование всех попыток входа**
  - Успешные и неудачные попытки логируются в `ActivityEvent`
  - Записывается IP адрес, время, причина неудачи

- **Защита для веб-интерфейса и API**
  - Веб-логин: `SecureLoginView`
  - JWT API: `SecureTokenObtainPairView`

### 3. Защита от утечки информации

- **Security Headers**
  - `X-Frame-Options: DENY` - защита от clickjacking
  - `X-Content-Type-Options: nosniff` - защита от MIME sniffing
  - `X-XSS-Protection` - защита от XSS
  - `Referrer-Policy: strict-origin-when-cross-origin`

- **Скрытие деталей ошибок в production**
  - Кастомный обработчик исключений для DRF
  - Общие сообщения об ошибках без технических деталей

- **HTTPS и безопасные cookies**
  - `SESSION_COOKIE_SECURE = True` (в production)
  - `CSRF_COOKIE_SECURE = True` (в production)
  - HSTS заголовки

- **Защита от индексации поисковыми системами**
  - Meta-теги `noindex, nofollow` во всех HTML страницах
  - `robots.txt` с полным запретом индексации (`Disallow: /`)
  - Защита конфиденциальных данных от попадания в поисковики

### 4. Усиленная валидация паролей

- Минимум 8 символов
- Проверка на схожесть с именем пользователя
- Проверка на распространенные пароли
- Проверка на чисто числовые пароли

### 5. Безопасность сессий

- `SESSION_COOKIE_HTTPONLY = True` - защита от XSS
- `SESSION_COOKIE_SAMESITE = 'Lax'` - защита от CSRF
- Время жизни сессии: 24 часа

## Настройка на VDS

### 1. Настройка кеша (для rate limiting)

Для production рекомендуется использовать Redis:

```python
# В backend/crm/settings.py
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": "redis://127.0.0.1:6379/1",
    }
}
```

Или Memcached:

```python
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.memcached.PyMemcacheCache",
        "LOCATION": "127.0.0.1:11211",
    }
}
```

### 2. Настройка переменных окружения

В `.env` на VDS убедитесь, что установлены:

```bash
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=<сильный ключ 50+ символов>
DJANGO_ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com
DJANGO_SECURE_SSL_REDIRECT=1
DJANGO_SESSION_COOKIE_SECURE=1
DJANGO_CSRF_COOKIE_SECURE=1
```

### 3. Настройка Nginx (рекомендуется)

Добавьте в конфигурацию Nginx:

```nginx
# Rate limiting
limit_req_zone $binary_remote_addr zone=general:10m rate=10r/s;
limit_req_zone $binary_remote_addr zone=login:10m rate=5r/m;

server {
    # ...
    
    # Rate limiting для логина
    location /login/ {
        limit_req zone=login burst=2 nodelay;
        proxy_pass http://127.0.0.1:8001;
    }
    
    # Rate limiting для остальных запросов
    location / {
        limit_req zone=general burst=20 nodelay;
        proxy_pass http://127.0.0.1:8001;
    }
    
    # Security headers
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
}
```

## Мониторинг безопасности

Все события безопасности логируются в `ActivityEvent` с типом `security`:

- `login_success` - успешный вход
- `login_failed` - неудачная попытка входа
- `account_locked` - блокировка аккаунта
- `jwt_login_success` - успешный вход через JWT API

Проверить логи можно в админке Django или через:

```python
from audit.models import ActivityEvent
ActivityEvent.objects.filter(entity_type="security").order_by("-created_at")
```

### 6. Content Security Policy (CSP)

- **Защита от XSS атак**
  - Строгие правила для загрузки скриптов и стилей
  - Разрешены только доверенные источники (self, Tailwind CDN)
  - Блокировка встраивания в iframe (`frame-ancestors 'none'`)

### 7. Security.txt

- **Ответственное раскрытие уязвимостей**
  - Файл `.well-known/security.txt` с контактами для сообщений об уязвимостях
  - Доступен по адресу `https://ваш-домен/.well-known/security.txt`

### 8. Валидация файлов

- **Проверка расширений и MIME типов**
  - Валидация по расширению файла
  - Проверка реального MIME типа по содержимому файла
  - Защита от подделки расширений (например, .exe под видом .pdf)

### 9. Защита медиа файлов

- **Требование аутентификации**
  - Все медиа файлы доступны только авторизованным пользователям
  - Защита от прямого доступа к файлам по URL

## Рекомендации

1. **Регулярно проверяйте логи** на подозрительную активность
2. **Используйте сильные пароли** для всех пользователей
3. **Настройте мониторинг** для отслеживания множественных неудачных попыток
4. **Обновляйте зависимости** регулярно
5. **Используйте HTTPS** везде в production
6. **Настройте бэкапы** базы данных регулярно
7. **Настройте SECURITY_CONTACT_EMAIL** в `.env` для security.txt
8. **Проверяйте CSP заголовки** в браузере (F12 → Network → Headers)

