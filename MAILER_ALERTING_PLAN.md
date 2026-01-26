# Mailer Alerting Plan

Операционный план настройки алёртов для почтового сервиса рассылок.

## 1. Критические алёрты

### ALERT-001: Redis errors в rate limiter

**Имя:** `mailer_rate_limiter_redis_error`  
**Severity:** `critical`  
**Trigger Condition:** Появление ERROR лога с `error_type="rate_limiter_backend_error"`  
**Порог:** >= 1 событие за 1 минуту  
**Окно:** 1 минута  

**Источник данных:**
- **Логи:** `level=ERROR AND error_type="rate_limiter_backend_error" AND policy="fail_open"`
- **Message pattern:** `"Error reserving rate limit token (Redis unavailable), allowing send (fail-open)"`

**Action / Runbook:**
- См. раздел "Redis недоступен" в `MAILER_ONCALL_PLAYBOOK.md`
- Проверить доступность Redis: `redis-cli PING`
- Перезапустить Redis при необходимости: `docker restart redis`
- Проверить логи на детали ошибки: `docker logs celery_worker | grep "rate_limiter_backend_error"`

---

### ALERT-002: PROCESSING stuck > 30 минут

**Имя:** `mailer_processing_stuck`  
**Severity:** `critical`  
**Trigger Condition:** Кампания в статусе PROCESSING более 30 минут  
**Порог:** >= 1 кампания  
**Окно:** 5 минут (проверка каждые 5 минут)  

**Источник данных:**
- **SQL запрос:**
  ```sql
  SELECT COUNT(*) as stuck_count
  FROM mailer_campaignqueue
  WHERE status = 'processing'
    AND started_at < NOW() - INTERVAL '30 minutes';
  ```
- **Логи:** `level=WARNING AND message="Stuck PROCESSING campaigns detected"`

**Action / Runbook:**
- См. раздел "PROCESSING > 30 минут" в `MAILER_ONCALL_PLAYBOOK.md`
- Проверить статус воркеров: `celery -A crm inspect active`
- Запустить cleanup: `reconcile_campaign_queue.delay()`
- При необходимости перезапустить воркер: `docker restart celery_worker`

---

### ALERT-003: Circuit breaker сработал

**Имя:** `mailer_circuit_breaker_triggered`  
**Severity:** `critical`  
**Trigger Condition:** Кампания автоматически паузирована из-за множественных transient ошибок  
**Порог:** >= 1 событие за 5 минут  
**Окно:** 5 минут  

**Источник данных:**
- **SQL запрос:**
  ```sql
  SELECT COUNT(*) as circuit_breaker_count
  FROM mailer_campaignqueue
  WHERE consecutive_transient_errors >= (
    SELECT CAST(value AS INTEGER) 
    FROM django_settings 
    WHERE key = 'MAILER_CIRCUIT_BREAKER_THRESHOLD'
  ) OR consecutive_transient_errors >= 10;
  ```
- **Логи:** `level=ERROR AND message содержит "too many transient errors" AND consecutive_errors >= 10`

**Action / Runbook:**
- См. раздел "transient_error" в `MAILER_ONCALL_PLAYBOOK.md`
- Проверить SMTP доступность (smtp.bz)
- Проверить SMTP настройки в `GlobalMailAccount`
- После исправления: вручную возобновить кампанию через UI или SQL

---

## 2. Предупреждающие алёрты

### ALERT-004: Queue depth > 50

**Имя:** `mailer_queue_depth_high`  
**Severity:** `warning`  
**Trigger Condition:** Количество кампаний в очереди (PENDING) превышает 50  
**Порог:** > 50 кампаний  
**Окно:** 5 минут (проверка каждые 5 минут)  

**Источник данных:**
- **SQL запрос:**
  ```sql
  SELECT COUNT(*) as queue_depth
  FROM mailer_campaignqueue
  WHERE status = 'pending';
  ```

**Action / Runbook:**
- См. раздел "Топ причин пауз" в `MAILER_ONCALL_PLAYBOOK.md` (SQL запрос)
- Проверить причины пауз: выполнить SQL запрос "Топ причин пауз"
- Возможно увеличить пропускную способность или устранить блокирующие причины

---

### ALERT-005: Quota exhausted длительное время

**Имя:** `mailer_quota_exhausted_long`  
**Severity:** `warning`  
**Trigger Condition:** Квота исчерпана и не синхронизировалась более 2 часов  
**Порог:** `emails_available <= 0 AND last_synced_at < NOW() - 2 hours`  
**Окно:** 30 минут (проверка каждые 30 минут)  

