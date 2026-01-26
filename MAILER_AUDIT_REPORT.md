# Отчёт аудита: Почтовый сервис рассылок

## 1. Общая оценка

Система в целом хорошо спроектирована и следует большинству архитектурных принципов. Основные улучшения (defer_queue, Redis rate limiter, единый источник правды) реализованы корректно. Однако обнаружены **критические проблемы**, которые могут привести к нарушению лимитов, дублям писем и неконсистентным статусам. После исправления найденных проблем система будет production-ready.

**Основные сильные стороны:**
- Единый сервис defer_queue() реализован правильно
- Redis rate limiter использует атомарные операции (INCR)
- Защита от гонок через select_for_update применена корректно
- Poll endpoint правильно использует CampaignQueue как источник правды

**Критические проблемы:**
- Нарушение инварианта A: прямые вызовы SMTP в views.py
- Нарушение инварианта C: не все паузы используют defer_queue
- Риск превышения rate limit при падении процесса
- Race condition в check_rate_limit_per_hour

## 2. Проверка инвариантов

| Инвариант | Статус | Комментарий |
|-----------|--------|-------------|
| **A) Отправка только через Celery** | ❌ **НЕ ВЫПОЛНЕНО** | В `views.py` строки 240 и 2329: прямые вызовы `send_via_smtp()` в тестовых функциях. Это нарушает архитектурный принцип. |
| **B) Одна кампания в PROCESSING** | ✅ **ВЫПОЛНЕНО** | Защита через Redis lock (строка 370) и `select_for_update(skip_locked=True)` (строка 509). Дополнительно есть reconcile_campaign_queue для исправления аномалий. |
| **C) Все паузы с defer_reason и deferred_until** | ⚠️ **ЧАСТИЧНО** | **Проблемы:**<br>1. Строка 430: outside_hours не использует defer_queue (старая логика)<br>2. Строка 907-910: transient_blocked меняет статус без deferred_until<br>3. Строка 404-408: проверка deferred_until, но не через defer_queue |
| **D) CampaignQueue — единый источник правды** | ✅ **ВЫПОЛНЕНО** | Poll endpoint (строки 1270-1297) правильно берет reason_code и next_run_at из CampaignQueue. Дополнительно проверяет только smtp_disabled. |
| **E) Rate limiting атомарный, не через SendLog** | ⚠️ **ЧАСТИЧНО** | **Проблемы:**<br>1. Строка 594: используется Redis rate limiter ✅<br>2. Строка 34 в rate_limiter.py: `cache.get()` не атомарен — race condition между проверкой и increment<br>3. Строка 824-826: порядок операций — send_via_smtp ДО increment. При падении процесса письмо может быть отправлено, но счетчик не увеличится → риск превышения лимита |
| **F) Квота 15 000/мес корректно учитывается** | ✅ **ВЫПОЛНЕНО** | `get_effective_quota_available()` (rate_limiter.py:94-127) правильно учитывает локальные отправки после last_synced_at. |
| **G) Нет race conditions** | ⚠️ **ЧАСТИЧНО** | **Хорошо:**<br>- Redis lock (строка 370) ✅<br>- select_for_update при взятии кампании (строка 509) ✅<br>- select_for_update при взятии батча (строка 675) ✅<br>**Проблема:**<br>- Race condition в check_rate_limit_per_hour (см. E) |
| **H) Нет дублей писем** | ⚠️ **ЧАСТИЧНО** | **Хорошо:**<br>- select_for_update защищает от параллельной обработки ✅<br>**Проблема:**<br>- При падении процесса после send_via_smtp, но до записи статуса recipient, письмо может быть отправлено повторно (строка 824-830). Нет идемпотентности. |
| **I) Финальный статус честный** | ⚠️ **ЧАСТИЧНО** | **Хорошо:**<br>- Строка 916-925: проверка failed перед завершением ✅<br>**Проблема:**<br>- Строка 416: кампания помечается как SENT без проверки failed (но это в контексте очистки stale очередей, где pending уже нет) |
| **J) Poll endpoint корректный** | ✅ **ВЫПОЛНЕНО** | Все поля присутствуют: active_campaign (1218-1226), queued_count (1229-1233), next_campaign_at (1236-1244), reason_code и next_run_at из CampaignQueue (1270-1297). |

## 3. Проверка edge-cases

### ✅ Граница часа (rate limit)
**Проверено:** Ключ формируется по часу (`mailer:rate:hour:YYYY-MM-DD:HH`), при переходе на новый час создается новый ключ. Логика корректна.

