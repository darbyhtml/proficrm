# Инвентаризация: Аналитика звонков (ЭТАП 0)

## Цель
Полная инвентаризация всех мест, где используются данные о звонках, для последующего добавления полной аналитики с синхронизацией Android ↔ Backend ↔ Frontend.

---

## 1. ANDROID: Источники данных о звонках

### 1.1. Чтение CallLog

**Файлы:**
- `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/data/CallLogObserverManager.kt`
- `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/CallListenerService.kt`

**Что читается:**
- `CallLog.Calls.NUMBER` - номер телефона
- `CallLog.Calls.TYPE` - тип звонка (OUTGOING_TYPE=2, INCOMING_TYPE=1, MISSED_TYPE=3, REJECTED_TYPE=5)
- `CallLog.Calls.DURATION` - длительность в секундах (Long)
- `CallLog.Calls.DATE` - timestamp начала звонка (Long, миллисекунды)

**Где используется:**
- `CallLogObserverManager.readCallLogForPhone()` - поиск звонка по номеру в временном окне (±5 минут)
- `CallListenerService.readCallLogForPhone()` - повторные проверки (5, 10, 15 секунд)
- `CallLogObserverManager.handleCallResult()` - обработка найденного результата
- `CallListenerService.handleCallResult()` - обработка найденного результата

**Данные, которые извлекаются:**
```kotlin
data class CallInfo(
    val type: Int,        // OUTGOING_TYPE, INCOMING_TYPE, MISSED_TYPE, REJECTED_TYPE
    val duration: Long,   // секунды
    val date: Long        // timestamp начала звонка
)
```

### 1.2. Определение статуса звонка

**Файлы:**
- `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/CallListenerService.kt` (метод `determineHumanStatus()`)
- `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/data/CallLogObserverManager.kt` (метод `determineCallStatus()`)

**Логика определения:**
- `OUTGOING_TYPE + duration > 0` → `CONNECTED` ("Разговор состоялся")
- `OUTGOING_TYPE + duration == 0` → `NO_ANSWER` ("Не ответили")
- `MISSED_TYPE` → `NO_ANSWER` ("Не ответили")
- `INCOMING_TYPE + duration > 0` → `CONNECTED` ("Разговор состоялся")
- `INCOMING_TYPE + duration == 0` → `NO_ANSWER` ("Не ответили")
- `REJECTED_TYPE (5)` → `REJECTED` ("Сброс")
- Иначе → `UNKNOWN` ("Не удалось определить результат")

**Маппинг для CRM (старый формат):**
- `CONNECTED` → `"connected"`
- `NO_ANSWER` → `"no_answer"`
- `REJECTED` → `"rejected"`
- `UNKNOWN` → не отправляется (или `"no_answer"`)

### 1.3. Модель CallHistoryItem (локальная история)

**Файл:** `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/domain/CallHistoryItem.kt`

**Поля:**
```kotlin
data class CallHistoryItem(
    val id: String,                    // call_request_id из CRM
    val phone: String,                 // номер телефона
    val phoneDisplayName: String? = null, // имя из контактов (если есть)
    val status: CallStatus,             // CONNECTED, NO_ANSWER, REJECTED, UNKNOWN
    val statusText: String,             // человеческий текст статуса
    val durationSeconds: Int? = null,   // длительность в секундах (если есть)
    val startedAt: Long,                // timestamp начала звонка
    val sentToCrm: Boolean = false,      // отправлено ли в CRM
    val sentToCrmAt: Long? = null       // когда отправлено
)
```

**Что НЕ хранится:**
- ❌ Направление звонка (OUTGOING/INCOMING/MISSED)
- ❌ Метод определения результата (OBSERVER/RETRY/UNKNOWN)
- ❌ Количество попыток определения
- ❌ Источник действия пользователя (NOTIFICATION/HISTORY/CRM_UI)
- ❌ Время окончания звонка (только startedAt + duration)

### 1.4. Модель PendingCall (ожидаемые звонки)

**Файл:** `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/domain/PendingCall.kt`

**Поля:**
```kotlin
data class PendingCall(
    val callRequestId: String,        // ID запроса из CRM
    val phoneNumber: String,          // номер телефона (нормализованный)
    val startedAtMillis: Long,        // время начала ожидания (когда открыли звонилку)
    val state: PendingState,          // PENDING, RESOLVING, RESOLVED, FAILED
    val attempts: Int = 0              // количество попыток проверки
)
```

**Что НЕ хранится:**
- ❌ Источник действия (откуда пришла команда)

### 1.5. Отправка данных в CRM

**Файл:** `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/network/ApiClient.kt`

**Метод:** `sendCallUpdate()`

