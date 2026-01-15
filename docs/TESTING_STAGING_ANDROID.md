# План тестирования: Staging + Android + CRM

## Обзор

Этот документ описывает пошаговое тестирование новой функциональности:
- Витрина мобильного приложения (`/mobile-app/`)
- QR-логин (одноразовый токен, TTL 5 минут)
- Удалённый logout
- Интеграция Android приложения

**Важно:** Все тесты выполняются на **staging** окружении (`http://95.142.47.245/`).

---

## ЧАСТЬ A: Тестирование на STAGING сервере

### A1. Подготовка и деплой

#### 1.1. Подключение к серверу

```bash
# Подключиться к staging серверу
ssh user@95.142.47.245

# Перейти в директорию проекта
cd /opt/crm-staging
```

#### 1.2. Обновление кода и зависимостей

```bash
# Обновить код из репозитория
git pull

# Проверить, что новые миграции есть
ls -la backend/phonebridge/migrations/ | grep -E "mobile|qr"

# Если миграций нет - создать их (локально, затем закоммитить)
# docker compose -f docker-compose.staging.yml exec web python manage.py makemigrations phonebridge
```

#### 1.3. Установка новых зависимостей

```bash
# Проверить requirements.txt на наличие qrcode[pil]
grep -i qrcode backend/requirements.txt

# Если нет - добавить вручную или пересобрать контейнер
# Пересборка контейнера (автоматически установит зависимости)
docker compose -f docker-compose.staging.yml build web
```

#### 1.4. Применение миграций

```bash
# Выполнить миграции
docker compose -f docker-compose.staging.yml exec web python manage.py migrate

# Проверить, что таблицы созданы
docker compose -f docker-compose.staging.yml exec web python manage.py dbshell
# В psql:
# \dt phonebridge_mobileappbuild
# \dt phonebridge_mobileappqrtoken
# \q
```

#### 1.5. Сбор статики

```bash
# Собрать статические файлы (если были изменения в templates)
docker compose -f docker-compose.staging.yml exec web python manage.py collectstatic --noinput
```

#### 1.6. Перезапуск сервисов

```bash
# Перезапустить контейнеры (если нужно)
docker compose -f docker-compose.staging.yml restart web

# Проверить статус
docker compose -f docker-compose.staging.yml ps

# Проверить логи
docker compose -f docker-compose.staging.yml logs -f web
```

### A2. Проверка базовой работоспособности CRM

#### 2.1. Health check

```bash
# Проверить health endpoint
curl http://95.142.47.245/health/

# Ожидаемый результат: {"status": "ok"} или 200 OK
```

#### 2.2. Доступность главной страницы

```bash
# Проверить главную страницу
curl -I http://95.142.47.245/

# Ожидаемый результат: 200 OK или редирект на /login/
```

#### 2.3. Проверка логина

1. Открыть в браузере: `http://95.142.47.245/`
2. Войти с тестовыми учётными данными
3. Убедиться, что дашборд открывается

### A3. Тестирование страницы `/mobile-app/`

#### 3.1. Доступность страницы

1. Войти в CRM под любым авторизованным пользователем
2. Открыть: `http://95.142.47.245/mobile-app/`
3. **Ожидаемый результат:**
   - Страница открывается без ошибок
   - Видны блоки: "Скачать приложение", "QR-вход", "История версий", "Инструкция"

#### 3.2. Проверка доступа (только авторизованным)

1. Выйти из CRM (logout)
2. Попытаться открыть: `http://95.142.47.245/mobile-app/`
3. **Ожидаемый результат:**
   - Редирект на `/login/`
   - Страница недоступна без авторизации

#### 3.3. Загрузка APK через админку

1. Войти как администратор
2. Открыть: `http://95.142.47.245/admin/`
3. Перейти: `Phonebridge → Mobile app builds`
4. Нажать "Add Mobile app build"
5. Заполнить:
   - **Env:** `production`
   - **Version name:** `0.5-staging`
   - **Version code:** `5`
   - **File:** выбрать APK файл (например, `app-staging-debug.apk`)
   - **Is active:** `True`
