# Технический обзор Android-приложения CRMProfiDialer

**Версия документа:** 1.0  
**Дата:** 2024  
**Приложение:** CRMProfiDialer (Kotlin)  
**Версия приложения:** 0.5 (versionCode: 5)

---

## 1. Назначение приложения (бизнес-логика)

### Основная функция
Android-приложение для менеджеров CRM-системы, которое автоматически получает команды на звонки из CRM и открывает телефонный номер для дозвона.

### Сценарии использования
1. **Автоматический прием команд на звонки:**
   - Менеджер входит в приложение (логин/пароль или QR-код)
   - Приложение работает в фоне как foreground service
   - Периодически опрашивает CRM API (`/api/phone/calls/pull/`)
   - При получении команды (номер телефона) открывает системную звонилку

2. **Отправка результатов звонков:**
   - После звонка приложение читает CallLog
   - Отправляет результат (статус, длительность) в CRM (`/api/phone/calls/update/`)
   - При отсутствии интернета сохраняет в оффлайн-очередь (Room)

3. **Мониторинг и телеметрия:**
   - Отправка heartbeat для отслеживания "живости" устройства
   - Сбор метрик latency для всех API запросов
   - Отправка логов приложения в CRM для дебага

4. **Оффлайн-режим:**
   - Все критические запросы (call update, heartbeat, telemetry, logs) сохраняются в локальную БД при сетевых ошибках
   - Автоматическая отправка очереди при восстановлении связи

---

## 2. Технологический стек и зависимости

### Язык и платформа
- **Kotlin** 1.9.24
- **Android SDK:** minSdk 21 (Android 5.0), targetSdk 34 (Android 14), compileSdk 34
- **JVM Target:** 17
- **Gradle:** 8.13.2 (Android Gradle Plugin)

### Основные зависимости

#### AndroidX Core
- `androidx.core:core-ktx:1.13.1` — Kotlin extensions для Android
- `androidx.appcompat:appcompat:1.7.0` — поддержка Material Design и обратная совместимость
- `com.google.android.material:material:1.12.0` — Material Design компоненты

**Использование:** UI компоненты (Activity, Button, TextView), темы, стили

#### Безопасность
- `androidx.security:security-crypto:1.1.0-alpha06` — EncryptedSharedPreferences для хранения токенов

**Использование:** `TokenManager` использует `EncryptedSharedPreferences` для шифрования access/refresh токенов, username, device_id, is_admin. Fallback на обычные SharedPreferences при ошибках инициализации.

#### Сеть
- `com.squareup.okhttp3:okhttp:4.12.0` — HTTP клиент
- `com.squareup.okhttp3:logging-interceptor:4.12.0` — HTTP logging (только для debug)

**Использование:** 
- `ApiClient` — единый HTTP клиент для всех API запросов
- `AuthInterceptor` — автоматическая подстановка Bearer токена
- `TelemetryInterceptor` — сбор метрик latency
- `SafeHttpLoggingInterceptor` — безопасное логирование HTTP (маскирование чувствительных данных)

#### Асинхронность
- `org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1` — Coroutines для асинхронных операций

**Использование:** 
- Все сетевые запросы в `ApiClient` выполняются в `Dispatchers.IO`
- `CallListenerService` использует `CoroutineScope` для polling loop
- `QueueManager` использует coroutines для фоновой отправки очереди

#### База данных
- `androidx.room:room-runtime:2.6.1` — Room Persistence Library
- `androidx.room:room-ktx:2.6.1` — Kotlin extensions для Room
- `androidx.room:room-compiler:2.6.1` (kapt) — кодогенерация для Room

**Использование:** 
- `AppDatabase` — база данных для оффлайн-очереди
- `QueueItem` — Entity для элементов очереди
- `QueueDao` — DAO для работы с очередью
- `QueueManager` — менеджер для добавления/отправки элементов очереди

#### QR-сканер
- `com.journeyapps:zxing-android-embedded:4.3.0` — библиотека для сканирования QR-кодов

**Использование:** 
- `QRLoginActivity` — сканирование QR-кода для входа
- `PortraitCaptureActivity` — кастомная Activity для фиксации портретной ориентации камеры

---

## 3. Сборка и конфигурация

### Build Flavors

#### Staging
- **BASE_URL:** `http://95.142.47.245` (HTTP, без SSL)
- **applicationIdSuffix:** `.staging` → `ru.groupprofi.crmprofi.dialer.staging`
- **versionNameSuffix:** `-staging` → `0.5-staging`
- **Network Security Config:** разрешен cleartext traffic только для `95.142.47.245`

#### Production
- **BASE_URL:** `https://crm.groupprofi.ru` (HTTPS)
- **applicationId:** `ru.groupprofi.crmprofi.dialer` (без суффикса)
- **versionName:** `0.5`
- **Network Security Config:** полностью запрещен cleartext traffic (только HTTPS)

### BuildConfig поля
- `BuildConfig.BASE_URL` — базовый URL API (разный для staging/production)
- `BuildConfig.DEBUG` — флаг debug сборки (используется для включения HTTP logging)

### Network Security Config

#### Staging (`app/src/staging/res/xml/network_security_config.xml`)
```xml
<base-config cleartextTrafficPermitted="false">
    <!-- По умолчанию запрещаем cleartext -->
</base-config>
<domain-config cleartextTrafficPermitted="true">
    <domain includeSubdomains="false">95.142.47.245</domain>
    <!-- Разрешаем HTTP только для staging IP -->
</domain-config>
```

#### Production (`app/src/production/res/xml/network_security_config.xml`)
```xml
<base-config cleartextTrafficPermitted="false">
    <!-- Полностью запрещаем cleartext, только HTTPS -->
</base-config>
```

### Signing (подпись APK/AAB)

**Конфигурация:** `app/build.gradle` (строки 42-69)

**Источники секретов (приоритет):**
1. Environment variables: `STORE_FILE`, `STORE_PASSWORD`, `KEY_ALIAS`, `KEY_PASSWORD`
2. `local.properties` (в корне проекта): `storeFile`, `storePassword`, `keyAlias`, `keyPassword`

**Применение:**
- Signing config `productionRelease` применяется только для `productionRelease` build variant
- Если секреты не найдены — сборка проходит без подписи (для тестирования)

**Важно:** `local.properties` и keystore файлы НЕ должны коммититься в git (добавлены в `.gitignore`)

### R8/ProGuard

**Статус:** По умолчанию выключен (`minifyEnabled false`)

**ProGuard rules:** `app/proguard-rules.pro`

**Защищенные классы:**
- Room Database (`AppDatabase`, `QueueItem`, `QueueDao`)
- Security Crypto (`EncryptedSharedPreferences`, Google Tink)
- OkHttp / Okio
- Kotlin metadata и coroutines
- Все классы приложения (`ru.groupprofi.crmprofi.dialer.**`)

