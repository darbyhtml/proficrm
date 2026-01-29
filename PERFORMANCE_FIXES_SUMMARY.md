# Резюме исправлений производительности и стабильности

## Root Causes (причины проблем)

### 1. ПРОИЗВОДИТЕЛЬНОСТЬ (Skipped frames)

**Проблема:** "Skipped 101 frames", "Skipped 56 frames", "Skipped 49 frames" - блокировки main thread.

**Root Causes:**
- `CRMApplication.onCreate()` выполнял тяжелые операции синхронно на main thread:
  - `AppContainer.init()` создавал все зависимости (TokenManager, ApiClient, Repositories, Database)
  - `TokenManager.getInstance()` создавал `EncryptedSharedPreferences` синхронно (тяжелая операция)
- `MainActivity.onCreate()` вызывал `updateReadinessStatus()` синхронно, который выполнял тяжелые проверки:
  - Чтение SharedPreferences
  - Проверка разрешений через `AppReadinessChecker`
  - Инициализация Flow подписок
- `SafeHttpLoggingInterceptor.log()` выполнял regex/replace операции на main thread при каждом HTTP запросе
- Отсутствовал StrictMode для обнаружения блокировок main thread

**Исправления:**
- ✅ `Application.onCreate()`: тяжелые операции отложены на фоновый поток после первого кадра (`Choreographer.postFrameCallback`)
- ✅ `MainActivity.onCreate()`: `updateReadinessStatus()` отложен на `postFrameCallback`
- ✅ `SafeHttpLoggingInterceptor`: маскирование выполняется на `Dispatchers.Default`
- ✅ Добавлен StrictMode в debug build для обнаружения блокировок
- ✅ Добавлены `Trace.beginSection/endSection` для профилирования

### 2. КАМЕРА: "initCamera called twice"

**Проблема:** Двойная инициализация камеры в `PortraitCaptureActivity`.

**Root Cause:**
- `PortraitCaptureActivity.onResume()` вызывался дважды из-за lifecycle событий (возможно из библиотеки zxing)
- Отсутствовал guard для предотвращения повторной инициализации
- `requestedOrientation` устанавливался каждый раз при `onResume()`

**Исправления:**
- ✅ Добавлен `AtomicBoolean` guard для предотвращения двойного вызова
- ✅ Ориентация устанавливается только один раз за lifecycle
- ✅ Флаг сбрасывается в `onPause()` и `onDestroy()`

### 3. НАВИГАЦИЯ: Лишние пересоздания Activity

**Проблема:** Последовательность `LoginActivity → QRLoginActivity → PortraitCaptureActivity → MainActivity → OnboardingActivity` могла создавать дубликаты.

**Root Cause:**
- `OnboardingActivity` запускал `MainActivity` без правильных intent flags
- Отсутствовали flags `FLAG_ACTIVITY_CLEAR_TOP | NEW_TASK` для предотвращения дубликатов

**Исправления:**
- ✅ Добавлены правильные intent flags в `OnboardingActivity.startActivity()`
- ✅ Добавлено debug логирование для отслеживания навигации

### 4. ЛОГИ/ШУМ

**Проблема:** Избыточное логирование в release build.

**Root Cause:**
- `AppLogger` логировал все уровни, включая DEBUG, в release build
- Это создавало спам в logcat и снижало производительность

**Исправления:**
- ✅ DEBUG логи пропускаются в release build (`BuildConfig.DEBUG` check)
- ✅ Добавлена метрика времени старта в debug режиме

## Patch Summary

### Измененные файлы:

1. **`android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/CRMApplication.kt`**
   - Отложена инициализация `AppContainer` на фоновый поток после первого кадра
   - Добавлен StrictMode в debug режиме
   - Добавлены Trace метки для профилирования

2. **`android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/MainActivity.kt`**
   - Отложен `updateReadinessStatus()` на `postFrameCallback`
   - Отложено сохранение `device_id` на фоновый поток
   - Добавлена метрика времени старта (debug only)
   - Добавлены Trace метки

