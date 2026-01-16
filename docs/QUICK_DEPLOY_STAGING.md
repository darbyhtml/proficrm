# Быстрый деплой на staging

## Команды для выполнения на staging сервере

```bash
# 1. Подключиться к серверу
ssh user@staging-server

# 2. Перейти в директорию проекта
cd /path/to/crm  # замените на актуальный путь

# 3. Обновить код
git pull origin main

# 4. Применить миграции
cd backend
source venv/bin/activate  # если используется venv
python manage.py migrate phonebridge

# 5. Собрать статические файлы
python manage.py collectstatic --noinput

# 6. Перезапустить сервисы
sudo systemctl restart gunicorn
sudo systemctl restart celery  # если используется

# 7. Проверить статус
sudo systemctl status gunicorn
```

## Проверка после деплоя

```bash
# Проверить API
curl -X POST http://localhost:8000/api/phone/calls/update/ \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"call_request_id": "<uuid>", "call_status": "connected"}'

# Запустить тесты
cd backend
python manage.py test phonebridge.tests.UpdateCallInfoViewTest.test_legacy_payload_acceptance
```

## Откат (если нужно)

```bash
# Откат миграции
python manage.py migrate phonebridge 0006_mobileappbuild_mobileappqrtoken

# Откат кода
git reset --hard HEAD~1
sudo systemctl restart gunicorn
```

**Подробная инструкция:** см. `docs/DEPLOY_STAGING_COMMANDS.md`