**Примечание:** Для production release можно включить minify через отдельный buildType или через `matchingFallbacks`, но сейчас это не настроено.

---

## 4. Структура проекта

### Дерево каталогов

```
android/CRMProfiDialer/
├── app/
│   ├── build.gradle                    # Конфигурация сборки (flavors, signing, dependencies)
│   ├── proguard-rules.pro              # ProGuard правила для R8
│   └── src/
│       ├── main/
│       │   ├── AndroidManifest.xml    # Манифест приложения (permissions, activities, service)
│       │   ├── java/ru/groupprofi/crmprofi/dialer/
│       │   │   ├── MainActivity.kt                    # Главный экран (логин, статус)
│       │   │   ├── OnboardingActivity.kt             # Экран онбординга (первый запуск)
│       │   │   ├── QRLoginActivity.kt                 # Вход по QR-коду
│       │   │   ├── PortraitCaptureActivity.kt         # Кастомная Activity для QR-сканера (портрет)
│       │   │   ├── LogsActivity.kt                   # Экран просмотра логов
│       │   │   ├── CallListenerService.kt             # Foreground service для polling
│       │   │   ├── CRMApplication.kt                  # Application класс (глобальные объекты)
│       │   │   ├── AppState.kt                       # Глобальное состояние (isForeground)
│       │   │   ├── BuildConfig.kt                     # Автогенерируемый (BASE_URL, DEBUG)
│       │   │   ├── auth/
│       │   │   │   └── TokenManager.kt                # Управление токенами (EncryptedSharedPreferences)
│       │   │   ├── network/
│       │   │   │   ├── ApiClient.kt                   # Единый HTTP клиент
│       │   │   │   ├── AuthInterceptor.kt             # Interceptor для Bearer токена
│       │   │   │   ├── TelemetryInterceptor.kt          # Interceptor для сбора метрик
│       │   │   │   └── SafeHttpLoggingInterceptor.kt  # Безопасное HTTP логирование
│       │   │   ├── queue/
│       │   │   │   ├── AppDatabase.kt                 # Room Database
│       │   │   │   ├── QueueItem.kt                   # Entity для элементов очереди
│       │   │   │   ├── QueueDao.kt                     # DAO для работы с очередью
│       │   │   │   └── QueueManager.kt                # Менеджер очереди (enqueue, flush)
│       │   │   └── logs/
│       │   │       ├── AppLogger.kt                   # Единый логгер (singleton)
│       │   │       ├── LogCollector.kt                # Сборщик логов (кольцевой буфер)
│       │   │       ├── LogInterceptor.kt               # Перехватчик android.util.Log
│       │   │       └── LogSender.kt                   # Отправка логов в CRM (legacy)
│       │   └── res/
│       │       ├── layout/
│       │       │   ├── activity_main.xml              # Layout для MainActivity
│       │       │   ├── activity_onboarding.xml        # Layout для OnboardingActivity
│       │       │   └── activity_logs.xml              # Layout для LogsActivity
│       │       ├── menu/
│       │       │   └── logs_menu.xml                  # Menu для LogsActivity (export, clear)
│       │       ├── drawable/
│       │       │   ├── ic_crm.xml                     # Иконка приложения
│       │       │   └── ic_launcher_foreground.xml     # Иконка launcher
│       │       └── values/
│       │           ├── strings.xml                    # Строковые ресурсы
│       │           ├── colors.xml                     # Цвета
│       │           └── themes.xml                     # Темы Material Design
│       ├── staging/
│       │   └── res/xml/
│       │       └── network_security_config.xml        # Network Security Config для staging
│       └── production/
│           └── res/xml/
│               └── network_security_config.xml        # Network Security Config для production
├── build.gradle                                        # Root build.gradle (plugins)
├── settings.gradle                                     # Настройки проекта
├── gradle.properties                                   # Gradle properties
├── local.properties                                    # Локальные секреты (keystore, не коммитится)
└── gradlew / gradlew.bat                               # Gradle wrapper
```

### Пакеты (packages)

- `ru.groupprofi.crmprofi.dialer` — корневой пакет
  - `auth/` — аутентификация и управление токенами
  - `network/` — сетевой слой (HTTP клиент, interceptors)
  - `queue/` — оффлайн-очередь (Room)
  - `logs/` — логирование (сбор, маскирование, отправка)

---

## 5. Компоненты приложения (по классам)

### 5.1. UI Components

#### MainActivity
**Файл:** `app/src/main/java/ru/groupprofi/crmprofi/dialer/MainActivity.kt`

**Назначение:** Главный экран приложения. Отображает форму входа, статус сервиса, кнопки управления.

**Основные методы:**
- `onCreate()` — инициализация UI, проверка токенов, запуск сервиса
- `onResume()` — обновление статуса, проверка разрешений
- `handleLogin()` — обработка входа по логину/паролю
- `startListening()` — запуск `CallListenerService`
- `updateLogsButtonVisibility()` — показ/скрытие кнопки логов (сейчас для всех)

**Зависимости:**
- `TokenManager` — проверка токенов, сохранение после входа
- `ApiClient` — запросы к API (login, registerDevice, getUserInfo)
- `CallListenerService` — запуск/остановка сервиса
- `QueueManager` — статистика очереди

**Входы/выходы:**
- Вход: `Intent` от `QRLoginActivity` (результат QR-логина)
- Выход: `Intent` в `QRLoginActivity`, `LogsActivity`, `CallListenerService`

**Риски:**
- Если `TokenManager` не инициализирован — может упасть при проверке токенов
- При отсутствии разрешений сервис не запустится, но ошибка может быть неочевидной

#### OnboardingActivity
**Файл:** `app/src/main/java/ru/groupprofi/crmprofi/dialer/OnboardingActivity.kt`

**Назначение:** Экран онбординга, показывается один раз при первом запуске.

**Основные методы:**
- `onCreate()` — проверка флага `onboarding_shown`, показ/пропуск онбординга

**Зависимости:**
- `SharedPreferences` ("onboarding") — хранение флага показа онбординга

**Входы/выходы:**
- Вход: `Intent` с `ACTION_MAIN` (launcher activity)
- Выход: `Intent` в `MainActivity` после нажатия "Продолжить"

**Риски:**
- Если layout `activity_onboarding.xml` отсутствует — упадет при `setContentView()`

#### QRLoginActivity
**Файл:** `app/src/main/java/ru/groupprofi/crmprofi/dialer/QRLoginActivity.kt`

**Назначение:** Сканирование QR-кода для входа в систему.

**Основные методы:**
- `onCreate()` — инициализация, запуск QR-сканера
- `startQrScanner()` — настройка и запуск `PortraitCaptureActivity`
- `handleQrToken()` — обмен QR-токена на JWT через `ApiClient.exchangeQrToken()`

