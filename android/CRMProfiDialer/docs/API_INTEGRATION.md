# Интеграция CRMProfiDialer с CRM

Этот документ описывает, как Android‑приложение CRMProfiDialer общается с CRM‑сервером:
- какие используются эндпойнты;
- какие данные передаются;
- какие есть основные сценарии (авторизация, команды на звонки, результаты, телеметрия).

Конкретные пути и структура payload основаны на текущей реализации клиента и серверного API `/api/phone/*`.

---

## Базовая конфигурация

- **Базовый URL** (пример):  
  `https://crm.groupprofi.ru`
- **Протокол**: HTTPS, REST‑стиль.
- **Авторизация**: JWT access/refresh токены в заголовке `Authorization: Bearer <token>`.
- **Основной namespace** для телефонного API: `/api/phone/…`.

---

## Обзор эндпойнтов (точно как в клиенте)

Ниже — сводная таблица основных запросов, которые делает Android‑клиент.

| Категория | Метод | Путь | Назначение |
|----------|--------|------|-----------|
| Авторизация | `POST` | `/api/token/` | Выдача пары `access`/`refresh` по логину/паролю |
| Авторизация | `POST` | `/api/token/refresh/` | Обновление `access` по `refresh` токену |
| Авторизация | `POST` | `/api/phone/qr/exchange/` | Обмен QR‑токена на пару `access`/`refresh` |
| Пользователь | `GET` | `/api/phone/user/info/` | Информация о пользователе (логин, флаги, is_admin) |
| Устройства | `POST` | `/api/phone/devices/register/` | Регистрация устройства/клиента в CRM |
| Команды | `GET` | `/api/phone/calls/pull/` | Long‑poll запрос команд звонков для устройства |
| Результат звонка | `POST` | `/api/phone/calls/update/` | Отправка фактического результата звонка (legacy/extended payload, fallback) |
| Heartbeat | `POST` | `/api/phone/devices/heartbeat/` | Периодический пинг: устройство живо, сервис работает |
| Телеметрия | `POST` | `/api/phone/telemetry/` | Батч телеметрии: `{ device_id, items: [...] }` |
| Логи | `POST` | `/api/phone/logs/` | Лог‑бандл: `{ device_id, ts, level_summary, source, payload }` |

Конкретные структуры JSON могут развиваться; клиент сохранён максимально толерантным к добавлению новых полей.

---

## Авторизация

### Логин по логину/паролю

- **Метод**: `POST /api/token/`
- **Тело (пример)**:

```json
{
  "username": "operator1",
  "password": "••••••••"
}
```

- **Ответ (пример)**:

```json
{
  "access": "<jwt-access-token>",
  "refresh": "<jwt-refresh-token>",
  "is_admin": false
}
```

Клиент сохраняет токены через `TokenManager` (шифрованные prefs) и использует `access` для всех дальнейших запросов.

### Обновление токена

- **Метод**: `POST /api/token/refresh/`
- **Тело (пример)**:

```json
{
  "refresh": "<jwt-refresh-token>"
}
```

- **Ответ**:

```json
{
  "access": "<new-access-token>"
}
```

Особенности на стороне клиента:
- `AuthInterceptor` автоматически пытается обновить токен при 401/403;
- `TokenManager` отслеживает:
  - время последнего успешного refresh,
  - количество неудачных попыток и backoff;
- кратковременные сетевые проблемы или временные 5xx не должны приводить к немедленному логауту.

### QR‑авторизация

- **Метод**: `POST /api/phone/qr/exchange/`
- **Назначение**: вход через одноразовый QR‑код, сгенерированный в CRM.
- **Ответ (пример)**:

```json
{
  "access": "<jwt-access-token>",
  "refresh": "<jwt-refresh-token>",
  "username": "operator1"
}
```

Используется в `QRLoginActivity` для быстрой онбординга устройств.

---

## Регистрация устройства

После успешной авторизации клиент регистрирует устройство в CRM:

- **Метод**: `POST /api/phone/devices/register/`
- **Пример тела**:

```json
{
  "device_id": "ab12cd34ef56...",
  "device_name": "Xiaomi Redmi Note 9",
  "fcm_token": "..." 
}
```

Особенности:
- `device_id` — стабильный идентификатор клиента (генерируется приложением, а не OEM‑ID, чтобы избежать ограничений платформы).
- `fcm_token` **передаётся только если непустой** (в клиенте он опциональный).

---

## Получение команд на звонки (`pullCall`)

Основной рабочий цикл: long‑poll запросы от `CallListenerService` к CRM.

- **Метод**: `GET /api/phone/calls/pull/`
- **Типичный запрос**:

```text
GET /api/phone/calls/pull/?device_id=...&wait_seconds=25
Authorization: Bearer <access>
```

Где:
- `device_id` — идентификатор зарегистрированного устройства;
- `wait_seconds` — длительность long‑poll (сервер может держать соединение до N секунд).

### Возможные ответы

- **Новая команда**: `200 OK` с JSON‑телом.

Клиент ожидает **минимальный** формат:

```json
{
  "id": "12345",
  "phone": "+79991234567"
}
```

Клиент читает:
- `phone` (обязательно, иначе команда считается пустой),
- `id` (как строку, используется как `callRequestId`).

- **Нет команд**: `204 No Content`

- **Rate‑limit**: `429 Too Many Requests`
  - клиент переходит в backoff, увеличивает задержку между запросами;
  - счётчики 429 и время в backoff попадают в `PullCallMetrics` и диагностику.

### Клиентская обработка

При успешном ответе:
- создаётся `PendingCall` (незавершённый звонок);
- создаётся запись в истории с привязкой к `callRequestId`;
- пользователю показывается уведомление/подсказка (через UI).

---

## Отправка результата звонка

Когда исходящий звонок завершился (или не состоялся), клиент отправляет результат в CRM.

- **Метод**: `POST /api/phone/calls/update/`
- **Ключевой момент**: клиент поддерживает **2 формата** payload:
  - **legacy** (минимальный: 4 поля),
  - **extended** (расширенный: + направление, метод резолва, источник, причины UNKNOWN и т.п.).

### Legacy payload (обратная совместимость)

Отправляется, если **нет ни одного нового поля** (или если сервер не принял extended и клиент сделал fallback).

```json
{
  "call_request_id": "12345",
  "call_status": "connected",
  "call_started_at": "2026-02-10T10:00:00Z",
  "call_duration_seconds": 42
}
```

### Extended payload (новые поля)

Отправляется, когда заполнено хотя бы одно из дополнительных полей.

```json
{
  "call_request_id": "12345",
  "call_status": "connected",
  "call_started_at": "2026-02-10T10:00:00Z",
  "call_duration_seconds": 42,

  "call_ended_at": "2026-02-10T10:00:42Z",
  "direction": "outgoing",
  "resolve_method": "observer",
  "resolve_reason": "string (опционально)",
  "reason_if_unknown": "missing_calllog_permission (опционально)",
  "attempts_count": 1,
  "action_source": "crm_ui"
}
```

**Fallback на legacy**: если сервер вернул строгую ошибку для extended (например, HTTP 400/415/422), клиент повторяет запрос в legacy‑формате.

**Статусы и источники**:
- Статусы уходят как строки (например, `connected`, `no_answer`, `rejected`, `no_action`, `unknown`) — см. `CallStatusApi` в `domain/CallEventContract.kt`.
- `action_source` — см. `ActionSource` (`crm_ui`, `notification`, `history`, `manual`, `unknown`).

---

## Heartbeat и телеметрия

### Heartbeat

Периодический сигнал «я живой», который позволяет CRM видеть состояние устройств.

- **Метод**: `POST /api/phone/devices/heartbeat/`
- **Пример тела**:

```json
{
  "device_id": "ab12cd34ef56...",
  "device_name": "Xiaomi Redmi Note 9",
  "app_version": "1.0.0",
  "last_poll_code": 204,
  "last_poll_at": "2026-02-10T10:10:00Z",
  "encryption_enabled": true,

  "queue_stuck": true,
  "stuck_count": 12,
  "oldest_stuck_age_sec": 3600,
  "stuck_by_type": {
    "call_update": 10,
    "telemetry": 2
  }
}
```

Отправляется не на каждый цикл, а, например, раз в несколько минут / каждые N итераций.

### Телеметрия

Телеметрия — агрегированные метрики, которые помогают:
- видеть задержку доставки команд;
- понимать, сколько времени устройство провело в backoff;
- анализировать количество 429/401/5xx.

- **Метод**: `POST /api/phone/telemetry/`
- **Формат запроса в клиенте**:

```json
{
  "device_id": "ab12cd34ef56...",
  "items": [
    {
      "ts": "2026-02-10T10:10:00Z",
      "type": "pull_call",
      "endpoint": "/api/phone/calls/pull/",
      "http_code": 204,
      "value_ms": 25000,
      "extra": {
        "mode": "LONG_POLL"
      }
    }
  ]
}
```

**Поведение при 429**: если сервер ограничивает (HTTP 429), клиент **не** добавляет телеметрию в очередь, чтобы не устроить лавину запросов.

---

## Логи

Для сложных случаев поддержки клиент может отправлять агрегированные логи.

- **Метод**: `POST /api/phone/logs/`
- **Формат запроса в клиенте**:

```json
{
  "device_id": "ab12cd34ef56...",
  "ts": "2026-02-10T10:15:00Z",
  "level_summary": "WARN:12 ERROR:1",
  "source": "support_report",
  "payload": "TEXT_LOG_HERE (маскированные номера, токены, device_id)"
}
```

Важно:
- перед отправкой все чувствительные данные маскируются на клиенте;
- журналы хранятся только во внутреннем хранилище приложения.

---

## Сценарии «от начала до конца»

### 1. Первичная настройка устройства

1. Пользователь устанавливает приложение и запускает его.
2. Проходит онбординг (разрешения, батарея, OEM‑настройки).
3. Логинится:
   - через логин/пароль → `POST /api/token/`,
   - либо по QR → `POST /api/phone/qr/exchange/`.
4. Клиент сохраняет токены, вызывает `POST /api/phone/devices/register/`.
5. Запускается `CallListenerService` и начинается `pullCall`.

### 2. Команда на звонок из CRM

1. Оператор в веб‑CRM нажимает «позвонить» клиенту.
2. Бэкенд создаёт команду в очереди для конкретного `device_id`.
3. Клиент в режиме `LONG_POLL` или `BURST` делает `GET /api/phone/calls/pull/`.
4. Получает команду (`200 OK`):
   - создаёт `PendingCall`,
   - логирует событие в `DiagnosticsMetricsBuffer`,
   - отображает подсказку пользователю (через UI).
5. Пользователь совершает звонок (через системную звонилку).
6. После окончания:
   - `CallLogObserverManager` находит запись в CallLog,
   - `CallLogCorrelator` сопоставляет её с командой,
   - клиент вызывает `POST /api/phone/calls/update/` с результатом,
   - вызывается `flushTelemetry()` (отправка метрик/логов).

### 3. Ручной звонок и аналитика

1. Пользователь открывает вкладку «Телефон» (`DialerFragment`), вводит номер и жмёт «Позвонить».
2. Клиент создаёт `CallHistoryItem`/`PendingCall` с `actionSource = MANUAL`.
3. Звонок совершается через системную звонилку.
4. По завершении:
   - результат определяется тем же механизмом CallLog;
   - в CRM уходит `status + action_source = "manual"`;
   - телеметрия обновляется.

---

## Связанные материалы

- Архитектура клиента — `ARCHITECTURE.md`
- Пользовательские сценарии и экраны — `FEATURES.md`
- Режимы работы и флаги (LOCAL_ONLY/FULL, FCM и др.) — `CONFIGURATION.md`
- Диагностика и метрики long‑poll — `guides/DIAGNOSTICS_GUIDE.md`