**Отправляемые данные:**
```json
{
  "call_request_id": "uuid",
  "call_status": "connected" | "no_answer" | "rejected",
  "call_started_at": "2024-01-01T12:00:00Z",  // ISO 8601 UTC
  "call_duration_seconds": 120  // optional, только если > 0
}
```

**Что НЕ отправляется:**
- ❌ Направление звонка (OUTGOING/INCOMING/MISSED)
- ❌ Метод определения результата (OBSERVER/RETRY/UNKNOWN)
- ❌ Количество попыток
- ❌ Источник действия пользователя
- ❌ Время окончания звонка (только started_at + duration)

**Очередь оффлайн:**
- При отсутствии интернета данные сохраняются в `QueueItem` (Room)
- Тип: `"call_update"`
- Endpoint: `"/api/phone/calls/update/"`
- Payload: JSON строка

### 1.6. Действия пользователя

**Файлы:**
- `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/core/CallFlowCoordinator.kt` - обработка команды на звонок
- `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/ui/CallsHistoryActivity.kt` - действия из истории

**Источники действий:**
1. **NOTIFICATION** - нажатие на уведомление "Пора позвонить" (через `CallFlowCoordinator.handleCallCommand()`)
2. **HISTORY** - нажатие "Перезвонить" из истории (`CallsHistoryActivity` → `openDialer()`)
3. **CRM_UI** - команда из CRM (polling → `CallFlowCoordinator.handleCallCommand()`)
4. **UNKNOWN** - ручной звонок (не отслеживается)

**Что НЕ отслеживается:**
- ❌ Источник действия не сохраняется и не отправляется в CRM
- ❌ Действие "Скопировать номер" не логируется

---

## 2. BACKEND: Приём и хранение данных

### 2.1. API Endpoint

**Файл:** `backend/phonebridge/api.py`

**Endpoint:** `POST /api/phone/calls/update/`

**View:** `UpdateCallInfoView`

**Serializer:** `UpdateCallInfoSerializer`

**Принимаемые данные:**
```python
{
    "call_request_id": UUID (required),
    "call_status": "connected" | "no_answer" | "busy" | "rejected" | "missed" (optional),
    "call_started_at": DateTime ISO 8601 (optional),
    "call_duration_seconds": Integer >= 0 (optional)
}
```

**Валидация:**
- `call_request_id` должен существовать и принадлежать текущему пользователю
- `call_status` должен быть из `CallRequest.CallStatus.choices`
- `call_duration_seconds` должен быть >= 0

**Что НЕ принимается:**
- ❌ Направление звонка
- ❌ Метод определения результата
- ❌ Количество попыток
- ❌ Источник действия пользователя
- ❌ Время окончания звонка

### 2.2. Модель CallRequest

**Файл:** `backend/phonebridge/models.py`

**Поля, связанные с результатом звонка:**
```python
class CallRequest(models.Model):
    # ... другие поля ...
    
    # Статус звонка
    call_status = CharField(
        max_length=16,
        choices=CallStatus.choices,  # CONNECTED, NO_ANSWER, BUSY, REJECTED, MISSED
        null=True,
        blank=True,
        db_index=True
    )
    
    # Время начала звонка
    call_started_at = DateTimeField(null=True, blank=True)
    
    # Длительность в секундах
    call_duration_seconds = IntegerField(null=True, blank=True)
```

**Что НЕ хранится:**
- ❌ Направление звонка (OUTGOING/INCOMING/MISSED)
- ❌ Метод определения результата (OBSERVER/RETRY/UNKNOWN)
- ❌ Количество попыток определения
- ❌ Источник действия пользователя (NOTIFICATION/HISTORY/CRM_UI)
- ❌ Время окончания звонка (только started_at + duration)

**Индексы:**
- `["user", "status", "created_at"]` - для фильтрации по пользователю и статусу

### 2.3. Статистика и аналитика

**Файл:** `backend/ui/views.py`

**View:** `settings_calls_stats()` (строка 5264)

**Что считается:**
- `total` - всего звонков с `call_status__isnull=False`
- `connected` - звонков со статусом `CONNECTED`
- `no_answer` - звонков со статусом `NO_ANSWER`
- `busy` - звонков со статусом `BUSY`
- `rejected` - звонков со статусом `REJECTED`
- `missed` - звонков со статусом `MISSED`
- `total_duration` - сумма `call_duration_seconds` (только для CONNECTED)
- `avg_duration` - средняя длительность (total_duration / total)

**Фильтры:**
- Период: день/месяц
- Менеджер (user_id)
- Статус звонка

**Что НЕ считается:**
- ❌ Дозвоняемость % (connected / total)
- ❌ "Не удалось определить" (UNKNOWN статус не существует в БД)
- ❌ Средняя длительность по статусам (только общая)
- ❌ Метрики по направлению звонка
- ❌ Метрики по методу определения результата

**View:** `analytics_user()` (строка 652)