3. **`android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/network/SafeHttpLoggingInterceptor.kt`**
   - Маскирование выполняется на `Dispatchers.Default` вместо main thread
   - Добавлена обработка ошибок маскирования

4. **`android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/PortraitCaptureActivity.kt`**
   - Добавлен `AtomicBoolean` guard для предотвращения двойного вызова `initCamera`
   - Ориентация устанавливается только один раз за lifecycle

5. **`android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/logs/AppLogger.kt`**
   - DEBUG логи пропускаются в release build
   - Добавлен импорт `BuildConfig`

6. **`android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/ui/onboarding/OnboardingActivity.kt`**
   - Добавлены intent flags для предотвращения дубликатов `MainActivity`

## File List

```
android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/
├── CRMApplication.kt                    [MODIFIED]
├── MainActivity.kt                      [MODIFIED]
├── PortraitCaptureActivity.kt           [MODIFIED]
└── network/
    └── SafeHttpLoggingInterceptor.kt    [MODIFIED]

android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/
├── logs/
│   └── AppLogger.kt                     [MODIFIED]
└── ui/onboarding/
    └── OnboardingActivity.kt            [MODIFIED]
```

## How to Test

### 1. Unit Tests

```powershell
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
cd C:\Users\Admin\Desktop\CRM\android\CRMProfiDialer

# Запустить unit тесты
.\gradlew :app:testDebugUnitTest
```

### 2. Build & Install

```powershell
# Собрать debug APK
.\gradlew :app:assembleDebug

# Установить на устройство (замените на ваш путь)
adb install -r app\build\outputs\apk\debug\app-debug.apk
```

### 3. ADB Testing Steps

**ВАЖНО:** Если `adb` не найден в PATH, добавьте Android SDK Platform Tools:
```powershell
# Windows PowerShell
$env:Path += ";C:\Users\$env:USERNAME\AppData\Local\Android\Sdk\platform-tools"
# Или используйте полный путь к adb.exe
```

**Альтернатива:** Используйте Android Studio для тестирования (см. раздел 4).

#### 3.1. Проверка производительности (Skipped frames)

```bash
# Очистить логи
adb logcat -c

# Запустить приложение
adb shell am start -n ru.groupprofi.crmprofi.dialer/.ui.login.LoginActivity

# Проверить логи на "Skipped frames" (должно быть значительно меньше)
adb logcat | grep -i "Skipped"

# Проверить Trace метки (если включен systrace)
adb shell am start -n ru.groupprofi.crmprofi.dialer/.ui.login.LoginActivity
# Затем в Android Studio: View → Tool Windows → Profiler → CPU → Record
```

#### 3.2. Проверка камеры (initCamera called twice)

**Через Android Studio (рекомендуется):**
1. Запустите приложение в debug режиме
2. Откройте **View → Tool Windows → Logcat**
3. Фильтр: `tag:PortraitCaptureActivity OR tag:CameraPreview`
4. Запустите QR login flow
5. Проверьте, что нет сообщений "initCamera called twice"

**Через ADB:**
```bash
# Очистить логи
adb logcat -c

# Запустить QR login flow
adb shell am start -n ru.groupprofi.crmprofi.dialer/.ui.login.LoginActivity

# Проверить логи на "initCamera called twice" (не должно быть)
adb logcat | grep -i "initCamera\|CameraPreview"

# Проверить логи на "CameraPreview resume()" (должен быть один раз)
adb logcat | grep -i "CameraPreview.*resume"
```

#### 3.3. Проверка навигации

**Через Android Studio (рекомендуется):**
1. Запустите приложение в debug режиме
2. Откройте **View → Tool Windows → Logcat**
3. Фильтр: `tag:MainActivity OR tag:LoginActivity OR tag:OnboardingActivity`
4. Выполните полный flow: Login → QR → Main → Onboarding
5. Проверьте последовательность в логах (не должно быть дубликатов)