**Зависимости:**
- `zxing-android-embedded` — библиотека для сканирования QR
- `PortraitCaptureActivity` — кастомная Activity для фиксации портретной ориентации
- `ApiClient` — обмен QR-токена
- `TokenManager` — сохранение токенов после успешного обмена

**Входы/выходы:**
- Вход: `Intent` из `MainActivity` (кнопка "Вход по QR")
- Выход: `Intent` в `MainActivity` с результатом (успех/ошибка)

**Риски:**
- Если камера недоступна или разрешение не дано — сканер не запустится
- QR-токен может быть истекшим или уже использованным (одноразовый)

#### PortraitCaptureActivity
**Файл:** `app/src/main/java/ru/groupprofi/crmprofi/dialer/PortraitCaptureActivity.kt`

**Назначение:** Кастомная `CaptureActivity` для фиксации портретной ориентации камеры.

**Основные методы:**
- `onResume()` — фиксация `SCREEN_ORIENTATION_PORTRAIT`

**Зависимости:**
- `com.journeyapps.barcodescanner.CaptureActivity` — базовый класс

**Риски:**
- На некоторых устройствах камера может все равно поворачиваться (зависит от производителя)

#### LogsActivity
**Файл:** `app/src/main/java/ru/groupprofi/crmprofi/dialer/LogsActivity.kt`

**Назначение:** Экран просмотра логов приложения, статуса очереди, статистики.

**Основные методы:**
- `onCreate()` — инициализация UI, загрузка логов
- `refreshLogs()` — обновление логов из `AppLogger`
- `filterLogs()` — фильтрация по уровню и поисковому запросу
- `exportLogs()` — экспорт логов через Share Intent
- `sendLogsToServer()` — отправка логов через `ApiClient.sendLogBundle()`

**Зависимости:**
- `AppLogger` — получение логов
- `TokenManager` — проверка токенов
- `QueueManager` — статистика очереди
- `ApiClient` — отправка логов

**Входы/выходы:**
- Вход: `Intent` из `MainActivity` (кнопка "Логи приложения")
- Выход: Share Intent для экспорта логов

**Риски:**
- Если `QueueManager` не инициализирован (Room не сгенерировал классы) — статистика покажет ошибку
- Большое количество логов может замедлить UI (нужен пагинация)

### 5.2. Service

#### CallListenerService
**Файл:** `app/src/main/java/ru/groupprofi/crmprofi/dialer/CallListenerService.kt`

**Назначение:** Foreground service для фонового polling команд на звонки.

**Основные методы:**
- `onStartCommand()` — инициализация, запуск polling loop
- `onDestroy()` — остановка polling loop
- `updateListeningNotification()` — обновление уведомления с статусом
- `showCallNotification()` — показ уведомления с действием "Позвонить"
- `checkCallLogAndSend()` — чтение CallLog и отправка результата в CRM
- `isWorkingHours()` — проверка рабочего времени (9:00-18:00)

**Зависимости:**
- `TokenManager` — получение токенов, device_id
- `ApiClient` — polling (`pullCall()`), отправка результатов (`sendCallUpdate()`)
- `QueueManager` — добавление в очередь при сетевых ошибках
- `AppLogger` / `LogCollector` — логирование
- `AppState` — проверка `isForeground` для открытия звонилки

**Входы/выходы:**
- Вход: `Intent` с `ACTION_START` или `ACTION_STOP` из `MainActivity`
- Выход: `Notification` (foreground service), `Intent.ACTION_DIAL` (открытие звонилки)

**Особенности:**
- Адаптивная частота polling:
  - При получении команды (200): 1.5 секунды
  - При пустых ответах (204): постепенное увеличение (1.5s → 3s → 5s)
  - При rate limiting (429): значительное увеличение (5s → 10s → 30s)
  - Вне рабочего времени: 5 секунд
- Джиттер: случайная задержка ±200мс для предотвращения синхронизации устройств
- Периодические задачи:
  - Heartbeat каждые 10 циклов
  - Flush очереди каждые 20 циклов
  - Отправка логов каждые 120 циклов или при накоплении >200 логов

**Риски:**
- Если разрешения не даны (POST_NOTIFICATIONS, READ_CALL_LOG) — сервис остановится
- При 401/403 сервис очищает токены и останавливается (graceful logout)
- При сетевых ошибках (код 0) сервис продолжает работу, но не отправляет данные

### 5.3. Authentication

#### TokenManager
**Файл:** `app/src/main/java/ru/groupprofi/crmprofi/dialer/auth/TokenManager.kt`

**Назначение:** Единая точка правды для управления токенами и учетными данными.

**Основные методы:**
- `getInstance(context)` — singleton
- `getAccessToken()` / `getRefreshToken()` — получение токенов
- `saveTokens(access, refresh, username)` — сохранение токенов
- `updateAccessToken(access)` — обновление только access токена (после refresh)
- `refreshAccessToken()` — обновление access токена через refresh token (с Mutex)
- `clearAll()` — очистка всех данных
- `saveDeviceId()` / `getDeviceId()` — управление device_id
- `saveIsAdmin()` / `isAdmin()` — управление флагом администратора
- `saveLastPoll()` / `getLastPoll()` — сохранение статуса последнего polling

**Зависимости:**
- `androidx.security.crypto.EncryptedSharedPreferences` — шифрованное хранилище
- `androidx.security.crypto.MasterKey` — ключ шифрования
- `kotlinx.coroutines.sync.Mutex` — защита от race conditions при refresh

**Хранимые данные:**
- `access` — JWT access token
- `refresh` — JWT refresh token
- `username` — имя пользователя
- `device_id` — идентификатор устройства (ANDROID_ID)
- `is_admin` — флаг администратора (boolean)
- `last_poll_code` / `last_poll_at` — статус последнего polling

**Особенности:**
- Использует `EncryptedSharedPreferences` с fallback на обычные `SharedPreferences` при ошибках
- Миграция старых plain prefs в secure prefs (однократно)
- Thread-safe refresh токена через `Mutex` (предотвращает множественные одновременные refresh)

**Риски:**
- Если `EncryptedSharedPreferences` не инициализируется — fallback на plain prefs (менее безопасно)
- При ошибке миграции старые токены могут быть потеряны

### 5.4. Network

#### ApiClient
**Файл:** `app/src/main/java/ru/groupprofi/crmprofi/dialer/network/ApiClient.kt`

**Назначение:** Единый HTTP клиент для всех API запросов.

