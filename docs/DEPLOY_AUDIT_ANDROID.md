# Аудит деплоя: папка android/ на серверах

## Резюме

**Вывод:** Папка `android/` **НЕ НУЖНА** на серверах для runtime работы CRM.

**Рекомендация:** Исключить `android/` из Docker build context через `.dockerignore`. На сервере папка может остаться в репозитории (для истории/откатов), но не будет использоваться в runtime.

---

## ЧАСТЬ 1: Аудит текущего деплоя

### 1.1. Структура Docker деплоя

#### Dockerfile.staging
```dockerfile
# Копирует ТОЛЬКО backend/
COPY backend/requirements.txt /app/backend/requirements.txt
COPY backend /app/backend
```

**Вывод:** ✅ `android/` **НЕ попадает** в Docker image, так как Dockerfile копирует только `backend/`.

#### docker-compose.staging.yml
```yaml
web:
  build:
    context: .          # ⚠️ Build context = весь репозиторий
    dockerfile: Dockerfile.staging
  volumes:
    - ./backend:/app/backend  # ✅ Монтируется только backend/
```

**Вывод:**
- ⚠️ Build context = `.` (весь репозиторий виден при сборке)
- ✅ Volumes монтируют только `./backend`, не весь репозиторий
- ✅ Runtime использует только `backend/`

#### Отсутствие .dockerignore

**Текущее состояние:** `.dockerignore` отсутствует.

**Проблема:** При `docker build` весь репозиторий (включая `android/`) попадает в build context, даже если не копируется в image. Это:
- Увеличивает время сборки
- Увеличивает размер build context
- Небезопасно (может случайно скопировать лишнее)

**Решение:** Создать `.dockerignore` для исключения `android/` из build context.

### 1.2. Проверка на сервере (staging)

#### Где находится репозиторий

```bash
# Предположительно:
cd /opt/crm-staging
ls -la
```

**Ожидаемая структура:**
```
/opt/crm-staging/
├── backend/
├── android/          # ⚠️ Попадает через git pull
├── docker-compose.staging.yml
├── Dockerfile.staging
└── .env.staging
```

#### Что используется в runtime

1. **Docker image:** Собирается из `backend/` (через Dockerfile)
2. **Volumes:** Монтируются только `./backend:/app/backend`
3. **Nginx:** Раздаёт только `/static/` и `/media/` из volumes

**Вывод:** ✅ `android/` **НЕ используется** в runtime.

#### Проверка наличия android/ на сервере

```bash
# На staging сервере выполнить:
cd /opt/crm-staging
ls -la android/ 2>/dev/null && echo "android/ существует" || echo "android/ отсутствует"

# Проверить размер
du -sh android/ 2>/dev/null || echo "android/ отсутствует"

# Проверить, есть ли build artifacts
find android/ -name "*.apk" -o -name "*.aab" 2>/dev/null
find android/ -name "build" -type d 2>/dev/null
find android/ -name ".gradle" -type d 2>/dev/null
```

**Ожидаемый результат:**
- `android/` существует (через `git pull`)
- Может содержать build artifacts (если кто-то собирал на сервере)
- Не используется в runtime

### 1.3. Проверка Nginx конфигурации

#### nginx/staging.conf

```nginx
location /media/ {
    alias /usr/share/nginx/html/media/;  # Из volume media_staging
}
```

**Вывод:** ✅ Nginx раздаёт только файлы из `media_staging` volume, не из репозитория.

**APK файлы:**
- Хранятся в `backend/media/mobile_apps/` (через Django FileField)
- Раздаются через `/media/mobile_apps/...` (Nginx)
- **НЕ** из папки `android/` в репозитории

---

## ЧАСТЬ 2: Что нужно сделать

### 2.1. Создать .dockerignore (ОБЯЗАТЕЛЬНО)

**Файл:** `.dockerignore` (в корне репозитория)

**Содержимое:** Уже создано (см. `.dockerignore`)

