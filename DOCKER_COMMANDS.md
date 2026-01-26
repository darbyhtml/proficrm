# Команды для работы с Docker и тестирования изменений

## Проверка изменений перед пушем

### 1. Проверка синтаксиса и импортов
```bash
# В контейнере web
docker compose exec web python -m py_compile backend/companies/normalizers.py
docker compose exec web python -m py_compile backend/companies/api.py
docker compose exec web python -m py_compile backend/companies/models.py
docker compose exec web python -m py_compile backend/ui/forms.py
docker compose exec web python -m py_compile backend/tasksapp/api.py
```

### 2. Проверка Django (без миграций)
```bash
docker compose exec web python manage.py check
```

### 3. Проверка миграций
```bash
# Проверка, что миграции не требуются
docker compose exec web python manage.py makemigrations --check --dry-run

# Просмотр плана миграций (если нужно)
docker compose exec web python manage.py migrate --plan
```

### 4. Запуск тестов
```bash
# Все тесты компаний
docker compose exec web python manage.py test companies.tests

# Конкретный тест
docker compose exec web python manage.py test companies.tests.NormalizersTestCase

# Тесты API
docker compose exec web python manage.py test companies.tests.CompanyAPITestCase
```

### 5. Проверка работы API (опционально)
```bash
# После перезапуска контейнера можно проверить API вручную
# curl http://localhost:8001/api/companies/?search=test
```

## Перезапуск сервисов после изменений

```bash
# Перезапуск только web (если изменения только в коде)
docker compose restart web

# Перезапуск всех сервисов (если нужны миграции)
docker compose restart

# Пересборка (если изменились зависимости)
docker compose up -d --build
```

## Проверка логов

```bash
# Логи web
docker compose logs -f web

# Логи celery
docker compose logs -f celery

# Последние 100 строк логов
docker compose logs --tail=100 web
```

## Подготовка к пушу в Git

### 1. Проверка статуса
```bash
git status
```

### 2. Просмотр изменений
```bash
git diff
git diff --staged
```

### 3. Добавление файлов
```bash
# Новый файл нормализаторов
git add backend/companies/normalizers.py

# Измененные файлы
git add backend/companies/models.py
git add backend/companies/api.py
git add backend/companies/tests.py
git add backend/ui/forms.py
git add backend/tasksapp/api.py
```

### 4. Коммит
```bash
git commit -m "feat: единый слой нормализации данных для компаний

- Создан companies/normalizers.py с едиными нормализаторами
- Приведены к единому поведению UI формы, DRF сериализаторы и модели
- Исправлены DRF фильтры (SearchFilter, OrderingFilter)
- Добавлены поля work_schedule и work_timezone в API
- Добавлены тесты для нормализации и API

Исправления:
A) Нормализация телефонов - единый normalize_phone()
B) Нормализация work_schedule - добавлена в API
C) DRF фильтры - исправлены SearchFilter и OrderingFilter
D) Поля Company - выровнены UI и API
E) Нормализация ИНН - единый normalize_inn()"
```

### 5. Пуш
```bash
# В текущую ветку
git push

# Или в feature-ветку
git push origin feature/unified-normalization
```

## Полный чеклист перед пушем

- [ ] Все файлы скомпилированы без ошибок
- [ ] `python manage.py check` проходит без ошибок
- [ ] `python manage.py makemigrations --check` не требует новых миграций
- [ ] Тесты проходят: `python manage.py test companies.tests`
- [ ] Логи не показывают ошибок после перезапуска
- [ ] Изменения закоммичены с понятным сообщением
- [ ] Проверен `git status` - нет лишних файлов
