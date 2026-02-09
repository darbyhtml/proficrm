# Changelog — CRM Profi Dialer (Android)

Все значимые изменения проекта приводятся в этом файле.

---

## [Unreleased] — Соответствие лучшим практикам (архитектура, безопасность, производительность, интеграция с CRM)

### Производительность

- **I/O на Dispatchers.IO**: Все сетевые вызовы, работа с БД и SharedPreferences в ApiClient, TokenManager, OnboardingActivity, QueueManager, LogsActivity выполняются через `withContext(Dispatchers.IO)` или `CoroutineScope(Dispatchers.IO)`.
- **Отсутствие runBlocking на main thread**: Удалён синхронный вызов из UI: отчёт диагностики строится только через `SupportReportBuilder.buildReport()` из корутины; статус очереди в SupportHealthActivity загружается асинхронно через `addCheckItemQueueAsync()`. Единственный оставшийся `runBlocking` — в `QueueManager.enqueue()`; вызывается только из контекста IO (ApiClient, LogSender), не из UI.
- **Отложенная инициализация**: Тяжёлые компоненты (TokenManager, AppContainer) инициализируются после первого кадра через `Choreographer.postFrameCallback` и `applicationScope.launch(Dispatchers.IO)` в `CRMApplication.onCreate()`.
- **StrictMode в debug**: Включён в debug-сборках с `detectDiskReads()`, `detectDiskWrites()`, `detectNetwork()`; нарушения логируются только для стека пакета `ru.groupprofi` (penaltyListener с фильтром по className).

### Безопасность

- **Токены в EncryptedSharedPreferences**: TokenManager использует `EncryptedSharedPreferences` с fallback на обычные prefs при ошибке; инициализация выполняется на Dispatchers.IO.
- **Маскирование чувствительных данных в логах**: В MainActivity при запуске CallListenerService в лог передаётся маскированный `device_id` (первые 4 + `***` + последние 4). AppLogger маскирует Bearer-токены, пароли, device_id, номера телефонов перед записью в буфер и файл.
- **Логи в release**: В AppLogger в release не пишутся сообщения уровня DEBUG и VERBOSE. Файл логов хранится только во внутренней памяти приложения (`context.filesDir`).

### Архитектура

- **Нет блокирующих вызовов в UI**: Проверка onboarding и загрузка статуса очереди выполняются в корутинах с `withContext(Dispatchers.IO)`; shareDiagnostics вызывает `buildReport()` из `lifecycleScope.launch`.
- **Lifecycle корутин**: В AutoRecoveryManager при отмене job обрабатывается `CancellationException` (rethrow без логирования), чтобы в логах не появлялись сообщения вида "StandaloneCoroutine was cancelled".
- **Навигация без дублирования активностей**:
  - MainActivity → Login: `FLAG_ACTIVITY_NEW_TASK | FLAG_ACTIVITY_CLEAR_TASK | FLAG_ACTIVITY_CLEAR_TOP`.
  - LoginActivity → MainActivity: `FLAG_ACTIVITY_CLEAR_TOP | FLAG_ACTIVITY_NEW_TASK | FLAG_ACTIVITY_CLEAR_TASK`.
  - OnboardingActivity → MainActivity: `FLAG_ACTIVITY_CLEAR_TOP | FLAG_ACTIVITY_NEW_TASK | FLAG_ACTIVITY_CLEAR_TASK`.

### Интеграция с CRM

- **Форсированная отправка телеметрии**: При 429 и 401 в CallListenerService вызывается `scope.launch { apiClient.flushTelemetry() }`. При выходе в MainActivity перед очисткой токенов и остановкой сервиса вызывается `lifecycleScope.launch(Dispatchers.IO) { apiClient.flushTelemetry() }`. При резолве звонка (handleCallResolved / handleCallResultUnknown) по-прежнему вызывается `apiClient.flushTelemetry()`.

### Логика приложения

- **PortraitCaptureActivity**: Ориентация устанавливается один раз за lifecycle (`orientationSet`); сброс флага — в `onDestroy()`. Вызов `super.onResume()` при каждом onResume сохранён для корректного возобновления камеры при возврате в экран.

### Обработка ошибок

- **CrashLogStore**: Сохранение краша выполняется в отдельном потоке через `Executors.newSingleThreadExecutor().execute { ... }` в `CRMApplication.setupCrashHandler()`, а не в потоке краша (избегаем disk I/O на main/crash thread).

### Дополнительно

- **Удалён deprecated API**: Удалён метод `SupportReportBuilder.build()` с `runBlocking`; единственный способ получить отчёт — `buildReport(context)` из корутины.
- **Замечания**:
  - Часть инфраструктурного кода (TokenManager, QueueManager, CrashLogStore, LogSender и др.) по-прежнему использует `android.util.Log` для WARN/ERROR; сообщения не содержат чувствительных данных. При необходимости их можно постепенно перевести на AppLogger для единообразия и маскирования.
  - `QueueManager.enqueue()` использует `runBlocking(Dispatchers.IO)` для гарантии записи в БД до выхода; вызывается только из suspend-функций на IO — не из main thread.
  - Unit-тесты имеются для domain (CallDirection, CallEventPayload, CallStatsUseCase, PhoneNumberNormalizer, ResolveMethodActionSource) и для SafeHttpLoggingInterceptor; остальная логика (ApiClient, QueueManager, TokenManager, сервисы) пока не покрыта тестами — рекомендуется добавлять по мере доработок.

---

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.0.0/).