**Эффект:**
- `android/` исключается из Docker build context
- Ускоряет сборку образов
- Уменьшает размер build context
- Предотвращает случайное копирование лишнего

**Проверка:**

```bash
# Локально проверить, что .dockerignore работает
docker build -f Dockerfile.staging -t test-build .
# Проверить размер build context (должен быть меньше)

# Или проверить, что android/ не попадает:
docker build -f Dockerfile.staging -t test-build . 2>&1 | grep -i android
# Не должно быть упоминаний android/ в процессе сборки
```

### 2.2. Проверка на сервере (опционально)

#### Если android/ не нужен на сервере (рекомендуется оставить)

**Причины оставить:**
- История коммитов (git log)
- Возможность отката
- Документация (RELEASE.md)
- Не занимает много места (код, не бинарники)

**Рекомендация:** Оставить `android/` в репозитории на сервере, но исключить из Docker build context через `.dockerignore`.

#### Если нужно удалить android/ с сервера (осторожно!)

**ВАЖНО:** Удалять только если:
1. Убедились, что Docker build работает без `android/`
2. Убедились, что деплой не зависит от `android/`
3. Сделали backup

**Шаги:**

```bash
# 1. Создать backup
cd /opt/crm-staging
tar -czf android-backup-$(date +%Y%m%d).tar.gz android/

# 2. Переместить в архив (вместо удаления)
mkdir -p /opt/archive
mv android /opt/archive/android-$(date +%Y%m%d)

# 3. Проверить, что всё работает
docker compose -f docker-compose.staging.yml build
docker compose -f docker-compose.staging.yml up -d

# 4. Если всё ок - можно удалить архив через неделю
# rm -rf /opt/archive/android-*
```

**НЕ РЕКОМЕНДУЕТСЯ:** Удалять `android/` из git репозитория (это история проекта).

---

## ЧАСТЬ 3: Проверка после изменений

### 3.1. Проверка Docker build

```bash
# На сервере после git pull
cd /opt/crm-staging

# Проверить, что .dockerignore существует
cat .dockerignore | grep android

# Пересобрать образ
docker compose -f docker-compose.staging.yml build web

# Проверить логи сборки (не должно быть android/)
docker compose -f docker-compose.staging.yml build web 2>&1 | grep -i android
# Ожидаемый результат: пусто (нет упоминаний android/)
```

### 3.2. Проверка runtime

```bash
# Проверить, что контейнер работает
docker compose -f docker-compose.staging.yml ps

# Проверить, что backend доступен
docker compose -f docker-compose.staging.yml exec web ls -la /app/backend/

# Убедиться, что android/ НЕ в контейнере
docker compose -f docker-compose.staging.yml exec web ls -la /app/ | grep android
# Ожидаемый результат: пусто (android/ отсутствует в контейнере)
```

### 3.3. Проверка деплоя

```bash
# Выполнить полный деплой
/opt/crm-staging/deploy.sh

# Или вручную:
cd /opt/crm-staging
git pull
docker compose -f docker-compose.staging.yml build
docker compose -f docker-compose.staging.yml up -d
docker compose -f docker-compose.staging.yml exec web python manage.py migrate
docker compose -f docker-compose.staging.yml exec web python manage.py collectstatic --noinput

# Проверить, что всё работает
curl http://95.142.47.245/health/
```

---

## ЧАСТЬ 4: Итоговые рекомендации

### ✅ Что сделать ОБЯЗАТЕЛЬНО

1. **Создать `.dockerignore`** (уже сделано)
   - Исключает `android/` из Docker build context
   - Безопасно, не ломает существующий деплой

2. **Закоммитить `.dockerignore`**
   - Включить в репозиторий
   - Применить на staging и production

3. **Проверить деплой на staging**
   - Убедиться, что сборка работает
   - Убедиться, что runtime не затронут

### ⚠️ Что сделать ОПЦИОНАЛЬНО