**Источник данных:**
- **SQL запрос:**
  ```sql
  SELECT 
    emails_available,
    last_synced_at,
    sync_error
  FROM mailer_smtpbzquota
  ORDER BY id DESC
  LIMIT 1;
  ```
- **Условие:** `emails_available <= 0 AND last_synced_at < NOW() - INTERVAL '2 hours'`

**Action / Runbook:**
- См. раздел "quota_exhausted" в `MAILER_ONCALL_PLAYBOOK.md`
- Проверить, что `sync_smtp_bz_quota` task работает
- Проверить `GlobalMailAccount.smtp_bz_api_key`
- Запустить sync вручную: `sync_smtp_bz_quota.delay()`
- Пополнить квоту в smtp.bz при необходимости

---

### ALERT-006: Throttle backend errors

**Имя:** `mailer_throttle_backend_error`  
**Severity:** `warning`  
**Trigger Condition:** Ошибка Redis в throttle helper (fail-closed)  
**Порог:** >= 1 событие за 5 минут  
**Окно:** 5 минут  

**Источник данных:**
- **Логи:** `level=ERROR AND error_type="throttle_backend_error"`
- **Message pattern:** `"Throttle backend (Redis) unavailable for action"`

**Action / Runbook:**
- См. раздел "Redis недоступен" в `MAILER_ONCALL_PLAYBOOK.md`
- Проверить доступность Redis: `redis-cli PING`
- Перезапустить Redis при необходимости: `docker restart redis`
- Проверить логи на детали ошибки

---

### ALERT-007: Celery beat не работает

**Имя:** `mailer_celery_beat_down`  
**Severity:** `warning`  
**Trigger Condition:** Нет логов от Celery beat за последние 5 минут  
**Порог:** Нет логов за 5 минут  
**Окно:** 5 минут  

**Источник данных:**
- **Логи:** Отсутствие логов от `celery_beat` контейнера
- **Проверка:** `docker logs celery_beat --tail 100 | grep "beat:"` (должны быть регулярные записи)

**Action / Runbook:**
- См. раздел "Как проверить, что Celery beat работает" в `MAILER_ONCALL_PLAYBOOK.md`
- Проверить статус контейнера: `docker ps | grep celery_beat`
- Перезапустить beat: `docker restart celery_beat`
- Проверить scheduled tasks: `celery -A crm inspect scheduled`

---

## 3. Как внедрять

### Вариант A: Grafana Loki (LogQL)

**Примеры запросов:**

**ALERT-001 (Redis errors в rate limiter):**
```logql
{app="crm"} 
  |= "Error reserving rate limit token" 
  | json 
  | error_type="rate_limiter_backend_error"
```

**ALERT-002 (PROCESSING stuck):**
```logql
{app="crm"} 
  |= "Stuck PROCESSING campaigns detected" 
  | json
```

**ALERT-003 (Circuit breaker):**
```logql
{app="crm"} 
  |= "too many transient errors" 
  | json 
  | consecutive_errors >= 10
```

**ALERT-006 (Throttle backend errors):**
```logql
{app="crm"} 
  | json 
  | error_type="throttle_backend_error"
```

**Настройка алёрта в Grafana:**
1. Создать Alert Rule в Grafana
2. Query: использовать LogQL выше
3. Condition: `count_over_time(...[1m]) > 0` для критических, `> 0` для предупреждающих
4. Notification: настроить канал (Slack, email, PagerDuty)

---

### Вариант B: ELK Stack (KQL)

**Примеры запросов:**

**ALERT-001:**
```kql
level:ERROR AND error_type:"rate_limiter_backend_error" AND policy:"fail_open"
```

**ALERT-002:**
```kql
level:WARNING AND message:"Stuck PROCESSING campaigns detected"
```

**ALERT-003:**
```kql
level:ERROR AND message:*"too many transient errors"* AND consecutive_errors:>=10
```

**ALERT-006:**
```kql
level:ERROR AND error_type:"throttle_backend_error"
```

**Настройка Watcher в Elasticsearch:**
1. Создать Watcher в Elasticsearch
2. Input: использовать KQL запрос выше
3. Condition: `ctx.payload.hits.total > 0`
4. Actions: отправить в Slack/email/webhook

---

### Вариант C: Sentry (если используется)

**Настройка алёртов в Sentry:**

1. **ALERT-001, ALERT-003, ALERT-006:** Создать Issue Alert на основе:
   - **Tags:** `error_type=rate_limiter_backend_error` или `error_type=throttle_backend_error`
   - **Message contains:** `"too many transient errors"` для circuit breaker
   - **Frequency:** немедленно

