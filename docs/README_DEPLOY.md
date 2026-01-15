# Deployment improvements: Android directory exclusion

## Краткое резюме

Добавлен `.dockerignore` для исключения папки `android/` из Docker build context. Это ускоряет сборку образов и делает деплой более безопасным, не влияя на runtime.

## Что сделано

### 1. Создан `.dockerignore`
- Исключает `android/` из Docker build context
- Исключает Android build artifacts (`build/`, `.gradle/`, `*.apk`, `*.aab`)
- Исключает keystores (`*.jks`, `*.keystore`)
- Исключает другие ненужные файлы (IDE, временные скрипты, документацию)

### 2. Добавлена документация

#### `docs/TESTING_STAGING_ANDROID.md`
Пошаговое руководство по тестированию:
- **Часть A**: Тестирование на staging сервере (CRM, `/mobile-app/`, QR-login, logout)
- **Часть B**: Тестирование Android приложения (QR-login, polling, offline queue)
- **Часть C**: Функциональное тестирование в CRM (CallRequest, PhoneDevice, audit)

#### `docs/DEPLOY_AUDIT_ANDROID.md`
Аудит деплоя и инструкции:
- Анализ использования `android/` на серверах
- Проверка Docker build context
- Рекомендации по очистке (опционально)
- Команды для проверки

## Почему это безопасно

### ✅ Не ломает существующий деплой
- Dockerfile уже копирует только `backend/`
- Volumes монтируют только `./backend`
- `.dockerignore` только оптимизирует build context

### ✅ Не влияет на runtime
- Контейнеры используют только `backend/`
- Nginx раздаёт только `/static/` и `/media/`
- `android/` не используется в runtime

### ✅ Можно откатить
- Просто удалить `.dockerignore` или сделать `git revert`

## Как проверить

### Локально

```bash
# Проверить .dockerignore
cat .dockerignore | grep android

# Тест сборки (должна быть быстрее)
docker build -f Dockerfile.staging -t test-build .
```

### На staging сервере

```bash
cd /opt/crm-staging

# Обновить код
git pull

# Пересобрать образы
docker compose -f docker-compose.staging.yml build web

# Проверить, что всё работает
docker compose -f docker-compose.staging.yml up -d
curl http://95.142.47.245/health/
```

## Что НЕ изменилось

- ❌ `android/` остаётся в git репозитории (для истории/откатов)
- ❌ Структура проекта не изменилась
- ❌ Dockerfile не изменился
- ❌ docker-compose не изменился
- ❌ Runtime поведение не изменилось

## Коммиты

1. `chore(deploy): prevent android sources from entering Docker build context`
   - Добавлен `.dockerignore`

2. `docs: add staging testing plan and deployment audit`
   - Добавлена документация по тестированию и аудиту

## Следующие шаги

1. ✅ Закоммитить изменения (уже сделано)
2. ⏳ Протестировать на staging (см. `docs/TESTING_STAGING_ANDROID.md`)
3. ⏳ Применить на production после проверки

## Дополнительная информация

- Полный план тестирования: `docs/TESTING_STAGING_ANDROID.md`
- Аудит деплоя: `docs/DEPLOY_AUDIT_ANDROID.md`
- Changelog: `CHANGELOG_DEPLOY.md`