6. Сохранить
7. **Ожидаемый результат:**
   - APK загружается успешно
   - SHA256 вычисляется автоматически
   - `uploaded_by` заполняется автоматически

#### 3.4. Проверка скачивания APK

1. Открыть: `http://95.142.47.245/mobile-app/`
2. Нажать "Скачать APK" (кнопка с последней версией)
3. **Ожидаемый результат:**
   - Файл скачивается
   - Имя файла: `crmprofi-0.5-staging-5.apk`
   - Content-Type: `application/vnd.android.package-archive`

#### 3.5. Проверка таблицы версий

1. На странице `/mobile-app/` проверить таблицу "История версий"
2. **Ожидаемый результат:**
   - Видны все загруженные версии (последние 10)
   - Отображаются: версия, дата, размер файла
   - Кнопка "Скачать" работает для каждой версии

### A4. Тестирование QR-логина

#### 4.1. Создание QR-токена (API)

```bash
# Войти в CRM и получить session cookie
# Затем выполнить:

curl -X POST http://95.142.47.245/api/phone/qr/create/ \
  -H "Cookie: sessionid=YOUR_SESSION_ID" \
  -H "Content-Type: application/json"

# Ожидаемый результат:
# {
#   "token": "long_base64url_token...",
#   "expires_at": "2025-01-XX..."
# }
```

**Проверки:**
- [ ] Токен создаётся успешно
- [ ] `expires_at` = текущее время + 5 минут
- [ ] Без авторизации возвращает 401

#### 4.2. Rate limiting на создание QR

```bash
# Быстро создать несколько токенов подряд
for i in {1..3}; do
  curl -X POST http://95.142.47.245/api/phone/qr/create/ \
    -H "Cookie: sessionid=YOUR_SESSION_ID"
  sleep 1
done

# Ожидаемый результат:
# Первый запрос - успех
# Второй запрос (менее чем через 10 секунд) - 429 Too Many Requests
```

#### 4.3. Генерация QR-изображения

1. Открыть: `http://95.142.47.245/mobile-app/`
2. Нажать "QR-вход"
3. **Ожидаемый результат:**
   - Открывается модалка с QR-кодом
   - QR-код отображается (изображение PNG)
   - Виден таймер обратного отсчёта (5:00)
   - Подпись "Действует 5 минут"

#### 4.4. Проверка TTL QR-токена

1. Создать QR-токен через API или UI
2. Подождать 6 минут
3. Попытаться обменять токен:

```bash
curl -X POST http://95.142.47.245/api/phone/qr/exchange/ \
  -H "Content-Type: application/json" \
  -d '{"token": "EXPIRED_TOKEN"}'

# Ожидаемый результат:
# {"detail": "Неверный или истекший токен"} (400 Bad Request)
```

#### 4.5. Проверка одноразовости QR-токена

1. Создать QR-токен
2. Обменять его один раз:

```bash
curl -X POST http://95.142.47.245/api/phone/qr/exchange/ \
  -H "Content-Type: application/json" \
  -d '{"token": "VALID_TOKEN"}'

# Ожидаемый результат:
# {
#   "access": "jwt_access_token...",
#   "refresh": "jwt_refresh_token...",
#   "username": "testuser"
# }
```

3. Попытаться обменять тот же токен повторно:

```bash
curl -X POST http://95.142.47.245/api/phone/qr/exchange/ \
  -H "Content-Type: application/json" \
  -d '{"token": "SAME_TOKEN"}'

# Ожидаемый результат:
# {"detail": "Неверный или истекший токен"} (400 Bad Request)
```

### A5. Тестирование logout endpoints

#### 5.1. Logout с refresh token

```bash
# Получить JWT токены (через login или QR exchange)
ACCESS_TOKEN="..."
REFRESH_TOKEN="..."

# Выполнить logout
curl -X POST http://95.142.47.245/api/phone/logout/ \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"refresh\": \"$REFRESH_TOKEN\"}"

# Ожидаемый результат:
# {"ok": true, "message": "Сессия завершена"}
```

