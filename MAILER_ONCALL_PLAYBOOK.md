# Mailer On-Call Playbook

Операционное руководство для диагностики и решения проблем почтового сервиса рассылок.

## Schema Assumptions

**Основные таблицы:**
- `mailer_campaign` — кампании рассылок
- `mailer_campaignqueue` — очередь кампаний (OneToOne с campaign)
- `mailer_campaignrecipient` — получатели кампаний (ForeignKey к campaign)
- `mailer_sendlog` — логи отправки (ForeignKey к campaign и recipient)
- `mailer_smtpbzquota` — квота smtp.bz (singleton, обычно id=1)
- `mailer_globalmailaccount` — глобальные SMTP настройки (singleton, обычно id=1)

**Связи:**
- `CampaignQueue.campaign_id` → `Campaign.id` (OneToOne)
- `CampaignRecipient.campaign_id` → `Campaign.id` (ForeignKey)
- `SendLog.campaign_id` → `Campaign.id` (ForeignKey)
- `SendLog.recipient_id` → `CampaignRecipient.id` (ForeignKey, nullable)
- `Campaign.created_by_id` → `accounts_user.id` (ForeignKey, nullable)

**Конфигурируемые лимиты (через Django settings/env):**
- `MAILER_MAX_CAMPAIGN_RECIPIENTS` (default: 10000)
- `MAILER_THROTTLE_CAMPAIGN_START_PER_HOUR` (default: 10)
- `MAILER_THROTTLE_TEST_EMAIL_PER_HOUR` (default: 5)
- `MAILER_CIRCUIT_BREAKER_THRESHOLD` (default: 10)
- `MAILER_TRANSIENT_RETRY_DELAY_MINUTES` (default: 5)

## 1. Быстрый диагноз за 60 секунд

### Что смотреть в CampaignQueue

```sql
-- Проверить активные кампании и причины пауз
SELECT 
    id,
    campaign_id,
    status,
    defer_reason,
    deferred_until,
    consecutive_transient_errors,
    started_at,
    queued_at
FROM mailer_campaignqueue
WHERE status IN ('pending', 'processing')
ORDER BY queued_at;
```

**Ключевые поля:**
- `status`: `pending` = в очереди, `processing` = отправляется
- `defer_reason`: причина паузы (см. ниже)
- `deferred_until`: когда продолжится автоматически
- `consecutive_transient_errors`: если >= `MAILER_CIRCUIT_BREAKER_THRESHOLD` (default: 10), кампания автоматически паузится

### Что смотреть в SmtpBzQuota

```sql
-- Проверить квоту
SELECT 
    emails_available,
    emails_limit,
    last_synced_at,
    sync_error
FROM mailer_smtpbzquota
ORDER BY id DESC
LIMIT 1;
```

**Ключевые поля:**
- `emails_available`: доступно писем (если <= 0, рассылки откладываются)
- `last_synced_at`: когда последний раз синхронизировалась квота
- `sync_error`: ошибка синхронизации (если есть)

### Что смотреть в Redis

```bash
# Проверить rate limiter (текущий час)
redis-cli GET "crm:mailer:rate:hour:2026-01-26:14"

# Проверить глобальный lock
redis-cli GET "crm:mailer:send_pending_emails:lock"

# Проверить все ключи mailer
redis-cli KEYS "crm:mailer:*"
```

**Ключевые ключи:**
- `crm:mailer:rate:hour:YYYY-MM-DD:HH` — счетчик отправок в час (макс 100)
- `crm:mailer:send_pending_emails:lock` — глобальный lock (TTL 600 сек)

### Что смотреть в Celery

```bash
# Проверить статус воркеров
celery -A crm inspect active

# Проверить последний запуск задачи
celery -A crm inspect scheduled

# Проверить логи Celery
docker logs celery_worker --tail 100
docker logs celery_beat --tail 100
```

**Ключевые задачи:**
- `mailer.tasks.send_pending_emails` — основная задача отправки (запускается каждые 30 сек)
- `mailer.tasks.sync_smtp_bz_quota` — синхронизация квоты (каждый час)
- `mailer.tasks.reconcile_campaign_queue` — cleanup (каждые 5 минут)

## 2. Частые симптомы → причины → действия

### "Рассылка стоит" (defer_reason)

#### `daily_limit` — Дневной лимит пользователя достигнут

**Причина:** Пользователь отправил >= `per_user_daily_limit` писем сегодня.

**Проверка:**
```sql
SELECT 
    COUNT(*) as sent_today
FROM mailer_sendlog sl
JOIN mailer_campaign c ON c.id = sl.campaign_id
WHERE sl.provider = 'smtp_global'
  AND sl.status = 'sent'
  AND c.created_by_id = <user_id>
  AND sl.created_at >= CURRENT_DATE;
```

**Действие:**
- Это нормальное поведение — рассылка продолжится завтра в 09:00 МСК автоматически
- Если нужно продолжить сегодня: увеличить `GlobalMailAccount.per_user_daily_limit` или дождаться следующего дня

#### `quota_exhausted` — Глобальная квота исчерпана

