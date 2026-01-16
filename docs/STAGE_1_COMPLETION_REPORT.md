# Отчёт о завершении ЭТАПА 1: Единый контракт CallEvent

## Статус: ✅ ЗАВЕРШЁН

**Дата:** 2024-01-XX  
**Автор:** Cursor Agent

---

## Что сделано

### 1. Документация контракта

✅ **Создан документ:** `docs/CALL_EVENT_CONTRACT.md`

**Содержимое:**
- JSON схема для legacy и extended форматов
- Таблица всех полей с источниками и хранением
- Enum значения (source of truth) для всех типов
- Примеры payload (legacy, extended, minimal, unknown)
- Правила совместимости для Backend, Android, Frontend
- Маппинг статусов между слоями
- Инструкции по тестированию

### 2. Backend: Расширение Serializer и View

✅ **Обновлён файл:** `backend/phonebridge/models.py`

**Изменения:**
- Добавлен `UNKNOWN` в `CallRequest.CallStatus` choices
- Созданы новые enum классы (пока без полей в БД):
  - `CallDirection` (OUTGOING, INCOMING, MISSED, UNKNOWN)
  - `ResolveMethod` (OBSERVER, RETRY, UNKNOWN)
  - `ActionSource` (CRM_UI, NOTIFICATION, HISTORY, UNKNOWN)
- Добавлены TODO комментарии для ЭТАП 3 (миграции БД)

✅ **Обновлён файл:** `backend/phonebridge/api.py`

**Изменения:**
- Расширен `UpdateCallInfoSerializer`:
  - Добавлены optional поля: `call_ended_at`, `direction`, `resolve_method`, `attempts_count`, `action_source`
  - Добавлены валидаторы с graceful обработкой неизвестных значений
  - Неизвестный `call_status` маппится в `UNKNOWN` (не падает с 400)
  - Неизвестные enum поля логируются и игнорируются
- Обновлён `UpdateCallInfoView`:
  - Принимает новые поля, валидирует их
  - Вычисляет `call_ended_at` из `started_at + duration` (если не передан)
  - Логирует новые поля для отладки (ЭТАП 1)
  - Legacy поля сохраняются как раньше
  - Новые поля пока не сохраняются в БД (ЭТАП 3)

### 3. Android: Константы и структуры

✅ **Создан файл:** `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/domain/CallEventContract.kt`

**Содержимое:**
- `CallStatusApi` enum с маппингом из `CallHistoryItem.CallStatus`
- `CallDirection` enum с маппингом из `CallLog.Calls.TYPE`
- `ResolveMethod` enum
- `ActionSource` enum
- `CallEventPayload` data class:
  - Legacy поля (4 поля)
  - Новые поля (optional)
  - Методы `toLegacyJson()` и `toExtendedJson()`

**Важно:** На этом этапе структуры готовы, но реальная отправка новых полей начнётся в ЭТАП 2.

### 4. Frontend: Поддержка "unknown" статуса

✅ **Обновлён файл:** `backend/templates/ui/analytics_user.html`

**Изменения:**
- Добавлена поддержка `call_status == "unknown"` с цветом `text-purple-600`
- Отображается как "Не удалось определить" через `get_call_status_display`

✅ **Обновлён файл:** `backend/templates/ui/settings/calls_manager_detail.html`

**Изменения:**
- Добавлена поддержка `call_status == "unknown"` с текстом "Не удалось определить"

✅ **Обновлён файл:** `backend/ui/views.py`

**Изменения:**
- В `settings_calls_stats()` добавлен учёт `UNKNOWN` статуса в статистике
- В фильтре статусов добавлен `"unknown"` в `status_map`

### 5. Тесты совместимости

✅ **Создан файл:** `backend/phonebridge/tests.py`

**Тесты:**
1. `test_legacy_payload_acceptance` - legacy payload (4 поля) принимается и обрабатывается
2. `test_extended_payload_acceptance` - extended payload принимается без ошибок
3. `test_unknown_status_acceptance` - статус "unknown" принимается и сохраняется
4. `test_unknown_status_graceful_mapping` - неизвестный статус маппится в UNKNOWN
5. `test_minimal_payload_acceptance` - минимальный payload (только call_request_id) принимается
6. `test_ended_at_computation` - call_ended_at вычисляется из started_at + duration
7. `test_invalid_direction_graceful_handling` - неизвестный direction игнорируется

---

## Финальные строки enum/choices

### CallStatus (API значения)

- `"connected"` - Дозвонился
- `"no_answer"` - Не дозвонился
- `"busy"` - Занято
- `"rejected"` - Отклонен
- `"missed"` - Пропущен
- `"unknown"` - Не удалось определить (новое)

