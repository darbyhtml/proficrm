# Исправление предупреждения CORS (SEC-003)

## Проблема

В логах появляется предупреждение:
```
⚠️ SECURITY WARNING: CORS_ALLOWED_ORIGINS contains localhost origins in production: ['http://localhost:5173']
```

## Решение

### Вариант 1: Если фронтенд на том же домене (Django templates)

Если вы используете Django templates и фронтенд отдается с того же домена (`crm.groupprofi.ru`), то CORS не нужен. 

**Исправление:**
```bash
# В .env файле на сервере
CORS_ALLOWED_ORIGINS=
```

Или просто удалите эту строку из `.env` (будет использоваться пустое значение).

### Вариант 2: Если есть отдельный фронтенд на другом домене

Если у вас есть отдельный фронтенд (например, React/Vue приложение на другом домене), укажите правильный домен:

```bash
# В .env файле на сервере
CORS_ALLOWED_ORIGINS=https://app.groupprofi.ru,https://crm.groupprofi.ru
```

**Важно:** Укажите только production домены, без `localhost` или `127.0.0.1`.

### Вариант 3: Если CORS вообще не используется

Если вы не используете CORS (нет отдельного фронтенда), можно отключить CORS middleware:

1. Удалите `'corsheaders.middleware.CorsMiddleware'` из `MIDDLEWARE` в `settings.py`
2. Удалите `'corsheaders'` из `INSTALLED_APPS`
3. Удалите `CORS_ALLOWED_ORIGINS` из `.env`

Но это требует изменения кода, поэтому проще просто оставить `CORS_ALLOWED_ORIGINS=` пустым.

---

## Быстрое исправление (рекомендуется)

Судя по тому, что это Django templates приложение, скорее всего CORS не нужен. 

**Выполните на сервере:**
```bash
cd /opt/proficrm
nano .env
```

Найдите строку:
```
CORS_ALLOWED_ORIGINS=http://localhost:5173
```

Измените на:
```
CORS_ALLOWED_ORIGINS=
```

Или удалите эту строку полностью.

Затем перезапустите:
```bash
docker-compose -f docker-compose.yml -f docker-compose.vds.yml restart web
```

Проверьте, что предупреждение исчезло:
```bash
docker-compose -f docker-compose.yml -f docker-compose.vds.yml logs web | grep -i "SECURITY WARNING"
```

---

## Проверка после исправления

После исправления предупреждение должно исчезнуть. Если фронтенд работает нормально, значит CORS не нужен и можно оставить пустым.

Если после исправления фронтенд перестанет работать (маловероятно для Django templates), значит нужно указать правильный домен в `CORS_ALLOWED_ORIGINS`.