#### 5.2. Logout all (все сессии)

```bash
curl -X POST http://95.142.47.245/api/phone/logout/all/ \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json"

# Ожидаемый результат:
# {"ok": true, "message": "Все сессии завершены"}
```

### A6. Проверка audit логов

#### 6.1. Проверка логов скачивания APK

1. Скачать APK через `/mobile-app/`
2. Проверить в админке: `Audit → Activity events`
3. **Ожидаемый результат:**
   - Событие с типом `mobile_app`
   - Сообщение: "Скачана версия X.X (X)"
   - Метаданные: `version_name`, `version_code`, `ip`

#### 6.2. Проверка логов QR-логина

1. Создать QR-токен через API
2. Обменять токен
3. Проверить в админке: `Audit → Activity events`
4. **Ожидаемый результат:**
   - События создания и обмена QR-токена (если логируются)

#### 6.3. Проверка логов logout

1. Выполнить logout через API
2. Проверить в админке: `Audit → Activity events`
3. **Ожидаемый результат:**
   - Событие с типом `security`
   - Сообщение: "Выход из мобильного приложения"
   - Метаданные: `ip`, `device_id`, `has_refresh_token`

### A7. Проверка логов сервера

#### 7.1. Логи Django/Gunicorn

```bash
# Просмотр логов web контейнера
docker compose -f docker-compose.staging.yml logs -f web

# Фильтр по ошибкам
docker compose -f docker-compose.staging.yml logs web | grep -i error

# Ожидаемый результат:
# Нет критических ошибок
# Нет 500 Internal Server Error
```

#### 7.2. Логи Nginx

```bash
# Просмотр access логов
docker compose -f docker-compose.staging.yml exec nginx tail -f /var/log/nginx/access.log

# Проверить запросы к /mobile-app/
docker compose -f docker-compose.staging.yml exec nginx grep "/mobile-app/" /var/log/nginx/access.log
```

#### 7.3. Логи Celery (если используется)

```bash
docker compose -f docker-compose.staging.yml logs -f celery
```

---

## ЧАСТЬ B: Тестирование Android приложения

### B1. Сборка staging APK

#### 1.1. Подготовка окружения

```bash
# Перейти в директорию Android проекта
cd android/CRMProfiDialer

# Проверить, что Gradle wrapper доступен
./gradlew --version

# Ожидаемый результат:
# Gradle версия 8.x или выше
```

#### 1.2. Сборка stagingDebug APK

```bash
# Собрать staging debug APK
./gradlew assembleStagingDebug

# Ожидаемый результат:
# BUILD SUCCESSFUL
# APK находится в: app/build/outputs/apk/staging/debug/app-staging-debug.apk
```

#### 1.3. Проверка размера и подписи

```bash
# Проверить размер APK
ls -lh app/build/outputs/apk/staging/debug/app-staging-debug.apk

# Проверить, что APK не подписан (debug сборка)
apksigner verify --print-certs app/build/outputs/apk/staging/debug/app-staging-debug.apk

# Ожидаемый результат:
# APK не подписан (debug сборка)
# Размер примерно 5-15 МБ
```

### B2. Установка на устройство

#### 2.1. Через ADB (если доступно)

```bash
# Подключить устройство через USB
adb devices

# Установить APK
adb install app/build/outputs/apk/staging/debug/app-staging-debug.apk

# Ожидаемый результат:
# Success
```

#### 2.2. Ручная установка

1. Скопировать APK на устройство (через USB, email, облако)
2. На устройстве: Настройки → Безопасность → Разрешить установку из неизвестных источников
3. Открыть APK файл на устройстве
4. Установить приложение
5. **Ожидаемый результат:**
   - Приложение устанавливается успешно
   - ApplicationId: `ru.groupprofi.crmprofi.dialer.staging`
   - Иконка и название приложения корректны

### B3. Тестовые сценарии