**Основные методы:**
- `getInstance(context)` — singleton
- `login(username, password)` — вход по логину/паролю → `Triple(access, refresh, isAdmin)`
- `exchangeQrToken(qrToken)` — обмен QR-токена на JWT → `QrTokenResult(access, refresh, username, isAdmin)`
- `refreshAccessToken()` — обновление access токена (используется внутри других методов)
- `registerDevice(deviceId, deviceName)` — регистрация устройства
- `pullCall(deviceId)` — polling команд на звонки → `PullCallResponse(phone, callRequestId)?`
- `sendCallUpdate(...)` — отправка результата звонка
- `sendHeartbeat(...)` — отправка heartbeat
- `sendTelemetryBatch(...)` — отправка метрик latency
- `sendLogBundle(...)` — отправка логов
- `getUserInfo()` — получение информации о пользователе (включая `isAdmin`)
- `getHttpClient()` — получение OkHttpClient (для `QueueManager`)

**Зависимости:**
- `TokenManager` — получение/обновление токенов
- `QueueManager` — добавление в очередь при сетевых ошибках
- `OkHttpClient` — HTTP клиент с interceptors
- `BuildConfig.BASE_URL` — базовый URL API

**Interceptors (порядок добавления):**
1. `AuthInterceptor` — подстановка Bearer токена, обработка 401/403
2. `TelemetryInterceptor` — сбор метрик latency
3. `SafeHttpLoggingInterceptor` — HTTP logging (только в debug)

**Результаты:**
- Все методы возвращают `Result<T>`:
  - `Result.Success(data)` — успех
  - `Result.Error(message, code?)` — ошибка (с HTTP кодом, если есть)

**Особенности:**
- Автоматический refresh токена при 401 (через `TokenManager.refreshAccessToken()`)
- При сетевых ошибках добавляет запросы в оффлайн-очередь (`QueueManager.enqueue()`)
- Все запросы выполняются в `Dispatchers.IO`

**Риски:**
- Если `TokenManager` не инициализирован — все запросы будут без токена
- При ошибке refresh токена все последующие запросы вернут 401

#### AuthInterceptor
**Файл:** `app/src/main/java/ru/groupprofi/crmprofi/dialer/network/AuthInterceptor.kt`

**Назначение:** Interceptor для автоматической подстановки Bearer токена в заголовки.

**Основные методы:**
- `intercept(chain)` — добавление `Authorization: Bearer <token>` к запросу

**Зависимости:**
- `TokenManager` — получение access токена
- `CallListenerService` — остановка сервиса при 401/403

**Особенности:**
- НЕ добавляет токен для `/api/token/` и `/api/phone/qr/exchange/` (публичные endpoints)
- При 401/403: очищает токены через `TokenManager.clearAll()` и останавливает `CallListenerService`

**Риски:**
- Если `context` не передан — graceful logout не сработает (только очистка токенов)

#### TelemetryInterceptor
**Файл:** `app/src/main/java/ru/groupprofi/crmprofi/dialer/network/TelemetryInterceptor.kt`

**Назначение:** Interceptor для сбора метрик latency всех `/api/phone/*` запросов.

**Основные методы:**
- `intercept(chain)` — измерение времени выполнения запроса, добавление в очередь

**Зависимости:**
- `TokenManager` — получение device_id
- `QueueManager` (lazy) — добавление телеметрии в очередь

**Особенности:**
- Собирает метрики только для `/api/phone/*` endpoints
- Добавляет в очередь асинхронно (не блокирует основной поток)
- Формат: `{type: "latency", endpoint: "...", http_code: 200, value_ms: 150}`

**Риски:**
- Если `QueueManager` не инициализирован — телеметрия теряется (но не критично)

#### SafeHttpLoggingInterceptor
**Файл:** `app/src/main/java/ru/groupprofi/crmprofi/dialer/network/SafeHttpLoggingInterceptor.kt`

**Назначение:** Безопасное HTTP логирование с маскированием чувствительных данных.

**Основные методы:**
- `intercept(chain)` — делегирование в `HttpLoggingInterceptor`
- `maskSensitiveData(text)` — маскирование токенов, паролей, device_id, телефонов

**Зависимости:**
- `okhttp3.logging.HttpLoggingInterceptor` — базовый logging interceptor

**Особенности:**
- Включается только в debug сборках (`BuildConfig.DEBUG`)
- Маскирует:
  - Bearer токены → `Bearer ***`
  - access/refresh токены в JSON → `access="***"`
  - Пароли → `password="***"`
  - device_id → `device_id="1234***5678"` (первые 4 + последние 4)
  - Номера телефонов → `***1234` (последние 4 цифры)

**Риски:**
- Если маскирование не сработает (новый формат данных) — чувствительные данные могут попасть в логи

### 5.5. Queue (Offline)

#### AppDatabase
**Файл:** `app/src/main/java/ru/groupprofi/crmprofi/dialer/queue/AppDatabase.kt`

**Назначение:** Room Database для хранения оффлайн-очереди.

**Основные методы:**
- `getDatabase(context)` — singleton
- `queueDao()` — получение DAO

**Зависимости:**
- `androidx.room.Room` — Room Database
- `QueueItem` — Entity
- `QueueDao` — DAO

**Особенности:**
- Версия БД: 1
- Имя БД: `crmprofi_queue_db`
- `exportSchema = false` — схема не экспортируется
- Миграция `MIGRATION_0_1` — создание таблицы `queue_items`

**Риски:**
- Если Room не сгенерировал классы (`AppDatabase_Impl`) — инициализация упадет
- При изменении схемы нужно добавить новую миграцию

#### QueueItem
**Файл:** `app/src/main/java/ru/groupprofi/crmprofi/dialer/queue/QueueItem.kt`

**Назначение:** Entity для элемента оффлайн-очереди.

**Поля:**
- `id` — Primary Key (auto-increment)
- `type` — тип запроса: `"call_update"`, `"heartbeat"`, `"telemetry"`, `"log_bundle"`
- `payload` — JSON-тело запроса (готовый для отправки)
- `endpoint` — URL эндпоинта (например, `/api/phone/calls/update/`)
- `method` — HTTP метод: `"POST"`, `"PUT"`, `"PATCH"`
- `retryCount` — количество попыток отправки (максимум 3)
- `createdAt` — время создания (миллисекунды)
- `lastRetryAt` — время последней попытки

**Риски:**
- Если `payload` слишком большой — может быть проблема с производительностью

#### QueueDao
**Файл:** `app/src/main/java/ru/groupprofi/crmprofi/dialer/queue/QueueDao.kt`

**Назначение:** DAO для работы с оффлайн-очередью.

**Основные методы:**
- `insert(item)` — вставка элемента
- `getAll()` — получение всех элементов (отсортированы по `createdAt ASC`)
- `getPending(limit)` — получение элементов для отправки (`retryCount < 3`, максимум `limit`)
- `incrementRetry(id, now)` — увеличение счетчика попыток
- `delete(id)` — удаление элемента (после успешной отправки)
- `deleteOldFailed(cutoffTime)` — удаление старых неудачных элементов (старше 7 дней)
- `count()` — количество элементов
- `countByType(type)` — количество элементов по типу

