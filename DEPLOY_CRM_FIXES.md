# Команды для развертывания исправлений CRM на сервере

## Дата: 2026-01-XX
## Изменения:
- Увеличен z-index окна уведомлений
- Автоматическое сопоставление задач с TaskType
- Убрано дублирование отображения задач
- Кастомный виджет Select с иконками и цветами
- Самоочистка выполненных задач (3 месяца)

---

## Шаг 1: Подключение к серверу и переход в директорию

```bash
cd /opt/proficrm
```

---

## Шаг 2: Получение изменений из репозитория

```bash
git pull origin main
```

**Ожидаемый результат:**
```
Updating 292dcc6..9532614
Fast-forward
 backend/tasksapp/management/commands/cleanup_old_tasks.py |  XX +++++
 backend/templates/ui/base.html                           |  XX +++++
 backend/templates/ui/dashboard.html                      |  XX +---
 backend/ui/forms.py                                      |  XX +---
 backend/ui/views.py                                      |  XX +---
 backend/ui/widgets.py                                    |  XX +++++
```

---

## Шаг 3: Проверка миграций (не требуются, но проверим)

```bash
docker-compose exec web python manage.py showmigrations tasksapp
```

**Ожидаемый результат:** Все миграции применены `[X]`

---

## Шаг 4: Перезапуск веб-сервера

```bash
docker-compose restart web
```

**Ожидаемый результат:**
```
Restarting web ... done
```

---

## Шаг 5: Очистка кэша Django

```bash
docker-compose exec web python manage.py shell -c "from django.core.cache import cache; cache.clear()"
```

**Ожидаемый результат:** Команда выполнится без ошибок

---

## Шаг 6: Проверка работоспособности (запуск тестов)

```bash
# Запуск всех тестов dashboard
docker-compose exec web python manage.py test ui.tests.test_dashboard -v 2
```

**Ожидаемый результат:** Все тесты должны пройти успешно

---

## Шаг 7: Проверка логов (если что-то пошло не так)

```bash
docker-compose logs web --tail=50
```

---

## Полный скрипт одной командой

```bash
cd /opt/proficrm && \
git pull origin main && \
docker-compose restart web && \
docker-compose exec web python manage.py shell -c "from django.core.cache import cache; cache.clear()" && \
docker-compose exec web python manage.py test ui.tests.test_dashboard -v 2
```

---

## Проверка функциональности после деплоя

### 1. Окно уведомлений
- ✅ Откройте колокольчик уведомлений
- ✅ Проверьте, что окно отображается поверх всех элементов (z-index)

### 2. Автоматическое сопоставление задач
- ✅ Откройте рабочую область
- ✅ Проверьте, что задачи с названиями из справочника отображаются с правильными иконками и цветами
- ✅ Проверьте, что задачи без типа отображаются серым цветом (если это импортированные)

### 3. Убрано дублирование
- ✅ Проверьте, что на рабочем столе задачи отображаются только один раз (либо badge, либо текст)
- ✅ Если есть TaskType - показывается только badge
- ✅ Если нет TaskType - показывается только текст названия

### 4. Кастомный виджет Select
- ✅ Откройте создание новой задачи
- ✅ Проверьте, что в выпадающем списке "Задача" отображаются иконки и цвета
- ✅ Откройте редактирование задачи
- ✅ Проверьте, что выбранное значение отображается с иконкой и цветом

### 5. Самоочистка выполненных задач
- ✅ Проверьте, что команда доступна:
```bash
docker-compose exec web python manage.py cleanup_old_tasks --help
```
- ✅ Запустите тестовый прогон (dry-run):
```bash
docker-compose exec web python manage.py cleanup_old_tasks --dry-run --months 3
```

---

## Настройка автоматической очистки (опционально)

Для автоматической очистки выполненных задач можно добавить в cron:

```bash
# Редактировать crontab
crontab -e

# Добавить строку (запуск каждый день в 3:00 ночи)
0 3 * * * cd /opt/proficrm && docker-compose exec -T web python manage.py cleanup_old_tasks --months 3 >> /var/log/crm_cleanup.log 2>&1
```

---

## Откат изменений (если что-то пошло не так)

```bash
cd /opt/proficrm
git reset --hard HEAD~1
docker-compose restart web
docker-compose exec web python manage.py shell -c "from django.core.cache import cache; cache.clear()"
```

---

## Полезные команды для диагностики

```bash
# Проверка статуса контейнеров
docker-compose ps

# Просмотр логов в реальном времени
docker-compose logs -f web

# Проверка версии Python и Django
docker-compose exec web python --version
docker-compose exec web python manage.py --version

# Проверка доступности приложения
curl -I http://localhost:8000/
```

---

## Контакты для поддержки

Если возникли проблемы:
1. Проверьте логи: `docker-compose logs web --tail=100`
2. Проверьте статус контейнеров: `docker-compose ps`
3. Убедитесь, что все миграции применены: `docker-compose exec web python manage.py showmigrations`