**Что показывается:**
- Список звонков с `note="UI click"` (только инициированные через кнопку в CRM)
- Фильтр по периоду (день/неделя/месяц)
- Статус, время начала, длительность (если есть)

**View:** `settings_calls_manager_detail()` (строка 5455)

**Что показывается:**
- Детальный список звонков менеджера
- Таблица: дата/время, номер, компания/контакт, исход, длительность
- Фильтры: период, исход звонка

---

## 3. FRONTEND: Отображение данных

### 3.1. История звонков в CRM

**Файл:** `backend/templates/ui/analytics_user.html` (строка 147)

**Что показывается:**
- Статус звонка (цветной бейдж)
- Время начала (`call_started_at`)
- Длительность (`call_duration_seconds` → форматируется через `duration_formatted`)

**Что НЕ показывается:**
- ❌ Направление звонка
- ❌ Метод определения результата
- ❌ Количество попыток
- ❌ Источник действия
- ❌ Время окончания

**Файл:** `backend/templates/ui/settings/calls_manager_detail.html`

**Таблица:**
- Дата/время (из `call_started_at`)
- Номер (`phone_raw`)
- Компания/контакт
- Исход (цветной текст)
- Длительность (секунды, если есть)

**Что НЕ показывается:**
- ❌ Направление звонка
- ❌ Метод определения результата
- ❌ Количество попыток
- ❌ Источник действия
- ❌ Время окончания

### 3.2. Статистика звонков

**Файл:** `backend/templates/ui/settings/calls_stats.html` (используется в `settings_calls_stats()`)

**Что показывается:**
- Общая статистика по менеджерам
- Метрики: total, connected, no_answer, busy, rejected, missed
- Длительность: total_duration, avg_duration

**Что НЕ показывается:**
- ❌ Дозвоняемость % (connected / total * 100)
- ❌ "Не удалось определить" (UNKNOWN)
- ❌ Метрики по направлению
- ❌ Метрики по методу определения

---

## 4. ТАБЛИЦА "ИСТОЧНИК ПРАВДЫ"

| Поле | Где создаётся (Android) | Как передаётся (API) | Где хранится (Backend) | Как отображается (Frontend) |
|------|-------------------------|---------------------|------------------------|----------------------------|
| `call_request_id` | Из команды CRM (polling) | `call_request_id` (UUID) | `CallRequest.id` (UUID, PK) | Не показывается напрямую |
| `phone` | Из команды CRM или ручной ввод | Не отправляется отдельно | `CallRequest.phone_raw` (CharField) | `phone_raw` (форматированный) |
| `call_status` | Определяется из CallLog (type + duration) | `call_status` (string) | `CallRequest.call_status` (CharField, choices) | Цветной бейдж/текст |
| `call_started_at` | Из CallLog.Calls.DATE | `call_started_at` (ISO 8601) | `CallRequest.call_started_at` (DateTimeField) | `call_started_at|date:"d.m.Y H:i"` |
| `call_duration_seconds` | Из CallLog.Calls.DURATION | `call_duration_seconds` (int) | `CallRequest.call_duration_seconds` (IntegerField) | `call_duration_seconds` сек или форматированный |
| `direction` | ❌ НЕ извлекается | ❌ НЕ отправляется | ❌ НЕ хранится | ❌ НЕ показывается |
| `resolve_method` | ❌ НЕ отслеживается | ❌ НЕ отправляется | ❌ НЕ хранится | ❌ НЕ показывается |
| `attempts_count` | Есть в `PendingCall.attempts` | ❌ НЕ отправляется | ❌ НЕ хранится | ❌ НЕ показывается |
| `action_source` | ❌ НЕ отслеживается | ❌ НЕ отправляется | ❌ НЕ хранится | ❌ НЕ показывается |
| `ended_at` | ❌ НЕ извлекается (только startedAt + duration) | ❌ НЕ отправляется | ❌ НЕ хранится | ❌ НЕ показывается |

---

## 5. ОПРЕДЕЛЕНИЕ "ДЫРОК" (что есть, но не доходит до CRM)

### 5.1. Данные, которые есть на телефоне, но не доходят до CRM

1. **Направление звонка (direction)**
   - ✅ Есть в `CallLog.Calls.TYPE` (OUTGOING_TYPE, INCOMING_TYPE, MISSED_TYPE)
   - ❌ НЕ извлекается явно в `CallInfo`
   - ❌ НЕ отправляется в CRM
   - ❌ НЕ хранится в БД
   - ❌ НЕ показывается в UI

2. **Метод определения результата (resolve_method)**
   - ✅ Есть логика: `CallLogObserverManager` (OBSERVER) vs `CallListenerService.scheduleCallLogChecks()` (RETRY)
   - ❌ НЕ сохраняется явно
   - ❌ НЕ отправляется в CRM
   - ❌ НЕ хранится в БД
   - ❌ НЕ показывается в UI

