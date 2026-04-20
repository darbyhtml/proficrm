# Celery Unsafe Patterns Audit — Wave 0.2 deep scan

_Снапшот: **2026-04-20**. Повторный audit после Wave 0.1 (`docs/audit/celery-inventory.md`)._

Задача этого документа — пройтись по **всем 17 продуктивным tasks** CRM ПРОФИ и зафиксировать паттерны, которые при росте нагрузки или частых retry могут (а) потерять данные, (б) создать дубли, (в) положить worker через OOM/timeout, (г) замаскировать реальные ошибки.

## TL;DR

- Просканировано tasks: **17** (все `@shared_task` в `backend/*/tasks.py` + `tasks/*.py`; `debug_task` пропущен — не продуктивная).
- **Red-flag (P0-P1):** **4**
  - `mailer.sync_smtp_bz_delivery_events` — offset-pagination через `range(MAX_PAGES)` без ретрая при 5xx (P1, категория B+D)
  - `messenger.escalate_stalled_conversations` — идемпотентность делегирована `escalate_conversation`, без защиты от двойного переназначения через 2 минуты (P1, категория D)
  - `messenger.escalate_waiting_conversations` — 3 × `Notification.objects.create` без `dedupe_seconds`, две параллельные beat-тика → дубль (P1, категория D)
  - `messenger.send_offline_email_notification` — throttle-ключ ставится **до** SMTP-отправки, при retry письмо не отправится, оператор молча не получит alert (P1, категория D)
- **P2 (nice-to-fix):** **4**
  - `phonebridge.clean_old_call_requests` — бесполезный `raise` без `autoretry_for`
  - `mailer.sync_smtp_bz_unsubscribes` — offset-курсор в Redis **без TTL** + потенциальный рост очереди при длительном простое
  - `messenger.send_outbound_webhook` / `send_push_notification` — `autoretry_for=(Exception,)` слишком широкий (уже в inventory, подтверждено)
  - `companies.reindex_companies_daily` — все exceptions глотаются, нет Sentry-сигнала при системном провале
- **УЖЕ ЗАКРЫТО (не трогать):** `audit.tasks.purge_old_activity_events` — задизейблен в `settings.py::CELERY_BEAT_SCHEDULE` (коммит `5fe87ba7`), ждёт batched-delete переписи в Wave 3.

## Methodology

Искались 4 категории unsafe-паттернов:

**(A) Bulk DELETE / UPDATE без chunking.** Любой `.objects.filter(...).delete()` / `.update(...)` / `bulk_update()` в теле task'и, где ожидается рост строк > 10k. При delete Django тащит все PK в память + сигналы; при update — long-running TX + потенциальный lock contention.

**(B) Network calls без retry policy.** `requests.post/get`, SMTP (`smtplib`, `django.core.mail`), FCM/WebPush, сторонние API (smtp.bz, AmoCRM). Без `bind=True, autoretry_for=(requests.RequestException, ConnectionError), retry_backoff=True, max_retries=3+` — ошибка сети = потерянный запрос.

**(C) Shared mutable state / race conditions.** Global mutable dict/list на модульном уровне, Redis GET→modify→SET без `SETNX`/Lua, мутация `settings.*` из task.

**(D) Idempotency gaps.** `.objects.create(...)` без uniqueness constraint, отсутствие Redis-lock/select_for_update при повторных запусках, дубли Notification при retry.

Каждый task проверен вручную по всем 4 категориям. Результат ниже.

---

## Red-Flag tasks

### 1. `messenger.escalate_stalled_conversations` (P1 — idempotency gap, категория D)

**Файл:** `backend/messenger/tasks.py:59-79` + `backend/messenger/services.py:550-657`
**Schedule:** `120.0` сек (каждые 2 минуты).

**Проблема:**

Task выбирает диалоги через `get_conversations_eligible_for_escalation` (timeout=240с) и для каждого дёргает `escalate_conversation(conv)`. Внутри сервиса есть `select_for_update` на самом Conversation + проверка `if conv.assignee_id != current_assignee_id: return None`, **но** условие выбора эскалируемых диалогов (`assignee_opened_at__isnull=True AND assignee_assigned_at <= now - 4min`) не обновляется сразу после `escalate_conversation`.

