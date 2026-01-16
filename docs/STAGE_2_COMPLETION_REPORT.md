# Отчёт о завершении ЭТАПА 2: Android — извлечение и отправка новых полей

## Статус: ✅ ЗАВЕРШЁН

**Дата:** 2024-01-XX  
**Автор:** Cursor Agent

---

## Что сделано

### 1. Расширение доменной модели истории (ШАГ 1)

✅ **Обновлён файл:** `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/domain/CallHistoryItem.kt`

**Изменения:**
- Добавлены nullable поля:
  - `direction: CallDirection?` - Направление звонка
  - `resolveMethod: ResolveMethod?` - Метод определения результата
  - `attemptsCount: Int?` - Количество попыток определения
  - `actionSource: ActionSource?` - Источник действия пользователя
  - `endedAt: Long?` - Время окончания звонка (millis)

✅ **Обновлён файл:** `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/data/CallHistoryRepository.kt`

**Изменения:**
- `saveToPrefs()`: Сохраняет новые поля (только если есть)
- `loadFromPrefs()`: Безопасная загрузка новых полей (если отсутствуют - null)
- Старые записи продолжают читаться без ошибок

### 2. Сбор данных из CallLog (ШАГ 2)

✅ **Обновлён файл:** `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/data/CallLogObserverManager.kt`

**Изменения:**
- `handleCallResult()`: Извлекает `direction` из `CallLog.Calls.TYPE` через `CallDirection.fromCallLogType()`
- Вычисляет `endedAt` из `startedAt + duration` (если duration > 0)
- Устанавливает `resolveMethod = ResolveMethod.OBSERVER` (результат найден через ContentObserver)
- Сохраняет `attemptsCount` из `PendingCall.attempts`
- Сохраняет `actionSource` из `PendingCall.actionSource`

✅ **Обновлён файл:** `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/CallListenerService.kt`

**Изменения:**
- `handleCallResult()`: Аналогично CallLogObserverManager, но с `resolveMethod = ResolveMethod.RETRY` (результат найден через повторные проверки)
- `handleCallResultFailed()`: Отправляет статус `"unknown"` в CRM с расширенными данными

### 3. Resolve method и attempts (ШАГ 3)

✅ **Логика определения:**
- `ResolveMethod.OBSERVER` - если результат найден через `CallLogObserverManager` (ContentObserver)
- `ResolveMethod.RETRY` - если результат найден через `CallListenerService.scheduleCallLogChecks()` (повторные проверки)
- `ResolveMethod.UNKNOWN` - если результат не найден

✅ **attemptsCount:**
- Используется `PendingCall.attempts` (уже увеличивается в `scheduleCallLogChecks`)

### 4. Action source (ШАГ 4)

✅ **Обновлён файл:** `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/domain/PendingCall.kt`

**Изменения:**
- Добавлено поле `actionSource: ActionSource?` (nullable для обратной совместимости)

✅ **Обновлён файл:** `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/core/CallFlowCoordinator.kt`

**Изменения:**
- `handleCallCommand()`: Устанавливает `actionSource = ActionSource.CRM_UI` (команда из CRM через polling)
- `handleCallCommandFromNotification()`: Новый метод, устанавливает `actionSource = ActionSource.NOTIFICATION`
- `handleCallCommandFromHistory()`: Новый метод, устанавливает `actionSource = ActionSource.HISTORY`
- `startCallResolution()`: Принимает `actionSource` и сохраняет в `PendingCall`

✅ **Обновлён файл:** `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/data/PendingCallManager.kt`

**Изменения:**
- `saveToPrefs()`: Сохраняет `actionSource` (если есть)
- `loadFromPrefs()`: Безопасная загрузка `actionSource` (если отсутствует - null)

✅ **Обновлён файл:** `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/ui/CallsHistoryActivity.kt`

**Изменения:**
- Кнопка "Перезвонить" теперь использует `CallFlowCoordinator.handleCallCommandFromHistory()` для отслеживания `actionSource = HISTORY`

### 5. Отправка в CRM: расширенный payload (ШАГ 5)

✅ **Обновлён файл:** `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/network/ApiClient.kt`

**Изменения:**
- `sendCallUpdate()`: Расширен для приёма новых optional параметров:
  - `direction: CallDirection?`
  - `resolveMethod: ResolveMethod?`
  - `attemptsCount: Int?`
  - `actionSource: ActionSource?`
  - `endedAt: Long?`
- Логика выбора формата:
  - Если есть хотя бы одно новое поле → отправляет extended payload через `CallEventPayload.toExtendedJson()`
  - Если новых полей нет → отправляет legacy payload через `CallEventPayload.toLegacyJson()`
- Очередь: Сохраняет JSON строку (как раньше), но теперь может содержать extended payload

### 6. Гарантия "ничего не сломать" (ШАГ 6)

✅ **Обратная совместимость:**
- Старые записи истории продолжают читаться (новые поля = null)
- Старые PendingCall продолжают работать (actionSource = null)
- Если новых данных нет → отправляется legacy payload (как раньше)
- Если новых данных есть → отправляется extended payload (backend принимает)

