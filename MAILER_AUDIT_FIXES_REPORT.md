# Отчёт: Исправление критических проблем из аудита

## 1. Список исправленных критических проблем

### ✅ (1) Прямые вызовы SMTP в views.py
**Файл:** `backend/mailer/views.py` (строки ~240, ~2329)  
**Исправление:**
- Создана Celery task `send_test_email` в `backend/mailer/tasks.py` (строки 1059-1167)
- Заменены прямые вызовы `send_via_smtp()` на `send_test_email.delay()`
- Task использует `reserve_rate_limit_token()` для соблюдения лимитов
- Тестовые письма теперь проходят через ту же систему лимитов, что и обычные рассылки

**Изменённые места:**
- `backend/mailer/views.py:235-244` - функция `mail_settings` (тест SMTP настроек)
- `backend/mailer/views.py:2325-2345` - функция отправки тестового письма кампании
- `backend/mailer/tasks.py:1059-1167` - новая Celery task `send_test_email`

### ✅ (2) outside_hours не использует defer_queue
**Файл:** `backend/mailer/tasks.py` (строки 428-446)  
**Исправление:**
- Удалена старая логика с прямым `.update()` и ручными уведомлениями
- Заменена на цикл с `defer_queue()` для каждой PROCESSING кампании
- Теперь все кампании получают `defer_reason=outside_hours` и `deferred_until=next_work_start`

**Изменённые места:**
- `backend/mailer/tasks.py:428-446` - блок outside_hours теперь использует defer_queue

### ✅ (3) Порядок операций для rate limit: reserve → send → commit
**Файл:** `backend/mailer/tasks.py` (строки ~825-840), `backend/mailer/services/rate_limiter.py`  
**Исправление:**
- Создана функция `reserve_rate_limit_token()` в `rate_limiter.py` (строки 14-79)
- Функция атомарно резервирует токен ДО отправки (INCR + проверка лимита)
- Если лимит превышен, токен откатывается (DECR)
- В `tasks.py` порядок изменен: `reserve_token()` → `send_via_smtp()` → токен уже засчитан

**Изменённые места:**
- `backend/mailer/services/rate_limiter.py:14-79` - новая функция `reserve_rate_limit_token()`
- `backend/mailer/tasks.py:825-840` - порядок операций: reserve → send

### ✅ (4) Race condition в check_rate_limit_per_hour
**Файл:** `backend/mailer/services/rate_limiter.py`  
**Исправление:**
- `check_rate_limit_per_hour()` помечена как DEPRECATED
- Вместо неё используется `reserve_rate_limit_token()`, которая атомарно проверяет и резервирует
- Убрана двухшаговая схема get()+increment, теперь одна атомарная операция INCR + проверка

**Изменённые места:**
- `backend/mailer/services/rate_limiter.py:14-79` - `reserve_rate_limit_token()` (атомарная)
- `backend/mailer/services/rate_limiter.py:81-107` - `check_rate_limit_per_hour()` помечена как DEPRECATED
- `backend/mailer/tasks.py` - все вызовы заменены на `reserve_rate_limit_token()`

### ✅ (5) transient_blocked не фиксирует deferred_until
**Файл:** `backend/mailer/tasks.py` (строки ~904-910)  
**Исправление:**
- Заменено прямое обновление статуса на `defer_queue()` с `defer_reason="transient_error"`
- `deferred_until` устанавливается на `now + 5 минут`
- Добавлена константа `DEFER_REASON_TRANSIENT_ERROR` в `constants.py`

**Изменённые места:**
- `backend/mailer/constants.py:22` - добавлена `DEFER_REASON_TRANSIENT_ERROR`
- `backend/mailer/services/queue.py` - добавлена поддержка `transient_error` в reason_texts
- `backend/mailer/tasks.py:904-910` - transient_blocked использует defer_queue
- `backend/mailer/views.py:1289-1294` - poll endpoint поддерживает transient_error

## 2. Список изменённых файлов

### Новые функции/изменения:

1. **backend/mailer/tasks.py**
   - Добавлена Celery task `send_test_email()` (строки 1059-1167)
   - Исправлен блок outside_hours: использует defer_queue (строки 428-446)
   - Исправлен порядок операций rate limit: reserve → send (строки 825-840)
   - Исправлен transient_blocked: использует defer_queue (строки 904-910)
   - Обновлены импорты: добавлен `reserve_rate_limit_token`, `DEFER_REASON_TRANSIENT_ERROR`