Конкретнее:
1. Beat-тик T0: выбирает 10 диалогов с `assignee_assigned_at` = T0 − 5 мин.
2. `escalate_conversation(conv)` переназначает → пишет `assignee_assigned_at = T0`, `assignee_opened_at = None`.
3. Beat-тик T2 (через 2 мин): новый `assignee_assigned_at = T0` **не старше 4 мин** → диалог не выбирается. ОК.
4. **Но**: если первый вызов `escalate_conversation` завершился с exception **между** `increment(assignee.id)` rate-limiter'а и `save()` на Conversation — диалог останется с старым `assignee_assigned_at`. Beat-тик T2 его заберёт повторно, rate-limiter счётчик для нового assignee уже увеличен → Round-Robin выберет другого, дубль эскалации.

Код `escalate_conversation` (выдержка из `services.py:627-634`):
```python
# Увеличиваем счётчик Rate Limiter (по образцу Chatwoot)
default_rate_limiter.increment(assignee.id)   # <<< increment ДО save

with transaction.atomic():
    conv = Conversation.objects.select_for_update().get(pk=conversation.pk)
    if conv.assignee_id and conv.assignee_id != current_assignee_id:
        return None   # <<< rate-limiter уже инкрементирован, но мы возвращаем None
```

**Impact:**
- При сбое после `increment` → rate-limiter для нового assignee учитывает неосуществлённую эскалацию.
- Для некоторых диалогов возможна серия переназначений, если оператор падает в `AWAY/OFFLINE` прямо перед save.
- Уведомления (Notification внутри `auto_assign_conversation`-like логики) — нет, они создаются не здесь, а на уровне `waiting_conversations`. Но всё равно дубль события в WebSocket-канал инбокса.

**Fix plan (W3):**

```python
@shared_task(bind=True, max_retries=0, soft_time_limit=60, acks_late=True)
def escalate_stalled_conversations(self):
    from django.core.cache import cache
    from .services import get_conversations_eligible_for_escalation, escalate_conversation

    # Redis-lock на всю задачу, 120с timeout (равно beat-периоду)
    LOCK_KEY = "messenger:escalate_stalled:lock"
    if not cache.add(LOCK_KEY, "1", timeout=120):
        logger.info("escalate_stalled_conversations: уже выполняется, пропуск")
        return {"escalated": 0, "skipped_reason": "locked"}

    try:
        conversations = get_conversations_eligible_for_escalation()
        escalated = 0
        for conv in conversations:
            try:
                escalate_conversation(conv)
                escalated += 1
            except Exception:
                logger.warning("Failed to escalate conversation %s", conv.id, exc_info=True)
        return {"escalated": escalated}
    finally:
        try:
            cache.delete(LOCK_KEY)
        except Exception:
            pass
```

Плюс в `escalate_conversation` поменять порядок: `increment(assignee.id)` **после** успешного `save()` (т.е. внутри `transaction.atomic()`), чтобы rate-limiter был согласован с фактическим переназначением.

---

### 2. `messenger.escalate_waiting_conversations` (P1 — non-idempotent Notification.create, категория D)

**Файл:** `backend/messenger/tasks.py:203-308`
**Schedule:** `30.0` сек.

**Проблема:**

Функция защищена курсором `Conversation.escalation_level` (0/1/2/3/4) — каждый уровень триггерится один раз. Но **внутри** одного уровня идут прямые `Notification.objects.create(...)` **до** `Conversation.objects.filter(pk=conv.pk).update(escalation_level=target_level)`:

```python
elif target_level == 2 and conv.assignee_id:
    Notification.objects.create(   # <<< A
        user=conv.assignee, ...
    )
    stats["urgent"] += 1
elif target_level == 3 and conv.branch_id:
    ...
    for rop in rops:
        Notification.objects.create(   # <<< B (N ROP'ов)
            user=rop, ...
        )
...
Conversation.objects.filter(pk=conv.pk).update(    # <<< C
    escalation_level=target_level,
    last_escalated_at=now,
)
```

Если между A/B и C worker упадёт (OOM, `soft_time_limit` не задан → default 30 мин, но beat-тик каждые 30 сек перекроет) — следующий beat-тик через 30 сек увидит `escalation_level` старого уровня, выполнит escalation ещё раз → **дубль Notification в колокольчике** оператора/ROP.