2. **ALERT-002:** Создать Issue Alert на основе:
   - **Message contains:** `"Stuck PROCESSING campaigns detected"`
   - **Level:** WARNING
   - **Frequency:** немедленно

**Пример настройки Sentry Alert:**
```
Conditions:
  - The issue's level is equal to ERROR
  - The issue's tags contain error_type=rate_limiter_backend_error
Actions:
  - Send notification to Slack channel #alerts
  - Send email to oncall@example.com
```

---

### Вариант D: Минимум (cron SQL checks)

Если нет системы мониторинга логов, можно использовать простые SQL проверки через cron.

**Скрипт:** `scripts/check_mailer_alerts.sh`

```bash
#!/bin/bash
# Проверка критических алёртов через SQL

# ALERT-002: PROCESSING stuck
STUCK_COUNT=$(psql -U postgres -d crm -t -c "
  SELECT COUNT(*) 
  FROM mailer_campaignqueue 
  WHERE status = 'processing' 
    AND started_at < NOW() - INTERVAL '30 minutes';
")

if [ "$STUCK_COUNT" -gt 0 ]; then
  echo "ALERT: $STUCK_COUNT campaigns stuck in PROCESSING > 30 minutes"
  # Отправить email/уведомление
fi

# ALERT-003: Circuit breaker
CIRCUIT_BREAKER_COUNT=$(psql -U postgres -d crm -t -c "
  SELECT COUNT(*) 
  FROM mailer_campaignqueue 
  WHERE consecutive_transient_errors >= 10;
")

if [ "$CIRCUIT_BREAKER_COUNT" -gt 0 ]; then
  echo "ALERT: $CIRCUIT_BREAKER_COUNT campaigns triggered circuit breaker"
  # Отправить email/уведомление
fi

# ALERT-004: Queue depth
QUEUE_DEPTH=$(psql -U postgres -d crm -t -c "
  SELECT COUNT(*) 
  FROM mailer_campaignqueue 
  WHERE status = 'pending';
")

if [ "$QUEUE_DEPTH" -gt 50 ]; then
  echo "WARNING: Queue depth is $QUEUE_DEPTH (threshold: 50)"
  # Отправить email/уведомление
fi

# ALERT-005: Quota exhausted
QUOTA_CHECK=$(psql -U postgres -d crm -t -c "
  SELECT 
    CASE 
      WHEN emails_available <= 0 AND last_synced_at < NOW() - INTERVAL '2 hours' 
      THEN 1 
      ELSE 0 
    END
  FROM mailer_smtpbzquota 
  ORDER BY id DESC 
  LIMIT 1;
")

if [ "$QUOTA_CHECK" -eq 1 ]; then
  echo "WARNING: Quota exhausted and not synced for > 2 hours"
  # Отправить email/уведомление
fi
```

**Настройка cron:**
```crontab
# Проверка критических алёртов каждые 5 минут
*/5 * * * * /path/to/scripts/check_mailer_alerts.sh

# Проверка предупреждающих алёртов каждые 30 минут
*/30 * * * * /path/to/scripts/check_mailer_alerts.sh --warnings-only
```

---

## 4. Приоритеты внедрения

**Фаза 1 (критично, сразу):**
- ALERT-002: PROCESSING stuck (SQL check)
- ALERT-003: Circuit breaker (SQL check)

**Фаза 2 (важно, в течение недели):**
- ALERT-001: Redis errors в rate limiter (логи)
- ALERT-006: Throttle backend errors (логи)

**Фаза 3 (желательно, в течение месяца):**
- ALERT-004: Queue depth (SQL check)
- ALERT-005: Quota exhausted (SQL check)
- ALERT-007: Celery beat (логи/health check)

---

## 5. Дополнительные метрики для мониторинга

**SLO метрики (см. `MAILER_OBSERVABILITY_PACK.md`):**
- % кампаний завершённых без ошибок за сутки (цель: >= 95%)
- 99-й перцентиль времени от READY до первого SEND (цель: < 5 минут)
- Queue depth (цель: < 20 норма, < 50 предупреждение)

**Источники:**
- Логи: `message="Campaign finished"`, `message="Email sent successfully"`
- SQL: `CampaignQueue`, `SendLog`, `Campaign`

---

## 6. Контакты и эскалация

**On-call инженер:** См. текущий on-call ростер  
**Документация:** `MAILER_ONCALL_PLAYBOOK.md`  
**Observability:** `MAILER_OBSERVABILITY_PACK.md`
