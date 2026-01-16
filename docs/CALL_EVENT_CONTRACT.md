# Контракт CallEvent / CallUpdate

## Обзор

Единый контракт для синхронизации данных о звонках между Android приложением и CRM Backend.

**Endpoint:** `POST /api/phone/calls/update/`

**Версия:** 1.0 (расширенная, обратно совместимая с legacy)

---

## JSON Схема

### Legacy формат (4 поля, обратная совместимость)

```json
{
  "call_request_id": "uuid-string",
  "call_status": "connected" | "no_answer" | "rejected" | "missed" | "busy",
  "call_started_at": "2024-01-01T12:00:00Z",
  "call_duration_seconds": 120
}
```

### Extended формат (со всеми optional полями)

```json
{
  "call_request_id": "uuid-string",
  "call_status": "connected" | "no_answer" | "rejected" | "missed" | "busy" | "unknown",
  "call_started_at": "2024-01-01T12:00:00Z",
  "call_duration_seconds": 120,
  "call_ended_at": "2024-01-01T12:02:00Z",
  "direction": "outgoing" | "incoming" | "missed" | "unknown",
  "resolve_method": "observer" | "retry" | "unknown",
  "attempts_count": 3,
  "action_source": "crm_ui" | "notification" | "history" | "unknown"
}
```

---

## Таблица полей

| Поле | Тип | Optional | Источник (Android) | Хранение (Backend) | Отображение (Frontend) |
|------|-----|----------|-------------------|-------------------|----------------------|
| `call_request_id` | UUID (string) | **Required** | Из команды CRM (polling) | `CallRequest.id` (PK) | Не показывается напрямую |
| `call_status` | String (enum) | Optional | Определяется из CallLog (type + duration) | `CallRequest.call_status` | Цветной бейдж/текст |
| `call_started_at` | DateTime (ISO 8601) | Optional | Из `CallLog.Calls.DATE` | `CallRequest.call_started_at` | `d.m.Y H:i` |
| `call_duration_seconds` | Integer >= 0 | Optional | Из `CallLog.Calls.DURATION` | `CallRequest.call_duration_seconds` | Секунды или форматированный |
| `call_ended_at` | DateTime (ISO 8601) | Optional | Вычисляется: `started_at + duration` | `CallRequest.call_ended_at` (новое) | `d.m.Y H:i` |
| `direction` | String (enum) | Optional | Из `CallLog.Calls.TYPE` | `CallRequest.direction` (новое) | Текст/иконка |
| `resolve_method` | String (enum) | Optional | Логика: OBSERVER vs RETRY | `CallRequest.resolve_method` (новое) | Текст (для диагностики) |
| `attempts_count` | Integer >= 0 | Optional | Из `PendingCall.attempts` | `CallRequest.attempts_count` (новое) | Число (для диагностики) |
| `action_source` | String (enum) | Optional | Определяется: NOTIFICATION/HISTORY/CRM_UI | `CallRequest.action_source` (новое) | Текст/иконка |

---

## Enum значения (source of truth)

### CallStatus

**API значения (lowercase):**
- `"connected"` - Разговор состоялся
- `"no_answer"` - Не ответили
- `"rejected"` - Сброс/Отклонён
- `"missed"` - Пропущен
- `"busy"` - Занято
- `"unknown"` - Не удалось определить результат (новое)

**Backend модель:** `CallRequest.CallStatus`

**Android enum:** `CallStatusApi` (в domain)

**Маппинг из CallLog:**
- `OUTGOING_TYPE + duration > 0` → `"connected"`
- `OUTGOING_TYPE + duration == 0` → `"no_answer"`
- `MISSED_TYPE` → `"no_answer"`
- `INCOMING_TYPE + duration > 0` → `"connected"`
- `INCOMING_TYPE + duration == 0` → `"no_answer"`
- `REJECTED_TYPE (5)` → `"rejected"`
- Не удалось определить → `"unknown"`

### CallDirection

**API значения (lowercase):**
- `"outgoing"` - Исходящий
- `"incoming"` - Входящий
- `"missed"` - Пропущенный
- `"unknown"` - Неизвестно

**Backend модель:** `CallRequest.CallDirection` (новое)

**Android enum:** `CallDirection` (в domain)

**Маппинг из CallLog:**
- `CallLog.Calls.OUTGOING_TYPE (2)` → `"outgoing"`
- `CallLog.Calls.INCOMING_TYPE (1)` → `"incoming"`
- `CallLog.Calls.MISSED_TYPE (3)` → `"missed"`
- Иначе → `"unknown"`