#### Сценарий 1: Вход по QR-коду

**Шаги:**
1. Открыть приложение на устройстве
2. Нажать "Войти по QR-коду"
3. Разрешить доступ к камере (если запрашивается)
4. Открыть CRM в браузере на компьютере: `http://95.142.47.245/mobile-app/`
5. Нажать "QR-вход" в CRM
6. Отсканировать QR-код камерой устройства
7. Дождаться обработки

**Ожидаемый результат:**
- [ ] Сканер QR открывается
- [ ] QR-код сканируется успешно
- [ ] Токен обменивается на JWT
- [ ] Токены сохраняются в EncryptedSharedPreferences
- [ ] Устройство регистрируется автоматически
- [ ] CallListenerService запускается
- [ ] Экран показывает "Вход выполнен"
- [ ] Кнопки логина скрываются, показывается "Выйти"

**Проверка в CRM:**
- Открыть: `http://95.142.47.245/admin/phonebridge/phonedevice/`
- Убедиться, что устройство зарегистрировалось
- Проверить `device_id`, `last_seen_at`, `app_version`

#### Сценарий 2: Fallback логин по username/password

**Шаги:**
1. Открыть приложение
2. Ввести логин и пароль
3. Нажать "Войти"

**Ожидаемый результат:**
- [ ] Логин работает как раньше
- [ ] Токены сохраняются
- [ ] Устройство регистрируется
- [ ] Сервис запускается

#### Сценарий 3: Проверка polling (204/200)

**Шаги:**
1. Войти в приложение (QR или логин)
2. Дождаться запуска CallListenerService
3. Проверить уведомление сервиса (в шторке)
4. Открыть приложение и проверить статус

**Ожидаемый результат:**
- [ ] Уведомление показывает статус опроса
- [ ] Статус в приложении: "Ожидание команд · HH:MM:SS" (код 204)
- [ ] Нет ошибок в логах

**Проверка в CRM:**
- Открыть: `http://95.142.47.245/admin/phonebridge/phonedevice/`
- Проверить `last_poll_code` = 204
- Проверить `last_poll_at` обновляется

#### Сценарий 4: Команда на звонок → ACTION_DIAL → call log → update

**Шаги:**
1. В CRM создать команду звонка:
   - Открыть компанию/контакт
   - Нажать "Позвонить с телефона" (или создать CallRequest через admin)
2. На устройстве дождаться уведомления
3. Нажать на уведомление (или открыть приложение)
4. Проверить, что открылся dialer с номером
5. Совершить звонок (или симулировать)
6. Проверить, что данные о звонке отправились в CRM

**Ожидаемый результат:**
- [ ] Уведомление приходит на устройство
- [ ] При нажатии открывается dialer с правильным номером
- [ ] После звонка данные отправляются в CRM
- [ ] В CRM CallRequest обновляется: `call_status`, `call_duration_seconds`, `call_started_at`

**Проверка в CRM:**
- Открыть: `http://95.142.47.245/admin/phonebridge/callrequest/`
- Проверить статус CallRequest: `consumed` → обновлён `call_status`
- Проверить метаданные звонка

#### Сценарий 5: Офлайн очередь → flushQueue

**Шаги:**
1. Войти в приложение
2. Выключить Wi‑Fi и мобильные данные на устройстве
3. Совершить действие, которое требует отправки данных (например, звонок)
4. Проверить, что данные попали в очередь
5. Включить сеть
6. Дождаться автоматической отправки из очереди

**Ожидаемый результат:**
- [ ] При отсутствии сети данные сохраняются в Room очередь
- [ ] После включения сети данные автоматически отправляются
- [ ] Очередь очищается после успешной отправки

**Проверка в CRM:**
- Проверить, что данные пришли после включения сети
- Проверить логи на наличие retry запросов

#### Сценарий 6: Удалённый logout (graceful)

**Шаги:**
1. Войти в приложение
2. В CRM выполнить logout:

```bash
# Получить refresh token из приложения (через logcat или debug)
REFRESH_TOKEN="..."

# Выполнить logout
curl -X POST http://95.142.47.245/api/phone/logout/ \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"refresh\": \"$REFRESH_TOKEN\"}"
```

3. На устройстве попытаться выполнить любой API запрос (polling)
4. Проверить поведение приложения

**Ожидаемый результат:**
- [ ] При 401/403 токены очищаются автоматически
- [ ] CallListenerService останавливается
- [ ] Приложение показывает экран входа
- [ ] Можно войти снова (QR или логин/пароль)

**Альтернативный способ (через admin):**
- В CRM: `http://95.142.47.245/admin/phonebridge/mobileappqrtoken/`
- Найти активный токен пользователя
- Убедиться, что он помечен как использованный или истекший

---

## ЧАСТЬ C: Функциональное тестирование в CRM

### C1. Создание команды звонка

#### 1.1. Через UI (если реализовано)

1. Открыть компанию или контакт
2. Найти кнопку "Позвонить с телефона" или аналогичную
3. Нажать кнопку
4. **Ожидаемый результат:**
   - CallRequest создаётся в статусе `pending`
   - Команда появляется в polling

#### 1.2. Через Admin (для тестирования)

1. Открыть: `http://95.142.47.245/admin/phonebridge/callrequest/add/`
2. Заполнить:
   - **User:** выбрать тестового пользователя
   - **Phone raw:** `+79991234567`
   - **Status:** `pending`
   - **Company/Contact:** (опционально)
3. Сохранить
4. **Ожидаемый результат:**
   - CallRequest создаётся
   - При следующем polling устройство получает команду

### C2. Проверка обновления CallRequest

#### 2.1. После звонка с устройства

1. Создать CallRequest через admin
2. Дождаться, пока устройство получит команду (polling)
3. Совершить звонок на устройстве
4. Проверить обновление CallRequest

**Ожидаемый результат:**
- CallRequest обновляется:
  - `status` → `consumed`
  - `call_status` → `connected` / `no_answer` / и т.д.
  - `call_duration_seconds` → число секунд
  - `call_started_at` → timestamp

#### 2.2. Проверка в админке

1. Открыть: `http://95.142.47.245/admin/phonebridge/callrequest/`
2. Найти CallRequest по номеру телефона
3. Открыть детали
4. **Ожидаемый результат:**
   - Все поля заполнены корректно
   - Данные соответствуют фактическому звонку

### C3. Проверка моделей и таблиц

#### 3.1. PhoneDevice

```bash
# В Django shell
docker compose -f docker-compose.staging.yml exec web python manage.py shell

# Проверить устройства
from phonebridge.models import PhoneDevice
devices = PhoneDevice.objects.all()
for d in devices:
    print(f"{d.user.username}: {d.device_id}, last_seen: {d.last_seen_at}")
```

**Ожидаемый результат:**
- Устройства зарегистрированы
- `last_seen_at` обновляется при heartbeat/polling
- `app_version` заполнен

#### 3.2. PhoneTelemetry

```python
# В Django shell
from phonebridge.models import PhoneTelemetry
telemetry = PhoneTelemetry.objects.order_by('-ts')[:10]
for t in telemetry:
    print(f"{t.ts}: {t.type}, {t.endpoint}, {t.http_code}, {t.value_ms}ms")
```

**Ожидаемый результат:**
- Телеметрия собирается автоматически
- Видны latency метрики для API endpoints
- HTTP коды корректны

#### 3.3. PhoneLogBundle

```python
# В Django shell
from phonebridge.models import PhoneLogBundle
logs = PhoneLogBundle.objects.order_by('-ts')[:5]
for l in logs:
    print(f"{l.ts}: {l.level_summary}, {l.source}")
```

**Ожидаемый результат:**
- Логи отправляются при необходимости
- Чувствительные данные замаскированы

#### 3.4. MobileAppBuild

