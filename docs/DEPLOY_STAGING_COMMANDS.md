# Команды для деплоя на staging сервер

**Дата:** 2024-01-XX  
**Версия:** ЭТАП 0-6 (Call Analytics)

---

## Шаг 1: Подключение к серверу

```bash
# Подключиться к staging серверу
ssh user@staging-server

# Или если используется другой способ доступа
# (например, через Docker, через веб-интерфейс и т.д.)
```

---

## Шаг 2: Перейти в директорию проекта

```bash
# Перейти в директорию проекта (замените на актуальный путь)
cd /path/to/crm
# или
cd ~/crm
# или
cd /var/www/crm
```

---

## Шаг 3: Обновить код из репозитория

```bash
# Получить последние изменения
git pull origin main
# или
git pull origin master
# или
git pull origin develop  # если используется develop ветка
```

**Ожидаемый результат:**
```
Updating <old-commit>..<new-commit>
Fast-forward
 backend/phonebridge/tests_stats.py                    | 447 +++++++++++++++++++++
 backend/phonebridge/tests.py                          |  24 ++
 backend/ui/tests/__init__.py                          |   1 +
 backend/ui/tests/test_calls_stats_view.py             | 121 ++++++
 android/CRMProfiDialer/app/src/test/.../CallEventPayloadTest.kt | 121 ++++++
 ...
```

---

## Шаг 4: Применить миграции БД (если ещё не применены)

```bash
# Активировать виртуальное окружение (если используется)
source venv/bin/activate
# или
source .venv/bin/activate

# Применить миграции
cd backend
python manage.py migrate phonebridge

# Проверить, что миграция применена
python manage.py showmigrations phonebridge
```

**Ожидаемый результат:**
```
phonebridge
 [X] 0001_initial
 [X] 0002_...
 ...
 [X] 0007_add_call_analytics_fields  # Новая миграция должна быть отмечена [X]
```

---

## Шаг 5: Собрать статические файлы (если нужно)

```bash
cd backend
python manage.py collectstatic --noinput
```

---

## Шаг 6: Перезапустить сервисы

### Если используется systemd:

```bash
# Перезапустить Gunicorn
sudo systemctl restart gunicorn
# или
sudo systemctl restart crm-gunicorn

# Перезапустить Celery (если используется)
sudo systemctl restart celery
# или
sudo systemctl restart crm-celery

# Проверить статус
sudo systemctl status gunicorn
sudo systemctl status celery
```

### Если используется Docker:

```bash
# Перезапустить контейнеры
docker-compose restart backend
# или
docker-compose up -d --build backend

# Проверить логи
docker-compose logs -f backend
```

### Если используется supervisor:

```bash
# Перезапустить процессы
supervisorctl restart gunicorn
supervisorctl restart celery

# Проверить статус
supervisorctl status
```

---

## Шаг 7: Проверить, что всё работает

### 7.1. Проверить, что сервер отвечает

```bash
# Проверить, что Django запущен
curl http://localhost:8000/health
# или
curl http://staging-server:8000/health

# Проверить API endpoint
curl -X GET http://localhost:8000/api/phone/calls/pull/ \
  -H "Authorization: Bearer <test-token>"
```

### 7.2. Запустить тесты (опционально, но рекомендуется)

```bash
cd backend
python manage.py test phonebridge.tests phonebridge.tests_stats ui.tests.test_calls_stats_view --verbosity=2
```

**Ожидаемый результат:** Все тесты проходят

### 7.3. Проверить страницы UI

Открыть в браузере:
- `http://staging-server/settings/calls/stats/` — должна загружаться без ошибок
- `http://staging-server/analytics/users/<user_id>/` — должна загружаться без ошибок

---

## Шаг 8: Проверить логи (если есть ошибки)

```bash
# Django логи
tail -f /var/log/django/error.log
# или
tail -f /path/to/logs/django.log

# Gunicorn логи
tail -f /var/log/gunicorn/error.log
# или
journalctl -u gunicorn -f

# Nginx логи (если используется)
tail -f /var/log/nginx/error.log
```

---

## Быстрая команда (всё в одном)

Если у вас есть доступ к серверу и все пути известны:

```bash
# Подключиться к серверу
ssh user@staging-server

# Выполнить все команды
cd /path/to/crm && \
git pull origin main && \
cd backend && \
source venv/bin/activate && \
python manage.py migrate phonebridge && \
python manage.py collectstatic --noinput && \
sudo systemctl restart gunicorn && \
echo "✅ Деплой завершён"
```

---

## Откат (если что-то пошло не так)

### Откат миграции:

```bash
cd backend
python manage.py migrate phonebridge 0006_mobileappbuild_mobileappqrtoken
```

### Откат кода:

```bash
# Откатить к предыдущему коммиту
git reset --hard HEAD~1
# или
git checkout <previous-commit-hash>

# Перезапустить сервисы
sudo systemctl restart gunicorn
```

---

## Проверка после деплоя

### 1. API работает

```bash
# Legacy payload
curl -X POST http://staging-server/api/phone/calls/update/ \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "call_request_id": "<uuid>",
    "call_status": "connected",
    "call_started_at": "2024-01-15T14:30:00Z",
    "call_duration_seconds": 180
  }'
```

**Ожидаемый результат:** `200 OK`, `{"ok": true}`

### 2. UI работает

- Открыть `/settings/calls/stats/` — страница загружается
- Проверить, что новые поля отображаются (если есть данные)
- Проверить, что unknown статус показывается как "Не удалось определить"

### 3. Тесты проходят

```bash
cd backend
python manage.py test phonebridge.tests.UpdateCallInfoViewTest.test_legacy_payload_acceptance
```

**Ожидаемый результат:** `OK`

---

## Контакты для помощи

- **Backend:** [ответственный]
- **DevOps:** [ответственный]
- **Логи:** `/var/log/django/` или `/var/log/gunicorn/`

---

**Статус:** ✅ Готово к деплою