### ResolveMethod

**API значения (lowercase):**
- `"observer"` - Определено через ContentObserver (CallLogObserverManager)
- `"retry"` - Определено через повторные проверки (CallListenerService.scheduleCallLogChecks)
- `"unknown"` - Неизвестно

**Backend модель:** `CallRequest.ResolveMethod` (новое)

**Android enum:** `ResolveMethod` (в domain)

**Логика определения:**
- Если результат найден через `CallLogObserverManager` → `"observer"`
- Если результат найден через `CallListenerService.scheduleCallLogChecks()` → `"retry"`
- Иначе → `"unknown"`

### ActionSource

**API значения (lowercase):**
- `"crm_ui"` - Команда из CRM (polling)
- `"notification"` - Нажатие на уведомление "Пора позвонить"
- `"history"` - Нажатие "Перезвонить" из истории звонков
- `"unknown"` - Неизвестно (ручной звонок или не отслеживается)

**Backend модель:** `CallRequest.ActionSource` (новое)

**Android enum:** `ActionSource` (в domain)

**Логика определения:**
- Команда из polling → `"crm_ui"`
- Нажатие на уведомление → `"notification"`
- Нажатие "Перезвонить" из истории → `"history"`
- Иначе → `"unknown"`

---

## Примеры Payload

### Пример 1: Legacy (старый формат, 4 поля)

```json
{
  "call_request_id": "123e4567-e89b-12d3-a456-426614174000",
  "call_status": "connected",
  "call_started_at": "2024-01-15T14:30:00Z",
  "call_duration_seconds": 180
}
```

**Ожидаемое поведение:**
- ✅ Backend принимает и сохраняет как раньше
- ✅ Frontend отображает статус, время, длительность
- ✅ Новые поля не требуются

### Пример 2: Extended (новый формат, все поля)

```json
{
  "call_request_id": "123e4567-e89b-12d3-a456-426614174000",
  "call_status": "connected",
  "call_started_at": "2024-01-15T14:30:00Z",
  "call_duration_seconds": 180,
  "call_ended_at": "2024-01-15T14:33:00Z",
  "direction": "outgoing",
  "resolve_method": "observer",
  "attempts_count": 1,
  "action_source": "crm_ui"
}
```

**Ожидаемое поведение:**
- ✅ Backend принимает и сохраняет все поля (если миграции применены)
- ✅ Frontend отображает все доступные поля
- ✅ Если миграции не применены — новые поля игнорируются, но запрос не падает

### Пример 3: Минимальный (только call_request_id)

```json
{
  "call_request_id": "123e4567-e89b-12d3-a456-426614174000"
}
```

**Ожидаемое поведение:**
- ✅ Backend принимает (валидация мягкая, все поля optional кроме call_request_id)
- ✅ Если других полей нет — ничего не обновляется, но 200 OK

### Пример 4: UNKNOWN статус

```json
{
  "call_request_id": "123e4567-e89b-12d3-a456-426614174000",
  "call_status": "unknown",
  "call_started_at": "2024-01-15T14:30:00Z",
  "direction": "outgoing",
  "resolve_method": "retry",
  "attempts_count": 3
}
```

**Ожидаемое поведение:**
- ✅ Backend принимает статус "unknown"
- ✅ Frontend отображает "Не удалось определить результат"
- ✅ Учитывается в метриках как отдельная категория

---

## Правила совместимости

### Backend

1. **Приём legacy payload:**
   - Все поля кроме `call_request_id` — optional
   - Если новых полей нет — запрос обрабатывается как раньше
   - Новые поля игнорируются, если миграции не применены (но запрос не падает)

2. **Валидация:**
   - `call_request_id` — required, UUID
   - `call_status` — если передан, должен быть из choices (включая "unknown")
   - `call_duration_seconds` — если передан, должен быть >= 0
   - Новые enum поля — если передан, должны быть из choices, иначе логируется и игнорируется

3. **Вычисление `call_ended_at`:**
   - Если `call_ended_at` не передан, но есть `call_started_at` и `call_duration_seconds`:
     - Вычисляется: `call_ended_at = call_started_at + timedelta(seconds=call_duration_seconds)`
   - Только если оба поля валидны и не null

4. **Обработка неизвестных значений:**
   - Если передан неизвестный `call_status` — логируется, маппится в "unknown" (не 400)
   - Если передан неизвестный `direction/resolve_method/action_source` — логируется, игнорируется (не 400)