2. **backend/mailer/services/rate_limiter.py**
   - Добавлена функция `reserve_rate_limit_token()` (строки 14-79) - атомарная резервация токена
   - `check_rate_limit_per_hour()` помечена как DEPRECATED (строки 81-107)

3. **backend/mailer/constants.py**
   - Добавлена константа `DEFER_REASON_TRANSIENT_ERROR = "transient_error"` (строка 22)
   - Обновлен `DEFER_REASONS` для включения transient_error

4. **backend/mailer/services/queue.py**
   - Добавлена поддержка `DEFER_REASON_TRANSIENT_ERROR` в reason_texts

5. **backend/mailer/views.py**
   - Заменены прямые вызовы `send_via_smtp()` на `send_test_email.delay()` (строки 235-244, 2325-2345)
   - Удален импорт `send_via_smtp` (строка 31)
   - Обновлен poll endpoint: добавлена поддержка transient_error (строка 1294)

6. **backend/mailer/tests.py**
   - Обновлены тесты rate limiter: используют `reserve_rate_limit_token()` вместо `increment_rate_limit_per_hour()`
   - Добавлены тесты для outside_hours: `MailerOutsideHoursDeferTests`
   - Добавлены тесты для transient_error: `MailerTransientErrorDeferTests`
   - Добавлены тесты для send_test_email: `MailerTestEmailTaskTests`

## 3. Короткий чеклист ручной проверки

### Проверка 1: Тестовые письма через Celery
1. Зайти в Почта → Настройки
2. Нажать "Отправить тестовое письмо"
3. Проверить, что письмо отправлено
4. Проверить в логах Celery, что task `send_test_email` выполнилась
5. Проверить, что `send_via_smtp` не вызывается напрямую из views (нет в логах web-процесса)

### Проверка 2: outside_hours использует defer_queue
1. Установить системное время вне рабочего времени (например, 20:00 МСК)
2. Создать кампанию с получателями
3. Запустить рассылку (или дождаться автоматического запуска)
4. Проверить CampaignQueue: `defer_reason = "outside_hours"`, `deferred_until = следующий день 09:00 МСК`
5. Проверить poll endpoint: `reason_code = "outside_hours"`, `next_run_at` указан

### Проверка 3: Rate limit reserve → send
1. Создать кампанию с 150 получателями
2. Запустить рассылку
3. Проверить в логах, что `reserve_rate_limit_token()` вызывается ДО `send_via_smtp()`
4. После 100 писем проверить, что кампания откладывается
5. Проверить CampaignQueue: `defer_reason = "rate_per_hour"`, `deferred_until = начало следующего часа`

### Проверка 4: Атомарность rate limit
1. Запустить два celery worker одновременно
2. Создать кампанию с 150 получателями
3. Проверить, что отправлено ровно 100 писем (не больше)
4. Проверить, что нет превышения лимита в Redis: `redis-cli GET "crm:mailer:rate:hour:YYYY-MM-DD:HH"` должно быть ≤ 100

### Проверка 5: Transient error использует defer_queue
1. Имитировать временную SMTP ошибку (например, временно отключить SMTP сервер)
2. Запустить рассылку
3. Проверить CampaignQueue: `defer_reason = "transient_error"`, `deferred_until = now + 5 минут`
4. Проверить poll endpoint: `reason_code = "transient_error"`, `next_run_at` указан

### Проверка 6: Тесты проходят
1. Запустить: `python manage.py test mailer.tests.MailerOutsideHoursDeferTests`
2. Запустить: `python manage.py test mailer.tests.MailerTransientErrorDeferTests`
3. Запустить: `python manage.py test mailer.tests.MailerTestEmailTaskTests`
4. Запустить: `python manage.py test mailer.tests.MailerRateLimiterTests`
5. Все тесты должны пройти успешно

## 4. КРАТКОЕ РЕЗЮМЕ

Исправлены все 5 критических проблем из аудита почтового сервиса рассылок. Тестовые письма теперь отправляются через Celery task с соблюдением лимитов. Outside_hours и transient_error используют единый сервис defer_queue для фиксации причин пауз. Rate limiting переведен на атомарную схему резервации токенов (reserve → send), что исключает превышение лимита при падениях процесса и race conditions. Все изменения минимально-инвазивны, используют существующую архитектуру и покрыты тестами. Система готова к production.