1. **Очистить build artifacts на сервере** (если есть)
   ```bash
   # Удалить только build artifacts, не весь android/
   find /opt/crm-staging/android -name "build" -type d -exec rm -rf {} +
   find /opt/crm-staging/android -name ".gradle" -type d -exec rm -rf {} +
   find /opt/crm-staging/android -name "*.apk" -delete
   find /opt/crm-staging/android -name "*.aab" -delete
   ```

2. **Переместить android/ в архив** (если очень нужно)
   - Только после проверки деплоя
   - С backup

### ❌ Что НЕ делать

1. **НЕ удалять `android/` из git репозитория**
   - Это история проекта
   - Нужно для откатов и документации

2. **НЕ удалять `android/` на сервере без проверки**
   - Сначала проверить деплой
   - Сделать backup

3. **НЕ менять Dockerfile без необходимости**
   - Текущий Dockerfile правильный (копирует только backend/)
   - `.dockerignore` достаточно

---

## ЧАСТЬ 5: Проверка production

### 5.1. Аналогичная проверка на production

```bash
# На production сервере (аналогично staging)
cd /opt/crm-production  # или где находится production

# Проверить структуру
ls -la

# Проверить docker-compose
cat docker-compose.yml | grep -A 5 "build:"

# Проверить наличие android/
ls -la android/ 2>/dev/null && echo "Существует" || echo "Отсутствует"
```

### 5.2. Применить изменения

1. Закоммитить `.dockerignore` в репозиторий
2. На production: `git pull`
3. Пересобрать образы (если нужно)
4. Проверить работоспособность

---

## Итоговый вывод

### Текущее состояние

- ✅ Dockerfile копирует только `backend/` (правильно)
- ✅ Volumes монтируют только `backend/` (правильно)
- ⚠️ Build context = `.` (видит весь репозиторий, но не критично)
- ❌ Нет `.dockerignore` (нужно добавить)

### После изменений

- ✅ `.dockerignore` исключает `android/` из build context
- ✅ Docker build быстрее и безопаснее
- ✅ Runtime не затронут (использует только `backend/`)
- ✅ `android/` остаётся в репозитории (для истории)

### Безопасность изменений

- ✅ Не ломает существующий деплой
- ✅ Не влияет на runtime
- ✅ Можно откатить (git revert)
- ✅ Минимальные изменения (только `.dockerignore`)

---

## Команды для проверки на сервере

```bash
# Полная проверка на staging
cd /opt/crm-staging

# 1. Проверить структуру
echo "=== Структура репозитория ==="
ls -la | head -20

# 2. Проверить наличие android/
echo "=== Проверка android/ ==="
if [ -d "android" ]; then
    echo "android/ существует"
    du -sh android/
    echo "Содержимое android/:"
    ls -la android/ | head -10
else
    echo "android/ отсутствует"
fi

# 3. Проверить .dockerignore
echo "=== Проверка .dockerignore ==="
if [ -f ".dockerignore" ]; then
    echo ".dockerignore существует"
    grep -i android .dockerignore && echo "android/ исключён" || echo "android/ НЕ исключён"
else
    echo ".dockerignore отсутствует (нужно создать)"
fi

# 4. Проверить Dockerfile
echo "=== Проверка Dockerfile ==="
grep -i "COPY\|copy" Dockerfile.staging

# 5. Проверить docker-compose
echo "=== Проверка docker-compose ==="
grep -A 3 "build:" docker-compose.staging.yml | head -10

# 6. Проверить volumes
echo "=== Проверка volumes ==="
grep -A 2 "volumes:" docker-compose.staging.yml | grep -E "backend|android"

# 7. Проверить, что в контейнере нет android/
echo "=== Проверка контейнера ==="
docker compose -f docker-compose.staging.yml exec web ls -la /app/ 2>/dev/null | grep android || echo "android/ отсутствует в контейнере (правильно)"
```