### Android

1. **Отправка legacy payload:**
   - Старые версии Android продолжают отправлять 4 поля
   - Новые версии могут отправлять extended формат

2. **Подготовка данных:**
   - `direction` — извлекается из `CallLog.Calls.TYPE`
   - `resolve_method` — определяется по источнику (OBSERVER vs RETRY)
   - `attempts_count` — из `PendingCall.attempts`
   - `action_source` — определяется по контексту (NOTIFICATION/HISTORY/CRM_UI)
   - `call_ended_at` — вычисляется: `startedAt + durationSeconds`

### Frontend

1. **Отображение legacy данных:**
   - Если новых полей нет — не показывать их, а не ломаться
   - Если `call_status == "unknown"` — показывать "Не удалось определить результат"

2. **Безопасный рендер:**
   - Все новые поля проверяются на наличие перед отображением
   - Если поля нет — показывать "—" или не показывать секцию

---

## Маппинг статусов

### Android → API

| Android CallHistoryItem.CallStatus | API call_status | Backend CallRequest.CallStatus |
|-------------------------------------|-----------------|-------------------------------|
| `CONNECTED` | `"connected"` | `CONNECTED` |
| `NO_ANSWER` | `"no_answer"` | `NO_ANSWER` |
| `REJECTED` | `"rejected"` | `REJECTED` |
| `UNKNOWN` | `"unknown"` | `UNKNOWN` (новое) |

**Примечание:** `MISSED` и `BUSY` определяются на уровне CallLog и маппятся напрямую.

### CallLog → Android

| CallLog.Calls.TYPE | CallLog.Calls.DURATION | Android CallStatus | API call_status |
|-------------------|------------------------|-------------------|-----------------|
| `OUTGOING_TYPE (2)` | `> 0` | `CONNECTED` | `"connected"` |
| `OUTGOING_TYPE (2)` | `== 0` | `NO_ANSWER` | `"no_answer"` |
| `MISSED_TYPE (3)` | — | `NO_ANSWER` | `"no_answer"` |
| `INCOMING_TYPE (1)` | `> 0` | `CONNECTED` | `"connected"` |
| `INCOMING_TYPE (1)` | `== 0` | `NO_ANSWER` | `"no_answer"` |
| `REJECTED_TYPE (5)` | — | `REJECTED` | `"rejected"` |
| Не найдено | — | `UNKNOWN` | `"unknown"` |

---

## Версионирование

**Текущая версия:** 1.0 (extended, обратно совместимая)

**Legacy версия:** 0.x (4 поля)

**Правила:**
- Legacy формат поддерживается бессрочно
- Новые поля всегда optional
- Неизвестные значения обрабатываются gracefully (логирование + fallback)

---

## Тестирование совместимости

### Тест 1: Legacy payload

```bash
curl -X POST https://crm.groupprofi.ru/api/phone/calls/update/ \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "call_request_id": "123e4567-e89b-12d3-a456-426614174000",
    "call_status": "connected",
    "call_started_at": "2024-01-15T14:30:00Z",
    "call_duration_seconds": 180
  }'
```

**Ожидаемый результат:** `200 OK`, данные сохранены как раньше.

### Тест 2: Extended payload

```bash
curl -X POST https://crm.groupprofi.ru/api/phone/calls/update/ \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "call_request_id": "123e4567-e89b-12d3-a456-426614174000",
    "call_status": "connected",
    "call_started_at": "2024-01-15T14:30:00Z",
    "call_duration_seconds": 180,
    "call_ended_at": "2024-01-15T14:33:00Z",
    "direction": "outgoing",
    "resolve_method": "observer",
    "attempts_count": 1,
    "action_source": "crm_ui"
  }'
```

**Ожидаемый результат:** `200 OK`, все поля приняты (если миграции применены) или игнорированы (если нет).

### Тест 3: UNKNOWN статус

```bash
curl -X POST https://crm.groupprofi.ru/api/phone/calls/update/ \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "call_request_id": "123e4567-e89b-12d3-a456-426614174000",
    "call_status": "unknown",
    "call_started_at": "2024-01-15T14:30:00Z"
  }'
```

**Ожидаемый результат:** `200 OK`, статус сохранён как "unknown".

---

**Дата создания:** 2024-01-XX  
**Автор:** Cursor Agent  
**Статус:** ✅ Активен (ЭТАП 1)
