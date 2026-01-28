# Инструкция по применению изменений на сервере

## Вариант 1: Использование готового скрипта деплоя (рекомендуется)

Если у вас настроен автоматический деплой через скрипты:

### Для Production (docker-compose.prod.yml):
```bash
cd /path/to/project
./deploy_production.sh
```

### Для быстрого деплоя исправлений:
```bash
cd /path/to/project
./deploy_crm_fixes.sh
```

---

## Вариант 2: Ручное применение (пошагово)

### Шаг 1: Подключитесь к серверу
```bash
ssh user@your-server
cd /path/to/project
```

### Шаг 2: Обновите код из репозитория
```bash
git pull origin main
```

### Шаг 3: Примените миграции базы данных

**Если используете Docker:**
```bash
# Для docker-compose.yml
docker compose exec web python manage.py migrate

# Или для docker-compose.prod.yml
docker compose -f docker-compose.prod.yml exec web python manage.py migrate
```

**Если без Docker (прямой запуск):**
```bash
cd backend
source venv/bin/activate  # или ваш способ активации виртуального окружения
python manage.py migrate
```

### Шаг 4: Соберите статические файлы (если нужно)

**Если используете Docker:**
```bash
docker compose exec web python manage.py collectstatic --noinput
```

**Если без Docker:**
```bash
python manage.py collectstatic --noinput
```

### Шаг 5: Перезапустите приложение

**Если используете Docker:**
```bash
# Перезапуск только web-сервиса
docker compose restart web

# Или полный перезапуск
docker compose down
docker compose up -d
```

**Если используете systemd (gunicorn/uwsgi):**
```bash
sudo systemctl restart crm  # или имя вашего сервиса
```

**Если используете supervisor:**
```bash
sudo supervisorctl restart crm  # или имя вашего процесса
```

### Шаг 6: Очистите кэш (опционально, но рекомендуется)

**Если используете Docker:**
```bash
docker compose exec web python manage.py shell -c "from django.core.cache import cache; cache.clear()"
```

**Если без Docker:**
```bash
python manage.py shell -c "from django.core.cache import cache; cache.clear()"
```

---

## Вариант 3: Для VDS с docker-compose.vds.yml

```bash
cd /path/to/project

# 1. Обновление кода
git pull origin main

# 2. Применение миграций
docker compose -f docker-compose.yml -f docker-compose.vds.yml exec web python manage.py migrate

# 3. Сбор статики
docker compose -f docker-compose.yml -f docker-compose.vds.yml exec web python manage.py collectstatic --noinput

# 4. Перезапуск
docker compose -f docker-compose.yml -f docker-compose.vds.yml restart web
```

---

## Проверка после деплоя

### 1. Проверьте, что миграции применены:
```bash
docker compose exec web python manage.py showmigrations ui
```

Должны быть отмечены как `[X]`:
- `[X] 0007_remove_uiuserpreference_company_list_view_mode`
- `[X] 0008_uiuserpreference_company_detail_view_mode`

### 2. Проверьте работоспособность:
- Откройте `/companies/` - должен быть обычный список (без переключателя)
- Откройте любую карточку компании `/companies/<uuid>/`
- Проверьте наличие переключателя режимов (две иконки в шапке)
- Переключите режим и убедитесь, что он сохраняется

### 3. Проверьте логи (если что-то не работает):
```bash
# Docker
docker compose logs web --tail=50

# Или systemd
sudo journalctl -u crm -n 50
```

---

## Важные замечания

1. **Резервная копия БД (рекомендуется перед миграциями):**
   ```bash
   # Если есть скрипт backup
   ./scripts/backup_postgres.sh
   
   # Или вручную
   docker compose exec db pg_dump -U crm crm > backup_$(date +%Y%m%d_%H%M%S).sql
   ```

2. **Миграции безопасны:**
   - `0007` - удаляет поле `company_list_view_mode` (если оно было)
   - `0008` - добавляет поле `company_detail_view_mode` с default='classic'
   - Обе миграции обратимые и безопасные

3. **Если возникли проблемы:**
   - Проверьте логи: `docker compose logs web`
   - Проверьте статус контейнеров: `docker compose ps`
   - Убедитесь, что база данных доступна

---

## Быстрая команда (все в одной строке)

```bash
cd /path/to/project && \
git pull origin main && \
docker compose exec web python manage.py migrate && \
docker compose exec web python manage.py collectstatic --noinput && \
docker compose restart web && \
echo "✅ Деплой завершен!"
```
