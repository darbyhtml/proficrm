# Потоки логики («что куда и как»)

Этот документ описывает **основные сквозные сценарии** CRMProfiDialer: какие компоненты участвуют, какие данные где появляются и какие запросы уходят в CRM.

Если вам нужна карта файлов — см. `CODEMAP.md`.

---

## 1) Запуск приложения и подготовка к работе

**Цель:** подготовить токены/DI, запустить foreground‑сервис, начать получение команд.

### Компоненты
- `CRMApplication` → инициализация базовых компонентов.
- `AppContainer` → связывает зависимости.
- `MainActivity` → запускает `CallListenerService`.
- `CallListenerService` → стартует как foreground и запускает цикл `pullCall`.

### Что происходит пошагово

1. `CRMApplication.onCreate()` инициализирует `TokenManager`, логирование, контейнер.
2. `MainActivity` при старте (или после успешного логина) инициирует запуск `CallListenerService`.
3. `CallListenerService`:
   - проверяет готовность `TokenManager` и наличие токенов/device_id;
   - стартует foreground‑уведомление;
   - регистрирует broadcast‑receiver’ы (например, `APP_OPENED`, `WAKE_NOW`);
   - (если разрешено) поднимает `CallLogObserverManager`;
   - запускает фоновый цикл получения команд.

**Если чего‑то не хватает** (нет токенов/device_id, нет уведомлений, нет прав) — сервис не падает молча, а сохраняет причину, чтобы UI/диагностика могли показать, что именно блокирует готовность.

---

## 2) Авторизация

### 2.1 Логин по логину/паролю

**UI:** `LoginActivity`

**Сеть:** `ApiClient.login()` → `POST /api/token/`

**Дальше:** сохранение токенов в `TokenManager` → переход в `MainActivity` → запуск сервиса.

### 2.2 Логин по QR

**UI:** `QRLoginActivity`

**Сеть:** `ApiClient.exchangeQrToken()` → `POST /api/phone/qr/exchange/`

Особенность: при временной сетевой неготовности (DNS) клиент делает несколько повторов (best‑effort).

---

## 3) Получение команды на звонок из CRM

**Цель:** принять команду, показать уведомление/подсказку, открыть звонилку, начать отслеживание результата.

### Компоненты и роли
- `CallListenerService` — тянет команды через `pullCall`.
- `ApiClient.pullCall()` — `GET /api/phone/calls/pull/?device_id=…&wait_seconds=…`
- `CallFlowCoordinator.handleCallCommand()` — «команда → уведомление → dialer → PendingCall».
- `PendingCallStore` (`PendingCallManager`) — хранит ожидаемые звонки.

### Пошагово

1. `CallListenerService` делает `pullCall`.
2. Если сервер вернул `200` и `phone` заполнен:
   - фиксируется метрика/событие в `PullCallMetrics`/`DiagnosticsMetricsBuffer`;
   - вызывается `CallFlowCoordinator.handleCallCommand(phone, callRequestId)`.
3. `CallFlowCoordinator`:
   - показывает уведомление «Пора позвонить»;
   - открывает системную звонилку `Intent.ACTION_DIAL` (сразу в foreground или с небольшой задержкой в фоне);
   - создаёт `PendingCall` со статусом `PENDING` и `ActionSource.CRM_UI`.

---

## 4) Определение результата звонка (CallLog → статус)

**Цель:** по записи в CallLog понять, что стало со звонком, и отправить результат в CRM (в FULL‑режиме).

### Компоненты и роли
- `permissions/PermissionGate.kt` — проверка, можно ли трекать CallLog.
- `data/CallLogObserverManager.kt` — ContentObserver: реагирует на изменения CallLog.
- `data/CallLogCorrelator.kt` — сопоставляет запись журнала с ожидаемым звонком.
- `ApiClient.sendCallUpdate()` — `POST /api/phone/calls/update/`.

### Важные детали