**Причина:** `SmtpBzQuota.emails_available <= 0`

**Проверка:**
```sql
SELECT emails_available, emails_limit, last_synced_at
FROM mailer_smtpbzquota
ORDER BY id DESC LIMIT 1;
```

**Действие:**
1. Проверить, что `sync_smtp_bz_quota` task работает (последний запуск < 2 часов назад)
2. Если квота реально исчерпана:
   - Пополнить квоту в smtp.bz
   - Дождаться следующего sync (каждый час) или запустить вручную:
     ```python
     from mailer.tasks import sync_smtp_bz_quota
     sync_smtp_bz_quota.delay()
     ```
3. Если sync не работает — проверить `SmtpBzQuota.sync_error` и `GlobalMailAccount.smtp_bz_api_key`

#### `rate_per_hour` — Лимит 100 писем/час достигнут

**Причина:** В текущем часе отправлено >= 100 писем.

**Проверка:**
```bash
redis-cli GET "crm:mailer:rate:hour:$(date +%Y-%m-%d:%H)"
```

**Действие:**
- Это нормальное поведение — рассылка продолжится в начале следующего часа автоматически
- Если нужно продолжить раньше: подождать до начала следующего часа (например, если сейчас 14:30, продолжится в 15:00)

#### `outside_hours` — Вне рабочего времени

**Причина:** Текущее время МСК не в диапазоне 9:00-18:00.

**Проверка:**
```python
from mailer.tasks import _is_working_hours
print(_is_working_hours())  # False если вне рабочего времени
```

**Действие:**
- Это нормальное поведение — рассылка продолжится завтра в 09:00 МСК автоматически
- Если нужно продолжить вне рабочего времени: изменить `WORKING_HOURS_START`/`WORKING_HOURS_END` в `constants.py` (требует перезапуска)

#### `transient_error` — Временная ошибка отправки

**Причина:** SMTP сервер вернул временную ошибку (timeout, connection error, etc.)

**Проверка:**
```sql
SELECT 
    consecutive_transient_errors,
    deferred_until
FROM mailer_campaignqueue
WHERE campaign_id = <campaign_id>;
```

**Действие:**
1. Если `consecutive_transient_errors < 10`:
   - Кампания автоматически повторит попытку через 5 минут
   - Проверить доступность SMTP сервера (smtp.bz)
2. Если `consecutive_transient_errors >= 10`:
   - Кампания автоматически паузится (circuit breaker)
   - Проверить SMTP настройки и доступность сервера
   - После исправления: вручную возобновить кампанию через UI

### "PROCESSING > 30 минут" — Зависшая кампания

**Причина:** Воркер упал во время обработки или deadlock.

**Проверка:**
```sql
SELECT 
    id,
    campaign_id,
    status,
    started_at,
    EXTRACT(EPOCH FROM (NOW() - started_at))/60 as minutes_stuck
FROM mailer_campaignqueue
WHERE status = 'processing'
  AND started_at < NOW() - INTERVAL '30 minutes';
```

**Действие:**
1. Проверить, что воркер работает: `celery -A crm inspect active`
2. Если воркер не отвечает — перезапустить:
   ```bash
   docker restart celery_worker
   ```
3. Если воркер работает, но кампания зависла:
   - Запустить cleanup вручную:
     ```python
     from mailer.tasks import reconcile_campaign_queue
     reconcile_campaign_queue.delay()
     ```
   - Или вручную сбросить статус:
     ```sql
     UPDATE mailer_campaignqueue
     SET status = 'pending', started_at = NULL
     WHERE id = <queue_id>;
     ```

### "Redis недоступен"

**Причина:** Redis временно недоступен.

**Проверка:**
```bash
redis-cli PING
```

**Действие:**
1. Проверить статус Redis: `docker ps | grep redis`
2. Если Redis упал — перезапустить: `docker restart redis`
3. **Важно:** При ошибке Redis rate limiter использует fail-open стратегию (разрешает отправку), но нужно восстановить Redis для корректной работы лимитов

### "Квота не синхронизируется"

**Причина:** `sync_smtp_bz_quota` task не работает или API ключ неверный.

**Проверка:**
```sql
SELECT 
    last_synced_at,
    sync_error,
    emails_available
FROM mailer_smtpbzquota
ORDER BY id DESC LIMIT 1;
```

**Действие:**
1. Проверить, что Celery beat работает: `docker logs celery_beat --tail 50`
2. Проверить `GlobalMailAccount.smtp_bz_api_key` (должен быть валидный API ключ smtp.bz)
3. Запустить sync вручную:
   ```python
   from mailer.tasks import sync_smtp_bz_quota
   sync_smtp_bz_quota.delay()
   ```
4. Проверить логи на ошибки: `docker logs celery_worker | grep sync_smtp_bz_quota`

## 3. Runbook-операции

### Как безопасно включить продолжение рассылки

1. **Проверить причину паузы:**
   ```sql
   SELECT defer_reason, deferred_until FROM mailer_campaignqueue WHERE campaign_id = '<campaign_uuid>';
   ```

