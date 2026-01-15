# Инструкция по сборке релизных версий Android-приложения

## Подготовка keystore

### Создание нового keystore

Если у вас еще нет keystore для подписи релизных сборок, создайте его командой:

```bash
keytool -genkey -v -keystore crmprofi-release.jks -keyalg RSA -keysize 2048 -validity 10000 -alias crmprofi
```

**Важно:**
- Сохраните keystore в безопасном месте (НЕ коммитьте в git!)
- Запомните или сохраните в безопасном месте:
  - Пароль keystore (`storePassword`)
  - Пароль ключа (`keyPassword`)
  - Alias ключа (`keyAlias`, обычно `crmprofi`)
- Срок действия: 10000 дней (~27 лет)
- Keystore должен быть в безопасном месте с резервной копией

### Проверка существующего keystore

```bash
keytool -list -v -keystore crmprofi-release.jks -alias crmprofi
```

## Настройка секретов для подписи

### Вариант 1: Переменные окружения (рекомендуется для CI/CD)

Установите переменные окружения перед сборкой:

**Linux/macOS:**
```bash
export STORE_FILE="/path/to/crmprofi-release.jks"
export STORE_PASSWORD="your_store_password"
export KEY_ALIAS="crmprofi"
export KEY_PASSWORD="your_key_password"
```

**Windows (PowerShell):**
```powershell
$env:STORE_FILE="C:\path\to\crmprofi-release.jks"
$env:STORE_PASSWORD="your_store_password"
$env:KEY_ALIAS="crmprofi"
$env:KEY_PASSWORD="your_key_password"
```

**Windows (CMD):**
```cmd
set STORE_FILE=C:\path\to\crmprofi-release.jks
set STORE_PASSWORD=your_store_password
set KEY_ALIAS=crmprofi
set KEY_PASSWORD=your_key_password
```

### Вариант 2: local.properties (для локальной разработки)

Добавьте в файл `android/CRMProfiDialer/local.properties`:

```properties
# SDK location (уже должно быть)
sdk.dir=C\:\\Users\\YourUser\\AppData\\Local\\Android\\Sdk

# Release signing config
storeFile=../crmprofi-release.jks
storePassword=your_store_password
keyAlias=crmprofi
keyPassword=your_key_password
```

**Примечания:**
- `storeFile` может быть абсолютным путем (`C:\path\to\keystore.jks`) или относительным к корню проекта (`../crmprofi-release.jks`)
- `local.properties` уже в `.gitignore`, секреты не попадут в git

**Приоритет:** Переменные окружения имеют приоритет над `local.properties`.

## Сборка релизных версий

### Staging Debug (для тестирования)

```bash
cd android/CRMProfiDialer
./gradlew assembleStagingDebug
```

**Результат:** `app/build/outputs/apk/staging/debug/app-staging-debug.apk`

**Особенности:**
- Не требует keystore
- Не минифицирован
- ApplicationId: `ru.groupprofi.crmprofi.dialer.staging`
- BASE_URL: `http://95.142.47.245`

### Production Release APK

```bash
cd android/CRMProfiDialer
./gradlew assembleProductionRelease
```

**Результат:** `app/build/outputs/apk/production/release/app-production-release.apk`

**Особенности:**
- Требует настроенный keystore (ENV vars или local.properties)
- Минифицирован (R8/ProGuard включен)
- Подписан production keystore
- ApplicationId: `ru.groupprofi.crmprofi.dialer`
- BASE_URL: `https://crm.groupprofi.ru`

### Production Release AAB (для публикации в Google Play / RuStore)

```bash
cd android/CRMProfiDialer
./gradlew bundleProductionRelease
```

**Результат:** `app/build/outputs/bundle/productionRelease/app-production-release.aab`

**Особенности:**
- Требует настроенный keystore
- Минифицирован и оптимизирован
- Подписан production keystore
- Готов к загрузке в магазины приложений

## Проверка подписи

### Проверка APK

```bash
jarsigner -verify -verbose -certs app/build/outputs/apk/production/release/app-production-release.apk
```

### Проверка AAB

```bash
jarsigner -verify -verbose -certs app/build/outputs/bundle/productionRelease/app-production-release.aab
```

### Просмотр информации о подписи

```bash
# APK
apksigner verify --print-certs app/build/outputs/apk/production/release/app-production-release.apk

# AAB (нужен bundletool)
bundletool dump manifest --bundle=app/build/outputs/bundle/productionRelease/app-production-release.aab
```

## Troubleshooting

### Ошибка: "Signing config missing for productionRelease"

**Причина:** Не настроены секреты для подписи.

**Решение:**
1. Убедитесь, что установлены переменные окружения ИЛИ заполнен `local.properties`
2. Проверьте, что `storeFile` указывает на существующий keystore
3. Проверьте правильность паролей

### Ошибка: "Keystore file not found"

**Причина:** Неверный путь к keystore.

**Решение:**
- Используйте абсолютный путь: `C:\path\to\keystore.jks` (Windows) или `/path/to/keystore.jks` (Linux/macOS)
- Или относительный путь от корня проекта: `../crmprofi-release.jks`

### Ошибка: "Keystore was tampered with, or password was incorrect"

**Причина:** Неверный пароль keystore или ключа.

**Решение:**
- Проверьте `STORE_PASSWORD` и `KEY_PASSWORD`
- Убедитесь, что используете правильный alias (`KEY_ALIAS`)

### Staging сборки не требуют keystore

Staging сборки (`assembleStagingDebug`, `assembleStagingRelease`) собираются без подписи и не требуют keystore. Это нормально.

## Безопасность

⚠️ **ВАЖНО:**
- Никогда не коммитьте keystore в git
- Никогда не коммитьте пароли в git
- Храните keystore в безопасном месте с резервной копией
- Используйте переменные окружения в CI/CD вместо hardcoded паролей
- Если keystore потерян — невозможно обновить приложение в магазинах (нужно создавать новое приложение)

## Структура артефактов

```
app/build/outputs/
├── apk/
│   ├── staging/
│   │   └── debug/
│   │       └── app-staging-debug.apk
│   └── production/
│       └── release/
│           └── app-production-release.apk
└── bundle/
    └── productionRelease/
        └── app-production-release.aab
```

## Версионирование

Версия приложения задается в `app/build.gradle`:

```gradle
defaultConfig {
    versionCode = 5        // Увеличивать при каждой публикации
    versionName = "0.5"    // Видимая версия для пользователей
}
```

**Правила:**
- `versionCode` должен увеличиваться с каждой публикацией в магазины
- `versionName` может быть любой строкой (например, "1.0.0", "0.5-staging")