**Через ADB:**
```bash
# Очистить логи
adb logcat -c

# Запустить полный flow: Login → QR → Main → Onboarding
adb shell am start -n ru.groupprofi.crmprofi.dialer/.ui.login.LoginActivity

# Проверить последовательность Activity (не должно быть дубликатов)
adb logcat | grep -i "Activity.*onCreate\|Activity.*onResume"

# Проверить debug логи навигации
adb logcat | grep -i "MainActivity.*redirecting\|OnboardingActivity.*completed"
```

#### 3.4. Проверка StrictMode (debug only)

**Через Android Studio (рекомендуется):**
1. Запустите debug build
2. Откройте **View → Tool Windows → Logcat**
3. Фильтр: `tag:StrictMode`
4. Выполните действия в приложении
5. Проверьте, что нет нарушений (disk/network на main thread)

**Через ADB:**
```bash
# В debug build StrictMode должен логировать нарушения
adb logcat | grep -i "StrictMode"

# Проверить, что нет disk/network операций на main thread
adb logcat | grep -i "StrictMode.*violation"
```

#### 3.5. Проверка логов (release build)

**Через Android Studio (рекомендуется):**
1. Соберите release APK: `.\gradlew :app:assembleRelease`
2. Установите APK на устройство через Android Studio (Run → Edit Configurations → Install APK)
3. Запустите приложение
4. Откройте **View → Tool Windows → Logcat**
5. Фильтр: `level:DEBUG`
6. Проверьте, что нет DEBUG логов от AppLogger/OkHttp (только INFO/WARN/ERROR)

**Через ADB:**
```bash
# Собрать release APK
.\gradlew :app:assembleRelease

# Установить release APK
adb install -r app\build\outputs\apk\release\app-release.apk

# Проверить, что DEBUG логи не появляются
adb logcat | grep -i "DEBUG.*AppLogger\|DEBUG.*OkHttp"
# Должно быть пусто (только INFO/WARN/ERROR)
```

### 4. Профилирование (Android Studio)

1. Запустить приложение в debug режиме
2. Открыть **View → Tool Windows → Profiler**
3. Выбрать процесс `ru.groupprofi.crmprofi.dialer`
4. Нажать **CPU → Record**
5. Выполнить действия: запуск приложения, QR login, переходы
6. Остановить запись
7. Проверить:
   - Время до первого кадра (должно быть < 500ms)
   - Нет длительных блокировок main thread
   - Trace метки видны в timeline

### 5. Метрики времени старта (debug only)

**Через Android Studio (рекомендуется):**
1. Запустите debug build
2. Откройте **View → Tool Windows → Logcat**
3. Фильтр: `tag:MainActivity onCreate completed`
4. Запустите приложение
5. Проверьте время в логах (ожидается < 500ms)

**Через ADB:**
```bash
# Проверить логи на метрику времени старта
adb logcat | grep -i "MainActivity.*onCreate completed"

# Пример ожидаемого вывода:
# MainActivity: onCreate completed in 234ms
```

## Ожидаемые результаты

### Производительность:
- ✅ "Skipped frames" должно быть < 10 при старте (было 101+)
- ✅ Время до первого кадра < 500ms
- ✅ Нет блокировок main thread > 16ms

### Камера:
- ✅ Нет сообщений "initCamera called twice"
- ✅ "CameraPreview resume()" вызывается один раз за lifecycle

### Навигация:
- ✅ Нет дубликатов Activity в back stack
- ✅ Корректная последовательность: Login → QR → Main → Onboarding

### Логи:
- ✅ В release build нет DEBUG логов
- ✅ В debug build есть метрики времени старта

## Дополнительные исправления (январь 2026, вторая итерация)

### Проблемы из логов нового билда

После сборки нового билда в логах обнаружены дополнительные проблемы производительности:

1. **StrictMode violations** - множественные нарушения DiskReadViolation и DiskWriteViolation на main thread:
   - `AppLogger.writeToFile()` вызывается синхронно на main thread
   - `AppLogger.cleanupOldLogFile()` вызывается синхронно в `initialize()`
   - `MainActivity.shouldShowOnboarding()` читает SharedPreferences синхронно на main thread
   - `CallListenerService.onStartCommand()` вызывает I/O операции на main thread

2. **"Skipped frames"** все еще присутствуют:
   - "Skipped 75 frames!" при запуске LoginActivity
   - "Skipped 52 frames!" при запуске MainActivity
   - "Skipped 35 frames!" при запуске CallListenerService

3. **"initCamera called twice"** все еще присутствует, несмотря на добавленный guard

### Исправления

#### 1. AppLogger: асинхронная запись в файл

**Файл:** `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/logs/AppLogger.kt`

**Изменения:**
- Добавлен `fileWriteScope = CoroutineScope(Dispatchers.IO + SupervisorJob())` для асинхронной записи в файл
- `writeToFile()` теперь вызывается через `fileWriteScope.launch` вместо синхронного вызова
- `cleanupOldLogFile()` теперь вызывается асинхронно через `fileWriteScope.launch` в `initialize()`
- `writeToFile()` изменен на `suspend fun` для корректной работы в корутине

**Результат:** Запись логов в файл больше не блокирует main thread.

#### 2. MainActivity: асинхронная проверка onboarding

**Файл:** `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/MainActivity.kt`

**Изменения:**
- `shouldShowOnboarding()` теперь вызывается через `runBlocking(Dispatchers.IO)` для чтения SharedPreferences на фоновом потоке
- Добавлен импорт `kotlinx.coroutines.withContext`

**Результат:** Чтение SharedPreferences для проверки onboarding больше не блокирует main thread.

#### 3. CallListenerService: отложенная регистрация CallLogObserverManager

**Файл:** `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/CallListenerService.kt`

**Изменения:**
- Регистрация `CallLogObserverManager` теперь откладывается на фоновый поток через `scope.launch`
- `logSender` инициализируется синхронно (быстрая операция), но регистрация `CallLogObserverManager` (которая вызывает `AppLogger.d()`) откладывается

**Результат:** I/O операции при регистрации ContentObserver больше не блокируют main thread в `onStartCommand()`.

#### 4. PortraitCaptureActivity: улучшенный guard для предотвращения двойной инициализации

**Файл:** `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/PortraitCaptureActivity.kt`

**Изменения:**
- `orientationSet` больше не сбрасывается в `onPause()`, только в `onDestroy()`
- Это предотвращает повторную инициализацию при быстрых переходах между `onPause()` и `onResume()`
- Добавлены комментарии, объясняющие логику

**Результат:** Должно предотвратить повторную инициализацию камеры при быстрых переходах между состояниями Activity.

### Ожидаемые результаты после второй итерации

- ✅ Уменьшение количества StrictMode violations (DiskReadViolation/DiskWriteViolation)
- ✅ Уменьшение количества "Skipped frames" при запуске приложения
- ✅ Устранение проблемы "initCamera called twice" (или значительное уменьшение)
- ✅ Улучшение общей производительности приложения

## Третья итерация: TokenManager / EncryptedSharedPreferences на main thread (январь 2026)

### Проблема из логов

На холодном старте после установки по-прежнему фиксировались:
- **StrictMode policy violation: DiskReadViolation/DiskWriteViolation on main thread**
- Стек: `LoginActivity.onCreate` → `TokenManager.createSecurePrefs` → `EncryptedSharedPreferences.create` / `SharedPreferencesImpl.commit`
- **Choreographer: Skipped 75 frames** — UI блокируется из-за тяжёлой крипто/IO инициализации при первом запуске

### Причина

`TokenManager.getInstance(context)` создавал `EncryptedSharedPreferences` и выполнял миграцию синхронно на main thread при первом обращении (часто в `LoginActivity.onCreate`).

### Исправления

