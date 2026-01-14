# Code Review Report: CRM Backend + Android App
**Дата:** 2026-01-14  
**Reviewer:** Principal Engineer (Django/DRF + Senior Android + Security/DevOps)  
**Цель:** Проверка production-readiness, поиск багов/уязвимостей, унификация логики

---

## 1. Executive Summary

### ✅ Что хорошо:
1. **Транзакционная целостность**: `select_for_update(skip_locked=True)` в `PullCallView` предотвращает race conditions
2. **Безопасность очереди**: Room миграции без `fallbackToDestructiveMigration()`, корректная обработка retry
3. **Маскирование PII**: Логи маскируются перед отправкой (токены, пароли, номера)
4. **RBAC**: Корректная фильтрация по ролям и филиалам в статистике звонков
5. **Rate limiting**: Middleware защищает API endpoints от DDoS
6. **Timezone awareness**: `USE_TZ=True`, корректное использование `timezone.now()`
7. **Адаптивный polling**: Джиттер и адаптивная частота снижают нагрузку
8. **Оффлайн-очередь**: Надежная доставка данных при сетевых сбоях

### ⚠️ Оставшиеся риски (не блокируют production test):
1. **Нет защиты от дубликатов call_request_id**: Повторные отправки из очереди могут создать дубликаты в CallRequest (требует миграцию БД)
2. **Security-crypto alpha**: Альфа-версия в production, возможны неожиданные падения на старых Android (21-22). Мониторинг через `encryption_enabled`, fallback работает
3. **Нет per-user throttling**: Только rate limiting по IP (60 req/min), один пользователь теоретически может забить API (низкий риск)

---

## 2. Repository Map

### Backend Structure:
```
backend/
├── crm/                    # Core Django settings, URLs, middleware
│   ├── settings.py         # USE_TZ=True, security headers, rate limiting
│   ├── urls.py             # Main routing: /api/phone/*, /api/token/*
│   └── middleware.py       # SecurityHeadersMiddleware
├── phonebridge/            # Mobile app integration
│   ├── models.py           # PhoneDevice, CallRequest, PhoneTelemetry, PhoneLogBundle
│   ├── api.py              # PullCallView, UpdateCallInfoView, DeviceHeartbeatView, etc.
│   └── management/commands/cleanup_telemetry_logs.py
├── accounts/               # User management, RBAC
│   ├── middleware.py       # RateLimitMiddleware (60 req/min для phone API)
│   └── security.py         # get_client_ip(), rate limiting logic
├── ui/                     # Django templates UI
│   ├── views.py            # settings_calls_stats, settings_mobile_*
│   └── urls.py             # UI routing
└── templates/ui/settings/  # Mobile devices, call stats templates
```

### Android Structure:
```
android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/
├── MainActivity.kt              # Login, permissions, service start
├── OnboardingActivity.kt        # First-time user guide
├── CallListenerService.kt      # Foreground service, polling loop
├── queue/
│   ├── AppDatabase.kt          # Room DB, миграция 0→1
│   ├── QueueItem.kt            # Entity
│   ├── QueueDao.kt             # DAO (getPending, incrementRetry, deleteOldFailed)
│   └── QueueManager.kt         # Enqueue, flushQueue (max 3 retries)
└── logs/
    ├── LogCollector.kt         # In-memory buffer
    ├── LogSender.kt            # Masking PII, отправка в CRM
    └── LogInterceptor.kt       # Wrapper around android.util.Log
```

### Зависимости и границы:
- **Backend → Android**: REST API (`/api/phone/*`), JWT auth
- **Android → Backend**: OkHttp, EncryptedSharedPreferences для токенов
- **Нарушения границ**: Нет (чистая архитектура)

---

## 3. Findings Table

| Severity | Component | Описание | Файл:строка | Риск | Как проверить | Рекомендация | Статус |
|----------|-----------|----------|-------------|------|---------------|--------------|--------|
| **HIGH** | Backend DB | Нет защиты от дубликатов call_request_id при повторных отправках из очереди | `phonebridge/api.py:186-229` | Дубликаты в аналитике, искажение статистики | Отправить один call_request_id дважды из очереди | Добавить `unique=True` на `call_request_id` или проверку перед созданием | ⚠️ Требует миграцию |
| **HIGH** | Android Security | Security-crypto alpha в production | `app/build.gradle:39` | Неожиданные падения на старых Android (21-22) | Тест на Android 7/8 | Мониторинг через `encryption_enabled`, fallback работает | ⚠️ Мониторится |
| **MEDIUM** | Backend API | Нет throttling per-user для phone endpoints | `accounts/middleware.py:46-54` | Один пользователь теоретически может забить API | Отправить 1000 запросов с одного токена | Добавить DRF throttling класс для phone API (опционально) | ⚠️ Низкий риск |
| **MEDIUM** | Backend DB | Нет индекса на `call_request_id` для быстрого поиска дубликатов | `phonebridge/models.py:46-108` | Медленный поиск дубликатов | Запрос с `call_request_id` в WHERE | Добавить индекс или unique constraint | ⚠️ После миграции |