Кроме того: `Notification.objects.create` **не использует** `notifications.service.notify()` с его `dedupe_seconds` — это значит поверх dedupe-механизма, который есть у сервиса, прямой create обходит его.

**Impact:**
- Видимый spam в колокольчике (конкретный жалоб тикет — см. `docs/problems-solved.md`, если был).
- Вероятность low (требует сбой worker между create и update), но частота beat=30с множит риск.

**Fix plan (W3):**

(a) Завернуть в `transaction.atomic()` — Notification.create + Conversation.update вместе. Если крэш — транзакция откатится.

```python
with transaction.atomic():
    if target_level == 2 and conv.assignee_id:
        Notification.objects.create(...)
    elif target_level == 3 and conv.branch_id:
        for rop in rops:
            Notification.objects.create(...)
    ...
    Conversation.objects.filter(pk=conv.pk).update(
        escalation_level=target_level,
        last_escalated_at=now,
    )
```

(b) Добавить Redis-lock на уровне задачи (как в `send_pending_emails`) на 30с timeout, чтобы два beat-тика не гнали параллельно:
```python
LOCK_KEY = "messenger:escalate_waiting:lock"
if not cache.add(LOCK_KEY, "1", timeout=30):
    return {"skipped": "locked"}
```

(c) Перейти с прямого `Notification.objects.create` на `notifications.service.notify(..., dedupe_seconds=60)` — двойная защита.

---

### 3. `messenger.send_offline_email_notification` (P1 — throttle ставится до send, категория D)

**Файл:** `backend/messenger/tasks.py:124-200`

**Проблема:**

```python
cache_key = f"messenger:email_notify:{conversation_id}"
if cache.get(cache_key):
    return {"status": "throttled"}
cache.set(cache_key, "1", timeout=900)   # <<< ставим throttle ДО отправки
...
try:
    send_via_smtp(account=account, msg=msg)
except Exception:
    raise   # retry (max_retries=1)
```

Логика намеренно **at-most-once** — throttle ставится до SMTP. Но при retry (`max_retries=1`) следующий запуск task через Celery backoff пойдёт через ту же ветку → `cache.get(cache_key)` = `"1"` → `return "throttled"`. Оператор офлайн, получил новое сообщение, но **e-mail-уведомления не будет** следующие 15 мин. Регулярная потеря alerts.

**Impact:**
- При 503/timeout на SMTP первое письмо теряется полностью (15 мин blackout).
- В production при flaky SMTP-сервере это вполне реальный сценарий.

**Fix plan (W3):**

Переставить throttle-set на момент **после** успешной отправки:

```python
# Throttle check (читаем, но не ставим)
cache_key = f"messenger:email_notify:{conversation_id}"
if cache.get(cache_key):
    return {"status": "throttled"}
...
try:
    send_via_smtp(account=account, msg=msg)
    # Throttle-set только после успеха, иначе retry пройдёт к следующей попытке
    cache.set(cache_key, "1", timeout=900)
    return {"status": "sent"}
except Exception:
    raise  # retry
```

Минус: если SMTP отправит, но упадёт между send и `cache.set` — retry отправит ещё раз (дубль). Но это **лучше**, чем пропустить alert целиком. Компромисс at-least-once в пользу доставки.

---

### 4. `mailer.sync_smtp_bz_delivery_events` (P1 — no retry on 5xx + SendLog без uniqueness, категория B + D)

**Файл:** `backend/mailer/tasks/sync.py:26-126`
**Schedule:** `SMTP_BZ_DELIVERY_SYNC_SECONDS` = 600c (10 мин).

**Проблема A (категория B — network без retry):**

Task вызывает `get_message_logs(api_key, status=st, limit=200, offset=offset, ...)` в цикле `for _page in range(SMTP_BZ_SYNC_MAX_PAGES)` — **без** `bind=True, autoretry_for, max_retries`. Если smtp.bz отдаёт 502 на странице 3 из 20 — цикл ловит пустой `resp` и обрывается через `break`. Остальные страницы **не** обрабатываются → часть bounce'ов не синхронизирована до следующего beat-тика через 10 мин.