**Риски:**
- Если Room не сгенерировал классы — все методы упадут

#### QueueManager
**Файл:** `app/src/main/java/ru/groupprofi/crmprofi/dialer/queue/QueueManager.kt`

**Назначение:** Менеджер оффлайн-очереди: добавление элементов и периодическая отправка.

**Основные методы:**
- `enqueue(type, endpoint, payload, method)` — добавление элемента в очередь
- `flushQueue(baseUrl, accessToken, httpClient)` — отправка всех pending элементов
- `getStats()` — статистика очереди (количество по типам)
- `getStuckMetrics()` — метрики застрявших элементов (достигших max retries)

**Зависимости:**
- `AppDatabase` (lazy) — база данных
- `QueueDao` (lazy) — DAO
- `OkHttpClient` — для отправки элементов

**Особенности:**
- Ленивая инициализация БД (создается только при первом использовании)
- При ошибке инициализации БД возвращает пустую статистику (не падает)
- Максимум 3 попытки отправки для каждого элемента
- Автоматическая очистка старых неудачных элементов (старше 7 дней)
- Отправка алерта в CRM для застрявших элементов (не чаще раза в 5 минут)

**Риски:**
- Если БД не инициализирована — `enqueue()` упадет, но `getStats()` вернет пустую статистику

### 5.6. Logging

#### AppLogger
**Файл:** `app/src/main/java/ru/groupprofi/crmprofi/dialer/logs/AppLogger.kt`

**Назначение:** Единый централизованный логгер для всего приложения (singleton).

**Основные методы:**
- `initialize(context, enableFileLogging)` — инициализация (вызывается из `CRMApplication.onCreate()`)
- `d(tag, message)` / `i(tag, message)` / `w(tag, message)` / `e(tag, message)` — логирование
- `e(tag, message, throwable)` — логирование с исключением
- `getRecentLogs(maxEntries)` — получение последних логов (без очистки буфера)
- `getAllLogs()` — получение всех логов (для экспорта)
- `clearLogs()` — очистка буфера
- `canViewLogs()` — проверка доступа к логам (сейчас всегда `true`)

**Зависимости:**
- `ConcurrentLinkedQueue` — кольцевой буфер логов (максимум 3000 записей)
- `Mutex` — thread-safe доступ к буферу
- `File` — опциональное хранение в файл (`app_logs.txt`, максимум 5 MB)

**Особенности:**
- Маскирование чувствительных данных перед записью в буфер (токены, пароли, device_id, телефоны)
- Хранение в памяти (кольцевой буфер) и опционально в файл
- Thread-safe (использует `Mutex` для синхронизации)

**Риски:**
- Если файл логов слишком большой — может замедлить запись
- При большом количестве логов буфер может переполниться (старые логи удаляются)

#### LogCollector
**Файл:** `app/src/main/java/ru/groupprofi/crmprofi/dialer/logs/LogCollector.kt`

**Назначение:** Простой сборщик логов для отправки в CRM (legacy, используется для совместимости).

**Основные методы:**
- `addLog(level, tag, message)` — добавление лога в буфер
- `takeLogs(maxEntries)` — получение и очистка накопленных логов (формирует `LogBundle`)
- `getRecentLogs(maxEntries)` — получение последних логов без очистки
- `getAllLogs()` — получение всех логов для экспорта
- `getBufferSize()` — количество логов в буфере

**Зависимости:**
- `ConcurrentLinkedQueue` — кольцевой буфер (максимум 1000 записей)
- `Mutex` — thread-safe доступ

**Особенности:**
- Используется `LogInterceptor` для автоматического сбора логов из `android.util.Log`
- Формирует `LogBundle` для отправки в CRM

**Риски:**
- Дублирование функциональности с `AppLogger` (можно объединить в будущем)

#### LogInterceptor
**Файл:** `app/src/main/java/ru/groupprofi/crmprofi/dialer/logs/LogInterceptor.kt`

**Назначение:** Перехватчик логов для сбора в `LogCollector`.

**Основные методы:**
- `setCollector(collector)` — установка `LogCollector`
- `v(tag, msg)` / `d(tag, msg)` / `i(tag, msg)` / `w(tag, msg)` / `e(tag, msg)` — обертки над `android.util.Log`

**Зависимости:**
- `LogCollector` — сборщик логов

**Особенности:**
- Используется как замена `android.util.Log` для автоматического сбора логов
- Все вызовы также пишутся в системный `android.util.Log`

**Риски:**
- Если `collector` не установлен — логи не собираются (но пишутся в системный log)

#### LogSender
**Файл:** `app/src/main/java/ru/groupprofi/crmprofi/dialer/logs/LogSender.kt`

**Назначение:** Отправка лог-бандлов в CRM (legacy, используется в `CallListenerService`).

**Основные методы:**
- `sendLogBundle(baseUrl, accessToken, deviceId, bundle)` — отправка лог-бандла

**Зависимости:**
- `OkHttpClient` — HTTP клиент
- `QueueManager` — добавление в очередь при сетевых ошибках

**Особенности:**
- Маскирует чувствительные данные перед отправкой
- При сетевых ошибках добавляет в оффлайн-очередь

**Риски:**
- Дублирование функциональности с `ApiClient.sendLogBundle()` (можно использовать только `ApiClient`)

### 5.7. Utilities

#### CRMApplication
**Файл:** `app/src/main/java/ru/groupprofi/crmprofi/dialer/CRMApplication.kt`

**Назначение:** Application класс для хранения глобальных объектов.

**Основные методы:**
- `onCreate()` — инициализация `AppLogger`, настройка `LogInterceptor`

**Зависимости:**
- `LogCollector` — глобальный экземпляр (для совместимости)
- `AppLogger` — инициализация единого логгера

**Особенности:**
- Регистрируется в `AndroidManifest.xml` как `android:name=".CRMApplication"`

**Риски:**
- Если `AppLogger.initialize()` упадет — логирование может не работать

#### AppState
**Файл:** `app/src/main/java/ru/groupprofi/crmprofi/dialer/AppState.kt`

**Назначение:** Глобальное состояние приложения (singleton object).

**Поля:**
- `isForeground` — флаг, находится ли приложение на переднем плане

**Использование:**
- `CallListenerService` проверяет `AppState.isForeground` для открытия звонилки сразу (если приложение на экране) или только уведомления (если в фоне)

**Риски:**
- Если `isForeground` не обновляется корректно — звонилка может не открыться

---

## 6. Потоки данных (End-to-end схемы)

### A) Логин по паролю

