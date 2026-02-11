# Карта кода CRMProfiDialer («что где лежит»)

Этот документ отвечает на вопрос: **что куда и как устроено в коде** Android‑приложения.

---

## Главная точка входа

- **`CRMApplication.kt`**  
  Инициализация `TokenManager`, `AppContainer`, глобального `LogCollector`, crash handler и т.п.

- **`core/AppContainer.kt`**  
  Простой Service Locator. Здесь связываются:
  - domain интерфейсы (`CallHistoryStore`, `PendingCallStore`, `AppReadinessProvider`);
  - data реализации (`CallHistoryRepository`, `PendingCallManager`, `AppReadinessChecker`);
  - инфраструктура (`ApiClient`, `TokenManager`, `AppNotificationManager`, `AutoRecoveryManager`, `CallFlowCoordinator`).

---

## UI (экраны) и «куда нажимает пользователь»

Папка: `ui/`

| Экран | Файл | За что отвечает |
|------|------|-----------------|
| Логин | `ui/login/LoginActivity.kt` | Вход по логину/паролю через `ApiClient.login()` |
| QR‑логин | `QRLoginActivity.kt` | Скан/обмен QR через `ApiClient.exchangeQrToken()` |
| Онбординг | `ui/onboarding/OnboardingActivity.kt` | Разрешения/фон/подсказки |
| Главная | `ui/home/HomeFragment.kt` | Статус готовности, статистика, быстрые действия |
| Телефон | `ui/dialer/DialerFragment.kt` | Ручной звонок, создание `PendingCall` с `ActionSource.MANUAL` |
| История | `ui/history/HistoryFragment.kt` + `CallDetailBottomSheet.kt` | История звонков, поиск, детали, «перезвонить» |
| Настройки | `ui/settings/SettingsFragment.kt` | Разрешения, OEM‑инструкции, диагностика (dev‑mode) |
| Поддержка | `ui/support/SupportHealthActivity.kt` | Состояние очереди/диагностические проверки |

Главная Activity:
- **`MainActivity.kt`**: Bottom Navigation, запуск/контроль `CallListenerService`, обработка «выхода».

---

## Фоновая логика: сервис и обработка команд

- **`CallListenerService.kt`**  
  Foreground‑сервис и «двигатель» приложения:
  - тянет команды через `ApiClient.pullCall()` (`/api/phone/calls/pull/`);
  - управляет режимами опроса (`LONG_POLL/BURST/SLOW`), backoff, cooldown, rate‑limit;
  - инициирует обработку команды через `AppContainer.callFlowCoordinator`;
  - инициирует периодические операции: heartbeat, flush очереди, отправку логов;
  - поднимает `CallLogObserverManager`, если есть разрешения.

- **`core/CallFlowCoordinator.kt`**  
  Координатор «команда → действия»:
  - `handleCallCommand()` — команда пришла из CRM (`ActionSource.CRM_UI`): показывает уведомление, открывает звонилку, создаёт `PendingCall`;
  - `handleCallCommandFromNotification()` — пользователь нажал уведомление (`ActionSource.NOTIFICATION`);
  - `handleCallCommandFromHistory()` — «перезвонить» из истории (`ActionSource.HISTORY`).

---

## Сеть и интеграция с CRM

Папка: `network/`

| Компонент | Файл | Что делает |
|----------|------|------------|
| HTTP клиент и API | `ApiClient.kt` | Все запросы к CRM: auth, pullCall, update, heartbeat, telemetry, logs |
| Auth header + refresh | `AuthInterceptor.kt` | Добавляет `Authorization: Bearer …`, вызывает refresh при 401 |
| Backoff pullCall | `PullCallBackoff.kt` | Политика backoff именно для `pullCall` |
| Метрики pullCall | `PullCallMetrics.kt` | Режимы, деградации, 429/час, backoff time |
| Общий backoff | `RateLimitBackoff.kt` | Exponential backoff для некоторых API |
| Телеметрия | `TelemetryBatcher.kt`, `TelemetryInterceptor.kt` | Батчинг телеметрии и привязка к запросам |
| Безопасный HTTP‑логгер | `SafeHttpLoggingInterceptor.kt` | Логи без утечек PII/токенов |

Точные эндпойнты и payload’ы см. в `API_INTEGRATION.md`.

---

## Данные: история, ожидания, сопоставление CallLog

Папки: `data/`, `domain/`, `queue/`

- **История звонков**
  - `domain/CallHistoryItem.kt` — модель истории и статусы.
  - `domain/CallHistoryStore.kt` — интерфейс.
  - `data/CallHistoryRepository.kt` — реализация (хранение/обновления/markSent).

- **Ожидаемые звонки (PendingCall)**
  - `domain/PendingCall.kt`, `domain/PendingCallStore.kt` — модель и интерфейс.
  - `data/PendingCallManager.kt` — реализация: хранение pending‑звонков, таймауты, cleanup.

- **CallLog наблюдение и корреляция**
  - `data/CallLogObserverManager.kt` — ContentObserver на CallLog, попытка определить результат.
  - `data/CallLogCorrelator.kt` — сопоставление записи журнала со звонком (окна времени, idempotency и т.п.).

- **Очередь (offline/retry)**
  - `queue/AppDatabase.kt`, `QueueDao.kt`, `QueueItem.kt`, `QueueManager.kt`
  - `ApiClient` кладёт в очередь:
    - `call_update` (при отсутствии сети/5xx),
    - `heartbeat` (при отсутствии сети/5xx),
    - `telemetry` (при отсутствии сети/5xx; но **не** при 429),
    - `log_bundle` (при отсутствии сети/5xx).

---

## Авторизация, токены и безопасность

- **`auth/TokenManager.kt`**
  - хранит access/refresh;
  - использует `EncryptedSharedPreferences` (best‑effort);
  - хранит метаданные refresh‑успехов/ошибок;
  - хранит device_id и служебные флаги (например, причины блокировки сервиса).

- **`logs/AppLogger.kt`**
  - централизует логирование;
  - маскирует чувствительные данные.

---

## Диагностика и поддержка

- `diagnostics/DiagnosticsPanel.kt` — генерация диагностического отчёта.
- `diagnostics/DiagnosticsMetricsBuffer.kt` — ring‑buffer событий для «чёрного ящика».
- `support/SupportReportBuilder.kt` — сбор «support report».
- `logs/LogCollector.kt`, `logs/LogSender.kt` — сбор и отправка логов.

---

## Полезный «быстрый поиск» по задачам

| Хочу понять… | С чего начать в коде |
|-------------|-----------------------|
| Почему не приходят команды | `CallListenerService.kt` → `ApiClient.pullCall()` → `PullCallBackoff/PullCallMetrics` |
| Почему не определяется результат | `PermissionGate.kt` → `CallLogObserverManager.kt` → `CallLogCorrelator.kt` |
| Что реально уходит в CRM | `network/ApiClient.kt` (`sendCallUpdate`, `sendTelemetryBatch`, `sendHeartbeat`, `sendLogBundle`) |
| Почему разлогинивает | `AuthInterceptor.kt` + `ApiClient.refreshToken()` + `TokenManager.kt` |
| Как открыть диагностику | `ui/settings/SettingsFragment.kt` + `diagnostics/DiagnosticsPanel.kt` |

