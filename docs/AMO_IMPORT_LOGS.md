# Просмотр логов импорта AMO CRM в реальном времени

## Основные команды

### 1. Просмотр всех логов веб-контейнера в реальном времени

```bash
docker compose logs -f web
```

Эта команда показывает все логи контейнера `web` и обновляет их в реальном времени (флаг `-f` означает "follow").

### 2. Просмотр только последних N строк и затем следить за обновлениями

```bash
docker compose logs --tail=100 -f web
```

Показывает последние 100 строк и затем продолжает выводить новые логи.

### 3. Фильтрация логов по ключевым словам

#### Фильтрация по "migrate" (основные логи импорта):

```bash
docker compose logs -f web | grep -i "migrate"
```

#### Фильтрация по "contact" (логи контактов):

```bash
docker compose logs -f web | grep -i "contact"
```

#### Фильтрация по "company" (логи компаний):

```bash
docker compose logs -f web | grep -i "company"
```

#### Комбинированная фильтрация (несколько ключевых слов):

```bash
docker compose logs -f web | grep -E "(migrate|contact|company|bulk|created|updated)"
```

### 4. Просмотр логов с временными метками

```bash
docker compose logs -f --timestamps web
```

Показывает время каждого сообщения в логах.

### 5. Просмотр логов за последний час

```bash
docker compose logs --since 1h -f web
```

### 6. Просмотр логов с фильтрацией по уровню (только ошибки)

```bash
docker compose logs -f web | grep -i "error\|exception\|failed"
```

## Рекомендуемая команда для мониторинга импорта

Для удобного мониторинга импорта контактов используйте:

```bash
docker compose logs --tail=200 -f --timestamps web | grep -E "(migrate_filtered|contact|bulk|created|updated|skipped|error)"
```

Эта команда:
- Показывает последние 200 строк
- Обновляет логи в реальном времени
- Показывает временные метки
- Фильтрует только важные сообщения об импорте

## Просмотр логов в отдельном терминале

### Вариант 1: Использовать два терминала

**Терминал 1** - запуск импорта через веб-интерфейс:
- Откройте браузер и запустите импорт

**Терминал 2** - просмотр логов:
```bash
docker compose logs -f web
```

### Вариант 2: Запуск в фоне с перенаправлением в файл

```bash
# Запуск импорта и сохранение логов в файл
docker compose logs -f web > import_logs_$(date +%Y%m%d_%H%M%S).txt 2>&1 &

# Просмотр файла в реальном времени
tail -f import_logs_*.txt
```

## Просмотр логов через Docker Desktop

Если используете Docker Desktop:
1. Откройте Docker Desktop
2. Перейдите в раздел "Containers"
3. Найдите контейнер `web` (или `crm-web-1`)
4. Нажмите на контейнер
5. Перейдите на вкладку "Logs"
6. Логи будут обновляться автоматически

## Полезные ключевые слова для фильтрации

- `migrate_filtered` - основные логи импорта
- `bulk` - операции массового создания/обновления
- `created` - созданные записи
- `updated` - обновленные записи
- `skipped` - пропущенные записи
- `contact` - логи контактов
- `company` - логи компаний
- `phone` - логи телефонов
- `email` - логи email
- `error` - ошибки
- `warning` - предупреждения
- `INFO` - информационные сообщения
- `DEBUG` - отладочные сообщения

## Пример вывода логов при импорте контактов

```
2026-01-22T10:30:15.123Z migrate_filtered: ===== НАЧАЛО ИМПОРТА КОНТАКТОВ для 50 компаний =====
2026-01-22T10:30:16.456Z migrate_filtered: получено 150 контактов из API для 50 компаний (bulk-метод)
2026-01-22T10:30:17.789Z migrate_filtered: обработка контакта 1/150 (processed: 1, skipped: 0, errors: 0)
2026-01-22T10:30:18.012Z migrate_filtered: created 5 new contacts
2026-01-22T10:30:19.345Z migrate_filtered: updated 10 existing contacts, skipped 135 without changes
2026-01-22T10:30:20.678Z migrate_filtered: ===== ИМПОРТ КОНТАКТОВ ЗАВЕРШЕН: created=15, seen=150, processed=150, skipped=0, errors=0 =====
```

## Увеличение уровня детализации логов

Если нужно больше деталей, можно временно изменить уровень логирования в Django settings:

```python
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'loggers': {
        'amocrm': {
            'handlers': ['console'],
            'level': 'DEBUG',  # Изменить с INFO на DEBUG
            'propagate': False,
        },
    },
}
```

После изменения перезапустите контейнер:
```bash
docker compose restart web
```

## Очистка старых логов

Если логи занимают много места:

```bash
# Очистка логов Docker
docker compose logs --tail=0 web > /dev/null 2>&1

# Или через Docker system prune (осторожно - удалит все неиспользуемые данные)
docker system prune -f
```