```
MainActivity.handleLogin()
    ↓
ApiClient.login(username, password)
    ↓
POST /api/token/
    Body: {username, password}
    ↓
Response: {access, refresh, is_admin}
    ↓
TokenManager.saveTokens(access, refresh, username)
TokenManager.saveIsAdmin(isAdmin)
    ↓
ApiClient.registerDevice(deviceId, deviceName)
    ↓
POST /api/phone/devices/register/
    Headers: Authorization: Bearer <access>
    Body: {device_id, device_name}
    ↓
MainActivity.startListening()
    ↓
Intent → CallListenerService (ACTION_START)
    ↓
CallListenerService.onStartCommand()
    ↓
startForeground() + polling loop
```

### B) Логин по QR

```
MainActivity (кнопка "Вход по QR")
    ↓
Intent → QRLoginActivity
    ↓
QRLoginActivity.startQrScanner()
    ↓
PortraitCaptureActivity (zxing)
    ↓
Сканирование QR-кода → qrToken (строка)
    ↓
ApiClient.exchangeQrToken(qrToken)
    ↓
POST /api/phone/qr/exchange/
    Body: {token: qrToken}
    ↓
Response: {access, refresh, username, is_admin}
    ↓
TokenManager.saveTokens(access, refresh, username)
TokenManager.saveIsAdmin(isAdmin)
TokenManager.saveDeviceId(deviceId)
    ↓
ApiClient.registerDevice(deviceId, deviceName)
    ↓
Intent → MainActivity (FLAG_ACTIVITY_CLEAR_TOP)
    ↓
MainActivity.onResume() → startListening()
    ↓
CallListenerService запускается
```

### C) Polling (получение команд)

```
CallListenerService (polling loop)
    ↓
ApiClient.pullCall(deviceId)
    ↓
GET /api/phone/calls/pull/?device_id=<deviceId>
    Headers: Authorization: Bearer <access>
    ↓
Response варианты:
    - 204 (No Content) → нет команд
    - 200 (OK) → {phone, call_request_id}
    - 401 (Unauthorized) → токен истек
    - 429 (Too Many Requests) → rate limiting
    - 0 (network error) → нет интернета
    ↓
Обработка:
    - 200: showCallNotification() + открыть звонилку (если foreground)
    - 204: увеличить задержку (адаптивная частота)
    - 429: значительно увеличить задержку (5s → 10s → 30s)
    - 401: clearAll() + stopSelf() (graceful logout)
    - 0: продолжить работу, данные в очередь
    ↓
Адаптивная задержка (baseDelay + jitter):
    - 200: 1.5s
    - 204: 1.5s → 3s → 5s (зависит от consecutiveEmptyPolls)
    - 429: 5s → 10s → 30s
    - Вне рабочего времени: 5s
    - Джиттер: ±200мс
    ↓
delay(delayMs) → следующий цикл
```

### D) Call result (отправка результата звонка)

```
CallListenerService.checkCallLogAndSend(phone)
    ↓
Чтение CallLog (через 5 секунд после открытия звонилки)
    ↓
Поиск последнего звонка на номер phone
    ↓
Получение данных:
    - duration (секунды)
    - type (INCOMING/OUTGOING/MISSED)
    - date (timestamp)
    ↓
ApiClient.sendCallUpdate(callRequestId, phone, duration, type, date)
    ↓
POST /api/phone/calls/update/
    Headers: Authorization: Bearer <access>
    Body: {call_request_id, phone, duration, type, date}
    ↓
Успех (200):
    → Удаление из pending calls
    ↓
Ошибка (сетевая/401):
    → QueueManager.enqueue("call_update", "/api/phone/calls/update/", payload)
    → Сохранение в Room БД
    ↓
Периодический flush очереди (каждые 20 циклов):
    → QueueManager.flushQueue()
    → Отправка всех pending элементов
    → Удаление успешно отправленных
    → Увеличение retryCount для неудачных (максимум 3)
```

### E) Logout / 401 (graceful logout)

```
ApiClient (любой запрос)
    ↓
AuthInterceptor.intercept()
    ↓
Response: 401 или 403
    ↓
AuthInterceptor:
    → AppLogger.w("Received 401/403, clearing tokens")
    → TokenManager.clearAll()
    → context.stopService(CallListenerService)
    ↓
CallListenerService (polling loop)
    ↓
Response: 401
    ↓
CallListenerService:
    → AppLogger.w("Authentication failed (401), stopping service")
    → TokenManager.clearAll() (если еще не очищено)
    → updateListeningNotification("Требуется повторный вход")
    → delay(10000) // 10 секунд
    → stopSelf()
    ↓
MainActivity.onResume()
    ↓
Проверка TokenManager.hasTokens()
    ↓
false → показ формы входа
    ↓
Пользователь видит: "Требуется повторный вход в приложении"
```

---

## 7. Хранилище и безопасность

### EncryptedSharedPreferences (TokenManager)

**Что хранится:**
- `access` — JWT access token (шифруется)
- `refresh` — JWT refresh token (шифруется)
- `username` — имя пользователя (шифруется)
- `device_id` — идентификатор устройства (шифруется)
- `is_admin` — флаг администратора (boolean, шифруется)
- `last_poll_code` / `last_poll_at` — статус последнего polling (не критично)

**Шифрование:**
- `MasterKey` — AES256_GCM
- `PrefKeyEncryptionScheme` — AES256_SIV
- `PrefValueEncryptionScheme` — AES256_GCM

**Fallback:**
- При ошибке инициализации `EncryptedSharedPreferences` → обычные `SharedPreferences` (менее безопасно, но работает)

**Миграция:**
- Автоматическая миграция старых plain prefs в secure prefs (однократно при первом запуске)

### Room Database (очередь)

**Что хранится:**
- `QueueItem` — элементы оффлайн-очереди:
  - `call_update` — результаты звонков
  - `heartbeat` — heartbeat запросы
  - `telemetry` — метрики latency
  - `log_bundle` — лог-бандлы

**Безопасность:**
- Данные не шифруются (хранятся в SQLite)
- Чувствительные данные (токены, пароли) маскируются перед добавлением в очередь

**Очистка:**
- Автоматическое удаление старых неудачных элементов (старше 7 дней, `retryCount >= 3`)

### Чувствительные данные и маскирование

**Что маскируется:**
- Bearer токены → `Bearer ***`
- access/refresh токены → `access="***"`
- Пароли → `password="***"`
- device_id → `device_id="1234***5678"` (первые 4 + последние 4 символа)
- Номера телефонов → `***1234` (последние 4 цифры)
- Полные URL с query параметрами → `***`

**Где маскируется:**
- `AppLogger.log()` — перед записью в буфер
- `SafeHttpLoggingInterceptor.maskSensitiveData()` — перед логированием HTTP
- `LogSender.maskSensitiveData()` — перед отправкой в CRM

### Отправка данных на backend

**Что отправляется:**
1. **Call updates** (`/api/phone/calls/update/`):
   - `call_request_id`, `phone`, `duration`, `type`, `date`
   - Зачем: обновление статуса звонка в CRM