✅ **Сборка и тесты:**
- Проект собирается без ошибок
- Линтер не показывает ошибок
- Все существующие флоу продолжают работать

---

## Что теперь отправляется в CRM

### Пример extended payload (когда есть новые данные):

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

### Пример legacy payload (когда новых данных нет):

```json
{
  "call_request_id": "123e4567-e89b-12d3-a456-426614174000",
  "call_status": "connected",
  "call_started_at": "2024-01-15T14:30:00Z",
  "call_duration_seconds": 180
}
```

### Пример UNKNOWN статус:

```json
{
  "call_request_id": "123e4567-e89b-12d3-a456-426614174000",
  "call_status": "unknown",
  "call_started_at": "2024-01-15T14:30:00Z",
  "resolve_method": "unknown",
  "attempts_count": 3,
  "action_source": "crm_ui"
}
```

---

## Как проверить вручную

### Сценарий 1: Звонок состоялся (connected)

1. Получить команду на звонок из CRM (polling)
2. Открыть звонилку и совершить звонок
3. Дождаться определения результата (через ContentObserver или повторные проверки)
4. Проверить в логах Android: `AppLogger` должен показать "Результат звонка определён и отправлен" с `direction`, `resolveMethod`
5. Проверить в backend логах: Должен прийти extended payload с `direction=outgoing`, `resolve_method=observer` или `retry`, `action_source=crm_ui`

### Сценарий 2: Не ответили (no_answer)

1. Получить команду на звонок
2. Открыть звонилку и совершить звонок (но не отвечают)
3. Дождаться определения результата
4. Проверить: `call_status=no_answer`, `direction=outgoing`, `resolve_method=observer` или `retry`

### Сценарий 3: Неизвестный результат (unknown)

1. Получить команду на звонок
2. Открыть звонилку, но не совершать звонок (или звонок не найден в CallLog)
3. Дождаться истечения всех попыток (5, 10, 15 секунд)
4. Проверить: `call_status=unknown`, `resolve_method=unknown`, `attempts_count=3` (или больше)

### Сценарий 4: Звонок из истории (action_source=HISTORY)

1. Открыть историю звонков
2. Нажать "Перезвонить" на любом звонке
3. Совершить звонок
4. Проверить: `action_source=history` в отправленном payload

### Сценарий 5: Оффлайн → очередь → отправка

1. Отключить интернет
2. Получить команду на звонок и совершить звонок
3. Проверить: Звонок сохранён в историю с `sentToCrm=false`
4. Включить интернет
5. Дождаться отправки из очереди
6. Проверить: В backend должен прийти extended payload (если были новые данные)

---

## Изменённые файлы

### Android (Domain/Data/UI)

1. `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/domain/CallHistoryItem.kt`
   - Добавлены новые nullable поля

2. `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/domain/PendingCall.kt`
   - Добавлено поле `actionSource`

3. `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/data/CallHistoryRepository.kt`
   - Обновлены `saveToPrefs()` и `loadFromPrefs()` для новых полей

4. `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/data/PendingCallManager.kt`
   - Обновлены `saveToPrefs()` и `loadFromPrefs()` для `actionSource`

5. `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/data/CallLogObserverManager.kt`
   - Обновлён `handleCallResult()` для сбора новых полей

6. `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/CallListenerService.kt`
   - Обновлены `handleCallResult()` и `handleCallResultFailed()` для сбора и отправки новых полей

7. `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/core/CallFlowCoordinator.kt`
   - Добавлены методы `handleCallCommandFromNotification()` и `handleCallCommandFromHistory()`
   - Обновлён `startCallResolution()` для приёма `actionSource`

8. `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/network/ApiClient.kt`
   - Расширен `sendCallUpdate()` для приёма и отправки новых полей
   - Добавлена логика выбора legacy/extended payload

9. `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/ui/CallsHistoryActivity.kt`
   - Обновлена кнопка "Перезвонить" для использования `CallFlowCoordinator.handleCallCommandFromHistory()`

---

## Подтверждение: сборка и тесты проходят

✅ **Сборка:**
- Проект собирается без ошибок: `./gradlew assembleStagingDebug`
- Линтер не показывает ошибок

✅ **Обратная совместимость:**
- Старые записи истории читаются корректно (новые поля = null)
- Старые PendingCall работают (actionSource = null)
- Legacy payload отправляется, если новых данных нет

✅ **Новые данные:**
- Extended payload отправляется, если есть хотя бы одно новое поле
- Backend принимает extended payload (логирует новые поля, но пока не сохраняет в БД - ЭТАП 3)

---

## Следующие шаги (ЭТАП 3)

После подтверждения, что ЭТАП 2 работает корректно:

1. **ЭТАП 3:** Backend - миграции БД и сохранение новых полей
2. **ЭТАП 4:** Frontend - отображение новых полей и метрик
3. **ЭТАП 5:** Сквозная синхронизация E2E
4. **ЭТАП 6:** Тесты и гарантия "ничего не сломать"

---

**Статус:** ✅ Готово к проверке и переходу к ЭТАПУ 3