### ⚠️ Падение воркера после отправки
**Проблема:** Если процесс упадет после `send_via_smtp()` (строка 824), но до записи статуса recipient (строка 827), письмо может быть отправлено повторно при ретрае.  
**Риск:** Дубли писем.  
**Статус:** Проблема обнаружена (см. раздел 3, проблема #6).

### ✅ quota_exhausted
**Проверено:** deferred_until всегда выставляется (строка 647), нет "вечного ожидания".

### ✅ Несколько кампаний в очереди
**Проверено:** 
- deferred кампания не блокирует следующую (фильтр `deferred_until__lte=now_atomic` на строке 515)
- Порядок обработки корректный (order_by на строке 517)
- Есть reconcile_campaign_queue для исправления аномалий

### ✅ Уведомления
**Проверено:** Дедупликация реализована в defer_queue (строки 65-67), нет спама одинаковыми уведомлениями.

### ✅ Redis / cache backend
**Проверено:** В production (DEBUG=False) используется Redis (django_redis.cache.RedisCache). В development используется LocMemCache, что нормально.

## 4. Найденные проблемы и риски

### Критические

#### 1. Нарушение инварианта A: прямые вызовы SMTP в views.py
**Файл:** `backend/mailer/views.py`  
**Строки:** 240, 2329  
**Проблема:** Тестовые функции `mail_settings` и `campaign_send_test_email` вызывают `send_via_smtp()` напрямую, минуя Celery task.  
**Риск:** Письма отправляются вне системы лимитов и очереди.  
**Исправление:** Вынести отправку тестовых писем в отдельную Celery task или явно пометить как исключение с комментарием.

#### 2. Нарушение инварианта C: outside_hours не использует defer_queue
**Файл:** `backend/mailer/tasks.py`  
**Строки:** 428-500  
**Проблема:** Старая логика outside_hours не была заменена на defer_queue. Используется прямое обновление через `.update()`, что не фиксирует deferred_until и defer_reason для каждой кампании.  
**Риск:** Кампании откладываются без фиксации причины и времени возобновления.  
**Исправление:** Заменить на цикл с defer_queue для каждой PROCESSING кампании.

#### 3. Риск превышения rate limit при падении процесса
**Файл:** `backend/mailer/tasks.py`  
**Строки:** 824-826  
**Проблема:** Порядок операций: `send_via_smtp()` → `increment_rate_limit_per_hour()`. Если процесс упадет между ними, письмо может быть отправлено, но счетчик не увеличится.  
**Риск:** Превышение лимита 100 писем/час.  
**Исправление:** Увеличивать счетчик ДО отправки (оптимистично) или использовать транзакцию с откатом при ошибке.

#### 4. Race condition в check_rate_limit_per_hour
**Файл:** `backend/mailer/services/rate_limiter.py`  
**Строка:** 34  
**Проблема:** `cache.get()` не атомарен. Между проверкой (строка 34) и increment (строка 70) два воркера могут оба увидеть count < 100 и оба отправить письма.  
**Риск:** Превышение лимита при параллельной обработке.  
**Исправление:** Использовать атомарную операцию (например, Lua script в Redis) или проверять лимит внутри increment.

#### 5. Transient error не фиксирует deferred_until
**Файл:** `backend/mailer/tasks.py`  
**Строки:** 907-910  
**Проблема:** При transient_blocked статус меняется на PENDING без deferred_until и defer_reason.  
**Риск:** Кампания может быть взята в обработку сразу, что приведет к повторной ошибке.  
**Исправление:** Использовать defer_queue с коротким deferred_until (например, +5 минут) или отдельной причиной.

### Средние

#### 6. Нет идемпотентности при падении процесса
**Файл:** `backend/mailer/tasks.py`  
**Строки:** 824-830  
**Проблема:** Если процесс упадет после `send_via_smtp()`, но до записи статуса recipient, письмо может быть отправлено повторно при ретрае.  
**Риск:** Дубли писем.  
**Исправление:** Использовать Message-ID как уникальный ключ для проверки дублей или записывать статус ДО отправки (оптимистично).

#### 7. Завершение кампании без проверки failed (в некоторых местах)
**Файл:** `backend/mailer/tasks.py`  
**Строка:** 416  
**Проблема:** При очистке stale очередей кампания помечается как SENT без проверки failed.  
**Риск:** Кампании с failed получателями могут быть помечены как успешные.  
**Исправление:** Добавить проверку failed перед установкой SENT (как на строке 916).

### Потенциальные

#### 8. Redis только в production
**Файл:** `backend/crm/settings.py`  
**Строки:** 371-394  
**Замечание:** В development используется LocMemCache, что нормально для разработки, но rate limiting не будет работать корректно между процессами.  
**Риск:** Низкий (только в development).  
**Рекомендация:** Добавить предупреждение в лог при использовании LocMemCache в production-подобных сценариях.

## 5. Рекомендации

### Критические исправления (обязательно)

1. **Исправить outside_hours в tasks.py (строка 428-500):**
   ```python
   # Заменить на:
   if not _is_working_hours():
       logger.debug("Outside working hours (9:00-18:00 MSK), deferring campaigns")
       msk_now = timezone.now().astimezone(ZoneInfo("Europe/Moscow"))
       next_start = msk_now.replace(hour=WORKING_HOURS_START, minute=0, second=0, microsecond=0)
       if msk_now.hour >= WORKING_HOURS_END:
           next_start = next_start + timezone.timedelta(days=1)
       
       processing_to_defer = CampaignQueue.objects.filter(
           status=CampaignQueue.Status.PROCESSING,
           campaign__recipients__status=CampaignRecipient.Status.PENDING,
       ).select_related("campaign")
       
       for q in processing_to_defer:
           defer_queue(q, DEFER_REASON_OUTSIDE_HOURS, next_start, notify=True)
       
       return {"processed": False, "campaigns": 0, "reason": "outside_working_hours"}
   ```

2. **Исправить порядок операций в tasks.py (строка 824-826):**
   ```python
   # Изменить на:
   try:
       # Увеличиваем счетчик ДО отправки (оптимистично)
       increment_success, new_count = increment_rate_limit_per_hour(max_per_hour)
       if not increment_success or new_count > max_per_hour:
           # Лимит превышен, откладываем
           defer_queue(queue_entry, DEFER_REASON_RATE_HOUR, rate_reset_at, notify=True)
           break
       
       send_via_smtp(smtp_cfg, msg, smtp=smtp)
       # ... остальной код
   ```

3. **Исправить race condition в rate_limiter.py:**
   ```python
   # В check_rate_limit_per_hour использовать атомарную проверку:
   # Вариант 1: Проверять внутри increment
   # Вариант 2: Использовать Lua script в Redis
   # Вариант 3: Убрать check, проверять только в increment
   ```

4. **Исправить transient_blocked (строка 907-910):**
   ```python
   # Заменить на:
   if transient_blocked:
       from datetime import timedelta
       next_retry = timezone.now() + timedelta(minutes=5)
       defer_queue(queue_entry, "transient_error", next_retry, notify=False)
   ```

### Важные улучшения

5. **Добавить идемпотентность через Message-ID:**
   - Перед отправкой проверять, не было ли уже отправлено письмо с таким Message-ID
   - Или записывать статус SENT ДО отправки (оптимистично)

6. **Добавить проверку failed при очистке stale очередей (строка 416):**
   ```python
   if camp and camp.status in (Campaign.Status.READY, Campaign.Status.SENDING):
       has_failed = camp.recipients.filter(status=CampaignRecipient.Status.FAILED).exists()
       if not has_failed:
           camp.status = Campaign.Status.SENT
       camp.save(update_fields=["status", "updated_at"])
   ```

### Опциональные улучшения

7. **Пометить тестовые функции в views.py:**
   - Добавить комментарий, что это исключение из правила "только через Celery"
   - Или вынести в отдельный модуль test_utils

8. **Добавить мониторинг:**
   - Логировать случаи превышения rate limit
   - Алерты при обнаружении нескольких PROCESSING кампаний

## 6. КРАТКОЕ РЕЗЮМЕ

Почтовый сервис рассылок в целом хорошо спроектирован: единый сервис defer_queue, атомарный Redis rate limiter, защита от гонок через select_for_update, poll endpoint использует CampaignQueue как источник правды. Однако обнаружены **5 критических проблем**, которые необходимо исправить перед production:

1. **Прямые вызовы SMTP в views.py** — нарушение архитектурного принципа (тестовые функции).
2. **outside_hours не использует defer_queue** — старая логика не была заменена, кампании откладываются без фиксации причины.
3. **Риск превышения rate limit** — неправильный порядок операций при отправке может привести к превышению лимита при падении процесса.
4. **Race condition в rate limiter** — неатомарная проверка лимита может привести к превышению при параллельной обработке.
5. **Transient error не фиксирует deferred_until** — кампании могут быть взяты в обработку сразу после ошибки.

После исправления этих проблем система будет production-ready и надежно соблюдать все лимиты и инварианты.
