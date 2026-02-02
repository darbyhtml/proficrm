# Чеклист тестирования улучшений

## Таблица "Заявлено vs По факту"

| Компонент | Заявлено | По факту | Статус |
|-----------|----------|----------|--------|
| **RateLimitBackoff** | Exponential backoff (10→20→40→80→160), jitter ±20%, Retry-After, reset при 200/204 | ✅ Реализовано: exponential backoff, jitter ±20%, Retry-After через max(backoff, retryAfter), reset при 200/204 | ✅ |
| **Adaptive polling** | Режимы FAST/SLOW/RATE_LIMIT, логирование с режимами и emptyCount | ✅ Реализовано: режимы определены, логирование добавлено с режимами, emptyCount, nextDelayMs | ✅ |
| **Telemetry batching** | Батчинг 20 элементов или 45 секунд, forced flush на важных событиях | ✅ Реализовано: батчинг через ApiClient.sendTelemetryBatch, flush на COMMAND_RECEIVED, RATE_LIMIT_ENTER, CALL_RESOLVED | ✅ |
| **CallLog matching** | Сравнение по последним 10 цифрам, fallback на endsWith, логи без PII | ✅ Реализовано: сравнение по 10/7 цифрам, логи с last4, типом события, elapsed | ✅ |
| **SafeHttpLoggingInterceptor** | Корректное маскирование JSON без порчи формата | ✅ Реализовано: исправлены регулярные выражения для сохранения JSON формата | ✅ |
| **Поиск компаний (Postgres)** | FTS + pg_trgm, EXACT-first, подсветка, explain | ✅ `CompanySearchService`, `CompanySearchIndex`, `get_company_search_backend()` (PostgreSQL-only) | ✅ |

## План правок (выполнено)

### 2.1 Rate limiting backoff (429)
- ✅ Исправлен `RateLimitBackoff.getRateLimitDelay()`: использует `max(backoff, retryAfterSeconds)`
- ✅ Jitter ±20% работает корректно
- ✅ Reset при 200/204 реализован
- ✅ Логирование: `429 rate-limited: retryAfter=Xs, backoff=Yms, mode=RATE_LIMIT`

### 2.2 Adaptive polling (без busy-loop)
- ✅ Режимы FAST/SLOW/RATE_LIMIT определены
- ✅ Логирование: `Poll: code=XXX, nextDelayMs=..., mode=FAST|SLOW|RATE_LIMIT, emptyCount=...`
- ✅ При 429 используется режим RATE_LIMIT с exponential backoff

### 2.3 Telemetry batching
- ✅ `TelemetryBatcher` использует `ApiClient.sendTelemetryBatch()` напрямую
- ✅ Flush при size>=20 OR timer>=45s
- ✅ Forced flush на COMMAND_RECEIVED, RATE_LIMIT_ENTER, CALL_RESOLVED
- ✅ Логирование: `TelemetryBatcher flush: nItems=..., reason=SIZE|TIMER|FORCED`

### 2.4 CallLog matching
- ✅ Сравнение по последним 10 цифрам с fallback на 7
- ✅ Логи без PII: только last4, тип события, elapsed
- ✅ Улучшенное логирование: `CallLog matched: last4=..., type=..., elapsed=...ms`

### 2.5 SafeHttpLoggingInterceptor
- ✅ Исправлены регулярные выражения для корректного маскирования JSON
- ✅ Сохранение формата `"key":"value"` при маскировании
- ✅ Исправлен кейс `"device_id":"9982171c26e26682"` -> `"device_id":"9982***6682"`

## Поиск компаний (Postgres)

- **Backend:** `SEARCH_ENGINE_BACKEND=postgres` (единственный вариант) — используется `CompanySearchService` (FTS + pg_trgm, EXACT-first).
- **EXACT-first:** проверить, что точные совпадения по email / телефону / ИНН возвращают только соответствующие компании и сортируются по `updated_at desc`.
- **Проверки поиска:** запрос по ИНН/названию/контакту/телефону возвращает релевантные компании; подсветка и «причины совпадения» отображаются в списке.
- **Тесты:** `companies.tests_search` — `SearchBackendFacadeTests`, `SearchServicePostgresTests` (требуют PostgreSQL).