2. **Heartbeat** (`/api/phone/devices/heartbeat/`):
   - `device_id`, `device_name`, `last_poll_code`, `last_poll_at`, `queue_stats`
   - Зачем: мониторинг "живости" устройства

3. **Telemetry** (`/api/phone/telemetry/`):
   - `device_id`, `items: [{type: "latency", endpoint, http_code, value_ms}]`
   - Зачем: мониторинг производительности API

4. **Logs** (`/api/phone/logs/`):
   - `device_id`, `ts`, `level_summary`, `source`, `payload` (маскированный)
   - Зачем: дебаг проблем на устройствах

**Безопасность:**
- Все запросы идут через HTTPS (production) или HTTP (staging, только для `95.142.47.245`)
- Токены передаются в заголовке `Authorization: Bearer <token>`
- Чувствительные данные маскируются перед отправкой логов

---

## 8. Разрешения, уведомления, фон

### Permissions

#### Объявленные в AndroidManifest.xml:
1. `INTERNET` — для сетевых запросов (не требует runtime запроса)
2. `FOREGROUND_SERVICE` — для foreground service (Android 9+)
3. `FOREGROUND_SERVICE_DATA_SYNC` — для foreground service типа `dataSync` (Android 14+)
4. `POST_NOTIFICATIONS` — для показа уведомлений (Android 13+, требует runtime запроса)
5. `READ_CALL_LOG` — для чтения истории звонков (требует runtime запроса)
6. `READ_PHONE_STATE` — для чтения состояния телефона (требует runtime запроса)

#### Runtime запросы:
- `POST_NOTIFICATIONS` — запрашивается в `MainActivity.onResume()` (Android 13+)
- `READ_CALL_LOG` — запрашивается в `MainActivity.onResume()` (Android 6+)
- `READ_PHONE_STATE` — запрашивается в `MainActivity.onResume()` (Android 6+)

**Обработка отказа:**
- Если разрешения не даны — сервис не запускается (`CallListenerService` проверяет разрешения в `onStartCommand()`)

### POST_NOTIFICATIONS Flow

```
MainActivity.onResume()
    ↓
Проверка: Build.VERSION.SDK_INT >= 33
    ↓
Проверка: NotificationManagerCompat.areNotificationsEnabled()
    ↓
false → запрос разрешения
    ↓
ActivityCompat.requestPermissions(POST_NOTIFICATIONS)
    ↓
onRequestPermissionsResult()
    ↓
granted → можно запускать сервис
denied → показать сообщение, сервис не запустится
```

### Foreground Service

**Тип:** `dataSync` (Android 14+)

**Уведомление:**
- Канал: `"listening_channel"` (ID: `NOTIFICATION_CHANNEL_ID`)
- Заголовок: "Слушаю команды"
- Текст: обновляется каждые несколько секунд с статусом polling
- Иконка: `android.R.drawable.ic_dialog_info`
- Приоритет: `NotificationCompat.PRIORITY_LOW` (не мешает пользователю)

**Обновление уведомления:**
- При каждом polling цикле через `updateListeningNotification()`
- Формат: `"Опрос: <код> · <время>"` или `"Нет подключения · <время>"`

**Особенности:**
- Сервис останавливается, если уведомления отключены или разрешение не дано
- При 401/403 сервис показывает уведомление "Требуется повторный вход" и останавливается через 10 секунд

### Типичные проблемы на разных Android версиях

#### Android 13+ (targetSdk 33+)
- **Проблема:** `startForeground()` может упасть, если разрешение `POST_NOTIFICATIONS` не дано
- **Решение:** Проверка разрешения перед `startForeground()` в `CallListenerService.onStartCommand()`

#### Android 14+ (targetSdk 34+)
- **Проблема:** Требуется явное объявление типа foreground service (`FOREGROUND_SERVICE_DATA_SYNC`)
- **Решение:** Объявлено в `AndroidManifest.xml` и в `startForeground()` передается `ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC`

#### Android 6+ (READ_CALL_LOG)
- **Проблема:** Разрешение `READ_CALL_LOG` требует runtime запроса
- **Решение:** Запрос в `MainActivity.onResume()`, проверка перед чтением CallLog в `CallListenerService`

#### Android 5.0+ (minSdk 21)
- **Проблема:** Некоторые API могут быть недоступны на старых версиях
- **Решение:** Проверки `Build.VERSION.SDK_INT` перед использованием новых API

---

## 9. Как дебажить

### Где смотреть логи в приложении

1. **В приложении:**
   - Открыть `LogsActivity` (кнопка "📋 Логи приложения" в `MainActivity`)
   - Показывает последние 3000 логов из `AppLogger`
   - Поддерживает поиск и фильтрацию по уровню (E/W/I/D)
   - Можно экспортировать через Share Intent

2. **В Android Studio:**
   - Logcat фильтр: `tag:CallListenerService` или `tag:ApiClient`
   - Все логи пишутся в системный `android.util.Log` (даже через `AppLogger`)

3. **В файле:**
   - `AppLogger` опционально пишет в файл `app_logs.txt` (в app-private storage)
   - Максимальный размер: 5 MB (автоматическая очистка при превышении)

### Какие эндпоинты дергаются

**Основные endpoints:**
- `POST /api/token/` — вход по логину/паролю
- `POST /api/phone/qr/exchange/` — обмен QR-токена
- `POST /api/token/refresh/` — обновление access токена
- `POST /api/phone/devices/register/` — регистрация устройства
- `GET /api/phone/calls/pull/?device_id=<id>` — polling команд
- `POST /api/phone/calls/update/` — отправка результата звонка
- `POST /api/phone/devices/heartbeat/` — heartbeat
- `POST /api/phone/telemetry/` — метрики latency
- `POST /api/phone/logs/` — отправка логов
- `GET /api/phone/user/info/` — информация о пользователе

**Проверка в логах:**
- `SafeHttpLoggingInterceptor` логирует все HTTP запросы/ответы (только в debug)
- Маскирование чувствительных данных автоматически

### Как проверить staging vs production

**Staging:**
- `applicationId`: `ru.groupprofi.crmprofi.dialer.staging`
- `versionName`: `0.5-staging`
- `BASE_URL`: `http://95.142.47.245`
- Можно установить вместе с production (разные applicationId)

**Production:**
- `applicationId`: `ru.groupprofi.crmprofi.dialer`
- `versionName`: `0.5`
- `BASE_URL`: `https://crm.groupprofi.ru`
- Можно установить вместе со staging

**Проверка:**
- В `LogsActivity` показывается `BuildConfig.BASE_URL` и `BuildConfig.VERSION_NAME`
- В логах видно, на какой URL идут запросы

### Команды сборки