```python
# В Django shell
from phonebridge.models import MobileAppBuild
builds = MobileAppBuild.objects.filter(is_active=True)
for b in builds:
    print(f"{b.version_name} ({b.version_code}): {b.file.name}, SHA256: {b.sha256[:16]}...")
```

**Ожидаемый результат:**
- APK файлы загружены
- SHA256 вычислен
- Файлы доступны в `media/mobile_apps/`

#### 3.5. MobileAppQrToken

```python
# В Django shell
from phonebridge.models import MobileAppQrToken
from django.utils import timezone
tokens = MobileAppQrToken.objects.order_by('-created_at')[:10]
for t in tokens:
    valid = "valid" if t.is_valid() else "expired/used"
    print(f"{t.user.username}: {t.token[:16]}..., expires: {t.expires_at}, {valid}")
```

**Ожидаемый результат:**
- Токены создаются при запросе
- Использованные токены помечены `used_at`
- Истекшие токены не валидны

---

## Ожидаемый результат (критерии успеха)

### ✅ Staging сервер

- [ ] CRM работает без ошибок
- [ ] Страница `/mobile-app/` доступна авторизованным
- [ ] APK загружаются через админку
- [ ] APK скачиваются через `/mobile-app/`
- [ ] QR-токены создаются и обмениваются
- [ ] QR-токены одноразовые и с TTL 5 минут
- [ ] Logout endpoints работают
- [ ] Audit логирует события
- [ ] Нет критических ошибок в логах

### ✅ Android приложение

- [ ] Staging APK собирается успешно
- [ ] APK устанавливается на устройство
- [ ] Вход по QR работает
- [ ] Fallback логин работает
- [ ] Polling работает (204/200)
- [ ] Команды на звонок доставляются
- [ ] ACTION_DIAL открывается корректно
- [ ] Данные о звонке отправляются в CRM
- [ ] Офлайн очередь работает
- [ ] Graceful logout при 401/403

### ✅ CRM функциональность

- [ ] CallRequest создаются и обновляются
- [ ] PhoneDevice регистрируются
- [ ] PhoneTelemetry собирается
- [ ] MobileAppBuild хранятся корректно
- [ ] MobileAppQrToken работают как ожидается

---

## Troubleshooting

### Проблема: QR-код не сканируется

**Возможные причины:**
- Неверный формат токена в QR
- Камера не фокусируется
- Недостаточное освещение

**Решение:**
- Проверить логи Android: `adb logcat | grep QRLogin`
- Убедиться, что QR содержит только токен (не URL)
- Попробовать другой QR-сканер для проверки

### Проблема: 401 после входа

**Возможные причины:**
- Токены не сохраняются
- Refresh token истек
- Неверный BASE_URL

**Решение:**
- Проверить логи: `adb logcat | grep TokenManager`
- Проверить EncryptedSharedPreferences
- Убедиться, что BASE_URL правильный (staging: `http://95.142.47.245`)

### Проблема: APK не скачивается

**Возможные причины:**
- Файл не загружен в media
- Права доступа
- Nginx не раздает media

**Решение:**
- Проверить: `docker compose -f docker-compose.staging.yml exec web ls -la /app/backend/media/mobile_apps/`
- Проверить Nginx конфиг: `/media/` location
- Проверить права на файлы

---

## Дополнительные проверки

### Производительность

- [ ] Страница `/mobile-app/` загружается быстро (< 1 сек)
- [ ] QR-код генерируется быстро (< 500 мс)
- [ ] Polling не создаёт нагрузку (интервал адаптивный)

### Безопасность

- [ ] APK доступны только авторизованным
- [ ] QR-токены одноразовые
- [ ] Токены не логируются в production
- [ ] Чувствительные данные маскируются

### Совместимость

- [ ] Работает на Android 7+ (minSdk 21)
- [ ] Работает на Android 13+ (POST_NOTIFICATIONS)
- [ ] Работает на Android 14+ (foreground service)

---

## Следующие шаги после тестирования

1. Если всё работает — можно деплоить на production
2. Если есть проблемы — зафиксировать в issues и исправить
3. Обновить документацию при необходимости