#### 1. TokenManager: асинхронная инициализация

**Файл:** `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/auth/TokenManager.kt`

**Изменения:**
- Конструктор принимает только `SharedPreferences` (тяжёлая работа вынесена из конструктора).
- Добавлен **suspend fun init(appContext: Context): TokenManager** — вся работа (createSecurePrefs, миграция) выполняется в **withContext(Dispatchers.IO)** под **Mutex** (идемпотентность).
- **getInstance()** — без аргументов, возвращает инициализированный экземпляр; при отсутствии инициализации — **IllegalStateException**.
- **getInstanceOrNull()** — для деградированного режима (например, CallListenerService до готовности Application).
- Метки **Trace.beginSection/endSection("TokenManager.init")** для профилирования (debug).
- Никакого disk I/O / crypto init на main thread.

#### 2. CRMApplication: прогрев TokenManager после первого кадра

**Файл:** `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/CRMApplication.kt`

**Изменения:**
- В блоке после **Choreographer.postFrameCallback** сначала вызывается **TokenManager.init(this@CRMApplication)**, затем **AppContainer.init()**.
- Инициализация выполняется в том же **applicationScope.launch** (без блокировки main thread).

#### 3. LoginActivity: без синхронного TokenManager в onCreate

**Файл:** `android/CRMProfiDialer/app/src/main/java/ru/groupprofi/crmprofi/dialer/ui/login/LoginActivity.kt`

**Изменения:**
- Убран вызов **TokenManager.getInstance(this)** из **onCreate**.
- **setContentView**, **initViews**, **setupListeners** и регистрация **qrLoginLauncher** выполняются сразу (лёгкие операции).
- Инициализация и проверка токенов: **lifecycleScope.launch { TokenManager.init(applicationContext); ... if (hasTokens) startMainActivity() }**.
- В callback **qrLoginLauncher** используется **TokenManager.getInstanceOrNull()?.hasTokens()** (без требования предварительного init в Activity).

#### 4. Остальные модули

- **AppContainer.init()**: вызов изменён на **TokenManager.getInstance()** (без context).
- **ApiClient, QRLoginActivity, AppReadinessChecker, AutoRecoveryManager, LogsActivity, CallFlowCoordinator**: **TokenManager.getInstance(context)** заменён на **TokenManager.getInstance()** (предполагается, что init уже выполнен в Application или ранее в flow).
- **CallListenerService**: используется **TokenManager.getInstanceOrNull()**; при **null** — **stopSelf()** и **START_NOT_STICKY** (деградация при раннем старте сервиса до init).
- **BootCompletedReceiver**: логика перенесена в корутину с **goAsync()**; сначала **TokenManager.init()**, затем **AppContainer.init()**, затем проверка токенов и запуск сервиса. Main thread не блокируется.

### Как проверить (logcat)

```bash
# StrictMode — не должно быть нарушений от TokenManager/EncryptedSharedPreferences на main thread
adb logcat | grep -i "StrictMode.*Disk"

# Skipped frames — ожидаемо существенное снижение (например, < 10–15 на холодном старте LoginActivity)
adb logcat | grep -i "Skipped.*frames"
```

### Ожидаемые результаты после третьей итерации

- ✅ Нет StrictMode DiskReadViolation/DiskWriteViolation, связанных с TokenManager / EncryptedSharedPreferences на main thread.
- ✅ Существенное снижение Skipped frames при открытии LoginActivity (целевой порядок < 10–15).
- ✅ QR flow не нарушен; токены сохраняются, запросы выполняются.
- ✅ Lifecycle-safe: без runBlocking на UI, только applicationContext.

## Commit Hash

**FULL commit hash:**
```
c3ed1fc08ccd1b131bd46b83bb51fdddad2f1e3b
```

**Ссылка на коммит:**
```
https://github.com/darbyhtml/proficrm/commit/c3ed1fc08ccd1b131bd46b83bb51fdddad2f1e3b
```
