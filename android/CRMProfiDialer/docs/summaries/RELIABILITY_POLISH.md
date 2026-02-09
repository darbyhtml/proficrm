# RELIABILITY_POLISH.md

## Цель
Довести систему доставки команд до состояния максимально предсказуемого, диагностируемого и устойчивого на OEM устройствах, с подготовкой к Push/FCM как ускорителю.

---

## Основные изменения

### 1. Hard Cap + Stability гарантии для BURST

**Проблема:** Burst window мог зацикливаться или слишком часто включаться, увеличивая нагрузку.

**Решение:**
- **Hard cap:** Максимум 30 циклов в burst (60s / 2s), после чего принудительный выход
- **Cooldown:** После завершения burst - 25 секунд cooldown, следующий burst нельзя включить раньше (кроме реальной команды)
- **Debounce триггеров:** "APP_OPENED" и "NETWORK_RESTORED" не продлевают burst бесконечно (debounce 10 секунд)
- **Защита от быстрых ответов:** Если сервер отвечает < 300мс, система переходит в adaptive polling (10s) вместо лавины запросов

**Файлы:**
- `CallListenerService.kt`: добавлены `burstCooldownEndsAt`, `burstCycleCount`, `MAX_BURST_CYCLES`, `BURST_COOLDOWN_MS`, `SERVER_FAST_RESPONSE_THRESHOLD_MS`
- Обновлены методы `isInBurstWindow()`, `activateBurstWindow()`, `calculateNextPollDelay()`

---

### 2. Метрики "Time to Delivery" + Диагностика задержек

**Проблема:** Недостаточно метрик для измерения скорости доставки команд.

**Решение:**
- **command_delivery_latency_ms:** Время от создания команды в CRM до получения на телефоне (если сервер присылает createdAt)
- **cycle_wait_time:** Fallback метрика - время ожидания в текущем long-poll цикле
- **429 counters:** `count429LastHour`, `maxBackoffReached`, `timeSpentInBackoffMs`
- **Export в Settings:** В DEBUG режиме показываются средняя доставка, 429/час, время в backoff

**Файлы:**
- `PullCallMetrics.kt`: добавлены `deliveryLatencies`, `cycleWaitStartTime`, `backoffStartTime`, `totalTimeSpentInBackoffMs`, `maxBackoffReached`
- Методы: `recordCommandReceived(createdAtTimestamp)`, `recordBackoffExit()`, `getAverageDeliveryLatencyMs()`, `getTimeSpentInBackoffMs()`, `getCycleWaitTimeMs()`
- `SettingsFragment.kt`: обновлен `updatePullCallMode()` для показа новых метрик

---

### 3. OEM / DOZE / Battery Optimization улучшения

**Проблема:** Даже с foreground service некоторые OEM "режут" работу.

**Решение:**
- **Foreground notification:** Улучшено уведомление с action "Открыть приложение" и динамическим текстом режима работы
- **Network connectivity awareness:** `ConnectivityManager.NetworkCallback` для отслеживания восстановления/потери сети
- **OEM help:** Добавлена кнопка "Инструкции для Xiaomi/Huawei/Samsung" в Settings с текстовыми инструкциями

**Файлы:**
- `CallListenerService.kt`: обновлен `buildForegroundNotification()`, добавлен `updateForegroundNotification()`, `registerNetworkConnectivityCallback()`, `unregisterNetworkConnectivityCallback()`
- `SettingsFragment.kt`: добавлена кнопка `oemHelpButton` и метод `showOemHelpDialog()`
- `fragment_settings.xml`: добавлена кнопка для OEM help

---

### 4. Подготовка к Push/FCM как ускорителю (НЕ включать по умолчанию)

**Цель:** Скелет для будущего Push, но сейчас НЕ включаем.

**Решение:**
- **Feature flag:** `AppFeatures.ENABLE_FCM_ACCELERATOR = false`
- **Заготовка FirebaseMessagingService:** `FcmMessagingService.kt` с обработкой push типа "CALL_COMMAND_AVAILABLE"
- **PullCallCoordinator:** Координатор для пробуждения pullCall цикла извне (`wakeNow()`)
- **Broadcast receivers:** Обработка `ACTION_APP_OPENED` и `ACTION_WAKE_NOW` в `CallListenerService`

