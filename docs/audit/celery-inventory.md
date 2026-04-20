# Инвентаризация Celery задач CRM ПРОФИ
_Снапшот: 2026-04-20. Wave 0.1._

Источник: `backend/crm/celery.py`, `backend/crm/settings.py`, `backend/*/tasks.py`.

## Сводка

- Всего `@shared_task` / `@app.task`: **18** (включая `debug_task`).
- Продуктивных задач: **17** (без `debug_task`).
- Периодических (Celery Beat): **13**.
- Задач без retry policy (`max_retries=0` / не указано): **10**. Red flag для IO-задач.
- Задач с `autoretry_for=(Exception,)` (слишком широко): **2** (`send_outbound_webhook`, `send_push_notification`).
- Red-flag задач по идемпотентности: **3** (см. раздел «Красный список» в конце).
- Задач, потенциально дольше 5 минут: **3** (`send_pending_emails`, `reindex_companies_daily`, `generate_contract_reminders`).

### Глобальная конфигурация Celery
_backend/crm/settings.py:610-627_

| Параметр | Значение | Комментарий |
|----------|----------|-------------|
| `CELERY_BROKER_URL` | `redis://localhost:6379/1` (env-override) | Redis DB 1 |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/2` | Redis DB 2 |
| `CELERY_TASK_SERIALIZER` | `json` | |
| `CELERY_TIMEZONE` | `TIME_ZONE` = `Europe/Moscow` | Все cron — MSK |
| `CELERY_TASK_TRACK_STARTED` | `True` | |
| `CELERY_TASK_TIME_LIMIT` | `30 * 60` (30 мин) | Hard-лимит |
| `CELERY_TASK_SOFT_TIME_LIMIT` | `25 * 60` (25 мин) | Soft-лимит |
| `CELERY_WORKER_PREFETCH_MULTIPLIER` | `1` | Не брать задачи заранее |
| `CELERY_TASK_ACKS_LATE` | `True` | Ack только после завершения |
| `CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP` | `True` | |
| `CELERY_WORKER_HIJACK_ROOT_LOGGER` | `False` | |

Отдельные queues/routes НЕ настроены — все задачи идут в `default` (celery).

## Расписание Celery Beat
_backend/crm/settings.py:665-738_

| Beat-ключ | Task | Расписание | Queue | Идемпотентна |
|-----------|------|------------|-------|--------------|
| `send-pending-emails` | `mailer.tasks.send_pending_emails` | `60.0` сек (каждую мин) | default | 🟢 (Redis-лок + SendLog idempotency) |
| `sync-smtp-bz-quota` | `mailer.tasks.sync_smtp_bz_quota` | `SMTP_BZ_QUOTA_SYNC_SECONDS` = 300c | default | 🟢 (Singleton `SmtpBzQuota`) |
| `sync-smtp-bz-unsubscribes` | `mailer.tasks.sync_smtp_bz_unsubscribes` | `SMTP_BZ_UNSUB_SYNC_SECONDS` = 600c | default | 🟢 (bulk_create `ignore_conflicts=True`) |
| `sync-smtp-bz-delivery-events` | `mailer.tasks.sync_smtp_bz_delivery_events` | `SMTP_BZ_DELIVERY_SYNC_SECONDS` = 600c | default | 🟡 (bulk_create SendLog без uniqueness — см. red flag) |
| `reconcile-mail-campaign-queue` | `mailer.tasks.reconcile_campaign_queue` | `MAILER_QUEUE_RECONCILE_SECONDS` = 300c | default | 🟢 (`get_or_create`) |
| `clean-old-call-requests` | `phonebridge.tasks.clean_old_call_requests` | `3600.0` сек (1 ч) | default | 🟢 (DELETE по cutoff) |
| `reindex-companies-daily` | `companies.tasks.reindex_companies_daily` | `crontab(hour=0, minute=0)` MSK | default | 🟢 (management command) |
| `generate-recurring-tasks` | `tasksapp.tasks.generate_recurring_tasks` | `crontab(hour=6, minute=0)` MSK | default | 🟢 (Redis-lock + `SELECT FOR UPDATE` + `exists()` + `IntegrityError`-savepoint) |
| `generate-contract-reminders` | `notifications.tasks.generate_contract_reminders` | `crontab(hour=6, minute=30)` MSK | default | 🟢 (явный `exists()` перед `create`) |
| `purge-old-activity-events` | `audit.tasks.purge_old_activity_events` | `crontab(hour=3, minute=0, day_of_week=0)` MSK | default | 🟢 (DELETE) |
| `purge-old-error-logs` | `audit.tasks.purge_old_error_logs` | `crontab(hour=3, minute=15, day_of_week=0)` MSK | default | 🟢 (DELETE) |
| `purge-old-notifications` | `notifications.tasks.purge_old_notifications` | `crontab(hour=3, minute=30, day_of_week=0)` MSK | default | 🟢 (DELETE с фильтром `is_read=True`) |
| `messenger-auto-resolve` | `messenger.tasks.auto_resolve_conversations` | `900.0` сек (15 мин) | default | 🟢 (UPDATE по фильтру, без INSERT) |
| `messenger-escalate-stalled` | `messenger.tasks.escalate_stalled_conversations` | `120.0` сек (2 мин) | default | 🟡 (см. red flag — нет защиты от дублирующих уведомлений) |
| `messenger-escalate-waiting` | `messenger.escalate_waiting_conversations` | `30.0` сек | default | 🟢 (`escalation_level` чек + UPDATE условно) |
| `messenger-check-offline-operators` | `messenger.check_offline_operators` | `60.0` сек | default | 🟢 (UPDATE по `messenger_last_seen`) |

### Red-flag по расписанию

1. **`send-pending-emails` каждую минуту + `soft_time_limit=25min`**. Если SMTP-отправка тормозит, две периоды beat накладываются. Защищено через `cache.add` Redis-лок (`mailer:send_pending_emails:lock`, default 10 мин по `MAILER_SEND_LOCK_TIMEOUT` — см. константу), но сам лок хрупкий: `cache.get`-compare-and-delete в `finally` не атомарен (может удалить чужой лок при рестарте worker'а).
2. **`messenger-escalate-waiting` каждые 30 секунд**. Очень часто. Если candidates-QuerySet большой (тысячи OPEN/PENDING диалогов), за 30 секунд не уложится — накладки.
3. **`reindex-companies-daily` 00:00 MSK** — не параллелится с ETL, хорошо.

## Инвентаризация по модулям

---

### crm/celery.py

#### debug_task
- **Имя:** `crm.celery.debug_task`
- **Файл:** `backend/crm/celery.py:22-25`
- **Queue:** default
- **Schedule:** — (вручную)
- **Retry:** не указан, `ignore_result=True`, `bind=True`
- **Rate limit:** —
- **Идемпотентность:** 🟢 пишет только в лог
- **Dependencies:** `logging`
- **Ожидаемая длительность:** <1 мс
- **Notes:** отладочная, не используется в beat.

---

### companies/tasks.py

#### reindex_companies_daily
- **Имя:** `companies.tasks.reindex_companies_daily`
- **Файл:** `backend/companies/tasks.py:14-44`
- **Queue:** default
- **Schedule:** `crontab(hour=0, minute=0)` MSK (ежедневно в полночь)
- **Retry:** ❌ не указан. `max_retries` по умолчанию `None` = сколько угодно, но `autoretry_for` не задан — задача просто не ретраится при raise.
- **Rate limit:** —
- **Идемпотентность:** 🟢 вызывает `normalize_companies_data --batch_size=500` и `rebuild_company_search_index --chunk=500`. Оба management-команды идемпотентны (UPDATE-only). Исключения ловятся через `except Exception` внутри тела — задача всегда успешно завершается.
- **Dependencies:** `django.core.management.call_command`, `Company`, `CompanySearchIndex`.
- **Ожидаемая длительность:** десятки секунд — несколько минут на 50k-100k компаний. При росте базы может упереться в `CELERY_TASK_SOFT_TIME_LIMIT=25min`.
- **Notes:** все исключения глотаются через `try/except ... logger.exception`, никаких сигналов провала во внешний мир. При постоянной ошибке нормализации/переиндексации задача «молча падает» → данные FTS могут устаревать без алерта. Потенциально долгая: кандидат на разбивку, если `Company` > 200k.

---

### audit/tasks.py

#### purge_old_activity_events
- **Имя:** `audit.tasks.purge_old_activity_events`
- **Файл:** `backend/audit/tasks.py:20-31`
- **Queue:** default
- **Schedule:** `crontab(hour=3, minute=0, day_of_week=0)` MSK (воскресенье 03:00)
- **Retry:** ❌ не указан (`ignore_result=True`)
- **Rate limit:** —
- **Идемпотентность:** 🟢 `DELETE WHERE created_at < cutoff` — безопасно повторять.
- **Dependencies:** `ActivityEvent`.
- **Ожидаемая длительность:** при 9.5M строк (см. MEMORY: `ActivityEvent` 9.5M) без batching — **красный флаг**. Django `.delete()` загружает PK в память, генерит CASCADE-сигналы. При > 100k устаревших строк возможен OOM воркера или TX lock на десятки секунд.
- **Notes:** ⚠️ см. red flag-список ниже. Отсутствует chunking/raw SQL.

#### purge_old_error_logs
- **Имя:** `audit.tasks.purge_old_error_logs`
- **Файл:** `backend/audit/tasks.py:34-57`
- **Queue:** default
- **Schedule:** `crontab(hour=3, minute=15, day_of_week=0)` MSK
- **Retry:** ❌ не указан (`ignore_result=True`)
- **Rate limit:** —
- **Идемпотентность:** 🟢 двухфазный DELETE (soft: `resolved=True` старше 90d; hard: всё старше 180d).
- **Dependencies:** `ErrorLog`.
- **Ожидаемая длительность:** секунды (таблица мелкая по сравнению с ActivityEvent).
- **Notes:** ок.

---

### notifications/tasks.py

#### purge_old_notifications
- **Имя:** `notifications.tasks.purge_old_notifications`
- **Файл:** `backend/notifications/tasks.py:19-33`
- **Queue:** default
- **Schedule:** `crontab(hour=3, minute=30, day_of_week=0)` MSK
- **Retry:** ❌ не указан (`ignore_result=True`)
- **Rate limit:** —
- **Идемпотентность:** 🟢 `DELETE WHERE created_at<cutoff AND is_read=True`.
- **Dependencies:** `Notification`.
- **Ожидаемая длительность:** секунды.
- **Notes:** ок.

#### generate_contract_reminders
- **Имя:** `notifications.tasks.generate_contract_reminders`
- **Файл:** `backend/notifications/tasks.py:36-121`
- **Queue:** default
- **Schedule:** `crontab(hour=6, minute=30)` MSK (ежедневно)
- **Retry:** ❌ не указан (`ignore_result=True`)
- **Rate limit:** —
- **Идемпотентность:** 🟢 перед `CompanyContractReminder.objects.create(...)` идёт `exists()`-чек по `(user, company_id, contract_until, days_before)`. `notify()` внутри try/except — повторный запуск в тот же день не создаст дубли напоминаний; уведомления в колокольчике, впрочем, могут дублироваться если `notify()` не использует `dedupe_seconds` (в теле — не использует).
- **Dependencies:** `Company`, `CompanyContractReminder`, `Notification`, `notifications.service.notify`.
- **Ожидаемая длительность:** зависит от числа компаний с `contract_until`. На 10k компаний — несколько секунд; iterator chunk=500 защищает от mem-blowup.
- **Notes:** 🟡 потенциальный дубликат `Notification` если `exists()`-чек по `CompanyContractReminder` не попал в race (маловероятно — задача раз в сутки). Сам `notify()` не использует `dedupe_seconds` — но это скорее micro-дубль одного уведомления в крайних случаях.

---

### tasksapp/tasks.py

#### generate_recurring_tasks
- **Имя:** `tasksapp.tasks.generate_recurring_tasks`
- **Файл:** `backend/tasksapp/tasks.py:76-102` (обёртка) + `_generate_recurring_tasks_inner` 105-146 + `_process_template` 149-225
- **Queue:** default
- **Schedule:** `crontab(hour=6, minute=0)` MSK
- **Retry:** `max_retries=0` — **намеренно** (см. комментарий «редис-лок на всю задачу»).
- **Rate limit:** —
- **Идемпотентность:** 🟢 **лучший пример в проекте**. Три уровня защиты:
  1. Redis-lock `lock:generate_recurring_tasks` (timeout 15 мин) — второй worker выйдет сразу.
  2. `Task.objects.select_for_update(of=("self",))` на каждом шаблоне.
  3. `Task.objects.filter(parent_recurring_task=template, due_at=occ_dt).exists()` + savepoint + `except IntegrityError` (на случай UniqueConstraint).
- **Dependencies:** `Task`, `dateutil.rrule.rrulestr`.
- **Ожидаемая длительность:** зависит от числа шаблонов. На каждый шаблон — до `MAX_OCCURRENCES=1000` вставок. Если сотни шаблонов — несколько минут.
- **Notes:** единственная задача с корректной защитой от duplicate-race. Образец для остальных.

---

### phonebridge/tasks.py

#### clean_old_call_requests
- **Имя:** `phonebridge.tasks.clean_old_call_requests`
- **Файл:** `backend/phonebridge/tasks.py:16-31`
- **Queue:** default
- **Schedule:** `3600.0` сек (ежечасно)
- **Retry:** ❌ не указан, но внутри `except Exception: ... raise` — raise → без `autoretry_for` worker просто залогирует и вернёт FAILURE без ретрая.
- **Rate limit:** —
- **Идемпотентность:** 🟢 `DELETE WHERE created_at<cutoff`.
- **Dependencies:** `CallRequest`.
- **Ожидаемая длительность:** секунды.
- **Notes:** `raise` после `logger.error` бессмыслен без retry-policy — только засоряет `CELERY_RESULT_BACKEND` FAILURE-записями. Либо добавить `autoretry_for`, либо проглотить.

---

### messenger/tasks.py

#### auto_resolve_conversations
- **Имя:** `messenger.tasks.auto_resolve_conversations` (автоимя по функции; `name=` не задан, но beat ссылается как `messenger.tasks.auto_resolve_conversations`)
- **Файл:** `backend/messenger/tasks.py:18-56`
- **Queue:** default
- **Schedule:** `900.0` сек (15 мин)
- **Retry:** `max_retries=0` (`soft_time_limit=120`, `acks_late=True`)
- **Rate limit:** —
- **Идемпотентность:** 🟢 только UPDATE (RESOLVED→CLOSED и OPEN/PENDING→RESOLVED) с фильтрами по cutoff-времени.
- **Dependencies:** `Conversation`.
- **Ожидаемая длительность:** секунды-миллисекунды.
- **Notes:** ок.

#### escalate_stalled_conversations
- **Имя:** `messenger.tasks.escalate_stalled_conversations`
- **Файл:** `backend/messenger/tasks.py:59-79`
- **Queue:** default
- **Schedule:** `120.0` сек (2 мин)
- **Retry:** `max_retries=0`, `soft_time_limit=60`, `acks_late=True`
- **Rate limit:** —
- **Идемпотентность:** 🟡 делегирует в `messenger.services.escalate_conversation`. Нужно проверять внутри сервиса: пишет ли assignee-перебор без идемпотентного ключа? (Wave 3 todo — см. red list).
- **Dependencies:** `messenger.services.{get_conversations_eligible_for_escalation, escalate_conversation}`.
- **Ожидаемая длительность:** зависит от числа активных диалогов.
- **Notes:** ⚠️ red flag candidate — идемпотентность не самоочевидна (см. red list #1).

#### dispatch_async_listeners
- **Имя:** `messenger.tasks.dispatch_async_listeners`
- **Файл:** `backend/messenger/tasks.py:82-121`
- **Queue:** default
- **Schedule:** — (вызывается из EventDispatcher через `.delay()`)
- **Retry:** `max_retries=2`, `soft_time_limit=30`, `acks_late=True`
- **Rate limit:** —
- **Идемпотентность:** 🟡 **зависит от слушателей**. Сама функция просто вызывает `listener(event_name, timestamp, data)` в цикле. Если listener пишет в БД без защиты от дубля — ретрай этой task создаст дубликат. `try/except Exception: logger.error` внутри цикла означает: ошибка одного listener'а не валит задачу → но при падении до цикла (например, import_module) — raise с retry.
- **Dependencies:** `messenger.dispatchers.get_async_listener_registry`, динамически импортируемые listener'ы.
- **Ожидаемая длительность:** зависит от количества listener'ов и их кода. Limit 30s soft.
- **Notes:** ⚠️ red flag candidate — идемпотентность **делегирована listener'ам**, а их там несколько. Нужен аудит самих listener'ов (Wave 3 отдельно).

#### send_offline_email_notification
- **Имя:** `messenger.tasks.send_offline_email_notification`
- **Файл:** `backend/messenger/tasks.py:124-200`
- **Queue:** default
- **Schedule:** — (вызывается из messenger-сигналов через `.delay()`)
- **Retry:** `max_retries=1`, `soft_time_limit=30`, `acks_late=True`. `raise` при ошибке SMTP → retry.
- **Rate limit:** —
- **Идемпотентность:** 🟢 явная защита через Redis cache: `cache.get(f"messenger:email_notify:{conversation_id}") → return "throttled"`, иначе `cache.set(key, "1", timeout=900)` **до** отправки. Сам cache.set-before-send означает: если SMTP упадёт — email не отправится, но throttle-ключ уже установлен на 15 минут → retry (max_retries=1) тоже увидит throttle и вернёт. Это осознанная трассировка: лучше пропустить письмо, чем отправить дважды.
- **Dependencies:** `Conversation`, `Message`, `AgentProfile`, `GlobalMailAccount`, `mailer.smtp_sender`.
- **Ожидаемая длительность:** 1-5 сек (SMTP).
- **Notes:** ok. Минорно: throttle-ключ выставляется до фактической отправки, так что ретрай может совсем не отправить письмо. По сути это P1: после первого FAILED оператор не получит email за 15 мин. Для алертов — приемлемо.

#### escalate_waiting_conversations
- **Имя:** `messenger.escalate_waiting_conversations`
- **Файл:** `backend/messenger/tasks.py:203-308`
- **Queue:** default
- **Schedule:** `30.0` сек (каждые 30 секунд!)
- **Retry:** не указан (по умолчанию `max_retries=3`, `autoretry_for` нет → не ретраится на raise)
- **Rate limit:** —
- **Идемпотентность:** 🟢 **хорошо продумана**. Используется `conv.escalation_level` (0/1/2/3/4) как идемпотентный курсор: `if target_level <= conv.escalation_level: continue`. Каждый уровень триггерит events ровно один раз благодаря финальному `Conversation.objects.filter(pk=conv.pk).update(escalation_level=target_level, last_escalated_at=now)`.
- **Dependencies:** `Conversation`, `User`, `Notification`.
- **Ожидаемая длительность:** ≤ секунды при десятках-сотнях активных диалогов.
- **Notes:** 🟡 **потенциальная проблема частоты** — каждые 30с при scan candidates через join по `last_customer_msg_at`, `last_agent_msg_at`. При `Conversation` >10-50k активных и без индекса на `(status, last_customer_msg_at)` — нагрузка. Кандидат на профилирование.

#### check_offline_operators
- **Имя:** `messenger.check_offline_operators`
- **Файл:** `backend/messenger/tasks.py:311-324`
- **Queue:** default
- **Schedule:** `60.0` сек (каждую минуту)
- **Retry:** не указан
- **Rate limit:** —
- **Идемпотентность:** 🟢 `UPDATE WHERE messenger_online=True AND messenger_last_seen < threshold` — безопасно повторять.
- **Dependencies:** `User`.
- **Ожидаемая длительность:** миллисекунды.
- **Notes:** ок.

#### send_outbound_webhook
- **Имя:** `messenger.send_outbound_webhook`
- **Файл:** `backend/messenger/tasks.py:335-373`
- **Queue:** default
- **Schedule:** — (on-demand)
- **Retry:** `autoretry_for=(Exception,)`, `retry_backoff=True`, `retry_backoff_max=600`, `retry_jitter=True`, `max_retries=5`, `acks_late=True`.
- **Rate limit:** —
- **Идемпотентность:** 🟡 делает HTTP POST — receiver обязан быть идемпотентен. На нашей стороне сообщение формируется вне task (в сервисе), сюда приходит уже готовый body + headers. 5xx → retry, 4xx не ретраится.
- **Dependencies:** `requests`.
- **Ожидаемая длительность:** ≤ 5 сек (`timeout=5.0`).
- **Notes:** 🟡 `autoretry_for=(Exception,)` слишком широкий — ретраит ВСЕ исключения, включая баги в самой task (`TypeError`, `KeyError`). См. red list #2. Лучше `(requests.RequestException, RuntimeError)`.

#### send_push_notification
- **Имя:** `messenger.send_push_notification`
- **Файл:** `backend/messenger/tasks.py:376-390`
- **Queue:** default
- **Schedule:** — (on-demand)
- **Retry:** `autoretry_for=(Exception,)`, `retry_backoff=True`, `retry_backoff_max=300`, `max_retries=3`, `acks_late=True`
- **Rate limit:** —
- **Идемпотентность:** 🟡 делегирует в `messenger.push._deliver_push_to_subscription`. Push — at-most-once или at-least-once? Надо посмотреть в `push.py` (Wave 3).
- **Dependencies:** `messenger.push._deliver_push_to_subscription`.
- **Ожидаемая длительность:** ≤ 5-10 сек.
- **Notes:** 🟡 `autoretry_for=(Exception,)` — см. red list #2. Также нет `retry_jitter=True` (есть только у webhook) — в случае массового сбоя push-сервера все ретраи пойдут синхронной волной.

---

### mailer/tasks/

#### send_pending_emails
- **Имя:** `mailer.tasks.send_pending_emails`
- **Файл:** `backend/mailer/tasks/send.py:77-520`
- **Queue:** default
- **Schedule:** `60.0` сек (каждую минуту)
- **Retry:** `bind=True, max_retries=3`. В теле ловится верхний `except Exception: raise self.retry(exc=exc, countdown=60)` — retry с countdown 60 сек.
- **Rate limit:** —. Однако rate-limit применён на уровне кампании через `reserve_rate_limit_token(max_per_hour)` в `_process_batch_recipients`.
- **Идемпотентность:** 🟢 **самая защищённая задача в проекте**:
  1. Redis-лок `mailer:send_pending_emails:lock` (taimeout `MAILER_SEND_LOCK_TIMEOUT` сек, default из `SEND_TASK_LOCK_TIMEOUT`). Hardened: token-based (`lock_val=timestamp`) + `if cache.get(lock_key) == lock_val: cache.delete()` в `finally`.
  2. `SELECT FOR UPDATE SKIP LOCKED` на `CampaignQueue` и `CampaignRecipient` — многопроцессорная безопасность.
  3. SendLog-idempotency check: перед отправкой каждому recipient проверяем `SendLog.objects.filter(campaign, recipient, status=SENT).exists()` → если да, помечаем recipient SENT без повторной отправки.
  4. `confirmed_sent_ids`/`confirmed_failed_ids` recovery на старте батча.
- **Dependencies:** `Campaign`, `CampaignQueue`, `CampaignRecipient`, `GlobalMailAccount`, `MailAccount`, `SendLog`, `SmtpBzQuota`, `Unsubscribe`, `UserDailyLimitStatus`, `mailer.smtp_sender`, `notifications.service`.
- **Ожидаемая длительность:** зависит от `batch_size` (default `SEND_BATCH_SIZE_DEFAULT`) и SMTP-латентности. При batch_size=50 и ~2с/email → 100 секунд. Под `soft_time_limit` умещается.
- **Notes:** ⚠️ **потенциально долгая** (до 25 мин soft-limit). Также: `cache.get == lock_val`-compare-and-delete не атомарен в Redis без Lua-скрипта — теоретически при рестарте worker'а между `get` и `delete` можно удалить чужой лок. На практике маловероятно (timeout 10 мин).

#### send_test_email
- **Имя:** `mailer.tasks.send_test_email`
- **Файл:** `backend/mailer/tasks/send.py:523-633`
- **Queue:** default
- **Schedule:** — (on-demand)
- **Retry:** ❌ не указан
- **Rate limit:** —
- **Идемпотентность:** 🟡 пишет `SendLog.objects.create(...)` без уникального ключа. Если пользователь дважды нажмёт кнопку «Отправить тест» — будет два SendLog'а и, возможно, два письма. Задача on-demand, так что дубликат — user-facing.
- **Dependencies:** `GlobalMailAccount`, `SmtpBzQuota`, `MailAccount`, `Campaign`, `SendLog`, `mailer.smtp_sender`.
- **Ожидаемая длительность:** 1-10 сек.
- **Notes:** ок для on-demand.

#### sync_smtp_bz_quota
- **Имя:** `mailer.tasks.sync_smtp_bz_quota`
- **Файл:** `backend/mailer/tasks/sync.py:129-176`
- **Queue:** default
- **Schedule:** `SMTP_BZ_QUOTA_SYNC_SECONDS` = 300c (каждые 5 мин)
- **Retry:** ❌ не указан. Тело ловит `except Exception` — не пробрасывает.
- **Rate limit:** —
- **Идемпотентность:** 🟢 обновление singleton-модели `SmtpBzQuota.load()` — UPDATE, не INSERT.
- **Dependencies:** `mailer.smtp_bz_api.get_quota_info`, `GlobalMailAccount`, `SmtpBzQuota`.
- **Ожидаемая длительность:** 1-3 сек (external HTTP).
- **Notes:** ок.

#### sync_smtp_bz_unsubscribes
- **Имя:** `mailer.tasks.sync_smtp_bz_unsubscribes`
- **Файл:** `backend/mailer/tasks/sync.py:179-252`
- **Queue:** default
- **Schedule:** `SMTP_BZ_UNSUB_SYNC_SECONDS` = 600c (каждые 10 мин)
- **Retry:** ❌ не указан. Ловит `except Exception`.
- **Rate limit:** —
- **Идемпотентность:** 🟢 `bulk_create(ignore_conflicts=True)` + `bulk_update` для существующих. Курсор offset хранится в `cache` ключе `smtp_bz:unsub:offset` (без TTL). При пустой `data` → курсор сбрасывается в 0.
- **Dependencies:** `mailer.smtp_bz_api.get_unsubscribers`, `GlobalMailAccount`, `Unsubscribe`, `django.core.cache`.
- **Ожидаемая длительность:** 1-5 сек.
- **Notes:** 🟡 курсор хранится **в Redis без TTL** — при рестарте Redis (или смене его persistence-политики) курсор потеряется → задача начнёт с offset=0 и прогонит все отписки заново. `ignore_conflicts=True` спасает от дубликатов в Unsubscribe, но `bulk_update` для существующих пройдёт лишние записи. Приемлемо.

#### sync_smtp_bz_delivery_events
- **Имя:** `mailer.tasks.sync_smtp_bz_delivery_events`
- **Файл:** `backend/mailer/tasks/sync.py:26-126`
- **Queue:** default
- **Schedule:** `SMTP_BZ_DELIVERY_SYNC_SECONDS` = 600c (каждые 10 мин)
- **Retry:** ❌ не указан
- **Rate limit:** —
- **Идемпотентность:** 🟡 **частично**:
  - `CampaignRecipient.bulk_update` — идемпотентно (UPDATE).
  - `SendLog.bulk_create(batch_logs)` — **без `ignore_conflicts`** и без уникального ключа по `(campaign, recipient, status='FAILED')`. Повторный прогон за тот же день даст повторные FAILED-логи для тех же recipient'ов.
- **Dependencies:** `mailer.smtp_bz_api.get_message_logs`, `CampaignRecipient`, `SendLog`, `GlobalMailAccount`.
- **Ожидаемая длительность:** секунды (3 статуса × `SMTP_BZ_SYNC_MAX_PAGES` страниц).
- **Notes:** ⚠️ см. red list #3.

#### reconcile_campaign_queue
- **Имя:** `mailer.tasks.reconcile_campaign_queue`
- **Файл:** `backend/mailer/tasks/reconcile.py:20-114`
- **Queue:** default
- **Schedule:** `MAILER_QUEUE_RECONCILE_SECONDS` = 300c (каждые 5 мин)
- **Retry:** ❌ не указан
- **Rate limit:** —
- **Идемпотентность:** 🟢 все операции condiționals:
  - `filter(...).update(...)` — идемпотентно.
  - `get_or_create(campaign=camp, defaults=...)` — защищено от дубликатов.
- **Dependencies:** `Campaign`, `CampaignQueue`, `CampaignRecipient`.
- **Ожидаемая длительность:** 1-5 сек.
- **Notes:** ок.

---

## КРИТИЧНО — Красный список

### 1. `audit.tasks.purge_old_activity_events` — DELETE 9.5M строк без батчей

- **Файл:** `backend/audit/tasks.py:20-31`
- **Проблема:** `ActivityEvent.objects.filter(created_at__lt=cutoff).delete()` без chunking. По `MEMORY.md` таблица содержит 9.5M строк. Django `.delete()` загружает PK всех удаляемых строк в память, дёргает `pre_delete`/`post_delete`-сигналы и CASCADE — при миллионе устаревших записей это OOM воркера + лок таблицы на десятки секунд.
- **Последствие:** может «тихо» упасть через `CELERY_TASK_TIME_LIMIT=30min`, либо уронить PostgreSQL lock contention.
- **Fix-план:** перейти на raw-SQL `DELETE FROM audit_activityevent WHERE created_at < %s LIMIT 10000` в цикле до исчерпания либо `QuerySet._raw_delete()` (Django internal, no signals).

### 2. `messenger.tasks.escalate_stalled_conversations` → `services.escalate_conversation` — идемпотентность делегирована

- **Файл:** `backend/messenger/tasks.py:59-79`
- **Проблема:** задача не проверяет, ретрай это или первый запуск. Делегирует в `escalate_conversation`, в теле которого предположительно меняется assignee и отправляются уведомления. Если `escalate_conversation` упадёт частично (Notification создан, Conversation не обновлена), повторный запуск беат'а через 2 минуты создаст дубль Notification — оператор получит 2 колокольчика за один раз.
- **Fix-план:** проверить `messenger/services.py::escalate_conversation` — есть ли там guard через `conversation.escalation_level` или `last_escalated_at` (как в `escalate_waiting_conversations`). Wave 3 задача.

### 3. `mailer.tasks.sync_smtp_bz_delivery_events` — SendLog.bulk_create без uniqueness

- **Файл:** `backend/mailer/tasks/sync.py:99-120`
- **Проблема:** задача читает bounce/return/cancel из API smtp.bz за сегодняшнюю дату и для каждого SENT→FAILED recipient'а создаёт `SendLog(status=FAILED)`. Если задача пробежит 2 раза за день (а она бежит каждые 10 мин) — один и тот же bounce даст N записей в SendLog за сутки. В коде есть защита `if r.status == CampaignRecipient.Status.SENT` — второй прогон увидит статус FAILED и пойдёт в ветку `elif r.status == CampaignRecipient.Status.FAILED: if last_error != msg: ...` — лог НЕ создаётся. **Но** если последняя ошибка была другой — создаётся новый SendLog.
- **Факт:** при повторном запуске с идентичными `reason` — дубля нет (проходит только UPDATE). Риск — различие `reason` между прогонами.
- **Fix-план:** либо `ignore_conflicts=True` + UniqueConstraint `(campaign, recipient, status, error)`, либо hash-based dedupe по сообщению. Wave 3 задача.

### 4. (Побочный) `autoretry_for=(Exception,)` в messenger webhook/push — слишком широко

- **Файлы:** `backend/messenger/tasks.py:335-343`, `376-385`
- **Проблема:** любая ошибка (включая баги в самой task — `TypeError`, `AttributeError`, `KeyError`) ретраится до 5/3 раз. В результате: баг в коде вызывает 5x нагрузку + 5x мусорных записей в `CELERY_RESULT_BACKEND`, вместо одного чистого FAILURE, который было бы видно в Sentry.
- **Fix-план:** сузить до `(requests.exceptions.RequestException, RuntimeError)` для webhook и `(PushError, RuntimeError)` для push. Оставить `autoretry_for=(Exception,)` — только если в теле сам `try/except` явно фильтрует bugs.

---

## Задачи без retry policy для IO-операций

Для IO-задач (SMTP, HTTP, GeoIP, Redis RPC) retry критичен. Задачи ниже **делают IO, но retry не настроен**:

| Задача | IO | Нет retry | Комментарий |
|--------|-----|-----------|-------------|
| `companies.tasks.reindex_companies_daily` | DB heavy | Да | Ошибки ловятся и логируются (нет `raise`) — но никакого алерта в случае системного провала. |
| `audit.tasks.purge_old_activity_events` | DB | Да | OK — идемпотентно, повтор beat через неделю. |
| `audit.tasks.purge_old_error_logs` | DB | Да | OK. |
| `notifications.tasks.purge_old_notifications` | DB | Да | OK. |
| `notifications.tasks.generate_contract_reminders` | DB + Notification | Да | При сбое — напоминания не создадутся до следующих суток. |
| `phonebridge.tasks.clean_old_call_requests` | DB | Да (raise без autoretry) | Бесполезный raise. |
| `mailer.tasks.sync_smtp_bz_quota` | HTTP | Да | Ловит `except Exception`, сохраняет `sync_error` в singleton — приемлемо. |
| `mailer.tasks.sync_smtp_bz_unsubscribes` | HTTP | Да | Ловит `except Exception`, возвращает `{"status": "error"}` — next beat перезапустит. |
| `mailer.tasks.sync_smtp_bz_delivery_events` | HTTP | Да | Без `except` в теле — raise доходит до Celery, FAILURE, следующий beat пройдёт через 10 мин. |
| `mailer.tasks.send_test_email` | SMTP | Да | On-demand — OK, user решает повторить. |
| `messenger.tasks.auto_resolve_conversations` | DB | Да (`max_retries=0`) | Идемпотентно, следующий beat через 15 мин. |
| `messenger.tasks.escalate_stalled_conversations` | DB + Notification | Да (`max_retries=0`) | ⚠️ см. red list #2. |
| `messenger.escalate_waiting_conversations` | DB + Notification | Да | Идемпотентно через `escalation_level` cursor. |
| `messenger.check_offline_operators` | DB | Да | Идемпотентно. |

**Задачи с retry**:

| Задача | Retry policy |
|--------|--------------|
| `mailer.tasks.send_pending_emails` | `bind=True, max_retries=3`, inline `self.retry(countdown=60)` |
| `messenger.tasks.dispatch_async_listeners` | `max_retries=2` (без autoretry_for — только ручной raise из listener'а через цикл ловится) |
| `messenger.tasks.send_offline_email_notification` | `max_retries=1`, raise при SMTP-ошибке |
| `messenger.send_outbound_webhook` | `autoretry_for=(Exception,)`, `retry_backoff=True`, `max_retries=5`, `retry_jitter=True` |
| `messenger.send_push_notification` | `autoretry_for=(Exception,)`, `retry_backoff=True`, `max_retries=3` |

---

## Задачи с `autoretry_for=(Exception,)` — слишком широко

| Задача | Файл | Fix |
|--------|------|-----|
| `messenger.send_outbound_webhook` | `backend/messenger/tasks.py:335-343` | Сузить до `(requests.exceptions.RequestException, RuntimeError)` |
| `messenger.send_push_notification` | `backend/messenger/tasks.py:376-385` | Сузить до доменных ошибок модуля `messenger.push` |

**Причина:** `autoretry_for=(Exception,)` ретраит любые баги кода (TypeError, AttributeError), что: (а) маскирует логические ошибки в Sentry, (б) создаёт 5x/3x лишних запусков, (в) при длительной проблеме замусоривает result backend.

---

## Задачи, которые могут крутиться > 5 минут

| Задача | Риск | Причина | Мелкая митигация |
|--------|------|---------|------------------|
| `mailer.tasks.send_pending_emails` | Высокий | SMTP + circuit breaker + 25-min soft-limit | Разбить по кампаниям через subtask (Celery canvas chord) |
| `companies.tasks.reindex_companies_daily` | Средний | Полная переиндексация FTS + нормализация | Уже разбито на два call_command'а с chunk=500 — но нет явного timeout-мониторинга |
| `notifications.tasks.generate_contract_reminders` | Низкий-средний | iterator chunk 500, O(companies × thresholds × 2 exists-query) | При росте `Company` > 100k стоит добавить prefetch существующих reminder'ов одним запросом |
| `audit.tasks.purge_old_activity_events` | **Высокий** | 9.5M строк без chunking (см. red list #1) | Batched delete через raw SQL |
| `tasksapp.tasks.generate_recurring_tasks` | Низкий | Redis lock 15 мин уже страхует | OK |

---

## Выводы для Wave 3/4

1. **Срочно (P0):** `audit.purge_old_activity_events` — переделать на batched raw SQL. Может лечь при следующем воскресном прогоне из-за 9.5M строк.
2. **Средне (P1):** Аудит `messenger/services.escalate_conversation` + async listeners → идемпотентность.
3. **Средне (P1):** Сузить `autoretry_for=(Exception,)` в `send_outbound_webhook` / `send_push_notification`.
4. **Низко (P2):** Добавить `ignore_conflicts=True` или UniqueConstraint для SendLog в `sync_smtp_bz_delivery_events`.
5. **Низко (P2):** Убрать бесполезный `raise` в `phonebridge.clean_old_call_requests`.
6. **Низко (P2):** Вынести rate-sensitive задачи (`escalate_waiting_conversations` каждые 30 сек) в отдельную queue — сейчас всё идёт в `default`, одна зависшая `send_pending_emails` блокирует их.
7. **Архитектура:** отсутствует разделение на queues (realtime / email / maintenance). Один worker, один prefetch=1 — все ждут друг друга.