2. **Если причина устранена** (например, квота пополнена):
   ```sql
   UPDATE mailer_campaignqueue
   SET deferred_until = NULL, defer_reason = '', consecutive_transient_errors = 0
   WHERE campaign_id = '<campaign_uuid>' AND status = 'pending';
   ```

3. **Или через UI:** Кнопка "Возобновить" в деталях кампании

### Как изменить конфигурируемые лимиты

Лимиты настраиваются через Django settings или environment variables:

**В .env файле:**
```bash
MAILER_MAX_CAMPAIGN_RECIPIENTS=10000
MAILER_THROTTLE_CAMPAIGN_START_PER_HOUR=10
MAILER_THROTTLE_TEST_EMAIL_PER_HOUR=5
MAILER_CIRCUIT_BREAKER_THRESHOLD=10
MAILER_TRANSIENT_RETRY_DELAY_MINUTES=5
```

**После изменения:**
- Перезапустить Django/Celery процессы для применения новых значений
- Проверить текущие значения можно через Django shell:
  ```python
  from django.conf import settings
  print(settings.MAILER_MAX_CAMPAIGN_RECIPIENTS)
  ```

### Как поставить кампанию на паузу

1. **Через UI:** Кнопка "Пауза" в деталях кампании
2. **Через SQL:**
   ```sql
   UPDATE mailer_campaign
   SET status = 'paused'
   WHERE id = '<campaign_uuid>';
   
   UPDATE mailer_campaignqueue
   SET status = 'pending', started_at = NULL, consecutive_transient_errors = 0
   WHERE campaign_id = '<campaign_uuid>';
   ```

### Как проверить, что Celery beat работает

```bash
# Проверить scheduled tasks
celery -A crm inspect scheduled

# Проверить логи beat
docker logs celery_beat --tail 100 | grep "beat:"

# Должны быть регулярные запуски:
# - mailer.tasks.send_pending_emails (каждые 30 сек)
# - mailer.tasks.sync_smtp_bz_quota (каждый час)
# - mailer.tasks.reconcile_campaign_queue (каждые 5 минут)
```

Если beat не работает:
```bash
docker restart celery_beat
```

## 4. Алёрты, которые надо настроить

### Критические алёрты

1. **Redis errors в rate limiter**
   - Условие: `logger.error` с `"Error reserving rate limit token"` в логах
   - Действие: Проверить доступность Redis, перезапустить при необходимости

2. **PROCESSING stuck > 30 минут**
   - Условие: `CampaignQueue.status = 'processing' AND started_at < NOW() - INTERVAL '30 minutes'`
   - Действие: Запустить `reconcile_campaign_queue` или перезапустить воркер

3. **consecutive_transient_errors >= 10**
   - Условие: `CampaignQueue.consecutive_transient_errors >= 10`
   - Действие: Проверить SMTP доступность, исправить проблему, возобновить кампанию

### Предупреждающие алёрты

4. **queue_depth > 50**
   - Условие: `COUNT(*) FROM mailer_campaignqueue WHERE status = 'pending' > 50`
   - Действие: Проверить причины пауз, возможно увеличить пропускную способность

5. **quota_exhausted длительное время**
   - Условие: `SmtpBzQuota.emails_available <= 0 AND last_synced_at < NOW() - INTERVAL '2 hours'`
   - Действие: Проверить sync task, пополнить квоту

6. **Celery beat не работает**
   - Условие: Нет логов от beat за последние 5 минут
   - Действие: Перезапустить `celery_beat`

## 5. Полезные SQL-запросы

### Статистика по кампаниям

```sql
-- Активные кампании
SELECT 
    c.id,
    c.name,
    c.status,
    q.status as queue_status,
    q.defer_reason,
    q.deferred_until,
    q.consecutive_transient_errors,
    COUNT(CASE WHEN r.status = 'pending' THEN 1 END) as pending,
    COUNT(CASE WHEN r.status = 'sent' THEN 1 END) as sent,
    COUNT(CASE WHEN r.status = 'failed' THEN 1 END) as failed
FROM mailer_campaign c
LEFT JOIN mailer_campaignqueue q ON q.campaign_id = c.id
LEFT JOIN mailer_campaignrecipient r ON r.campaign_id = c.id
WHERE c.status IN ('ready', 'sending')
GROUP BY c.id, c.name, c.status, q.status, q.defer_reason, q.deferred_until, q.consecutive_transient_errors;
```

### Отправки за последний час

```sql
SELECT 
    COUNT(*) as sent_count,
    COUNT(DISTINCT campaign_id) as campaigns_count
FROM mailer_sendlog
WHERE provider = 'smtp_global'
  AND status = 'sent'
  AND created_at >= NOW() - INTERVAL '1 hour';
```

### Топ причин пауз

```sql
SELECT 
    defer_reason,
    COUNT(*) as count,
    MIN(deferred_until) as earliest_resume
FROM mailer_campaignqueue
WHERE status = 'pending'
  AND defer_reason != ''
GROUP BY defer_reason
ORDER BY count DESC;
```