**Файлы:**
- `TelemetryMode.kt`: добавлен `ENABLE_FCM_ACCELERATOR` feature flag
- `FcmMessagingService.kt`: новый файл с заготовкой FCM service
- `CallListenerService.kt`: добавлены `registerBroadcastReceivers()`, `handleWakeNow()`, обработка `ACTION_APP_OPENED` и `ACTION_WAKE_NOW`
- `MainActivity.kt`: добавлен `notifyAppOpened()` для отправки broadcast при открытии приложения

**ВАЖНО:** FCM не включен по умолчанию. Для включения:
1. Настроить Firebase Cloud Messaging в проекте
2. Добавить `google-services.json`
3. Изменить `AppFeatures.ENABLE_FCM_ACCELERATOR = true`
4. Добавить `FcmMessagingService` в `AndroidManifest.xml` (если нужно)

---

### 5. Тестируемость и Unit Tests

**Решение:**
- **Unit tests:** `PullCallBackoffTest.kt` с тестами на backoff стратегии (429, network errors, server errors, reset, decrement, cap)
- **Симуляция режимов:** Защита от быстрых ответов сервера через `SERVER_FAST_RESPONSE_THRESHOLD_MS`

**Файлы:**
- `PullCallBackoffTest.kt`: новый файл с unit tests

---

## Измененные файлы

1. `CallListenerService.kt` - основная логика burst cap, cooldown, network awareness, broadcast receivers, FCM координатор
2. `PullCallMetrics.kt` - расширенные метрики доставки, backoff tracking
3. `SettingsFragment.kt` - отображение новых метрик, OEM help dialog
4. `fragment_settings.xml` - кнопка для OEM help
5. `MainActivity.kt` - уведомление об открытии приложения
6. `TelemetryMode.kt` - feature flag для FCM accelerator
7. `FcmMessagingService.kt` - новый файл, заготовка для FCM
8. `PullCallBackoffTest.kt` - новый файл, unit tests

---

## Где включить Push Accelerator

**Файл:** `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/config/TelemetryMode.kt`

**Строка:** Изменить `val ENABLE_FCM_ACCELERATOR: Boolean = false` на `true`

**Как работает:**
1. При получении push "CALL_COMMAND_AVAILABLE" → `FcmMessagingService.onMessageReceived()`
2. Вызывается `PullCallCoordinator.wakeNow(reason="PUSH")`
3. `CallListenerService.handleWakeNow()` отменяет backoff, активирует burst на 15s, немедленно запускает pullCall

**ВАЖНО:** Push - только ускоритель. Основная доставка остается через long-poll / adaptive polling.

---

## Ручной тест-план (10 пунктов)

1. **Burst hard cap:** Открыть приложение → проверить, что burst не длится больше 60 секунд → после завершения проверить cooldown 25 секунд
2. **Debounce APP_OPENED:** Открыть/закрыть/открыть приложение быстро → проверить, что burst не продлевается бесконечно
3. **Защита от быстрых ответов:** Если сервер отвечает мгновенно (< 300мс) → проверить, что система переходит в SLOW режим (10s) без лавины запросов
4. **429 backoff:** Симулировать 429 → проверить, что backoff ≤ 15s, burst отменяется, cooldown активируется
5. **Network recovery:** Airplane mode → включить сеть → проверить, что burst активируется один раз (с debounce)
6. **Network loss:** При потере сети → проверить, что система переходит в SLOW без лишних запросов
7. **Foreground notification:** Проверить, что уведомление показывает текущий режим работы и имеет action "Открыть"
8. **OEM help:** Открыть Settings → нажать "Инструкции для Xiaomi/Huawei/Samsung" → проверить, что показываются правильные инструкции для текущего устройства
9. **Метрики в Settings:** Проверить, что в DEBUG режиме показываются средняя доставка, 429/час, время в backoff
10. **Экран выключен/фон:** Выключить экран → проверить, что сервис жив, уведомление видно, команды получаются

---

## Критерии приемки

✅ Burst не может зациклиться и не может продлеваться бесконечно от "app opened"
✅ Есть метрика доставки команды (реальная или приближенная) + 429 counters
✅ Уведомление foreground читаемое + action открыть приложение
✅ Улучшены подсказки по OEM/батарее (текст/экран)
✅ Сеть: recovery → burst once, loss → no spam
✅ Есть заготовка под push-ускоритель под флагом, не влияющая на текущий билд
✅ Добавлены минимальные unit tests на backoff
✅ Ничего не сломано

---

## Примечания

- Все изменения обратно совместимы
- FCM не включен по умолчанию (требует настройки Firebase)
- Unit tests находятся в `app/src/test/java/`
- Метрики в DEBUG режиме показываются только в Settings (не нагружают UI)