3. **Количество попыток определения (attempts_count)**
   - ✅ Есть в `PendingCall.attempts`
   - ❌ НЕ отправляется в CRM
   - ❌ НЕ хранится в БД
   - ❌ НЕ показывается в UI

4. **Источник действия пользователя (action_source)**
   - ✅ Можно определить: NOTIFICATION (уведомление), HISTORY (история), CRM_UI (polling)
   - ❌ НЕ отслеживается явно
   - ❌ НЕ отправляется в CRM
   - ❌ НЕ хранится в БД
   - ❌ НЕ показывается в UI

5. **Время окончания звонка (ended_at)**
   - ✅ Можно вычислить: `startedAt + durationSeconds`
   - ❌ НЕ извлекается явно
   - ❌ НЕ отправляется в CRM
   - ❌ НЕ хранится в БД
   - ❌ НЕ показывается в UI

6. **Статус "Не удалось определить" (UNKNOWN)**
   - ✅ Есть в `CallHistoryItem.CallStatus.UNKNOWN`
   - ❌ НЕ отправляется в CRM (маппится в `"no_answer"` или не отправляется)
   - ❌ НЕ хранится в БД (нет такого статуса в `CallRequest.CallStatus`)
   - ❌ НЕ показывается в UI как отдельная метрика

### 5.2. Данные, которые доходят, но не хранятся

Нет таких данных (всё, что отправляется, хранится).

### 5.3. Данные, которые хранятся, но не показываются/не считаются

1. **Дозвоняемость %**
   - ✅ Данные есть: `call_status = CONNECTED` vs `call_status != CONNECTED`
   - ❌ НЕ считается в `settings_calls_stats()`
   - ❌ НЕ показывается в UI

2. **"Не удалось определить"**
   - ❌ Статус UNKNOWN не существует в БД
   - ❌ НЕ считается
   - ❌ НЕ показывается

3. **Средняя длительность по статусам**
   - ✅ Данные есть: `call_duration_seconds` для CONNECTED
   - ❌ НЕ считается отдельно по статусам
   - ❌ НЕ показывается

4. **Метрики по направлению звонка**
   - ❌ Направление не хранится
   - ❌ НЕ считается
   - ❌ НЕ показывается

5. **Метрики по методу определения результата**
   - ❌ Метод не хранится
   - ❌ НЕ считается
   - ❌ НЕ показывается

---

## 6. ВЫВОДЫ И РЕКОМЕНДАЦИИ

### 6.1. Что нужно добавить

1. **В Android:**
   - Извлекать `direction` из `CallLog.Calls.TYPE`
   - Отслеживать `resolve_method` (OBSERVER vs RETRY)
   - Отслеживать `action_source` (NOTIFICATION, HISTORY, CRM_UI)
   - Сохранять `attempts_count` из `PendingCall.attempts`
   - Вычислять `ended_at` = `startedAt + durationSeconds`
   - Отправлять статус UNKNOWN в CRM (или добавить отдельный статус)

2. **В Backend:**
   - Добавить поля в `CallRequest`: `direction`, `resolve_method`, `attempts_count`, `action_source`, `ended_at`
   - Добавить статус UNKNOWN в `CallRequest.CallStatus` (или обрабатывать отдельно)
   - Расширить `UpdateCallInfoSerializer` для приёма новых полей (optional)
   - Добавить расчёт дозвоняемости % в `settings_calls_stats()`
   - Добавить метрики по направлению, методу определения, источнику действия

3. **В Frontend:**
   - Показывать направление звонка (если есть)
   - Показывать метод определения результата (если есть)
   - Показывать источник действия (если есть)
   - Показывать дозвоняемость % в статистике
   - Показывать "Не удалось определить" как отдельную метрику
   - Показывать время окончания звонка (если есть)

### 6.2. Обратная совместимость

- Все новые поля должны быть **optional** (nullable)
- Backend должен принимать старый payload без новых полей
- Frontend должен корректно работать, если новые поля отсутствуют
- Android старых версий должен продолжать работать с новым backend

---

## 7. СЛЕДУЮЩИЕ ШАГИ

1. **ЭТАП 1:** Создать единый контракт `CallEvent` с новыми полями (optional)
2. **ЭТАП 2:** Обновить Android для извлечения и отправки новых данных
3. **ЭТАП 3:** Обновить Backend для приёма, валидации и хранения новых данных
4. **ЭТАП 4:** Обновить Frontend для отображения новых данных и метрик
5. **ЭТАП 5:** Проверить сквозную синхронизацию E2E
6. **ЭТАП 6:** Добавить тесты и гарантировать обратную совместимость

---

**Дата создания:** 2024-01-XX  
**Автор:** Cursor Agent  
**Статус:** ✅ Завершено (ЭТАП 0)