Если 502 повторяется >1 часа — накопятся bounces, которые в итоге синхронизируются, **но** CampaignRecipient уже в статусе SENT (не FAILED) → повторные отправки на битые ящики в следующих кампаниях (уже исправлено в `sync_smtp_bz_delivery_events` через `Unsubscribe`, но для разовых bounces без unsub — нет).

**Проблема B (категория D — SendLog без uniqueness):**

Из inventory (red list #3 Wave 0.1) — подтверждаю:

```python
batch_logs.append(
    SendLog(
        campaign=r.campaign,
        recipient=r,
        provider="smtp_global",
        status=SendLog.Status.FAILED,
        error=msg[:500],
    )
)
...
SendLog.objects.bulk_create(batch_logs)  # <<< без ignore_conflicts, без UniqueConstraint
```

И в конце `SendLog.objects.bulk_create(batch_logs)` без `ignore_conflicts=True`. Логика:
- Первый beat-тик: видит recipient в статусе SENT → переводит в FAILED, создаёт SendLog(FAILED, reason="X").
- Второй beat-тик (10 мин спустя): видит recipient в статусе FAILED, `if r.status == FAILED: if r.last_error != msg: r.last_error = msg; to_update.append(r)` — лог не создаётся, OK.
- **Но**: если smtp.bz возвращает другой `reason` для того же bounce (разные формулировки) — создаётся новый SendLog на каждом прогоне (N прогонов × смена reason = N записей).

**Impact:**
- Накопление дубль-SendLog'ов → раздут отчёт `mailer.views.report` (пагинация, отображаемое число FAILED > реальное).
- При 502 на smtp.bz — частичная обработка bounces.

**Fix plan (W3):**

(a) Обернуть `get_message_logs` в task-level retry. Сам task переписать:

```python
@shared_task(
    name="mailer.tasks.sync_smtp_bz_delivery_events",
    bind=True,
    autoretry_for=(requests.RequestException, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
    acks_late=True,
)
def sync_smtp_bz_delivery_events(self):
    ...
```

(b) Добавить `UniqueConstraint(fields=["campaign", "recipient", "status", "error"], name="sendlog_unique_failed")` в `SendLog` модель (миграция + `ignore_conflicts=True` в bulk_create) **или** hash-based dedupe по `(recipient_id, status, error_hash)` через Redis set.

(c) Переделать `for _page in range(SMTP_BZ_SYNC_MAX_PAGES)` на `while has_next:` с пер-странично retry (через отдельную retry-хелпер-функцию).

---

## P2 — nice-to-fix

### 5. `mailer.sync_smtp_bz_unsubscribes` — offset-курсор в Redis без TTL (категория C)

**Файл:** `backend/mailer/tasks/sync.py:179-252`

Из Wave 0.1 подтверждаю:

```python
offset_key = "smtp_bz:unsub:offset"
offset = int(cache.get(offset_key) or 0)
...
cache.set(offset_key, 0, timeout=None)        # <<< без TTL (persist forever)
cache.set(offset_key, offset + limit, timeout=None)
```

**Проблема:**
- Redis без `allkeys-lru` policy при OOM выкинет ключ → курсор потеряется → начнётся с 0 → `bulk_create(ignore_conflicts=True)` + `bulk_update` для существующих. Дубль-работы, но не data corruption.
- Если smtp.bz вернул 500 на странице N — курсор не двинется, следующий beat-тик попробует ту же страницу → бесконечный loop на плохой странице.

**Fix plan (W4):**
- Добавить TTL = 24 часа на `offset_key` — если застряли, следующий день откатит курсор.
- Счётчик подряд-fail'ов страницы в отдельном ключе, при >5 — сброс курсора.

---

### 6. `phonebridge.clean_old_call_requests` — бесполезный `raise` (категория B)

**Файл:** `backend/phonebridge/tasks.py:16-31`

```python
try:
    cutoff_date = timezone.now() - timedelta(days=days_old)
    deleted_count, _ = CallRequest.objects.filter(created_at__lt=cutoff_date).delete()
    ...
except Exception as exc:
    logger.error(f"Error cleaning old call requests: {exc}", exc_info=True)
    raise   # <<< raise без autoretry_for
```

`raise` без `autoretry_for` → Celery помечает task FAILURE и идёт дальше. Задача beat'ится через час, если ошибка не прошла — опять FAILURE. В `CELERY_RESULT_BACKEND` (Redis DB 2) накапливаются FAILURE-записи, засоряющие мониторинг.

**Fix plan (W4):**
- Убрать `raise` (таблица CallRequest никогда не уйдёт в рост, ошибки — транзиентные). Либо добавить `autoretry_for=(DatabaseError,), max_retries=3, retry_backoff=True`.

---

### 7. `messenger.send_outbound_webhook` / `send_push_notification` — `autoretry_for=(Exception,)` слишком широко (категория B)

**Файлы:** `backend/messenger/tasks.py:335-343`, `376-385`

Уже зафиксировано в Wave 0.1 inventory (red list #4). Подтверждаю:

```python
@shared_task(
    bind=True,
    name="messenger.send_outbound_webhook",
    autoretry_for=(Exception,),   # <<< ВСЁ ретраится, включая TypeError
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
    acks_late=True,
)
def send_outbound_webhook(self, *, url, body, headers, inbox_id, event_type):
    import requests
    try:
        resp = requests.post(url, data=body.encode("utf-8"), headers=headers, timeout=5.0)
    except Exception:
        raise
    if resp.status_code >= 500:
        raise RuntimeError(f"webhook 5xx: {resp.status_code}")
```

Баг в теле (например, `headers` не dict → `TypeError` в `requests.post`) ретраится 5 раз с backoff → 5× нагрузки + 5 FAILURE-записей в result-backend, вместо одной чистой в Sentry.

**Fix plan (W6):**

```python
autoretry_for=(
    requests.exceptions.RequestException,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    RuntimeError,  # наш raise при 5xx
)
```

Для push — аналогично:
```python
autoretry_for=(
    pywebpush.WebPushException,  # но 404/410 ловятся до raise, см. push.py:55-60
    requests.exceptions.RequestException,
    RuntimeError,
)
```

---

### 8. `companies.reindex_companies_daily` — silent failure (категория B)

**Файл:** `backend/companies/tasks.py:14-44`

```python
try:
    call_command("normalize_companies_data", batch_size=500)
except Exception as e:
    logger.exception("reindex_companies_daily: normalize_companies_data failed: %s", e)

if connection.vendor == "postgresql":
    try:
        call_command("rebuild_company_search_index", chunk=500)
    except Exception as e:
        logger.exception("reindex_companies_daily: Postgres rebuild_company_search_index: %s", e)
```

Все исключения проглатываются → task всегда SUCCESS → **нет алерта в Sentry при постоянном провале нормализации/реиндексации**. Данные FTS могут устаревать сутками без сигнала.

**Fix plan (W4):**
- После `logger.exception` отправлять `sentry_sdk.capture_exception(e)` напрямую (условно, через `if settings.SENTRY_DSN`).
- **Или** пробрасывать `raise` наружу и позволить Celery записать FAILURE — но тогда нужен `autoretry_for=(DatabaseError,), max_retries=2`, чтобы одноразовый flake не уронил FTS-реиндекс.

---

## Clean tasks (no issues found)

| Task | Файл | Защита |
|------|------|--------|
| `audit.purge_old_error_logs` | `backend/audit/tasks.py:34-57` | Малая таблица, DELETE по cutoff идемпотентен |
| `notifications.purge_old_notifications` | `backend/notifications/tasks.py:19-33` | DELETE с фильтром `is_read=True`, идемпотентно |
| `notifications.generate_contract_reminders` | `backend/notifications/tasks.py:36-121` | `exists()` check перед `create`, iterator(chunk_size=500) |
| `tasksapp.generate_recurring_tasks` | `backend/tasksapp/tasks.py:76-225` | **Эталон**: Redis-lock + `select_for_update` + `exists()` + savepoint + IntegrityError handling |
| `messenger.auto_resolve_conversations` | `backend/messenger/tasks.py:18-56` | Только UPDATE с cutoff фильтром, идемпотентно |
| `messenger.check_offline_operators` | `backend/messenger/tasks.py:311-324` | UPDATE по `messenger_last_seen`, идемпотентно |
| `messenger.dispatch_async_listeners` | `backend/messenger/tasks.py:82-121` | ⚠️ См. ниже — регистрация listener'ов сейчас пустая, task фактически no-op |
| `mailer.send_pending_emails` | `backend/mailer/tasks/send.py:77-520` | Лучшая защита в проекте: Redis-lock + `select_for_update(skip_locked)` + SendLog idempotency + recovery |
| `mailer.send_test_email` | `backend/mailer/tasks/send.py:523-633` | On-demand, user триггерит — допустимо без uniqueness |
| `mailer.sync_smtp_bz_quota` | `backend/mailer/tasks/sync.py:129-176` | UPDATE singleton SmtpBzQuota, ловит exceptions внутри |
| `mailer.reconcile_campaign_queue` | `backend/mailer/tasks/reconcile.py:20-114` | Всё идемпотентно: `filter(...).update(...)` + `get_or_create` |

### Примечание о `dispatch_async_listeners`

**Файл:** `backend/messenger/tasks.py:82-121` + `backend/messenger/dispatchers.py:90-103`.

Task вызывается из `EventDispatcher.dispatch(..., run_async=True)` если есть `_async_listeners[event_name]`. Grep по `subscribe.*run_async=True` и `register_async_listener` в проекте ничего не нашёл — **регистрация async-слушателей сейчас не используется**. Task уходит в Celery, возвращает `{"event": ..., "listeners": 0}`.

**Это значит:**
- Риск идемпотентности listener'ов сейчас = 0 (потому что их нет).
- Но архитектурно паттерн остался: если кто-то зарегистрирует async listener, идемпотентность делегируется ему. Нужен guide в `docs/architecture.md` — **«любой async listener обязан быть идемпотентным или иметь собственную защиту»** (cache key, SELECT FOR UPDATE, etc.).

---

## Summary по категориям

| # | Категория | Red-flag count | P2 count | Fix wave |
|---|-----------|----------------|----------|----------|
| A | Bulk без chunking | 0 (кроме уже закрытого `purge_old_activity_events`) | 0 | — |
| B | Network без retry | 1 (`sync_smtp_bz_delivery_events`) | 3 (`clean_old_call_requests`, `send_outbound_webhook`, `send_push_notification`, `reindex_companies_daily`) | W3 / W4 / W6 |
| C | Shared mutable state | 0 | 1 (`sync_smtp_bz_unsubscribes` offset без TTL) | W4 |
| D | Non-idempotent | 3 (`escalate_stalled`, `escalate_waiting`, `send_offline_email_notification`) | 1 (`sync_smtp_bz_delivery_events` SendLog) | W3 |

Общий счёт:
- **P0:** 0 (уже закрытый `purge_old_activity_events` мы не считаем).
- **P1:** 4.
- **P2:** 4.

---

## Hotlist updates

Считаем score по формуле Wave 0.1 (impact × freq × risk, каждое 1-5):

| # | Task | Impact | Freq | Risk | Score | Попадает? |
|---|------|--------|------|------|-------|-----------|
| 1 | `messenger.escalate_stalled_conversations` (idempotency delegation) | 3 (дубль эскалации, отдельный бранч, но видимый) | 5 (beat 120с) | 3 (требует сбой на узком окне) | **45** | Нет (score < 60) |
| 2 | `messenger.escalate_waiting_conversations` (Notification without dedupe) | 4 (spam в колокольчике, user-visible) | 5 (beat 30с) | 4 (частое окно сбоя × frequency) | **80** | **Да** |
| 3 | `messenger.send_offline_email_notification` (throttle before send) | 4 (пропущенный alert оператора = потеря клиента) | 3 (on-demand, зависит от частоты offline) | 4 (flaky SMTP — реален) | **48** | Нет (score < 60) |
| 4 | `mailer.sync_smtp_bz_delivery_events` (no retry + SendLog dup) | 3 (отчёт раздут, send к bounce-боксам при blackout 5xx) | 4 (beat 600с) | 4 (502 от smtp.bz наблюдалось в логах) | **48** | Нет (score < 60) |

**Итог:** только **один** новый item проходит порог ≥ 60 → **`messenger.escalate_waiting_conversations` с score 80**.

### Diff для `docs/audit/hotlist.md` (применить в оркестрирующей сессии)

Добавить **позицию 8** перед разделом «Как использовать этот файл». Точный текст для вставки:

```markdown

## 8. `backend/messenger/tasks.py::escalate_waiting_conversations` — Notification без dedupe

- **Score:** 80 (impact 4 × freq 5 × risk 4)
- **Где лечится:** **Wave 3** (core CRM hardening, вместе с escalate_stalled)
- **Статус сейчас:** работает, но 3 прямых `Notification.objects.create(...)` внутри task, курсор `escalation_level` обновляется **после** create, beat каждые 30 секунд
- **Что переписать:**
  ```python
  # BEFORE:
  if target_level == 3 and conv.branch_id:
      for rop in rops:
          Notification.objects.create(...)    # прямой create
      stats["rop_alert"] += 1
  ...
  Conversation.objects.filter(pk=conv.pk).update(escalation_level=target_level, ...)

  # AFTER:
  with transaction.atomic():
      if target_level == 3 and conv.branch_id:
          for rop in rops:
              notify(
                  user=rop,
                  kind=Notification.Kind.INFO,
                  title=f"Клиент ждёт {int(waiting)} мин — требуется вмешательство",
                  body=...,
                  url=f"/messenger/?conv={conv.id}",
                  payload={"conversation_id": conv.id, "level": "rop_alert"},
                  dedupe_seconds=60,   # <<< защита от двойного beat-тика
              )
          stats["rop_alert"] += 1
      ...
      Conversation.objects.filter(pk=conv.pk).update(
          escalation_level=target_level,
          last_escalated_at=now,
      )
  ```
  Плюс Redis-lock на уровне task (30с timeout):
  ```python
  LOCK_KEY = "messenger:escalate_waiting:lock"
  if not cache.add(LOCK_KEY, "1", timeout=30):
      return {"skipped": "locked"}
  ```
- **Верификация:** Playwright-сценарий «2 оператора, 5 диалогов в waiting 10 мин» → в колокольчике ровно 5 Notification, не 10.
```

Также обновить таблицу в разделе «Как использовать этот файл» → «История изменений» — добавить строку:

```markdown
| 2026-04-20 | Wave 0.2 deep audit celery tasks → добавлен item 8 (`escalate_waiting_conversations`, score 80). |
```

---

## Выводы для W3/W4/W6

1. **W3 (core CRM hardening) — срочно:**
   - Redis-lock + `dedupe_seconds` в `escalate_waiting_conversations` (item 8 hotlist).
   - Redis-lock в `escalate_stalled_conversations` + перестановка `increment` rate-limiter'а после save.
   - Throttle-key **после** SMTP send в `send_offline_email_notification`.
   - `autoretry_for` + UniqueConstraint/hash-dedupe SendLog в `sync_smtp_bz_delivery_events`.

2. **W4 (observability / small fixes):**
   - TTL для курсора `sync_smtp_bz_unsubscribes`.
   - Убрать `raise` в `phonebridge.clean_old_call_requests` **или** добавить `autoretry_for=(DatabaseError,)`.
   - `sentry_sdk.capture_exception` в `reindex_companies_daily`.

3. **W6 (polish):**
   - Сузить `autoretry_for=(Exception,)` до доменных ошибок в `send_outbound_webhook` / `send_push_notification`.
   - Guide «любой async listener обязан быть идемпотентным» в `docs/architecture.md`.
   - Разделить queues: `realtime` (escalate_waiting_*, check_offline_operators, dispatch_async_listeners, send_outbound_webhook, send_push_notification), `email` (send_pending_emails, send_test_email, send_offline_email_notification), `maintenance` (purge_*, sync_smtp_bz_*, reindex_companies_daily, generate_recurring_tasks, generate_contract_reminders, clean_old_call_requests, reconcile_campaign_queue, auto_resolve, escalate_stalled).

---

_Конец Wave 0.2 audit. Source: `backend/*/tasks.py`, `backend/mailer/tasks/*.py`, `backend/messenger/services.py`, `backend/messenger/push.py`, `backend/messenger/dispatchers.py`, `backend/crm/celery.py`, `backend/crm/settings.py::CELERY_BEAT_SCHEDULE`._
