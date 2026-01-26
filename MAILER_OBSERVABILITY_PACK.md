# Mailer Observability Pack

Минимальный набор для мониторинга и observability почтового сервиса рассылок.

## 1. События и ключевые поля

### Успешная отправка письма

**Message:** `"Email sent successfully"`  
**Level:** `INFO`

**Ключевые поля:**
- `campaign_id` — UUID кампании
- `queue_id` — UUID записи в очереди
- `recipient_id` — UUID получателя
- `email_domain` — домен email (PII-safe)
- `email_masked` — маскированный email (PII-safe)
- `email_hash` — хэш email (PII-safe)
- `smtp_message_id` — Message-ID письма
- `provider` — провайдер ("smtp_global")
- `took_ms` — время отправки в миллисекундах
- `rate_limit_count` — текущий счётчик rate limit

### Ошибка отправки письма

**Message:** `"Failed to send email {masked}: {error}"`  
**Level:** `ERROR`

**Ключевые поля:**
- `campaign_id` — UUID кампании
- `recipient_id` — UUID получателя
- `email_domain`, `email_masked`, `email_hash` — PII-safe представление email
- `error_type` — тип ошибки ("smtp_error")
- `exception` — полный stack trace (если есть)

### Завершение кампании

**Message:** `"Campaign finished"`  
**Level:** `INFO`

**Ключевые поля:**
- `campaign_id` — UUID кампании
- `queue_id` — UUID записи в очереди
- `campaign_status` — финальный статус ("SENT", "SENDING")
- `totals: {sent, failed, total}` — итоги кампании
- `duration_seconds` — длительность кампании (секунды)
- `finished_with_errors` — были ли ошибки (true/false)

### Отложение кампании (defer)

**Message:** `"Campaign deferred"` или содержит `"deferring"`  
**Level:** `INFO` / `WARNING`

**Ключевые поля:**
- `campaign_id` — UUID кампании
- `queue_id` — UUID записи в очереди
- `defer_reason` — причина ("daily_limit", "quota_exhausted", "outside_hours", "rate_per_hour", "transient_error")
- `deferred_until` — когда продолжится (ISO datetime)

### Rate limit reserve

**Message:** `"Rate limit token reserve"`  
**Level:** `DEBUG`

**Ключевые поля:**
- `campaign_id` — UUID кампании
- `allowed` — разрешена ли отправка (true/false)
- `current_count` — текущий счётчик
- `max_per_hour` — лимит (обычно 100)
- `key_hour` — ключ часа ("YYYY-MM-DD:HH")

### Rate limiter ошибка (Redis)

**Message:** `"Error reserving rate limit token (Redis unavailable), allowing send (fail-open)"`  
**Level:** `ERROR`

**Ключевые поля:**
- `error_type` — "rate_limiter_backend_error"
- `policy` — "fail_open"

### Throttle ошибка (Redis)

**Message:** `"Throttle backend (Redis) unavailable for action {action}, user {user_id}"`  
**Level:** `ERROR`

**Ключевые поля:**
- `user_id` — ID пользователя
- `action` — действие ("campaign_start", "send_test_email")
- `error_type` — "throttle_backend_error"

### Circuit breaker сработал

**Message:** `"Campaign {id}: too many transient errors ({count}), pausing"`  
**Level:** `ERROR`

**Ключевые поля:**
- `campaign_id` — UUID кампании
- `queue_id` — UUID записи в очереди
- `consecutive_errors` — количество последовательных ошибок

### Зависшие PROCESSING кампании

**Message:** `"Stuck PROCESSING campaigns detected: {ids}"`  
**Level:** `WARNING`

**Ключевые поля:**
- `stuck_count` — количество зависших кампаний
- `campaign_ids` — список UUID кампаний
- `threshold_minutes` — порог (обычно 30)

## 2. Готовые фильтры/поисковые запросы

### "Campaign deferred"

**ELK/Grafana Loki:**
```
message:"Campaign deferred" OR message:"deferring"
```

**grep:**
```bash
grep -E "(Campaign deferred|deferring)" logs/crm.log
```

**Поля для группировки:**
- `defer_reason` — причина отложения
- `campaign_id` — для трейсинга конкретной кампании

### "Rate limiter errors"

**ELK/Grafana Loki:**
```
level:ERROR AND (message:"Error reserving rate limit token" OR error_type:"rate_limiter_backend_error")
```

**grep:**
```bash
grep "Error reserving rate limit token" logs/crm.log
```

**Метрика:** COUNT за последний час → alert если > 0

### "Stuck processing"

**ELK/Grafana Loki:**
```
level:WARNING AND message:"Stuck PROCESSING campaigns detected"
```

**grep:**
```bash
grep "Stuck PROCESSING campaigns detected" logs/crm.log
```

**Метрика:** COUNT за последние 5 минут → alert если > 0

### "Test email blocked by throttle"

**ELK/Grafana Loki:**
```
level:WARNING AND message:"throttled" AND action:"send_test_email"
```

**grep:**
```bash
grep "throttled.*send_test_email" logs/crm.log
```

### "Email sent successfully"

**ELK/Grafana Loki:**
```
level:INFO AND message:"Email sent successfully"
```

**Метрика:** COUNT за последний час → `emails_sent_per_hour`