**Staging Debug:**
```bash
cd android/CRMProfiDialer
./gradlew assembleStagingDebug
```
APK: `app/build/outputs/apk/staging/debug/app-staging-debug.apk`

**Production Release:**
```bash
cd android/CRMProfiDialer
./gradlew assembleProductionRelease
```
APK: `app/build/outputs/apk/production/release/app-production-release.apk`

**Production Release (AAB для Google Play):**
```bash
cd android/CRMProfiDialer
./gradlew bundleProductionRelease
```
AAB: `app/build/outputs/bundle/productionRelease/app-production-release.aab`

**Clean build (при проблемах с Room):**
```bash
cd android/CRMProfiDialer
./gradlew clean
./gradlew assembleStagingDebug
```

---

## 10. Известные ограничения и рекомендации

### Ограничения

1. **Room Database инициализация:**
   - Если Room не сгенерировал классы (`AppDatabase_Impl`) — `QueueManager` упадет при первом использовании
   - **Решение:** Clean build (`./gradlew clean`)

2. **Оффлайн-очередь:**
   - Максимум 3 попытки отправки для каждого элемента
   - Старые неудачные элементы удаляются через 7 дней
   - **Риск:** При длительном отсутствии интернета данные могут быть потеряны

3. **Адаптивная частота polling:**
   - При rate limiting (429) задержка увеличивается до 30 секунд
   - **Риск:** Команды могут приходить с задержкой

4. **Чтение CallLog:**
   - Проверка CallLog через 5 секунд после открытия звонилки
   - **Риск:** Если пользователь не позвонил или позвонил позже — результат может быть неверным

5. **Маскирование чувствительных данных:**
   - Маскирование основано на регулярных выражениях
   - **Риск:** Новые форматы данных могут не маскироваться

6. **Foreground Service:**
   - Сервис останавливается при отключении уведомлений или отсутствии разрешений
   - **Риск:** Пользователь может случайно отключить уведомления и не понять, почему не работают команды

7. **QR-логин:**
   - QR-токен одноразовый и имеет TTL 5 минут
   - **Риск:** Если токен истек или уже использован — вход не сработает

8. **Логирование:**
   - Буфер логов ограничен 3000 записей (кольцевой буфер)
   - **Риск:** Старые логи могут быть потеряны при переполнении

9. **Миграция токенов:**
   - Миграция из plain prefs в secure prefs выполняется один раз
   - **Риск:** При ошибке миграции старые токены могут быть потеряны

10. **AppState.isForeground:**
    - Флаг обновляется вручную в `MainActivity.onResume()` / `onPause()`
    - **Риск:** Если Activity не вызывает эти методы — флаг может быть неверным

### Рекомендации для улучшения

1. **Пагинация логов:**
   - В `LogsActivity` добавить пагинацию для больших объемов логов

2. **Улучшение маскирования:**
   - Использовать более надежные алгоритмы маскирования (например, на основе структуры JSON)

3. **Улучшение чтения CallLog:**
   - Проверять CallLog несколько раз (через 5, 10, 15 секунд) или использовать `PhoneStateListener`

4. **Улучшение обработки ошибок:**
   - Более детальные сообщения об ошибках для пользователя
   - Retry механизм для критических запросов

5. **Мониторинг очереди:**
   - Уведомления пользователя при накоплении большого количества элементов в очереди

6. **Улучшение AppState:**
   - Использовать `Application.ActivityLifecycleCallbacks` для автоматического обновления `isForeground`

7. **Объединение LogCollector и AppLogger:**
   - Убрать дублирование функциональности между `LogCollector` и `AppLogger`

8. **Улучшение обработки 429:**
   - Использовать заголовок `Retry-After` из ответа сервера для более точной задержки

9. **Добавление тестов:**
   - Unit тесты для `TokenManager`, `ApiClient`, `QueueManager`
   - Instrumented тесты для `CallListenerService`

10. **Документация API:**
    - Документировать все API endpoints и их форматы запросов/ответов

---

## 11. Мертвый код и неиспользуемые файлы

### Потенциально неиспользуемые компоненты

1. **LogSender:**
   - Используется в `CallListenerService` для отправки логов
   - Дублирует функциональность `ApiClient.sendLogBundle()`
   - **Рекомендация:** Перейти на `ApiClient.sendLogBundle()` и удалить `LogSender`

2. **LogCollector (в CRMApplication):**
   - Используется для совместимости с `LogInterceptor`
   - Дублирует функциональность `AppLogger`
   - **Рекомендация:** Объединить с `AppLogger` или удалить после миграции

3. **LogInterceptor:**
   - Используется для автоматического сбора логов из `android.util.Log`
   - Не все компоненты используют `LogInterceptor` (многие используют `AppLogger` напрямую)
   - **Рекомендация:** Унифицировать логирование (использовать только `AppLogger`)

### Потенциально опасные места

1. **Fallback на plain SharedPreferences:**
   - В `TokenManager.createSecurePrefs()` при ошибке инициализации `EncryptedSharedPreferences` используется обычный `SharedPreferences`
   - **Риск:** Токены хранятся в незашифрованном виде
   - **Рекомендация:** Логировать предупреждение и требовать переустановки приложения

2. **Небезопасный Intent:**
   - `Intent.ACTION_DIAL` открывает системную звонилку (безопасно)
   - Но если злоумышленник перехватит Intent — может подменить номер
   - **Риск:** Низкий (системная звонилка проверяет Intent)

3. **Логирование в production:**
   - `SafeHttpLoggingInterceptor` включается только в debug, но `AppLogger` всегда пишет в системный log
   - **Риск:** При root-доступе логи могут быть прочитаны
   - **Рекомендация:** Отключить логирование в production release (через ProGuard или флаг)

4. **Очередь не шифруется:**
   - `QueueItem.payload` хранится в SQLite без шифрования
   - **Риск:** При root-доступе данные могут быть прочитаны
   - **Рекомендация:** Шифровать `payload` перед сохранением в очередь (опционально)

---

## Заключение

Android-приложение CRMProfiDialer представляет собой корпоративное решение для автоматизации звонков менеджеров через CRM-систему. Приложение использует современный стек технологий (Kotlin, Coroutines, Room, OkHttp) и следует best practices для безопасности (EncryptedSharedPreferences, маскирование чувствительных данных).

Основные сильные стороны:
- Централизованное управление токенами через `TokenManager`
- Оффлайн-очередь для надежности
- Адаптивная частота polling для оптимизации нагрузки
- Безопасное логирование с маскированием данных

Основные области для улучшения:
- Унификация логирования (`LogCollector` vs `AppLogger`)
- Улучшение обработки ошибок и пользовательских сообщений
- Добавление тестов
- Документация API endpoints

---

**Автор документа:** AI Assistant  
**Дата создания:** 2024  
**Версия приложения:** 0.5 (versionCode: 5)