## Чеклист ручного тестирования

### Тест 1: Симуляция 429 (Rate Limiting)

**Шаги:**
1. Настроить мок-сервер или использовать реальный сервер с rate limiting
2. Запустить приложение и дождаться получения 429 ответа
3. Наблюдать логи и проверить увеличение задержек

**Ожидаемые логи:**
```
CallListenerService: PullCall: 429 (rate limited, Retry-After: 30s, backoff level=0)
CallListenerService: 429 rate-limited: retryAfter=30s, backoff=30000ms, mode=RATE_LIMIT
CallListenerService: Poll: code=429, nextDelayMs=36000ms, mode=RATE_LIMIT, emptyCount=0, retryAfter=30s, backoff=0
TelemetryBatcher: TelemetryBatcher flush: nItems=5, reason=FORCED
CallListenerService: PullCall: 429 (rate limited, Retry-After: 30s, backoff level=1)
CallListenerService: 429 rate-limited: retryAfter=30s, backoff=40000ms, mode=RATE_LIMIT
CallListenerService: Poll: code=429, nextDelayMs=42000ms, mode=RATE_LIMIT, emptyCount=0, retryAfter=30s, backoff=1
```

**Проверки:**
- [ ] Задержки увеличиваются экспоненциально (10s → 20s → 40s → 80s → 160s)
- [ ] При наличии Retry-After используется max(backoff, retryAfterSeconds)
- [ ] Jitter добавляется (±20%)
- [ ] При получении 200/204 backoff сбрасывается
- [ ] Телеметрия отправляется при входе в rate limit режим

### Тест 2: Проверка батчинга телеметрии

**Шаги:**
1. Запустить приложение
2. Выполнить несколько API запросов (pullCall, sendCallUpdate и т.д.)
3. Наблюдать логи отправки телеметрии

**Ожидаемые логи:**
```
TelemetryInterceptor: (latency телеметрия собирается, но не отправляется сразу)
...
TelemetryBatcher: TelemetryBatcher flush: nItems=20, reason=SIZE
```
или
```
TelemetryBatcher: TelemetryBatcher flush: nItems=15, reason=TIMER
```

**При получении команды:**
```
CallListenerService: COMMAND_RECEIVED id=abc123 pollLatencyMs=150
TelemetryBatcher: TelemetryBatcher flush: nItems=8, reason=FORCED
```

**Проверки:**
- [ ] Телеметрия отправляется батчами (не поштучно)
- [ ] Flush происходит при накоплении 20 элементов
- [ ] Flush происходит через 45 секунд (TIMER)
- [ ] Flush происходит при получении команды (FORCED)
- [ ] В логах видно reason (SIZE/TIMER/FORCED)

### Тест 3: Проверка CallLog matching

**Шаги:**
1. Получить команду на звонок
2. Совершить звонок на указанный номер
3. Наблюдать логи поиска в CallLog

**Ожидаемые логи:**
```
CallListenerService: CallLog search: last4=2233, window=1020s
CallListenerService: CallLog matched: last4=2233, type=OUTGOING, duration=45s, elapsed=5234ms, checked=3
CallListenerService: CALL_RESOLVED id=abc123 status=CONNECTED direction=OUTGOING resolveMethod=RETRY
TelemetryBatcher: TelemetryBatcher flush: nItems=12, reason=FORCED
```

**При несовпадении:**
```
CallListenerService: CallLog search: last4=2233, window=1020s
CallListenerService: CallLog search: checked=15 entries (sample: type=1,date=1234567890,last4=5678; ...), no match found
CallListenerService: CallLog search: no match found for last4=2233
```

