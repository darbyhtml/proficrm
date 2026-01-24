# Оптимизация для работы на 4 GB RAM (NetAngels)

**Дата:** 2026-01-24  
**Текущая конфигурация:** 4 CPU / 4 GB RAM / 40 GB NVMe  
**Целевая нагрузка:** 30-35 одновременных пользователей

---

## ⚠️ Критическое предупреждение

**4 GB RAM — это минимальная конфигурация для 30-35 пользователей!**

При такой конфигурации:
- ✅ Система будет работать, но с ограничениями
- ⚠️ Нет запаса для пиковых нагрузок
- ⚠️ Риск нехватки памяти при одновременной работе всех пользователей
- ⚠️ Медленная работа при сложных запросах (аналитика, поиск)

**Рекомендуется апгрейд до 8 GB RAM в ближайшее время!**

---

## 1. Оптимизация PostgreSQL для 4 GB RAM

### 1.1. Настройки postgresql.conf

```conf
# Критичные настройки для 4 GB RAM
shared_buffers = 1GB              # 25% от RAM (максимум!)
effective_cache_size = 2GB        # 50% от RAM
maintenance_work_mem = 256MB      # уменьшено для экономии
work_mem = 8MB                    # уменьшено (было бы 16MB для 8GB)
max_connections = 50              # уменьшено (было бы 100 для 8GB)
checkpoint_completion_target = 0.9
wal_buffers = 16MB
default_statistics_target = 100
random_page_cost = 1.1            # для SSD
effective_io_concurrency = 200    # для SSD
```

### 1.2. Применение настроек

```bash
# Найти файл конфигурации PostgreSQL
sudo -u postgres psql -c "SHOW config_file;"

# Отредактировать файл (обычно /etc/postgresql/16/main/postgresql.conf)
sudo nano /etc/postgresql/16/main/postgresql.conf

# Перезапустить PostgreSQL
sudo systemctl restart postgresql
```

### 1.3. Проверка использования памяти

```sql
-- Проверить текущие настройки
SHOW shared_buffers;
SHOW effective_cache_size;
SHOW max_connections;

-- Проверить использование памяти
SELECT 
    name,
    setting,
    unit,
    source
FROM pg_settings
WHERE name IN ('shared_buffers', 'effective_cache_size', 'max_connections', 'work_mem');
```

---

## 2. Оптимизация Redis для 4 GB RAM

### 2.1. Настройки redis.conf

```conf
# Критичные настройки для 4 GB RAM
maxmemory 300mb                   # 7.5% от RAM (было бы 1GB для 8GB)
maxmemory-policy allkeys-lru      # вытеснение старых ключей
save ""                           # отключить автоматическое сохранение (если не критично)
```

### 2.2. Применение настроек

```bash
# Найти файл конфигурации Redis
redis-cli CONFIG GET dir

# Отредактировать файл (обычно /etc/redis/redis.conf)
sudo nano /etc/redis/redis.conf

# Перезапустить Redis
sudo systemctl restart redis
```

### 2.3. Проверка использования памяти

```bash
# Проверить использование памяти Redis
redis-cli INFO memory

# Проверить текущие настройки
redis-cli CONFIG GET maxmemory
redis-cli CONFIG GET maxmemory-policy
```

---

## 3. Оптимизация Gunicorn

### 3.1. Текущая конфигурация (правильная для 4 GB RAM)

```bash
# docker-compose.staging.yml
gunicorn crm.wsgi:application --bind 0.0.0.0:8000 --workers 2 --timeout 120
```

**⚠️ НЕ УВЕЛИЧИВАТЬ workers!** 2 workers — это максимум для 4 GB RAM.

### 3.2. Альтернатива: использование threads (опционально)

Если нужно больше параллелизма, можно использовать threads вместо workers:

```bash
# Использовать 2 workers с 2 threads каждый = 4 параллельных запроса
gunicorn crm.wsgi:application --bind 0.0.0.0:8000 --workers 2 --threads 2 --timeout 120
```

**Внимание:** threads подходят для I/O-bound операций (polling), но не для CPU-bound.

---

## 4. Оптимизация Celery

### 4.1. Текущая конфигурация (правильная для 4 GB RAM)

```bash
# docker-compose.staging.yml
celery -A crm worker --loglevel=info --concurrency=2
```

**⚠️ НЕ УВЕЛИЧИВАТЬ concurrency!** 2 — это максимум для 4 GB RAM.

---

## 5. Мониторинг использования ресурсов

### 5.1. Мониторинг RAM

```bash
# Проверить использование RAM
free -h

# Мониторинг в реальном времени
watch -n 1 free -h

# Проверить использование RAM процессами
ps aux --sort=-%mem | head -20
```

### 5.2. Мониторинг диска

```bash
# Проверить использование диска
df -h

# Проверить размер директорий
du -sh /var/lib/postgresql/*  # PostgreSQL данные
du -sh /var/lib/redis/*       # Redis данные
du -sh /app/backend/media/*   # Медиафайлы
du -sh /app/backend/logs/*   # Логи
```

### 5.3. Мониторинг PostgreSQL

```sql
-- Проверить активные соединения
SELECT count(*) FROM pg_stat_activity;

-- Проверить использование памяти PostgreSQL
SELECT 
    name,
    setting,
    unit
FROM pg_settings
WHERE name LIKE '%memory%' OR name LIKE '%cache%';
```

### 5.4. Настройка алертов

Создайте скрипт для мониторинга и отправки алертов:

```bash
#!/bin/bash
# /usr/local/bin/check_resources.sh

RAM_USAGE=$(free | grep Mem | awk '{printf "%.0f", $3/$2 * 100}')
DISK_USAGE=$(df -h / | awk 'NR==2 {print $5}' | sed 's/%//')

if [ $RAM_USAGE -gt 90 ]; then
    echo "⚠️ КРИТИЧНО: Использование RAM: ${RAM_USAGE}%"
    # Отправить уведомление (email, telegram, etc.)
fi

if [ $DISK_USAGE -gt 80 ]; then
    echo "⚠️ ВНИМАНИЕ: Использование диска: ${DISK_USAGE}%"
    # Отправить уведомление
fi
```

Добавьте в crontab:

```bash
# Проверка каждые 5 минут
*/5 * * * * /usr/local/bin/check_resources.sh
```

---

## 6. Очистка и оптимизация диска

### 6.1. Ротация логов

```bash
# Настроить logrotate для Django логов
sudo nano /etc/logrotate.d/django

# Содержимое:
/app/backend/logs/*.log {
    daily
    rotate 7
    compress
    delaycompress
    notifempty
    missingok
    create 0640 crmuser crmuser
}
```

### 6.2. Очистка старых данных PostgreSQL

```sql
-- Очистка старых записей (пример для audit логов)
DELETE FROM audit_activityevent 
WHERE created_at < NOW() - INTERVAL '90 days';

-- Вакуум для освобождения места
VACUUM FULL;
```

### 6.3. Очистка старых медиафайлов

```bash
# Найти старые файлы (старше 90 дней)
find /app/backend/media -type f -mtime +90 -ls

# Удалить старые файлы (осторожно!)
# find /app/backend/media -type f -mtime +90 -delete
```

---

## 7. Оптимизация Django

### 7.1. Кэширование

Убедитесь, что кэширование настроено правильно:

```python
# backend/crm/settings.py
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': os.getenv('REDIS_URL', 'redis://127.0.0.1:6379/0'),
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            'MAX_ENTRIES': 10000,  # Ограничить количество ключей
            'CULL_FREQUENCY': 3,   # Удалять 1/3 старых ключей при превышении
        },
        'KEY_PREFIX': 'crm',
        'TIMEOUT': 300,  # 5 минут по умолчанию
    }
}
```

### 7.2. Оптимизация запросов

Используйте `select_related` и `prefetch_related` для уменьшения количества запросов:

```python
# Пример оптимизации
companies = Company.objects.select_related('responsible', 'branch').prefetch_related('phones', 'emails')
```

---

## 8. План апгрейда NetAngels

### 8.1. Вариант 1: Минимальный апгрейд (рекомендуется)

**Текущая конфигурация:**
- CPU: 4 ядра
- RAM: 4 GB
- Диск: 40 GB NVMe
- Стоимость: 1952.93 руб/мес

**Апгрейд до:**
- CPU: 4 ядра (без изменений)
- RAM: 8 GB (+~500-800 руб/мес)
- Диск: 80-100 GB NVMe (+~200-400 руб/мес)
- **Итого:** ~2650-3150 руб/мес (+~700-1200 руб/мес)

**Преимущества:**
- ✅ Достаточно RAM для комфортной работы
- ✅ Больше места для данных и логов
- ✅ Запас для роста нагрузки

### 8.2. Вариант 2: Оптимальный апгрейд

**Апгрейд до:**
- CPU: 6-8 ядер (+~500-1000 руб/мес)
- RAM: 16 GB (+~1000-1500 руб/мес)
- Диск: 200 GB NVMe (+~400-600 руб/мес)
- **Итого:** ~3850-5050 руб/мес (+~1900-3100 руб/мес)

**Преимущества:**
- ✅ Отличная производительность
- ✅ Большой запас для роста
- ✅ Возможность увеличить workers и concurrency

---

## 9. Чек-лист оптимизации

### Немедленно (для работы на 4 GB RAM):

- [ ] Оптимизировать PostgreSQL настройки (shared_buffers = 1GB, effective_cache_size = 2GB)
- [ ] Настроить Redis maxmemory = 300mb
- [ ] Оставить Gunicorn workers = 2 (не увеличивать!)
- [ ] Оставить Celery concurrency = 2 (не увеличивать!)
- [ ] Настроить мониторинг RAM и диска
- [ ] Настроить ротацию логов
- [ ] Настроить алерты при использовании RAM > 90%

### В ближайшее время (рекомендуется):

- [ ] Апгрейд RAM до 8 GB (критично!)
- [ ] Апгрейд диска до 80-100 GB
- [ ] Настроить автоматическое резервное копирование
- [ ] Настроить мониторинг производительности

### В перспективе (при росте нагрузки):

- [ ] Апгрейд до 16 GB RAM
- [ ] Апгрейд диска до 200 GB
- [ ] Увеличение workers до 4-6
- [ ] Увеличение Celery concurrency до 4-6

---

## 10. Контакты для апгрейда NetAngels

Для апгрейда конфигурации на NetAngels:
1. Войдите в панель управления NetAngels
2. Перейдите в раздел "Управление сервером"
3. Выберите "Изменить тариф" или "Апгрейд ресурсов"
4. Выберите нужную конфигурацию
5. Обратитесь в поддержку, если нужна помощь

---

**Примечание:** Эти настройки оптимизированы для работы на 4 GB RAM, но рекомендуется апгрейд до 8 GB RAM для комфортной работы с 30-35 пользователями.