**Примечание:** Все CRITICAL и остальные HIGH/MEDIUM проблемы исправлены в предыдущих коммитах.

---

## 4. Однотипность (Conventions)

### Правила, которые соблюдаются:
1. ✅ **Naming**: `device_id`, `last_seen_at`, `last_poll_*`, `encryption_enabled` — единообразно
2. ✅ **Timezone**: Всегда `timezone.now()`, `timezone.localtime()`, `USE_TZ=True`
3. ✅ **Permissions**: Все phone API используют `IsAuthenticated`
4. ✅ **Error handling**: Единый формат `{"detail": "..."}` для ошибок
5. ✅ **Logging**: Структурированное логирование с контекстом

### Нарушения (исправлено):
1. ✅ **Получение IP**: Исправлено — используется `accounts.security.get_client_ip()` везде

### Оставшиеся улучшения (опционально):
2. **RBAC проверки**: Дублирование логики проверки ролей в `settings_calls_stats` и `settings_calls_manager_detail`
   - Можно создать декоратор `@require_phone_access` или утилиту (не критично)

3. **Валидация device_id**: Повторяется в нескольких views
   - Можно создать mixin `DeviceValidationMixin` (не критично)

---

## 5. Production Readiness Checklist

### Backend:
- ✅ Миграции корректны (nullable, defaults, indexes)
- ✅ Транзакции используются (`select_for_update` в PullCallView)
- ✅ Timezone awareness (`USE_TZ=True`)
- ✅ Rate limiting (60 req/min для phone API)
- ✅ Валидация X-Forwarded-For с allowlist прокси
- ✅ Лимиты на payload (logs 50KB, telemetry 100 items)
- ✅ XSS защита в логах (экранирование)
- ⚠️ **НЕТ защиты от дубликатов call_request_id** (HIGH, требует миграцию)
- ⚠️ **НЕТ per-user throttling** (MEDIUM, низкий риск)

### Android:
- ✅ Room миграции без destructive
- ✅ Foreground Service корректно настроен (notification channel, START_STICKY)
- ✅ Permissions handling (runtime для READ_CALL_LOG, POST_NOTIFICATIONS)
- ✅ Graceful degradation (работает без call log permissions)
- ✅ PII masking перед отправкой
- ✅ Адаптивный polling + jitter
- ✅ Mutex для refresh token (kotlinx.coroutines.sync.Mutex)
- ✅ Алерт при max retries (отправка через heartbeat)
- ⚠️ **Security-crypto alpha** (HIGH, мониторится через encryption_enabled)

---

## 6. Patch Plan

### SAFE (A) — Не меняет поведение:

#### Patch 1: Валидация X-Forwarded-For
**Файл:** `backend/crm/settings.py`
```python
# После строки 68 (CSRF_TRUSTED_ORIGINS)
# Trust proxy headers only from our own proxy
USE_X_FORWARDED_HOST = True
USE_X_FORWARDED_FOR = True
# В production: установить IP прокси через env
PROXY_IPS = [ip.strip() for ip in os.getenv("DJANGO_PROXY_IPS", "").split(",") if ip.strip()]
```

**Файл:** `backend/phonebridge/api.py` (строка 77-83)
```python
# Заменить на:
from accounts.security import get_client_ip
ip = get_client_ip(request)  # Использует единую логику с валидацией
```

#### Patch 2: Лимит размера payload
**Файл:** `backend/phonebridge/api.py` (строка 309)
```python
class PhoneLogBundleSerializer(serializers.Serializer):
    # ... existing fields ...
    payload = serializers.CharField(max_length=50000)  # ~50KB max
```

**Файл:** `backend/phonebridge/api.py` (строка 262)
```python
class TelemetryBatchSerializer(serializers.Serializer):
    device_id = serializers.CharField(max_length=64, required=False, allow_blank=True)
    items = TelemetryItemSerializer(many=True, max_length=100)  # Max 100 items per batch
```