### "Campaign finished"

**ELK/Grafana Loki:**
```
level:INFO AND message:"Campaign finished"
```

**Метрика:** 
- COUNT WHERE `finished_with_errors=false` → успешные кампании
- COUNT WHERE `finished_with_errors=true` → кампании с ошибками

## 3. Рекомендуемые алёрты

### Критические

1. **Redis errors в rate limiter**
   - Условие: `level=ERROR AND error_type="rate_limiter_backend_error"`
   - Частота: немедленно
   - Действие: Проверить доступность Redis, восстановить при необходимости

2. **PROCESSING stuck > 30 минут**
   - Условие: `level=WARNING AND message="Stuck PROCESSING campaigns detected"`
   - Частота: немедленно
   - Действие: Запустить `reconcile_campaign_queue` или перезапустить воркер

3. **Circuit breaker сработал**
   - Условие: `level=ERROR AND message содержит "too many transient errors"`
   - Частота: немедленно
   - Действие: Проверить SMTP доступность, исправить проблему

### Предупреждающие

4. **Queue depth > 50**
   - Условие: COUNT(`CampaignQueue.status='pending'`) > 50
   - Частота: каждые 5 минут
   - Действие: Проверить причины пауз, возможно увеличить пропускную способность

5. **Quota exhausted длительное время**
   - Условие: `SmtpBzQuota.emails_available <= 0 AND last_synced_at < NOW() - 2 hours`
   - Частота: каждые 30 минут
   - Действие: Проверить sync task, пополнить квоту

6. **Throttle backend errors**
   - Условие: `level=ERROR AND error_type="throttle_backend_error"`
   - Частота: немедленно
   - Действие: Проверить доступность Redis

## 4. SLO-like метрики

### 1. % кампаний завершённых без ошибок за сутки

**Формула:**
```
COUNT(campaigns_finished WHERE finished_with_errors=false) / COUNT(campaigns_finished) * 100
```

**Целевое значение:** >= 95%

**Источник:** Логи с `message="Campaign finished"`, поле `finished_with_errors`

### 2. 99-й перцентиль времени от READY до первого SEND

**Формула:**
```
P99(time_first_send - time_ready)
```

**Целевое значение:** < 5 минут

**Источник:** 
- `CampaignQueue.queued_at` (время READY)
- Первый `SendLog.created_at` для кампании (время первого SEND)

### 3. Queue depth (глубина очереди)

**Формула:**
```
COUNT(CampaignQueue WHERE status='pending')
```

**Целевое значение:** < 20 (норма), < 50 (предупреждение)

**Источник:** `CampaignQueue.status='pending'`

### Дополнительные метрики (опционально)

4. **Emails sent per hour**
   - Формула: COUNT(`message="Email sent successfully"`) WHERE `timestamp >= NOW() - 1 hour`
   - Целевое значение: зависит от лимита (обычно <= 100)

5. **Top defer reasons**
   - Формула: GROUP BY `defer_reason` COUNT(*) ORDER BY COUNT DESC
   - Использование: понять основные причины пауз

6. **Average campaign duration**
   - Формула: AVG(`duration_seconds`) WHERE `finished_with_errors=false`
   - Целевое значение: зависит от размера кампаний

## 5. Операционные проверки

### Ежедневные проверки

1. **Queue depth:** должно быть < 20
2. **Stuck campaigns:** должно быть 0
3. **Quota sync:** `last_synced_at` должен быть < 2 часов назад

### Еженедельные проверки

1. **SLO метрики:** проверить % успешных кампаний, P99 время
2. **Top defer reasons:** понять основные причины пауз
3. **Redis errors:** проверить логи на ошибки Redis

### При инциденте

1. Открыть `MAILER_ONCALL_PLAYBOOK.md`
2. Выполнить "Быстрый диагноз за 60 секунд"
3. Найти симптом в разделе "Частые симптомы"
4. Выполнить действия из runbook

## 6. Интеграция с системами мониторинга

### ELK Stack

**Index pattern:** `crm-mailer-*` или `crm-*`

**Пример запроса:**
```json
{
  "query": {
    "bool": {
      "must": [
        {"match": {"message": "Email sent successfully"}},
        {"range": {"timestamp": {"gte": "now-1h"}}}
      ]
    }
  }
}
```

### Grafana Loki

**LogQL:**
```
{app="crm"} |= "Email sent successfully" | json | timestamp >= now() - 1h
```

### Prometheus (если добавить экспорт метрик)

**Пример метрик:**
- `mailer_emails_sent_total{status="success"}` — counter
- `mailer_campaigns_finished_total{with_errors="false"}` — counter
- `mailer_queue_depth` — gauge
- `mailer_rate_limit_usage{hour="2026-01-26:14"}` — gauge

## 7. Примеры дашбордов

### Основной дашборд

1. **Queue depth** (gauge)
2. **Emails sent per hour** (line chart, последние 24 часа)
3. **Campaigns finished** (pie chart: успешные vs с ошибками)
4. **Top defer reasons** (bar chart)
5. **Redis errors** (alert panel)

### Детальный дашборд кампании

1. **Campaign timeline** (от READY до SENT)
2. **Emails sent/failed** (по времени)
3. **Defer events** (timeline с причинами)
4. **Rate limit usage** (по часам)
