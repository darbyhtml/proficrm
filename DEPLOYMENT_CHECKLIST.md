# Чеклист для проверки критичных улучшений

## ✅ Что было реализовано

### 1. Redis для кеширования и Celery
- ✅ Добавлен сервис Redis в `docker-compose.yml` (используется и на VDS)
- ✅ Настроен Redis для кеширования (через `CACHES` в `settings.py`)
- ✅ Настроен Celery для фоновых задач (рассылки, очистка звонков)
- ✅ Настроен Celery Beat для периодических задач (каждую минуту/час)

### 2. Фоновые задачи (Celery)
- ✅ Перенесен `mailer_worker` в Celery task (`mailer.tasks.send_pending_emails`)
- ✅ Добавлена задача очистки старых CallRequest (`phonebridge.tasks.clean_old_call_requests`)
- ✅ Настроен Celery Beat schedule:
  - Отправка писем: каждую минуту
  - Очистка старых запросов: каждый час

### 3. Мониторинг
- ✅ Добавлен health check endpoint: `/health/`
- ✅ Проверяет доступность БД, Redis и Celery

### 4. Логирование
- ✅ Настроено структурированное логирование (JSON в production)
- ✅ Логи пишутся в `backend/logs/crm.log` (ротация 10MB, 5 файлов)

---

## 🔍 Что нужно проверить после деплоя

### 1. Проверка Docker контейнеров

```bash
# Проверить, что все контейнеры запущены
docker-compose -f docker-compose.yml -f docker-compose.vds.yml ps

# Должны быть запущены:
# - db (PostgreSQL)
# - redis (Redis)
# - web (Django)
# - celery (Celery worker)
# - celery-beat (Celery Beat scheduler)
```

**Ожидаемый результат:** Все 5 контейнеров должны быть в статусе "Up"

---

### 2. Проверка Redis

```bash
# Проверить подключение к Redis
docker-compose -f docker-compose.yml -f docker-compose.vds.yml exec redis redis-cli ping

# Должен вернуть: PONG
```

**Ожидаемый результат:** `PONG`

---

### 3. Проверка Celery Worker

```bash
# Проверить логи Celery worker
docker-compose -f docker-compose.yml -f docker-compose.vds.yml logs celery --tail=50

# Должны быть сообщения:
# - "celery@... ready"
# - "Connected to redis://redis:6379/1"
```

**Ожидаемый результат:** Worker подключен к Redis и готов к работе

---

### 4. Проверка Celery Beat

```bash
# Проверить логи Celery Beat
docker-compose -f docker-compose.yml -f docker-compose.vds.yml logs celery-beat --tail=50

# Должны быть сообщения:
# - "beat: Starting..."
# - "DatabaseScheduler: Schedule changed"
```

**Ожидаемый результат:** Beat scheduler запущен и видит расписание задач

---

### 5. Проверка Health Check

```bash
# Проверить health check endpoint
curl https://crm.groupprofi.ru/health/

# Или через браузер:
# https://crm.groupprofi.ru/health/
```

**Ожидаемый результат:**
```json
{
  "status": "ok",
  "checks": {
    "database": "ok",
    "cache": "ok",
    "celery": "ok"
  }
}
```

Если какой-то компонент недоступен, статус будет `"degraded"` или `503`.

---

### 6. Проверка кеширования (Redis)

```bash
# В Django shell проверить работу кеша
docker-compose -f docker-compose.yml -f docker-compose.vds.yml exec web python manage.py shell

# В shell:
from django.core.cache import cache
cache.set('test_key', 'test_value', 60)
cache.get('test_key')  # Должно вернуть 'test_value'
```

**Ожидаемый результат:** Кеш работает, значения сохраняются и читаются

---

### 7. Проверка отправки писем через Celery

1. Создайте тестовую кампанию с получателями
2. Проверьте логи Celery worker:

```bash
docker-compose -f docker-compose.yml -f docker-compose.vds.yml logs celery --tail=100 | grep "send_pending_emails"
```

**Ожидаемый результат:** Видны логи обработки задач отправки писем

---

### 8. Проверка логирования

```bash
# Проверить наличие логов
docker-compose -f docker-compose.yml -f docker-compose.vds.yml exec web ls -lh backend/logs/

# Должен быть файл crm.log
```

**Ожидаемый результат:** Файл `crm.log` существует и содержит логи

---

## ⚠️ Возможные проблемы и решения

### Проблема: Redis не запускается

**Решение:**
```bash
# Проверить логи Redis
docker-compose -f docker-compose.yml -f docker-compose.vds.yml logs redis

# Пересоздать контейнеры
docker-compose -f docker-compose.yml -f docker-compose.vds.yml down
docker-compose -f docker-compose.yml -f docker-compose.vds.yml up -d --build
```

---

### Проблема: Celery worker не подключается к Redis

**Решение:**
1. Проверить переменные окружения:
   ```bash
   docker-compose -f docker-compose.yml -f docker-compose.vds.yml exec celery env | grep REDIS
   docker-compose -f docker-compose.yml -f docker-compose.vds.yml exec celery env | grep CELERY
   ```

2. Убедиться, что Redis доступен:
   ```bash
   docker-compose -f docker-compose.yml -f docker-compose.vds.yml exec celery ping redis
   ```

---

### Проблема: Health check показывает ошибки

**Решение:**
- Если `database: error` - проверить подключение к PostgreSQL
- Если `cache: error` - проверить Redis
- Если `celery: warning` - проверить, что Celery worker запущен

---

### Проблема: Письма не отправляются

**Решение:**
1. Проверить, что Celery Beat запущен (задачи должны запускаться каждую минуту)
2. Проверить логи Celery worker на наличие ошибок
3. Проверить настройки SMTP в CRM

---

## 📝 Команды для быстрой проверки

```bash
# Полная проверка всех сервисов
docker-compose -f docker-compose.yml -f docker-compose.vds.yml ps
docker-compose -f docker-compose.yml -f docker-compose.vds.yml exec redis redis-cli ping
curl https://crm.groupprofi.ru/health/

# Проверка логов
docker-compose -f docker-compose.yml -f docker-compose.vds.yml logs celery --tail=20
docker-compose -f docker-compose.yml -f docker-compose.vds.yml logs celery-beat --tail=20
docker-compose -f docker-compose.yml -f docker-compose.vds.yml logs web --tail=20
```

---

## ✅ Итоговый чеклист

- [ ] Все 5 контейнеров запущены (db, redis, web, celery, celery-beat)
- [ ] Redis отвечает на ping (PONG)
- [ ] Health check возвращает `{"status": "ok"}`
- [ ] Celery worker подключен к Redis
- [ ] Celery Beat видит расписание задач
- [ ] Кеширование работает (Redis)
- [ ] Логи пишутся в файл
- [ ] Письма отправляются через Celery (проверить через тестовую кампанию)

---

## 🚀 После успешной проверки

Все критичные улучшения работают! Система готова к production использованию с:
- ✅ Фоновыми задачами через Celery
- ✅ Redis кешированием
- ✅ Мониторингом через health check
- ✅ Структурированным логированием