### CallDirection (API значения)

- `"outgoing"` - Исходящий
- `"incoming"` - Входящий
- `"missed"` - Пропущенный
- `"unknown"` - Неизвестно

### ResolveMethod (API значения)

- `"observer"` - Определено через ContentObserver
- `"retry"` - Определено через повторные проверки
- `"unknown"` - Неизвестно

### ActionSource (API значения)

- `"crm_ui"` - Команда из CRM
- `"notification"` - Нажатие на уведомление
- `"history"` - Нажатие из истории звонков
- `"unknown"` - Неизвестно

---

## Примеры Payload

### Legacy (старый формат, 4 поля)

```json
{
  "call_request_id": "123e4567-e89b-12d3-a456-426614174000",
  "call_status": "connected",
  "call_started_at": "2024-01-15T14:30:00Z",
  "call_duration_seconds": 180
}
```

### Extended (новый формат, все поля)

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

### UNKNOWN статус

```json
{
  "call_request_id": "123e4567-e89b-12d3-a456-426614174000",
  "call_status": "unknown",
  "call_started_at": "2024-01-15T14:30:00Z"
}
```

---

## Что не сломано для старых клиентов

✅ **Legacy payload продолжает работать:**
- Android старых версий отправляет 4 поля
- Backend принимает и обрабатывает как раньше
- Frontend отображает данные корректно

✅ **Обратная совместимость:**
- Все новые поля optional
- Неизвестные значения обрабатываются gracefully (логирование + fallback)
- Старые клиенты не получают ошибки валидации

✅ **Существующие флоу:**
- Polling продолжает работать
- Отправка call_update продолжает работать
- Очередь оффлайн продолжает работать
- Статистика продолжает считаться

---

## Инструкция "Как проверить"

### 1. Запустить тесты

```bash
cd backend
python manage.py test phonebridge.tests.UpdateCallInfoViewTest
```

**Ожидаемый результат:** Все 7 тестов проходят.

### 2. Проверить legacy payload (curl)

```bash
curl -X POST http://localhost:8000/api/phone/calls/update/ \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "call_request_id": "<uuid>",
    "call_status": "connected",
    "call_started_at": "2024-01-15T14:30:00Z",
    "call_duration_seconds": 180
  }'
```

**Ожидаемый результат:** `200 OK`, данные сохранены как раньше.

### 3. Проверить extended payload (curl)

```bash
curl -X POST http://localhost:8000/api/phone/calls/update/ \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "call_request_id": "<uuid>",
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

**Ожидаемый результат:** `200 OK`, новые поля приняты и залогированы (но пока не сохранены в БД).

### 4. Проверить UNKNOWN статус (curl)

```bash
curl -X POST http://localhost:8000/api/phone/calls/update/ \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "call_request_id": "<uuid>",
    "call_status": "unknown",
    "call_started_at": "2024-01-15T14:30:00Z"
  }'
```

**Ожидаемый результат:** `200 OK`, статус сохранён как "unknown".

### 5. Проверить Frontend

1. Открыть `/analytics/users/<user_id>/`
2. Проверить, что звонки со статусом "unknown" отображаются с текстом "Не удалось определить"
3. Открыть `/settings/calls/stats/`
4. Проверить, что статистика учитывает "unknown" статус

---

## Следующие шаги (ЭТАП 2)

После подтверждения, что ЭТАП 1 работает корректно:

1. **ЭТАП 2:** Android - извлечение и отправка новых полей
2. **ЭТАП 3:** Backend - миграции БД и сохранение новых полей
3. **ЭТАП 4:** Frontend - отображение новых полей и метрик
4. **ЭТАП 5:** Сквозная синхронизация E2E
5. **ЭТАП 6:** Тесты и гарантия "ничего не сломать"

---

## Изменённые файлы

### Backend
- `backend/phonebridge/models.py` - добавлен UNKNOWN статус и новые enum классы
- `backend/phonebridge/api.py` - расширен Serializer и View
- `backend/phonebridge/tests.py` - добавлены тесты совместимости
- `backend/ui/views.py` - добавлен учёт UNKNOWN в статистике
- `backend/templates/ui/analytics_user.html` - поддержка "unknown" статуса
- `backend/templates/ui/settings/calls_manager_detail.html` - поддержка "unknown" статуса

### Android
- `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/domain/CallEventContract.kt` - константы и структуры

### Документация
- `docs/CALL_EVENT_CONTRACT.md` - полный контракт
- `docs/STAGE_1_COMPLETION_REPORT.md` - этот отчёт

---

**Статус:** ✅ Готово к проверке и переходу к ЭТАПУ 2