#### Patch 3: XSS защита в логах
**Файл:** `backend/templates/ui/settings/mobile_device_detail.html` (строка 123)
```django
<pre class="...">{{ l.payload|escape }}</pre>
```

#### Patch 4: Унификация получения IP
**Файл:** `backend/phonebridge/api.py` (строка 77-83)
```python
from accounts.security import get_client_ip
ip = get_client_ip(request)
```

### RISKY (B) — Требует migration plan:

#### Patch 5: Защита от дубликатов call_request_id
**Migration:** `backend/phonebridge/migrations/0006_add_call_request_id_unique.py`
```python
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('phonebridge', '0005_phonedevice_encryption_enabled')]
    
    operations = [
        migrations.AddField(
            model_name='callrequest',
            name='call_request_id',
            field=models.UUIDField(null=True, blank=True, unique=True, db_index=True),
        ),
        # Backfill: создать call_request_id = id для существующих записей
        migrations.RunPython(backfill_call_request_id, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='callrequest',
            name='call_request_id',
            field=models.UUIDField(unique=True, db_index=True),
        ),
    ]
```

**Файл:** `backend/phonebridge/api.py` (UpdateCallInfoView)
```python
# Добавить проверку перед обновлением:
call_request_id = s.validated_data["call_request_id"]
if CallRequest.objects.filter(call_request_id=call_request_id).exists():
    # Идемпотентность: если уже обработан, возвращаем успех
    return Response({"ok": True, "already_processed": True})
```

#### Patch 6: Mutex для refresh token (Android)
**Файл:** `android/.../CallListenerService.kt` (после строки 55)
```kotlin
@Volatile
private var isRefreshing = false
```

**Файл:** `android/.../CallListenerService.kt` (refreshAccess, строка 418)
```kotlin
private suspend fun refreshAccess(...): String? = withContext(Dispatchers.IO) {
    synchronized(this@CallListenerService) {
        if (isRefreshing) {
            // Ждем завершения текущего refresh
            delay(1000)
            return securePrefs().getString(KEY_TOKEN, null)
        }
        isRefreshing = true
        try {
            // ... existing refresh logic ...
        } finally {
            isRefreshing = false
        }
    }
}
```

---

## 7. Test Plan

### Backend Unit/Integration Tests:
```python
# tests/test_phone_api_security.py
def test_x_forwarded_for_validation():
    """Проверка валидации IP за прокси: доверяет XFF только если REMOTE_ADDR в allowlist"""
    # Тест 1: REMOTE_ADDR в PROXY_IPS → использует X-Forwarded-For
    # Тест 2: REMOTE_ADDR не в PROXY_IPS → использует REMOTE_ADDR (защита от spoofing)
    # Тест 3: X-Forwarded-For отсутствует → использует REMOTE_ADDR
    
def test_payload_size_limit():
    """Проверка лимита размера payload (50KB)"""
    # Тест: payload > 50KB → ValidationError
    
def test_telemetry_batch_limit():
    """Проверка лимита батча (100 items) с явной валидацией"""
    # Тест 1: 100 items → OK
    # Тест 2: 101 items → ValidationError "Максимум 100 items за раз"
    
def test_queue_stuck_alert():
    """Проверка алерта при max retries в очереди"""
    # Тест: heartbeat с queue_stuck=true → last_error_code="queue_stuck" в PhoneDevice
    
def test_rbac_calls_stats():
    """Проверка RBAC: менеджер видит только свои звонки"""
    # Тест: менеджер не видит звонки других менеджеров
```

### Android Manual Test Matrix:
| Android Version | Test Scenario | Expected Result |
|----------------|---------------|-----------------|
| 7.0 (API 24) | Login → Service start | Работает, fallback на обычные prefs если crypto не поддерживается |
| 8.0 (API 26) | Notification channel | Создается корректно |
| 10.0 (API 29) | Background restrictions | Service работает в foreground |
| 13.0 (API 33) | POST_NOTIFICATIONS | Запрашивается, service останавливается если denied |
| 14.0 (API 34) | All features | Полная функциональность |