- **Без `READ_CALL_LOG`** результат чаще всего будет `UNKNOWN` (это ожидаемо и отражается в UI/диагностике).
- Сопоставление делается по нормализованному номеру (`PhoneNumberNormalizer`), окнам времени, направлению, idempotency.
- В API есть **стандартизированные строки**:
  - `CallStatusApi`: `connected`, `no_answer`, `rejected`, `no_action`, `unknown` и т.д.
  - `ActionSource`: `crm_ui`, `notification`, `history`, `manual`, `unknown`.
  - `ResolveMethod`: `observer`, `retry`, `unknown`.

---

## 5) Отправка результата в CRM

**Сеть:** `ApiClient.sendCallUpdate()` → `POST /api/phone/calls/update/`

### Legacy vs Extended

- **Legacy**: минимальный payload (4 поля) для обратной совместимости.
- **Extended**: включает доп. поля (direction/resolve/action_source/reason/ended_at…).

Если extended не принят сервером (HTTP 400/415/422), клиент делает **fallback** на legacy.

### OFFLINE/RETRY

Если нет сети или сервер отвечает 5xx:
- запрос кладётся в `QueueManager` как `call_update`,
- будет отправлен позже при следующей возможности.

---

## 6) Ручной звонок (вкладка «Телефон»)

**UI:** `ui/dialer/DialerFragment.kt`

**Цель:** фиксировать ручные звонки в истории и (в FULL‑режиме) отправлять их статусы в CRM с `action_source = manual`.

### Пошагово

1. Пользователь вводит номер → приложение нормализует/форматирует его (UI‑формат не влияет на нормализацию).
2. Нажимает «Позвонить» → создаётся `PendingCall` с `ActionSource.MANUAL`.
3. Открывается системная звонилка.
4. Результат определяется тем же механизмом (CallLog observer/correlator).
5. Отправка в CRM идёт через тот же `sendCallUpdate()` (если `AppFeatures.isCrmEnabled()`).

---

## 7) «Перезвонить» из уведомления и из истории

**Уведомление:** `CallFlowCoordinator.handleCallCommandFromNotification()`  
Источник: `ActionSource.NOTIFICATION`

**История:** `CallFlowCoordinator.handleCallCommandFromHistory()`  
Источник: `ActionSource.HISTORY` (если есть `callRequestId`; иначе — просто открытие dialer без трекинга)

---

## 8) Heartbeat, телеметрия и логи

### Heartbeat

**Сеть:** `ApiClient.sendHeartbeat()` → `POST /api/phone/devices/heartbeat/`

Поля включают `last_poll_code`, `last_poll_at`, `encryption_enabled` и (опционально) метрики «застревания» очереди.

### Телеметрия

**Сеть:** `ApiClient.sendTelemetryBatch()` → `POST /api/phone/telemetry/`

Формат: `{ device_id, items: [...] }`  

Особенность: при **HTTP 429** телеметрия **не** ставится в очередь (чтобы не создавать лавину).

### Логи

**Сеть:** `ApiClient.sendLogBundle()` → `POST /api/phone/logs/`

Формат: `{ device_id, ts, level_summary, source, payload }`  
При отсутствии сети или 5xx лог‑бандл кладётся в `QueueManager` как `log_bundle`.

---

## 9) Диагностика (как это связано с логикой)

**UI:** `ui/settings/SettingsFragment.kt` (dev‑mode и открытие диалога)  
**Сбор отчёта:** `diagnostics/DiagnosticsPanel.kt`  
**События «чёрного ящика»:** `diagnostics/DiagnosticsMetricsBuffer.kt`

Диагностический отчёт собирает:
- параметры устройства/сборки,
- статусы разрешений (CallLog, уведомления),
- режим `pullCall` и метрики деградации/backoff,
- сетевое состояние,
- состояние «ожидаемых звонков»,
- последние события (ring‑buffer), которые объясняют «что происходило» перед проблемой.

Подробный разбор полей — в `guides/DIAGNOSTICS_GUIDE.md`.