**Проверки:**
- [ ] В логах нет полных номеров телефонов (только last4)
- [ ] Логи содержат тип события (OUTGOING/INCOMING/MISSED)
- [ ] Логи содержат elapsed time
- [ ] При совпадении логируется `CallLog matched`
- [ ] При резолве отправляется телеметрия (FORCED flush)

### Тест 4: Проверка маскирования JSON в логах

**Шаги:**
1. Включить debug режим сборки
2. Выполнить запрос с device_id в теле
3. Проверить логи OkHttp

**Ожидаемые логи (до исправления - НЕПРАВИЛЬНО):**
```
device_id="9982171c26e26682"  // ❌ Порча JSON формата
```

**Ожидаемые логи (после исправления - ПРАВИЛЬНО):**
```
"device_id":"9982***6682"  // ✅ Корректный JSON формат
```

**Проверки:**
- [ ] JSON формат сохраняется (двойные кавычки вокруг ключей и значений)
- [ ] device_id маскируется корректно (первые 4 + последние 4 цифры)
- [ ] Не появляется формат `device_id="..."` (с одинарными кавычками или без кавычек)
- [ ] Маскирование работает для всех чувствительных полей (access, refresh, password, phone)

### Тест 5: Проверка адаптивного polling

**Шаги:**
1. Запустить приложение в foreground
2. Наблюдать логи polling
3. Перевести приложение в background
4. Наблюдать изменение режимов

**Ожидаемые логи (FAST режим):**
```
CallListenerService: PullCall: 204 (no commands)
CallListenerService: Poll: code=204, nextDelayMs=650ms, mode=FAST, emptyCount=1
CallListenerService: PullCall: 204 (no commands)
CallListenerService: Poll: code=204, nextDelayMs=750ms, mode=FAST, emptyCount=2
```

**Ожидаемые логи (SLOW режим):**
```
CallListenerService: PullCall: 204 (no commands)
CallListenerService: Poll: code=204, nextDelayMs=2500ms, mode=SLOW, emptyCount=5
CallListenerService: PullCall: 204 (no commands)
CallListenerService: Poll: code=204, nextDelayMs=3500ms, mode=SLOW, emptyCount=10
```

**Проверки:**
- [ ] В foreground режим FAST (650ms)
- [ ] В background режим SLOW (2-10s)
- [ ] При получении команды возврат к FAST режиму
- [ ] emptyCount увеличивается при пустых ответах
- [ ] В логах видно режим (FAST/SLOW/RATE_LIMIT)

## Ожидаемые строки логов (примеры)

### Rate Limiting (429)
```
CallListenerService: PullCall: 429 (rate limited, Retry-After: 30s, backoff level=1)
CallListenerService: 429 rate-limited: retryAfter=30s, backoff=40000ms, mode=RATE_LIMIT
CallListenerService: Poll: code=429, nextDelayMs=42000ms, mode=RATE_LIMIT, emptyCount=0, retryAfter=30s, backoff=1
TelemetryBatcher: TelemetryBatcher flush: nItems=5, reason=FORCED
```

### Telemetry Batching
```
TelemetryBatcher: TelemetryBatcher flush: nItems=20, reason=SIZE
TelemetryBatcher: TelemetryBatcher flush: nItems=15, reason=TIMER
TelemetryBatcher: TelemetryBatcher flush: nItems=8, reason=FORCED
```

### CallLog Matching
```
CallListenerService: CallLog search: last4=2233, window=1020s
CallListenerService: CallLog matched: last4=2233, type=OUTGOING, duration=45s, elapsed=5234ms, checked=3
CallListenerService: CALL_RESOLVED id=abc123 status=CONNECTED direction=OUTGOING resolveMethod=RETRY
```

### Adaptive Polling
```
CallListenerService: PullCall: 204 (no commands)
CallListenerService: Poll: code=204, nextDelayMs=650ms, mode=FAST, emptyCount=5
CallListenerService: PullCall: 200 (command received)
CallListenerService: Poll: code=200, nextDelayMs=650ms, mode=FAST, emptyCount=0
```