### End-to-End Smoke Checklist:
1. ✅ Login → device register → service start
2. ✅ Pull 204 длительно → adaptive polling (1.5 → 3 → 5 сек)
3. ✅ Pull 200 → ACTION_DIAL → result → send update → analytics
4. ✅ Сеть пропала → queue → сеть появилась → flush → backend отражает данные
5. ✅ 401 access → refresh → continue
6. ✅ Refresh expired → stop service → user prompted → old session cleared
7. ✅ Админ в CRM видит device status/alerts/logs/telemetry
8. ✅ Руководитель видит аналитику по своим менеджерам, менеджер — только свою

---

## 8. Final Verdict

### ✅ Исправлено (SAFE патчи применены):
1. ✅ **X-Forwarded-For с валидацией прокси** (CRITICAL) — исправлено: доверяет XFF только если REMOTE_ADDR в allowlist прокси (`PROXY_IPS`)
2. ✅ **Лимиты на payload** (CRITICAL) — исправлено: logs 50KB, telemetry batch 100 items с явной валидацией `validate_items()`
3. ✅ **XSS защита в логах** (MEDIUM) — исправлено: экранирование через `|escape` в шаблоне
4. ✅ **Унификация получения IP** (LOW) — исправлено: используется `accounts.security.get_client_ip()` везде
5. ✅ **Mutex для refresh token** (HIGH) — исправлено: `kotlinx.coroutines.sync.Mutex` предотвращает race condition
6. ✅ **Алерт при max retries** (MEDIUM) — исправлено: отправка алерта в CRM через heartbeat при достижении max retries (3)

### ⚠️ Осталось (не блокирует production test):
1. **Нет защиты от дубликатов call_request_id** (HIGH) — требует миграцию БД, можно после теста
2. **Security-crypto alpha** (HIGH) — мониторинг через `encryption_enabled`, fallback работает корректно

### ✅ Можно оставить на после теста:
3. Per-user throttling (MEDIUM) — rate limiting по IP работает (60 req/min для phone API)

---

## Рекомендации по приоритетам:

1. ✅ **Срочно (до production test):** — **ВЫПОЛНЕНО**
   - ✅ Patch 1: Валидация X-Forwarded-For
   - ✅ Patch 2: Лимиты на payload
   - ✅ Patch 3: XSS защита
   - ✅ Patch 6: Mutex для refresh token
   - ✅ Алерт при max retries

2. **Важно (после production test):**
   - Patch 5: Защита от дубликатов call_request_id (требует миграцию)

3. **Улучшения (опционально):**
   - Per-user throttling (rate limiting по IP работает)

---

**Статус:** ✅ **Критические SAFE-патчи применены. Проект готов к production test.**

**Обновлено:** 2026-01-14 — применены все SAFE-патчи:
- Безопасная обработка X-Forwarded-For с allowlist прокси (`PROXY_IPS` env)
- Лимиты на payload с явной валидацией (DRF `max_length` не работает для `many=True`)
- Mutex для refresh token (Android) через `kotlinx.coroutines.sync.Mutex`
- Алерт в CRM при max retries очереди (отправка через heartbeat)

---

## Примененные патчи (конкретные изменения):

### 1. Безопасная обработка X-Forwarded-For
**Файл:** `backend/accounts/security.py`
- Добавлена проверка `REMOTE_ADDR in PROXY_IPS` перед использованием X-Forwarded-For
- Если прокси не в allowlist → используется `REMOTE_ADDR` (защита от spoofing)

**Файл:** `backend/crm/settings.py`
- Добавлен `PROXY_IPS` из env переменной `DJANGO_PROXY_IPS`

### 2. Валидация telemetry batch
**Файл:** `backend/phonebridge/api.py`
- Добавлен метод `validate_items()` в `TelemetryBatchSerializer` (явная проверка длины списка)
- Лимит: максимум 100 items за раз

### 3. Mutex для refresh token
**Файл:** `android/.../CallListenerService.kt`
- Добавлен `refreshMutex = Mutex()`
- `refreshAccessWithMutex()` использует `mutex.withLock` для предотвращения параллельных refresh
- `pullCallWithRefresh()` теперь `suspend fun` для корректной работы с mutex

### 4. Алерт при max retries
**Файл:** `android/.../QueueManager.kt`
- При достижении `retryCount >= 3` отправляется алерт через `sendQueueStuckAlert()`
- Алерт отправляется в `/api/phone/devices/heartbeat/` с `queue_stuck=true`

**Файл:** `backend/phonebridge/api.py`
- `DeviceHeartbeatSerializer` расширен полями `queue_stuck`, `stuck_items`, `stuck_count`
- При `queue_stuck=true` сохраняется в `PhoneDevice.last_error_code="queue_stuck"` и `last_error_message`