### JSON Masking (в логах OkHttp)
```
"device_id":"9982***6682"
"access":"masked"
"password":"masked"
"phone":"***4567"
```

## Критерии успешного тестирования

1. ✅ Rate limiting: задержки увеличиваются экспоненциально, учитывается Retry-After
2. ✅ Telemetry batching: отправка батчами, не поштучно
3. ✅ CallLog matching: логи без PII, корректное совпадение номеров
4. ✅ JSON masking: формат не портится, маскирование работает
5. ✅ Adaptive polling: режимы переключаются корректно, логи содержат нужную информацию

## Новые тесты (январь 2026)

### Тест 6: Проверка single-flight polling (защита от параллельных запросов)

**Шаги:**
1. Запустить приложение
2. Быстро вызвать `onStartCommand()` несколько раз (например, через ADB или быстрое переключение приложения)
3. Наблюдать логи polling

**Ожидаемые логи:**
```
CallListenerService: PullCall: 204 (no commands)
CallListenerService: Poll: code=204, nextDelayMs=650ms, mode=FAST, emptyCount=1
CallListenerService: PullCall: 204 (no commands)
CallListenerService: Poll: code=204, nextDelayMs=750ms, mode=FAST, emptyCount=2
```

**Проверки:**
- [ ] НЕТ параллельных polling запросов (все запросы идут последовательно)
- [ ] При повторном `onStartCommand()` предыдущий polling job отменяется
- [ ] В логах нет дублирования запросов с одинаковым timestamp

### Тест 7: Проверка предотвращения лавины телеметрии при 429

**Шаги:**
1. Настроить сервер для возврата 429 на polling запросы
2. Запустить приложение и дождаться получения 429
3. Наблюдать логи телеметрии

**Ожидаемые логи:**
```
CallListenerService: PullCall: 429 (rate limited)
CallListenerService: 429 rate-limited: retryAfter=30s, backoff=30000ms, mode=RATE_LIMIT
ApiClient: Telemetry batch rate-limited (429), skipping
```

**Проверки:**
- [ ] НЕТ телеметрии для 429 запросов в TelemetryInterceptor
- [ ] При 429 на отправку телеметрии она НЕ попадает в очередь
- [ ] Логируется сообщение "Telemetry batch rate-limited (429), skipping"
- [ ] НЕТ лавины телеметрии при rate limiting

### Тест 8: Проверка улучшенного маскирования JSON

**Шаги:**
1. Включить debug режим сборки
2. Выполнить запрос с device_id в query параметрах и в JSON теле
3. Проверить логи OkHttp

**Ожидаемые логи (правильно):**
```
GET /api/phone/calls/pull/?device_id=9982***6682
POST /api/phone/telemetry/
{
  "device_id":"9982***6682",
  "items":[...]
}
```

**Проверки:**
- [ ] Query параметры маскируются корректно: `device_id=9982***6682`
- [ ] JSON формат сохраняется: `"device_id":"9982***6682"`
- [ ] НЕТ порчи формата типа `device_id="9982***6682"` внутри JSON
- [ ] Маскирование работает для всех форматов (query, JSON, headers)

## Известные ограничения

- TelemetryBatcher при ошибке отправки возвращает элементы в очередь (может привести к дублированию при сетевых проблемах)
- Rate limiting backoff не сохраняется между перезапусками приложения
- CallLog matching может не найти звонок, если номер был изменен системой (например, добавлен код страны)
- Warning "initCamera called twice" может появляться из библиотеки zxing (QR-сканер), не критично

---

## Команды, которые реально прогонялись (PowerShell / Windows)

```powershell
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
cd C:\Users\Admin\Desktop\CRM\android\CRMProfiDialer

.\gradlew :app:compileDebugKotlin
.\gradlew :app:assembleDebug
.\gradlew :app:testDebugUnitTest
.\gradlew :app:lintDebug
```

**Ожидаемо:** все команды завершаются `BUILD SUCCESSFUL`.
